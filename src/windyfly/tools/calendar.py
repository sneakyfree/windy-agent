"""Calendar tool — "What's on my calendar today?"

Google Calendar integration via OAuth2. Falls back gracefully to
the local reminders system when not configured.

Setup: windy setup-calendar → opens browser for OAuth consent.
Requires: google-api-python-client, google-auth-oauthlib (optional deps).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_CREDS_PATH = Path(os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "data/google_calendar_creds.json"))
_TOKEN_PATH = Path(os.environ.get("GOOGLE_CALENDAR_TOKEN", "data/google_calendar_token.json"))


def _is_configured() -> bool:
    """Check if Google Calendar is configured."""
    return _TOKEN_PATH.exists()


def _get_service():
    """Get authenticated Google Calendar service."""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        return None

    if not _TOKEN_PATH.exists():
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH))
        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            _TOKEN_PATH.write_text(creds.to_json())
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        logger.warning("Google Calendar auth failed: %s", e)
        return None


def get_today_events() -> dict[str, Any]:
    """Get today's calendar events."""
    if not _is_configured():
        return _not_configured_response()

    service = _get_service()
    if not service:
        return {"events": [], "error": "Calendar authentication failed. Run: windy setup-calendar"}

    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    end = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()

    return _fetch_events(service, start, end, "today")


def get_upcoming_events(days: int = 7) -> dict[str, Any]:
    """Get upcoming events for the next N days."""
    if not _is_configured():
        return _not_configured_response()

    service = _get_service()
    if not service:
        return {"events": [], "error": "Calendar authentication failed."}

    now = datetime.now(timezone.utc)
    start = now.isoformat()
    end = (now + timedelta(days=days)).isoformat()

    return _fetch_events(service, start, end, f"next {days} days")


def create_event(
    title: str,
    start: str,
    end: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a calendar event."""
    if not _is_configured():
        return _not_configured_response()

    service = _get_service()
    if not service:
        return {"success": False, "error": "Calendar authentication failed."}

    # Default end = start + 1 hour
    if not end:
        try:
            start_dt = datetime.fromisoformat(start)
            end = (start_dt + timedelta(hours=1)).isoformat()
        except ValueError:
            end = start

    event = {
        "summary": title,
        "start": {"dateTime": start, "timeZone": "UTC"},
        "end": {"dateTime": end, "timeZone": "UTC"},
    }
    if description:
        event["description"] = description

    try:
        result = service.events().insert(calendarId="primary", body=event).execute()
        return {
            "success": True,
            "event_id": result.get("id", ""),
            "message": f"Created event: {title}",
            "link": result.get("htmlLink", ""),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _fetch_events(service, start: str, end: str, period: str) -> dict[str, Any]:
    """Fetch events from Google Calendar."""
    try:
        result = service.events().list(
            calendarId="primary",
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute()

        events = []
        for e in result.get("items", []):
            start_time = e.get("start", {}).get("dateTime", e.get("start", {}).get("date", ""))
            events.append({
                "title": e.get("summary", "Untitled"),
                "start": start_time,
                "end": e.get("end", {}).get("dateTime", ""),
                "description": e.get("description", ""),
                "id": e.get("id", ""),
            })

        if not events:
            return {"events": [], "message": f"No events {period}."}

        lines = [f"📅 {len(events)} event(s) {period}:\n"]
        for ev in events:
            time_str = ev["start"].split("T")[1][:5] if "T" in ev["start"] else ev["start"]
            lines.append(f"  • {time_str} — {ev['title']}")

        return {"events": events, "message": "\n".join(lines)}
    except Exception as e:
        return {"events": [], "error": str(e)}


def _not_configured_response() -> dict[str, Any]:
    """Response when Google Calendar is not configured."""
    return {
        "events": [],
        "message": (
            "I don't have access to your calendar yet. "
            "Run `windy setup-calendar` to connect Google Calendar. "
            "In the meantime, I can set reminders for you — just ask!"
        ),
    }


def setup_calendar_oauth() -> bool:
    """Run the Google Calendar OAuth2 setup flow.

    Opens browser for consent. Returns True on success.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        logger.error("Install google-auth-oauthlib: pip install google-api-python-client google-auth-oauthlib")
        return False

    if not _CREDS_PATH.exists():
        logger.error("Google Calendar credentials file not found: %s", _CREDS_PATH)
        logger.error("Download from Google Cloud Console → APIs & Services → Credentials")
        return False

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(_CREDS_PATH),
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        creds = flow.run_local_server(port=0)
        _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_PATH.write_text(creds.to_json())
        logger.info("Google Calendar connected successfully!")
        return True
    except Exception as e:
        logger.error("Calendar OAuth setup failed: %s", e)
        return False


def register_calendar_tools(registry: ToolRegistry) -> None:
    """Register calendar tools with the LLM."""
    registry.register(
        name="get_today_events",
        description=(
            "Get today's calendar events. Use when the user asks "
            "'What's on my calendar?', 'Do I have any meetings today?', "
            "'What's my schedule?'. Falls back to reminders if calendar not set up."
        ),
        parameters={"type": "object", "properties": {}},
        fn=get_today_events,
    )

    registry.register(
        name="get_upcoming_events",
        description="Get calendar events for the next N days. Use for 'What's my schedule this week?'.",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days ahead (default: 7)"},
            },
        },
        fn=lambda days=7: get_upcoming_events(days),
    )

    registry.register(
        name="create_event",
        description=(
            "Create a calendar event. Use when the user says 'Schedule a meeting', "
            "'Add X to my calendar', 'Book time for...'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "start": {"type": "string", "description": "Start time (ISO format or natural language)"},
                "end": {"type": "string", "description": "End time (optional, defaults to +1 hour)"},
                "description": {"type": "string", "description": "Optional event description"},
            },
            "required": ["title", "start"],
        },
        fn=create_event,
    )
