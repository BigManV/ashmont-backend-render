from __future__ import annotations

import asyncio
from datetime import timedelta

import db
from services import alerts, retell, twilio
from utils import is_outbound_call_time, next_outbound_call_time, utc_now


def render_template(template: str, row: dict) -> str:
    return (
        template
        .replace("{{name}}", row.get("full_name") or "there")
        .replace("{{business_type}}", row.get("business_type") or "your business")
    )


async def process_due_sequences() -> int:
    processed = 0
    for row in db.due_sequence_runs(limit=20):
        processed += 1
        if row.get("appointment_booked") or row.get("opt_out_sms"):
            db.advance_sequence(row["id"], row["current_step"])
            continue

        channel = row["channel"]
        if channel == "sms":
            body = render_template(row["template"], row)
            result = await twilio.send_sms(row["phone_number"], body)
            db.log_sequence_event(
                row["id"], row["lead_id"], row["current_step"], channel,
                "sent" if result["ok"] else "failed",
                result.get("provider_id"),
                result.get("error"),
            )
            if not result["ok"]:
                await alerts.create_alert(
                    "twilio_send_failed",
                    "warning",
                    "SMS sequence step failed",
                    result.get("error") or "Twilio failed to send an outreach SMS.",
                    row["lead_id"],
                    result,
                )
                db.reschedule_sequence_run(row["id"], utc_now() + timedelta(minutes=15), "twilio_send_failed")
                continue
            else:
                db.upsert_sms_message(
                    {
                        "lead_id": row["lead_id"],
                        "direction": "outbound",
                        "from_number": None,
                        "to_number": row["phone_number"],
                        "body": body,
                        "status": "sent",
                        "twilio_sid": result.get("provider_id"),
                        "raw_payload": result.get("data") or {},
                    }
                )
        elif channel == "voice":
            if not is_outbound_call_time():
                next_allowed = next_outbound_call_time()
                db.reschedule_sequence_run(row["id"], next_allowed, "outside_calling_hours")
                db.log_sequence_event(
                    row["id"], row["lead_id"], row["current_step"], channel,
                    "deferred", None, "Outside Ashmont calling hours.",
                )
                continue
            result = await retell.trigger_call(row, attempt_number=row["current_step"])
            if result.get("call_id"):
                db.mark_call_triggered(row["lead_id"], result["call_id"], attempt_number=row["current_step"])
            if result.get("deferred") and result.get("next_allowed_call_time"):
                db.reschedule_sequence_run(row["id"], next_allowed_call_time_from_result(result), "outside_calling_hours")
                db.log_sequence_event(
                    row["id"], row["lead_id"], row["current_step"], channel,
                    "deferred", None, result.get("error"),
                )
                continue
            if result.get("duplicate_blocked"):
                db.reschedule_sequence_run(row["id"], utc_now() + timedelta(minutes=10), "duplicate_call_blocked")
                db.log_sequence_event(
                    row["id"], row["lead_id"], row["current_step"], channel,
                    "deferred", None, result.get("error"),
                )
                continue
            db.log_sequence_event(
                row["id"], row["lead_id"], row["current_step"], channel,
                "triggered" if result["ok"] else "failed",
                result.get("call_id"),
                result.get("error"),
            )
            if not result["ok"]:
                await alerts.create_alert(
                    "retell_sequence_failed",
                    "warning",
                    "Voice sequence step failed",
                    result.get("error") or "Retell failed to trigger an outreach call.",
                    row["lead_id"],
                    result,
                )
                db.reschedule_sequence_run(row["id"], utc_now() + timedelta(minutes=15), "retell_sequence_failed")
                continue
        else:
            db.log_sequence_event(row["id"], row["lead_id"], row["current_step"], channel, "skipped", None, "Unsupported channel.")

        db.advance_sequence(row["id"], row["current_step"])
    return processed


async def run_forever() -> None:
    while True:
        try:
            processed = await process_due_sequences()
            db.record_system_health(
                "outreach_worker",
                "ok",
                f"Processed {processed} due sequence run(s).",
                {"processed": processed},
            )
        except Exception as exc:
            try:
                db.record_system_health(
                    "outreach_worker",
                    "error",
                    str(exc),
                    {"error": str(exc)},
                )
            except Exception:
                pass
            try:
                await alerts.create_alert(
                    "outreach_worker_error",
                    "urgent",
                    "Outreach worker error",
                    str(exc),
                )
            except Exception:
                pass
        await asyncio.sleep(30)


def next_allowed_call_time_from_result(result: dict):
    from utils import parse_dt

    return parse_dt(result["next_allowed_call_time"])
