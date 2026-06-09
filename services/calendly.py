from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

import owners
from config import get_settings


def _headers() -> dict:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.calendly_api_key}",
        "Content-Type": "application/json",
    }


def event_type_uri_for_owner(owner_key: str | None) -> str:
    settings = get_settings()
    key = owners.normalize_owner_key(owner_key)
    per_owner = getattr(settings, f"calendly_{key}_event_type_uri", "") or ""
    return per_owner or settings.calendly_event_type_uri


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_slot_time(value: Any, fallback_tz=timezone.utc) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=fallback_tz)
    return parsed.astimezone(timezone.utc)


def _extract_url(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("join_url", "location", "url", "meeting_url"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                return candidate
        for child in value.values():
            found = _extract_url(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _extract_url(child)
            if found:
                return found
    return None


def availability_has_exact_slot(data: dict, start_time: datetime) -> bool:
    expected = start_time.astimezone(timezone.utc)
    collection = data.get("collection") if isinstance(data, dict) else []
    if isinstance(collection, list):
        for slot in collection:
            if not isinstance(slot, dict):
                continue
            status = str(slot.get("status") or "available").lower()
            if status and status != "available":
                continue
            parsed = _parse_slot_time(slot.get("start_time") or slot.get("start"))
            if parsed and abs((parsed - expected).total_seconds()) < 60:
                return True
    return False


async def get_availability(start_time: datetime, end_time: datetime, owner_key: str | None = None) -> dict:
    settings = get_settings()
    selected_owner_key = owners.normalize_owner_key(owner_key)
    event_type_uri = event_type_uri_for_owner(selected_owner_key)
    if not settings.calendly_api_key or not event_type_uri:
        return {
            "ok": False,
            "configured": False,
            "owner_key": selected_owner_key,
            "owner_name": owners.owner_name(selected_owner_key),
            "provider": "calendly",
            "error": f"CALENDLY_API_KEY and the Calendly event type URI for {owners.owner_name(selected_owner_key)} are required.",
            "slots": [],
        }

    params = {
        "event_type": event_type_uri,
        "start_time": _iso_utc(start_time),
        "end_time": _iso_utc(end_time),
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{settings.calendly_api_base_url.rstrip('/')}/event_type_available_times",
            params=params,
            headers=_headers(),
        )
    if response.status_code >= 400:
        return {
            "ok": False,
            "configured": True,
            "owner_key": selected_owner_key,
            "owner_name": owners.owner_name(selected_owner_key),
            "provider": "calendly",
            "error": response.text,
            "status_code": response.status_code,
            "slots": [],
        }
    data = response.json()
    collection = data.get("collection") if isinstance(data, dict) else []
    return {
        "ok": True,
        "configured": True,
        "owner_key": selected_owner_key,
        "owner_name": owners.owner_name(selected_owner_key),
        "provider": "calendly",
        "event_type_id": event_type_uri,
        "available": availability_has_exact_slot(data, start_time),
        "slots": collection if isinstance(collection, list) else [],
        "data": data,
    }


def _split_guests(value: str | None) -> list[str]:
    return [part.strip() for part in str(value or "").replace(";", ",").split(",") if part.strip()]


def _booking_payload(booking: dict, event_type_uri: str, owner_key: str) -> dict:
    settings = get_settings()
    invitee_email = booking.get("attendee_email") or "no-reply@ashmontinsurance.com"
    payload = {
        "event_type": event_type_uri,
        "start_time": _iso_utc(booking["start_time"]),
        "invitee": {
            "name": booking["attendee_name"],
            "email": invitee_email,
            "timezone": settings.calendar_timezone,
        },
        "tracking": {
            "utm_source": "ashmont-ai",
            "utm_medium": "calendar-api",
            "utm_campaign": "contractor_gl",
            "utm_content": str(booking.get("lead_id") or ""),
            "utm_term": str(booking.get("call_attempt_id") or ""),
        },
        "questions_and_answers": [
            {
                "question": "Business type",
                "answer": booking.get("business_type") or "Not provided",
            },
            {
                "question": "Ashmont notes",
                "answer": booking.get("notes") or "Booked by Ashmont AI qualification system.",
            },
            {
                "question": "Assigned owner",
                "answer": owners.owner_name(owner_key),
            },
        ],
    }
    location_kind = settings.calendly_location_kind.strip()
    if location_kind:
        payload["location"] = {"kind": location_kind}
        if settings.calendly_location_value.strip():
            payload["location"]["location"] = settings.calendly_location_value.strip()
    guests = _split_guests(settings.calendly_event_guests)
    if guests:
        payload["event_guests"] = guests
    return payload


async def book_meeting(booking: dict) -> dict:
    settings = get_settings()
    selected_owner_key = owners.normalize_owner_key(booking.get("owner_key"))
    event_type_uri = event_type_uri_for_owner(selected_owner_key)
    if not settings.calendly_api_key or not event_type_uri:
        return {
            "ok": False,
            "configured": False,
            "owner_key": selected_owner_key,
            "owner_name": owners.owner_name(selected_owner_key),
            "provider": "calendly",
            "error": f"CALENDLY_API_KEY and the Calendly event type URI for {owners.owner_name(selected_owner_key)} are required.",
        }

    payload = _booking_payload(booking, event_type_uri, selected_owner_key)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{settings.calendly_api_base_url.rstrip('/')}/invitees",
            json=payload,
            headers=_headers(),
        )
    if response.status_code >= 400:
        return {
            "ok": False,
            "configured": True,
            "owner_key": selected_owner_key,
            "owner_name": owners.owner_name(selected_owner_key),
            "provider": "calendly",
            "error": response.text,
            "status_code": response.status_code,
        }
    data = response.json()
    resource = data.get("resource") if isinstance(data, dict) and isinstance(data.get("resource"), dict) else data
    booking_id = (
        resource.get("uri")
        or resource.get("event")
        or resource.get("uuid")
        or resource.get("id")
        or ""
    )
    meeting_url = _extract_url(resource.get("location"))
    return {
        "ok": True,
        "configured": True,
        "owner_key": selected_owner_key,
        "owner_name": owners.owner_name(selected_owner_key),
        "provider": "calendly",
        "event_type_id": event_type_uri,
        "data": data,
        "booking_id": str(booking_id),
        "meeting_url": meeting_url,
    }
