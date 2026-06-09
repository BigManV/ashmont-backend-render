import hmac
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

import owners
import pytest
from config import Settings
from services import calcom
from services.calendly import availability_has_exact_slot
from utils import (
    contains_booking_confirmation,
    contains_time_agreement,
    contains_voicemail_or_no_consent,
    extract_qualification_decision,
    is_outbound_call_time,
    next_outbound_call_time,
    validate_booking_evidence,
)
from models import CalendarAvailabilityRequest, CalendarBookingRequest, CalendarHoldRequest, LeadIntake, LeadQualificationLogRequest


def test_booking_evidence_blocks_voicemail_callback_text():
    ok, reason = validate_booking_evidence(
        customer_confirmed=True,
        customer_confirmation="Please leave me a message and I will call you back.",
        agent_recap="Tuesday, June 2, 2026 at 10 AM Eastern.",
    )

    assert not ok
    assert "voicemail" in reason.lower() or "callback" in reason.lower()


def test_booking_evidence_requires_explicit_confirmation():
    ok, reason = validate_booking_evidence(
        customer_confirmed=False,
        customer_confirmation="That works.",
        agent_recap="Tuesday, June 2, 2026 at 10 AM Eastern.",
    )

    assert not ok
    assert "customer_confirmed" in reason


def test_booking_confirmation_accepts_clear_booking_words():
    assert contains_booking_confirmation("Yes, please book it.")
    assert not contains_booking_confirmation("I am not available, leave a message.")


def test_time_agreement_is_less_strict_than_final_booking_confirmation():
    assert contains_time_agreement("Tuesday at 2 sounds good.")
    assert not contains_booking_confirmation("Tuesday at 2 sounds good.")


def test_voicemail_detection_catches_no_consent_phrases():
    assert contains_voicemail_or_no_consent("Wrong number. Do not call me again.")


def test_qualification_decision_accepts_explicit_positive_signal():
    assert extract_qualification_decision({"qualified": True, "reason": "Has active contracting work."}) is True
    assert extract_qualification_decision({"qualification_status": "qualified"}) is True


def test_qualification_decision_accepts_explicit_negative_signal():
    assert extract_qualification_decision({"not_qualified": True, "reason": "Personal auto request."}) is False
    assert extract_qualification_decision({"qualification_status": "not qualified"}) is False


def test_qualification_decision_does_not_treat_false_retry_as_disqualified():
    assert extract_qualification_decision({"qualified": False, "retry": True, "scenario": "voicemail_retry"}) is None


def test_optional_lead_email_allows_blank_make_values():
    payload = LeadIntake(
        full_name="Jordan Smith",
        phone_number="+15551234567",
        email="",
        tcpa_consent=True,
    )

    assert payload.email is None


def test_optional_booking_email_allows_blank_make_values():
    payload = CalendarBookingRequest(
        lead_id="00000000-0000-0000-0000-000000000001",
        attendee_name="Jordan Smith",
        attendee_email=" ",
        hold_id="00000000-0000-0000-0000-000000000002",
    )

    assert payload.attendee_email is None


def test_optional_calendar_tool_fields_allow_blank_make_values():
    availability = CalendarAvailabilityRequest(
        start_time="2026-06-08T15:00:00Z",
        end_time="2026-06-08T15:30:00Z",
        preferred_owner_key="",
    )
    hold = CalendarHoldRequest(
        lead_id="00000000-0000-0000-0000-000000000001",
        call_attempt_id=" ",
        start_time="2026-06-08T15:00:00Z",
        end_time="",
        preferred_owner_key="",
    )
    booking = CalendarBookingRequest(
        lead_id="00000000-0000-0000-0000-000000000001",
        call_attempt_id=" ",
        hold_id=" ",
        start_time="",
        end_time="",
        preferred_owner_key="",
        attendee_name="Jordan Smith",
    )
    qualification = LeadQualificationLogRequest(
        lead_id="00000000-0000-0000-0000-000000000001",
        qualified=True,
        call_attempt_id=" ",
    )

    assert availability.preferred_owner_key is None
    assert hold.call_attempt_id is None
    assert hold.end_time is None
    assert hold.preferred_owner_key is None
    assert booking.call_attempt_id is None
    assert booking.hold_id is None
    assert booking.start_time is None
    assert booking.end_time is None
    assert booking.preferred_owner_key is None
    assert qualification.call_attempt_id is None


def test_outbound_call_window_skips_after_hours_to_next_business_morning():
    friday_after_hours = datetime(2026, 5, 29, 19, 30, tzinfo=ZoneInfo("America/New_York"))
    next_allowed = next_outbound_call_time(friday_after_hours).astimezone(ZoneInfo("America/New_York"))

    assert not is_outbound_call_time(friday_after_hours)
    assert next_allowed.weekday() == 0
    assert next_allowed.hour == 9
    assert next_allowed.minute == 0


def test_owner_order_uses_preferred_then_fallback():
    assert owners.owner_order("aditya") == ["aditya", "archit"]
    assert owners.owner_order("archit") == ["archit", "aditya"]


def test_balanced_owner_assignment_prefers_lower_count_then_aditya_tie():
    assert owners.choose_balanced_owner_key({"aditya": 3, "archit": 2}) == "archit"
    assert owners.choose_balanced_owner_key({"aditya": 2, "archit": 2}) == "aditya"


def test_calendar_provider_normalizes_calendly_and_calcom_aliases():
    assert Settings(calendar_provider="Calendly").normalized_calendar_provider == "calendly"
    assert Settings(calendar_provider="cal.com").normalized_calendar_provider == "calcom"


def test_calendly_availability_accepts_exact_available_slot():
    start_time = datetime(2026, 6, 3, 15, 0, tzinfo=ZoneInfo("UTC"))

    assert availability_has_exact_slot(
        {
            "collection": [
                {"start_time": "2026-06-03T15:00:00Z", "status": "available"},
                {"start_time": "2026-06-03T15:30:00Z", "status": "busy"},
            ]
        },
        start_time,
    )

    assert not availability_has_exact_slot(
        {"collection": [{"start_time": "2026-06-03T15:00:00Z", "status": "busy"}]},
        start_time,
    )


def test_calcom_webhook_signature_verifies_raw_body():
    body = b'{"triggerEvent":"BOOKING_CREATED","payload":{"uid":"cal_123"}}'
    secret = "test-secret"
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    assert calcom.verify_webhook_signature(body, signature, secret)
    assert calcom.verify_webhook_signature(body, f"sha256={signature}", secret)
    assert not calcom.verify_webhook_signature(body, "bad-signature", secret)


def test_calcom_webhook_booking_values_preserve_calcom_source_of_truth():
    envelope = {
        "triggerEvent": "BOOKING_CREATED",
        "payload": {
            "uid": "cal_123",
            "eventTypeId": 42,
            "title": "Ashmont consultation",
            "startTime": "2026-06-03T15:00:00Z",
            "endTime": "2026-06-03T15:30:00Z",
            "timeZone": "America/New_York",
            "organizer": {"email": "host@ashmont.example", "name": "Cal Host"},
            "attendees": [{"name": "Jordan Smith", "email": "customer@example.com"}],
            "metadata": {"lead_id": "00000000-0000-0000-0000-000000000001", "owner_key": "archit"},
            "references": [{"meetingUrl": "https://cal.example/meeting"}],
        },
    }

    values = calcom.appointment_values_from_webhook(
        envelope,
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "email": "customer@example.com",
            "owner_key": "aditya",
        },
        "BOOKING_CREATED",
    )

    assert values["calendar_provider"] == "calcom"
    assert values["external_booking_id"] == "cal_123"
    assert values["external_event_type_id"] == "42"
    assert values["owner_key"] == "archit"
    assert values["invitee_email"] == "customer@example.com"
    assert values["meeting_url"] == "https://cal.example/meeting"
    assert values["metadata"]["source"] == "calcom_webhook"


def test_appointment_notification_targets_allocated_owner_and_customer():
    pytest.importorskip("pydantic_settings")
    from config import get_settings
    from services.gmail import build_appointment_notification_message

    settings = get_settings()
    settings.gmail_username = "ops@ashmont.example"
    settings.gmail_from_email = "ops@ashmont.example"
    settings.archit_notification_email = "ananay@advogueai.org"
    settings.appointment_notification_cc = "ops-team@ashmont.example"

    message, recipients = build_appointment_notification_message(
        appointment={
            "owner_key": "archit",
            "start_time": datetime(2026, 6, 3, 15, 0, tzinfo=ZoneInfo("UTC")),
            "end_time": datetime(2026, 6, 3, 15, 30, tzinfo=ZoneInfo("UTC")),
            "meeting_url": "https://cal.example/meeting",
            "cal_booking_id": "cal_123",
            "invitee_email": "customer@example.com",
        },
        lead={
            "full_name": "Jordan Smith",
            "phone_number": "+15551234567",
            "email": "customer@example.com",
            "business_type": "Contractor",
        },
        booking={},
        fallback_used=True,
    )

    assert recipients == ["ananay@advogueai.org", "customer@example.com", "ops-team@ashmont.example"]
    assert "Jordan Smith with Archit" in message["Subject"]
    assert "Owner fallback was used" in message.get_content()
