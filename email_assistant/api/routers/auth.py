from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from api.dependencies import get_db
from config import FRONTEND_BASE_URL
from services.graph_service import GraphServiceError, graph_service
from services.mailbox_state_service import handle_microsoft_callback

router = APIRouter(tags=["auth"])


@router.get("/auth/microsoft/start", response_model=dict)
def microsoft_auth_start(state: str | None = Query(default=None)) -> dict[str, str]:
    auth = graph_service.build_authorize_url(state=state)
    return {"authorize_url": auth["authorize_url"], "state": auth["state"]}


@router.get("/auth/microsoft/callback")
def microsoft_auth_callback(
    code: str = Query(...),
    state: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    try:
        result = handle_microsoft_callback(db, code=code, state=state)
        return RedirectResponse(f"{FRONTEND_BASE_URL}/?user_id={result.user_id}&connected=true")
    except HTTPException:
        db.rollback()
        raise
    except GraphServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
