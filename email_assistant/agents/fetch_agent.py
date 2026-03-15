"""
Fetch Agent — Outlook 邮件抓取
使用 O365 SDK + Microsoft Azure OAuth (device flow)
首次运行会打印一个 URL，在浏览器中授权后 token 自动缓存。
"""

import uuid
from datetime import datetime
from typing import List

from O365 import Account, FileSystemTokenBackend

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import (
    AZURE_CLIENT_ID,
    AZURE_CLIENT_SECRET,
    AZURE_TENANT_ID,
    ATTACHMENTS_DIR,
    TOKEN_FILE,
    EMAIL_FETCH_LIMIT,
)
from schemas import EmailMessage
from agents.monitoring_agent import monitor


def _get_account() -> Account:
    """Build and return an authenticated O365 Account."""
    credentials = (AZURE_CLIENT_ID, AZURE_CLIENT_SECRET)
    token_backend = FileSystemTokenBackend(
        token_path=str(TOKEN_FILE.parent),
        token_filename=TOKEN_FILE.name,
    )
    account = Account(
        credentials,
        auth_flow_type="authorization",
        tenant_id=AZURE_TENANT_ID,
        token_backend=token_backend,
    )
    if not account.is_authenticated:
        # Device / browser auth — prints URL for user to visit
        account.authenticate(scopes=["basic", "message_all"])
    return account


def _parse_message(msg) -> EmailMessage:
    """Convert O365 Message object to EmailMessage schema."""
    # Collect attachment filenames (not yet downloaded)
    attachment_names = []
    if msg.has_attachments:
        msg.attachments.download_attachments()
        for att in msg.attachments:
            attachment_names.append(att.name)

    return EmailMessage(
        id=str(msg.object_id) if msg.object_id else str(uuid.uuid4()),
        from_addr=str(msg.sender.address) if msg.sender else "",
        to=[r.address for r in msg.to._recipients] if msg.to else [],
        subject=msg.subject or "",
        body=msg.body or "",
        received_at=msg.received.strftime("%Y-%m-%dT%H:%M:%S") if msg.received else None,
        attachments=attachment_names,
    )


@monitor("FetchAgent")
def fetch_emails(limit: int = EMAIL_FETCH_LIMIT, unread_only: bool = True) -> List[EmailMessage]:
    """
    Fetch emails from Outlook inbox.
    Returns a list of EmailMessage objects.
    """
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    account = _get_account()
    mailbox = account.mailbox()
    inbox = mailbox.inbox_folder()

    query = inbox.new_query().on_attribute("isRead").equals(False) if unread_only else None
    raw_messages = inbox.get_messages(limit=limit, query=query)

    emails: List[EmailMessage] = []
    for msg in raw_messages:
        # Download attachments if any
        if msg.has_attachments:
            msg.attachments.download_attachments()
            for att in msg.attachments:
                att.save(location=str(ATTACHMENTS_DIR))

        emails.append(_parse_message(msg))

    return emails


def mark_as_read(email_id: str) -> bool:
    """Mark a message as read in Outlook."""
    account = _get_account()
    mailbox = account.mailbox()
    inbox = mailbox.inbox_folder()
    messages = inbox.get_messages(limit=1, query=inbox.new_query().on_attribute("id").equals(email_id))
    for msg in messages:
        msg.mark_as_read()
        return True
    return False
