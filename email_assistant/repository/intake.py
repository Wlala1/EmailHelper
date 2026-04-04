from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from models import Attachment, Email, EmailRecipient, User
from schemas import AttachmentPayload, EmailPayload, EmailRecipientPayload, UserPayload
from utils import ensure_utc, utcnow

from repository.common import store_attachment_content


def upsert_user(session: Session, user: UserPayload) -> User:
    existing = session.get(User, user.user_id)
    if existing is None:
        existing = User(
            user_id=user.user_id,
            primary_email=user.primary_email,
            display_name=user.display_name,
            timezone=user.timezone,
            last_login_at_utc=utcnow(),
        )
        session.add(existing)
    else:
        existing.primary_email = user.primary_email or existing.primary_email
        existing.display_name = user.display_name or existing.display_name
        existing.timezone = user.timezone or existing.timezone
        existing.last_login_at_utc = utcnow()
    return existing


def upsert_email(session: Session, user_id: str, email: EmailPayload) -> Email:
    existing = session.get(Email, email.email_id)
    if existing is None:
        existing = Email(
            email_id=email.email_id,
            user_id=user_id,
            graph_message_id=email.graph_message_id,
            graph_immutable_id=email.graph_immutable_id,
            internet_message_id=email.internet_message_id,
            conversation_id=email.conversation_id,
            sender_name=email.sender_name,
            sender_email=email.sender_email,
            subject=email.subject,
            body_content_type=email.body_content_type,
            body_content=email.body_content,
            body_preview=email.body_preview,
            received_at_utc=ensure_utc(email.received_at_utc),
            has_attachments=email.has_attachments,
            direction=email.direction.value if email.direction else None,
            mailbox_folder=email.mailbox_folder.value if email.mailbox_folder else None,
            graph_parent_folder_id=email.graph_parent_folder_id,
            mailbox_last_modified_at_utc=ensure_utc(email.mailbox_last_modified_at_utc),
            processed_mode=email.processed_mode.value if email.processed_mode else None,
        )
        session.add(existing)
    else:
        existing.user_id = user_id
        existing.graph_message_id = email.graph_message_id
        existing.graph_immutable_id = email.graph_immutable_id
        existing.internet_message_id = email.internet_message_id
        existing.conversation_id = email.conversation_id
        existing.sender_name = email.sender_name
        existing.sender_email = email.sender_email
        existing.subject = email.subject
        existing.body_content_type = email.body_content_type
        existing.body_content = email.body_content
        existing.body_preview = email.body_preview
        existing.received_at_utc = ensure_utc(email.received_at_utc)
        existing.has_attachments = email.has_attachments
        existing.direction = email.direction.value if email.direction else existing.direction
        existing.mailbox_folder = email.mailbox_folder.value if email.mailbox_folder else existing.mailbox_folder
        existing.graph_parent_folder_id = email.graph_parent_folder_id
        if email.mailbox_last_modified_at_utc:
            existing.mailbox_last_modified_at_utc = ensure_utc(email.mailbox_last_modified_at_utc)
        existing.processed_mode = email.processed_mode.value if email.processed_mode else existing.processed_mode
    return existing


def replace_recipients(session: Session, email_id: str, recipients: list[EmailRecipientPayload]) -> None:
    session.query(EmailRecipient).filter(EmailRecipient.email_id == email_id).delete()
    for recipient in recipients:
        session.add(
            EmailRecipient(
                email_id=email_id,
                recipient_email=recipient.recipient_email,
                recipient_name=recipient.recipient_name,
                recipient_type=recipient.recipient_type,
            )
        )


def upsert_attachments(session: Session, email_id: str, attachments: list[AttachmentPayload]) -> list[Attachment]:
    saved: list[Attachment] = []
    for item in attachments:
        local_path = item.local_path or store_attachment_content(item.attachment_id, item.name, item.content_base64)
        existing = session.get(Attachment, item.attachment_id)
        if existing is None:
            existing = Attachment(
                attachment_id=item.attachment_id,
                email_id=email_id,
                graph_attachment_id=item.graph_attachment_id,
                name=item.name,
                content_type=item.content_type,
                size_bytes=item.size_bytes,
                is_inline=item.is_inline,
                local_path=local_path,
            )
            session.add(existing)
        else:
            existing.email_id = email_id
            existing.graph_attachment_id = item.graph_attachment_id
            existing.name = item.name
            existing.content_type = item.content_type
            existing.size_bytes = item.size_bytes
            existing.is_inline = item.is_inline
            existing.local_path = local_path or existing.local_path
        saved.append(existing)
    return saved


def get_email(session: Session, email_id: str) -> Email | None:
    return session.get(Email, email_id)


def get_email_by_graph_immutable_id(session: Session, *, user_id: str, graph_immutable_id: str) -> Email | None:
    return session.scalars(
        select(Email).where(
            Email.user_id == user_id,
            or_(Email.graph_immutable_id == graph_immutable_id, Email.graph_message_id == graph_immutable_id),
        ).limit(1)
    ).first()


def get_email_attachments(session: Session, email_id: str) -> list[Attachment]:
    return session.scalars(select(Attachment).where(Attachment.email_id == email_id)).all()
