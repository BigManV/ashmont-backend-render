from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import httpx

import owners
from config import get_settings
from utils import parse_dt


SLOTS_API_VERSION = "2024-09-04"
BOOKINGS_API_VERSION = "2026-02-25"
BOOKING_CREATED_EVENTS = {"BOOKING_CREATED", "BOOKING_REQUESTED", "BOOKING_PAID"}
BOOKING_CANCELLED_EVENTS = {"BOOKING_CANCELLED", "BOOKING_REJECTED"}
BOOKING_RESCHEDULED_EVENTS = {"BOOKING_RESCHEDULED"}


def _headers(api_version: str | None = None) -> dict:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.cal_api_key}",
        "cal-api-version": api_version or settings.cal_api_version,
        "Content-Type": "application/json",
    }


def _event_type_id(owner_key: str | None) -> str:
    settings = get_settings()
    return owners.event_type_id_for_owner(settings, owner_key)


def _cal_event_type_value(owner_key: str | None):
    event_type_id = _event_type_id(owner_key)
    try:
        return int(event_type_id)
    except (TypeError, ValueError):
        return event_type_id


def _parse_slot_time(value, fallback_tz=timezone.utc) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=fallback_tz)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def verify_webhook_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not secret:
        return False
    provided = signature.strip()
    if provided.lower().startswith("sha256="):
        provided = provided.split("=", 1)[1].strip()
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, provided)


def webhook_event_name(envelope: dict) -> str:
    return str(envelope.get("triggerEvent") or envelope.get("event") or envelope.get("type") or "").strip().upper()


def webhook_booking_payload(envelope: dict) -> dict:
    payload = envelope.get("payload")
    if isinstance(payload, dict):
        return payload
    return envelope


def webhook_booking_status(event_name: str) -> str:
    event = str(event_name or "").strip().upper()
    if event in BOOKING_CANCELLED_EVENTS:
        return "cancelled"
    if event in BOOKING_RESCHEDULED_EVENTS:
        return "booked"
    return "booked"


def _first_present(*values):
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _response_value(responses: dict, *keys: str):
    for key in keys:
        value = responses.get(key)
        if isinstance(value, dict):
            nested = _first_present(value.get("value"), value.get("text"), value.get("label"))
            if nested:
                return nested
        if value:
            return value
    return None


def _first_attendee(booking: dict) -> dict:
    attendees = booking.get("attendees")
    if isinstance(attendees, list) and attendees and isinstance(attendees[0], dict):
        return attendees[0]
    attendee = booking.get("attendee")
    return attendee if isinstance(attendee, dict) else {}


def webhook_booking_uid(booking: dict) -> str:
    return str(
        _first_present(
            booking.get("uid"),
            booking.get("bookingUid"),
            booking.get("booking_uid"),
            booking.get("id"),
        )
        or ""
    )


def webhook_reschedule_uid(booking: dict) -> str | None:
    value = _first_present(booking.get("rescheduleUid"), booking.get("rescheduledFromUid"), booking.get("rescheduled_from_uid"))
    return str(value) if value else None


def webhook_attendee(booking: dict) -> dict:
    responses = booking.get("responses") if isinstance(booking.get("responses"), dict) else {}
    attendee = _first_attendee(booking)
    name = _first_present(attendee.get("name"), _response_value(responses, "name", "full_name", "fullName"))
    email = _first_present(attendee.get("email"), _response_value(responses, "email", "email_address", "emailAddress"))
    phone = _first_present(attendee.get("phoneNumber"), attendee.get("phone"), _response_value(responses, "phone", "phone_number", "phoneNumber"))
    return {
        "name": str(name or "").strip(),
        "email": str(email or "").strip().lower(),
        "phone": str(phone or "").strip(),
    }


def webhook_metadata(booking: dict) -> dict:
    for key in ("metadata", "metaData"):
        value = booking.get(key)
        if isinstance(value, dict):
            return value
    return {}


def webhook_meeting_url(booking: dict) -> str | None:
    references = booking.get("references")
    if isinstance(references, list):
        for reference in references:
            if isinstance(reference, dict):
                url = _first_present(reference.get("meetingUrl"), reference.get("meeting_url"), reference.get("url"))
                if url:
                    return str(url)
    location = booking.get("location")
    if isinstance(location, dict):
        return _first_present(location.get("link"), location.get("url"), location.get("value"))
    if isinstance(location, str) and location.startswith(("http://", "https://")):
        return location
    return None


def webhook_event_type_id(booking: dict) -> str | None:
    event_type = booking.get("eventType") if isinstance(booking.get("eventType"), dict) else {}
    value = _first_present(
        booking.get("eventTypeId"),
        booking.get("event_type_id"),
        event_type.get("id"),
        event_type.get("slug"),
        booking.get("type"),
    )
    return str(value) if value else None


def webhook_owner_key(booking: dict, default_owner_key: str | None = None) -> str:
    settings = get_settings()
    metadata = webhook_metadata(booking)
    owner_from_metadata = metadata.get("owner_key") or metadata.get("ownerKey")
    if owner_from_metadata:
        return owners.normalize_owner_key(str(owner_from_metadata), default=default_owner_key or "aditya")

    organizer = booking.get("organizer") if isinstance(booking.get("organizer"), dict) else {}
    organizer_email = str(organizer.get("email") or "").strip().lower()
    email_map = {
        (settings.aditya_notification_email or "").strip().lower(): "aditya",
        (settings.archit_notification_email or "").strip().lower(): "archit",
    }
    if organizer_email in email_map and organizer_email:
        return email_map[organizer_email]
    return owners.normalize_owner_key(default_owner_key)


def webhook_lead_id(booking: dict) -> str | None:
    metadata = webhook_metadata(booking)
    value = _first_present(metadata.get("lead_id"), metadata.get("leadId"))
    return str(value) if value else None


def appointment_values_from_webhook(envelope: dict, lead: dict, event_name: str) -> dict:
    booking = webhook_booking_payload(envelope)
    attendee = webhook_attendee(booking)
    owner_key = webhook_owner_key(booking, lead.get("owner_key"))
    start_time = parse_dt(_first_present(booking.get("startTime"), booking.get("start"), booking.get("start_time")))
    end_value = _first_present(booking.get("endTime"), booking.get("end"), booking.get("end_time"))
    end_time = parse_dt(end_value) if end_value else start_time + timedelta(minutes=30)
    organizer = booking.get("organizer") if isinstance(booking.get("organizer"), dict) else {}
    uid = webhook_booking_uid(booking)
    return {
        "lead_id": lead["id"],
        "call_attempt_id": None,
        "cal_booking_id": uid,
        "cal_event_type_id": webhook_event_type_id(booking),
        "calendar_provider": "calcom",
        "external_booking_id": uid,
        "external_event_type_id": webhook_event_type_id(booking),
        "owner_key": owner_key,
        "status": webhook_booking_status(event_name),
        "start_time": start_time,
        "end_time": end_time,
        "timezone": booking.get("timeZone") or organizer.get("timeZone") or get_settings().calendar_timezone,
        "event_title": booking.get("title") or "Ashmont consultation",
        "meeting_url": webhook_meeting_url(booking),
        "invitee_email": attendee.get("email") or lead.get("email"),
        "transcript_verified": False,
        "metadata": {
            "calendar": envelope,
            "calendar_provider": "calcom",
            "trigger_event": event_name,
            "owner_key": owner_key,
            "owner_name": owners.owner_name(owner_key),
            "organizer": organizer,
            "attendee": attendee,
            "reschedule_uid": webhook_reschedule_uid(booking),
            "source": "calcom_webhook",
        },
    }


def availability_has_exact_slot(data: dict, start_time) -> bool:
    expected = start_time.astimezone(timezone.utc)
    candidates: list[datetime] = []

    def visit(value) -> None:
        if isinstance(value, dict):
            for key in ("time", "start", "startTime", "start_time"):
                parsed = _parse_slot_time(value.get(key), expected.tzinfo or timezone.utc)
                if parsed:
                    candidates.append(parsed)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            parsed = _parse_slot_time(value, expected.tzinfo or timezone.utc)
            if parsed:
                candidates.append(parsed)

    visit(data)
    return any(abs((candidate - expected).total_seconds()) < 60 for candidate in candidates)


def _booking_metadata(values: dict) -> dict:
    metadata = {
        "lead_id": values.get("lead_id"),
        "call_attempt_id": values.get("call_attempt_id"),
        "business_type": values.get("business_type"),
        "owner_key": values.get("owner_key"),
        "owner_name": values.get("owner_name"),
        "source": values.get("source"),
    }
    return {key: str(value) for key, value in metadata.items() if value is not None and str(value)}


async def get_availability(start_time, end_time, owner_key: str | None = None) -> dict:
    settings = get_settings()
    selected_owner_key = owners.normalize_owner_key(owner_key)
    event_type_id = _event_type_id(selected_owner_key)
    if not settings.cal_api_key or not event_type_id:
        return {
            "ok": False,
            "configured": False,
            "owner_key": selected_owner_key,
            "owner_name": owners.owner_name(selected_owner_key),
            "provider": "calcom",
            "error": f"CAL_API_KEY and the Cal.com event type for {owners.owner_name(selected_owner_key)} are required.",
            "slots": [],
        }

    params = {
        "eventTypeId": event_type_id,
        "start": start_time.isoformat(),
        "end": end_time.isoformat(),
        "timeZone": settings.calendar_timezone,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{settings.cal_api_base_url}/slots", params=params, headers=_headers(SLOTS_API_VERSION))
    if response.status_code >= 400:
        return {
            "ok": False,
            "configured": True,
            "owner_key": selected_owner_key,
            "owner_name": owners.owner_name(selected_owner_key),
            "provider": "calcom",
            "error": response.text,
            "status_code": response.status_code,
            "slots": [],
        }
    data = response.json()
    return {
        "ok": True,
        "configured": True,
        "owner_key": selected_owner_key,
        "owner_name": owners.owner_name(selected_owner_key),
        "provider": "calcom",
        "event_type_id": str(event_type_id),
        "available": availability_has_exact_slot(data, start_time),
        "slots": data.get("data") or data.get("slots") or [],
        "data": data,
    }


async def book_meeting(booking: dict) -> dict:
    settings = get_settings()
    selected_owner_key = owners.normalize_owner_key(booking.get("owner_key"))
    event_type_id = _event_type_id(selected_owner_key)
    if not settings.cal_api_key or not event_type_id:
        return {
            "ok": False,
            "configured": False,
            "owner_key": selected_owner_key,
            "owner_name": owners.owner_name(selected_owner_key),
            "provider": "calcom",
            "error": f"CAL_API_KEY and the Cal.com event type for {owners.owner_name(selected_owner_key)} are required.",
        }

    payload = {
        "eventTypeId": _cal_event_type_value(selected_owner_key),
        "start": booking["start_time"].isoformat(),
        "attendee": {
            "name": booking["attendee_name"],
            "email": booking.get("attendee_email") or "no-reply@ashmontinsurance.com",
            "timeZone": settings.calendar_timezone,
        },
        "metadata": _booking_metadata(
            {
                "lead_id": booking["lead_id"],
                "call_attempt_id": booking.get("call_attempt_id"),
                "business_type": booking.get("business_type"),
                "owner_key": selected_owner_key,
                "owner_name": owners.owner_name(selected_owner_key),
                "source": "ashmont-ai",
            }
        ),
        "bookingFieldsResponses": {
            "notes": booking.get("notes") or "Booked by Ashmont AI qualification system.",
        },
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{settings.cal_api_base_url}/bookings", json=payload, headers=_headers(BOOKINGS_API_VERSION))
    if response.status_code >= 400:
        return {
            "ok": False,
            "configured": True,
            "owner_key": selected_owner_key,
            "owner_name": owners.owner_name(selected_owner_key),
            "provider": "calcom",
            "error": response.text,
            "status_code": response.status_code,
        }
    data = response.json()
    resource = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    return {
        "ok": True,
        "configured": True,
        "owner_key": selected_owner_key,
        "owner_name": owners.owner_name(selected_owner_key),
        "provider": "calcom",
        "event_type_id": str(event_type_id),
        "data": data,
        "booking_id": str(resource.get("uid") or resource.get("id") or resource.get("bookingUid") or ""),
        "meeting_url": resource.get("meetingUrl") or resource.get("location"),
    }
