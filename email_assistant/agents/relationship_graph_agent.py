from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from models import EmailRecipient, RelationshipObservation
from repositories import get_current_classifier, get_email

try:
    from neo4j import GraphDatabase
except Exception:  # pragma: no cover - optional dependency
    GraphDatabase = None


def _infer_org(email: str) -> tuple[Optional[str], Optional[str]]:
    if "@" not in email:
        return None, None
    domain = email.split("@", 1)[1].lower()
    org = domain.split(".")[0].replace("-", " ").title()
    return org, domain


def _sync_neo4j(observations: list[dict[str, Any]]) -> dict[str, Any]:
    if not observations:
        return {"status": "skipped", "reason": "no_observations"}
    if GraphDatabase is None:
        return {"status": "skipped", "reason": "neo4j_driver_not_installed"}
    if not (NEO4J_URI and NEO4J_USER and NEO4J_PASSWORD):
        return {"status": "skipped", "reason": "neo4j_credentials_missing"}

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as neo_session:
            for obs in observations:
                neo_session.run(
                    """
                    MERGE (p:Person {email: $person_email})
                    SET p.name = coalesce($person_name, p.name),
                        p.role = coalesce($person_role, p.role)
                    WITH p
                    MERGE (u:User {user_id: $user_id})
                    MERGE (u)-[r:OBSERVED_CONTACT {signal_type: $signal_type}]->(p)
                    SET r.weight = coalesce(r.weight, 0) + $signal_weight,
                        r.last_observed_at = $observed_at_utc
                    """,
                    **obs,
                )
        return {"status": "written", "count": len(observations)}
    except Exception as exc:
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

    observations: list[dict[str, Any]] = []

    sender_org_name, sender_domain = _infer_org(email.sender_email)
    sender_observation = {
        "person_email": email.sender_email,
        "person_name": email.sender_name,
        "person_role": sender_role,
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
                "person_role": "Recipient",
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
                "signal_type": obs["signal_type"],
                "signal_weight": obs["signal_weight"],
                "observed_at_utc": obs["observed_at_utc"].isoformat(),
            }
        )
    neo4j_sync = _sync_neo4j(neo_payload)

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
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
    }
