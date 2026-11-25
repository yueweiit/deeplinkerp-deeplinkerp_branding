"""Shared OA archive refresh policy. No database or application dependencies."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

VERSION = 1
FULL_INTERVAL = timedelta(hours=24)
OVERLAP = timedelta(minutes=10)


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def utc_now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def fingerprint(row):
    # An archive timestamp alone is not a business change. Status, result,
    # tasks, forms and tombstones remain part of the comparison.
    return digest({key: value for key, value in row.items() if key != "updated_at"})


def decode(value):
    try:
        state = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return {}
    return state if isinstance(state, dict) and state.get("version") == VERSION else {}


def plan(state, scope, *, now=None):
    """The caller persists this plan for all pages of a single round."""
    now = now or utc_now()
    state = decode(state)
    if state.get("scope") != scope:
        state = {}
    watermark, reconciled = timestamp(state.get("watermark")), timestamp(state.get("reconciled_at"))
    full = (not watermark or not reconciled or reconciled > now or watermark > now
        or now - reconciled >= FULL_INTERVAL)
    return {"mode": "full" if full else "incremental",
        "updated_after": None if full else (watermark - OVERLAP).isoformat()}


def completed(state, scope, round_plan, sync_until):
    """Only call after the final page and its business writes have succeeded."""
    state = decode(state)
    return {"version": VERSION, "scope": scope, "watermark": sync_until,
        "reconciled_at": sync_until if round_plan["mode"] == "full" else state.get("reconciled_at")}
