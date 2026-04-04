from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.dependencies import get_db
from config import OUMA_SCHEMA_VERSION
from schemas import UserModeStatusResponse
from services.graph_service import GraphServiceError
from services.mailbox_state_service import build_user_status_response, retry_bootstrap
from services.status_service import build_trace_email_status

router = APIRouter(tags=["status"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "schema_version": OUMA_SCHEMA_VERSION}


@router.get("/v2/users/{user_id}/status", response_model=UserModeStatusResponse)
def user_status(user_id: str, db: Session = Depends(get_db)):
    return build_user_status_response(db, user_id)


@router.post("/v2/users/{user_id}/bootstrap/retry", response_model=UserModeStatusResponse)
def retry_user_bootstrap(user_id: str, db: Session = Depends(get_db)):
    try:
        return retry_bootstrap(db, user_id=user_id)
    except HTTPException:
        db.rollback()
        raise
    except GraphServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/v2/traces/{trace_id}/emails/{email_id}/status", response_model=dict)
def trace_email_status(trace_id: str, email_id: str, db: Session = Depends(get_db)):
    return build_trace_email_status(db, trace_id=trace_id, email_id=email_id)
