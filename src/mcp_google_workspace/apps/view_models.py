"""View-model builders for dashboard, weekly calendar, and detail views."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pytz

from ..gmail.mime_utils import decode_rfc2047, extract_message_bodies
from .schemas import (
    DashboardCard,
    DashboardCardAction,
    DashboardSection,
    DashboardState,
    DashboardViewModel,
    EmailDetailViewModel,
    EventDetailAttendee,
    EventDetailViewModel,
    WeeklyCalendarDay,
    WeeklyCalendarEvent,
    WeeklyCalendarViewModel,
)


def _event_start(event: dict[str, Any]) -> str:
    start = event.get("start", {})
    return start.get("dateTime") or start.get("date") or ""


def _event_end(event: dict[str, Any]) -> str:
    end = event.get("end", {})
    return end.get("dateTime") or end.get("date") or ""


def _event_title(event: dict[str, Any]) -> str:
    return event.get("summary") or "(No title)"


def _normalize_iso(value: str) -> str:
    return value.replace("Z", "+00:00")


def _parse_event_start_local(event: dict[str, Any], tz: pytz.BaseTzInfo) -> datetime:
    start = event.get("start", {})
    if "dateTime" in start:
        parsed = datetime.fromisoformat(_normalize_iso(start["dateTime"]))
        if parsed.tzinfo is None:
            parsed = pytz.UTC.localize(parsed)
        return parsed.astimezone(tz)
    if "date" in start:
        return tz.localize(datetime.combine(date.fromisoformat(start["date"]), time.min))
    return tz.localize(datetime.combine(date.today(), time.min))


def _parse_event_end_local(event: dict[str, Any], tz: pytz.BaseTzInfo) -> datetime:
    end = event.get("end", {})
    if "dateTime" in end:
        parsed = datetime.fromisoformat(_normalize_iso(end["dateTime"]))
        if parsed.tzinfo is None:
            parsed = pytz.UTC.localize(parsed)
        return parsed.astimezone(tz)
    if "date" in end:
        return tz.localize(datetime.combine(date.fromisoformat(end["date"]), time.min))
    return tz.localize(datetime.combine(date.today(), time.min))


def _is_all_day(event: dict[str, Any]) -> bool:
    start = event.get("start", {})
    return "date" in start and "dateTime" not in start


def _self_response_status(event: dict[str, Any]) -> str | None:
    attendees = event.get("attendees") or []
    for attendee in attendees:
        if isinstance(attendee, dict) and attendee.get("self"):
            return attendee.get("responseStatus")
    return None


def _description_snippet(description: str | None) -> str | None:
    if not description:
        return None
    clean = " ".join(description.split())
    if not clean:
        return None
    return clean[:140] + "..." if len(clean) > 140 else clean


def build_weekly_calendar_view_model(
    *,
    anchor_date: date,
    timezone_name: str,
    events: list[dict[str, Any]],
    include_weekend: bool,
) -> WeeklyCalendarViewModel:
    tz = pytz.timezone(timezone_name)
    days_since_sunday = (anchor_date.weekday() + 1) % 7
    week_start = anchor_date - timedelta(days=days_since_sunday)
    day_count = 7 if include_weekend else 5
    days: list[WeeklyCalendarDay] = []
    day_map: dict[date, WeeklyCalendarDay] = {}
    today_local = datetime.now(tz).date()

    for offset in range(day_count):
        current = week_start + timedelta(days=offset)
        day = WeeklyCalendarDay(
            date=current,
            day_label=current.strftime("%a"),
            is_today=current == today_local,
        )
        days.append(day)
        day_map[current] = day

    for event in events:
        start_local = _parse_event_start_local(event, tz)
        end_local = _parse_event_end_local(event, tz)
        day = day_map.get(start_local.date())
        if day is None:
            continue
        attendees = event.get("attendees") or []
        conference_data = event.get("conferenceData") or {}
        normalized = WeeklyCalendarEvent(
            event_id=event.get("id"),
            calendar_id=event.get("calendar_id"),
            title=_event_title(event),
            start=start_local.isoformat(),
            end=end_local.isoformat(),
            all_day=_is_all_day(event),
            status=event.get("status", "confirmed"),
            attendee_response_status=_self_response_status(event),
            location=event.get("location"),
            description_snippet=_description_snippet(event.get("description")),
            attendee_count=len(attendees),
            has_conference=bool(conference_data.get("entryPoints")),
            color_id=event.get("colorId"),
        )
        if normalized.all_day:
            day.all_day_events.append(normalized)
        else:
            day.timed_events.append(normalized)

    for day in days:
        day.timed_events.sort(key=lambda item: item.start)

    fallback_lines = [
        f"Weekly calendar view ({timezone_name})",
        f"Week of {week_start.isoformat()} to {(week_start + timedelta(days=day_count - 1)).isoformat()}",
    ]
    for day in days:
        fallback_lines.append(
            f"- {day.day_label} {day.date.isoformat()}: "
            f"{len(day.all_day_events)} all-day, {len(day.timed_events)} timed"
        )

    return WeeklyCalendarViewModel(
        week_start=week_start,
        week_end=week_start + timedelta(days=day_count - 1),
        timezone=timezone_name,
        total_events=sum(len(day.all_day_events) + len(day.timed_events) for day in days),
        days=days,
        fallback_text="\n".join(fallback_lines),
    )


def _build_calendar_card(events: list[dict[str, Any]]) -> DashboardCard:
    items = [
        {
            "event_id": event.get("id"),
            "title": _event_title(event),
            "start": _event_start(event),
            "end": _event_end(event),
            "status": event.get("status", "confirmed"),
        }
        for event in events[:15]
    ]
    summary = f"{len(events)} scheduled event(s)"
    fallback = "\n".join(
        f"- {item['title']} ({item['start']} - {item['end']})"
        for item in items[:5]
    ) or "No events scheduled."
    return DashboardCard(
        id="calendar-agenda",
        title="Calendar",
        card_type="calendar",
        summary=summary,
        fallback_text=fallback,
        data={"events": items, "total": len(events)},
        actions=[
            DashboardCardAction(
                id="calendar-refresh",
                label="Refresh schedule",
                tool_name="apps_get_dashboard",
            )
        ],
    )


def _build_inbox_card(unread_count: int, messages: list[dict[str, Any]]) -> DashboardCard:
    normalized = [
        {
            "id": msg.get("id"),
            "subject": msg.get("subject") or "(No subject)",
            "from": msg.get("from") or "(Unknown sender)",
            "date": msg.get("date"),
            "snippet": msg.get("snippet"),
        }
        for msg in messages[:10]
    ]
    fallback = "\n".join(
        f"- {msg['subject']} — {msg['from']}"
        for msg in normalized[:5]
    ) or "No recent inbox messages."
    return DashboardCard(
        id="inbox-summary",
        title="Inbox",
        card_type="inbox",
        summary=f"{unread_count} unread message(s)",
        fallback_text=fallback,
        data={"unread_count": unread_count, "messages": normalized},
        actions=[
            DashboardCardAction(
                id="inbox-refresh",
                label="Refresh inbox",
                tool_name="apps_get_dashboard",
            )
        ],
    )


def _build_prep_card(events: list[dict[str, Any]]) -> DashboardCard:
    prep_items: list[dict[str, Any]] = []
    for event in events[:8]:
        attendees = event.get("attendees") or []
        prep_items.append(
            {
                "event_id": event.get("id"),
                "title": _event_title(event),
                "start": _event_start(event),
                "attendee_count": len(attendees),
                "has_description": bool(event.get("description")),
            }
        )
    fallback = "\n".join(
        f"- Prep for {item['title']} at {item['start']}"
        for item in prep_items[:4]
    ) or "No meetings require preparation."
    return DashboardCard(
        id="meeting-prep",
        title="Meeting Prep",
        card_type="prep",
        summary=f"{len(prep_items)} upcoming meeting(s) to prep",
        fallback_text=fallback,
        data={"prep_items": prep_items},
    )


def build_dashboard_view_model(
    state: DashboardState,
    calendar_events: list[dict[str, Any]],
    unread_count: int,
    inbox_messages: list[dict[str, Any]],
    section_errors: dict[str, str] | None = None,
) -> DashboardViewModel:
    sections = [
        DashboardSection(
            id="schedule",
            title="Schedule",
            cards=[_build_calendar_card(calendar_events), _build_prep_card(calendar_events)],
            fallback_text="Calendar schedule and prep insights.",
        ),
        DashboardSection(
            id="communications",
            title="Communications",
            cards=[_build_inbox_card(unread_count, inbox_messages)],
            fallback_text="Inbox status and latest communication context.",
        ),
    ]
    return DashboardViewModel(
        title=f"Workspace Dashboard ({state.view})",
        generated_at_utc=datetime.now(timezone.utc),
        state=state,
        sections=sections,
        warnings=[] if not section_errors else ["One or more sections failed to load completely."],
        section_errors=section_errors or {},
    )


def _extract_event_timezone(event: dict[str, Any]) -> str | None:
    start = event.get("start", {})
    end = event.get("end", {})
    return start.get("timeZone") or end.get("timeZone")


def _extract_conference(event: dict[str, Any]) -> tuple[str | None, str | None]:
    conference = event.get("conferenceData") or {}
    for entry in conference.get("entryPoints", []):
        if entry.get("uri"):
            return entry.get("uri"), entry.get("entryPointType")
    return None, None


def build_event_detail_view_model(event: dict[str, Any], calendar_id: str) -> EventDetailViewModel:
    attendees: list[EventDetailAttendee] = []
    for attendee in event.get("attendees") or []:
        email = attendee.get("email")
        if not email:
            continue
        attendees.append(
            EventDetailAttendee(
                email=email,
                display_name=attendee.get("displayName"),
                optional=bool(attendee.get("optional")),
                organizer=bool(attendee.get("organizer")),
                self=bool(attendee.get("self")),
                response_status=attendee.get("responseStatus"),
            )
        )

    conference_link, conference_provider = _extract_conference(event)
    organizer = event.get("organizer") or {}

    return EventDetailViewModel(
        event_id=event.get("id") or "",
        calendar_id=calendar_id,
        title=_event_title(event),
        start=_event_start(event),
        end=_event_end(event),
        timezone=_extract_event_timezone(event),
        status=event.get("status", "confirmed"),
        location=event.get("location"),
        description=event.get("description"),
        conference_link=conference_link,
        conference_provider=conference_provider,
        organizer_email=organizer.get("email"),
        organizer_name=organizer.get("displayName"),
        attendees=attendees,
    )


def _headers_map(message: dict[str, Any]) -> dict[str, str]:
    payload = message.get("payload") or {}
    return {
        header.get("name", "").lower(): header.get("value", "")
        for header in payload.get("headers") or []
    }


def build_email_detail_view_model(message: dict[str, Any]) -> EmailDetailViewModel:
    headers = _headers_map(message)
    bodies = extract_message_bodies(message.get("payload") or {})
    labels = message.get("labelIds") or []
    return EmailDetailViewModel(
        message_id=message.get("id") or "",
        thread_id=message.get("threadId"),
        subject=decode_rfc2047(headers.get("subject")) or "(No subject)",
        from_value=decode_rfc2047(headers.get("from")) or "(Unknown sender)",
        to=decode_rfc2047(headers.get("to")),
        cc=decode_rfc2047(headers.get("cc")),
        bcc=decode_rfc2047(headers.get("bcc")),
        date=headers.get("date"),
        snippet=message.get("snippet"),
        text_body=bodies.get("text") or None,
        html_body=bodies.get("html") or None,
        labels=labels,
        is_unread="UNREAD" in labels,
    )


