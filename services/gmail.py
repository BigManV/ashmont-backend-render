from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from zoneinfo import ZoneInfo

import owners
from config import get_settings
from utils import ensure_aware_utc


def _split_recipients(value: str | None) -> list[str]:
    return [part.strip() for part in str(value or "").replace(";", ",").split(",") if part.strip()]


def _owner_email(owner_key: str | None) -> str:
    settings = get_settings()
    key = owners.normalize_owner_key(owner_key)
    return str(getattr(settings, f"{key}_notification_email", "") or "").strip()


def _format_slot(start_time, end_time) -> str:
    settings = get_settings()
    timezone = ZoneInfo(settings.calendar_timezone)
    start = ensure_aware_utc(start_time).astimezone(timezone)
    end = ensure_aware_utc(end_time).astimezone(timezone) if end_time else None
    date_label = f"{start.strftime('%A, %B')} {start.day}, {start.year}"
    start_label = start.strftime("%I:%M %p").lstrip("0")
    if not end:
        return f"{date_label} at {start_label} {start.tzname()}"
    end_label = end.strftime("%I:%M %p").lstrip("0")
    return f"{date_label}, {start_label} - {end_label} {start.tzname()}"


def build_appointment_notification_message(
    *,
    appointment: dict,
    lead: dict,
    booking: dict,
    fallback_used: bool,
) -> tuple[EmailMessage, list[str]]:
    settings = get_settings()
    owner_key = owners.normalize_owner_key(appointment.get("owner_key") or lead.get("owner_key"))
    owner_name = owners.owner_name(owner_key)
    owner_email = _owner_email(owner_key)
    customer_email = str(appointment.get("invitee_email") or lead.get("email") or "").strip()
    cc_recipients = _split_recipients(settings.appointment_notification_cc)
    recipients = [email for email in [owner_email, customer_email] + cc_recipients if email]
    recipients = list(dict.fromkeys(recipients))

    from_email = settings.gmail_from_email or settings.gmail_username
    from_name = settings.gmail_from_name or "Ashmont Insurance"
    customer_name = lead.get("full_name") or "Customer"
    business_type = lead.get("business_type") or "Business"
    phone_number = lead.get("phone_number") or "No phone number stored"
    meeting_url = appointment.get("meeting_url") or booking.get("meeting_url") or "Meeting link pending"
    calendar_provider = appointment.get("calendar_provider") or booking.get("provider") or settings.normalized_calendar_provider
    booking_id = (
        appointment.get("external_booking_id")
        or appointment.get("cal_booking_id")
        or booking.get("booking_id")
        or "Pending"
    )
    slot = _format_slot(appointment.get("start_time"), appointment.get("end_time"))

    subject = f"Ashmont appointment booked: {customer_name} with {owner_name}"
    fallback_note = "\nOwner fallback was used because the preferred owner was unavailable.\n" if fallback_used else ""
    body = f"""Appointment booked.

Lead: {customer_name}
Business: {business_type}
Phone: {phone_number}
Email: {customer_email or "No email stored"}

Owner: {owner_name}
Time: {slot}
Meeting: {meeting_url}
Calendar provider: {calendar_provider}
External booking ID: {booking_id}
{fallback_note}
This appointment was allocated automatically after the lead was qualified and the customer explicitly confirmed the slot.
"""

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((from_name, from_email))
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    return message, recipients


def _send_message(message: EmailMessage, recipients: list[str]) -> None:
    settings = get_settings()
    with smtplib.SMTP(settings.gmail_smtp_host, settings.gmail_smtp_port, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(settings.gmail_username, settings.gmail_app_password)
        smtp.send_message(message, to_addrs=recipients)


async def send_appointment_notification(
    *,
    appointment: dict,
    lead: dict,
    booking: dict,
    fallback_used: bool,
) -> dict:
    settings = get_settings()
    missing = [
        name for name, value in {
            "GMAIL_USERNAME": settings.gmail_username,
            "GMAIL_APP_PASSWORD": settings.gmail_app_password,
            "GMAIL_FROM_EMAIL/GMAIL_USERNAME": settings.gmail_from_email or settings.gmail_username,
        }.items()
        if not value
    ]
    message, recipients = build_appointment_notification_message(
        appointment=appointment,
        lead=lead,
        booking=booking,
        fallback_used=fallback_used,
    )
    if not recipients:
        return {
            "ok": False,
            "configured": bool(not missing),
            "sent": False,
            "recipients": [],
            "error": "No appointment notification recipients configured.",
        }
    if missing:
        return {
            "ok": False,
            "configured": False,
            "sent": False,
            "recipients": recipients,
            "error": f"Missing Gmail config: {', '.join(missing)}",
        }

    try:
        await asyncio.to_thread(_send_message, message, recipients)
    except Exception as exc:
        return {
            "ok": False,
            "configured": True,
            "sent": False,
            "recipients": recipients,
            "error": str(exc),
        }
    return {
        "ok": True,
        "configured": True,
        "sent": True,
        "recipients": recipients,
        "subject": message["Subject"],
    }
