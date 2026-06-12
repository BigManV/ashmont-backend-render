from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import owners
from config import get_settings
from utils import ensure_aware_utc, seconds_between, utc_now


def jsonb(value: Any) -> Jsonb:
    return Jsonb(json.loads(json.dumps(value or {}, default=str)))


def uuid_or_none(value: Any) -> str | None:
    if not value:
        return None
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return None


@contextmanager
def get_conn():
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


def fetch_one(sql: str, params: tuple | dict | None = None) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def fetch_all(sql: str, params: tuple | dict | None = None) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def execute(sql: str, params: tuple | dict | None = None) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            try:
                row = cur.fetchone()
            except psycopg.ProgrammingError:
                row = None
        conn.commit()
        return row


def ensure_booking_workflow_schema() -> None:
    execute(
        """
        alter table leads add column if not exists needs_human_review boolean not null default false;
        alter table leads add column if not exists human_review_reasons jsonb not null default '[]'::jsonb;
        alter table appointments add column if not exists calendar_provider text not null default 'calcom';
        alter table appointments add column if not exists external_booking_id text;
        alter table appointments add column if not exists external_event_type_id text;
        alter table appointments add column if not exists reminder_sent_at timestamptz;

        create table if not exists system_health_checks (
          id uuid primary key default gen_random_uuid(),
          check_type text not null,
          status text not null,
          latency_ms integer,
          message text,
          metadata jsonb not null default '{}'::jsonb,
          created_at timestamptz not null default now()
        );

        create index if not exists idx_system_health_checks_type_created
          on system_health_checks(check_type, created_at desc);

        create table if not exists dashboard_users (
          id uuid primary key,
          email text unique not null,
          display_name text not null,
          access_level text not null default 'full',
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now()
        );

        create table if not exists appointment_slot_holds (
          id uuid primary key default gen_random_uuid(),
          lead_id uuid not null references leads(id) on delete cascade,
          call_attempt_id uuid references call_attempts(id) on delete set null,
          owner_key text not null default 'aditya',
          status text not null default 'held',
          start_time timestamptz not null,
          end_time timestamptz not null,
          timezone text not null default 'America/New_York',
          expires_at timestamptz not null,
          availability_verified_at timestamptz,
          step1_time_agreed_at timestamptz,
          step1_customer_phrase text,
          step1_agent_prompt text,
          step2_booking_confirmed_at timestamptz,
          step2_customer_phrase text,
          step2_agent_recap text,
          transcript_excerpt text,
          metadata jsonb not null default '{}'::jsonb,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now(),
          booked_appointment_id uuid references appointments(id) on delete set null
        );

        create index if not exists idx_slot_holds_lead_id on appointment_slot_holds(lead_id);
        create index if not exists idx_slot_holds_status_expires on appointment_slot_holds(status, expires_at);
        create index if not exists idx_slot_holds_owner_start on appointment_slot_holds(owner_key, start_time);
        create unique index if not exists idx_slot_holds_active_owner_start
          on appointment_slot_holds(owner_key, start_time)
          where status in ('held', 'time_agreed', 'confirmed', 'booking');
        create unique index if not exists idx_appointments_external_booking_id
          on appointments(calendar_provider, external_booking_id)
          where external_booking_id is not null;
        """
    )


def create_webhook_receipt(source: str, raw_payload: dict, status: str = "received", external_id: str | None = None) -> str:
    receipt_id = str(uuid4())
    execute(
        """
        insert into webhook_receipts (id, source, external_id, status, raw_payload)
        values (%s, %s, %s, %s, %s)
        """,
        (receipt_id, source, external_id, status, jsonb(raw_payload)),
    )
    return receipt_id


def complete_webhook_receipt(receipt_id: str, status: str, lead_id: str | None = None, error_message: str | None = None) -> None:
    execute(
        """
        update webhook_receipts
        set status = %s, lead_id = %s, error_message = %s, processed_at = now()
        where id = %s
        """,
        (status, lead_id, error_message, receipt_id),
    )


def upsert_lead(payload: dict) -> dict:
    lead_id = str(uuid4())
    row = execute(
        """
        insert into leads (
            id, full_name, phone_number, email, business_type, source_campaign,
            source_detail, meta_lead_id, tcpa_consent, submitted_at, owner_key, raw_payload
        )
        values (
            %s, %(full_name)s, %(phone_number)s, %(email)s, %(business_type)s,
            'contractor_gl', %(source_detail)s, %(meta_lead_id)s,
            %(tcpa_consent)s, %(submitted_at)s, %(owner_key)s, %(raw_payload)s
        )
        on conflict (phone_number) do update set
            full_name = excluded.full_name,
            email = coalesce(excluded.email, leads.email),
            business_type = coalesce(excluded.business_type, leads.business_type),
            source_detail = coalesce(excluded.source_detail, leads.source_detail),
            meta_lead_id = coalesce(excluded.meta_lead_id, leads.meta_lead_id),
            tcpa_consent = excluded.tcpa_consent,
            submitted_at = excluded.submitted_at,
            raw_payload = excluded.raw_payload,
            owner_key = coalesce(leads.owner_key, excluded.owner_key, 'aditya'),
            updated_at = now()
        returning *
        """,
        {
            "id": lead_id,
            **{
                **payload,
                "owner_key": owners.normalize_owner_key(payload.get("owner_key")),
                "raw_payload": jsonb(payload.get("raw_payload")),
            },
        },
    )
    return owners.with_owner_name(row)


def create_crm_job(lead_id: str, job_type: str, payload: dict) -> dict:
    return execute(
        """
        insert into crm_sync_jobs (id, lead_id, job_type, status, payload)
        values (%s, %s, %s, 'pending', %s)
        returning *
        """,
        (str(uuid4()), lead_id, job_type, jsonb(payload)),
    )


def get_lead(lead_id: str) -> dict | None:
    return owners.with_owner_name(fetch_one("select * from leads where id = %s", (lead_id,)))


def get_lead_by_phone(phone_number: str) -> dict | None:
    return owners.with_owner_name(fetch_one("select * from leads where phone_number = %s", (phone_number,)))


def get_lead_by_email(email: str) -> dict | None:
    normalized = str(email or "").strip().lower()
    if not normalized:
        return None
    return owners.with_owner_name(fetch_one("select * from leads where lower(coalesce(email, '')) = %s", (normalized,)))


def upsert_dashboard_user_profile(user: dict) -> dict:
    return execute(
        """
        insert into dashboard_users (id, email, display_name, access_level)
        values (%(id)s, %(email)s, %(display_name)s, %(access_level)s)
        on conflict (id) do update set
            email = excluded.email,
            display_name = excluded.display_name,
            access_level = excluded.access_level,
            updated_at = now()
        returning *
        """,
        {
            "id": user["id"],
            "email": user["email"],
            "display_name": user.get("display_name") or user["email"],
            "access_level": user.get("access_level") or "full",
        },
    )


def next_owner_key() -> str:
    rows = fetch_all(
        """
        select owner_key, count(*) as count
        from leads
        group by owner_key
        """
    )
    return owners.choose_balanced_owner_key({row["owner_key"]: row["count"] for row in rows})


def has_recent_active_call(lead_id: str, lookback_minutes: int = 10) -> bool:
    row = fetch_one(
        """
        select 1
        from call_attempts
        where lead_id = %s
          and status in ('triggered', 'started')
          and coalesce(started_at, created_at) >= now() - (%s || ' minutes')::interval
        limit 1
        """,
        (lead_id, lookback_minutes),
    )
    return bool(row)


def count_call_attempts(lead_id: str) -> int:
    row = fetch_one("select count(*) as count from call_attempts where lead_id = %s", (lead_id,))
    return int(row["count"] or 0) if row else 0


def count_voicemail_attempts(lead_id: str) -> int:
    row = fetch_one(
        "select count(*) as count from call_attempts where lead_id = %s and voicemail = true",
        (lead_id,),
    )
    return int(row["count"] or 0) if row else 0


def mark_call_triggered(lead_id: str, retell_call_id: str | None, attempt_number: int = 1) -> dict:
    call_id = str(uuid4())
    row = execute(
        """
        insert into call_attempts (
            id, lead_id, retell_call_id, attempt_number, status, persona, started_at
        )
        values (%s, %s, %s, %s, 'triggered', 'commercial_gl', now())
        on conflict (retell_call_id) do update set
            status = excluded.status,
            started_at = coalesce(call_attempts.started_at, excluded.started_at)
        returning *
        """,
        (call_id, lead_id, retell_call_id, attempt_number),
    )
    execute(
        """
        update leads
        set call_status = 'triggered',
            first_call_triggered_at = coalesce(first_call_triggered_at, now()),
            speed_to_lead_seconds = coalesce(speed_to_lead_seconds, extract(epoch from (now() - submitted_at))::int),
            updated_at = now()
        where id = %s
        """,
        (lead_id,),
    )
    return row


def update_call_started(lead: dict, retell_call_id: str, raw_payload: dict) -> dict:
    triggered = fetch_one("select count(*) as count from call_attempts where lead_id = %s", (lead["id"],))
    attempt_number = int(triggered["count"] or 0) + 1
    call = execute(
        """
        insert into call_attempts (
            id, lead_id, retell_call_id, attempt_number, status, persona, started_at, retell_analysis
        )
        values (%s, %s, %s, %s, 'started', 'commercial_gl', now(), %s)
        on conflict (retell_call_id) do update set
            status = 'started',
            started_at = coalesce(call_attempts.started_at, now()),
            retell_analysis = excluded.retell_analysis
        returning *
        """,
        (str(uuid4()), lead["id"], retell_call_id, attempt_number, jsonb(raw_payload)),
    )
    speed = seconds_between(lead.get("submitted_at"), utc_now())
    execute(
        """
        update leads
        set call_status = 'started',
            first_call_started_at = coalesce(first_call_started_at, now()),
            speed_to_lead_seconds = coalesce(speed_to_lead_seconds, %s),
            updated_at = now()
        where id = %s
        """,
        (speed, lead["id"]),
    )
    return call


def update_call_analyzed(retell_call_id: str, values: dict) -> dict | None:
    values = {
        **values,
        "transcript_json": jsonb(values.get("transcript_json")),
        "summary": jsonb(values.get("summary")),
        "retell_analysis": jsonb(values.get("retell_analysis")),
    }
    return execute(
        """
        update call_attempts
        set status = %(status)s,
            ended_at = coalesce(%(ended_at)s, ended_at, now()),
            duration_seconds = %(duration_seconds)s,
            answered = %(answered)s,
            voicemail = %(voicemail)s,
            recording_url = %(recording_url)s,
            transcript = %(transcript)s,
            transcript_json = %(transcript_json)s,
            summary = %(summary)s,
            retell_analysis = %(retell_analysis)s,
            failure_reason = %(failure_reason)s
        where retell_call_id = %(retell_call_id)s
        returning *
        """,
        {"retell_call_id": retell_call_id, **values},
    )


def update_lead_after_call(lead_id: str, answered: bool, voicemail: bool, status: str) -> None:
    call_status = "answered" if answered else ("voicemail" if voicemail else status)
    execute(
        """
        update leads
        set call_status = %s,
            status = case when %s then 'contacted' else status end,
            updated_at = now()
        where id = %s
        """,
        (call_status, answered, lead_id),
    )


def log_lead_qualification(lead_id: str, qualified: bool, log_payload: dict) -> dict | None:
    qualification_status = "qualified" if qualified else "not_qualified"
    payload = {
        **log_payload,
        "qualified": qualified,
        "qualification_status": qualification_status,
        "logged_at": log_payload.get("logged_at") or utc_now().isoformat(),
    }
    payload_json = jsonb(payload)
    row = execute(
        """
        update leads
        set status = case
                when appointment_booked = true or status in ('booked', 'opted_out') then status
                else %(qualification_status)s
            end,
            sequence_status = case
                when %(qualification_status)s = 'not_qualified' and appointment_booked = false then 'stopped'
                else sequence_status
            end,
            raw_payload = jsonb_set(
                jsonb_set(coalesce(raw_payload, '{}'::jsonb), '{qualification}', %(payload)s, true),
                '{qualification_logs}',
                (
                    case
                        when jsonb_typeof(coalesce(raw_payload, '{}'::jsonb)->'qualification_logs') = 'array'
                        then coalesce(raw_payload, '{}'::jsonb)->'qualification_logs'
                        else '[]'::jsonb
                    end
                ) || jsonb_build_array(%(payload)s),
                true
            ),
            updated_at = now()
        where id = %(lead_id)s
        returning *
        """,
        {
            "lead_id": lead_id,
            "qualification_status": qualification_status,
            "payload": payload_json,
        },
    )
    if row and not qualified and not row.get("appointment_booked"):
        stop_sequence_runs_for_lead(lead_id, "not_qualified")
    return owners.with_owner_name(row)


def flag_lead_for_review(lead_id: str, reason: str, metadata: dict | None = None) -> dict | None:
    review_payload = {
        "reason": reason,
        "metadata": metadata or {},
        "flagged_at": utc_now().isoformat(),
    }
    return owners.with_owner_name(execute(
        """
        update leads
        set needs_human_review = true,
            human_review_reasons = (
                case
                    when jsonb_typeof(coalesce(human_review_reasons, '[]'::jsonb)) = 'array'
                    then coalesce(human_review_reasons, '[]'::jsonb)
                    else '[]'::jsonb
                end
            ) || jsonb_build_array(%(review_payload)s),
            raw_payload = jsonb_set(
                coalesce(raw_payload, '{}'::jsonb),
                '{human_review}',
                jsonb_build_object(
                    'needs_human_review', true,
                    'latest_reason', %(reason)s,
                    'latest_metadata', %(metadata)s,
                    'flagged_at', %(flagged_at)s
                ),
                true
            ),
            updated_at = now()
        where id = %(lead_id)s
        returning *
        """,
        {
            "lead_id": lead_id,
            "reason": reason,
            "metadata": jsonb(metadata or {}),
            "flagged_at": review_payload["flagged_at"],
            "review_payload": jsonb(review_payload),
        },
    ))


def expire_appointment_holds() -> None:
    execute(
        """
        update appointment_slot_holds
        set status = 'expired',
            updated_at = now()
        where status in ('held', 'time_agreed', 'confirmed', 'booking')
          and expires_at <= now()
        """
    )


def get_appointment_hold(hold_id: str) -> dict | None:
    expire_appointment_holds()
    return owners.with_owner_name(fetch_one("select * from appointment_slot_holds where id = %s", (hold_id,)))


def get_latest_active_hold_for_lead(lead_id: str) -> dict | None:
    expire_appointment_holds()
    return owners.with_owner_name(fetch_one(
        """
        select *
        from appointment_slot_holds
        where lead_id = %s
          and status in ('held', 'time_agreed', 'confirmed', 'booking')
        order by created_at desc
        limit 1
        """,
        (lead_id,),
    ))


def find_conflicting_hold(start_time: datetime, end_time: datetime, owner_key: str, exclude_hold_id: str | None = None) -> dict | None:
    expire_appointment_holds()
    params: list[Any] = [owners.normalize_owner_key(owner_key, strict=True), end_time, start_time]
    exclude = ""
    if exclude_hold_id:
        exclude = "and id <> %s"
        params.append(exclude_hold_id)
    return owners.with_owner_name(fetch_one(
        f"""
        select *
        from appointment_slot_holds
        where owner_key = %s
          and status in ('held', 'time_agreed', 'confirmed', 'booking')
          and start_time < %s
          and end_time > %s
          {exclude}
        order by start_time asc
        limit 1
        """,
        tuple(params),
    ))


def create_appointment_hold(values: dict) -> dict:
    expire_appointment_holds()
    hold_id = str(uuid4())
    start_time = ensure_aware_utc(values["start_time"])
    end_time = ensure_aware_utc(values["end_time"])
    prepared = {
        "id": hold_id,
        "lead_id": values["lead_id"],
        "call_attempt_id": uuid_or_none(values.get("call_attempt_id")),
        "owner_key": owners.normalize_owner_key(values.get("owner_key"), strict=True),
        "start_time": start_time,
        "end_time": end_time,
        "timezone": values.get("timezone") or "America/New_York",
        "expires_at": ensure_aware_utc(values["expires_at"]),
        "availability_verified_at": ensure_aware_utc(values.get("availability_verified_at") or utc_now()),
        "transcript_excerpt": values.get("transcript_excerpt"),
        "metadata": jsonb(values.get("metadata")),
    }
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from leads where id = %s for update", (prepared["lead_id"],))
            lead = cur.fetchone()
            if not lead:
                raise RuntimeError("Lead not found for appointment hold.")
            if lead.get("appointment_booked"):
                raise RuntimeError("Lead already has a booked appointment.")

            cur.execute(
                """
                select *
                from appointments
                where status = 'booked'
                  and owner_key = %(owner_key)s
                  and start_time < %(end_time)s
                  and end_time > %(start_time)s
                order by start_time asc
                limit 1
                for update
                """,
                prepared,
            )
            if cur.fetchone():
                raise RuntimeError("Appointment slot is already booked.")

            cur.execute(
                """
                select *
                from appointment_slot_holds
                where owner_key = %(owner_key)s
                  and status in ('held', 'time_agreed', 'confirmed', 'booking')
                  and start_time < %(end_time)s
                  and end_time > %(start_time)s
                order by start_time asc
                limit 1
                for update
                """,
                prepared,
            )
            if cur.fetchone():
                raise RuntimeError("Appointment slot is already held.")

            cur.execute(
                """
                insert into appointment_slot_holds (
                    id, lead_id, call_attempt_id, owner_key, status, start_time, end_time,
                    timezone, expires_at, availability_verified_at, transcript_excerpt, metadata
                )
                values (
                    %(id)s, %(lead_id)s, %(call_attempt_id)s, %(owner_key)s, 'held',
                    %(start_time)s, %(end_time)s, %(timezone)s, %(expires_at)s,
                    %(availability_verified_at)s, %(transcript_excerpt)s, %(metadata)s
                )
                returning *
                """,
                prepared,
            )
            row = cur.fetchone()
        conn.commit()
    return owners.with_owner_name(row)


def mark_hold_time_agreed(hold_id: str, values: dict) -> dict | None:
    expire_appointment_holds()
    row = execute(
        """
        update appointment_slot_holds
        set status = case when status = 'confirmed' then status else 'time_agreed' end,
            step1_time_agreed_at = now(),
            step1_customer_phrase = %(customer_phrase)s,
            step1_agent_prompt = %(agent_prompt)s,
            transcript_excerpt = coalesce(%(transcript_excerpt)s, transcript_excerpt),
            metadata = coalesce(metadata, '{}'::jsonb) || %(metadata)s,
            updated_at = now()
        where id = %(hold_id)s
          and status in ('held', 'time_agreed', 'confirmed')
          and expires_at > now()
        returning *
        """,
        {
            "hold_id": hold_id,
            "customer_phrase": values.get("customer_phrase"),
            "agent_prompt": values.get("agent_prompt"),
            "transcript_excerpt": values.get("transcript_excerpt"),
            "metadata": jsonb({"time_agreement": values.get("raw_payload") or {}}),
        },
    )
    return owners.with_owner_name(row)


def mark_hold_booking_confirmed(hold_id: str, values: dict) -> dict | None:
    expire_appointment_holds()
    row = execute(
        """
        update appointment_slot_holds
        set status = 'confirmed',
            step2_booking_confirmed_at = now(),
            step2_customer_phrase = %(customer_phrase)s,
            step2_agent_recap = %(agent_recap)s,
            transcript_excerpt = coalesce(%(transcript_excerpt)s, transcript_excerpt),
            metadata = coalesce(metadata, '{}'::jsonb) || %(metadata)s,
            updated_at = now()
        where id = %(hold_id)s
          and status in ('time_agreed', 'confirmed')
          and step1_time_agreed_at is not null
          and expires_at > now()
        returning *
        """,
        {
            "hold_id": hold_id,
            "customer_phrase": values.get("customer_phrase"),
            "agent_recap": values.get("agent_recap"),
            "transcript_excerpt": values.get("transcript_excerpt"),
            "metadata": jsonb({"booking_confirmation": values.get("raw_payload") or {}}),
        },
    )
    return owners.with_owner_name(row)


def mark_hold_status(hold_id: str, status: str, appointment_id: str | None = None, metadata: dict | None = None) -> dict | None:
    row = execute(
        """
        update appointment_slot_holds
        set status = %(status)s,
            booked_appointment_id = coalesce(%(appointment_id)s, booked_appointment_id),
            metadata = coalesce(metadata, '{}'::jsonb) || %(metadata)s,
            updated_at = now()
        where id = %(hold_id)s
        returning *
        """,
        {
            "hold_id": hold_id,
            "status": status,
            "appointment_id": appointment_id,
            "metadata": jsonb(metadata or {}),
        },
    )
    return owners.with_owner_name(row)


def list_leads(search: str | None, status: str | None, limit: int, offset: int) -> list[dict]:
    clauses = []
    params: list[Any] = []
    if search:
        clauses.append("(full_name ilike %s or phone_number ilike %s or coalesce(email, '') ilike %s)")
        needle = f"%{search}%"
        params.extend([needle, needle, needle])
    if status:
        clauses.append("status = %s")
        params.append(status)
    where = "where " + " and ".join(clauses) if clauses else ""
    params.extend([limit, offset])
    rows = fetch_all(
        f"""
        select *
        from leads
        {where}
        order by submitted_at desc
        limit %s offset %s
        """,
        tuple(params),
    )
    return owners.with_owner_names(rows)


def list_calls(limit: int = 50, offset: int = 0) -> list[dict]:
    rows = fetch_all(
        """
        select c.*, l.full_name, l.phone_number, l.email, l.business_type, l.owner_key
        from call_attempts c
        join leads l on l.id = c.lead_id
        order by coalesce(c.started_at, c.created_at) desc
        limit %s offset %s
        """,
        (limit, offset),
    )
    return owners.with_owner_names(rows)


def list_appointments(limit: int = 50, offset: int = 0) -> list[dict]:
    rows = fetch_all(
        """
        select a.*, l.full_name, l.phone_number, l.email, l.business_type,
               l.needs_human_review, l.human_review_reasons,
               coalesce(a.owner_key, l.owner_key, 'aditya') as owner_key,
               coalesce((a.metadata->>'booked_through_chat')::boolean, false) as booked_through_chat,
               coalesce((a.metadata->>'fallback_used')::boolean, false) as fallback_used
        from appointments a
        join leads l on l.id = a.lead_id
        order by a.start_time desc
        limit %s offset %s
        """,
        (limit, offset),
    )
    return owners.with_owner_names(rows)


def lead_has_booked_appointment(lead_id: str) -> bool:
    row = fetch_one(
        """
        select 1
        from leads
        where id = %s and appointment_booked = true
        union all
        select 1
        from appointments
        where lead_id = %s and status = 'booked'
        limit 1
        """,
        (lead_id, lead_id),
    )
    return bool(row)


def find_conflicting_appointment(start_time: datetime, end_time: datetime, lead_id: str | None = None, owner_key: str | None = None) -> dict | None:
    clauses = ["status = 'booked'", "start_time < %s", "end_time > %s"]
    params: list[Any] = [end_time, start_time]
    if lead_id:
        clauses.append("lead_id <> %s")
        params.append(lead_id)
    if owner_key:
        clauses.append("owner_key = %s")
        params.append(owners.normalize_owner_key(owner_key, strict=True))
    where = " and ".join(clauses)
    return owners.with_owner_name(fetch_one(
        f"""
        select *
        from appointments
        where {where}
        order by start_time asc
        limit 1
        """,
        tuple(params),
    ))


def create_appointment(values: dict) -> dict:
    appointment_id = str(uuid4())
    start_time = ensure_aware_utc(values["start_time"])
    end_time = ensure_aware_utc(values["end_time"])
    prepared = {
        "id": appointment_id,
        "lead_id": values["lead_id"],
        "call_attempt_id": uuid_or_none(values.get("call_attempt_id")),
        "cal_booking_id": values.get("cal_booking_id") or values.get("external_booking_id"),
        "cal_event_type_id": values.get("cal_event_type_id") or values.get("external_event_type_id"),
        "calendar_provider": values.get("calendar_provider") or "calcom",
        "external_booking_id": values.get("external_booking_id") or values.get("cal_booking_id"),
        "external_event_type_id": values.get("external_event_type_id") or values.get("cal_event_type_id"),
        "owner_key": owners.normalize_owner_key(values.get("owner_key"), strict=True),
        "start_time": start_time,
        "end_time": end_time,
        "timezone": values.get("timezone") or "America/New_York",
        "event_title": values.get("event_title"),
        "meeting_url": values.get("meeting_url"),
        "invitee_email": values.get("invitee_email"),
        "transcript_verified": bool(values.get("transcript_verified")),
        "metadata": jsonb(values.get("metadata")),
    }
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from leads where id = %s for update", (prepared["lead_id"],))
            lead = cur.fetchone()
            if not lead:
                raise RuntimeError("Lead not found for appointment.")
            if lead.get("appointment_booked"):
                raise RuntimeError("Lead already has a booked appointment.")

            cur.execute(
                """
                select *
                from appointments
                where status = 'booked'
                  and owner_key = %(owner_key)s
                  and start_time < %(end_time)s
                  and end_time > %(start_time)s
                order by start_time asc
                limit 1
                for update
                """,
                prepared,
            )
            conflict = cur.fetchone()
            if conflict:
                raise RuntimeError("Appointment slot is already booked.")

            cur.execute(
                """
                insert into appointments (
                    id, lead_id, call_attempt_id, cal_booking_id, cal_event_type_id,
                    calendar_provider, external_booking_id, external_event_type_id,
                    owner_key, status, start_time, end_time, timezone, event_title, meeting_url,
                    invitee_email, lead_agreed, transcript_verified, metadata
                )
                values (
                    %(id)s, %(lead_id)s, %(call_attempt_id)s, %(cal_booking_id)s,
                    %(cal_event_type_id)s, %(calendar_provider)s, %(external_booking_id)s,
                    %(external_event_type_id)s, %(owner_key)s, 'booked', %(start_time)s, %(end_time)s,
                    %(timezone)s, %(event_title)s, %(meeting_url)s, %(invitee_email)s,
                    true, %(transcript_verified)s, %(metadata)s
                )
                returning *
                """,
                prepared,
            )
            row = cur.fetchone()
            cur.execute(
                """
                update leads
                set appointment_booked = true,
                    appointment_id = %s,
                    owner_key = %s,
                    sequence_status = 'stopped',
                    status = 'booked',
                    updated_at = now()
                where id = %s
                """,
                (appointment_id, prepared["owner_key"], prepared["lead_id"]),
            )
            cur.execute(
                """
                update sequence_runs
                set status = 'stopped',
                    stopped_reason = 'appointment_booked',
                    updated_at = now()
                where lead_id = %s
                  and status = 'active'
                """,
                (prepared["lead_id"],),
            )
        conn.commit()
    return owners.with_owner_name(row)


def upsert_calendar_appointment(values: dict) -> dict:
    external_booking_id = str(values.get("external_booking_id") or values.get("cal_booking_id") or "").strip()
    if not external_booking_id:
        raise RuntimeError("Calendar webhook is missing a booking ID.")

    start_time = ensure_aware_utc(values["start_time"])
    end_time = ensure_aware_utc(values["end_time"])
    status = values.get("status") or "booked"
    prepared = {
        "id": str(uuid4()),
        "lead_id": values["lead_id"],
        "call_attempt_id": uuid_or_none(values.get("call_attempt_id")),
        "cal_booking_id": values.get("cal_booking_id") or external_booking_id,
        "cal_event_type_id": values.get("cal_event_type_id") or values.get("external_event_type_id"),
        "calendar_provider": values.get("calendar_provider") or "calcom",
        "external_booking_id": external_booking_id,
        "external_event_type_id": values.get("external_event_type_id") or values.get("cal_event_type_id"),
        "owner_key": owners.normalize_owner_key(values.get("owner_key"), strict=True),
        "status": status,
        "start_time": start_time,
        "end_time": end_time,
        "timezone": values.get("timezone") or "America/New_York",
        "event_title": values.get("event_title"),
        "meeting_url": values.get("meeting_url"),
        "invitee_email": values.get("invitee_email"),
        "transcript_verified": bool(values.get("transcript_verified")),
        "metadata": jsonb(values.get("metadata")),
        "reschedule_uid": (values.get("metadata") or {}).get("reschedule_uid"),
    }
    with get_conn() as conn:
        with conn.cursor() as cur:
            if status == "booked" and prepared["reschedule_uid"]:
                cur.execute(
                    """
                    update appointments
                    set status = 'rescheduled',
                        metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object('rescheduled_to', %(external_booking_id)s)
                    where calendar_provider = %(calendar_provider)s
                      and external_booking_id = %(reschedule_uid)s
                    """,
                    prepared,
                )
            if status == "booked":
                cur.execute(
                    """
                    update appointments
                    set status = 'superseded',
                        metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object('superseded_by', %(external_booking_id)s)
                    where lead_id = %(lead_id)s
                      and status = 'booked'
                      and coalesce(external_booking_id, cal_booking_id, '') <> %(external_booking_id)s
                    """,
                    prepared,
                )

            cur.execute(
                """
                insert into appointments (
                    id, lead_id, call_attempt_id, cal_booking_id, cal_event_type_id,
                    calendar_provider, external_booking_id, external_event_type_id,
                    owner_key, status, start_time, end_time, timezone, event_title, meeting_url,
                    invitee_email, lead_agreed, transcript_verified, metadata
                )
                values (
                    %(id)s, %(lead_id)s, %(call_attempt_id)s, %(cal_booking_id)s,
                    %(cal_event_type_id)s, %(calendar_provider)s, %(external_booking_id)s,
                    %(external_event_type_id)s, %(owner_key)s, %(status)s, %(start_time)s, %(end_time)s,
                    %(timezone)s, %(event_title)s, %(meeting_url)s, %(invitee_email)s,
                    true, %(transcript_verified)s, %(metadata)s
                )
                on conflict (calendar_provider, external_booking_id)
                  where external_booking_id is not null
                do update set
                    lead_id = excluded.lead_id,
                    call_attempt_id = coalesce(appointments.call_attempt_id, excluded.call_attempt_id),
                    cal_booking_id = excluded.cal_booking_id,
                    cal_event_type_id = excluded.cal_event_type_id,
                    external_event_type_id = excluded.external_event_type_id,
                    owner_key = excluded.owner_key,
                    status = excluded.status,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    timezone = excluded.timezone,
                    event_title = excluded.event_title,
                    meeting_url = excluded.meeting_url,
                    invitee_email = excluded.invitee_email,
                    transcript_verified = appointments.transcript_verified or excluded.transcript_verified,
                    metadata = coalesce(appointments.metadata, '{}'::jsonb) || excluded.metadata
                returning *
                """,
                prepared,
            )
            row = cur.fetchone()
            if status == "booked":
                cur.execute(
                    """
                    update leads
                    set appointment_booked = true,
                        appointment_id = %s,
                        owner_key = %s,
                        sequence_status = 'stopped',
                        status = 'booked',
                        updated_at = now()
                    where id = %s
                    """,
                    (row["id"], prepared["owner_key"], prepared["lead_id"]),
                )
                cur.execute(
                    """
                    update sequence_runs
                    set status = 'stopped',
                        stopped_reason = 'appointment_booked',
                        updated_at = now()
                    where lead_id = %s
                      and status = 'active'
                    """,
                    (prepared["lead_id"],),
                )
            else:
                cur.execute(
                    """
                    update leads
                    set appointment_booked = false,
                        appointment_id = null,
                        status = case when status = 'booked' then 'contacted' else status end,
                        updated_at = now()
                    where id = %s
                      and appointment_id = %s
                    """,
                    (prepared["lead_id"], row["id"]),
                )
        conn.commit()
    return owners.with_owner_name(row)


def claim_appointments_for_reminder(window_minutes: int, limit: int = 10) -> list[dict]:
    # Atomically marks reminder_sent_at while claiming, so a reminder SMS is
    # sent at most once per appointment even with concurrent workers.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                with due as (
                    select a.id
                    from appointments a
                    join leads l on l.id = a.lead_id
                    where a.status = 'booked'
                      and a.reminder_sent_at is null
                      and a.start_time > now()
                      and a.start_time <= now() + (%s || ' minutes')::interval
                      and l.opt_out_sms = false
                      and l.phone_number is not null
                    order by a.start_time asc
                    limit %s
                    for update of a skip locked
                )
                update appointments a
                set reminder_sent_at = now()
                from due, leads l
                where a.id = due.id
                  and l.id = a.lead_id
                returning a.*, l.full_name, l.phone_number
                """,
                (window_minutes, limit),
            )
            rows = cur.fetchall()
        conn.commit()
    return rows


def cancel_calendar_appointment(calendar_provider: str, external_booking_id: str, metadata: dict | None = None) -> dict | None:
    provider = calendar_provider or "calcom"
    booking_id = str(external_booking_id or "").strip()
    if not booking_id:
        return None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update appointments
                set status = 'cancelled',
                    metadata = coalesce(metadata, '{}'::jsonb) || %(metadata)s
                where calendar_provider = %(provider)s
                  and external_booking_id = %(booking_id)s
                returning *
                """,
                {"provider": provider, "booking_id": booking_id, "metadata": jsonb(metadata or {})},
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    """
                    update leads
                    set appointment_booked = false,
                        appointment_id = null,
                        status = case when status = 'booked' then 'contacted' else status end,
                        updated_at = now()
                    where id = %s
                      and appointment_id = %s
                    """,
                    (row["lead_id"], row["id"]),
                )
        conn.commit()
    return owners.with_owner_name(row)


def upsert_sms_message(values: dict) -> dict:
    return execute(
        """
        insert into sms_messages (
            id, lead_id, direction, from_number, to_number, body, status, twilio_sid, raw_payload
        )
        values (
            %(id)s, %(lead_id)s, %(direction)s, %(from_number)s, %(to_number)s,
            %(body)s, %(status)s, %(twilio_sid)s, %(raw_payload)s
        )
        returning *
        """,
        {**values, "id": str(uuid4()), "raw_payload": jsonb(values.get("raw_payload"))},
    )


def stop_sequence_runs_for_lead(lead_id: str, reason: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update sequence_runs
                set status = 'stopped',
                    stopped_reason = %s,
                    updated_at = now()
                where lead_id = %s
                  and status = 'active'
                """,
                (reason, lead_id),
            )
            stopped = cur.rowcount
        conn.commit()
    return stopped


def set_sms_opt_out(lead_id: str) -> None:
    execute(
        """
        update leads
        set opt_out_sms = true,
            sequence_status = 'stopped',
            status = 'opted_out',
            updated_at = now()
        where id = %s
        """,
        (lead_id,),
    )
    stop_sequence_runs_for_lead(lead_id, "sms_opt_out")


def create_alert(alert_type: str, severity: str, title: str, message: str, lead_id: str | None = None, metadata: dict | None = None) -> dict:
    return execute(
        """
        insert into alert_log (id, alert_type, severity, title, message, lead_id, metadata)
        values (%s, %s, %s, %s, %s, %s, %s)
        returning *
        """,
        (str(uuid4()), alert_type, severity, title, message, lead_id, jsonb(metadata)),
    )


def list_alerts(status: str | None = None, limit: int = 50) -> list[dict]:
    if status:
        return fetch_all(
            "select * from alert_log where status = %s order by created_at desc limit %s",
            (status, limit),
        )
    return fetch_all("select * from alert_log order by created_at desc limit %s", (limit,))


def resolve_alert(alert_id: str) -> dict | None:
    return execute(
        """
        update alert_log
        set status = 'resolved', resolved_at = now()
        where id = %s
        returning *
        """,
        (alert_id,),
    )


def get_kpis() -> dict:
    mtd_start = utc_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return fetch_one(
        """
        with lead_stats as (
            select
                count(*) filter (where submitted_at >= %(mtd_start)s) as total_leads_mtd,
                avg(speed_to_lead_seconds) filter (where speed_to_lead_seconds is not null and submitted_at >= %(mtd_start)s) as avg_speed_to_lead,
                count(*) filter (where appointment_booked = true and submitted_at >= %(mtd_start)s) as appointments_mtd
            from leads
        ),
        call_stats as (
            select
                count(*) filter (where started_at >= %(mtd_start)s) as calls_mtd,
                count(*) filter (where answered = true and started_at >= %(mtd_start)s) as answered_calls_mtd
            from call_attempts
        ),
        spend_stats as (
            select coalesce(sum(spend_amount), 0) as spend_mtd
            from ad_spend_daily
            where spend_date >= %(mtd_start)s::date
        )
        select
            lead_stats.total_leads_mtd,
            lead_stats.appointments_mtd,
            case when lead_stats.total_leads_mtd > 0
                then round((lead_stats.appointments_mtd::numeric / lead_stats.total_leads_mtd::numeric) * 100, 1)
                else 0 end as appointment_rate,
            coalesce(round(lead_stats.avg_speed_to_lead::numeric, 0), 0) as avg_speed_to_lead,
            case when call_stats.calls_mtd > 0
                then round((call_stats.answered_calls_mtd::numeric / call_stats.calls_mtd::numeric) * 100, 1)
                else 0 end as call_connection_rate,
            spend_stats.spend_mtd,
            case when lead_stats.total_leads_mtd > 0
                then round((spend_stats.spend_mtd / lead_stats.total_leads_mtd::numeric), 2)
                else 0 end as cpl
        from lead_stats, call_stats, spend_stats
        """,
        {"mtd_start": mtd_start},
    )


def get_outreach_config() -> list[dict]:
    return fetch_all(
        """
        select *
        from sequence_steps
        where sequence_key = 'contractor_gl'
        order by step_number asc
        """
    )


def record_system_health(
    check_type: str,
    status: str,
    message: str | None = None,
    metadata: dict | None = None,
    latency_ms: int | None = None,
) -> dict:
    return execute(
        """
        insert into system_health_checks (id, check_type, status, latency_ms, message, metadata)
        values (%s, %s, %s, %s, %s, %s)
        returning *
        """,
        (str(uuid4()), check_type, status, latency_ms, message, jsonb(metadata or {})),
    )


def get_worker_status() -> dict:
    runs = fetch_one(
        """
        select
            count(*) filter (where status = 'active') as active_sequence_runs,
            count(*) filter (where status = 'active' and next_run_at <= now()) as due_sequence_runs,
            min(next_run_at) filter (where status = 'active') as next_sequence_run_at
        from sequence_runs
        """
    ) or {}
    event_rows = fetch_all(
        """
        select channel, status, count(*) as count
        from sequence_events
        where created_at >= now() - interval '24 hours'
        group by channel, status
        """
    )
    latest_event = fetch_one(
        """
        select created_at
        from sequence_events
        order by created_at desc
        limit 1
        """
    )
    latest_worker = fetch_one(
        """
        select status, message, metadata, created_at
        from system_health_checks
        where check_type = 'outreach_worker'
        order by created_at desc
        limit 1
        """
    )
    events_24h = {
        f"{row['channel']}_{row['status']}_24h": int(row["count"] or 0)
        for row in event_rows
    }
    return {
        "active_sequence_runs": int(runs.get("active_sequence_runs") or 0),
        "due_sequence_runs": int(runs.get("due_sequence_runs") or 0),
        "next_sequence_run_at": runs.get("next_sequence_run_at"),
        "last_sequence_event_at": (latest_event or {}).get("created_at"),
        "sequence_events_24h": events_24h,
        "outreach_worker": latest_worker
        or {
            "status": "unknown",
            "message": "No outreach worker heartbeat has been recorded yet.",
            "metadata": {},
            "created_at": None,
        },
    }


def upsert_outreach_step(values: dict) -> dict:
    return execute(
        """
        insert into sequence_steps (
            id, sequence_key, step_number, channel, delay_minutes, template, active
        )
        values (%s, 'contractor_gl', %(step_number)s, %(channel)s, %(delay_minutes)s, %(template)s, %(active)s)
        on conflict (sequence_key, step_number) do update set
            channel = excluded.channel,
            delay_minutes = excluded.delay_minutes,
            template = excluded.template,
            active = excluded.active,
            updated_at = now()
        returning *
        """,
        {"id": str(uuid4()), **values},
    )


def queue_sequence_for_lead(lead_id: str, delay_minutes: int = 5) -> None:
    execute(
        """
        insert into sequence_runs (id, lead_id, sequence_key, current_step, status, next_run_at)
        values (%s, %s, 'contractor_gl', 1, 'active', now() + (%s || ' minutes')::interval)
        on conflict (lead_id, sequence_key) do update set
            status = case when sequence_runs.status = 'stopped' then sequence_runs.status else 'active' end,
            next_run_at = least(coalesce(sequence_runs.next_run_at, excluded.next_run_at), excluded.next_run_at),
            updated_at = now()
        """,
        (str(uuid4()), lead_id, delay_minutes),
    )
    execute(
        """
        update leads
        set sequence_status = 'active',
            sequence_stage = greatest(coalesce(sequence_stage, 0), 1),
            updated_at = now()
        where id = %s
          and appointment_booked = false
          and status not in ('booked', 'opted_out')
        """,
        (lead_id,),
    )


def queue_sequence_for_lead_at(lead_id: str, next_run_at: datetime, current_step: int = 1) -> None:
    execute(
        """
        insert into sequence_runs (id, lead_id, sequence_key, current_step, status, next_run_at)
        values (%s, %s, 'contractor_gl', %s, 'active', %s)
        on conflict (lead_id, sequence_key) do update set
            status = case when sequence_runs.status = 'stopped' then sequence_runs.status else 'active' end,
            next_run_at = least(coalesce(sequence_runs.next_run_at, excluded.next_run_at), excluded.next_run_at),
            updated_at = now()
        """,
        (str(uuid4()), lead_id, current_step, ensure_aware_utc(next_run_at)),
    )
    execute(
        """
        update leads
        set sequence_status = 'active',
            sequence_stage = greatest(coalesce(sequence_stage, 0), %s),
            updated_at = now()
        where id = %s
          and appointment_booked = false
          and status not in ('booked', 'opted_out')
        """,
        (current_step, lead_id),
    )


def reschedule_sequence_run(run_id: str, next_run_at: datetime, reason: str) -> None:
    execute(
        """
        update sequence_runs
        set next_run_at = %s,
            stopped_reason = %s,
            updated_at = now()
        where id = %s
        """,
        (ensure_aware_utc(next_run_at), reason, run_id),
    )


def due_sequence_runs(limit: int = 20) -> list[dict]:
    # Claims due runs atomically (FOR UPDATE SKIP LOCKED + a short lease on
    # next_run_at) so concurrent worker instances never double-process a run.
    # advance_sequence/reschedule_sequence_run overwrite the lease afterwards.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                with due as (
                    select r.id
                    from sequence_runs r
                    join leads l on l.id = r.lead_id
                    join sequence_steps s on s.sequence_key = r.sequence_key and s.step_number = r.current_step
                    where r.status = 'active'
                      and r.next_run_at <= now()
                      and s.active = true
                      and l.opt_out_sms = false
                      and l.appointment_booked = false
                      and coalesce(l.sequence_status, '') <> 'stopped'
                      and coalesce(l.status, '') not in ('not_qualified', 'booked', 'opted_out')
                    order by r.next_run_at asc
                    limit %s
                    for update of r skip locked
                )
                update sequence_runs r
                set next_run_at = now() + interval '3 minutes',
                    updated_at = now()
                from due, leads l, sequence_steps s
                where r.id = due.id
                  and l.id = r.lead_id
                  and s.sequence_key = r.sequence_key
                  and s.step_number = r.current_step
                returning r.*, l.full_name, l.phone_number, l.email, l.business_type, l.opt_out_sms, l.appointment_booked,
                          s.channel, s.template, s.delay_minutes
                """,
                (limit,),
            )
            rows = cur.fetchall()
        conn.commit()
    return rows


def advance_sequence(run_id: str, current_step: int) -> None:
    next_step = current_step + 1
    next_row = fetch_one(
        """
        select delay_minutes
        from sequence_steps
        where sequence_key = 'contractor_gl' and step_number = %s and active = true
        """,
        (next_step,),
    )
    if not next_row:
        execute(
            """
            update sequence_runs
            set status = 'completed', updated_at = now()
            where id = %s
            """,
            (run_id,),
        )
        execute(
            """
            update leads
            set sequence_status = 'completed',
                updated_at = now()
            where id = (select lead_id from sequence_runs where id = %s)
              and appointment_booked = false
              and status not in ('booked', 'opted_out')
            """,
            (run_id,),
        )
        return
    execute(
        """
        update sequence_runs
        set current_step = %s,
            next_run_at = now() + (%s || ' minutes')::interval,
            updated_at = now()
        where id = %s
        """,
        (next_step, next_row["delay_minutes"], run_id),
    )
    execute(
        """
        update leads
        set sequence_status = 'active',
            sequence_stage = %s,
            updated_at = now()
        where id = (select lead_id from sequence_runs where id = %s)
          and appointment_booked = false
          and status not in ('booked', 'opted_out')
        """,
        (next_step, run_id),
    )


def log_sequence_event(run_id: str, lead_id: str, step_number: int, channel: str, status: str, provider_id: str | None = None, error_message: str | None = None) -> None:
    execute(
        """
        insert into sequence_events (
            id, sequence_run_id, lead_id, step_number, channel, status, provider_id, error_message
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (str(uuid4()), run_id, lead_id, step_number, channel, status, provider_id, error_message),
    )


def upsert_ad_spend(spend_date: datetime, campaign: str, spend_amount: float) -> dict:
    return execute(
        """
        insert into ad_spend_daily (id, spend_date, campaign, spend_amount)
        values (%s, %s, %s, %s)
        on conflict (spend_date, campaign) do update set
            spend_amount = excluded.spend_amount,
            updated_at = now()
        returning *
        """,
        (str(uuid4()), spend_date.date(), campaign, spend_amount),
    )
