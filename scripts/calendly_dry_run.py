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
from services import calendly


def parse_aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("Use an ISO datetime with timezone, e.g. 2026-06-08T15:00:00-04:00")
    return parsed.astimezone(timezone.utc)


def summarize_error(result: dict[str, Any]) -> str:
    status = result.get("status_code")
    error = result.get("error") or "No error body returned."
    return f"status={status} error={error}" if status else str(error)


async def list_event_types() -> int:
    settings = get_settings()
    if not settings.calendly_api_key:
        print("CALENDLY_API_KEY is not configured.")
        return 2

    headers = {"Authorization": f"Bearer {settings.calendly_api_key}"}
    base_url = settings.calendly_api_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=20) as client:
        user_response = await client.get(f"{base_url}/users/me", headers=headers)
        print(f"users_me_status={user_response.status_code}")
        if user_response.status_code >= 400:
            print(user_response.text)
            return 1

        user = user_response.json().get("resource", {})
        user_uri = user.get("uri")
        org_uri = user.get("current_organization")
        print(f"user_name={user.get('name')}")
        print(f"user_email={user.get('email')}")
        print(f"user_uri={user_uri}")
        print(f"org_uri={org_uri}")

        queries = []
        if user_uri:
            queries.append(("user", {"active": "true", "user": user_uri}))
        if org_uri:
            queries.append(("organization", {"active": "true", "organization": org_uri}))

        seen: set[str] = set()
        for label, params in queries:
            response = await client.get(f"{base_url}/event_types", params=params, headers=headers)
            print(f"event_types_by_{label}_status={response.status_code}")
            if response.status_code >= 400:
                print(response.text)
                continue
            collection = response.json().get("collection", [])
            print(f"event_types_by_{label}_count={len(collection)}")
            for event_type in collection:
                uri = str(event_type.get("uri") or "")
                if uri in seen:
                    continue
                seen.add(uri)
                profile = event_type.get("profile") or {}
                print(
                    "EVENT_TYPE|"
                    f"name={event_type.get('name')}|"
                    f"slug={event_type.get('slug')}|"
                    f"duration={event_type.get('duration')}|"
                    f"active={event_type.get('active')}|"
                    f"owner={profile.get('owner')}|"
                    f"uri={uri}"
                )
    return 0


async def check_or_book(args: argparse.Namespace) -> int:
    owner_key = owners.normalize_owner_key(args.owner, strict=True)
    event_type_uri = calendly.event_type_uri_for_owner(owner_key)
    if not event_type_uri:
        env_name = f"CALENDLY_{owner_key.upper()}_EVENT_TYPE_URI"
        print(f"{env_name} is not configured.")
        return 2

    start_time = parse_aware_datetime(args.start)
    end_time = parse_aware_datetime(args.end) if args.end else start_time + timedelta(minutes=args.duration_minutes)
    availability = await calendly.get_availability(start_time, end_time, owner_key)
    print(f"owner={availability.get('owner_name')} ({owner_key})")
    print(f"event_type_uri={event_type_uri}")
    print(f"availability_ok={availability.get('ok')}")
    print(f"available={availability.get('available')}")
    if not availability.get("ok"):
        print(summarize_error(availability))
        return 1

    for slot in availability.get("slots", [])[:10]:
        print(f"SLOT|start={slot.get('start_time') or slot.get('start')}|status={slot.get('status')}")

    if not args.book:
        print("No booking created. Re-run with --book to create the Calendly invitee.")
        return 0
    if not availability.get("available"):
        print("No booking created because the exact requested start time is unavailable.")
        return 1
    if not args.attendee_name or not args.attendee_email:
        print("--attendee-name and --attendee-email are required with --book.")
        return 2

    booking = {
        "lead_id": args.lead_id,
        "owner_key": owner_key,
        "start_time": start_time,
        "end_time": end_time,
        "attendee_name": args.attendee_name,
        "attendee_email": args.attendee_email,
        "business_type": args.business_type,
        "notes": args.notes or "Calendly dry run booking from Ashmont backend.",
    }
    result = await calendly.book_meeting(booking)
    print(f"booking_ok={result.get('ok')}")
    if not result.get("ok"):
        print(summarize_error(result))
        return 1
    print(f"booking_id={result.get('booking_id')}")
    print(f"meeting_url={result.get('meeting_url')}")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="Check or create a real Calendly booking through Ashmont settings.")
    parser.add_argument("--list-event-types", action="store_true", help="List visible Calendly event types and exit.")
    parser.add_argument("--owner", choices=owners.OWNER_KEYS, default="aditya")
    parser.add_argument("--start", help="ISO datetime with timezone, e.g. 2026-06-08T15:00:00-04:00")
    parser.add_argument("--end", help="Optional ISO end datetime with timezone.")
    parser.add_argument("--duration-minutes", type=int, default=30)
    parser.add_argument("--attendee-name")
    parser.add_argument("--attendee-email")
    parser.add_argument("--business-type", default="Contractor")
    parser.add_argument("--notes")
    parser.add_argument("--lead-id", default="calendly-dry-run")
    parser.add_argument("--book", action="store_true", help="Create the real Calendly invitee after availability passes.")
    args = parser.parse_args()

    if args.list_event_types:
        return await list_event_types()
    if not args.start:
        parser.error("--start is required unless --list-event-types is used.")
    return await check_or_book(args)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
