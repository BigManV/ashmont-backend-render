from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import Random
from uuid import NAMESPACE_DNS, uuid5

import owners


SEED_NAMESPACE = "ashmont-demo-2026"
SEED_SOURCE = "demo_seed_2026_ashmont"
HANDLER_KEYS = owners.OWNER_KEYS

BOOKING_OUTCOMES = [
    ("aditya", "aditya", False, "direct_aditya"),
    ("archit", "archit", False, "direct_archit"),
    ("aditya", "archit", True, "aditya_unavailable_archit_booked"),
    ("archit", "aditya", True, "archit_unavailable_aditya_booked"),
]

PEOPLE = [
    ("Avery Simmons", "Simmons Roofing Group", "Roofing contractor"),
    ("Nolan Brooks", "Brooks Concrete Repair", "Concrete contractor"),
    ("Priya Shah", "Shah Facility Services", "Janitorial contractor"),
    ("Jordan Miles", "Miles Electrical Co", "Electrical contractor"),
    ("Camille Bennett", "Bennett Custom Builders", "General contractor"),
    ("Omar Castillo", "Castillo Plumbing Pros", "Plumbing contractor"),
    ("Leah Nguyen", "Nguyen HVAC Services", "HVAC contractor"),
    ("Malcolm Price", "Price Masonry Works", "Masonry contractor"),
    ("Daphne Wallace", "Wallace Landscape Design", "Landscaping contractor"),
    ("Victor Chen", "Chen Finish Carpentry", "Carpentry contractor"),
    ("Hannah Ortiz", "Ortiz Commercial Painting", "Painting contractor"),
    ("Garrett Novak", "Novak Fire Protection", "Fire sprinkler contractor"),
    ("Maya Thompson", "Thompson Flooring Studio", "Flooring contractor"),
    ("Andre Walker", "Walker Site Cleanup", "Demolition contractor"),
    ("Sierra Cole", "Cole Security Installations", "Low voltage contractor"),
    ("Elias Romero", "Romero Tile and Stone", "Tile contractor"),
    ("Natalie Kim", "Kim Window Systems", "Glazing contractor"),
    ("Terrence Hayes", "Hayes Asphalt Maintenance", "Paving contractor"),
    ("Brooke Lawson", "Lawson Pool Service", "Pool service contractor"),
    ("Anika Patel", "Patel Commercial Drywall", "Drywall contractor"),
    ("Zachary Quinn", "Quinn Roofing and Gutters", "Roofing contractor"),
    ("Danielle Foster", "Foster Mechanical", "Mechanical contractor"),
    ("Idris Morgan", "Morgan Restoration Crew", "Water damage restoration"),
    ("Carla Jimenez", "Jimenez Cleaning Services", "Commercial cleaning"),
    ("Grant Ellis", "Ellis Steel Fab", "Metal fabrication"),
    ("Naomi Fisher", "Fisher Solar Installs", "Solar installation contractor"),
    ("Miles Carter", "Carter Framing Co", "Framing contractor"),
    ("Tasha Greene", "Greene Tree Care", "Tree service contractor"),
    ("Peter Wong", "Wong Elevator Service", "Elevator service contractor"),
    ("Felicia Grant", "Grant Event Builds", "Temporary structure contractor"),
    ("Samir Desai", "Desai Pest Solutions", "Pest control contractor"),
    ("Kendra Owens", "Owens Commercial Glass", "Glass contractor"),
    ("Marcus Reed", "Reed Roofing", "Roofing contractor"),
    ("Elena Torres", "Torres Builds", "General contractor"),
    ("Ryan Patel", "Patel Plumbing", "Plumbing contractor"),
    ("Lydia Stone", "Stone Commercial Interiors", "Interior buildout contractor"),
    ("Devon Hart", "Hart Excavation", "Excavation contractor"),
    ("Paula McKinney", "McKinney Appliance Repair", "Appliance repair"),
    ("Connor Walsh", "Walsh Fence Company", "Fencing contractor"),
    ("Janelle Rivers", "Rivers Property Maintenance", "Property maintenance"),
    ("Brianna Holt", "Holt Door and Dock", "Overhead door contractor"),
    ("Ethan Park", "Park Parking Lot Striping", "Striping contractor"),
    ("Sophie Lambert", "Lambert Sign Installers", "Sign installation contractor"),
    ("Keith Duncan", "Duncan Septic Service", "Septic contractor"),
    ("Rina Kapoor", "Kapoor Cabinetry", "Cabinet installation"),
    ("Isaac Vaughn", "Vaughn Moving Labor", "Moving contractor"),
    ("Melissa Byrne", "Byrne Commercial Laundry", "Laundry equipment service"),
    ("Anton Brooks", "Brooks Snow Removal", "Snow removal contractor"),
    ("Grace Miller", "Miller Commercial Kitchens", "Kitchen equipment contractor"),
    ("Diego Alvarez", "Alvarez Marine Repair", "Marine repair contractor"),
    ("Chloe Hansen", "Hansen Medical Office Cleaning", "Medical office cleaning"),
    ("Warren Fields", "Fields Industrial Coatings", "Industrial coatings"),
    ("Nina Roberts", "Roberts Playground Install", "Playground installation"),
    ("Trevor Blake", "Blake Insulation Crew", "Insulation contractor"),
    ("Patricia Young", "Young Fire Door Service", "Fire door contractor"),
    ("Jamal Pierce", "Pierce Lighting Retrofits", "Lighting retrofit contractor"),
    ("Rebecca Cohen", "Cohen Waterproofing", "Waterproofing contractor"),
    ("Tyler Bennett", "Bennett Garage Floors", "Epoxy flooring contractor"),
    ("Monique Davis", "Davis Commercial Movers", "Commercial moving"),
    ("Evan Richards", "Richards Concrete Cutting", "Concrete cutting contractor"),
    ("Celeste Moore", "Moore Retail Fixture Install", "Fixture installation"),
    ("Philip Adams", "Adams Drain Cleaning", "Drain cleaning contractor"),
    ("Sandra Diaz", "Diaz Roofing Consultants", "Roof inspection consultant"),
    ("Raymond Scott", "Scott Pavement Sealcoat", "Sealcoating contractor"),
]

SCENARIOS = [
    "booked_call",
    "booked_chat",
    "qualified_needs_follow_up",
    "not_qualified_small_job",
    "voicemail",
    "failed_wrong_number",
    "contacted_price_sensitive",
    "new_not_called",
    "opted_out",
    "booked_call",
    "qualified_high_value",
    "not_qualified_personal_auto",
    "failed_no_answer",
    "contacted_docs_needed",
    "qualified_later_date",
    "booked_chat",
]

SOURCE_DETAILS = [
    "Meta lead form - commercial GL",
    "Google search landing page",
    "Retargeting form - contractor insurance",
    "Referral from existing agency client",
    "LinkedIn lead gen form",
    "Website quote request",
]

JOB_NOTES = [
    "needs certificate for a municipal bid",
    "has one claim from a subcontractor injury",
    "is adding two employees this month",
    "needs additional insured wording for a landlord",
    "currently insured but shopping renewal",
    "new venture with prior industry experience",
    "asked about umbrella coverage",
    "works in multiple states",
]


def _stable_id(kind: str, index: int) -> str:
    return str(uuid5(NAMESPACE_DNS, f"{SEED_NAMESPACE}:{kind}:{index}"))


def _slug(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "." for ch in text).strip(".").replace("..", ".")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _call_transcript(first_name: str, business: str, scenario: str, job_note: str) -> str:
    if scenario in {"booked_call", "booked_chat"}:
        return "\n".join(
            [
                f"AI: Hi {first_name}, this is Ashmont Insurance following up on your commercial liability request.",
                f"{first_name}: Yes, I run {business}. We need GL coverage because the next job {job_note}.",
                "AI: Understood. Roughly how many employees and subcontractors are on site in a normal week?",
                f"{first_name}: Usually three employees and two subs. Payroll is changing, so I want someone to look at it.",
                "AI: That sounds like a fit for a licensed Ashmont advisor. I can book a 30 minute review.",
                f"{first_name}: Yes, book it. I can do the time you suggested.",
                "AI: Perfect. I will send the calendar confirmation and include the notes from this call.",
            ]
        )
    if scenario.startswith("qualified"):
        return "\n".join(
            [
                f"AI: Hi {first_name}, this is Ashmont Insurance. I saw your request for contractor GL coverage.",
                f"{first_name}: Correct. {business} is reviewing coverage because it {job_note}.",
                "AI: Do you have active operations and a target effective date in the next 30 to 60 days?",
                f"{first_name}: Yes. We are active now and need something before the next certificate request.",
                "AI: Great. You look qualified for a coverage review. Would tomorrow or later this week work?",
                f"{first_name}: Later this week is better. Text me options and I will pick one.",
            ]
        )
    if scenario.startswith("not_qualified"):
        return "\n".join(
            [
                f"AI: Hi {first_name}, this is Ashmont Insurance about your liability request.",
                f"{first_name}: I may have filled out the wrong form. I only need coverage for a tiny side job.",
                "AI: Are you operating as a business with payroll, subcontractors, or commercial certificates needed?",
                f"{first_name}: No, it is just me helping a friend for one weekend.",
                "AI: Thanks for clarifying. This may not be a fit for Ashmont's commercial GL program.",
            ]
        )
    if scenario == "voicemail":
        return f"Voicemail: Hi {first_name}, this is Ashmont Insurance calling about your commercial liability request for {business}. We will also send a text with next steps."
    if scenario == "failed_wrong_number":
        return "System: Call connected to a person who said this is the wrong number and does not know the business."
    if scenario == "failed_no_answer":
        return "System: No answer after ring timeout. No voicemail greeting was detected."
    if scenario == "opted_out":
        return f"AI: Hi {first_name}, this is Ashmont Insurance following up on your quote request.\n{first_name}: Please do not call or text this number again.\nAI: Understood. We will mark you opted out."
    return "\n".join(
        [
            f"AI: Hi {first_name}, this is Ashmont Insurance. I am following up on your commercial GL request.",
            f"{first_name}: I am interested, but I need pricing before I book anything.",
            "AI: The advisor can review operations and carriers before giving a realistic range.",
            f"{first_name}: Send me the checklist first. If it looks reasonable, I will schedule.",
        ]
    )


def _chat_messages(first_name: str, business: str, scenario: str, submitted_at: datetime) -> list[dict]:
    messages = [
        {
            "role": "assistant",
            "body": f"Hi {first_name}, this is Ashmont Insurance. I received your commercial liability request for {business}.",
            "created_at": _iso(submitted_at + timedelta(minutes=4)),
        },
    ]
    if scenario == "booked_chat":
        messages.extend(
            [
                {
                    "role": "user",
                    "body": "Yes, I can handle it by text. We need proof of GL for a contract starting next month.",
                    "created_at": _iso(submitted_at + timedelta(minutes=7)),
                },
                {
                    "role": "assistant",
                    "body": "Great. You look like a fit for a quick advisor review. Can I book you for a 30 minute consultation?",
                    "created_at": _iso(submitted_at + timedelta(minutes=9)),
                },
                {
                    "role": "user",
                    "body": "Yes. Please book the earliest afternoon slot and send the invite to my email.",
                    "created_at": _iso(submitted_at + timedelta(minutes=11)),
                },
            ]
        )
    elif scenario.startswith("qualified"):
        messages.extend(
            [
                {
                    "role": "user",
                    "body": "We are active and need a certificate soon, but I need to check my calendar first.",
                    "created_at": _iso(submitted_at + timedelta(minutes=12)),
                },
                {
                    "role": "assistant",
                    "body": "No problem. I can send a few times and note that you are looking for commercial GL with certificate needs.",
                    "created_at": _iso(submitted_at + timedelta(minutes=14)),
                },
            ]
        )
    elif scenario.startswith("not_qualified"):
        messages.append(
            {
                "role": "user",
                "body": "This is only for a one-off personal project, not a commercial business.",
                "created_at": _iso(submitted_at + timedelta(minutes=13)),
            }
        )
    elif scenario == "opted_out":
        messages.append(
            {
                "role": "user",
                "body": "STOP",
                "created_at": _iso(submitted_at + timedelta(minutes=9)),
            }
        )
    elif scenario == "new_not_called":
        messages.append(
            {
                "role": "assistant",
                "body": "I can help route this to the right commercial advisor. What is the best callback window?",
                "created_at": _iso(submitted_at + timedelta(minutes=8)),
            }
        )
    else:
        messages.extend(
            [
                {
                    "role": "user",
                    "body": "Can you text me what information you need first?",
                    "created_at": _iso(submitted_at + timedelta(minutes=15)),
                },
                {
                    "role": "assistant",
                    "body": "Yes. I will send a short checklist and keep the call follow-up open.",
                    "created_at": _iso(submitted_at + timedelta(minutes=17)),
                },
            ]
        )
    return messages


def build_demo_data(now: datetime | None = None) -> dict:
    rng = Random(20260531)
    now = now or datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    leads = []
    calls = []
    appointments = []
    sms_messages = []
    alerts = []
    ad_spend_daily = []

    appointment_index = 0
    for index, (full_name, company, business_type) in enumerate(PEOPLE):
        first_name = full_name.split()[0]
        scenario = SCENARIOS[index % len(SCENARIOS)]
        submitted_at = now - timedelta(hours=2 + (index * 7) % 180, minutes=(index * 11) % 55)
        source_detail = SOURCE_DETAILS[index % len(SOURCE_DETAILS)]
        job_note = JOB_NOTES[index % len(JOB_NOTES)]
        phone_number = f"+1202555{1000 + index:04d}"
        email = None if index in {8, 22, 39, 57} else f"{_slug(first_name)}@{_slug(company)}.example"
        speed_to_lead = None if scenario == "new_not_called" else rng.randint(22, 390)
        appointment_booked = scenario in {"booked_call", "booked_chat"}
        answered = scenario not in {"voicemail", "failed_wrong_number", "failed_no_answer", "new_not_called"}
        voicemail = scenario == "voicemail"
        opt_out = scenario == "opted_out"
        preferred_owner_key = HANDLER_KEYS[index % len(HANDLER_KEYS)]
        current_owner_key = preferred_owner_key
        fallback_used = False
        booking_outcome = None
        if appointment_booked:
            preferred_owner_key, current_owner_key, fallback_used, booking_outcome = BOOKING_OUTCOMES[appointment_index % len(BOOKING_OUTCOMES)]
        blocked_calendar_demo = not appointment_booked and scenario == "qualified_needs_follow_up" and index == 2

        if appointment_booked:
            status = "booked"
            call_status = "answered"
            sequence_status = "stopped"
        elif scenario.startswith("qualified"):
            status = "qualified"
            call_status = "answered"
            sequence_status = "active"
        elif scenario.startswith("not_qualified"):
            status = "not_qualified"
            call_status = "answered"
            sequence_status = "completed"
        elif scenario == "voicemail":
            status = "follow_up"
            call_status = "voicemail"
            sequence_status = "active"
        elif scenario.startswith("failed"):
            status = "failed"
            call_status = "failed" if scenario == "failed_wrong_number" else "no_answer"
            sequence_status = "active"
        elif scenario == "new_not_called":
            status = "new"
            call_status = "not_called"
            sequence_status = "not_started"
        elif scenario == "opted_out":
            status = "opted_out"
            call_status = "answered"
            sequence_status = "stopped"
        else:
            status = "contacted"
            call_status = "answered"
            sequence_status = "active"

        lead_id = _stable_id("lead", index)
        chat_messages = _chat_messages(first_name, company, scenario, submitted_at)
        raw_payload = {
            "seed_source": SEED_SOURCE,
            "company": company,
            "annual_revenue": rng.choice([180000, 420000, 750000, 1200000, 2400000, 5200000]),
            "employees": rng.choice([1, 2, 4, 7, 11, 18, 26]),
            "subcontractors": rng.choice([0, 1, 2, 4, 8, 15]),
            "coverage_need": rng.choice(["new policy", "renewal review", "certificate request", "bid requirement", "audit cleanup"]),
            "edge_case": scenario,
            "owner_key": current_owner_key,
            "owner_name": owners.owner_name(current_owner_key),
            "preferred_owner_key": preferred_owner_key,
            "preferred_owner_name": owners.owner_name(preferred_owner_key),
            "calendar_outcome": booking_outcome or ("both_handlers_unavailable" if blocked_calendar_demo else "not_requested"),
            "fallback_used": fallback_used,
            "chat_messages": chat_messages,
            "chat_transcript": "\n".join(f"{message['role'].title()}: {message['body']}" for message in chat_messages),
        }
        if blocked_calendar_demo:
            raw_payload["calendar_attempts"] = [
                {"owner_key": "aditya", "owner_name": "Aditya", "available": False, "status": "local_conflict"},
                {"owner_key": "archit", "owner_name": "Archit", "available": False, "status": "calendar_busy"},
            ]

        leads.append(
            {
                "id": lead_id,
                "full_name": full_name,
                "phone_number": phone_number,
                "email": email,
                "business_type": business_type,
                "source_campaign": "contractor_gl",
                "source_detail": source_detail,
                "meta_lead_id": f"ashmont-demo-{index + 1:03d}",
                "tcpa_consent": not opt_out,
                "submitted_at": _iso(submitted_at),
                "first_call_triggered_at": _iso(submitted_at + timedelta(seconds=speed_to_lead or 0)) if speed_to_lead is not None else None,
                "first_call_started_at": _iso(submitted_at + timedelta(seconds=(speed_to_lead or 0) + 40)) if speed_to_lead is not None else None,
                "speed_to_lead_seconds": speed_to_lead,
                "status": status,
                "call_status": call_status,
                "sequence_status": sequence_status,
                "sequence_stage": 0 if sequence_status == "not_started" else rng.choice([1, 2, 3]),
                "appointment_booked": appointment_booked,
                "appointment_id": None,
                "owner_key": current_owner_key,
                "owner_name": owners.owner_name(current_owner_key),
                "opt_out_sms": opt_out,
                "crm_status": rng.choice(["synced", "pending_hook", "queued", "retrying"]),
                "raw_payload": raw_payload,
                "created_at": _iso(submitted_at),
                "updated_at": _iso(submitted_at + timedelta(minutes=rng.randint(8, 240))),
            }
        )

        if scenario != "new_not_called":
            call_started = submitted_at + timedelta(seconds=(speed_to_lead or 120) + 40)
            duration = rng.randint(146, 640) if answered and not opt_out else rng.randint(18, 72)
            transcript = _call_transcript(first_name, company, scenario, job_note)
            call_id = _stable_id("call", index)
            calls.append(
                {
                    "id": call_id,
                    "lead_id": lead_id,
                    "retell_call_id": f"retell_demo_{index + 1:03d}_1",
                    "attempt_number": 1,
                    "direction": "outbound",
                    "status": "completed" if not scenario.startswith("failed") else "failed",
                    "persona": "commercial_gl",
                    "started_at": _iso(call_started),
                    "ended_at": _iso(call_started + timedelta(seconds=duration)),
                    "duration_seconds": duration,
                    "answered": answered,
                    "voicemail": voicemail,
                    "recording_url": f"https://recordings.example.test/ashmont/demo/{index + 1:03d}",
                    "transcript": transcript,
                    "transcript_json": {
                        "speaker_count": 2 if answered else 1,
                        "utterances": transcript.split("\n"),
                        "seed_source": SEED_SOURCE,
                    },
                    "summary": {
                        "qualified": scenario.startswith("qualified") or appointment_booked,
                        "appointment_requested": appointment_booked,
                        "not_qualified": scenario.startswith("not_qualified"),
                        "scenario": scenario,
                        "next_step": "booked" if appointment_booked else ("follow_up" if sequence_status == "active" else sequence_status),
                    },
                    "retell_analysis": {
                        "call_successful": answered,
                        "voicemail": voicemail,
                        "lead_sentiment": rng.choice(["positive", "neutral", "price_sensitive", "rushed"]),
                        "seed_source": SEED_SOURCE,
                    },
                    "failure_reason": "wrong_number" if scenario == "failed_wrong_number" else ("no_answer" if scenario == "failed_no_answer" else None),
                    "created_at": _iso(call_started),
                    "full_name": full_name,
                    "phone_number": phone_number,
                    "email": email,
                    "business_type": business_type,
                    "owner_key": current_owner_key,
                    "owner_name": owners.owner_name(current_owner_key),
                }
            )

            if scenario in {"voicemail", "failed_no_answer"} and index % 2 == 0:
                second_start = call_started + timedelta(hours=4)
                calls.append(
                    {
                        "id": _stable_id("call-second", index),
                        "lead_id": lead_id,
                        "retell_call_id": f"retell_demo_{index + 1:03d}_2",
                        "attempt_number": 2,
                        "direction": "outbound",
                        "status": "completed",
                        "persona": "commercial_gl",
                        "started_at": _iso(second_start),
                        "ended_at": _iso(second_start + timedelta(seconds=36)),
                        "duration_seconds": 36,
                        "answered": False,
                        "voicemail": True,
                        "recording_url": f"https://recordings.example.test/ashmont/demo/{index + 1:03d}-retry",
                        "transcript": f"Voicemail retry for {first_name}. Asked them to reply by text with a good callback time.",
                        "transcript_json": {"speaker_count": 1, "seed_source": SEED_SOURCE},
                        "summary": {"qualified": False, "retry": True, "scenario": "voicemail_retry"},
                        "retell_analysis": {"call_successful": False, "voicemail": True, "seed_source": SEED_SOURCE},
                        "failure_reason": "voicemail",
                        "created_at": _iso(second_start),
                        "full_name": full_name,
                        "phone_number": phone_number,
                        "email": email,
                        "business_type": business_type,
                        "owner_key": current_owner_key,
                        "owner_name": owners.owner_name(current_owner_key),
                    }
                )

        if appointment_booked:
            if appointment_index in {0, 1}:
                start_time = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
            else:
                days_out = 1 + (appointment_index // 4)
                hour = [10, 11, 14, 15][appointment_index % 4]
                minute = 0 if appointment_index % 2 == 0 else 30
                start_time = (now + timedelta(days=days_out)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            end_time = start_time + timedelta(minutes=30)
            appointment_id = _stable_id("appointment", index)
            appointments.append(
                {
                    "id": appointment_id,
                    "lead_id": lead_id,
                    "call_attempt_id": _stable_id("call", index) if scenario == "booked_call" else None,
                    "cal_booking_id": f"cal_demo_{index + 1:03d}",
                    "cal_event_type_id": "ashmont-demo-gl-review",
                    "owner_key": current_owner_key,
                    "owner_name": owners.owner_name(current_owner_key),
                    "status": "booked",
                    "start_time": _iso(start_time),
                    "end_time": _iso(end_time),
                    "timezone": "America/New_York",
                    "event_title": f"Ashmont GL consultation - {full_name}",
                    "meeting_url": f"https://cal.example.test/ashmont/{index + 1:03d}",
                    "invitee_email": email,
                    "lead_agreed": True,
                    "transcript_verified": True,
                    "booked_through_chat": scenario == "booked_chat",
                    "fallback_used": fallback_used,
                    "metadata": {
                        "seed_source": SEED_SOURCE,
                        "booked_through_chat": scenario == "booked_chat",
                        "owner_key": current_owner_key,
                        "owner_name": owners.owner_name(current_owner_key),
                        "preferred_owner_key": preferred_owner_key,
                        "preferred_owner_name": owners.owner_name(preferred_owner_key),
                        "fallback_used": fallback_used,
                        "calendar_outcome": booking_outcome,
                        "attempted_owners": [
                            {
                                "owner_key": preferred_owner_key,
                                "owner_name": owners.owner_name(preferred_owner_key),
                                "available": not fallback_used,
                                "status": "booked" if not fallback_used else "local_conflict",
                            },
                            {
                                "owner_key": current_owner_key,
                                "owner_name": owners.owner_name(current_owner_key),
                                "available": True,
                                "status": "booked",
                            },
                        ] if fallback_used else [
                            {
                                "owner_key": current_owner_key,
                                "owner_name": owners.owner_name(current_owner_key),
                                "available": True,
                                "status": "booked",
                            }
                        ],
                        "booking_evidence": {
                            "customer_confirmed": True,
                            "agent_recap": "Advisor consultation confirmed with explicit customer agreement.",
                        },
                    },
                    "created_at": _iso(submitted_at + timedelta(minutes=18)),
                    "full_name": full_name,
                    "phone_number": phone_number,
                    "business_type": business_type,
                }
            )
            leads[-1]["appointment_id"] = appointment_id
            appointment_index += 1

        for message_index, message in enumerate(chat_messages):
            direction = "outbound" if message["role"] == "assistant" else "inbound"
            sms_messages.append(
                {
                    "id": _stable_id("sms", index * 10 + message_index),
                    "lead_id": lead_id,
                    "direction": direction,
                    "from_number": "+16175550100" if direction == "outbound" else phone_number,
                    "to_number": phone_number if direction == "outbound" else "+16175550100",
                    "body": message["body"],
                    "status": "received" if direction == "inbound" else "sent",
                    "twilio_sid": f"SMDEMO{index + 1:03d}{message_index + 1:02d}",
                    "raw_payload": {"seed_source": SEED_SOURCE, "role": message["role"]},
                    "created_at": message["created_at"],
                }
            )

    alert_templates = [
        ("retell_call_failed", "urgent", "Retell call failed", "Provider returned a no-answer state after retry."),
        ("booking_evidence_mismatch", "urgent", "Booking claim needs review", "Analysis claimed booked, but transcript did not include explicit confirmation."),
        ("sms_unmatched", "warning", "Inbound SMS did not match a lead", "A reply arrived from a number outside the active lead table."),
        ("high_value_follow_up", "warning", "High value lead still needs appointment", "Qualified lead has revenue over $2M and no booked appointment."),
        ("crm_retry", "warning", "CRM sync retry queued", "The CRM webhook will retry after a transient timeout."),
        ("tcpa_opt_out", "urgent", "Lead opted out", "Lead replied STOP and should not receive further outreach."),
        ("calendar_reconcile", "warning", "Calendar slot needs manual check", "Cal booking exists but local confirmation is missing."),
        ("demo_seed_loaded", "info", "Demo data loaded", "Seeded Ashmont demo data is available in the dashboard."),
    ]
    for index, (alert_type, severity, title, message) in enumerate(alert_templates):
        lead = leads[(index * 7) % len(leads)]
        alerts.append(
            {
                "id": _stable_id("alert", index),
                "alert_type": alert_type,
                "severity": severity,
                "title": title,
                "message": f"{message} Lead: {lead['full_name']}.",
                "lead_id": lead["id"],
                "status": "resolved" if index in {2, 6} else "open",
                "fired_channels": ["dashboard"],
                "metadata": {"seed_source": SEED_SOURCE},
                "resolved_at": _iso(now - timedelta(hours=8)) if index in {2, 6} else None,
                "created_at": _iso(now - timedelta(hours=index * 5 + 1)),
            }
        )

    for day_offset, amount in enumerate([284.20, 412.75, 338.10, 529.40, 617.25, 451.80, 390.65, 563.95]):
        spend_date = (month_start + timedelta(days=day_offset * 3)).date().isoformat()
        ad_spend_daily.append(
            {
                "id": _stable_id("ad-spend", day_offset),
                "spend_date": spend_date,
                "campaign": "contractor_gl",
                "spend_amount": amount,
            }
        )

    return {
        "leads": leads,
        "calls": calls,
        "appointments": appointments,
        "sms_messages": sms_messages,
        "alerts": alerts,
        "ad_spend_daily": ad_spend_daily,
        "outreach_steps": outreach_steps(),
    }


def outreach_steps() -> list[dict]:
    return [
        {
            "id": _stable_id("step", 1),
            "sequence_key": "contractor_gl",
            "step_number": 1,
            "channel": "sms",
            "delay_minutes": 5,
            "template": "Hi {{name}}, this is Ashmont Insurance. We just tried to reach you about your contractor liability request. What is a good time for a quick call?",
            "active": True,
        },
        {
            "id": _stable_id("step", 2),
            "sequence_key": "contractor_gl",
            "step_number": 2,
            "channel": "voice",
            "delay_minutes": 60,
            "template": "Second AI call attempt for {{name}}.",
            "active": True,
        },
        {
            "id": _stable_id("step", 3),
            "sequence_key": "contractor_gl",
            "step_number": 3,
            "channel": "sms",
            "delay_minutes": 240,
            "template": "Hi {{name}}, following up on your contractor GL request. We can help review coverage options when you have a few minutes.",
            "active": True,
        },
    ]


def build_kpis(data: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    leads = [lead for lead in data["leads"] if datetime.fromisoformat(lead["submitted_at"]) >= month_start]
    calls = [call for call in data["calls"] if datetime.fromisoformat(call["started_at"]) >= month_start]
    appointments = [appointment for appointment in data["appointments"] if datetime.fromisoformat(appointment["created_at"]) >= month_start]
    speed_values = [lead["speed_to_lead_seconds"] for lead in leads if lead.get("speed_to_lead_seconds") is not None]
    spend_mtd = sum(float(row["spend_amount"]) for row in data["ad_spend_daily"])
    answered = [call for call in calls if call.get("answered")]
    return {
        "total_leads_mtd": len(leads),
        "appointments_mtd": len(appointments),
        "appointment_rate": round((len(appointments) / len(leads)) * 100, 1) if leads else 0,
        "avg_speed_to_lead": round(sum(speed_values) / len(speed_values)) if speed_values else 0,
        "call_connection_rate": round((len(answered) / len(calls)) * 100, 1) if calls else 0,
        "spend_mtd": round(spend_mtd, 2),
        "cpl": round(spend_mtd / len(leads), 2) if leads else 0,
    }
