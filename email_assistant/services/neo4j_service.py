"""Centralized Neo4j driver lifecycle and graph query helpers.

All Neo4j access should go through this module rather than importing
GraphDatabase directly in individual agents.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Optional

from config import NEO4J_PASSWORD, NEO4J_REQUIRED, NEO4J_URI, NEO4J_USER

logger = logging.getLogger(__name__)

try:
    from neo4j import GraphDatabase as _GraphDatabase
except ImportError:  # pragma: no cover
    _GraphDatabase = None  # type: ignore[assignment]


def get_neo4j_driver():
    """Return a live Neo4j driver instance.

    Raises RuntimeError if credentials are missing or the driver cannot be
    imported.  Callers are responsible for closing the driver.
    """
    if _GraphDatabase is None:
        raise RuntimeError("neo4j Python driver is not installed. Run: pip install neo4j")
    if not (NEO4J_URI and NEO4J_USER and NEO4J_PASSWORD):
        raise RuntimeError(
            "Neo4j credentials are not configured. "
            "Set NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD environment variables."
        )
    return _GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def verify_neo4j_connection() -> None:
    """Verify the Neo4j connection is healthy.

    Called at application startup.  If NEO4J_REQUIRED=true (production), a
    failed connection raises RuntimeError and prevents the app from starting.
    If NEO4J_REQUIRED=false (development), failure is logged as a warning.
    """
    if not (NEO4J_URI and NEO4J_USER and NEO4J_PASSWORD):
        if NEO4J_REQUIRED:
            raise RuntimeError(
                "NEO4J_REQUIRED=true but Neo4j credentials are not set. "
                "Provide NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD."
            )
        logger.warning("Neo4j credentials not configured — graph features disabled.")
        return

    try:
        driver = get_neo4j_driver()
        driver.verify_connectivity()
        _ensure_neo4j_indexes(driver)
        driver.close()
        logger.info("Neo4j connection verified successfully.")
    except Exception as exc:
        if NEO4J_REQUIRED:
            raise RuntimeError(f"Neo4j connection failed (NEO4J_REQUIRED=true): {exc}") from exc
        logger.warning("Neo4j connection failed — graph features disabled. Error: %s", exc)


def _ensure_neo4j_indexes(driver) -> None:
    """Create Neo4j indexes if they do not already exist."""
    with driver.session() as session:
        for statement in [
            "CREATE INDEX person_email IF NOT EXISTS FOR (p:Person) ON (p.email)",
            "CREATE INDEX org_domain IF NOT EXISTS FOR (o:Organization) ON (o.domain)",
            "CREATE INDEX org_name IF NOT EXISTS FOR (o:Organization) ON (o.name)",
            "CREATE INDEX user_id IF NOT EXISTS FOR (u:User) ON (u.user_id)",
            "CREATE INDEX event_candidate_id IF NOT EXISTS FOR (e:EventCandidate) ON (e.candidate_id)",
        ]:
            try:
                session.run(statement)
            except Exception as exc:
                logger.debug("Index creation skipped or failed: %s — %s", statement, exc)


@contextmanager
def neo4j_session():
    """Context manager that yields a Neo4j session.

    Raises RuntimeError (from get_neo4j_driver) if credentials are missing.
    The driver is closed when the context exits.
    """
    driver = get_neo4j_driver()
    try:
        with driver.session() as session:
            yield session
    finally:
        driver.close()


def is_neo4j_available() -> bool:
    """Return True if Neo4j credentials are configured and the driver is installed."""
    return _GraphDatabase is not None and bool(NEO4J_URI and NEO4J_USER and NEO4J_PASSWORD)


def sync_email_entities(
    *,
    user_id: str,
    email_id: str,
    sender_email: str,
    named_entities: list[str],
    attachment_entities: list[str],
) -> dict[str, Any]:
    """Write content-level entities from an email to Neo4j.

    Classifies each named entity into a node type (Meeting, Project,
    Organization, Topic) using simple keyword heuristics, then creates nodes
    and links them to the Email and the sender Person.

    Entity classification heuristics:
      - Contains meeting/call/sync/interview/appointment → Meeting
      - Contains project/initiative/sprint/roadmap      → Project
      - Contains university/corp/inc/ltd/group/lab      → Organization (content-extracted)
      - Everything else                                  → Topic

    Returns a summary dict.
    """
    if not is_neo4j_available():
        return {"status": "skipped", "reason": "neo4j_not_available"}

    import re

    meeting_re = re.compile(r"\b(meeting|call|sync|interview|appointment|session|seminar|workshop|webinar)\b", re.I)
    project_re = re.compile(r"\b(project|initiative|sprint|roadmap|milestone|deliverable|program)\b", re.I)
    org_re = re.compile(r"\b(university|college|institute|corp|inc\.|ltd|llc|group|lab|center|centre|department|dept)\b", re.I)

    all_entities = list(dict.fromkeys(named_entities + attachment_entities))
    if not all_entities:
        return {"status": "skipped", "reason": "no_entities"}

    classified: list[tuple[str, str]] = []
    for entity in all_entities:
        if meeting_re.search(entity):
            node_type = "Meeting"
        elif project_re.search(entity):
            node_type = "Project"
        elif org_re.search(entity):
            node_type = "Organization"
        else:
            node_type = "Topic"
        classified.append((entity, node_type))

    try:
        driver = get_neo4j_driver()
        try:
            with driver.session() as session:
                for entity_name, node_type in classified:
                    # Create the entity node and link it to the email's sender.
                    session.run(
                        f"""
                        MERGE (e:{node_type} {{name: $name}})
                        ON CREATE SET e.source = 'email_entity', e.first_seen_email_id = $email_id
                        WITH e
                        MATCH (p:Person {{email: $sender_email}})
                        MERGE (p)-[:MENTIONED {{email_id: $email_id}}]->(e)
                        """,
                        name=entity_name,
                        email_id=email_id,
                        sender_email=sender_email,
                    )
                    # Also link to the User node for graph-level queries.
                    session.run(
                        f"""
                        MERGE (u:User {{user_id: $user_id}})
                        MERGE (ent:{node_type} {{name: $name}})
                        MERGE (u)-[:INVOLVED_WITH {{email_id: $email_id}}]->(ent)
                        """,
                        user_id=user_id,
                        name=entity_name,
                        email_id=email_id,
                    )
        finally:
            driver.close()
        return {"status": "written", "entity_count": len(classified)}
    except Exception as exc:
        logger.warning("sync_email_entities failed for email %s: %s", email_id, exc)
        return {"status": "failed", "error": str(exc)}


def get_person_context(
    *,
    user_id: str,
    person_email: str,
) -> Optional[dict[str, Any]]:
    """Query Neo4j for rich relationship context around a person.

    Returns a dict with:
      - person_role, org_name, org_domain
      - decayed_weight, observation_count, last_observed_at
      - shared_org_members: list of emails of peers in the same organization
      - shared_events: list of candidate_ids for events involving this person

    Returns None if Neo4j is unavailable or the person is not in the graph.
    """
    if not is_neo4j_available():
        return None

    query = """
    MATCH (u:User {user_id: $user_id})-[r:OBSERVED_CONTACT]->(p:Person {email: $person_email})
    OPTIONAL MATCH (p)-[:MEMBER_OF]->(o:Organization)
    OPTIONAL MATCH (p)<-[:INVOLVES]-(e:EventCandidate)<-[:HAS_EVENT_CANDIDATE]-(u)
    OPTIONAL MATCH (u)-[:OBSERVED_CONTACT]->(peer:Person)-[:MEMBER_OF]->(o)
      WHERE peer.email <> p.email
    RETURN p.role AS person_role,
           o.name AS org_name,
           o.domain AS org_domain,
           CASE
             WHEN r.raw_weight IS NOT NULL AND r.last_observed_at IS NOT NULL
             THEN
               CASE
                 WHEN toFloat(r.raw_weight) * exp(
                   -0.007702 * toFloat(
                     datetime().epochMillis - datetime(r.last_observed_at).epochMillis
                   ) / 86400000.0
                 ) > 1.0 THEN 1.0
                 ELSE toFloat(r.raw_weight) * exp(
                   -0.007702 * toFloat(
                     datetime().epochMillis - datetime(r.last_observed_at).epochMillis
                   ) / 86400000.0
                 )
               END
             ELSE coalesce(toFloat(r.weight), 0.0)
           END AS decayed_weight,
           r.observation_count AS observation_count,
           r.last_observed_at AS last_observed_at,
           collect(DISTINCT peer.email) AS shared_org_members,
           collect(DISTINCT e.candidate_id) AS shared_events
    LIMIT 1
    """
    try:
        driver = get_neo4j_driver()
        try:
            with driver.session() as session:
                result = session.run(query, user_id=user_id, person_email=person_email)
                record = result.single()
                if record is None:
                    return None
                return {
                    "person_role": record["person_role"],
                    "org_name": record["org_name"],
                    "org_domain": record["org_domain"],
                    "decayed_weight": record["decayed_weight"],
                    "observation_count": record["observation_count"],
                    "last_observed_at": record["last_observed_at"],
                    "shared_org_members": list(record["shared_org_members"] or []),
                    "shared_events": list(record["shared_events"] or []),
                }
        finally:
            driver.close()
    except Exception as exc:
        logger.warning("get_person_context failed for %s: %s", person_email, exc)
        return None
