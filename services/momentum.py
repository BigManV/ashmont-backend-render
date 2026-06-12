from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx

import owners
from config import get_settings
from utils import ensure_aware_utc, is_valid_email, parse_dt, utc_now


def _iso(value: datetime) -> str:
    return ensure_aware_utc(value).isoformat().replace("+00:00", "Z")


def _owner_host(owner_key: str | None) -> dict | None:
    settings = get_settings()
    normalized = owners.normalize_owner_key(owner_key)
    email = (
        settings.aditya_notification_email
        if normalized == "aditya"
        else settings.archit_notification_email
    )
    email = str(email or "").strip().lower()
    if not is_valid_email(email):
        fallback = str(settings.aditya_notification_email or settings.archit_notification_email or "").strip().lower()
        email = fallback if is_valid_email(fallback) else ""
    if not email:
        return None
    return {"name": owners.owner_name(normalized), "email": email}


def _attendees(lead: dict) -> list[dict]:
    attendee = {
        "name": lead.get("full_name") or "Ashmont lead",
        "isInternal": False,
    }
    email = str(lead.get("email") or "").strip().lower()
    if is_valid_email(email):
        attendee["email"] = email
    return [attendee]


def _transcript_segments(transcript: str | None) -> dict | None:
    text = str(transcript or "").strip()
    if not text:
        return None
    segments = []
    for index, line in enumerate(part.strip() for part in text.splitlines() if part.strip()):
        speaker = "Speaker"
        body = line
        if ":" in line:
            possible_speaker, possible_body = line.split(":", 1)
            if possible_speaker.strip() and possible_body.strip():
                speaker = possible_speaker.strip()[:80]
                body = possible_body.strip()
        segments.append(
            {
                "speaker": {"name": speaker},
                "text": body,
                "timestampSeconds": float(index * 10),
            }
        )
    return {"segments": segments} if segments else None


async def _post_user_provided_meeting(meeting: dict, *, process_imported_meeting: bool) -> dict:
    settings = get_settings()
    if not settings.momentum_api_key:
        return {"ok": False, "configured": False, "error": "MOMENTUM_API_KEY is not configured."}

    payload = {
        "meeting": meeting,
        "processImportedMeeting": process_imported_meeting,
    }
    url = f"{settings.momentum_api_base_url.rstrip('/')}/v1/user-provided-meeting"
    headers = {
        "X-API-Key": settings.momentum_api_key,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, json=payload, headers=headers)
    body: Any
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if response.status_code >= 400:
        return {
            "ok": False,
            "configured": True,
            "status_code": response.status_code,
            "error": body,
            "endpoint": url,
        }
    return {"ok": True, "configured": True, "status_code": response.status_code, "data": body}


async def ingest_retell_call(lead: dict, call_attempt: dict | None, call_payload: dict, transcript: str | None) -> dict:
    host = _owner_host(lead.get("owner_key"))
    if not host:
        return {"ok": False, "configured": False, "error": "No valid Momentum host email is configured."}

    end_time = parse_dt(call_attempt.get("ended_at")) if call_attempt and call_attempt.get("ended_at") else utc_now()
    started_value = call_attempt.get("started_at") if call_attempt else None
    duration = int(call_attempt.get("duration_seconds") or 0) if call_attempt else 0
    start_time = parse_dt(started_value) if started_value else end_time - timedelta(seconds=max(duration, 60))
    if start_time >= end_time:
        end_time = start_time + timedelta(seconds=max(duration, 60))

    retell_call_id = call_payload.get("call_id") or call_payload.get("id") or (call_attempt or {}).get("retell_call_id")
    meeting = {
        "id": str(retell_call_id or (call_attempt or {}).get("id") or lead["id"]),
        "title": f"Ashmont GL call - {lead.get('full_name') or 'Lead'}",
        "startTime": _iso(start_time),
        "endTime": _iso(end_time),
        "host": host,
        "attendees": _attendees(lead),
    }
    recording_url = call_payload.get("recording_url") or call_payload.get("recordingUrl") or (call_attempt or {}).get("recording_url")
    if recording_url:
        meeting["recordingUrl"] = str(recording_url)
    transcript_payload = _transcript_segments(transcript)
    if transcript_payload:
        meeting["transcript"] = transcript_payload
    return await _post_user_provided_meeting(meeting, process_imported_meeting=True)


async def send_appointment_notification(
    *,
    appointment: dict,
    lead: dict,
    booking: dict,
    fallback_used: bool = False,
) -> dict:
    host = _owner_host(appointment.get("owner_key") or lead.get("owner_key"))
    if not host:
        return {"ok": False, "configured": False, "error": "No valid Momentum host email is configured."}

    start_time = parse_dt(appointment.get("start_time")) if appointment.get("start_time") else utc_now()
    end_time = parse_dt(appointment.get("end_time")) if appointment.get("end_time") else start_time + timedelta(minutes=30)
    meeting = {
        "id": str(appointment.get("external_booking_id") or appointment.get("cal_booking_id") or appointment["id"]),
        "title": appointment.get("event_title") or f"Ashmont GL consultation - {lead.get('full_name') or 'Lead'}",
        "startTime": _iso(start_time),
        "endTime": _iso(end_time),
        "host": host,
        "attendees": _attendees(lead),
    }
    meeting_url = booking.get("meeting_url") or appointment.get("meeting_url")
    if meeting_url:
        meeting["callUrl"] = str(meeting_url)
    result = await _post_user_provided_meeting(meeting, process_imported_meeting=False)
    return {
        **result,
        "provider": "momentum",
        "fallback_used": fallback_used,
        "appointment_id": appointment.get("id"),
    }
