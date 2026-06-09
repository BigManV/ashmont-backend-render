-- Ashmont Lehr-compatible Supabase schema surface.
-- Source reviewed: lehr-insurance-backend/schema_info.txt and lehr-insurance-backend/db.py.
-- This file copies table/column structure only. It does not copy any Supabase row values,
-- credentials, outreach seed content, or customer data.
--
-- Run after ashmont-backend/sql/supabase_schema.sql.

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- Lehr legacy auth table shape. Ashmont uses app_users + Supabase Auth instead.
-- Kept only so legacy dashboard/query code that expects public.users can import.
-- ---------------------------------------------------------------------------

create table if not exists users (
  id serial primary key,
  name varchar(100) not null,
  email varchar(255) unique not null,
  password varchar(255) not null
);

-- ---------------------------------------------------------------------------
-- Lehr-compatible lead columns on Ashmont's canonical leads table.
-- Ashmont keeps id as the canonical primary key; uuid is a compatibility alias.
-- ---------------------------------------------------------------------------

alter table leads add column if not exists uuid uuid;
alter table leads add column if not exists dob date;
alter table leads add column if not exists full_legal_name varchar(255);
alter table leads add column if not exists call_ids text[];
alter table leads add column if not exists follow_up integer default 0;
alter table leads add column if not exists voicemail_sent integer default 0;
alter table leads add column if not exists followup_sms_sent boolean default false;
alter table leads add column if not exists call_successful boolean default false;
alter table leads add column if not exists call_outcome varchar(20) default null;
alter table leads add column if not exists timezone varchar(64) not null default 'America/New_York';
alter table leads add column if not exists call_summary text;
alter table leads add column if not exists call_booked varchar(10);
alter table leads add column if not exists rescheduled varchar(10);
alter table leads add column if not exists reschedule_time timestamp null;
alter table leads add column if not exists objection_tags text;
alter table leads add column if not exists qualified varchar(10);
alter table leads add column if not exists qualification_score integer;
alter table leads add column if not exists address text;
alter table leads add column if not exists driver_license_number varchar(64);
alter table leads add column if not exists years_licensed integer;
alter table leads add column if not exists accidents integer;
alter table leads add column if not exists tickets integer;
alter table leads add column if not exists vehicle_year integer;
alter table leads add column if not exists vehicle_make_and_model text;
alter table leads add column if not exists ownership varchar(50);
alter table leads add column if not exists vehicle_usage varchar(50);
alter table leads add column if not exists annual_mileage integer;
alter table leads add column if not exists renewal_date date;
alter table leads add column if not exists full_coverage_or_liability varchar(50);
alter table leads add column if not exists vin varchar(50);
alter table leads add column if not exists current_carrier varchar(100);
alter table leads add column if not exists insurance_type varchar(50);
alter table leads add column if not exists property_type varchar(50);
alter table leads add column if not exists square_footage integer;
alter table leads add column if not exists year_built integer;
alter table leads add column if not exists coverage_amount integer;
alter table leads add column if not exists tags text[];
alter table leads add column if not exists appointment_data jsonb;
alter table leads add column if not exists appointment_link text;
alter table leads add column if not exists appointment_success boolean;
alter table leads add column if not exists vehicles_summary text;
alter table leads add column if not exists vehicle_count integer;
alter table leads add column if not exists vehicles jsonb;
alter table leads add column if not exists outreach_step integer default 0;
alter table leads add column if not exists next_outreach_time timestamp null;
alter table leads add column if not exists last_outreach_time timestamp null;
alter table leads add column if not exists sms_sent_count integer default 0;
alter table leads add column if not exists email_sent_count integer default 0;
alter table leads add column if not exists interaction_status varchar(50) default 'New';
alter table leads add column if not exists preferred_channel varchar(50) default null;
alter table leads add column if not exists text_received boolean default false;
alter table leads add column if not exists email_sent varchar(10) default 'No';
alter table leads add column if not exists inbound_call_received varchar(10) default 'No';
alter table leads add column if not exists last_outbound_call_at timestamp;

update leads
set uuid = id
where uuid is null;

update leads
set full_legal_name = full_name
where full_legal_name is null or btrim(full_legal_name) = '';

create unique index if not exists idx_leads_legacy_uuid on leads(uuid);

create or replace function sync_leads_lehr_compat()
returns trigger
language plpgsql
as $$
begin
  if new.uuid is null then
    new.uuid := new.id;
  end if;

  if new.full_legal_name is null or btrim(new.full_legal_name) = '' then
    new.full_legal_name := new.full_name;
  end if;

  if new.qualified is null then
    new.qualified := case
      when new.status = 'qualified' then 'Yes'
      when new.status = 'not_qualified' then 'No'
      else new.qualified
    end;
  end if;

  new.call_booked := case
    when new.appointment_booked = true then 'Yes'
    else coalesce(new.call_booked, 'No')
  end;

  return new;
end
$$;

drop trigger if exists trg_sync_leads_lehr_compat on leads;
create trigger trg_sync_leads_lehr_compat
before insert or update on leads
for each row execute function sync_leads_lehr_compat();

-- ---------------------------------------------------------------------------
-- Retell call lifecycle tables from Lehr.
-- Ashmont's canonical table is call_attempts; these are compatibility tables.
-- ---------------------------------------------------------------------------

create table if not exists call_started (
  call_id varchar(255) primary key,
  agent_id varchar(255),
  start_timestamp varchar(255),
  lead_uuid uuid references leads(uuid)
);

create table if not exists call_ended (
  call_id varchar(255) primary key,
  agent_id varchar(255),
  retell_llm_dynamic_variables text,
  end_timestamp varchar(255),
  duration_ms integer,
  transcript text,
  recording_url text,
  disconnection_reason text,
  call_cost text,
  combined_cost float,
  uuid uuid references leads(uuid)
);

create table if not exists call_analyzed (
  call_id varchar(255) primary key,
  agent_id varchar(255),
  retell_llm_dynamic_variables text,
  transcript text,
  recording_url text,
  disconnection_reason text,
  call_cost text,
  combined_cost float,
  custom_analysis_data text,
  total_duration_seconds float,
  user_sentiment text,
  call_successful boolean,
  lead_uuid uuid references leads(uuid),
  created_at timestamp default now()
);

-- ---------------------------------------------------------------------------
-- Lehr-compatible appointment columns on Ashmont's canonical appointments table.
-- Ashmont keeps UUID ids, owner routing, Cal.com/Calendly metadata, and evidence.
-- ---------------------------------------------------------------------------

alter table appointments add column if not exists lead_uuid uuid;
alter table appointments add column if not exists call_id varchar(255);
alter table appointments add column if not exists phone_number varchar(32);
alter table appointments add column if not exists email varchar(255);
alter table appointments add column if not exists appointment_available_ts text;
alter table appointments add column if not exists appointment_requested_at_utc timestamp null;
alter table appointments add column if not exists appointment_link text;
alter table appointments add column if not exists appointment_success boolean;
alter table appointments add column if not exists appointment_booked_at timestamp null;
alter table appointments add column if not exists raw_payload jsonb;

update appointments
set lead_uuid = lead_id
where lead_uuid is null;

update appointments
set appointment_booked_at = start_time at time zone 'UTC'
where appointment_booked_at is null and start_time is not null;

update appointments
set appointment_requested_at_utc = start_time at time zone 'UTC'
where appointment_requested_at_utc is null and start_time is not null;

update appointments
set appointment_success = (status = 'booked')
where appointment_success is null;

update appointments
set appointment_link = meeting_url
where appointment_link is null and meeting_url is not null;

update appointments
set email = invitee_email
where email is null and invitee_email is not null;

update appointments
set raw_payload = metadata
where raw_payload is null and metadata is not null;

create or replace function sync_appointments_lehr_compat()
returns trigger
language plpgsql
as $$
begin
  if new.lead_uuid is null then
    new.lead_uuid := new.lead_id;
  end if;

  if new.appointment_booked_at is null and new.start_time is not null then
    new.appointment_booked_at := new.start_time at time zone 'UTC';
  end if;

  if new.appointment_requested_at_utc is null and new.start_time is not null then
    new.appointment_requested_at_utc := new.start_time at time zone 'UTC';
  end if;

  if new.appointment_success is null then
    new.appointment_success := (new.status = 'booked');
  end if;

  if new.appointment_link is null then
    new.appointment_link := new.meeting_url;
  end if;

  if new.email is null then
    new.email := new.invitee_email;
  end if;

  if new.raw_payload is null then
    new.raw_payload := new.metadata;
  end if;

  return new;
end
$$;

drop trigger if exists trg_sync_appointments_lehr_compat on appointments;
create trigger trg_sync_appointments_lehr_compat
before insert or update on appointments
for each row execute function sync_appointments_lehr_compat();

create unique index if not exists ux_appointments_call_id_nonnull
  on appointments(call_id)
  where call_id is not null;

create unique index if not exists ux_appointments_success_slot
  on appointments(owner_key, appointment_booked_at)
  where appointment_success = true and appointment_booked_at is not null;

create unique index if not exists ux_appointments_success_lead_slot
  on appointments(lead_uuid, appointment_booked_at)
  where appointment_success = true
    and lead_uuid is not null
    and appointment_booked_at is not null;

-- ---------------------------------------------------------------------------
-- Lehr-compatible slot hold columns/tables.
-- Ashmont's canonical appointment_slot_holds table remains UUID-based and owner-aware.
-- ---------------------------------------------------------------------------

alter table appointment_slot_holds add column if not exists slot_start_utc timestamp;
alter table appointment_slot_holds add column if not exists call_id varchar(255);
alter table appointment_slot_holds add column if not exists lead_uuid uuid;
alter table appointment_slot_holds add column if not exists phone_number varchar(32);
alter table appointment_slot_holds add column if not exists hold_acquired_at timestamp;
alter table appointment_slot_holds add column if not exists released_at timestamp;
alter table appointment_slot_holds add column if not exists previous_slot_start_utc timestamp;

update appointment_slot_holds
set lead_uuid = lead_id
where lead_uuid is null;

update appointment_slot_holds
set slot_start_utc = start_time at time zone 'UTC'
where slot_start_utc is null and start_time is not null;

update appointment_slot_holds
set hold_acquired_at = created_at at time zone 'UTC'
where hold_acquired_at is null and created_at is not null;

create or replace function sync_slot_holds_lehr_compat()
returns trigger
language plpgsql
as $$
begin
  if new.lead_uuid is null then
    new.lead_uuid := new.lead_id;
  end if;

  if new.slot_start_utc is null and new.start_time is not null then
    new.slot_start_utc := new.start_time at time zone 'UTC';
  end if;

  if new.hold_acquired_at is null and new.created_at is not null then
    new.hold_acquired_at := new.created_at at time zone 'UTC';
  end if;

  if new.status in ('released', 'expired', 'failed', 'booked') and new.released_at is null then
    new.released_at := now() at time zone 'UTC';
  end if;

  return new;
end
$$;

drop trigger if exists trg_sync_slot_holds_lehr_compat on appointment_slot_holds;
create trigger trg_sync_slot_holds_lehr_compat
before insert or update on appointment_slot_holds
for each row execute function sync_slot_holds_lehr_compat();

create unique index if not exists ux_appointment_slot_holds_active_slot
  on appointment_slot_holds(owner_key, slot_start_utc)
  where status = 'held';

create index if not exists ix_appointment_slot_holds_call_status
  on appointment_slot_holds(call_id, status);

create unique index if not exists ux_appointment_slot_holds_one_held_per_call
  on appointment_slot_holds(call_id)
  where status = 'held';

create table if not exists appointment_slot_session_blocks (
  id bigserial primary key,
  call_id varchar(255) not null,
  slot_start_utc timestamp not null,
  expires_at timestamp not null,
  created_at timestamp default (now() at time zone 'UTC'),
  unique (call_id, slot_start_utc)
);

create index if not exists ix_appointment_slot_session_blocks_expires
  on appointment_slot_session_blocks(expires_at);

create table if not exists appointment_slot_hold_events (
  id bigserial primary key,
  slot_start_utc timestamp not null,
  call_id varchar(255) not null,
  event_type varchar(64) not null,
  holder_call_id varchar(255),
  detail jsonb,
  created_at timestamp default (now() at time zone 'UTC')
);

create index if not exists ix_appointment_slot_hold_events_slot_time
  on appointment_slot_hold_events(slot_start_utc, created_at desc);

-- ---------------------------------------------------------------------------
-- Chat, task, and outreach definition tables from Lehr.
-- ---------------------------------------------------------------------------

create table if not exists chat_messages (
  id serial primary key,
  phone_number varchar(20) not null,
  thread_id varchar(255),
  message_type varchar(50) not null,
  message_text text,
  response_text text,
  openai_thread_id varchar(255),
  response_complete boolean default false,
  created_at timestamp default now(),
  updated_at timestamp default now()
);

create table if not exists tasks (
  id serial primary key,
  type varchar(100) not null,
  payload jsonb,
  status varchar(20) not null default 'pending',
  scheduled_time timestamp not null,
  result text,
  retries integer not null default 0,
  created_at timestamp default now(),
  updated_at timestamp default now()
);

create table if not exists outreach_tasks (
  id serial primary key,
  step_number integer not null unique,
  action_type varchar(50) not null,
  template_key varchar(100),
  message_subject text,
  message_body text,
  delay_type varchar(20) not null default 'relative',
  delay_minutes integer default 0,
  absolute_hour integer,
  absolute_minute integer,
  absolute_days_offset integer default 0,
  is_active boolean default true,
  is_loop boolean default false,
  loop_interval_days integer,
  created_at timestamp default now(),
  updated_at timestamp default now()
);

-- ---------------------------------------------------------------------------
-- Indexes and RLS for compatibility tables.
-- ---------------------------------------------------------------------------

create index if not exists idx_call_started_lead_uuid on call_started(lead_uuid);
create index if not exists idx_call_ended_uuid on call_ended(uuid);
create index if not exists idx_call_analyzed_lead_uuid on call_analyzed(lead_uuid);
create index if not exists idx_chat_messages_phone_created on chat_messages(phone_number, created_at desc);
create index if not exists idx_tasks_due on tasks(status, scheduled_time);
create index if not exists idx_outreach_tasks_step_active on outreach_tasks(step_number, is_active);

alter table users enable row level security;
alter table call_started enable row level security;
alter table call_ended enable row level security;
alter table call_analyzed enable row level security;
alter table chat_messages enable row level security;
alter table tasks enable row level security;
alter table outreach_tasks enable row level security;
alter table appointment_slot_session_blocks enable row level security;
alter table appointment_slot_hold_events enable row level security;

drop policy if exists "authenticated read users" on users;
drop policy if exists "authenticated read call started" on call_started;
drop policy if exists "authenticated read call ended" on call_ended;
drop policy if exists "authenticated read call analyzed" on call_analyzed;
drop policy if exists "authenticated read chat messages" on chat_messages;
drop policy if exists "authenticated read tasks" on tasks;
drop policy if exists "authenticated read outreach tasks" on outreach_tasks;
drop policy if exists "authenticated read appointment slot session blocks" on appointment_slot_session_blocks;
drop policy if exists "authenticated read appointment slot hold events" on appointment_slot_hold_events;

create policy "authenticated read users" on users for select to authenticated using (true);
create policy "authenticated read call started" on call_started for select to authenticated using (true);
create policy "authenticated read call ended" on call_ended for select to authenticated using (true);
create policy "authenticated read call analyzed" on call_analyzed for select to authenticated using (true);
create policy "authenticated read chat messages" on chat_messages for select to authenticated using (true);
create policy "authenticated read tasks" on tasks for select to authenticated using (true);
create policy "authenticated read outreach tasks" on outreach_tasks for select to authenticated using (true);
create policy "authenticated read appointment slot session blocks" on appointment_slot_session_blocks for select to authenticated using (true);
create policy "authenticated read appointment slot hold events" on appointment_slot_hold_events for select to authenticated using (true);
