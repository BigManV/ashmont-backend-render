from __future__ import annotations

import httpx

import db
from config import get_settings


async def create_alert(alert_type: str, severity: str, title: str, message: str, lead_id: str | None = None, metadata: dict | None = None) -> dict:
    row = db.create_alert(alert_type, severity, title, message, lead_id, metadata)
    settings = get_settings()
    if settings.alert_slack_webhook_url and severity in {"critical", "urgent"}:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                await client.post(
                    settings.alert_slack_webhook_url,
                    json={"text": f"[{severity.upper()}] {title}\n{message}"},
                )
        except Exception:
            pass
    return row
