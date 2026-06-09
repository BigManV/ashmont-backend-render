from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
OUTBOUND_CALL_START = time(9, 0)
OUTBOUND_CALL_END = time(18, 0)
TIME_AGREEMENT_MARKERS = (
    "that works",
    "works for me",
    "sounds good",
    "that sounds good",
    "yes that works",
    "yes, that works",
    "yes please",
    "yes, please",
    "i can do that",
    "i can make that",
    "that time works",
    "that is fine",
    "that's fine",
)
BOOKING_CONFIRMATION_MARKERS = (
    "book it",
    "book that",
    "book the appointment",
    "go ahead and book",
    "please book",
    "yes book",
    "yes, book",
    "confirm the appointment",
    "schedule it",
    "set it up",
    "lock it in",
    "i'll be there",
    "ill be there",
)
VOICEMAIL_OR_NO_CONSENT_MARKERS = (
    "leave a message",
    "leave me a message",
    "call you back",
    "i'll call you back",
    "ill call you back",
    "voicemail",
    "voice mail",
    "not available",
    "can't take your call",
    "cannot take your call",
    "stop calling",
    "not interested",
    "wrong number",
    "do not call",
    "don't call",
)


def normalize_us_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if raw and raw.startswith("+") and 10 <= len(digits) <= 15:
        return f"+{digits}"
    raise ValueError("Phone number must be a valid US/E.164 number.")


def utc_now() -> datetime:
    return datetime.now(tz=ZoneInfo("UTC"))


def parse_dt(value: str | datetime | int | float | None) -> datetime:
    if value is None:
        return utc_now()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=ZoneInfo("UTC"))
        return value
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=ZoneInfo("UTC"))
    cleaned = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_business_hours_et(start_time: datetime) -> bool:
    local = start_time.astimezone(EASTERN)
    if local.weekday() >= 5:
        return False
    return time(9, 0) <= local.time() < time(18, 0)


def is_outbound_call_time(start_time: datetime | None = None) -> bool:
    local = (start_time or utc_now()).astimezone(EASTERN)
    if local.weekday() >= 5:
        return False
    return OUTBOUND_CALL_START <= local.time() < OUTBOUND_CALL_END


def next_outbound_call_time(start_time: datetime | None = None) -> datetime:
    local = (start_time or utc_now()).astimezone(EASTERN)
    next_time = local
    if next_time.weekday() >= 5:
        days_until_monday = 7 - next_time.weekday()
        next_time = (next_time + timedelta(days=days_until_monday)).replace(
            hour=OUTBOUND_CALL_START.hour,
            minute=OUTBOUND_CALL_START.minute,
            second=0,
            microsecond=0,
        )
    elif next_time.time() < OUTBOUND_CALL_START:
        next_time = next_time.replace(
            hour=OUTBOUND_CALL_START.hour,
            minute=OUTBOUND_CALL_START.minute,
            second=0,
            microsecond=0,
        )
    elif next_time.time() >= OUTBOUND_CALL_END:
        next_time = (next_time + timedelta(days=1)).replace(
            hour=OUTBOUND_CALL_START.hour,
            minute=OUTBOUND_CALL_START.minute,
            second=0,
            microsecond=0,
        )
        while next_time.weekday() >= 5:
            next_time += timedelta(days=1)
    return next_time.astimezone(UTC)


def minutes_until(value: datetime) -> int:
    delta = ensure_aware_utc(value) - utc_now()
    return max(1, int(delta.total_seconds() // 60))


def clean_full_name(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_email(value: str | None) -> str:
    return str(value or "").strip().lower()


def is_valid_email(value: str | None) -> bool:
    text = normalize_email(value)
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text))


def seconds_between(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def compact_text(value: str | None) -> str:
    return " ".join(str(value or "").lower().split())


def contains_voicemail_or_no_consent(value: str | None) -> bool:
    text = compact_text(value)
    return any(marker in text for marker in VOICEMAIL_OR_NO_CONSENT_MARKERS)


def contains_booking_confirmation(value: str | None) -> bool:
    text = compact_text(value)
    if contains_voicemail_or_no_consent(text):
        return False
    return any(marker in text for marker in BOOKING_CONFIRMATION_MARKERS)


def contains_time_agreement(value: str | None) -> bool:
    text = compact_text(value)
    if contains_voicemail_or_no_consent(text):
        return False
    return contains_booking_confirmation(text) or any(marker in text for marker in TIME_AGREEMENT_MARKERS)


def is_truthy_analysis_value(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"yes", "true", "qualified", "booked", "success"}
    return False


def extract_qualification_decision(analysis: object) -> bool | None:
    if not isinstance(analysis, dict):
        return None

    status = analysis.get("qualification_status") or analysis.get("qualification") or analysis.get("lead_status")
    if isinstance(status, str):
        normalized = status.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in {"qualified", "eligible", "good_fit"}:
            return True
        if normalized in {"not_qualified", "unqualified", "disqualified", "not_a_fit", "bad_fit"}:
            return False

    negative_keys = ("not_qualified", "disqualified", "unqualified", "not_a_fit")
    if any(is_truthy_analysis_value(analysis.get(key)) for key in negative_keys):
        return False

    positive_keys = ("qualified", "is_qualified", "lead_qualified", "eligible", "good_fit")
    if any(is_truthy_analysis_value(analysis.get(key)) for key in positive_keys):
        return True

    for key in positive_keys:
        value = analysis.get(key)
        if isinstance(value, str) and value.strip().lower() in {"no", "false", "not qualified", "not_qualified"}:
            return False

    return None


def validate_booking_evidence(
    *,
    customer_confirmed: bool,
    customer_confirmation: str | None,
    agent_recap: str | None = None,
    transcript_excerpt: str | None = None,
) -> tuple[bool, str]:
    evidence = "\n".join(
        part for part in (customer_confirmation, agent_recap, transcript_excerpt) if part
    )
    if contains_voicemail_or_no_consent(evidence):
        return False, "Booking blocked because the evidence looks like voicemail, callback-only, opt-out, or no-consent text."
    if not customer_confirmed:
        return False, "Booking requires explicit customer_confirmed=true from the caller or chat session."
    if not contains_booking_confirmation(customer_confirmation):
        return False, "Booking requires the customer's exact confirmation words, such as 'yes, please book it'."
    if agent_recap is not None and len(agent_recap.strip()) < 10:
        return False, "Booking requires an agent recap of the appointment before final confirmation."
    return True, "Booking evidence verified."


def analysis_claims_booking(analysis: object) -> bool:
    if not isinstance(analysis, dict):
        return False
    keys = ("appointment_booked", "appointment_success", "call_booked", "booked")
    for key in keys:
        if is_truthy_analysis_value(analysis.get(key)):
            return True
    return False


def extract_retell_call(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("call"), dict):
        return payload["call"]
    if isinstance(payload.get("data"), dict):
        data = payload["data"]
        if isinstance(data.get("call"), dict):
            return data["call"]
        return data
    return payload


def first_present(*values):
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None
