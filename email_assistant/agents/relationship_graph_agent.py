from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agents.classification.heuristics import heuristic_sender_role
from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from models import ClassifierResult, Email, EmailRecipient, RelationshipObservation
from repositories import get_current_classifier, get_email
from repository.classification import get_current_attachment_results
from services.neo4j_service import is_neo4j_available, sync_email_entities

logger = logging.getLogger(__name__)

_BROADCAST_RE = re.compile(
    r"(^|[^a-z])(no-?reply|do-?not-?reply|newsletter|notifications?|announce[a-z]*|"
    r"communications?|broadcast|circular|digest|mailer|updates?)([^a-z]|$)"
    r"|(news|comms|sem|wellness|events?)@",
    re.IGNORECASE,
)


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
    "Recipient":       ["recipient"],
    "External Contact": ["external contact", "external"],
    "System":          ["system", "broadcast", "newsletter", "mailing list", "no-reply", "noreply"],
}

# Multiplier applied to base signal_weight based on the sender's canonical role.
# Recipients always use base 0.4; this table only applies to the email sender.
ROLE_WEIGHT_MULTIPLIER: dict[str, float] = {
    "Professor":          2.5,
    "Advisor":            2.5,
    "Researcher":         2.0,   # covers Postdoc via taxonomy
    "PhD Student":        1.5,
    "Teaching Assistant": 1.2,
    "Manager":            1.5,
    "Engineer":           1.0,
    "Student":            1.0,
    "Intern":             0.9,
    "Administrator":      0.7,
    "Recruiter":          1.8,
    "Client":             1.8,
    "Vendor":             0.8,
    "Consultant":         1.2,
    "Recipient":          0.5,
    "External Contact":   0.6,
    "System":             0.05,
}


def _frequency_multiplier(count: int) -> float:
    """Map 6-month contact frequency to a weight boost multiplier."""
    if count >= 11:
        return 2.0
    if count >= 6:
        return 1.6
    if count >= 3:
        return 1.3
    return 1.0


def _get_contact_frequency_6m(session: Session, user_id: str, person_email: str) -> int:
    """Return the number of RelationshipObservation rows for this person in the past 6 months."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=182)
    return session.execute(
        select(func.count()).select_from(RelationshipObservation).where(
            RelationshipObservation.user_id == user_id,
            RelationshipObservation.person_email == person_email,
            RelationshipObservation.observed_at_utc >= cutoff,
        )
    ).scalar_one()


def _normalize_role(raw_role: Optional[str]) -> Optional[str]:
    """Map a role string to the closest taxonomy category for weight lookup.

    Strips a leading "Org · " prefix (from combined org+role format) before matching.
    Returns the canonical taxonomy name, or the role part as-is if no match found.
    """
    if not raw_role:
        return None
    # Strip "Org · Role" prefix — keep only the role part for taxonomy matching
    role_part = raw_role.split("·", 1)[-1].strip() if "·" in raw_role else raw_role
    lower = role_part.lower().strip()
    for category, keywords in ROLE_TAXONOMY.items():
        for kw in keywords:
            if kw in lower:
                return category
    return role_part.strip().title()


_MULTI_PART_TLD_SECOND = {"edu", "co", "com", "org", "ac", "gov", "net", "sch"}


def _registrar_domain(domain: str) -> str:
    """Return eTLD+1 — handles two-part TLDs like .edu.sg, .co.uk, .com.au."""
    parts = domain.rstrip(".").split(".")
    if len(parts) <= 2:
        return domain
    # e.g. "comp.nus.edu.sg": last="sg" (2-char ccTLD), second-to-last="edu" (known generic)
    if len(parts[-1]) == 2 and parts[-2] in _MULTI_PART_TLD_SECOND:
        return ".".join(parts[-3:]) if len(parts) >= 3 else domain
    return ".".join(parts[-2:])


def _infer_org(email_addr: str, person_name: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    if "@" not in email_addr:
        return None, None
    raw_domain = email_addr.split("@", 1)[1].lower()
    reg_domain = _registrar_domain(raw_domain)
    # Short SLD (≤4 chars) is likely an acronym — uppercase it; longer → Title Case.
    sld = reg_domain.split(".")[0].replace("-", " ")
    org_from_domain = sld.upper() if len(sld) <= 4 else sld.title()

    # If person_name leads with an all-caps word (e.g. "NUS Libraries", "MIT CSAIL"),
    # use that as the org label — it's more readable than the domain-derived name.
    if person_name:
        first_word = person_name.strip().split()[0] if person_name.strip() else ""
        if first_word.isupper() and len(first_word) >= 2:
            return first_word, reg_domain

    return org_from_domain, reg_domain


def _clean_body(email: Any) -> str:
    """Return plain-text body for LLM extraction — prefers body_preview over raw HTML."""
    if email.body_preview:
        return email.body_preview.strip()
    raw = email.body_content or ""
    import re as _re
    raw = _re.sub(r"<style[^>]*>.*?</style>", " ", raw, flags=_re.I | _re.S)
    raw = _re.sub(r"<[^>]+>", " ", raw)
    return _re.sub(r"\s+", " ", raw).strip()


def _llm_infer_sender_role(
    *,
    sender_email: str,
    sender_name: Optional[str],
    body_snippet: str,
    heuristic_role: str,
    domain_org: str,
    llm_client: Any,
) -> Optional[str]:
    """Single LLM call to produce a final 'Org · Role' string.

    Uses heuristic outputs as grounding hints so the LLM corrects rather than guesses.
    Returns None on failure so the caller falls back to the heuristic baseline.
    """
    if not llm_client:
        return None
    try:
        response = llm_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        'Output ONLY a single string in the format "Org · Role".\n'
                        "Rules:\n"
                        "- Org: use the domain org provided unless the body shows a clearer institutional name.\n"
                        "- Role: use the sender display name or job title from the body/signature; "
                        "if unclear, fall back to the email local-part before @.\n"
                        '- NEVER output "Unknown", "Recipient", or empty strings.\n'
                        '- Output nothing except the "Org · Role" string.'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Sender email: {sender_email}\n"
                        f"Sender name: {sender_name or 'unknown'}\n"
                        f"Domain org: {domain_org}\n"
                        f"Heuristic baseline: {heuristic_role}\n\n"
                        f"Body/signature snippet:\n{body_snippet}"
                    ),
                },
            ],
            max_tokens=30,
            temperature=0.0,
        )
        result = response.choices[0].message.content.strip()
        if not result or "·" not in result:
            return None
        return result
    except Exception as exc:
        logger.debug("_llm_infer_sender_role failed: %s", exc)
        return None


def _get_cached_sender_role(
    session: Session,
    user_id: str,
    sender_email: str,
    exclude_email_id: str,
) -> Optional[str]:
    """Return the most recent resolved person_role for this sender, or None if not yet seen."""
    return session.execute(
        select(RelationshipObservation.person_role)
        .where(
            RelationshipObservation.user_id == user_id,
            RelationshipObservation.person_email == sender_email,
            RelationshipObservation.signal_type == "email_from",
            RelationshipObservation.email_id != exclude_email_id,
            RelationshipObservation.person_role.isnot(None),
            RelationshipObservation.person_role != "Unknown",
        )
        .order_by(RelationshipObservation.observed_at_utc.desc())
        .limit(1)
    ).scalar_one_or_none()


def _get_sender_email_history(
    session: Session,
    user_id: str,
    sender_email: str,
    exclude_email_id: str,
    limit: int = 10,
) -> list[dict[str, str]]:
    """Return up to `limit` prior emails from this sender with their classifier results."""
    rows = session.execute(
        select(Email.subject, ClassifierResult.category, ClassifierResult.summary)
        .join(ClassifierResult, ClassifierResult.email_id == Email.email_id)
        .where(
            Email.sender_email == sender_email,
            ClassifierResult.user_id == user_id,
            ClassifierResult.is_current.is_(True),
            Email.email_id != exclude_email_id,
        )
        .order_by(Email.received_at_utc.desc())
        .limit(limit)
    ).fetchall()
    return [
        {"subject": r.subject or "", "category": r.category or "", "summary": r.summary or ""}
        for r in rows
    ]


def _llm_infer_sender_role_from_history(
    *,
    sender_email: str,
    sender_name: Optional[str],
    current_subject: str,
    current_body_snippet: str,
    history: list[dict[str, str]],
    heuristic_role: str,
    domain_org: str,
    llm_client: Any,
) -> Optional[str]:
    """LLM call using aggregated sender email history to infer 'Org · Role'.

    Passes the full history of subjects/categories/summaries so the LLM can
    reason across sending patterns rather than a single email body.
    Returns None on failure so the caller falls back to the heuristic.
    """
    if not llm_client:
        return None
    history_lines = "\n".join(
        f"{i+1}. [{h['category']}] {h['subject']} — {h['summary'][:120]}"
        for i, h in enumerate(history)
    )
    try:
        response = llm_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        'Output ONLY a single string in the format "Org · Role".\n'
                        "Rules:\n"
                        "- Org: use the domain org provided unless the email history shows a clearer institutional name.\n"
                        "- Role: infer from sender display name or job title first; "
                        "if not explicit, infer from the pattern of email categories and subjects "
                        "(e.g. many 'Course Notifications' → System/Platform; "
                        "'Recruitment' category → Recruiter; 'Prof' in name → Professor).\n"
                        "- Name: use the sender display name directly — do not fabricate.\n"
                        '- NEVER output "Unknown", "Recipient", or empty strings.\n'
                        '- Output nothing except the "Org · Role" string.'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Sender email: {sender_email}\n"
                        f"Sender name: {sender_name or 'unknown'}\n"
                        f"Domain org: {domain_org}\n"
                        f"Heuristic baseline: {heuristic_role}\n\n"
                        f"Current email subject: {current_subject}\n"
                        f"Current email snippet: {current_body_snippet}\n\n"
                        f"Email history from this sender ({len(history)} emails, newest first):\n"
                        f"{history_lines}"
                    ),
                },
            ],
            max_tokens=30,
            temperature=0.0,
        )
        result = response.choices[0].message.content.strip()
        if not result or "·" not in result:
            return None
        return result
    except Exception as exc:
        logger.debug("_llm_infer_sender_role_from_history failed: %s", exc)
        return None


def _llm_critic_sender_role(
    *,
    sender_email: str,
    sender_name: Optional[str],
    domain_org: str,
    heuristic_role: str,
    current_subject: str,
    current_body_snippet: str,
    history: list[dict[str, str]],
    proposed_role: str,
    llm_client: Any,
) -> Optional[tuple[str, float]]:
    """Critic LLM: validates/corrects the generator's proposed 'Org · Role' and assigns a weight.

    Receives the older half of the sender's email history as independent evidence.
    Returns (role_string, weight) or None on failure.
    """
    if not llm_client:
        return None
    history_lines = "\n".join(
        f"{i+1}. [{h['category']}] {h['subject']} — {h['summary'][:120]}"
        for i, h in enumerate(history)
    ) or "(no additional history available)"
    try:
        response = llm_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        'You are a critic reviewing an AI-generated "Org · Role" label for an email sender.\n\n'
                        "Your job:\n"
                        "1. Check whether the Org part is consistent with the sender's email domain.\n"
                        "2. Check whether the Role part is consistent with the sender's display name "
                        "and the dominant pattern across the email history (subjects, categories, summaries).\n"
                        "3. If the proposed label is correct, return it unchanged.\n"
                        "4. If it is wrong or imprecise, return a corrected version.\n"
                        "5. Choose a weight multiplier for this sender based on the table below.\n\n"
                        "Rules:\n"
                        "- Org: derive from the domain org hint unless the history shows a clearer institutional name.\n"
                        "- Role: use the sender display name or explicit job title first; infer from email "
                        "patterns only when the name is uninformative.\n"
                        '- NEVER output "Unknown", "Recipient", or an empty string for the role.\n\n'
                        "Weight reference (choose the closest match):\n"
                        "- Professor / Advisor: 2.5\n"
                        "- Researcher (Postdoc, Research Fellow, Scientist): 2.0\n"
                        "- Recruiter / Client: 1.8\n"
                        "- PhD Student / Manager: 1.5\n"
                        "- Teaching Assistant / Consultant: 1.2\n"
                        "- Engineer / Student: 1.0\n"
                        "- Intern / Vendor: 0.9\n"
                        "- Administrator / External Contact: 0.7\n"
                        "- Recipient / generic Contact: 0.5\n"
                        "- Institution, department, office, mailing list, museum, system account: 0.3\n"
                        "- Broadcast / no-reply / newsletter: 0.05\n\n"
                        "Output format: Org · Role | weight\n"
                        "Example: NUS · Professor | 2.5\n"
                        "Example: NUS · Admissions Office | 0.3\n"
                        "Output ONLY this line — nothing else."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Sender email: {sender_email}\n"
                        f"Sender name: {sender_name or 'unknown'}\n"
                        f"Domain org: {domain_org}\n"
                        f"Heuristic baseline: {heuristic_role}\n"
                        f"Current subject: {current_subject}\n"
                        f"Current snippet: {current_body_snippet}\n\n"
                        f"Supporting email history ({len(history)} emails, oldest first):\n"
                        f"{history_lines}\n\n"
                        f"Proposed role: {proposed_role}"
                    ),
                },
            ],
            max_tokens=40,
            temperature=0.0,
        )
        result = response.choices[0].message.content.strip()
        if "·" not in result or "|" not in result:
            return None
        role_part, weight_part = result.rsplit("|", 1)
        role_str = role_part.strip()
        weight = float(weight_part.strip())
        return (role_str, weight)
    except Exception as exc:
        logger.debug("_llm_critic_sender_role failed: %s", exc)
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
                        p.role = coalesce($person_role, p.role),
                        p.category = coalesce($person_category, p.category),
                        p.recent_topics = CASE WHEN $recent_topics IS NOT NULL THEN $recent_topics ELSE p.recent_topics END,
                        p.last_interaction_summary = CASE WHEN $last_interaction_summary IS NOT NULL THEN $last_interaction_summary ELSE p.last_interaction_summary END
                    WITH p
                    MERGE (u:User {user_id: $user_id})
                    MERGE (u)-[r:OBSERVED_CONTACT {signal_type: $signal_type}]->(p)
                    SET r.raw_weight = $signal_weight,
                        r.weight = $signal_weight,
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

    is_bootstrap = (email.processed_mode or "") == "bootstrap"

    sender_org_name, sender_domain = _infer_org(email.sender_email, email.sender_name)

    # Broadcast/system senders: skip LLM, assign fixed low weight
    is_broadcast = bool(_BROADCAST_RE.search(email.sender_email or ""))
    if is_broadcast:
        final_sender_role = f"{sender_org_name} · System" if sender_org_name else "System"
        sender_signal_weight = 0.05
    else:
        # Build heuristic baseline then refine with LLM (3-tier, shared by bootstrap and live)
        heuristic_role = heuristic_sender_role(email.sender_email, email.sender_name)
        llm_client = None
        if OPENAI_API_KEY:
            try:
                from openai import OpenAI
                llm_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
            except Exception:
                pass

        llm_weight: Optional[float] = None
        cached_role = _get_cached_sender_role(session, user_id, email.sender_email, email.email_id)
        if cached_role:
            llm_role = cached_role
        else:
            body_snippet = _clean_body(email)
            history = _get_sender_email_history(session, user_id, email.sender_email, email.email_id)

            # Split: generator sees newer half, critic sees older half (independent evidence)
            half = max(1, len(history) // 2)
            generator_history = history[:half]
            critic_history = history[half:] if len(history) >= 2 else history

            if generator_history:
                proposed = _llm_infer_sender_role_from_history(
                    sender_email=email.sender_email,
                    sender_name=email.sender_name,
                    current_subject=email.subject or "",
                    current_body_snippet=body_snippet[-400:].strip(),
                    history=generator_history,
                    heuristic_role=heuristic_role,
                    domain_org=sender_org_name or "",
                    llm_client=llm_client,
                )
            else:
                proposed = _llm_infer_sender_role(
                    sender_email=email.sender_email,
                    sender_name=email.sender_name,
                    body_snippet=body_snippet[-600:].strip(),
                    heuristic_role=heuristic_role,
                    domain_org=sender_org_name or "",
                    llm_client=llm_client,
                )

            if proposed:
                critic_result = _llm_critic_sender_role(
                    sender_email=email.sender_email,
                    sender_name=email.sender_name,
                    domain_org=sender_org_name or "",
                    heuristic_role=heuristic_role,
                    current_subject=email.subject or "",
                    current_body_snippet=body_snippet[-400:].strip(),
                    history=critic_history,
                    proposed_role=proposed,
                    llm_client=llm_client,
                )
                if critic_result:
                    llm_role, llm_weight = critic_result
                else:
                    llm_role = proposed
                    llm_weight = None
            else:
                llm_role = None
                llm_weight = None

        # Priority: LLM result > heuristic baseline
        final_sender_role = (llm_role or heuristic_role or "").strip() or None
        if llm_weight is not None:
            base = llm_weight
        else:
            base = ROLE_WEIGHT_MULTIPLIER.get(_normalize_role(final_sender_role) or "", 1.0)
        sender_freq = _get_contact_frequency_6m(session, user_id, email.sender_email)
        sender_signal_weight = min(round(base * _frequency_multiplier(sender_freq), 3), 5.0)

    # ── Cross-email memory: category, topics and summary from classifier ─────
    email_category: Optional[str] = (classifier.category or "").strip() or None if classifier else None
    classifier_topics: list[str] = list(classifier.named_entities or [])[:5] if classifier else []
    classifier_summary: str = (classifier.summary or "").strip() if classifier else ""
    recent_topics_value: Optional[list[str]] = classifier_topics or None
    last_interaction_summary_value: Optional[str] = classifier_summary or None

    observations: list[dict[str, Any]] = []

    sender_observation = {
        "person_email": email.sender_email,
        "person_name": email.sender_name,
        "person_role": final_sender_role,
        "person_category": email_category,
        "organisation_name": sender_org_name,
        "organisation_domain": sender_domain,
        "signal_type": "email_from",
        "signal_weight": sender_signal_weight,
        "observed_at_utc": email.received_at_utc.astimezone(timezone.utc),
    }
    observations.append(sender_observation)

    recipients = session.scalars(select(EmailRecipient).where(EmailRecipient.email_id == email_id)).all()
    for recipient in recipients:
        org_name, domain = _infer_org(recipient.recipient_email, recipient.recipient_name)
        recip_freq = _get_contact_frequency_6m(session, user_id, recipient.recipient_email)
        recip_weight = min(round(0.4 * _frequency_multiplier(recip_freq), 3), 2.0)
        observations.append(
            {
                "person_email": recipient.recipient_email,
                "person_name": recipient.recipient_name,
                "person_role": f"{org_name} · Contact" if org_name else "Contact",
                "person_category": None,
                "organisation_name": org_name,
                "organisation_domain": domain,
                "signal_type": f"email_{recipient.recipient_type}",
                "signal_weight": recip_weight,
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

    if not is_bootstrap:
        neo_payload = []
        for obs in observations:
            is_sender = obs["signal_type"] == "email_from"
            neo_payload.append(
                {
                    "user_id": user_id,
                    "person_email": obs["person_email"],
                    "person_name": obs["person_name"],
                    "person_role": obs["person_role"],
                    "person_category": obs.get("person_category"),
                    "org_name": obs["organisation_name"],
                    "org_domain": obs["organisation_domain"],
                    "signal_type": obs["signal_type"],
                    "signal_weight": obs["signal_weight"],
                    "observed_at_utc": obs["observed_at_utc"].isoformat(),
                    "recent_topics": recent_topics_value if is_sender else None,
                    "last_interaction_summary": last_interaction_summary_value if is_sender else None,
                }
            )
        neo4j_sync = _sync_neo4j(neo_payload)

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
    else:
        neo4j_sync = {"status": "skipped", "reason": "bootstrap"}
        entity_sync = {"status": "skipped", "reason": "bootstrap"}

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


def batch_sync_neo4j_for_user(session: Session, *, user_id: str) -> dict[str, Any]:
    """Sync all relationship observations for a user to Neo4j in one transaction.

    Called once after bootstrap completes instead of per-email during processing.
    """
    from sqlalchemy import select
    from models import ClassifierResult

    obs_rows = session.scalars(
        select(RelationshipObservation)
        .where(RelationshipObservation.user_id == user_id)
        .order_by(RelationshipObservation.observed_at_utc.asc())
    ).all()
    if not obs_rows:
        return {"status": "skipped", "reason": "no_observations"}

    clf_rows = session.scalars(
        select(ClassifierResult).where(
            ClassifierResult.user_id == user_id,
            ClassifierResult.is_current.is_(True),
        )
    ).all()
    clf_lookup = {r.email_id: r for r in clf_rows}

    neo_payload: list[dict[str, Any]] = []
    for obs in obs_rows:
        clf = clf_lookup.get(obs.email_id)
        is_sender = obs.signal_type == "email_from"
        display_role = obs.person_role
        person_category = (clf.category or "").strip() or None if clf and is_sender else None
        neo_payload.append({
            "user_id": user_id,
            "person_email": obs.person_email,
            "person_name": obs.person_name,
            "person_role": display_role,
            "person_category": person_category,
            "org_name": obs.organisation_name,
            "org_domain": obs.organisation_domain,
            "signal_type": obs.signal_type,
            "signal_weight": float(obs.signal_weight),
            "observed_at_utc": obs.observed_at_utc.isoformat(),
            "recent_topics": list(clf.named_entities or [])[:5] if clf and is_sender else None,
            "last_interaction_summary": (clf.summary or "").strip() if clf and is_sender else None,
        })

    return _sync_neo4j(neo_payload)
