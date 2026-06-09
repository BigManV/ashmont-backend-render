from __future__ import annotations

import httpx

from config import get_settings


STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}


def is_stop_message(body: str | None) -> bool:
    return (body or "").strip().upper() in STOP_WORDS


async def send_sms(to_number: str, body: str) -> dict:
    settings = get_settings()
    missing = [
        name for name, value in {
            "TWILIO_ACCOUNT_SID": settings.twilio_account_sid,
            "TWILIO_AUTH_TOKEN": settings.twilio_auth_token,
            "TWILIO_PHONE_NUMBER": settings.twilio_phone_number,
        }.items()
        if not value
    ]
    if missing:
        return {
            "ok": False,
            "configured": False,
            "error": f"Missing Twilio config: {', '.join(missing)}",
            "provider_id": None,
        }

    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    data = {"From": settings.twilio_phone_number, "To": to_number, "Body": body}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            url,
            data=data,
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        )
    if response.status_code >= 400:
        return {
            "ok": False,
            "configured": True,
            "error": response.text,
            "status_code": response.status_code,
            "provider_id": None,
        }
    payload = response.json()
    return {"ok": True, "configured": True, "provider_id": payload.get("sid"), "data": payload}
