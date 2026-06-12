create extension if not exists "pgcrypto";

create table if not exists app_users (
  id uuid primary key references auth.users(id) on delete cascade,
  email text unique not null,
  display_name text not null,
  access_level text not null default 'full',
  created_at timestamptz not null default now()
);

create table if not exists webhook_receipts (
  id uuid primary key default gen_random_uuid(),
  source text not null,
  external_id text,
  lead_id uuid,
  status text not null,
  received_at timestamptz not null default now(),
  processed_at timestamptz,
  raw_payload jsonb not null default '{}'::jsonb,
  error_message text
);

create table if not exists leads (
  id uuid primary key default gen_random_uuid(),
  full_name text not null,
  phone_number text not null unique,
  email text,
  business_type text,
  source_campaign text not null default 'contractor_gl',
  source_detail text,
  meta_lead_id text,
  tcpa_consent boolean not null default false,
  submitted_at timestamptz not null,
  first_call_triggered_at timestamptz,
  first_call_started_at timestamptz,
  speed_to_lead_seconds integer,
  status text not null default 'new',
  call_status text not null default 'not_called',
  sequence_status text not null default 'not_started',
  sequence_stage integer not null default 0,
  appointment_booked boolean not null default false,
  appointment_id uuid,
  owner_key text not null default 'aditya',
  opt_out_sms boolean not null default false,
  needs_human_review boolean not null default false,
  human_review_reasons jsonb not null default '[]'::jsonb,
  crm_status text not null default 'pending_hook',
  raw_payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists call_attempts (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references leads(id) on delete cascade,
  retell_call_id text unique,
  attempt_number integer not null,
  direction text not null default 'outbound',
  status text not null,
  persona text not null default 'commercial_gl',
  started_at timestamptz,
  ended_at timestamptz,
  duration_seconds integer not null default 0,
  answered boolean not null default false,
  voicemail boolean not null default false,
  recording_url text,
  transcript text,
  transcript_json jsonb not null default '{}'::jsonb,
  summary jsonb not null default '{}'::jsonb,
  retell_analysis jsonb not null default '{}'::jsonb,
  failure_reason text,
  created_at timestamptz not null default now()
);

create table if not exists appointments (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references leads(id) on delete cascade,
  call_attempt_id uuid references call_attempts(id) on delete set null,
  cal_booking_id text unique,
  cal_event_type_id text,
  calendar_provider text not null default 'calcom',
  external_booking_id text,
  external_event_type_id text,
  owner_key text not null default 'aditya',
  status text not null default 'booked',
  start_time timestamptz not null,
  end_time timestamptz not null,
  timezone text not null default 'America/New_York',
  event_title text,
  meeting_url text,
  invitee_email text,
  lead_agreed boolean not null default true,
  transcript_verified boolean not null default false,
  reminder_sent_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
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

alter table leads add column if not exists owner_key text not null default 'aditya';
alter table leads add column if not exists needs_human_review boolean not null default false;
alter table leads add column if not exists human_review_reasons jsonb not null default '[]'::jsonb;
alter table appointments add column if not exists owner_key text not null default 'aditya';
alter table appointments add column if not exists calendar_provider text not null default 'calcom';
alter table appointments add column if not exists external_booking_id text;
alter table appointments add column if not exists external_event_type_id text;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'leads_owner_key_check'
  ) then
    alter table leads
      add constraint leads_owner_key_check
      check (owner_key in ('aditya', 'archit'));
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'slot_holds_owner_key_check'
  ) then
    alter table appointment_slot_holds
      add constraint slot_holds_owner_key_check
      check (owner_key in ('aditya', 'archit'));
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'slot_holds_status_check'
  ) then
    alter table appointment_slot_holds
      add constraint slot_holds_status_check
      check (status in ('held', 'time_agreed', 'confirmed', 'booking', 'booked', 'expired', 'released', 'failed'));
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'appointments_owner_key_check'
  ) then
    alter table appointments
      add constraint appointments_owner_key_check
      check (owner_key in ('aditya', 'archit'));
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'leads_appointment_id_fk'
  ) then
    alter table leads
      add constraint leads_appointment_id_fk
      foreign key (appointment_id) references appointments(id) deferrable initially deferred;
  end if;
end $$;

create table if not exists sequence_configs (
  sequence_key text primary key,
  name text not null,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists sequence_steps (
  id uuid primary key default gen_random_uuid(),
  sequence_key text not null references sequence_configs(sequence_key) on delete cascade,
  step_number integer not null,
  channel text not null check (channel in ('voice', 'sms')),
  delay_minutes integer not null default 0,
  template text not null,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(sequence_key, step_number)
);

create table if not exists sequence_runs (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references leads(id) on delete cascade,
  sequence_key text not null references sequence_configs(sequence_key) on delete cascade,
  current_step integer not null default 1,
  status text not null default 'active',
  next_run_at timestamptz,
  stopped_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(lead_id, sequence_key)
);

create table if not exists sequence_events (
  id uuid primary key default gen_random_uuid(),
  sequence_run_id uuid references sequence_runs(id) on delete cascade,
  lead_id uuid not null references leads(id) on delete cascade,
  step_number integer not null,
  channel text not null,
  status text not null,
  provider_id text,
  error_message text,
  created_at timestamptz not null default now()
);

create table if not exists sms_messages (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid references leads(id) on delete set null,
  direction text not null check (direction in ('inbound', 'outbound')),
  from_number text,
  to_number text,
  body text not null,
  status text not null,
  twilio_sid text,
  raw_payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists crm_sync_jobs (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid references leads(id) on delete cascade,
  job_type text not null,
  status text not null default 'pending',
  attempts integer not null default 0,
  next_retry_at timestamptz,
  payload jsonb not null default '{}'::jsonb,
  error_message text,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists alert_log (
  id uuid primary key default gen_random_uuid(),
  alert_type text not null,
  severity text not null,
  title text not null,
  message text not null,
  lead_id uuid references leads(id) on delete set null,
  status text not null default 'open',
  fired_channels jsonb not null default '[]'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  resolved_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists system_health_checks (
  id uuid primary key default gen_random_uuid(),
  check_type text not null,
  status text not null,
  latency_ms integer,
  message text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists ad_spend_daily (
  id uuid primary key default gen_random_uuid(),
  spend_date date not null,
  campaign text not null default 'contractor_gl',
  spend_amount numeric(12,2) not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(spend_date, campaign)
);

insert into sequence_configs (sequence_key, name, active)
values ('contractor_gl', 'Ashmont contractor GL', true)
on conflict (sequence_key) do nothing;

insert into sequence_steps (sequence_key, step_number, channel, delay_minutes, template, active)
values
  ('contractor_gl', 1, 'sms', 5, 'Hi {{name}}, this is Ashmont Insurance. We just tried to reach you about your contractor liability quote request. What is a good time for a quick call?', true),
  ('contractor_gl', 2, 'voice', 60, 'Second AI call attempt for {{name}}.', true),
  ('contractor_gl', 3, 'sms', 240, 'Hi {{name}}, following up on your contractor GL request. We can help review coverage options and next steps when you have a few minutes.', true)
on conflict (sequence_key, step_number) do nothing;

create index if not exists idx_webhook_receipts_status on webhook_receipts(status);
create index if not exists idx_leads_phone_number on leads(phone_number);
create index if not exists idx_leads_submitted_at on leads(submitted_at desc);
create index if not exists idx_leads_status on leads(status);
create index if not exists idx_leads_owner_key on leads(owner_key);
create index if not exists idx_call_attempts_retell_call_id on call_attempts(retell_call_id);
create index if not exists idx_call_attempts_lead_id on call_attempts(lead_id);
create index if not exists idx_appointments_start_time on appointments(start_time desc);
create index if not exists idx_appointments_owner_key on appointments(owner_key);
create unique index if not exists idx_appointments_one_booked_per_lead
  on appointments(lead_id)
  where status = 'booked';
drop index if exists idx_appointments_booked_start_time;
create unique index if not exists idx_appointments_owner_booked_start_time
  on appointments(owner_key, start_time)
  where status = 'booked';
create unique index if not exists idx_appointments_external_booking_id
  on appointments(calendar_provider, external_booking_id)
  where external_booking_id is not null;
create index if not exists idx_slot_holds_lead_id on appointment_slot_holds(lead_id);
create index if not exists idx_slot_holds_status_expires on appointment_slot_holds(status, expires_at);
create index if not exists idx_slot_holds_owner_start on appointment_slot_holds(owner_key, start_time);
create unique index if not exists idx_slot_holds_active_owner_start
  on appointment_slot_holds(owner_key, start_time)
  where status in ('held', 'time_agreed', 'confirmed', 'booking');
create index if not exists idx_sequence_runs_due on sequence_runs(status, next_run_at);
create index if not exists idx_alert_log_status on alert_log(status, created_at desc);

alter table app_users enable row level security;
alter table webhook_receipts enable row level security;
alter table leads enable row level security;
alter table call_attempts enable row level security;
alter table appointments enable row level security;
alter table appointment_slot_holds enable row level security;
alter table sequence_configs enable row level security;
alter table sequence_steps enable row level security;
alter table sequence_runs enable row level security;
alter table sequence_events enable row level security;
alter table sms_messages enable row level security;
alter table crm_sync_jobs enable row level security;
alter table alert_log enable row level security;
alter table system_health_checks enable row level security;
alter table ad_spend_daily enable row level security;

drop policy if exists "authenticated read app users" on app_users;
drop policy if exists "authenticated read receipts" on webhook_receipts;
drop policy if exists "authenticated read leads" on leads;
drop policy if exists "authenticated read calls" on call_attempts;
drop policy if exists "authenticated read appointments" on appointments;
drop policy if exists "authenticated read appointment slot holds" on appointment_slot_holds;
drop policy if exists "authenticated read sequence configs" on sequence_configs;
drop policy if exists "authenticated read sequence steps" on sequence_steps;
drop policy if exists "authenticated read sequence runs" on sequence_runs;
drop policy if exists "authenticated read sequence events" on sequence_events;
drop policy if exists "authenticated read sms" on sms_messages;
drop policy if exists "authenticated read crm jobs" on crm_sync_jobs;
drop policy if exists "authenticated read alerts" on alert_log;
drop policy if exists "authenticated read health" on system_health_checks;
drop policy if exists "authenticated read ad spend" on ad_spend_daily;

create policy "authenticated read app users" on app_users for select to authenticated using (true);
create policy "authenticated read receipts" on webhook_receipts for select to authenticated using (true);
create policy "authenticated read leads" on leads for select to authenticated using (true);
create policy "authenticated read calls" on call_attempts for select to authenticated using (true);
create policy "authenticated read appointments" on appointments for select to authenticated using (true);
create policy "authenticated read appointment slot holds" on appointment_slot_holds for select to authenticated using (true);
create policy "authenticated read sequence configs" on sequence_configs for select to authenticated using (true);
create policy "authenticated read sequence steps" on sequence_steps for select to authenticated using (true);
create policy "authenticated read sequence runs" on sequence_runs for select to authenticated using (true);
create policy "authenticated read sequence events" on sequence_events for select to authenticated using (true);
create policy "authenticated read sms" on sms_messages for select to authenticated using (true);
create policy "authenticated read crm jobs" on crm_sync_jobs for select to authenticated using (true);
create policy "authenticated read alerts" on alert_log for select to authenticated using (true);
create policy "authenticated read health" on system_health_checks for select to authenticated using (true);
create policy "authenticated read ad spend" on ad_spend_daily for select to authenticated using (true);
