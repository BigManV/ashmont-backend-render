# Lehr Supabase Schema Inventory for Ashmont

Source files reviewed:

- `lehr-insurance-backend/schema_info.txt`
- `lehr-insurance-backend/db.py`
- SQL references across `lehr-insurance-backend/*.py`

No Supabase row values, customer records, API keys, credentials, or outreach seed messages are copied here. This is only table/column/type structure.

Ashmont implementation file:

- `ashmont-backend/sql/ashmont_lehr_compatible_schema.sql`

Run it after:

- `ashmont-backend/sql/supabase_schema.sql`

## Compatibility Notes

- Lehr used a flat `leads.uuid` primary key. Ashmont uses `leads.id` as the canonical UUID primary key and adds `leads.uuid` as a compatibility alias.
- Lehr used global appointment slot uniqueness. Ashmont adapts those indexes to include `owner_key`, because Ashmont routes between Aditya and Archit and must allow each owner to have independent calendars.
- Lehr used `needs_human_review varchar(10)` and `human_review_reasons text` in late migrations. Ashmont keeps the safer production types: `needs_human_review boolean` and `human_review_reasons jsonb`.
- Lehr used `appointments.id serial`. Ashmont keeps `appointments.id uuid`.
- Lehr defaults timezone to `America/Los_Angeles`. Ashmont defaults compatibility timezone fields to `America/New_York`.

## users

| Column | Type | Notes |
| --- | --- | --- |
| id | serial | Primary key |
| name | varchar(100) | Not null |
| email | varchar(255) | Unique, not null |
| password | varchar(255) | Not null; compatibility only, Ashmont uses Supabase Auth |

## leads

| Column | Type | Notes |
| --- | --- | --- |
| uuid | uuid | Lehr primary key; Ashmont compatibility alias |
| phone_number | varchar(32) / text | Existing Ashmont text column; Lehr used varchar(32) |
| email | varchar(255) / text | Existing Ashmont nullable text column; Lehr had not-null varchar |
| dob | date | Legacy personal-lines field |
| full_legal_name | varchar(255) | Compatibility copy of Ashmont `full_name` |
| call_ids | text[] | Legacy Retell call ids |
| follow_up | integer | Default 0 |
| voicemail_sent | integer | Default 0 |
| followup_sms_sent | boolean | Default false |
| call_successful | boolean | Default false |
| call_outcome | varchar(20) | Nullable |
| created_at | timestamp/timestamptz | Existing Ashmont timestamptz |
| updated_at | timestamp/timestamptz | Existing Ashmont timestamptz |
| timezone | varchar(64) | Ashmont default `America/New_York` |
| call_summary | text | Nullable |
| call_booked | varchar(10) | Compatibility state |
| rescheduled | varchar(10) | Nullable |
| reschedule_time | timestamp | Nullable |
| objection_tags | text | Nullable |
| qualified | varchar(10) | Compatibility state |
| qualification_score | integer | Nullable |
| address | text | Nullable |
| driver_license_number | varchar(64) | Legacy personal-lines field |
| years_licensed | integer | Legacy personal-lines field |
| accidents | integer | Legacy personal-lines field |
| tickets | integer | Legacy personal-lines field |
| vehicle_year | integer | Legacy personal-lines field |
| vehicle_make_and_model | text | Legacy personal-lines field |
| ownership | varchar(50) | Legacy vehicle/property ownership |
| vehicle_usage | varchar(50) | Legacy vehicle usage |
| annual_mileage | integer | Legacy vehicle field |
| renewal_date | date | Commercial renewal date also useful |
| full_coverage_or_liability | varchar(50) | Legacy personal-lines field |
| vin | varchar(50) | Legacy vehicle field |
| current_carrier | varchar(100) | Commercial/current insurance carrier also useful |
| insurance_type | varchar(50) | Legacy/pipeline classification |
| property_type | varchar(50) | Legacy property field |
| square_footage | integer | Legacy property field |
| year_built | integer | Legacy property field |
| coverage_amount | integer | Legacy property field |
| tags | text[] | Dashboard tags |
| appointment_data | jsonb | Legacy appointment payload |
| appointment_link | text | Legacy appointment link |
| appointment_success | boolean | Legacy appointment state |
| vehicles_summary | text | Multi-vehicle extraction |
| vehicle_count | integer | Multi-vehicle extraction |
| vehicles | jsonb | Multi-vehicle extraction |
| outreach_step | integer | Default 0 |
| next_outreach_time | timestamp | Nullable |
| last_outreach_time | timestamp | Nullable |
| sms_sent_count | integer | Default 0 |
| email_sent_count | integer | Default 0 |
| interaction_status | varchar(50) | Default `New` |
| preferred_channel | varchar(50) | Nullable |
| text_received | boolean | Default false |
| email_sent | varchar(10) | Default `No` |
| inbound_call_received | varchar(10) | Default `No` |
| last_outbound_call_at | timestamp | Outbound cooldown |
| needs_human_review | boolean | Ashmont safer type |
| human_review_reasons | jsonb | Ashmont safer type |

## call_started

| Column | Type | Notes |
| --- | --- | --- |
| call_id | varchar(255) | Primary key |
| agent_id | varchar(255) | Nullable |
| start_timestamp | varchar(255) | Lehr stored raw timestamp text |
| lead_uuid | uuid | References `leads.uuid` |

## call_ended

| Column | Type | Notes |
| --- | --- | --- |
| call_id | varchar(255) | Primary key |
| agent_id | varchar(255) | Nullable |
| retell_llm_dynamic_variables | text | Serialized payload |
| end_timestamp | varchar(255) | Lehr stored raw timestamp text |
| duration_ms | integer | Nullable |
| transcript | text | Nullable |
| recording_url | text | Nullable |
| disconnection_reason | text | Nullable |
| call_cost | text | Serialized payload |
| combined_cost | float | Nullable |
| uuid | uuid | References `leads.uuid` |

## call_analyzed

| Column | Type | Notes |
| --- | --- | --- |
| call_id | varchar(255) | Primary key |
| agent_id | varchar(255) | Nullable |
| retell_llm_dynamic_variables | text | Serialized payload |
| transcript | text | Nullable |
| recording_url | text | Nullable |
| disconnection_reason | text | Nullable |
| call_cost | text | Serialized payload |
| combined_cost | float | Nullable |
| custom_analysis_data | text | Serialized analysis |
| total_duration_seconds | float | Nullable |
| user_sentiment | text | Nullable |
| call_successful | boolean | Nullable |
| lead_uuid | uuid | References `leads.uuid` |
| created_at | timestamp | Default now |

## appointments

Ashmont keeps its canonical appointment columns and adds these Lehr-compatible columns:

| Column | Type | Notes |
| --- | --- | --- |
| lead_uuid | uuid | Compatibility copy of `lead_id` |
| call_id | varchar(255) | Legacy Retell call id |
| phone_number | varchar(32) | Nullable |
| email | varchar(255) | Nullable |
| appointment_available_ts | text | Raw requested time text |
| appointment_requested_at_utc | timestamp | Canonical requested slot |
| appointment_link | text | Compatibility copy of meeting URL |
| appointment_success | boolean | Compatibility state |
| appointment_booked_at | timestamp | Compatibility copy of booked start time |
| raw_payload | jsonb | Compatibility raw provider payload |

Indexes:

- `ux_appointments_call_id_nonnull` on `call_id` when not null
- `ux_appointments_success_slot` on `(owner_key, appointment_booked_at)` when successful
- `ux_appointments_success_lead_slot` on `(lead_uuid, appointment_booked_at)` when successful

## appointment_slot_holds

Ashmont keeps its canonical hold columns and adds these Lehr-compatible columns:

| Column | Type | Notes |
| --- | --- | --- |
| slot_start_utc | timestamp | Compatibility copy of `start_time` in UTC |
| call_id | varchar(255) | Legacy Retell call id |
| lead_uuid | uuid | Compatibility copy of `lead_id` |
| phone_number | varchar(32) | Nullable |
| hold_acquired_at | timestamp | Compatibility copy of created time |
| released_at | timestamp | Nullable |
| previous_slot_start_utc | timestamp | Nullable |

Indexes:

- `ux_appointment_slot_holds_active_slot` on `(owner_key, slot_start_utc)` for active held slots
- `ix_appointment_slot_holds_call_status` on `(call_id, status)`
- `ux_appointment_slot_holds_one_held_per_call` on `call_id` for active held slots

## appointment_slot_session_blocks

| Column | Type | Notes |
| --- | --- | --- |
| id | bigserial | Primary key |
| call_id | varchar(255) | Not null |
| slot_start_utc | timestamp | Not null |
| expires_at | timestamp | Not null |
| created_at | timestamp | Default UTC now |

Constraint:

- Unique `(call_id, slot_start_utc)`

## appointment_slot_hold_events

| Column | Type | Notes |
| --- | --- | --- |
| id | bigserial | Primary key |
| slot_start_utc | timestamp | Not null |
| call_id | varchar(255) | Not null |
| event_type | varchar(64) | Not null |
| holder_call_id | varchar(255) | Nullable |
| detail | jsonb | Nullable |
| created_at | timestamp | Default UTC now |

## chat_messages

| Column | Type | Notes |
| --- | --- | --- |
| id | serial | Primary key |
| phone_number | varchar(20) | Not null |
| thread_id | varchar(255) | Nullable |
| message_type | varchar(50) | Not null |
| message_text | text | Nullable |
| response_text | text | Nullable |
| openai_thread_id | varchar(255) | Nullable |
| response_complete | boolean | Default false |
| created_at | timestamp | Default now |
| updated_at | timestamp | Default now |

## tasks

| Column | Type | Notes |
| --- | --- | --- |
| id | serial | Primary key |
| type | varchar(100) | Not null |
| payload | jsonb | Nullable |
| status | varchar(20) | Default `pending` |
| scheduled_time | timestamp | Not null |
| result | text | Nullable |
| retries | integer | Default 0 |
| created_at | timestamp | Default now |
| updated_at | timestamp | Default now |

## outreach_tasks

| Column | Type | Notes |
| --- | --- | --- |
| id | serial | Primary key |
| step_number | integer | Unique, not null |
| action_type | varchar(50) | Not null |
| template_key | varchar(100) | Nullable |
| message_subject | text | Nullable |
| message_body | text | Nullable |
| delay_type | varchar(20) | Default `relative` |
| delay_minutes | integer | Default 0 |
| absolute_hour | integer | Nullable |
| absolute_minute | integer | Nullable |
| absolute_days_offset | integer | Default 0 |
| is_active | boolean | Default true |
| is_loop | boolean | Default false |
| loop_interval_days | integer | Nullable |
| created_at | timestamp | Default now |
| updated_at | timestamp | Default now |
