import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_ouma.db")

from db import Base, engine  # noqa: E402
from main import app  # noqa: E402


def _env(agent_name: str, trace_id: str, email_id: str, user_id: str, payload: dict) -> dict:
    return {
        "schema_version": "ouma.v2",
        "trace_id": trace_id,
        "run_id": str(uuid4()),
        "email_id": email_id,
        "user_id": user_id,
        "agent_name": agent_name,
        "produced_at_utc": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


def _intake_payload(email_id: str, user_id: str) -> dict:
    return {
        "user": {
            "user_id": user_id,
            "primary_email": "student@u.nus.edu",
            "display_name": "Test User",
            "timezone": "Asia/Singapore",
        },
        "email": {
            "email_id": email_id,
            "sender_name": "Prof Lim",
            "sender_email": "prof.lim@nus.edu.sg",
            "subject": "Call for Papers 2026",
            "body_content_type": "text/plain",
            "body_content": "Please submit by 2026-04-15. Teams meeting prep next week.",
            "body_preview": "submit by 2026-04-15",
            "received_at_utc": datetime.now(timezone.utc).isoformat(),
            "has_attachments": False,
        },
        "email_recipients": [{"recipient_email": "student@u.nus.edu", "recipient_type": "to"}],
        "attachments": [],
    }


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as test_client:
        yield test_client


def test_classifier_writes_attachment_skipped_when_no_attachments(client: TestClient):
    trace_id = str(uuid4())
    email_id = str(uuid4())
    user_id = str(uuid4())

    intake_env = _env("intake", trace_id, email_id, user_id, _intake_payload(email_id, user_id))
    intake_resp = client.post("/v2/intake/email", json=intake_env)
    assert intake_resp.status_code == 200

    classifier_env = _env("classifier", trace_id, email_id, user_id, {})
    classifier_resp = client.post("/v2/agents/classifier/run", json=classifier_env)
    assert classifier_resp.status_code == 200
    payload = classifier_resp.json()["payload"]

    assert payload["attachment_status"] == "skipped"
    assert payload["category"] in {
        "AcademicConferences",
        "CanvasCourseUpdates",
        "CampusFacultyCareerOpportunities",
        "SocialEvents",
        "TeamsMeetings",
    }


def test_response_requires_branch_gate(client: TestClient):
    trace_id = str(uuid4())
    email_id = str(uuid4())
    user_id = str(uuid4())

    client.post("/v2/intake/email", json=_env("intake", trace_id, email_id, user_id, _intake_payload(email_id, user_id)))
    client.post("/v2/agents/classifier/run", json=_env("classifier", trace_id, email_id, user_id, {}))

    blocked = client.post("/v2/agents/response/run", json=_env("response", trace_id, email_id, user_id, {}))
    assert blocked.status_code == 409

    rel = client.post("/v2/agents/relationship_graph/run", json=_env("relationship_graph", trace_id, email_id, user_id, {}))
    assert rel.status_code == 200

    sched = client.post("/v2/agents/schedule/run", json=_env("schedule", trace_id, email_id, user_id, {}))
    assert sched.status_code == 200

    ok = client.post("/v2/agents/response/run", json=_env("response", trace_id, email_id, user_id, {}))
    assert ok.status_code == 200
    assert "reply_required" in ok.json()["payload"]
