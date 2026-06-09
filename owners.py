from __future__ import annotations

from typing import Mapping


OWNER_KEYS = ("aditya", "archit")
OWNER_NAMES = {
    "aditya": "Aditya",
    "archit": "Archit",
}


def normalize_owner_key(value: str | None, default: str = "aditya", strict: bool = False) -> str:
    key = str(value or default or "aditya").strip().lower()
    if key in OWNER_KEYS:
        return key
    if strict:
        raise ValueError(f"Unknown owner_key: {value}")
    return normalize_owner_key(default or "aditya")


def owner_name(owner_key: str | None) -> str:
    return OWNER_NAMES[normalize_owner_key(owner_key)]


def other_owner_key(owner_key: str | None) -> str:
    key = normalize_owner_key(owner_key)
    return "archit" if key == "aditya" else "aditya"


def owner_order(preferred_owner_key: str | None) -> list[str]:
    preferred = normalize_owner_key(preferred_owner_key)
    return [preferred, other_owner_key(preferred)]


def choose_balanced_owner_key(counts: Mapping[str, int] | None = None) -> str:
    normalized = {key: int((counts or {}).get(key, 0) or 0) for key in OWNER_KEYS}
    return min(OWNER_KEYS, key=lambda key: (normalized[key], OWNER_KEYS.index(key)))


def with_owner_name(row: dict | None) -> dict | None:
    if not row:
        return row
    next_row = dict(row)
    next_row["owner_key"] = normalize_owner_key(next_row.get("owner_key"))
    next_row["owner_name"] = owner_name(next_row["owner_key"])
    return next_row


def with_owner_names(rows: list[dict]) -> list[dict]:
    return [with_owner_name(row) for row in rows]


def event_type_id_for_owner(settings, owner_key: str | None) -> str:
    key = normalize_owner_key(owner_key)
    per_owner = getattr(settings, f"cal_{key}_event_type_id", "") or ""
    return per_owner or settings.cal_event_type_id
