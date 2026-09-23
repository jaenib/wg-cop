"""Member helpers, balance maths, renames and chore entries (relocated verbatim)."""
import logging
from datetime import datetime

import pytz

from wgcop.store import save_data

logger = logging.getLogger(__name__)

def _normalise_member_name(name: str) -> str:
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
    # Create balance dict using member names (strings)
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


def _rename_member_in_data(data: dict, old_name: str, new_name: str) -> None:
    """Atomically rename a member across all data structures."""
    old_norm = _normalise_member_name(old_name)

    for m in data.get("members") or []:
        if isinstance(m, dict) and _normalise_member_name(m.get("name", "")) == old_norm:
            m["name"] = new_name

    chores = data.setdefault("chores", {})
    if old_name in chores:
        chores[new_name] = chores.pop(old_name)

    penalties = data.setdefault("penalties", {})
    if old_name in penalties:
        penalties[new_name] = penalties.pop(old_name)

    violators = data.setdefault("last_week_violators", {})
    old_v_key = _normalise_member_name(old_name)
    new_v_key = _normalise_member_name(new_name)
    if old_v_key in violators:
        violators[new_v_key] = violators.pop(old_v_key)

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


def add_chore_entry(data, member: str, points: int, description: str | None = None):
    entry = {
        "timestamp": datetime.now(pytz.timezone("Europe/Berlin")).isoformat(),
        "member": member,
        "points": points,
        "description": description or "",
    }

    data.setdefault("chore_log", []).append(entry)

    # keep your existing totals working
    data.setdefault("chores", {})
    data["chores"].setdefault(member, 0)
    data["chores"][member] += points

    save_data(data)
