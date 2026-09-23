"""Telegram user -> member resolution (relocated verbatim; data-driven links land in B4)."""
import logging

from wgcop.botconfig import GI_ID, GY_ID, JA_ID, NI_ID, TO_ID
from wgcop.members import _get_member_name, _member_to_dict, _normalise_member_name
from wgcop.store import load_data, save_data

logger = logging.getLogger(__name__)

def _match_member_UID(candidate):
    candidate_norm = str(_normalise_member_name(candidate))
    print(f"Matching candidate: {candidate_norm} and {JA_ID}")

    if candidate_norm == str(NI_ID):
        return _member_to_dict("Nicci Lopez")
    if candidate_norm == str(GI_ID):
        return _member_to_dict("Gjango Gmüseshole")
    if candidate_norm == str(GY_ID):
        return _member_to_dict("General Guysan")
    if candidate_norm == str(TO_ID):
        return _member_to_dict("Thomath Sucker")
    if candidate_norm == str(JA_ID):
        return _member_to_dict("Janidputzä")
    return None


def _resolve_member_for_user(user):
    if not user or not user.id:
        return None

    uid = user.id
    data = load_data()
    members = data.get("members") or []

    # Primary lookup: find member by stored uid field
    for member in members:
        if isinstance(member, dict) and member.get("uid") == uid:
            return member

    # Fallback: hardcoded UID map (bootstrap for members without uid stored yet)
    match = _match_member_UID(str(uid))
    if match:
        matched_name = _get_member_name(match)
        # Find the actual member in data and persist the uid
        for member in members:
            if isinstance(member, dict) and _normalise_member_name(member.get("name", "")) == _normalise_member_name(matched_name):
                member["uid"] = uid
                save_data(data)
                return member
        # Name from hardcoded map not in members list (shouldn't happen normally)
        return match

    return None
