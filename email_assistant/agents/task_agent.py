"""
Task Agent — 跟进任务管理
将需要跟进的邮件写入 Google Sheets，支持状态更新
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timedelta
from typing import List, Optional

import gspread
from google.oauth2.service_account import Credentials

from config import GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEET_NAME
from schemas import EmailMessage, UserPreferences
from agents.monitoring_agent import monitor

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

SHEET_HEADERS = ["Email ID", "Subject", "From", "Classification", "Status", "Due Date", "Summary", "Created At"]


def _get_sheet():
    """Return the first worksheet of the EmailTasks spreadsheet."""
    creds = Credentials.from_service_account_file(GOOGLE_SHEETS_CREDENTIALS, scopes=SCOPES)
    gc = gspread.authorize(creds)
    try:
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
    except gspread.SpreadsheetNotFound:
        spreadsheet = gc.create(GOOGLE_SHEET_NAME)

    worksheet = spreadsheet.sheet1

    # Initialize headers if sheet is empty
    if not worksheet.get_all_values():
        worksheet.append_row(SHEET_HEADERS)

    return worksheet


def _default_due_date(classification: Optional[str]) -> str:
    """Set due dates based on email category urgency."""
    days = {"fundraiser": 3, "customer": 2, "internal": 7}.get(classification or "", 5)
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


@monitor("TaskAgent")
def create_task(msg: EmailMessage, prefs: UserPreferences = None) -> EmailMessage:
    """
    Write a follow-up task to Google Sheets.
    Only creates tasks for categories in prefs.follow_up_categories.
    """
    follow_up_categories = prefs.follow_up_categories if prefs else ["fundraiser", "customer"]

    if msg.classification not in follow_up_categories:
        return msg  # Skip — not a follow-up category

    worksheet = _get_sheet()
    due_date = msg.task_due_date or _default_due_date(msg.classification)

    row = [
        msg.id,
        msg.subject,
        msg.from_addr,
        msg.classification or "",
        "Pending",
        due_date,
        (msg.summary or "")[:500],
        datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    ]
    worksheet.append_row(row)

    # Store row index for later updates
    all_rows = worksheet.get_all_values()
    msg.task_row = len(all_rows)  # 1-indexed, last row
    msg.task_status = "Pending"
    msg.task_due_date = due_date
    return msg


@monitor("TaskAgent")
def update_task_status(msg: EmailMessage, status: str) -> bool:
    """Update the Status column for an existing task row."""
    if msg.task_row is None:
        return False

    worksheet = _get_sheet()
    # Status is column 5 (E)
    worksheet.update_cell(msg.task_row, 5, status)
    msg.task_status = status
    return True


def get_all_tasks() -> List[dict]:
    """Return all tasks as a list of dicts."""
    worksheet = _get_sheet()
    rows = worksheet.get_all_records()
    return rows
