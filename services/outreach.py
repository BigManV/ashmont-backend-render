from __future__ import annotations

import asyncio
from datetime import timedelta
from zoneinfo import ZoneInfo

import db
import owners
from config import get_settings
from services import alerts, retell, twilio
from utils import ensure_aware_utc, is_outbound_call_time, next_outbound_call_time, utc_now


def render_template(template: str, row: dict) -> str:
    return (
        template
        .replace("{{name}}", row.get("full_name") or "there")
        .replace("{{business_type}}", row.get("business_type") or "your business")
    )


async def process_due_sequences() -> int:
    settings = get_settings()
    processed = 0
    for row in db.due_sequence_runs(limit=20):
        processed += 1
        if row.get("appointment_booked") or row.get("opt_out_sms"):
            db.stop_sequence_runs_for_lead(
                row["lead_id"],
                "appointment_booked" if row.get("appointment_booked") else "sms_opt_out",
            )
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
            call_attempts = db.count_call_attempts(row["lead_id"])
            voicemails = db.count_voicemail_attempts(row["lead_id"])
            if call_attempts >= settings.max_call_attempts or voicemails >= settings.max_voicemail_attempts:
                db.log_sequence_event(
                    row["id"], row["lead_id"], row["current_step"], channel,
                    "skipped", None,
                    f"Voice step skipped: lead reached dialing cap ({call_attempts} call attempts, {voicemails} voicemails).",
                )
                db.advance_sequence(row["id"], row["current_step"])
                continue
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


def build_reminder_sms(row: dict) -> str:
    timezone = ZoneInfo(row.get("timezone") or "America/New_York")
    start = ensure_aware_utc(row["start_time"]).astimezone(timezone)
    when = f"{start.strftime('%A, %b')} {start.day} at {start.strftime('%I:%M %p').lstrip('0')} {start.tzname()}"
    first_name = (row.get("full_name") or "there").split()[0]
    owner_name = owners.owner_name(row.get("owner_key"))
    meeting = f" Meeting link: {row['meeting_url']}." if row.get("meeting_url") else ""
    return (
        f"Hi {first_name}, a quick reminder from Ashmont Insurance: your consultation "
        f"with {owner_name} is on {when}.{meeting} Reply STOP to opt out."
    )


async def process_due_reminders() -> int:
    settings = get_settings()
    if settings.appointment_reminder_minutes <= 0:
        return 0
    sent = 0
    for row in db.claim_appointments_for_reminder(settings.appointment_reminder_minutes, limit=10):
        body = build_reminder_sms(row)
        result = await twilio.send_sms(row["phone_number"], body)
        if result["ok"]:
            sent += 1
            db.upsert_sms_message(
                {
                    "lead_id": row["lead_id"],
                    "direction": "outbound",
                    "from_number": None,
                    "to_number": row["phone_number"],
                    "body": body,
                    "status": "sent",
                    "twilio_sid": result.get("provider_id"),
                    "raw_payload": {"appointment_id": str(row["id"]), "kind": "appointment_reminder"},
                }
            )
        else:
            await alerts.create_alert(
                "appointment_reminder_failed",
                "warning",
                "Appointment reminder SMS failed",
                result.get("error") or "Twilio failed to send the appointment reminder.",
                row["lead_id"],
                {"appointment_id": str(row["id"]), "twilio": result},
            )
    return sent


async def run_forever() -> None:
    while True:
        try:
            processed = await process_due_sequences()
            db.expire_appointment_holds()
            reminders = await process_due_reminders()
            db.record_system_health(
                "outreach_worker",
                "ok",
                f"Processed {processed} sequence run(s), sent {reminders} reminder(s).",
                {"processed": processed, "reminders": reminders},
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
