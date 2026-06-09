from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, EmailStr, Field, field_validator


def blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class LeadIntake(BaseModel):
    full_name: str = Field(min_length=2)
    phone_number: str = Field(min_length=7)
    email: EmailStr | None = None
    business_type: str | None = None
    source_detail: str | None = None
    meta_lead_id: str | None = None
    tcpa_consent: bool
    submitted_at: datetime | None = None
    raw_payload: dict[str, Any] | None = None

    _normalize_optional_fields = field_validator("email", "submitted_at", mode="before")(blank_to_none)


class LeadListRequest(BaseModel):
    search: str | None = None
    status: str | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class CalendarAvailabilityRequest(BaseModel):
    start_time: datetime
    end_time: datetime
    preferred_owner_key: Literal["aditya", "archit"] | None = None

    _normalize_optional_fields = field_validator("preferred_owner_key", mode="before")(blank_to_none)


class CalendarBookingRequest(BaseModel):
    lead_id: str
    call_attempt_id: str | None = None
    hold_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    preferred_owner_key: Literal["aditya", "archit"] | None = None
    attendee_name: str
    attendee_email: EmailStr | None = None
    business_type: str | None = None
    notes: str | None = None
    customer_confirmed: bool = False
    customer_confirmation: str | None = None
    agent_recap: str | None = None
    transcript_excerpt: str | None = None

    _normalize_optional_fields = field_validator(
        "call_attempt_id",
        "hold_id",
        "start_time",
        "end_time",
        "preferred_owner_key",
        "attendee_email",
        mode="before",
    )(blank_to_none)


class CalendarHoldRequest(BaseModel):
    lead_id: str
    call_attempt_id: str | None = None
    start_time: datetime
    end_time: datetime | None = None
    preferred_owner_key: Literal["aditya", "archit"] | None = None
    ttl_minutes: int = Field(default=10, ge=2, le=30)
    requested_by: Literal["call", "chat", "sms", "analysis"] = "call"
    transcript_excerpt: str | None = None
    raw_payload: dict[str, Any] | None = None

    _normalize_optional_fields = field_validator(
        "call_attempt_id",
        "end_time",
        "preferred_owner_key",
        mode="before",
    )(blank_to_none)


class CalendarHoldTimeAgreementRequest(BaseModel):
    customer_phrase: str = Field(min_length=2)
    agent_prompt: str | None = None
    transcript_excerpt: str | None = None
    raw_payload: dict[str, Any] | None = None


class CalendarHoldTimeAgreementByIdRequest(CalendarHoldTimeAgreementRequest):
    hold_id: str


class CalendarLatestHoldTimeAgreementRequest(CalendarHoldTimeAgreementRequest):
    lead_id: str


class CalendarHoldBookingConfirmationRequest(BaseModel):
    customer_phrase: str = Field(min_length=2)
    agent_recap: str = Field(min_length=10)
    transcript_excerpt: str | None = None
    raw_payload: dict[str, Any] | None = None


class CalendarHoldBookingConfirmationByIdRequest(CalendarHoldBookingConfirmationRequest):
    hold_id: str


class CalendarLatestHoldBookingConfirmationRequest(CalendarHoldBookingConfirmationRequest):
    lead_id: str


class LeadQualificationLogRequest(BaseModel):
    lead_id: str
    qualified: bool
    source: Literal["call", "chat", "sms", "analysis"] = "call"
    call_attempt_id: str | None = None
    reason: str | None = None
    notes: str | None = None
    transcript_excerpt: str | None = None
    raw_payload: dict[str, Any] | None = None

    _normalize_optional_fields = field_validator("call_attempt_id", mode="before")(blank_to_none)


class OutreachStepInput(BaseModel):
    step_number: int = Field(ge=1)
    channel: str
    delay_minutes: int = Field(ge=0)
    template: str
    active: bool = True


class AdSpendInput(BaseModel):
    spend_date: datetime
    campaign: str = "contractor_gl"
    spend_amount: float = Field(ge=0)
