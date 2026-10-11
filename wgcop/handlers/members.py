"""Member management (admin: add, retire, remove), own profile, username change and vacation status."""
import difflib
import logging

from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.formatting import format_currency, format_signed_currency
from wgcop.handlers.common import admin_home, cancel, continue_admin_flow, continue_flow
from wgcop.homes import (
    HomeError,
    end_flow,
    home_currency,
    member_has_history,
    remove_member,
    require_home,
    retire_member,
    start_flow,
)
from wgcop.keyboards import (
    CHANGE_USERNAME_NEW,
    MANAGE_MEMBER,
    MANAGE_MEMBER_ACTION,
    MANAGE_MEMBER_ADD,
    get_cancel_keyboard,
    get_main_keyboard,
    get_manage_self_keyboard,
)
from wgcop.members import _match_member_name, _rename_member_in_data, member_stats
from wgcop.store import get_store, member_by_mid, member_by_name, member_for_uid, new_member

logger = logging.getLogger(__name__)

MAX_NAME_LEN = 40
BTN_MEMBERS_BACK = "Back"
BTN_MEMBERS_ADD = "Add member"
RETIRED_SUFFIX = " (retired)"
CLOSE_MATCH = 0.8  # difflib ratio from which a typed name counts as "probably this member"


def _member_summary(home) -> str:
    parts = []
    for m in home["members"]:
        flags = []
        if m.get("status") == "retired":
            flags.append("retired")
        else:
            if m.get("status") == "vacating":
                flags.append("vacating")
            flags.append("linked" if m.get("uid") else "not linked")
        parts.append(f"{m['name']} ({', '.join(flags)})")
    return ", ".join(parts)


def _stats_line(stats, currency) -> str:
    return (
        f"{stats['expenses_paid']} expense(s) paid ({format_currency(stats['amount_paid'], currency)}), "
        f"{stats['chore_points']} chore point(s), balance {format_signed_currency(stats['balance'], currency)}"
    )


def farewell_text(home, member, stats) -> str:
    currency = home_currency(home)
    balance = stats["balance"]
    if balance > 0.004:
        settle = "the house still owes you this"
    elif balance < -0.004:
        settle = "you still owe the house this"
    else:
        settle = "all square"
    return (
        f"Farewell from '{home['name']}' \U0001F44B\n\n"
        "An administrator has retired you from the household books, so your record is frozen as it stands:\n"
        f"  \u2022 {stats['expenses_paid']} expense(s) paid, {format_currency(stats['amount_paid'], currency)} in total\n"
        f"  \u2022 {stats['chore_points']} chore point(s)\n"
        f"  \u2022 final balance: {format_signed_currency(balance, currency)} ({settle})\n\n"
        "Thank you for your bookkeeping and for keeping the house running. "
        "Any open balance is settled between you and the house outside the bot. All the best!"
    )


# ------------------------------------------------------------ admin: members
def _member_label(member) -> str:
    return member["name"] + (RETIRED_SUFFIX if member.get("status") == "retired" else "")


def _member_from_label(home, text):
    """The member a tapped or typed label names (exact, case-insensitive; the '(retired)' suffix is optional)."""
    if text.casefold().endswith(RETIRED_SUFFIX):
        text = text[: -len(RETIRED_SUFFIX)]
    return member_by_name(home, text.strip())


def _closest_member(home, text):
    """An existing member whose name a typed name probably meant (typo guard), or None."""
    by_fold = {m["name"].casefold(): m for m in home["members"]}
    hits = difflib.get_close_matches(text.casefold(), list(by_fold), n=1, cutoff=CLOSE_MATCH)
    return by_fold[hits[0]] if hits else None


def _members_keyboard(home):
    rows = [[KeyboardButton(_member_label(m))] for m in home["members"]]
    rows.append([KeyboardButton(BTN_MEMBERS_ADD), KeyboardButton(BTN_MEMBERS_BACK)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def _show_member_list(message, home) -> int:
    if home["members"]:
        txt = (
            f"Current members: {_member_summary(home)}\n\n"
            f"Tap a member to manage them (retire or remove), or '{BTN_MEMBERS_ADD}' to add someone new."
        )
    else:
        txt = f"No members yet. Tap '{BTN_MEMBERS_ADD}' to add someone."
    await message.reply_text(txt, reply_markup=_members_keyboard(home))
    return MANAGE_MEMBER


async def manage_members(update: Update, context: CallbackContext) -> int:
    home = await admin_home(update, context)
    if home is None:
        return ConversationHandler.END
    start_flow(context, update.effective_chat.id, home)
    return await _show_member_list(update.message, home)


async def modify_members(update: Update, context: CallbackContext) -> int:
    """A tapped (or exactly typed) member name opens their actions; adding is its own step."""
    home, flow = await continue_admin_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.lower() in {"back", "cancel"}:
        end_flow(context, update.effective_chat.id)
        await update.message.reply_text("Member management closed.", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    if text.casefold() == BTN_MEMBERS_ADD.casefold():
        flow.pop("add_confirm", None)
        await update.message.reply_text(
            "Send the new member's name:",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_MEMBERS_BACK)]], resize_keyboard=True),
        )
        return MANAGE_MEMBER_ADD
    member = _member_from_label(home, text)
    if member is None:
        close = _closest_member(home, text)
        hint = f"Did you mean {close['name']}? Tap their name below." if close else "Tap a name below to manage a member."
        await update.message.reply_text(
            f"There is no member called '{text}'. {hint} To add someone new, tap '{BTN_MEMBERS_ADD}'.",
            reply_markup=_members_keyboard(home),
        )
        return MANAGE_MEMBER
    return await _show_member_actions(update, flow, home, member)


async def _show_member_actions(update: Update, flow, home, member) -> int:
    flow["manage_mid"] = member["mid"]
    name = member["name"]
    retired = member.get("status") == "retired"
    history = member_has_history(home, member)
    rows, hints = [], []
    if retired:
        intro = f"{name} retired on {str(member.get('retired_at') or '')[:10]}; their records are frozen."
    else:
        intro = f"{name}: {_stats_line(member_stats(home, member), home_currency(home))}."
        rows.append([KeyboardButton(f"Retire {name}")])
        hints.append(
            "Retire: keeps their bookkeeping frozen, takes them out of every menu, standing and report, "
            "unlinks their Telegram account and sends them a farewell note."
        )
    if history:
        hints.append("Remove is not offered because they appear in expenses, chores or penalties.")
    else:
        rows.append([KeyboardButton(f"Remove {name}")])
        hints.append("Remove: deletes the profile (no bookkeeping references it).")
    rows.append([KeyboardButton(BTN_MEMBERS_BACK)])
    await update.message.reply_text(
        intro + "\n\n" + "\n".join(hints), reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True)
    )
    return MANAGE_MEMBER_ACTION


async def member_add(update: Update, context: CallbackContext) -> int:
    """Add a member by name; a name that looks like an existing member has to be sent twice."""
    home, flow = await continue_admin_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = " ".join(update.message.text.split())
    if text.lower() in {"back", "cancel"}:
        flow.pop("add_confirm", None)
        return await _show_member_list(update.message, home)
    if not text or len(text) > MAX_NAME_LEN:
        await update.message.reply_text(f"Please send a name of 1\u2013{MAX_NAME_LEN} characters.")
        return MANAGE_MEMBER_ADD
    existing = member_by_name(home, text)
    if existing is not None:
        flow.pop("add_confirm", None)
        await update.message.reply_text(f"{existing['name']} is already a member.")
        return await _show_member_actions(update, flow, home, existing)
    close = _closest_member(home, text)
    if close is not None and flow.get("add_confirm") != text.casefold():
        flow["add_confirm"] = text.casefold()
        await update.message.reply_text(
            f"That looks like {close['name']}, who is already a member. "
            f"Send '{text}' again to add a new member with that name anyway, or '{BTN_MEMBERS_BACK}'."
        )
        return MANAGE_MEMBER_ADD
    flow.pop("add_confirm", None)
    get_store().update(home["id"], lambda h: h["members"].append(new_member(h, text)))
    end_flow(context, update.effective_chat.id)
    await update.message.reply_text(f"Added {text} to the household.", reply_markup=get_main_keyboard())
    return ConversationHandler.END


async def member_action(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_admin_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.lower() == "cancel":
        end_flow(context, update.effective_chat.id)
        await update.message.reply_text("Member management closed.", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    if text.lower() == "back":
        flow.pop("manage_mid", None)
        return await _show_member_list(update.message, home)
    member = member_by_mid(home, flow.get("manage_mid"))
    if member is None:
        flow.pop("manage_mid", None)
        await update.message.reply_text("That member no longer exists.")
        return await _show_member_list(update.message, home)
    name = member["name"]
    if text.casefold() == f"Retire {name}".casefold():
        return await _retire(update, context, home, member)
    if text.casefold() == f"Remove {name}".casefold():
        try:
            get_store().update(home["id"], lambda h: remove_member(h, member["mid"]))
        except HomeError as exc:
            await update.message.reply_text(str(exc))
            return MANAGE_MEMBER_ACTION
        end_flow(context, update.effective_chat.id)
        await update.message.reply_text(f"Removed {name} from the household.", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    await update.message.reply_text("Please use the buttons: Retire, Remove or Back.")
    return MANAGE_MEMBER_ACTION


async def _retire(update: Update, context: CallbackContext, home, member) -> int:
    stats = member_stats(home, member)  # retirement changes no numbers, so this is the frozen record
    try:
        _, (retired, former_uid) = get_store().update(home["id"], lambda h: retire_member(h, member["mid"]))
    except HomeError as exc:
        await update.message.reply_text(str(exc))
        return MANAGE_MEMBER_ACTION
    end_flow(context, update.effective_chat.id)
    name = retired["name"]
    if former_uid is None:
        note = f"{name} has no linked Telegram account, so no farewell note was sent."
    else:
        try:
            await context.bot.send_message(chat_id=former_uid, text=farewell_text(home, retired, stats))
            note = f"A farewell note was sent to {name}."
        except TelegramError as exc:
            logger.info("Home %s: farewell note to %s failed: %s", home["id"], former_uid, exc)
            note = f"The farewell note could not be delivered to {name} ({exc})."
    await update.message.reply_text(
        f"Retired {name}. Their records stay in the books, frozen: {_stats_line(stats, home_currency(home))}.\n"
        "They no longer appear in menus, standings or reports, and their Telegram link was removed.\n"
        + note,
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


# ---------------------------------------------------------------- profile
async def open_manage_self(update: Update, context: CallbackContext) -> None:
    home = await require_home(update, context)
    if home is None:
        return
    member = member_for_uid(home, update.effective_user.id)
    if not member:
        await update.message.reply_text(
            "I can't match you to a member of this home. Send /join in the group to link your profile."
        )
        return
    await update.message.reply_text(f"Managing your profile: {member['name']}", reply_markup=get_manage_self_keyboard())


async def start_change_username(update: Update, context: CallbackContext) -> int:
    home = await require_home(update, context)
    if home is None:
        return ConversationHandler.END
    member = member_for_uid(home, update.effective_user.id)
    if not member:
        await update.message.reply_text(
            "I can't match you to a member of this home. Send /join in the group first.",
            reply_markup=get_manage_self_keyboard(),
        )
        return ConversationHandler.END
    flow = start_flow(context, update.effective_chat.id, home)
    flow["change_username_old"] = member["name"]
    await update.message.reply_text(
        f"Current username: {member['name']}\n\nSend your new username:", reply_markup=get_cancel_keyboard()
    )
    return CHANGE_USERNAME_NEW


async def do_change_username(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    new_name = update.message.text.strip()
    if new_name.lower() == "cancel":
        return await cancel(update, context)
    if not new_name or len(new_name) > MAX_NAME_LEN:
        await update.message.reply_text(f"Please send a name of 1–{MAX_NAME_LEN} characters:")
        return CHANGE_USERNAME_NEW
    old_name = flow.get("change_username_old", "")

    def apply(h):
        if _match_member_name(h["members"], new_name) is not None:
            return False
        _rename_member_in_data(h, old_name, new_name)
        return True

    _, ok = get_store().update(home["id"], apply)
    if not ok:
        await update.message.reply_text(f"'{new_name}' is already taken. Try a different name:")
        return CHANGE_USERNAME_NEW
    end_flow(context, update.effective_chat.id)
    await update.message.reply_text(f"Username changed to {new_name}!", reply_markup=get_manage_self_keyboard())
    return ConversationHandler.END


async def set_vacation_status(update: Update, context: CallbackContext) -> None:
    """Toggle vacation status. Button: yourself. /setstatus <name>: any member of the home."""
    home = await require_home(update, context)
    if home is None:
        return
    if context.args:
        target_name = " ".join(context.args)
        target = _match_member_name(home["members"], target_name)
        if target is None:
            await update.message.reply_text(f"Member '{target_name}' not found.")
            return
    else:
        target = member_for_uid(home, update.effective_user.id)
        if target is None:
            await update.message.reply_text(
                "I cannot match you to a member of this home. Send /join in the group to link your profile."
            )
            return

    mid = target["mid"]

    def apply(h):
        for m in h["members"]:
            if m["mid"] == mid:
                if m.get("status") == "retired":
                    return m["name"], "retired"
                m["status"] = "vacating" if m.get("status", "active") == "active" else "active"
                return m["name"], m["status"]
        return None, None

    _, (name, new_status) = get_store().update(home["id"], apply)
    reply_kb = get_main_keyboard() if context.args else get_manage_self_keyboard()
    if new_status is None:
        await update.message.reply_text("That member no longer exists.", reply_markup=reply_kb)
    elif new_status == "retired":
        await update.message.reply_text(f"{name} has retired from this home; their record is frozen.", reply_markup=reply_kb)
    elif new_status == "vacating":
        await update.message.reply_text(
            f"{name} is now on vacation and will appear at the bottom when selecting splitters, "
            "as a reminder to only include them for long-term expenses.",
            reply_markup=reply_kb,
        )
    else:
        await update.message.reply_text(f"{name} is now active.", reply_markup=reply_kb)
