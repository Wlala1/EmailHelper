from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib import error, parse, request

from sqlalchemy.orm import Session

from config import (
    AZURE_CLIENT_ID,
    AZURE_CLIENT_SECRET,
    AZURE_REDIRECT_URI,
    AZURE_SCOPE,
    AZURE_TENANT_ID,
    MICROSOFT_AUTH_BASE_URL,
    MICROSOFT_GRAPH_BASE_URL,
)
from repositories import get_user_mailbox_account, upsert_user_mailbox_account
from utils import ensure_utc


class GraphServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class MicrosoftGraphService:
    def __init__(self) -> None:
        self.scope_list = [item.strip() for item in AZURE_SCOPE.split() if item.strip()]

    @property
    def authorize_endpoint(self) -> str:
        return f"{MICROSOFT_AUTH_BASE_URL}/{AZURE_TENANT_ID}/oauth2/v2.0/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{MICROSOFT_AUTH_BASE_URL}/{AZURE_TENANT_ID}/oauth2/v2.0/token"

    def build_authorize_url(self, *, state: Optional[str] = None) -> dict[str, str]:
        effective_state = state or secrets.token_urlsafe(24)
        params = {
            "client_id": AZURE_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": AZURE_REDIRECT_URI,
            "response_mode": "query",
            "scope": " ".join(self.scope_list),
            "state": effective_state,
            "prompt": "select_account",
        }
        return {
            "authorize_url": f"{self.authorize_endpoint}?{parse.urlencode(params)}",
            "state": effective_state,
        }

    def exchange_code_for_token(self, code: str) -> dict[str, Any]:
        form = {
            "client_id": AZURE_CLIENT_ID,
            "client_secret": AZURE_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": AZURE_REDIRECT_URI,
            "scope": " ".join(self.scope_list),
        }
        return self._post_form(self.token_endpoint, form)

    def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        form = {
            "client_id": AZURE_CLIENT_ID,
            "client_secret": AZURE_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(self.scope_list),
        }
        return self._post_form(self.token_endpoint, form)

    def fetch_user_profile(self, access_token: str) -> dict[str, Any]:
        return self._graph_get(
            "/me",
            access_token=access_token,
            query={"$select": "id,displayName,mail,userPrincipalName,preferredLanguage"},
        )

    def capture_delta_token(self, access_token: str, folder_name: str) -> str:
        _, delta_link = self.delta_messages(access_token, folder_name=folder_name, delta_token=None)
        if not delta_link:
            raise GraphServiceError(f"missing delta token for folder {folder_name}")
        return delta_link

    def list_messages_since(self, access_token: str, *, folder_name: str, since_utc: datetime) -> list[dict[str, Any]]:
        filter_field = "sentDateTime" if folder_name.lower() == "sentitems" else "receivedDateTime"
        next_url = self._graph_url(
            f"/me/mailFolders/{folder_name}/messages",
            {
                "$select": self._message_select_fields(),
                "$top": "50",
                "$orderby": f"{filter_field} desc",
                "$filter": f"{filter_field} ge {since_utc.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')}",
            },
        )
        items: list[dict[str, Any]] = []
        while next_url:
            payload = self._graph_request(next_url, access_token=access_token)
            items.extend(payload.get("value", []))
            next_url = payload.get("@odata.nextLink")
        return items

    def delta_messages(
        self,
        access_token: str,
        *,
        folder_name: str,
        delta_token: Optional[str],
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        if delta_token:
            next_url = delta_token
        else:
            next_url = self._graph_url(
                f"/me/mailFolders/{folder_name}/messages/delta",
                {"$select": self._message_select_fields(), "$top": "50"},
            )

        items: list[dict[str, Any]] = []
        delta_link: Optional[str] = None
        while next_url:
            payload = self._graph_request(next_url, access_token=access_token)
            items.extend(payload.get("value", []))
            delta_link = payload.get("@odata.deltaLink") or delta_link
            next_url = payload.get("@odata.nextLink")
        return items, delta_link

    def fetch_attachments(self, access_token: str, message_id: str) -> list[dict[str, Any]]:
        payload = self._graph_get(
            f"/me/messages/{message_id}/attachments",
            access_token=access_token,
            query={},
        )
        attachments: list[dict[str, Any]] = []
        for item in payload.get("value", []):
            if item.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            attachments.append(item)
        return attachments

    def create_reply_draft(self, access_token: str, message_id: str, *, body_html: str) -> dict[str, Any]:
        draft = self._graph_post(
            f"/me/messages/{message_id}/createReply",
            access_token=access_token,
            json_payload={},
        )
        draft_id = draft.get("id")
        if not draft_id:
            raise GraphServiceError("createReply did not return draft id")
        updated = self._graph_patch(
            f"/me/messages/{draft_id}",
            access_token=access_token,
            json_payload={
                "body": {
                    "contentType": "HTML",
                    "content": body_html,
                }
            },
        )
        return {
            "id": draft_id,
            "webLink": updated.get("webLink") or draft.get("webLink"),
        }

    def get_calendar_event(self, access_token: str, event_id: str) -> dict[str, Any]:
        """Fetch a single calendar event by ID.

        Returns the event dict including ``responseStatus`` and ``showAs``.
        Raises GraphServiceError if the event is not found.
        """
        return self._graph_get(
            f"/me/events/{event_id}",
            access_token=access_token,
            query={"$select": "id,subject,showAs,responseStatus,start,end,isCancelled"},
        )

    def get_calendar_events(
        self,
        access_token: str,
        *,
        start_time_utc: datetime,
        end_time_utc: datetime,
        top: int = 50,
    ) -> list[dict[str, Any]]:
        """List calendar events in a time window.

        Returns events including ``responseStatus`` so callers can detect
        accepted/declined/tentative status for candidate matching.
        """
        start_iso = start_time_utc.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        end_iso = end_time_utc.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        payload = self._graph_get(
            "/me/calendarView",
            access_token=access_token,
            query={
                "startDateTime": start_iso,
                "endDateTime": end_iso,
                "$select": "id,subject,showAs,responseStatus,start,end,isCancelled",
                "$top": str(top),
            },
        )
        return payload.get("value", [])

    def get_free_busy(
        self,
        access_token: str,
        *,
        start_time_utc: datetime,
        end_time_utc: datetime,
        schedules: Optional[list[str]] = None,
        availability_view_interval: int = 30,
    ) -> list[dict[str, Any]]:
        """Query the user's free/busy information via MS Graph getSchedule.

        Calls POST /me/calendar/getSchedule.
        Returns the list of schedule items from the Graph API response.
        Requires Calendars.Read scope (included in AZURE_SCOPE).
        """
        if schedules is None:
            schedules = ["me"]
        payload = self._graph_post(
            "/me/calendar/getSchedule",
            access_token=access_token,
            json_payload={
                "schedules": schedules,
                "startTime": {
                    "dateTime": start_time_utc.astimezone(timezone.utc).replace(tzinfo=None).isoformat(),
                    "timeZone": "UTC",
                },
                "endTime": {
                    "dateTime": end_time_utc.astimezone(timezone.utc).replace(tzinfo=None).isoformat(),
                    "timeZone": "UTC",
                },
                "availabilityViewInterval": availability_view_interval,
            },
        )
        return payload.get("value", [])

    def create_tentative_event(self, access_token: str, candidate: dict[str, Any]) -> dict[str, Any]:
        payload = self._graph_post(
            "/me/events",
            access_token=access_token,
            json_payload={
                "subject": candidate["title"],
                "body": {
                    "contentType": "HTML",
                    "content": f"OUMA transaction_id={candidate['transaction_id']}",
                },
                "start": {
                    "dateTime": candidate["start_time_utc"].astimezone(timezone.utc).replace(tzinfo=None).isoformat(),
                    "timeZone": "UTC",
                },
                "end": {
                    "dateTime": candidate["end_time_utc"].astimezone(timezone.utc).replace(tzinfo=None).isoformat(),
                    "timeZone": "UTC",
                },
                "showAs": candidate.get("show_as", "tentative"),
                "isAllDay": bool(candidate.get("is_all_day", False)),
            },
        )
        return {
            "id": payload.get("id"),
            "webLink": payload.get("webLink"),
        }

    def ensure_access_token(self, session: Session, user_id: str) -> str:
        account = get_user_mailbox_account(session, user_id)
        if account is None:
            raise GraphServiceError(f"mailbox account not found for user {user_id}")

        token_blob = dict(account.token_blob or {})
        access_token = token_blob.get("access_token")
        expires_at = ensure_utc(account.token_expires_at_utc)
        if access_token and expires_at and expires_at > datetime.now(timezone.utc) + timedelta(minutes=2):
            return access_token

        refresh_token = token_blob.get("refresh_token")
        if not refresh_token:
            raise GraphServiceError("refresh token unavailable")

        refreshed = self.refresh_token(refresh_token)
        refreshed_blob = self._build_token_blob(refreshed, fallback_refresh_token=refresh_token)
        upsert_user_mailbox_account(
            session,
            user_id=user_id,
            tenant_id=account.tenant_id,
            graph_user_id=account.graph_user_id,
            token_blob=refreshed_blob,
            token_expires_at_utc=self._token_expiry(refreshed),
            scopes=self.scope_list,
        )
        return refreshed_blob["access_token"]

    def persist_account_from_token(
        self,
        session: Session,
        *,
        user_id: str,
        tenant_id: str,
        graph_user_id: str,
        token_result: dict[str, Any],
    ) -> None:
        upsert_user_mailbox_account(
            session,
            user_id=user_id,
            tenant_id=tenant_id,
            graph_user_id=graph_user_id,
            token_blob=self._build_token_blob(token_result),
            token_expires_at_utc=self._token_expiry(token_result),
            scopes=self.scope_list,
        )

    def _token_expiry(self, token_result: dict[str, Any]) -> Optional[datetime]:
        expires_in = token_result.get("expires_in")
        if expires_in is None:
            return None
        return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

    def _build_token_blob(
        self,
        token_result: dict[str, Any],
        *,
        fallback_refresh_token: Optional[str] = None,
    ) -> dict[str, Any]:
        refresh_token = token_result.get("refresh_token") or fallback_refresh_token
        return {
            "access_token": token_result.get("access_token"),
            "refresh_token": refresh_token,
            "token_type": token_result.get("token_type", "Bearer"),
            "scope": token_result.get("scope", " ".join(self.scope_list)),
            "id_token": token_result.get("id_token"),
        }

    def _message_select_fields(self) -> str:
        return (
            "id,internetMessageId,conversationId,receivedDateTime,sentDateTime,lastModifiedDateTime,"
            "subject,from,toRecipients,ccRecipients,bodyPreview,body,hasAttachments,parentFolderId"
        )

    def _graph_get(self, path: str, *, access_token: str, query: Optional[dict[str, str]] = None) -> dict[str, Any]:
        return self._graph_request(self._graph_url(path, query), access_token=access_token)

    def _graph_post(self, path: str, *, access_token: str, json_payload: dict[str, Any]) -> dict[str, Any]:
        return self._graph_request(
            self._graph_url(path, None),
            access_token=access_token,
            method="POST",
            json_payload=json_payload,
        )

    def _graph_patch(self, path: str, *, access_token: str, json_payload: dict[str, Any]) -> dict[str, Any]:
        return self._graph_request(
            self._graph_url(path, None),
            access_token=access_token,
            method="PATCH",
            json_payload=json_payload,
        )

    def _graph_url(self, path: str, query: Optional[dict[str, str]]) -> str:
        url = path if path.startswith("http") else f"{MICROSOFT_GRAPH_BASE_URL}{path}"
        if query:
            return f"{url}?{parse.urlencode(query)}"
        return url

    def _post_form(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        data = parse.urlencode(form).encode("utf-8")
        req = request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise GraphServiceError(detail or str(exc), status_code=exc.code) from exc
        except error.URLError as exc:
            raise GraphServiceError(str(exc)) from exc

    def _graph_request(
        self,
        url: str,
        *,
        access_token: str,
        method: str = "GET",
        json_payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        data = None
        req = request.Request(url, method=method)
        if json_payload is not None:
            data = json.dumps(json_payload).encode("utf-8")
            req.data = data
            req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header('Prefer', 'IdType="ImmutableId"')
        try:
            with request.urlopen(req, timeout=60) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise GraphServiceError(detail or str(exc), status_code=exc.code) from exc
        except error.URLError as exc:
            raise GraphServiceError(str(exc)) from exc


def parse_graph_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise GraphServiceError(f"invalid Graph datetime: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def message_body_to_html(body: dict[str, Any]) -> tuple[str, str]:
    content_type = str(body.get("contentType") or "html").lower()
    content = str(body.get("content") or "")
    if content_type == "text":
        escaped = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = "<pre>" + escaped + "</pre>"
        return "text/plain", html
    return "text/html", content


def attachment_to_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "attachment_id": item.get("id") or secrets.token_hex(8),
        "graph_attachment_id": item.get("id"),
        "name": item.get("name") or "attachment.bin",
        "content_type": item.get("contentType"),
        "size_bytes": item.get("size"),
        "is_inline": bool(item.get("isInline", False)),
        "content_base64": item.get("contentBytes") or base64.b64encode(b"").decode("utf-8"),
    }


graph_service = MicrosoftGraphService()
