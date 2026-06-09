from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg
from psycopg.types.json import Jsonb

from config import get_settings
from demo_seed_data import SEED_SOURCE, build_demo_data, build_kpis


def parse_dt(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value)


def jsonb(value):
    return Jsonb(value or {})


def seed() -> dict:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured; the app is currently using DEV_MOCK_DATA.")

    data = build_demo_data()
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update leads
                set appointment_id = null
                where meta_lead_id like 'ashmont-demo-%'
                """
            )
            cur.execute(
                """
                delete from alert_log
                where metadata->>'seed_source' = %s
                   or lead_id in (select id from leads where meta_lead_id like 'ashmont-demo-%')
                """,
                (SEED_SOURCE,),
            )
            for table in ("sequence_events", "sequence_runs", "crm_sync_jobs", "sms_messages", "appointments", "call_attempts"):
                cur.execute(
                    f"""
                    delete from {table}
                    where lead_id in (select id from leads where meta_lead_id like 'ashmont-demo-%')
                    """
                )
            cur.execute("delete from leads where meta_lead_id like 'ashmont-demo-%'")
            cur.execute("delete from ad_spend_daily where campaign = 'contractor_gl' and spend_date >= date_trunc('month', now())::date")

            for lead in data["leads"]:
                cur.execute(
                    """
                    insert into leads (
                        id, full_name, phone_number, email, business_type, source_campaign,
                        source_detail, meta_lead_id, tcpa_consent, submitted_at,
                        first_call_triggered_at, first_call_started_at, speed_to_lead_seconds,
                        status, call_status, sequence_status, sequence_stage, appointment_booked,
                        appointment_id, owner_key, opt_out_sms, crm_status, raw_payload, created_at, updated_at
                    )
                    values (
                        %(id)s, %(full_name)s, %(phone_number)s, %(email)s, %(business_type)s,
                        %(source_campaign)s, %(source_detail)s, %(meta_lead_id)s, %(tcpa_consent)s,
                        %(submitted_at)s, %(first_call_triggered_at)s, %(first_call_started_at)s,
                        %(speed_to_lead_seconds)s, %(status)s, %(call_status)s, %(sequence_status)s,
                        %(sequence_stage)s, %(appointment_booked)s, null, %(owner_key)s, %(opt_out_sms)s,
                        %(crm_status)s, %(raw_payload)s, %(created_at)s, %(updated_at)s
                    )
                    """,
                    {
                        **lead,
                        "submitted_at": parse_dt(lead["submitted_at"]),
                        "first_call_triggered_at": parse_dt(lead.get("first_call_triggered_at")),
                        "first_call_started_at": parse_dt(lead.get("first_call_started_at")),
                        "created_at": parse_dt(lead["created_at"]),
                        "updated_at": parse_dt(lead["updated_at"]),
                        "raw_payload": jsonb(lead["raw_payload"]),
                    },
                )

            for call in data["calls"]:
                cur.execute(
                    """
                    insert into call_attempts (
                        id, lead_id, retell_call_id, attempt_number, direction, status, persona,
                        started_at, ended_at, duration_seconds, answered, voicemail, recording_url,
                        transcript, transcript_json, summary, retell_analysis, failure_reason, created_at
                    )
                    values (
                        %(id)s, %(lead_id)s, %(retell_call_id)s, %(attempt_number)s, %(direction)s,
                        %(status)s, %(persona)s, %(started_at)s, %(ended_at)s, %(duration_seconds)s,
                        %(answered)s, %(voicemail)s, %(recording_url)s, %(transcript)s,
                        %(transcript_json)s, %(summary)s, %(retell_analysis)s, %(failure_reason)s,
                        %(created_at)s
                    )
                    """,
                    {
                        **call,
                        "started_at": parse_dt(call["started_at"]),
                        "ended_at": parse_dt(call["ended_at"]),
                        "created_at": parse_dt(call["created_at"]),
                        "transcript_json": jsonb(call["transcript_json"]),
                        "summary": jsonb(call["summary"]),
                        "retell_analysis": jsonb(call["retell_analysis"]),
                    },
                )

            for appointment in data["appointments"]:
                cur.execute(
                    """
                    insert into appointments (
                        id, lead_id, call_attempt_id, cal_booking_id, cal_event_type_id, owner_key, status,
                        start_time, end_time, timezone, event_title, meeting_url, invitee_email,
                        lead_agreed, transcript_verified, metadata, created_at
                    )
                    values (
                        %(id)s, %(lead_id)s, %(call_attempt_id)s, %(cal_booking_id)s,
                        %(cal_event_type_id)s, %(owner_key)s, %(status)s, %(start_time)s, %(end_time)s,
                        %(timezone)s, %(event_title)s, %(meeting_url)s, %(invitee_email)s,
                        %(lead_agreed)s, %(transcript_verified)s, %(metadata)s, %(created_at)s
                    )
                    """,
                    {
                        **appointment,
                        "start_time": parse_dt(appointment["start_time"]),
                        "end_time": parse_dt(appointment["end_time"]),
                        "created_at": parse_dt(appointment["created_at"]),
                        "metadata": jsonb(appointment["metadata"]),
                    },
                )
                cur.execute(
                    """
                    update leads
                    set appointment_id = %s
                    where id = %s
                    """,
                    (appointment["id"], appointment["lead_id"]),
                )

            for message in data["sms_messages"]:
                cur.execute(
                    """
                    insert into sms_messages (
                        id, lead_id, direction, from_number, to_number, body,
                        status, twilio_sid, raw_payload, created_at
                    )
                    values (
                        %(id)s, %(lead_id)s, %(direction)s, %(from_number)s, %(to_number)s,
                        %(body)s, %(status)s, %(twilio_sid)s, %(raw_payload)s, %(created_at)s
                    )
                    """,
                    {
                        **message,
                        "raw_payload": jsonb(message["raw_payload"]),
                        "created_at": parse_dt(message["created_at"]),
                    },
                )

            cur.execute(
                """
                insert into sequence_configs (sequence_key, name, active)
                values ('contractor_gl', 'Ashmont contractor GL', true)
                on conflict (sequence_key) do update set active = excluded.active, updated_at = now()
                """
            )
            for step in data["outreach_steps"]:
                cur.execute(
                    """
                    insert into sequence_steps (
                        id, sequence_key, step_number, channel, delay_minutes, template, active
                    )
                    values (
                        %(id)s, %(sequence_key)s, %(step_number)s, %(channel)s,
                        %(delay_minutes)s, %(template)s, %(active)s
                    )
                    on conflict (sequence_key, step_number) do update set
                        channel = excluded.channel,
                        delay_minutes = excluded.delay_minutes,
                        template = excluded.template,
                        active = excluded.active,
                        updated_at = now()
                    """,
                    step,
                )

            for lead in data["leads"]:
                if lead["sequence_status"] in {"active", "completed", "stopped"}:
                    cur.execute(
                        """
                        insert into sequence_runs (
                            id, lead_id, sequence_key, current_step, status, next_run_at,
                            stopped_reason, created_at, updated_at
                        )
                        values (
                            gen_random_uuid(), %(lead_id)s, 'contractor_gl', %(current_step)s,
                            %(status)s, %(next_run_at)s, %(stopped_reason)s, %(created_at)s, %(updated_at)s
                        )
                        on conflict (lead_id, sequence_key) do nothing
                        """,
                        {
                            "lead_id": lead["id"],
                            "current_step": max(1, int(lead.get("sequence_stage") or 1)),
                            "status": "active" if lead["sequence_status"] == "active" else lead["sequence_status"],
                            "next_run_at": datetime.now(timezone.utc) if lead["sequence_status"] == "active" else None,
                            "stopped_reason": "appointment_booked" if lead["appointment_booked"] else ("opt_out" if lead["opt_out_sms"] else None),
                            "created_at": parse_dt(lead["created_at"]),
                            "updated_at": parse_dt(lead["updated_at"]),
                        },
                    )

            for alert in data["alerts"]:
                cur.execute(
                    """
                    insert into alert_log (
                        id, alert_type, severity, title, message, lead_id, status,
                        fired_channels, metadata, resolved_at, created_at
                    )
                    values (
                        %(id)s, %(alert_type)s, %(severity)s, %(title)s, %(message)s,
                        %(lead_id)s, %(status)s, %(fired_channels)s, %(metadata)s,
                        %(resolved_at)s, %(created_at)s
                    )
                    """,
                    {
                        **alert,
                        "fired_channels": Jsonb(alert["fired_channels"]),
                        "metadata": jsonb(alert["metadata"]),
                        "resolved_at": parse_dt(alert.get("resolved_at")),
                        "created_at": parse_dt(alert["created_at"]),
                    },
                )

            for spend in data["ad_spend_daily"]:
                cur.execute(
                    """
                    insert into ad_spend_daily (id, spend_date, campaign, spend_amount)
                    values (%(id)s, %(spend_date)s, %(campaign)s, %(spend_amount)s)
                    on conflict (spend_date, campaign) do update set
                        spend_amount = excluded.spend_amount,
                        updated_at = now()
                    """,
                    spend,
                )
        conn.commit()

    return {
        "leads": len(data["leads"]),
        "calls": len(data["calls"]),
        "appointments": len(data["appointments"]),
        "sms_messages": len(data["sms_messages"]),
        "alerts": len(data["alerts"]),
        "kpis": build_kpis(data),
    }


if __name__ == "__main__":
    summary = seed()
    print(summary)
