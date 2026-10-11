"""Which home does an update belong to, who is asking, and what may they do.

Resolution rules
----------------
* Group / supergroup chat: the home registered for that chat id. Callers must
  have access (linked member or a role holder); others are told to ``/join``.
* Private chat: the caller's single home; with several homes the stored
  selection (``Settings -> Switch Home``); otherwise a picker is shown.
* Anonymous senders (channel posts, anonymous group admins, bots) are never
  identities.

Conversation drafts are kept in ``user_data["flow:<chat_id>"]`` so the same
person can have independent drafts in two chats, and every draft remembers the
home it was started for. Switching home in a DM clears that chat's draft.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from wgcop.store import (
    StoreError,
    get_store,
    has_access,
    is_admin,
    is_user_id,
    member_by_mid,
    member_by_name,
    member_for_uid,
    new_member,
    normalise_name,
)

GROUP_TYPES = ("group", "supergroup")
GROUP_MEMBER_STATUSES = ("creator", "administrator", "member", "restricted")
GROUP_ADMIN_STATUSES = ("creator", "administrator")
CB_HOME_SELECT = "home:select:"
DEFAULT_TZ = "Europe/Berlin"


class HomeError(RuntimeError):
    """A user-facing refusal (message is safe to show)."""


@dataclass
class Caller:
    uid: int
    name: str
    username: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ identity
def caller_from_update(update) -> Caller | None:
    user = getattr(update, "effective_user", None)
    if user is None or getattr(user, "is_bot", False) or not is_user_id(getattr(user, "id", None)):
        return None
    message = getattr(update, "effective_message", None)
    if message is not None and getattr(message, "sender_chat", None):
        return None  # anonymous admin / channel post: no personal identity
    name = (getattr(user, "full_name", None) or getattr(user, "first_name", None) or "").strip()
    return Caller(uid=user.id, name=name or f"user{user.id}", username=getattr(user, "username", None))


def is_group_chat(chat) -> bool:
    return chat is not None and getattr(chat, "type", None) in GROUP_TYPES


async def group_member_status(bot, chat_id, uid) -> str | None:
    try:
        member = await bot.get_chat_member(chat_id, uid)
    except TelegramError:
        return None
    except Exception:  # network hiccups must not grant access
        return None
    return getattr(member, "status", None)


async def is_group_member(bot, chat_id, uid) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, uid)
    except Exception:
        return False
    status = getattr(member, "status", None)
    if status == "restricted":
        return getattr(member, "is_member", False) is True
    return status in ("creator", "administrator", "member")


async def is_group_admin(bot, chat_id, uid) -> bool:
    return (await group_member_status(bot, chat_id, uid)) in GROUP_ADMIN_STATUSES


# ----------------------------------------------------------------- resolution
@dataclass
class Resolution:
    home: dict | None = None
    reason: str | None = None       # None | anonymous | unlinked_group | not_member | no_homes | choose
    choices: list | None = None
    caller: Caller | None = None


def resolve_home(update, store=None) -> Resolution:
    store = store or get_store()
    caller = caller_from_update(update)
    if caller is None:
        return Resolution(reason="anonymous")
    chat = getattr(update, "effective_chat", None)
    if is_group_chat(chat):
        home = store.home_for_group(chat.id)
        if home is None:
            return Resolution(reason="unlinked_group", caller=caller)
        if not has_access(home, caller.uid):
            return Resolution(reason="not_member", caller=caller, home=None)
        return Resolution(home=home, caller=caller)
    homes = store.homes_for_user(caller.uid)
    if not homes:
        return Resolution(reason="no_homes", caller=caller)
    if len(homes) == 1:
        return Resolution(home=homes[0], caller=caller)
    selected = store.selected_home(caller.uid)
    if selected is not None:
        return Resolution(home=selected, caller=caller)
    return Resolution(reason="choose", choices=homes, caller=caller)


REASON_TEXT = {
    "anonymous": "I can only work with a personal Telegram account, not anonymous or channel senders.",
    "unlinked_group": "This group has no home yet. A group administrator can create one with /newhome <name>.",
    "not_member": "You are not a member of this home yet. Send /join here to get set up.",
    "no_homes": (
        "You are not in any home yet. Add me to your household's Telegram group and run "
        "/newhome <name> there, or send /join in a group that already has a home."
    ),
}


def build_home_picker(homes) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(h["name"], callback_data=f"{CB_HOME_SELECT}{h['id']}")]
        for h in sorted(homes, key=lambda h: h["name"].casefold())
    ]
    return InlineKeyboardMarkup(rows)


async def require_home(update, context, store=None, *, reply=True) -> dict | None:
    """Resolve the home for this update or explain to the user why not (returns None)."""
    res = resolve_home(update, store)
    if res.home is not None:
        return res.home
    if not reply:
        return None
    message = getattr(update, "effective_message", None)
    if res.reason == "choose":
        if message is not None:
            await message.reply_text(
                "You belong to several homes. Which one do you mean?",
                reply_markup=build_home_picker(res.choices),
            )
        return None
    text = REASON_TEXT.get(res.reason, "I cannot tell which home you mean.")
    if message is not None:
        await message.reply_text(text)
    return None


# -------------------------------------------------------------------- flows
def flow_key(chat_id) -> str:
    return f"flow:{chat_id}"


def flow_state(context, chat_id) -> dict:
    return context.user_data.setdefault(flow_key(chat_id), {})


def start_flow(context, chat_id, home) -> dict:
    """Begin a fresh draft in this chat, bound to ``home``."""
    context.user_data[flow_key(chat_id)] = {"home_id": home["id"], "chat_id": chat_id}
    return context.user_data[flow_key(chat_id)]


def end_flow(context, chat_id) -> None:
    context.user_data.pop(flow_key(chat_id), None)


def clear_flows(context) -> None:
    for key in [k for k in context.user_data if isinstance(k, str) and k.startswith("flow:")]:
        context.user_data.pop(key, None)


def flow_home(context, chat_id, uid, store=None) -> dict | None:
    """The home a running draft belongs to, if the caller still has access."""
    flow = context.user_data.get(flow_key(chat_id)) or {}
    hid = flow.get("home_id")
    if not hid:
        return None
    store = store or get_store()
    try:
        home = store.get_home(hid)
    except StoreError:
        return None
    return home if has_access(home, uid) else None


def owns_keyboard(query, flow) -> bool:
    """True if the tapped inline keyboard is the one this draft sent."""
    expected = flow.get("kb_msg")
    actual = getattr(getattr(query, "message", None), "message_id", None)
    return expected is not None and actual is not None and expected == actual


# -------------------------------------------------------------------- roles
def require_admin(home, uid) -> None:
    if not is_admin(home, uid):
        raise HomeError("Only a home administrator can do that.")


def add_admin(home, uid) -> None:
    if not is_user_id(uid):
        raise HomeError("That is not a valid Telegram user id.")
    admins = home["roles"]["admins"]
    if uid not in admins:
        admins.append(uid)


def remove_admin(home, uid) -> None:
    admins = home["roles"]["admins"]
    if uid not in admins:
        raise HomeError("That user is not an administrator.")
    if len(admins) == 1:
        raise HomeError("A home must keep at least one administrator. Add another admin first.")
    admins.remove(uid)


def set_single_role(home, role, uid) -> None:
    if role not in ("chronicler", "handler"):
        raise HomeError(f"Unknown role {role}.")
    if uid is not None and not is_user_id(uid):
        raise HomeError("That is not a valid Telegram user id.")
    home["roles"][role] = uid


def role_holders(home) -> dict:
    """uid -> list of role names, for display."""
    out = {}
    for uid in home["roles"]["admins"]:
        out.setdefault(uid, []).append("admin")
    for role in ("chronicler", "handler"):
        uid = home["roles"].get(role)
        if uid:
            out.setdefault(uid, []).append(role)
    return out


def display_name_for_uid(home, uid) -> str:
    member = member_for_uid(home, uid)
    if member:
        return member["name"]
    return f"user {uid}"


# ------------------------------------------------------------------ joining
def link_member(home, mid, uid) -> dict:
    member = member_by_mid(home, mid)
    if member is None:
        raise HomeError("That member does not exist.")
    if member.get("uid") not in (None, uid):
        raise HomeError(f"{member['name']} is already linked to another Telegram account.")
    other = member_for_uid(home, uid)
    if other is not None and other is not member:
        raise HomeError(f"You are already linked to {other['name']}.")
    member["uid"] = uid
    home["join_requests"].pop(str(uid), None)
    return member


def unlink_member(home, mid) -> dict:
    member = member_by_mid(home, mid)
    if member is None:
        raise HomeError("That member does not exist.")
    member.pop("uid", None)
    return member


def _unique_member_name(home, wanted: str) -> str:
    base = (wanted or "").strip() or "Member"
    name, n = base, 2
    while member_by_name(home, name) is not None:
        name = f"{base} {n}"
        n += 1
    return name


def join_as_new_member(home, caller: Caller) -> dict:
    """Create and link a member for the caller. Name collisions get a suffix."""
    existing = member_for_uid(home, caller.uid)
    if existing is not None:
        raise HomeError(f"You are already linked to {existing['name']}.")
    member = new_member(home, _unique_member_name(home, caller.name), uid=caller.uid)
    home["members"].append(member)
    home["join_requests"].pop(str(caller.uid), None)
    return member


def request_claim(home, caller: Caller, mid: str) -> dict:
    """Ask to be linked to an existing, unlinked member; an admin must approve."""
    if member_for_uid(home, caller.uid) is not None:
        raise HomeError("You are already linked to a member of this home.")
    member = member_by_mid(home, mid)
    if member is None:
        raise HomeError("That member does not exist.")
    if member.get("uid"):
        raise HomeError(f"{member['name']} is already linked to a Telegram account.")
    home["join_requests"][str(caller.uid)] = {
        "mid": mid,
        "display": caller.name,
        "username": caller.username,
        "requested": _now_iso(),
    }
    return member


def approve_claim(home, uid) -> dict:
    request = home["join_requests"].get(str(uid))
    if request is None:
        raise HomeError("There is no pending request for that user.")
    return link_member(home, request["mid"], uid)


def deny_claim(home, uid) -> None:
    if home["join_requests"].pop(str(uid), None) is None:
        raise HomeError("There is no pending request for that user.")


def unclaimed_members(home) -> list[dict]:
    return [m for m in home["members"] if not m.get("uid")]


def member_has_history(home, member) -> bool:
    name = normalise_name(member)
    for table in (home["chores"], home["penalties"]):
        # Legacy keys were free-text input, so compare them normalised like every other record.
        if any(value and normalise_name(key) == name for key, value in table.items()):
            return True
    for entry in home["chore_log"]:
        if normalise_name(entry.get("member")) == name:
            return True
    for expense in home["expenses"]:
        if normalise_name(expense.get("payer")) == name:
            return True
        if any(normalise_name(p) == name for p in expense.get("split_with") or []):
            return True
    return False


def remove_member(home, mid) -> dict:
    """Delete a member record; refused when bookkeeping references it (unlink instead)."""
    member = member_by_mid(home, mid)
    if member is None:
        raise HomeError("That member does not exist.")
    if member_has_history(home, member):
        raise HomeError(
            f"{member['name']} appears in expenses, chores or penalties. "
            "Unlink or set vacating instead of removing, so the books stay consistent."
        )
    home["members"].remove(member)
    return member


# ----------------------------------------------------------------- settings
def home_tz(home):
    try:
        return pytz.timezone((home.get("settings") or {}).get("timezone") or DEFAULT_TZ)
    except pytz.UnknownTimeZoneError:
        return pytz.timezone(DEFAULT_TZ)


def home_currency(home) -> str:
    return (home.get("settings") or {}).get("currency") or "CHF"


def new_invite_token() -> str:
    return secrets.token_urlsafe(6)
