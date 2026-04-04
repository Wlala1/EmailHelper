from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.dependencies import get_db
from config import OUMA_SCHEMA_VERSION
from db import SessionLocal
from repositories import create_feedback_event, get_user_writing_profile
from schemas import (
    FeedbackEventRequest,
    OUMAEnvelope,
    ReplyReviewRequest,
    ReplyReviewResultResponse,
    ReplyReviewStatusResponse,
)
from services.agent_run_service import envelope_response, run_agent_envelope
from services.calendar_feedback_service import sync_calendar_event_feedback
from services.reply_review_service import get_reply_review_status, submit_reply_review
from services.writing_profile_service import rebuild_user_writing_profile, update_preference_vector

router = APIRouter(tags=["agents"])


def _run(env: OUMAEnvelope, expected_name: str, db: Session) -> dict[str, object]:
    if env.agent_name.value != expected_name:
        raise HTTPException(status_code=400, detail=f"agent_name must be {expected_name}")
    payload = run_agent_envelope(db, env)
    return envelope_response(env, payload, schema_version=OUMA_SCHEMA_VERSION)


@router.post("/v2/intake/email", response_model=dict)
def intake_email(env: OUMAEnvelope, db: Session = Depends(get_db)):
    return _run(env, "intake", db)


@router.post("/v2/agents/classifier/run", response_model=dict)
def classifier_run(env: OUMAEnvelope, db: Session = Depends(get_db)):
    return _run(env, "classifier", db)


@router.post("/v2/agents/relationship_graph/run", response_model=dict)
def relationship_graph_run(env: OUMAEnvelope, db: Session = Depends(get_db)):
    return _run(env, "relationship_graph", db)


@router.post("/v2/agents/schedule/run", response_model=dict)
def schedule_run(env: OUMAEnvelope, db: Session = Depends(get_db)):
    return _run(env, "schedule", db)


@router.post("/v2/agents/response/run", response_model=dict)
def response_run(env: OUMAEnvelope, db: Session = Depends(get_db)):
    return _run(env, "response", db)


@router.get("/v2/agents/response/review/{email_id}", response_model=ReplyReviewStatusResponse)
def response_review_status(email_id: str, db: Session = Depends(get_db)):
    try:
        return get_reply_review_status(db, email_id=email_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/v2/agents/response/review/{email_id}", response_model=ReplyReviewResultResponse)
def response_review_submit(email_id: str, body: ReplyReviewRequest, db: Session = Depends(get_db)):
    try:
        result = submit_reply_review(db, email_id=email_id, body=body)
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/v2/feedback/event", response_model=dict, tags=["feedback"])
def submit_feedback(body: FeedbackEventRequest, db: Session = Depends(get_db)):
    """Record a user feedback signal on an agent suggestion.

    Immediately updates the preference_vector on the user's writing profile so
    that the next agent run can incorporate the new preference data.
    """
    event = create_feedback_event(
        db,
        user_id=body.user_id,
        email_id=body.email_id,
        target_type=body.target_type,
        target_id=body.target_id,
        feedback_signal=body.feedback_signal,
        feedback_metadata=body.feedback_metadata,
    )
    preference_vector = update_preference_vector(db, body.user_id)
    db.commit()
    return {
        "event_id": event.id,
        "preference_vector": preference_vector,
    }


@router.get("/v2/feedback/preference_vector/{user_id}", response_model=dict, tags=["feedback"])
def get_preference_vector(user_id: str, db: Session = Depends(get_db)):
    """Return the current preference_vector for a user."""
    profile = get_user_writing_profile(db, user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Writing profile not found for user {user_id}")
    return {
        "user_id": user_id,
        "preference_vector": getattr(profile, "preference_vector", {}) or {},
        "preference_vector_updated_at_utc": (
            getattr(profile, "preference_vector_updated_at_utc", None)
        ),
    }


@router.post("/v2/agents/calendar_feedback/sync/{user_id}", response_model=dict, tags=["n8n"])
def calendar_feedback_sync(user_id: str):
    """Sync Outlook calendar event response statuses back as feedback events.

    Called by the n8n Calendar Feedback Sync workflow every 15 minutes.
    """
    result = sync_calendar_event_feedback(SessionLocal, user_id=user_id)
    return {"user_id": user_id, **result}


@router.post("/v2/agents/profile/rebuild/{user_id}", response_model=dict, tags=["n8n"])
def profile_rebuild(user_id: str, db: Session = Depends(get_db)):
    """Rebuild the writing profile and preference vector for a user.

    Called by the n8n Daily Profile Rebuild workflow.
    """
    profile = rebuild_user_writing_profile(db, user_id)
    db.commit()
    return {"user_id": user_id, "profile": profile}
