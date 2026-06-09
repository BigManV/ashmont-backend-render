from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import owners
from config import get_settings
from services import calcom


EVENT_TYPES_API_VERSION = "2024-06-14"


def parse_aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("Use an ISO datetime with timezone, e.g. 2026-06-08T13:00:00-04:00")
    return parsed.astimezone(timezone.utc)


def summarize_error(result: dict[str, Any]) -> str:
    status = result.get("status_code")
    error = result.get("error") or "No error body returned."
    return f"status={status} error={error}" if status else str(error)


async def list_event_types() -> int:
    settings = get_settings()
    if not settings.cal_api_key:
        print("CAL_API_KEY is not configured.")
        return 2

    headers = {
        "Authorization": f"Bearer {settings.cal_api_key}",
        "cal-api-version": EVENT_TYPES_API_VERSION,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{settings.cal_api_base_url.rstrip('/')}/event-types", headers=headers)
    print(f"event_types_status={response.status_code}")
    if response.status_code >= 400:
        print(response.text)
        return 1

    data = response.json()
    collection = data.get("data") if isinstance(data, dict) else []
    if not isinstance(collection, list):
        print("Cal.com returned an unexpected event type response.")
        return 1
    print(f"event_types_count={len(collection)}")
    for event_type in collection:
        users = event_type.get("users") if isinstance(event_type, dict) else []
        user_names = ", ".join(str(user.get("name")) for user in users if isinstance(user, dict))
        print(
            "EVENT_TYPE|"
            f"id={event_type.get('id')}|"
            f"title={event_type.get('title')}|"
            f"slug={event_type.get('slug')}|"
            f"length={event_type.get('lengthInMinutes')}|"
            f"ownerId={event_type.get('ownerId')}|"
            f"users={user_names}"
        )
    return 0


async def check_or_book(args: argparse.Namespace) -> int:
    owner_key = owners.normalize_owner_key(args.owner, strict=True)
    settings = get_settings()
    event_type_id = owners.event_type_id_for_owner(settings, owner_key)
    if not event_type_id:
        env_name = f"CAL_{owner_key.upper()}_EVENT_TYPE_ID"
        print(f"{env_name} or CAL_EVENT_TYPE_ID is not configured.")
        return 2

    start_time = parse_aware_datetime(args.start)
    end_time = parse_aware_datetime(args.end) if args.end else start_time + timedelta(minutes=args.duration_minutes)
    availability = await calcom.get_availability(start_time, end_time, owner_key)
    print(f"owner={availability.get('owner_name')} ({owner_key})")
    print(f"event_type_id={event_type_id}")
    print(f"availability_ok={availability.get('ok')}")
    print(f"available={availability.get('available')}")
    if not availability.get("ok"):
        print(summarize_error(availability))
        return 1

    slots = availability.get("slots")
    if isinstance(slots, dict):
        shown = 0
        for day, day_slots in slots.items():
            if not isinstance(day_slots, list):
                continue
            for slot in day_slots:
                print(f"SLOT|day={day}|start={slot.get('start') if isinstance(slot, dict) else slot}")
                shown += 1
                if shown >= 10:
                    break
            if shown >= 10:
                break
    elif isinstance(slots, list):
        for slot in slots[:10]:
            print(f"SLOT|start={slot.get('start') if isinstance(slot, dict) else slot}")

    if not args.book:
        print("No booking created. Re-run with --book to create the Cal.com booking.")
        return 0
    if not availability.get("available"):
        print("No booking created because the exact requested start time is unavailable.")
        return 1
    if not args.attendee_name:
        print("--attendee-name is required with --book.")
        return 2

    booking = {
        "lead_id": args.lead_id,
        "owner_key": owner_key,
        "start_time": start_time,
        "end_time": end_time,
        "attendee_name": args.attendee_name,
        "attendee_email": args.attendee_email,
        "business_type": args.business_type,
        "notes": args.notes or "Cal.com dry run booking from Ashmont backend.",
    }
    result = await calcom.book_meeting(booking)
    print(f"booking_ok={result.get('ok')}")
    if not result.get("ok"):
        print(summarize_error(result))
        return 1
    print(f"booking_id={result.get('booking_id')}")
    print(f"meeting_url={result.get('meeting_url')}")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="Check or create a real Cal.com booking through Ashmont settings.")
    parser.add_argument("--list-event-types", action="store_true", help="List visible Cal.com event types and exit.")
    parser.add_argument("--owner", choices=owners.OWNER_KEYS, default="aditya")
    parser.add_argument("--start", help="ISO datetime with timezone, e.g. 2026-06-08T13:00:00-04:00")
    parser.add_argument("--end", help="Optional ISO end datetime with timezone.")
    parser.add_argument("--duration-minutes", type=int, default=30)
    parser.add_argument("--attendee-name")
    parser.add_argument("--attendee-email")
    parser.add_argument("--business-type", default="Contractor")
    parser.add_argument("--notes")
    parser.add_argument("--lead-id", default="calcom-dry-run")
    parser.add_argument("--book", action="store_true", help="Create the real Cal.com booking after availability passes.")
    args = parser.parse_args()

    if args.list_event_types:
        return await list_event_types()
    if not args.start:
        parser.error("--start is required unless --list-event-types is used.")
    return await check_or_book(args)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
