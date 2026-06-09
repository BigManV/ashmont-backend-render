from __future__ import annotations

from datetime import datetime

import owners
from config import get_settings
from services import calcom, calendly


SUPPORTED_PROVIDERS = {"calcom", "calendly"}


def provider_name() -> str:
    settings = get_settings()
    provider = settings.normalized_calendar_provider
    if provider not in SUPPORTED_PROVIDERS:
        return "calcom"
    return provider


def _module():
    return calendly if provider_name() == "calendly" else calcom


def is_configured(owner_key: str | None = None) -> bool:
    settings = get_settings()
    selected_owner_key = owners.normalize_owner_key(owner_key)
    if provider_name() == "calendly":
        return bool(settings.calendly_api_key and calendly.event_type_uri_for_owner(selected_owner_key))
    return bool(settings.cal_api_key and owners.event_type_id_for_owner(settings, selected_owner_key))


def status(owner_key: str | None = None) -> dict:
    selected_owner_key = owners.normalize_owner_key(owner_key)
    return {
        "provider": provider_name(),
        "owner_key": selected_owner_key,
        "owner_name": owners.owner_name(selected_owner_key),
        "configured": is_configured(selected_owner_key),
    }


async def get_availability(start_time: datetime, end_time: datetime, owner_key: str | None = None) -> dict:
    result = await _module().get_availability(start_time, end_time, owner_key)
    return {"provider": provider_name(), **result}


async def book_meeting(booking: dict) -> dict:
    result = await _module().book_meeting(booking)
    return {"provider": provider_name(), **result}
