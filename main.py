from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

import db
import mock_data
import owners
from auth import require_intake_key, require_tool_key, require_user
from config import get_settings
from models import (
    AdSpendInput,
    CalendarAvailabilityRequest,
    CalendarBookingRequest,
    CalendarHoldBookingConfirmationByIdRequest,
    CalendarHoldBookingConfirmationRequest,
    CalendarHoldRequest,
    CalendarHoldTimeAgreementByIdRequest,
    CalendarHoldTimeAgreementRequest,
    CalendarLatestHoldBookingConfirmationRequest,
    CalendarLatestHoldTimeAgreementRequest,
    LeadIntake,
    LeadListRequest,
    LeadQualificationLogRequest,
    OutreachStepInput,
)
from services import alerts, calendar_provider, calcom, momentum, outreach, retell, twilio
from utils import extract_retell_call, first_present, is_business_hours_et, normalize_us_phone, parse_dt
from utils import (
    analysis_claims_booking,
    contains_booking_confirmation,
    contains_time_agreement,
    contains_voicemail_or_no_consent,
    ensure_aware_utc,
    extract_qualification_decision,
    utc_now,
    validate_booking_evidence,
)


settings = get_settings()
app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def log_qualification_decision(
    lead_id: str,
    *,
    qualified: bool,
    source: str,
    call_attempt_id: str | None = None,
    retell_call_id: str | None = None,
    reason: str | None = None,
    notes: str | None = None,
    transcript_excerpt: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> dict:
    qualification_status = "qualified" if qualified else "not_qualified"
    log_payload = {
        "qualified": qualified,
        "qualification_status": qualification_status,
        "source": source,
        "call_attempt_id": call_attempt_id,
        "retell_call_id": retell_call_id,
        "reason": reason,
        "notes": notes,
        "transcript_excerpt": transcript_excerpt,
        "raw_payload": raw_payload or {},
        "logged_by": "ai",
        "logged_at": utc_now().isoformat(),
    }
    lead = db.log_lead_qualification(lead_id, qualified, log_payload)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")
    db.create_crm_job(lead_id, "qualification_logged", {"qualification": log_payload, "lead": lead})
    return {"lead": lead, "qualification": log_payload}


async def retell_tool_args(request: Request) -> dict[str, Any]:
    body = await request.json()
    if isinstance(body, dict) and isinstance(body.get("args"), dict):
        return body["args"]
    if isinstance(body, dict):
        return body
    raise HTTPException(status_code=422, detail="Retell tool payload must be a JSON object.")


@app.on_event("startup")
async def startup() -> None:
    if settings.database_url and not settings.dev_mock_data:
        db.ensure_booking_workflow_schema()
        asyncio.create_task(outreach.run_forever())


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": settings.app_name, "env": settings.app_env}


@app.get("/health/ready")
async def readiness(response: Response) -> dict:
    production = settings.app_env.lower() in {"prod", "production"}
    required = {
        "DATABASE_URL": settings.database_url,
        "SUPABASE_URL": settings.supabase_url,
        "SUPABASE_JWT_SECRET": settings.supabase_jwt_secret,
        "INTAKE_API_KEY": settings.intake_api_key,
        "TOOL_API_KEY": settings.tool_api_key,
        "FRONTEND_ORIGIN": settings.frontend_origin,
    }
    calendar_statuses = [calendar_provider.status(owner_key) for owner_key in owners.OWNER_KEYS]
    missing = [name for name, value in required.items() if not value]
    if not any(status["configured"] for status in calendar_statuses):
        missing.append("calendar provider credentials")
    if calendar_provider.provider_name() == "calcom" and not settings.cal_webhook_secret:
        missing.append("CAL_WEBHOOK_SECRET")

    checks: dict[str, Any] = {
        "env": settings.app_env,
        "database_configured": bool(settings.database_url),
        "supabase_configured": bool(settings.supabase_url and settings.supabase_jwt_secret),
        "calendar": calendar_statuses,
        "cal_webhook_configured": bool(settings.cal_webhook_secret),
        "retell_configured": bool(settings.retell_api_key and settings.retell_agent_id and settings.retell_from_number),
        "twilio_configured": bool(settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_phone_number),
        "gmail_configured": bool(settings.gmail_username and settings.gmail_app_password),
        "momentum_configured": bool(settings.momentum_api_key),
    }
    if settings.database_url and not settings.dev_mock_data:
        try:
            db.fetch_one("select 1 as ok")
            checks["database_reachable"] = True
        except Exception as exc:
            checks["database_reachable"] = False
            checks["database_error"] = str(exc)
    else:
        checks["database_reachable"] = False

    ok = bool(checks.get("database_reachable")) and not missing
    if production and not ok:
        response.status_code = 503
    return {
        "ok": ok,
        "service": settings.app_name,
        "provider": calendar_provider.provider_name(),
        "missing": missing,
        "checks": checks,
    }


@app.get("/me")
async def me(user: dict = Depends(require_user)) -> dict:
    return {"user": user}


@app.post("/new-lead")
async def new_lead(payload: LeadIntake, _: None = Depends(require_intake_key)) -> dict:
    receipt_id = db.create_webhook_receipt("lead_intake", payload.model_dump(mode="json"), external_id=payload.meta_lead_id)
    try:
        if not payload.tcpa_consent:
            await alerts.create_alert(
                "intake_rejected",
                "urgent",
                "Lead rejected without TCPA consent",
                f"{payload.full_name} was rejected because TCPA consent was false.",
                metadata=payload.model_dump(mode="json"),
            )
            db.complete_webhook_receipt(receipt_id, "rejected", error_message="TCPA consent is required.")
            raise HTTPException(status_code=422, detail="TCPA consent is required.")

        phone_number = normalize_us_phone(payload.phone_number)
        submitted_at = parse_dt(payload.submitted_at)
        owner_key = db.next_owner_key()
        lead = db.upsert_lead(
            {
                "full_name": payload.full_name.strip(),
                "phone_number": phone_number,
                "email": payload.email,
                "business_type": payload.business_type,
                "source_detail": payload.source_detail,
                "meta_lead_id": payload.meta_lead_id,
                "tcpa_consent": payload.tcpa_consent,
                "submitted_at": submitted_at,
                "owner_key": owner_key,
                "raw_payload": payload.raw_payload or payload.model_dump(mode="json"),
            }
        )
        db.complete_webhook_receipt(receipt_id, "processed", lead_id=lead["id"])
        db.create_crm_job(lead["id"], "lead_created", {"lead": lead})

        call_result = await retell.trigger_call(lead, attempt_number=1)
        if call_result.get("call_id"):
            db.mark_call_triggered(lead["id"], call_result["call_id"], attempt_number=1)
        elif call_result.get("deferred") and call_result.get("next_allowed_call_time"):
            db.queue_sequence_for_lead_at(lead["id"], parse_dt(call_result["next_allowed_call_time"]), current_step=2)
        else:
            await alerts.create_alert(
                "retell_call_failed",
                "urgent",
                "Retell call did not trigger",
                call_result.get("error") or "Retell returned no call ID.",
                lead["id"],
                call_result,
            )
            db.queue_sequence_for_lead(lead["id"], delay_minutes=5)

        return {
            "ok": True,
            "lead_id": lead["id"],
            "owner_key": lead["owner_key"],
            "owner_name": lead["owner_name"],
            "retell_call_id": call_result.get("call_id"),
            "retell_ok": call_result.get("ok", False),
            "retell_deferred": call_result.get("deferred", False),
            "receipt_id": receipt_id,
        }
    except HTTPException:
        raise
    except Exception as exc:
        db.complete_webhook_receipt(receipt_id, "failed", error_message=str(exc))
        await alerts.create_alert("intake_failed", "urgent", "Lead intake failed", str(exc), metadata=payload.model_dump(mode="json"))
        raise


@app.post("/webhook")
async def retell_webhook(request: Request) -> dict:
    payload = await request.json()
    call = extract_retell_call(payload)
    event = first_present(payload.get("event"), payload.get("event_type"), payload.get("type"), call.get("event"))
    retell_call_id = first_present(call.get("call_id"), call.get("id"), payload.get("call_id"))
    receipt_id = db.create_webhook_receipt("retell", payload, external_id=retell_call_id)

    try:
        metadata = call.get("metadata") or {}
        lead_id = first_present(metadata.get("lead_id"), call.get("lead_id"), payload.get("lead_id"))
        lead = db.get_lead(lead_id) if lead_id else None
        if not lead and retell_call_id:
            match = db.fetch_one(
                """
                select l.*
                from call_attempts c
                join leads l on l.id = c.lead_id
                where c.retell_call_id = %s
                limit 1
                """,
                (retell_call_id,),
            )
            lead = match

        if not lead:
            db.complete_webhook_receipt(receipt_id, "failed", error_message="Lead not found for Retell webhook.")
            await alerts.create_alert(
                "retell_unmatched_call",
                "urgent",
                "Retell webhook could not match a lead",
                f"Call ID: {retell_call_id or 'missing'}",
                metadata=payload,
            )
            return {"ok": False, "error": "lead_not_found"}

        if event == "call_started":
            db.update_call_started(lead, retell_call_id, payload)
            db.complete_webhook_receipt(receipt_id, "processed", lead_id=lead["id"])
            return {"ok": True, "event": event}

        if event in {"call_ended", "call_analyzed", "call_analysis_done"}:
            if retell_call_id:
                existing_call = db.fetch_one("select id from call_attempts where retell_call_id = %s", (retell_call_id,))
                if not existing_call:
                    db.mark_call_triggered(lead["id"], retell_call_id, attempt_number=1)
            transcript = first_present(call.get("transcript"), call.get("transcript_text"))
            analysis = first_present(call.get("call_analysis"), call.get("analysis"), call.get("custom_analysis_data"), {})
            recording_url = first_present(call.get("recording_url"), call.get("recordingUrl"))
            duration_seconds = first_present(call.get("duration_seconds"), call.get("duration_ms"))
            if isinstance(duration_seconds, int) and duration_seconds > 10000:
                duration_seconds = int(duration_seconds / 1000)
            disconnection_reason = first_present(call.get("disconnection_reason"), call.get("end_reason"))
            status = "completed"
            voicemail = (
                "voicemail" in str(disconnection_reason or "").lower()
                or contains_voicemail_or_no_consent(transcript)
            )
            answered = bool(transcript) and not voicemail
            ended_at = parse_dt(first_present(call.get("end_timestamp"), call.get("ended_at"))) if first_present(call.get("end_timestamp"), call.get("ended_at")) else None
            analysis_booking_claim = analysis_claims_booking(analysis)

            updated_call = db.update_call_analyzed(
                retell_call_id,
                {
                    "status": status,
                    "ended_at": ended_at,
                    "duration_seconds": duration_seconds or 0,
                    "answered": answered,
                    "voicemail": voicemail,
                    "recording_url": recording_url,
                    "transcript": transcript,
                    "transcript_json": call.get("transcript_object") or {},
                    "summary": analysis,
                    "retell_analysis": payload,
                    "failure_reason": disconnection_reason,
                },
            )
            db.update_lead_after_call(lead["id"], answered, voicemail, status)
            db.create_crm_job(lead["id"], "call_analyzed", {"call": call, "analysis": analysis})
            momentum_result = await momentum.ingest_retell_call(lead, updated_call, call, transcript)
            db.create_crm_job(
                lead["id"],
                "momentum_call_ingested" if momentum_result.get("ok") else "momentum_call_ingest_failed",
                {"call_attempt_id": updated_call["id"] if updated_call else None, "momentum": momentum_result},
            )
            if not momentum_result.get("ok"):
                await alerts.create_alert(
                    "momentum_call_ingest_failed",
                    "warning",
                    "Momentum call ingest failed",
                    str(momentum_result.get("error") or "Momentum did not accept the call payload."),
                    lead["id"],
                    {"retell_call_id": retell_call_id, "momentum": momentum_result},
                )
            qualification_decision = extract_qualification_decision(analysis)
            if qualification_decision is not None:
                qualification_reason = None
                if isinstance(analysis, dict):
                    qualification_reason = first_present(
                        analysis.get("qualification_reason"),
                        analysis.get("disqualification_reason"),
                        analysis.get("reason"),
                    )
                log_qualification_decision(
                    lead["id"],
                    qualified=qualification_decision,
                    source="analysis",
                    call_attempt_id=updated_call["id"] if updated_call else None,
                    retell_call_id=retell_call_id,
                    reason=qualification_reason,
                    notes="Logged from Retell call analysis.",
                    transcript_excerpt=transcript,
                    raw_payload={"analysis": analysis},
                )
            if analysis_booking_claim and (voicemail or not transcript):
                await alerts.create_alert(
                    "booking_evidence_mismatch",
                    "urgent",
                    "Booking claim needs human review",
                    "Retell analysis indicated an appointment, but transcript evidence looks like voicemail or no live confirmation.",
                    lead["id"],
                    {"retell_call_id": retell_call_id, "analysis": analysis, "transcript": transcript},
                )
                db.flag_lead_for_review(
                    lead["id"],
                    "booking_evidence_mismatch",
                    {"retell_call_id": retell_call_id, "analysis": analysis, "transcript": transcript},
                )
            if not answered:
                db.queue_sequence_for_lead(lead["id"], delay_minutes=5)
            db.complete_webhook_receipt(receipt_id, "processed", lead_id=lead["id"])
            return {"ok": True, "event": event, "call_attempt_id": updated_call["id"] if updated_call else None}

        db.complete_webhook_receipt(receipt_id, "ignored", lead_id=lead["id"], error_message=f"Ignored event: {event}")
        return {"ok": True, "event": event, "ignored": True}
    except Exception as exc:
        db.complete_webhook_receipt(receipt_id, "failed", error_message=str(exc))
        await alerts.create_alert("retell_webhook_failed", "urgent", "Retell webhook failed", str(exc), metadata=payload)
        raise


@app.post("/sms-webhook", response_class=PlainTextResponse)
async def sms_webhook(request: Request) -> str:
    form = await request.form()
    payload = dict(form)
    raw_from = payload.get("From", "")
    try:
        from_number = normalize_us_phone(raw_from)
    except ValueError:
        from_number = raw_from
    to_number = payload.get("To")
    body = payload.get("Body", "")
    twilio_sid = payload.get("MessageSid")
    receipt_id = db.create_webhook_receipt("twilio_sms", payload, external_id=twilio_sid)
    lead = db.get_lead_by_phone(from_number)
    try:
        if lead:
            db.upsert_sms_message(
                {
                    "lead_id": lead["id"],
                    "direction": "inbound",
                    "from_number": from_number,
                    "to_number": to_number,
                    "body": body,
                    "status": "received",
                    "twilio_sid": twilio_sid,
                    "raw_payload": payload,
                }
            )
            if twilio.is_stop_message(body):
                db.set_sms_opt_out(lead["id"])
            db.complete_webhook_receipt(receipt_id, "processed", lead_id=lead["id"])
        else:
            db.complete_webhook_receipt(receipt_id, "unmatched", error_message="No matching lead.")
            await alerts.create_alert("sms_unmatched", "warning", "Inbound SMS did not match a lead", from_number, metadata=payload)
    except Exception as exc:
        db.complete_webhook_receipt(receipt_id, "failed", error_message=str(exc))
        await alerts.create_alert("sms_webhook_failed", "urgent", "Twilio SMS webhook failed", str(exc), lead["id"] if lead else None, payload)
    return ""


def lead_from_calcom_booking(booking: dict) -> dict | None:
    lead_id = calcom.webhook_lead_id(booking)
    if lead_id:
        lead = db.get_lead(lead_id)
        if lead:
            return lead

    attendee = calcom.webhook_attendee(booking)
    if attendee.get("email"):
        lead = db.get_lead_by_email(attendee["email"])
        if lead:
            return lead
    if attendee.get("phone"):
        try:
            phone_number = normalize_us_phone(attendee["phone"])
            lead = db.get_lead_by_phone(phone_number)
            if lead:
                return lead
        except ValueError:
            pass
    return None


@app.post("/calcom/webhook")
async def calcom_webhook(request: Request) -> dict:
    if not settings.cal_webhook_secret:
        raise HTTPException(status_code=503, detail="CAL_WEBHOOK_SECRET is not configured.")

    raw_body = await request.body()
    signature = request.headers.get("x-cal-signature-256")
    if not calcom.verify_webhook_signature(raw_body, signature, settings.cal_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid Cal.com webhook signature.")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Cal.com webhook payload must be JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Cal.com webhook payload must be a JSON object.")

    event = calcom.webhook_event_name(payload)
    booking = calcom.webhook_booking_payload(payload)
    booking_uid = calcom.webhook_booking_uid(booking)
    receipt_id = db.create_webhook_receipt("calcom", payload, external_id=booking_uid)

    try:
        if event in calcom.BOOKING_CANCELLED_EVENTS:
            appointment = db.cancel_calendar_appointment(
                "calcom",
                booking_uid,
                {"calendar": payload, "trigger_event": event, "source": "calcom_webhook"},
            )
            if appointment:
                db.create_crm_job(appointment["lead_id"], "appointment_cancelled", {"appointment": appointment, "calendar": payload})
                db.complete_webhook_receipt(receipt_id, "processed", lead_id=appointment["lead_id"])
                return {"ok": True, "event": event, "appointment": appointment}
            db.complete_webhook_receipt(receipt_id, "unmatched", error_message="No matching appointment for Cal.com cancellation.")
            await alerts.create_alert(
                "calcom_unmatched_cancellation",
                "warning",
                "Cal.com cancellation did not match an appointment",
                f"Booking ID: {booking_uid or 'missing'}",
                metadata=payload,
            )
            return {"ok": False, "event": event, "error": "appointment_not_found"}

        if event not in calcom.BOOKING_CREATED_EVENTS | calcom.BOOKING_RESCHEDULED_EVENTS:
            db.complete_webhook_receipt(receipt_id, "ignored", error_message=f"Ignored event: {event}")
            return {"ok": True, "event": event, "ignored": True}

        lead = lead_from_calcom_booking(booking)
        if not lead:
            db.complete_webhook_receipt(receipt_id, "unmatched", error_message="No matching lead for Cal.com booking.")
            await alerts.create_alert(
                "calcom_unmatched_booking",
                "urgent",
                "Cal.com booking did not match a lead",
                f"Booking ID: {booking_uid or 'missing'}",
                metadata=payload,
            )
            return {"ok": False, "event": event, "error": "lead_not_found"}

        appointment = db.upsert_calendar_appointment(calcom.appointment_values_from_webhook(payload, lead, event))
        job_type = "appointment_rescheduled" if event in calcom.BOOKING_RESCHEDULED_EVENTS else "appointment_booked"
        db.create_crm_job(lead["id"], job_type, {"appointment": appointment, "calendar": payload})
        db.complete_webhook_receipt(receipt_id, "processed", lead_id=lead["id"])
        return {"ok": True, "event": event, "lead_id": lead["id"], "appointment": appointment}
    except Exception as exc:
        db.complete_webhook_receipt(receipt_id, "failed", error_message=str(exc))
        await alerts.create_alert("calcom_webhook_failed", "urgent", "Cal.com webhook failed", str(exc), metadata=payload)
        raise


@app.get("/dashboard/kpis")
async def dashboard_kpis(_: dict = Depends(require_user)) -> dict:
    if settings.dev_mock_data:
        return {"ok": True, "kpis": mock_data.kpis()}
    return {"ok": True, "kpis": db.get_kpis()}


@app.post("/leads/list")
async def leads_list(payload: LeadListRequest, _: dict = Depends(require_user)) -> dict:
    if settings.dev_mock_data:
        search = (payload.search or "").lower()
        rows = [lead for lead in mock_data.leads if not search or search in lead["full_name"].lower() or search in lead["phone_number"] or search in lead["email"].lower()]
        return {"ok": True, "leads": rows[payload.offset:payload.offset + payload.limit]}
    return {"ok": True, "leads": db.list_leads(payload.search, payload.status, payload.limit, payload.offset)}


@app.get("/leads/{lead_id}")
async def lead_detail(lead_id: str, _: dict = Depends(require_user)) -> dict:
    if settings.dev_mock_data:
        lead = next((row for row in mock_data.leads if row["id"] == lead_id), None)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found.")
        calls = [row for row in mock_data.calls if row["lead_id"] == lead_id]
        appointments = [row for row in mock_data.appointments if row["lead_id"] == lead_id]
        return {"ok": True, "lead": lead, "calls": calls, "appointments": appointments, "appointment_holds": []}
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")
    calls = db.fetch_all("select * from call_attempts where lead_id = %s order by created_at desc", (lead_id,))
    calls = owners.with_owner_names([{**call, "owner_key": lead["owner_key"]} for call in calls])
    appointments = db.fetch_all(
        """
        select *,
               coalesce((metadata->>'booked_through_chat')::boolean, false) as booked_through_chat,
               coalesce((metadata->>'fallback_used')::boolean, false) as fallback_used
        from appointments
        where lead_id = %s
        order by start_time desc
        """,
        (lead_id,),
    )
    appointments = owners.with_owner_names(appointments)
    appointment_holds = db.fetch_all(
        """
        select *
        from appointment_slot_holds
        where lead_id = %s
        order by created_at desc
        """,
        (lead_id,),
    )
    appointment_holds = owners.with_owner_names(appointment_holds)
    return {"ok": True, "lead": lead, "calls": calls, "appointments": appointments, "appointment_holds": appointment_holds}


@app.get("/calls/list")
async def calls_list(limit: int = 50, offset: int = 0, _: dict = Depends(require_user)) -> dict:
    if settings.dev_mock_data:
        return {"ok": True, "calls": mock_data.calls[offset:offset + limit]}
    return {"ok": True, "calls": db.list_calls(limit, offset)}


@app.get("/appointments/list")
async def appointments_list(limit: int = 50, offset: int = 0, _: dict = Depends(require_user)) -> dict:
    if settings.dev_mock_data:
        return {"ok": True, "appointments": mock_data.appointments[offset:offset + limit]}
    return {"ok": True, "appointments": db.list_appointments(limit, offset)}


@app.post("/leads/qualification")
async def lead_qualification(payload: LeadQualificationLogRequest, _: None = Depends(require_tool_key)) -> dict:
    logged = log_qualification_decision(
        payload.lead_id,
        qualified=payload.qualified,
        source=payload.source,
        call_attempt_id=payload.call_attempt_id,
        reason=payload.reason,
        notes=payload.notes,
        transcript_excerpt=payload.transcript_excerpt,
        raw_payload=payload.raw_payload,
    )
    return {"ok": True, **logged}


@app.post("/retell/leads/qualification")
async def retell_lead_qualification(request: Request, _: None = Depends(require_tool_key)) -> dict:
    payload = LeadQualificationLogRequest(**await retell_tool_args(request))
    return await lead_qualification(payload, _)


@app.post("/calendar/hold")
async def calendar_hold(payload: CalendarHoldRequest, _: None = Depends(require_tool_key)) -> dict:
    start_time = ensure_aware_utc(payload.start_time)
    end_time = ensure_aware_utc(payload.end_time) if payload.end_time else (start_time + timedelta(minutes=30))
    if start_time <= utc_now():
        raise HTTPException(status_code=422, detail="Appointment holds must be in the future.")
    if end_time <= start_time:
        raise HTTPException(status_code=422, detail="Appointment hold end_time must be after start_time.")
    if not is_business_hours_et(start_time):
        raise HTTPException(status_code=422, detail="Appointment holds must be weekdays between 9AM and 6PM ET.")

    lead = db.get_lead(payload.lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")
    if lead.get("appointment_booked"):
        raise HTTPException(status_code=409, detail="Lead already has a booked appointment.")
    qualification = (lead.get("raw_payload") or {}).get("qualification") or {}
    if lead.get("status") != "qualified" and qualification.get("qualified") is not True:
        await alerts.create_alert(
            "slot_hold_rejected_unqualified",
            "warning",
            "Slot hold rejected",
            "AI attempted to hold an appointment slot before logging the lead as qualified.",
            payload.lead_id,
            payload.model_dump(mode="json"),
        )
        raise HTTPException(status_code=409, detail="Lead must be logged as qualified before holding an appointment slot.")

    preferred_owner_key = owners.normalize_owner_key(payload.preferred_owner_key or lead.get("owner_key"))
    expires_at = utc_now() + timedelta(minutes=payload.ttl_minutes)
    attempts = []
    hold = None
    selected_owner_key = None
    for owner_key in owners.owner_order(preferred_owner_key):
        local_conflict = db.find_conflicting_appointment(start_time, end_time, lead_id=payload.lead_id, owner_key=owner_key)
        if local_conflict:
            attempts.append(
                {
                    "owner_key": owner_key,
                    "owner_name": owners.owner_name(owner_key),
                    "available": False,
                    "status": "local_conflict",
                    "message": "This handler already has a local booking at that time.",
                    "conflicting_appointment_id": local_conflict.get("id"),
                }
            )
            continue

        hold_conflict = db.find_conflicting_hold(start_time, end_time, owner_key)
        if hold_conflict:
            attempts.append(
                {
                    "owner_key": owner_key,
                    "owner_name": owners.owner_name(owner_key),
                    "available": False,
                    "status": "slot_held",
                    "message": "This handler already has a temporary hold at that time.",
                    "conflicting_hold_id": hold_conflict.get("id"),
                }
            )
            continue

        availability = await calendar_provider.get_availability(start_time, end_time, owner_key)
        availability_status = "available" if availability.get("available") else ("calendar_error" if not availability.get("ok") else "calendar_busy")
        attempts.append({**availability, "status": availability_status})
        if not availability.get("ok") or not availability.get("available"):
            continue

        try:
            hold = db.create_appointment_hold(
                {
                    "lead_id": payload.lead_id,
                    "call_attempt_id": payload.call_attempt_id,
                    "owner_key": owner_key,
                    "start_time": start_time,
                    "end_time": end_time,
                    "timezone": settings.calendar_timezone,
                    "expires_at": expires_at,
                    "availability_verified_at": utc_now(),
                    "transcript_excerpt": payload.transcript_excerpt,
                    "metadata": {
                        "requested_by": payload.requested_by,
                        "preferred_owner_key": preferred_owner_key,
                        "preferred_owner_name": owners.owner_name(preferred_owner_key),
                        "fallback_used": owner_key != preferred_owner_key,
                        "availability": availability,
                        "raw_payload": payload.raw_payload or {},
                    },
                }
            )
            selected_owner_key = owner_key
            attempts[-1] = {**attempts[-1], "status": "held", "hold_id": hold["id"]}
            break
        except RuntimeError as exc:
            attempts[-1] = {**attempts[-1], "status": "hold_failed", "available": False, "error": str(exc)}

    if not hold or not selected_owner_key:
        await alerts.create_alert(
            "slot_hold_failed",
            "warning",
            "Appointment slot hold failed",
            "No handler was available to temporarily hold the requested slot.",
            payload.lead_id,
            {"attempts": attempts, "preferred_owner_key": preferred_owner_key, "request": payload.model_dump(mode="json")},
        )
        calendar_error = any(attempt.get("status") in {"calendar_error", "hold_failed"} or (not attempt.get("ok") and attempt.get("configured") is False) for attempt in attempts)
        status_code = 502 if calendar_error and not any(attempt.get("status") in {"local_conflict", "slot_held", "calendar_busy"} for attempt in attempts) else 409
        raise HTTPException(
            status_code=status_code,
            detail={
                "message": "No available handler for the requested slot.",
                "preferred_owner_key": preferred_owner_key,
                "attempts": attempts,
            },
        )

    db.create_crm_job(payload.lead_id, "appointment_slot_held", {"hold": hold, "attempts": attempts})
    return {
        "ok": True,
        "hold": hold,
        "attempts": attempts,
        "fallback_used": selected_owner_key != preferred_owner_key,
        "expires_at": hold["expires_at"],
    }


@app.post("/retell/calendar/hold")
async def retell_calendar_hold(request: Request, _: None = Depends(require_tool_key)) -> dict:
    payload = CalendarHoldRequest(**await retell_tool_args(request))
    return await calendar_hold(payload, _)


@app.post("/calendar/holds/{hold_id}/time-agreement")
async def calendar_hold_time_agreement(hold_id: str, payload: CalendarHoldTimeAgreementRequest, _: None = Depends(require_tool_key)) -> dict:
    if not contains_time_agreement(payload.customer_phrase):
        hold = db.get_appointment_hold(hold_id)
        if hold:
            db.flag_lead_for_review(
                hold["lead_id"],
                "time_agreement_phrase_rejected",
                {"hold_id": hold_id, "customer_phrase": payload.customer_phrase},
            )
        raise HTTPException(status_code=422, detail="Time agreement requires the customer's exact affirmative phrase for the proposed slot.")
    hold = db.mark_hold_time_agreed(hold_id, payload.model_dump(mode="json"))
    if not hold:
        raise HTTPException(status_code=409, detail="Appointment hold is missing, expired, or no longer active.")
    db.create_crm_job(hold["lead_id"], "appointment_time_agreed", {"hold": hold})
    return {"ok": True, "hold": hold}


@app.post("/calendar/time-agreement")
async def calendar_hold_time_agreement_by_id(payload: CalendarHoldTimeAgreementByIdRequest, _: None = Depends(require_tool_key)) -> dict:
    next_payload = CalendarHoldTimeAgreementRequest(**payload.model_dump(exclude={"hold_id"}))
    return await calendar_hold_time_agreement(payload.hold_id, next_payload, _)


@app.post("/calendar/latest-hold/time-agreement")
async def calendar_latest_hold_time_agreement(payload: CalendarLatestHoldTimeAgreementRequest, _: None = Depends(require_tool_key)) -> dict:
    hold = db.get_latest_active_hold_for_lead(payload.lead_id)
    if not hold:
        raise HTTPException(status_code=404, detail="No active appointment hold found for this lead.")
    next_payload = CalendarHoldTimeAgreementRequest(**payload.model_dump(exclude={"lead_id"}))
    return await calendar_hold_time_agreement(hold["id"], next_payload, _)


@app.post("/retell/calendar/latest-hold/time-agreement")
async def retell_calendar_latest_hold_time_agreement(request: Request, _: None = Depends(require_tool_key)) -> dict:
    payload = CalendarLatestHoldTimeAgreementRequest(**await retell_tool_args(request))
    return await calendar_latest_hold_time_agreement(payload, _)


@app.post("/calendar/holds/{hold_id}/booking-confirmation")
async def calendar_hold_booking_confirmation(hold_id: str, payload: CalendarHoldBookingConfirmationRequest, _: None = Depends(require_tool_key)) -> dict:
    evidence_ok, evidence_message = validate_booking_evidence(
        customer_confirmed=True,
        customer_confirmation=payload.customer_phrase,
        agent_recap=payload.agent_recap,
        transcript_excerpt=payload.transcript_excerpt,
    )
    if not evidence_ok:
        hold = db.get_appointment_hold(hold_id)
        if hold:
            db.flag_lead_for_review(
                hold["lead_id"],
                "booking_confirmation_phrase_rejected",
                {"hold_id": hold_id, "customer_phrase": payload.customer_phrase, "message": evidence_message},
            )
        raise HTTPException(status_code=422, detail=evidence_message)
    hold = db.mark_hold_booking_confirmed(hold_id, payload.model_dump(mode="json"))
    if not hold:
        raise HTTPException(status_code=409, detail="Appointment hold must have time agreement before final booking confirmation.")
    db.create_crm_job(hold["lead_id"], "appointment_booking_confirmed", {"hold": hold, "verification_message": evidence_message})
    return {"ok": True, "hold": hold, "verification_message": evidence_message}


@app.post("/calendar/booking-confirmation")
async def calendar_hold_booking_confirmation_by_id(payload: CalendarHoldBookingConfirmationByIdRequest, _: None = Depends(require_tool_key)) -> dict:
    next_payload = CalendarHoldBookingConfirmationRequest(**payload.model_dump(exclude={"hold_id"}))
    return await calendar_hold_booking_confirmation(payload.hold_id, next_payload, _)


@app.post("/calendar/latest-hold/booking-confirmation")
async def calendar_latest_hold_booking_confirmation(payload: CalendarLatestHoldBookingConfirmationRequest, _: None = Depends(require_tool_key)) -> dict:
    hold = db.get_latest_active_hold_for_lead(payload.lead_id)
    if not hold:
        raise HTTPException(status_code=404, detail="No active appointment hold found for this lead.")
    next_payload = CalendarHoldBookingConfirmationRequest(**payload.model_dump(exclude={"lead_id"}))
    return await calendar_hold_booking_confirmation(hold["id"], next_payload, _)


@app.post("/retell/calendar/latest-hold/booking-confirmation")
async def retell_calendar_latest_hold_booking_confirmation(request: Request, _: None = Depends(require_tool_key)) -> dict:
    payload = CalendarLatestHoldBookingConfirmationRequest(**await retell_tool_args(request))
    return await calendar_latest_hold_booking_confirmation(payload, _)


@app.post("/calendar/availability")
async def calendar_availability(payload: CalendarAvailabilityRequest, _: None = Depends(require_tool_key)) -> dict:
    start_time = ensure_aware_utc(payload.start_time)
    end_time = ensure_aware_utc(payload.end_time)
    preferred_owner_key = owners.normalize_owner_key(payload.preferred_owner_key)
    attempts = []
    for owner_key in owners.owner_order(preferred_owner_key):
        attempts.append(await calendar_provider.get_availability(start_time, end_time, owner_key))
    return {
        "ok": any(attempt.get("ok") for attempt in attempts),
        "preferred_owner_key": preferred_owner_key,
        "preferred_owner_name": owners.owner_name(preferred_owner_key),
        "owners": attempts,
    }


@app.post("/retell/calendar/availability")
async def retell_calendar_availability(request: Request, _: None = Depends(require_tool_key)) -> dict:
    payload = CalendarAvailabilityRequest(**await retell_tool_args(request))
    return await calendar_availability(payload, _)


@app.post("/calendar/book")
async def calendar_book(payload: CalendarBookingRequest, _: None = Depends(require_tool_key)) -> dict:
    hold = None
    hold_id = payload.hold_id
    evidence_message = "Booking evidence verified."

    if hold_id:
        hold = db.get_appointment_hold(hold_id)
        if not hold:
            raise HTTPException(status_code=404, detail="Appointment hold not found.")
        if str(hold.get("lead_id")) != str(payload.lead_id):
            db.flag_lead_for_review(
                payload.lead_id,
                "booking_hold_lead_mismatch",
                {"hold_id": hold_id, "hold_lead_id": hold.get("lead_id")},
            )
            raise HTTPException(status_code=409, detail="Appointment hold does not belong to this lead.")
        if hold.get("status") != "confirmed" or not hold.get("step1_time_agreed_at") or not hold.get("step2_booking_confirmed_at"):
            db.flag_lead_for_review(
                payload.lead_id,
                "booking_attempt_without_verified_hold",
                {"hold_id": hold_id, "hold_status": hold.get("status")},
            )
            raise HTTPException(status_code=409, detail="Appointment hold must complete time agreement and final booking confirmation before booking.")
        if ensure_aware_utc(hold["expires_at"]) <= utc_now():
            db.mark_hold_status(hold_id, "expired")
            raise HTTPException(status_code=409, detail="Appointment hold expired before booking.")

        start_time = ensure_aware_utc(hold["start_time"])
        end_time = ensure_aware_utc(hold["end_time"])
        selected_owner_key = owners.normalize_owner_key(hold.get("owner_key"))
        preferred_owner_key = owners.normalize_owner_key((hold.get("metadata") or {}).get("preferred_owner_key") or payload.preferred_owner_key or selected_owner_key)
        customer_confirmation = hold.get("step2_customer_phrase")
        agent_recap = hold.get("step2_agent_recap")
        transcript_excerpt = payload.transcript_excerpt or hold.get("transcript_excerpt")
        evidence_ok, evidence_message = validate_booking_evidence(
            customer_confirmed=True,
            customer_confirmation=customer_confirmation,
            agent_recap=agent_recap,
            transcript_excerpt=transcript_excerpt,
        )
        if not evidence_ok:
            db.flag_lead_for_review(
                payload.lead_id,
                "verified_hold_evidence_rejected",
                {"hold_id": hold_id, "message": evidence_message},
            )
            raise HTTPException(status_code=422, detail=evidence_message)
    else:
        if not payload.start_time:
            raise HTTPException(status_code=422, detail="start_time is required when hold_id is not provided.")
        start_time = ensure_aware_utc(payload.start_time)
        end_time = ensure_aware_utc(payload.end_time) if payload.end_time else (start_time + timedelta(minutes=30))
        evidence_ok, evidence_message = validate_booking_evidence(
            customer_confirmed=payload.customer_confirmed,
            customer_confirmation=payload.customer_confirmation,
            agent_recap=payload.agent_recap,
            transcript_excerpt=payload.transcript_excerpt,
        )
        if not evidence_ok:
            await alerts.create_alert(
                "booking_evidence_rejected",
                "warning",
                "Calendar booking rejected",
                evidence_message,
                payload.lead_id,
                payload.model_dump(mode="json"),
            )
            db.flag_lead_for_review(
                payload.lead_id,
                "booking_evidence_rejected",
                {"message": evidence_message, "request": payload.model_dump(mode="json")},
            )
            raise HTTPException(status_code=422, detail=evidence_message)

    if start_time <= utc_now():
        raise HTTPException(status_code=422, detail="Appointments must be booked in the future.")
    if not is_business_hours_et(start_time):
        raise HTTPException(status_code=422, detail="Appointments must be booked weekdays between 9AM and 6PM ET.")
    if end_time <= start_time:
        raise HTTPException(status_code=422, detail="Appointment end_time must be after start_time.")

    if db.lead_has_booked_appointment(payload.lead_id):
        raise HTTPException(status_code=409, detail="Lead already has a booked appointment.")

    lead = db.get_lead(payload.lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    attempts = []
    booking_result = None
    if not hold:
        preferred_owner_key = owners.normalize_owner_key(payload.preferred_owner_key or lead.get("owner_key"))
        selected_owner_key = None
        db.flag_lead_for_review(
            payload.lead_id,
            "legacy_booking_without_slot_hold",
            {"request": payload.model_dump(mode="json")},
        )
    else:
        db.mark_hold_status(hold_id, "booking")

    owner_candidates = [selected_owner_key] if hold else owners.owner_order(preferred_owner_key)
    for owner_key in owner_candidates:
        local_conflict = db.find_conflicting_appointment(start_time, end_time, lead_id=payload.lead_id, owner_key=owner_key)
        if local_conflict:
            attempts.append(
                {
                    "owner_key": owner_key,
                    "owner_name": owners.owner_name(owner_key),
                    "available": False,
                    "status": "local_conflict",
                    "message": "This handler already has a local booking at that time.",
                    "conflicting_appointment_id": local_conflict.get("id"),
                }
            )
            continue

        availability = await calendar_provider.get_availability(start_time, end_time, owner_key)
        availability_status = "available" if availability.get("available") else ("calendar_error" if not availability.get("ok") else "calendar_busy")
        attempts.append({**availability, "status": availability_status})
        if not availability.get("ok") or not availability.get("available"):
            continue

        next_booking_payload = {
            **payload.model_dump(),
            "owner_key": owner_key,
            "start_time": start_time,
            "end_time": end_time,
        }
        next_booking_result = await calendar_provider.book_meeting(next_booking_payload)
        if next_booking_result["ok"]:
            booking_result = next_booking_result
            selected_owner_key = owner_key
            attempts[-1] = {**attempts[-1], "status": "booked", "booking_id": next_booking_result.get("booking_id")}
            break
        attempts[-1] = {
            **attempts[-1],
            "status": "booking_failed",
            "available": False,
            "error": next_booking_result.get("error") or "The calendar provider did not create a booking.",
            "status_code": next_booking_result.get("status_code"),
        }

    if not booking_result or not selected_owner_key:
        if hold_id:
            db.mark_hold_status(hold_id, "confirmed", metadata={"booking_failed_attempts": attempts})
        db.flag_lead_for_review(
            payload.lead_id,
            "calendar_booking_failed",
            {"attempts": attempts, "preferred_owner_key": preferred_owner_key},
        )
        await alerts.create_alert(
            "calendar_booking_failed",
            "urgent",
            "Calendar booking failed",
            "No handler was available for the requested appointment slot.",
            payload.lead_id,
            {"attempts": attempts, "preferred_owner_key": preferred_owner_key, "request": payload.model_dump(mode="json")},
        )
        calendar_error = any(attempt.get("status") in {"calendar_error", "booking_failed"} or (not attempt.get("ok") and attempt.get("configured") is False) for attempt in attempts)
        status_code = 502 if calendar_error and not any(attempt.get("status") in {"local_conflict", "calendar_busy"} for attempt in attempts) else 409
        raise HTTPException(
            status_code=status_code,
            detail={
                "message": "No available handler for the requested slot.",
                "preferred_owner_key": preferred_owner_key,
                "attempts": attempts,
            },
        )

    fallback_used = selected_owner_key != preferred_owner_key
    try:
        appointment = db.create_appointment(
            {
                "lead_id": payload.lead_id,
                "call_attempt_id": payload.call_attempt_id or (hold or {}).get("call_attempt_id"),
                "cal_booking_id": booking_result.get("booking_id") or None,
                "cal_event_type_id": booking_result.get("event_type_id"),
                "calendar_provider": booking_result.get("provider") or calendar_provider.provider_name(),
                "external_booking_id": booking_result.get("booking_id") or None,
                "external_event_type_id": booking_result.get("event_type_id"),
                "owner_key": selected_owner_key,
                "start_time": start_time,
                "end_time": end_time,
                "timezone": settings.calendar_timezone,
                "event_title": f"Ashmont GL consultation - {payload.attendee_name}",
                "meeting_url": booking_result.get("meeting_url"),
                "invitee_email": payload.attendee_email,
                "transcript_verified": True,
                "metadata": {
                    "calendar": booking_result.get("data", {}),
                    "calendar_provider": booking_result.get("provider") or calendar_provider.provider_name(),
                    "owner_key": selected_owner_key,
                    "owner_name": owners.owner_name(selected_owner_key),
                    "preferred_owner_key": preferred_owner_key,
                    "preferred_owner_name": owners.owner_name(preferred_owner_key),
                    "fallback_used": fallback_used,
                    "attempted_owners": attempts,
                    "hold_id": hold_id,
                    "booking_evidence": {
                        "customer_confirmed": True if hold else payload.customer_confirmed,
                        "customer_confirmation": (hold or {}).get("step2_customer_phrase") or payload.customer_confirmation,
                        "time_agreement": (hold or {}).get("step1_customer_phrase"),
                        "agent_recap": (hold or {}).get("step2_agent_recap") or payload.agent_recap,
                        "transcript_excerpt": payload.transcript_excerpt or (hold or {}).get("transcript_excerpt"),
                        "verification_message": evidence_message,
                    },
                },
            }
        )
    except RuntimeError as exc:
        await alerts.create_alert(
            "local_booking_conflict_after_calendar",
            "urgent",
            "Calendar booking needs manual reconciliation",
            str(exc),
            payload.lead_id,
                {"calendar": booking_result, "request": payload.model_dump(mode="json")},
        )
        db.flag_lead_for_review(
            payload.lead_id,
            "local_booking_conflict_after_calendar",
            {"error": str(exc), "calendar": booking_result, "request": payload.model_dump(mode="json")},
        )
        if hold_id:
            db.mark_hold_status(hold_id, "confirmed", metadata={"local_conflict_after_calendar": str(exc)})
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if hold_id:
        db.mark_hold_status(
            hold_id,
            "booked",
            appointment["id"],
            {
                "calendar_provider": booking_result.get("provider") or calendar_provider.provider_name(),
                "external_booking_id": booking_result.get("booking_id"),
                "cal_booking_id": booking_result.get("booking_id"),
            },
        )

    notification = await momentum.send_appointment_notification(
        appointment=appointment,
        lead=lead,
        booking=booking_result,
        fallback_used=fallback_used,
    )
    if notification.get("ok"):
        db.create_crm_job(
            payload.lead_id,
            "appointment_notification_sent",
            {"appointment_id": appointment["id"], "notification": notification},
        )
    else:
        db.create_crm_job(
            payload.lead_id,
            "appointment_notification_failed",
            {"appointment_id": appointment["id"], "notification": notification},
        )
        await alerts.create_alert(
            "appointment_notification_failed",
            "warning",
            "Appointment notification did not send",
            str(notification.get("error") or "Momentum appointment notification failed."),
            payload.lead_id,
            {"appointment_id": appointment["id"], "notification": notification},
        )
        db.flag_lead_for_review(
            payload.lead_id,
            "appointment_notification_failed",
            {"appointment_id": appointment["id"], "notification": notification},
        )

    return {
        "ok": True,
        "appointment": appointment,
        "calendar": booking_result,
        "cal": booking_result,
        "fallback_used": fallback_used,
        "attempts": attempts,
        "notification": notification,
    }


@app.post("/calendar/latest-hold/book")
async def calendar_latest_hold_book(payload: CalendarBookingRequest, _: None = Depends(require_tool_key)) -> dict:
    hold = db.get_latest_active_hold_for_lead(payload.lead_id)
    if not hold:
        raise HTTPException(status_code=404, detail="No active appointment hold found for this lead.")
    next_payload = CalendarBookingRequest(**{**payload.model_dump(), "hold_id": hold["id"]})
    return await calendar_book(next_payload, _)


@app.post("/retell/calendar/latest-hold/book")
async def retell_calendar_latest_hold_book(request: Request, _: None = Depends(require_tool_key)) -> dict:
    payload = CalendarBookingRequest(**await retell_tool_args(request))
    return await calendar_latest_hold_book(payload, _)


@app.get("/outreach/config")
async def get_outreach_config(_: dict = Depends(require_user)) -> dict:
    if settings.dev_mock_data:
        return {"ok": True, "steps": mock_data.outreach_steps}
    return {"ok": True, "steps": db.get_outreach_config()}


@app.post("/outreach/config")
async def save_outreach_step(payload: OutreachStepInput, _: dict = Depends(require_user)) -> dict:
    if payload.channel not in {"voice", "sms"}:
        raise HTTPException(status_code=422, detail="Only voice and sms channels are supported in Ashmont v1.")
    return {"ok": True, "step": db.upsert_outreach_step(payload.model_dump())}


@app.get("/alerts/list")
async def alerts_list(status: str | None = None, limit: int = 50, _: dict = Depends(require_user)) -> dict:
    if settings.dev_mock_data:
        return {"ok": True, "alerts": mock_data.alerts[:limit]}
    return {"ok": True, "alerts": db.list_alerts(status, limit)}


@app.post("/alerts/{alert_id}/resolve")
async def alert_resolve(alert_id: str, _: dict = Depends(require_user)) -> dict:
    row = db.resolve_alert(alert_id)
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found.")
    return {"ok": True, "alert": row}


@app.post("/ad-spend")
async def ad_spend(payload: AdSpendInput, _: dict = Depends(require_user)) -> dict:
    return {"ok": True, "spend": db.upsert_ad_spend(payload.spend_date, payload.campaign, payload.spend_amount)}
