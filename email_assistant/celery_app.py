"""
Celery 异步任务队列
Broker: Redis
任务链: fetch → classify → summarize → reply → task → done
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from celery import Celery, chain
from config import REDIS_URL

app = Celery("email_assistant", broker=REDIS_URL, backend=REDIS_URL)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    result_expires=3600,
)


# ── Individual tasks ──────────────────────────────────────────────────────────

@app.task(name="tasks.fetch_emails", bind=True)
def fetch_emails_task(self, limit: int = 10):
    """Fetch unread emails from Outlook and return list of email dicts."""
    from agents.fetch_agent import fetch_emails
    emails = fetch_emails(limit=limit)
    return [e.model_dump() for e in emails]


@app.task(name="tasks.classify_email", bind=True)
def classify_email_task(self, email_dict: dict):
    """Classify a single email dict."""
    from schemas import EmailMessage
    from agents.classification_agent import classify_email
    msg = EmailMessage(**email_dict)
    msg = classify_email(msg)
    return msg.model_dump()


@app.task(name="tasks.summarize_email", bind=True)
def summarize_email_task(self, email_dict: dict):
    """Summarize a single email dict."""
    from schemas import EmailMessage
    from agents.summarization_agent import summarize_email
    from agents.orchestrator import load_preferences
    msg = EmailMessage(**email_dict)
    prefs = load_preferences()
    msg = summarize_email(msg, prefs)
    return msg.model_dump()


@app.task(name="tasks.generate_reply", bind=True)
def generate_reply_task(self, email_dict: dict):
    """Generate reply draft for a single email dict."""
    from schemas import EmailMessage
    from agents.reply_agent import generate_reply
    from agents.orchestrator import load_preferences
    msg = EmailMessage(**email_dict)
    prefs = load_preferences()
    msg = generate_reply(msg, prefs)
    return msg.model_dump()


@app.task(name="tasks.create_task", bind=True)
def create_task_task(self, email_dict: dict):
    """Create a follow-up task in Google Sheets."""
    from schemas import EmailMessage
    from agents.task_agent import create_task
    from agents.orchestrator import load_preferences
    msg = EmailMessage(**email_dict)
    prefs = load_preferences()
    msg = create_task(msg, prefs)
    return msg.model_dump()


@app.task(name="tasks.process_email_react", bind=True)
def process_email_react_task(self, email_dict: dict):
    """Run the full ReAct loop on a single email."""
    from schemas import EmailMessage
    from agents.orchestrator import react_process_email
    msg = EmailMessage(**email_dict)
    processed_msg, steps = react_process_email(msg)
    return {
        "email": processed_msg.model_dump(),
        "react_trace": [str(s) for s in steps],
    }


# ── Batch pipeline ────────────────────────────────────────────────────────────

@app.task(name="tasks.run_pipeline", bind=True)
def run_pipeline_task(self, limit: int = 10):
    """
    Fetch emails and dispatch a ReAct processing task for each.
    Returns a list of AsyncResult IDs.
    """
    from agents.fetch_agent import fetch_emails
    emails = fetch_emails(limit=limit)
    task_ids = []
    for email in emails:
        result = process_email_react_task.delay(email.model_dump())
        task_ids.append(result.id)
    return {"dispatched": len(task_ids), "task_ids": task_ids}
