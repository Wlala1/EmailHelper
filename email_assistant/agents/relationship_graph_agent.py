from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from models import EmailRecipient, RelationshipObservation
from repositories import get_current_classifier, get_email
from repository.classification import get_current_attachment_results
from services.neo4j_service import is_neo4j_available, sync_email_entities

logger = logging.getLogger(__name__)


# Stable role taxonomy. Keys are canonical category names; values are keyword
# fragments that, when found in a role string (case-insensitive), map to that
# category.  Order matters: first match wins.
ROLE_TAXONOMY: dict[str, list[str]] = {
    "Professor":       ["professor", "prof.", "assoc. prof", "asst. prof", "faculty"],
    "Advisor":         ["advisor", "adviser", "supervisor", "pi ", "principal investigator", "mentor"],
    "Researcher":      ["researcher", "postdoc", "post-doc", "research fellow", "scientist", "phd candidate"],
    "Administrator":   ["dean", "director", "coordinator", "registrar", "office", "admin", "secretary", "head of"],
    "Recruiter":       ["recruiter", "talent", "hiring", "headhunter", "staffing", "hr ", "human resource"],
    "Manager":         ["manager", "lead ", "tech lead", "engineering lead", "team lead", "head of engineering"],
    "Engineer":        ["engineer", "developer", "swe", "software", "backend", "frontend", "devops", "architect"],
    "Student":         ["student", "undergraduate", "ug ", "bachelor", "master", "msc", "meng"],
    "PhD Student":     ["phd student", "phd candidate", "doctoral", "graduate student"],
    "Teaching Assistant": ["ta ", "teaching assistant", "tutor"],
    "Intern":          ["intern", "co-op", "coop"],
    "Client":          ["client", "customer", "consumer"],
    "Vendor":          ["vendor", "supplier", "partner"],
    "Consultant":      ["consultant", "contractor", "freelance"],
    "Recipient":       ["recipient"],  # fallback label
}


def _normalize_role(raw_role: Optional[str]) -> Optional[str]:
    """Map a free-form role string to the closest taxonomy category.

    Returns the canonical category name, or the original string (title-cased)
    if no taxonomy match is found.  Returns None if input is None or empty.
    """
    if not raw_role:
        return None
    lower = raw_role.lower().strip()
    for category, keywords in ROLE_TAXONOMY.items():
        for kw in keywords:
            if kw in lower:
                return category
    # No taxonomy hit — title-case and return as-is.
    return raw_role.strip().title()


def _infer_org(email: str) -> tuple[Optional[str], Optional[str]]:
    if "@" not in email:
        return None, None
    domain = email.split("@", 1)[1].lower()
    org = domain.split(".")[0].replace("-", " ").title()
    return org, domain


def _extract_org_from_body(body_text: str, llm_client: Any) -> Optional[str]:
    """Use LLM to extract an explicit organization name from the email body/signature.

    Falls back to None so domain-based inference still applies.
    """
    if not llm_client or not body_text:
        return None
    snippet = body_text[-600:].strip()
    if not snippet:
        return None
    try:
        response = llm_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract the organization/company/university name from the email signature or body. "
                        "Reply with ONLY the organization name (1-5 words), or 'unknown' if not found."
                    ),
                },
                {"role": "user", "content": snippet},
            ],
            max_tokens=20,
            temperature=0.0,
        )
        result = response.choices[0].message.content.strip()
        if result.lower() in ("unknown", "none", "n/a", ""):
            return None
        return result
    except Exception as exc:
        logger.debug("_extract_org_from_body failed: %s", exc)
        return None


def _infer_role_from_body(body_text: str, sender_name: str, llm_client: Any) -> Optional[str]:
    """Use LLM to infer the sender's role/title from the email body or signature.

    Falls back to None so the classifier sender_role is used instead.
    """
    if not llm_client or not body_text:
        return None
    snippet = body_text[-600:].strip()
    if not snippet:
        return None
    try:
        response = llm_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Extract the role or job title of '{sender_name}' from the email. "
                        "Reply with ONLY the role (1-4 words), e.g. 'PhD Advisor', 'Recruiter', "
                        "'Professor', 'Software Engineer'. Reply 'unknown' if not found."
                    ),
                },
                {"role": "user", "content": snippet},
            ],
            max_tokens=15,
            temperature=0.0,
        )
        result = response.choices[0].message.content.strip()
        if result.lower() in ("unknown", "none", "n/a", ""):
            return None
        return result
    except Exception as exc:
        logger.debug("_infer_role_from_body failed: %s", exc)
        return None


def _sync_neo4j(observations: list[dict[str, Any]]) -> dict[str, Any]:
    if not observations:
        return {"status": "skipped", "reason": "no_observations"}
    if not is_neo4j_available():
        return {"status": "skipped", "reason": "neo4j_not_available"}

    from services.neo4j_service import get_neo4j_driver

    driver = get_neo4j_driver()
    try:
        with driver.session() as neo_session:
            for obs in observations:
                # Upsert Person node and accumulate OBSERVED_CONTACT edge weight.
                neo_session.run(
                    """
                    MERGE (p:Person {email: $person_email})
                    SET p.name = coalesce($person_name, p.name),
                        p.role = coalesce($person_role, p.role)
                    WITH p
                    MERGE (u:User {user_id: $user_id})
                    MERGE (u)-[r:OBSERVED_CONTACT {signal_type: $signal_type}]->(p)
                    SET r.raw_weight = coalesce(r.raw_weight, 0) + $signal_weight,
                        r.weight = coalesce(r.weight, 0) + $signal_weight,
                        r.observation_count = coalesce(r.observation_count, 0) + 1,
                        r.last_observed_at = $observed_at_utc
                    """,
                    **{k: v for k, v in obs.items() if k not in ("org_name", "org_domain")},
                )

                # Upsert Organization node and MEMBER_OF edge (if org info present).
                org_domain = obs.get("org_domain")
                org_name = obs.get("org_name")
                if org_domain:
                    neo_session.run(
                        """
                        MATCH (p:Person {email: $person_email})
                        MERGE (o:Organization {domain: $org_domain})
                        ON CREATE SET o.name = $org_name, o.org_type = 'inferred'
                        MERGE (p)-[m:MEMBER_OF]->(o)
                        ON CREATE SET m.role = $person_role, m.inferred_at = $observed_at_utc
                        ON MATCH SET m.role = coalesce($person_role, m.role)
                        """,
                        person_email=obs["person_email"],
                        org_domain=org_domain,
                        org_name=org_name or org_domain,
                        person_role=obs.get("person_role"),
                        observed_at_utc=obs["observed_at_utc"],
                    )
        return {"status": "written", "count": len(observations)}
    except Exception as exc:
        logger.warning("_sync_neo4j failed: %s", exc)
        return {"status": "failed", "error": str(exc)}
    finally:
        driver.close()


def run_relationship_graph(
    session: Session,
    *,
    trace_id: str,
    run_id: str,
    email_id: str,
    user_id: str,
) -> dict[str, Any]:
    email = get_email(session, email_id)
    if email is None:
        raise ValueError(f"email_id not found: {email_id}")
    classifier = get_current_classifier(session, email_id)
    sender_role = classifier.sender_role if classifier else None

    # Attempt LLM-based org/role extraction from email body.
    llm_client = None
    if OPENAI_API_KEY:
        try:
            from openai import OpenAI
            llm_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        except Exception:
            pass

    body_text = email.body_content or ""
    extracted_org = _extract_org_from_body(body_text, llm_client)
    extracted_role = _infer_role_from_body(body_text, email.sender_name or "", llm_client)

    # Use extracted role if available; fall back to classifier sender_role.
    # Normalize to the stable taxonomy regardless of source.
    final_sender_role = _normalize_role(extracted_role or sender_role)

    observations: list[dict[str, Any]] = []

    sender_org_name, sender_domain = _infer_org(email.sender_email)
    # Prefer LLM-extracted org name over domain-inferred name.
    if extracted_org:
        sender_org_name = extracted_org

    sender_observation = {
        "person_email": email.sender_email,
        "person_name": email.sender_name,
        "person_role": final_sender_role,
        "organisation_name": sender_org_name,
        "organisation_domain": sender_domain,
        "signal_type": "email_from",
        "signal_weight": 1.0,
        "observed_at_utc": email.received_at_utc.astimezone(timezone.utc),
    }
    observations.append(sender_observation)

    recipients = session.scalars(select(EmailRecipient).where(EmailRecipient.email_id == email_id)).all()
    for recipient in recipients:
        org_name, domain = _infer_org(recipient.recipient_email)
        observations.append(
            {
                "person_email": recipient.recipient_email,
                "person_name": recipient.recipient_name,
                "person_role": _normalize_role("Recipient"),
                "organisation_name": org_name,
                "organisation_domain": domain,
                "signal_type": f"email_{recipient.recipient_type}",
                "signal_weight": 0.4,
                "observed_at_utc": email.received_at_utc.astimezone(timezone.utc),
            }
        )

    for obs in observations:
        session.add(
            RelationshipObservation(
                run_id=run_id,
                trace_id=trace_id,
                email_id=email_id,
                user_id=user_id,
                person_email=obs["person_email"],
                person_name=obs["person_name"],
                person_role=obs["person_role"],
                organisation_name=obs["organisation_name"],
                organisation_domain=obs["organisation_domain"],
                signal_type=obs["signal_type"],
                signal_weight=obs["signal_weight"],
                observed_at_utc=obs["observed_at_utc"],
            )
        )

    neo_payload = []
    for obs in observations:
        neo_payload.append(
            {
                "user_id": user_id,
                "person_email": obs["person_email"],
                "person_name": obs["person_name"],
                "person_role": obs["person_role"],
                "org_name": obs["organisation_name"],
                "org_domain": obs["organisation_domain"],
                "signal_type": obs["signal_type"],
                "signal_weight": obs["signal_weight"],
                "observed_at_utc": obs["observed_at_utc"].isoformat(),
            }
        )
    neo4j_sync = _sync_neo4j(neo_payload)

    # Content-level knowledge graph: extract named entities from classifier and
    # attachments, then write Meeting/Project/Organization/Topic nodes to Neo4j.
    classifier_entities: list[str] = list(classifier.named_entities or []) if classifier else []
    attachment_entities: list[str] = []
    for att in get_current_attachment_results(session, email_id):
        attachment_entities.extend(att.named_entities or [])
    entity_sync = sync_email_entities(
        user_id=user_id,
        email_id=email_id,
        sender_email=email.sender_email,
        named_entities=classifier_entities,
        attachment_entities=attachment_entities,
    )

    output_observations = []
    for obs in observations:
        output_observations.append(
            {
                **obs,
                "observed_at_utc": obs["observed_at_utc"].isoformat(),
            }
        )

    return {
        "observations": output_observations,
        "neo4j_sync": neo4j_sync,
        "entity_sync": entity_sync,
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
