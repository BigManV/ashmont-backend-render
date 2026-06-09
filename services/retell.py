from __future__ import annotations

import httpx

import db
import owners
from config import get_settings
from utils import clean_full_name, is_outbound_call_time, is_valid_email, next_outbound_call_time, normalize_email, normalize_us_phone


async def trigger_call(lead: dict, attempt_number: int = 1) -> dict:
    settings = get_settings()
    missing = [
        name for name, value in {
            "RETELL_API_KEY": settings.retell_api_key,
            "RETELL_AGENT_ID": settings.retell_agent_id,
            "RETELL_FROM_NUMBER": settings.retell_from_number,
        }.items()
        if not value
    ]
    if missing:
        return {
            "ok": False,
            "configured": False,
            "error": f"Missing Retell config: {', '.join(missing)}",
            "call_id": None,
        }

    lead_id = str(lead.get("id") or lead.get("lead_id") or "")
    if lead_id:
        fresh_lead = db.get_lead(lead_id)
        if fresh_lead:
            lead = fresh_lead
    if not lead_id:
        lead_id = str(lead.get("id") or lead.get("lead_id") or "")

    if not is_outbound_call_time():
        next_allowed = next_outbound_call_time()
        return {
            "ok": False,
            "deferred": True,
            "error": "Outbound call blocked outside Ashmont calling hours.",
            "next_allowed_call_time": next_allowed.isoformat(),
            "call_id": None,
        }

    if lead_id and db.has_recent_active_call(lead_id):
        return {
            "ok": False,
            "duplicate_blocked": True,
            "error": "Recent active call already exists for this lead.",
            "call_id": None,
        }

    try:
        to_number = normalize_us_phone(lead["phone_number"])
    except (KeyError, ValueError) as exc:
        return {
            "ok": False,
            "configured": True,
            "error": f"Cannot trigger call without a valid lead phone number: {exc}",
            "call_id": None,
        }

    lead_name = clean_full_name(lead.get("full_name"))
    lead_email = normalize_email(lead.get("email")) if is_valid_email(lead.get("email")) else ""
    owner_key = owners.normalize_owner_key(lead.get("owner_key"))
    payload = {
        "from_number": settings.retell_from_number,
        "to_number": to_number,
        "override_agent_id": settings.retell_agent_id,
        "metadata": {
            "lead_id": lead_id,
            "campaign": "contractor_gl",
            "attempt_number": attempt_number,
        },
        "retell_llm_dynamic_variables": {
            "lead_id": lead_id,
            "lead_name": lead_name,
            "lead_email": lead_email,
            "lead_phone_number": to_number,
            "business_type": lead.get("business_type") or "contractor",
            "owner_key": owner_key,
            "owner_name": owners.owner_name(owner_key),
            "campaign": "Contractor GL",
            "booking_window": "9AM-6PM ET, weekdays only. Bookings require explicit customer confirmation.",
        },
    }

    headers = {"Authorization": f"Bearer {settings.retell_api_key}"}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(settings.retell_create_call_url, json=payload, headers=headers)
    if response.status_code >= 400:
        return {
            "ok": False,
            "configured": True,
            "error": response.text,
            "status_code": response.status_code,
            "call_id": None,
        }
    data = response.json()
    return {
        "ok": True,
        "configured": True,
        "data": data,
        "call_id": data.get("call_id") or data.get("id"),
    }
