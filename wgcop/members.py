"""Member helpers, balance maths, renames and chore entries.

These are pure functions over a home dict (or the legacy data dict, which has
the same ``members`` / ``expenses`` / ``chores`` keys). Nothing here persists
anything; callers save through the store.
"""
import logging
from datetime import datetime

import pytz

logger = logging.getLogger(__name__)


def _normalise_member_name(name) -> str:
    """Normalize a member name (string or object) to lowercase."""
    if isinstance(name, dict):
        name = name.get("name", "")
    return name.strip().casefold() if name else ""


def _get_member_name(member):
    """Get the display name from a member (string or object)."""
    if isinstance(member, dict):
        return member.get("name", "")
    return member


def _get_member_status(member):
    """Get the status from a member object, default to 'active'."""
    if isinstance(member, dict):
        return member.get("status", "active")
    return "active"


def _is_retired(member) -> bool:
    return _get_member_status(member) == "retired"


def active_members(data) -> list:
    """Members who still take part: everyone except retired ones (whose records stay frozen)."""
    return [m for m in (data.get("members", []) or []) if not _is_retired(m)]


def member_stats(data, member) -> dict:
    """One member's bookkeeping totals: expenses paid and their sum, chore points, balance."""
    name = _get_member_name(member)
    norm = _normalise_member_name(name)
    paid = [e for e in (data.get("expenses", []) or []) if _normalise_member_name(e.get("payer")) == norm]
    amount_paid = 0.0
    for expense in paid:
        try:
            amount_paid += float(expense.get("amount", 0) or 0)
        except (TypeError, ValueError):
            pass
    points = sum(
        int(p or 0) for raw, p in (data.get("chores", {}) or {}).items() if _normalise_member_name(raw) == norm
    )
    return {
        "expenses_paid": len(paid),
        "amount_paid": round(amount_paid, 2),
        "chore_points": points,
        "balance": round(calculate_balances(data).get(name, 0.0), 2),
    }


def _member_to_dict(member_name: str):
    """Convert a member name to a member object."""
    return {"name": member_name, "status": "active"}


def _match_member_name(members, candidate):
    """Match a candidate name against a list of members (strings or objects)."""
    candidate_norm = _normalise_member_name(candidate)
    for member in members:
        if _normalise_member_name(member) == candidate_norm:
            return member
    return None


def calculate_balances(data):
    members = data.get("members", []) or []
    member_names = [_get_member_name(m) for m in members]
    balances = {name: 0.0 for name in member_names}

    for expense in data.get("expenses", []) or []:
        amount = float(expense.get("amount", 0.0) or 0.0)
        payer = _match_member_name(members, expense.get("payer"))
        payer_name = _get_member_name(payer) if payer else None

        split_with = []
        for participant in expense.get("split_with", []) or []:
            matched = _match_member_name(members, participant)
            if matched:
                split_with.append(matched)

        if not split_with:
            continue

        share = amount / len(split_with)

        if payer_name:
            balances[payer_name] = balances.get(payer_name, 0.0) + amount

        for participant in split_with:
            participant_name = _get_member_name(participant)
            balances[participant_name] = balances.get(participant_name, 0.0) - share

    return balances


def chore_points_by_member(data) -> dict:
    """Chore totals keyed by the member's canonical display name (unknown and retired names dropped)."""
    members = active_members(data)
    totals = {}
    for raw_name, points in (data.get("chores", {}) or {}).items():
        matched = _match_member_name(members, raw_name)
        if matched:
            totals[_get_member_name(matched)] = totals.get(_get_member_name(matched), 0) + int(points or 0)
    return totals


def _rename_member_in_data(data: dict, old_name: str, new_name: str) -> None:
    """Atomically rename a member across all data structures."""
    old_norm = _normalise_member_name(old_name)

    for m in data.get("members") or []:
        if isinstance(m, dict) and _normalise_member_name(m.get("name", "")) == old_norm:
            m["name"] = new_name

    chores = data.setdefault("chores", {})
    for key in [k for k in chores if _normalise_member_name(k) == old_norm]:
        chores[new_name] = chores.get(new_name, 0) + chores.pop(key)

    penalties = data.setdefault("penalties", {})
    for key in [k for k in penalties if _normalise_member_name(k) == old_norm]:
        penalties[new_name] = penalties.get(new_name, 0) + penalties.pop(key)

    violators = data.setdefault("last_week_violators", {})
    new_v_key = _normalise_member_name(new_name)
    if old_norm in violators:
        violators[new_v_key] = violators.pop(old_norm)

    # join_requests reference members by mid, nothing to rename there.
    for entry in data.get("chore_log") or []:
        if _normalise_member_name(entry.get("member", "")) == old_norm:
            entry["member"] = new_name

    for expense in data.get("expenses") or []:
        if _normalise_member_name(expense.get("payer", "")) == old_norm:
            expense["payer"] = new_name
        expense["split_with"] = [
            new_name if _normalise_member_name(p) == old_norm else p
            for p in expense.get("split_with") or []
        ]


def add_chore_entry(data, member: str, points: int, description: str | None = None, tz=None):
    """Append a chore log entry and bump the member's total. Caller persists."""
    entry = {
        "timestamp": datetime.now(tz or pytz.timezone("Europe/Berlin")).isoformat(),
        "member": member,
        "points": points,
        "description": description or "",
    }
    data.setdefault("chore_log", []).append(entry)
    data.setdefault("chores", {})
    data["chores"].setdefault(member, 0)
    data["chores"][member] += points
    return entry
