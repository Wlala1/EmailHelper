"""
FastAPI REST API 入口
提供邮件抓取、处理、回复发送、任务管理、偏好管理的 HTTP 接口
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from typing import List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel

from schemas import EmailMessage, UserPreferences, ProcessRequest, ReplyConfirmRequest, TaskUpdateRequest
from agents.orchestrator import load_preferences, save_preferences, update_preferences, react_process_email
from agents.monitoring_agent import logger

app = FastAPI(
    title="Email Assistant API",
    description="Multi-agent Outlook email assistant with ReAct reasoning",
    version="1.0.0",
)

# In-memory store for processed emails (replace with DB in production)
_email_store: dict[str, EmailMessage] = {}


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "agents": ["fetch", "classify", "summarize", "reply", "task", "monitor"]}


# ── Email fetching ────────────────────────────────────────────────────────────

@app.post("/emails/fetch", response_model=List[dict])
def fetch_emails_endpoint(limit: int = 10, unread_only: bool = True):
    """Fetch unread emails from Outlook and store them."""
    from agents.fetch_agent import fetch_emails
    try:
        emails = fetch_emails(limit=limit, unread_only=unread_only)
        for e in emails:
            _email_store[e.id] = e
        logger.info(f"Fetched {len(emails)} emails")
        return [e.model_dump() for e in emails]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/emails", response_model=List[dict])
def list_emails():
    """List all emails currently in memory."""
    return [e.model_dump() for e in _email_store.values()]


@app.get("/emails/{email_id}", response_model=dict)
def get_email(email_id: str):
    """Get a single email by ID."""
    email = _email_store.get(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    return email.model_dump()


# ── Processing (ReAct) ────────────────────────────────────────────────────────

@app.post("/emails/{email_id}/process", response_model=dict)
def process_email(email_id: str, req: Optional[ProcessRequest] = None):
    """Run the ReAct pipeline on a single email."""
    email = _email_store.get(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    if email.processed_at and not (req and req.force_reprocess):
        return {"email": email.model_dump(), "react_trace": [], "message": "Already processed"}

    try:
        processed, steps = react_process_email(email)
        _email_store[email_id] = processed
        return {
            "email": processed.model_dump(),
            "react_trace": [str(s) for s in steps],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/emails/process-all", response_model=dict)
def process_all_emails(background_tasks: BackgroundTasks):
    """Dispatch background processing for all unprocessed emails."""
    unprocessed = [e for e in _email_store.values() if e.processed_at is None]

    def _run():
        for email in unprocessed:
            try:
                processed, _ = react_process_email(email)
                _email_store[email.id] = processed
            except Exception as ex:
                logger.error(f"process_all failed for {email.id}: {ex}")

    background_tasks.add_task(_run)
    return {"message": f"Processing {len(unprocessed)} emails in background"}


# ── Reply ─────────────────────────────────────────────────────────────────────

@app.post("/emails/{email_id}/reply", response_model=dict)
def send_reply(email_id: str, req: ReplyConfirmRequest):
    """Confirm and send a reply via Outlook."""
    from agents.reply_agent import send_reply as _send
    email = _email_store.get(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    success = _send(email, req.reply_text)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to send reply")

    _email_store[email_id] = email
    return {"message": "Reply sent", "email_id": email_id}


# ── Tasks ─────────────────────────────────────────────────────────────────────

@app.get("/tasks", response_model=List[dict])
def get_tasks():
    """Retrieve all tasks from Google Sheets."""
    from agents.task_agent import get_all_tasks
    try:
        return get_all_tasks()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/tasks/{email_id}/status", response_model=dict)
def update_task(email_id: str, req: TaskUpdateRequest):
    """Update task status (Pending → Completed)."""
    from agents.task_agent import update_task_status
    email = _email_store.get(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    success = update_task_status(email, req.status)
    if not success:
        raise HTTPException(status_code=400, detail="Task row not found — was a task created for this email?")

    _email_store[email_id] = email
    return {"message": f"Task status updated to {req.status}", "email_id": email_id}


# ── Preferences ───────────────────────────────────────────────────────────────

@app.get("/preferences", response_model=dict)
def get_preferences():
    """Get current user preferences."""
    return load_preferences().model_dump()


@app.put("/preferences", response_model=dict)
def set_preferences(updates: dict):
    """Update user preferences (partial update supported)."""
    prefs = update_preferences(updates)
    return prefs.model_dump()


# ── Celery pipeline trigger ───────────────────────────────────────────────────

@app.post("/pipeline/run", response_model=dict)
def run_pipeline(limit: int = 10):
    """Trigger the full async Celery pipeline."""
    try:
        from celery_app import run_pipeline_task
        result = run_pipeline_task.delay(limit=limit)
        return {"task_id": result.id, "message": f"Pipeline started for up to {limit} emails"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
