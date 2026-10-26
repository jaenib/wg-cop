"""Edit-own-chores conversation (Settings -> Manage <profile> -> Edit Chores).

A linked member picks one of their recent chore log entries and corrects its
points or description, or deletes it. The member's running total in
``chores`` moves by the same amount, so standings, penalties and the weekly
report follow the correction. Only the caller's own entries are offered.

Entries carry no id, so a draft remembers the log index and the entry's
timestamp and every write re-checks both: a log that changed underneath
(another chat booked or deleted something) is never edited blindly.
"""
import re

from telegram import Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.formatting import format_chore_entry
from wgcop.handlers.chore import MAX_POINTS, _minutes_per_point
from wgcop.handlers.common import cancel, continue_flow
from wgcop.homes import end_flow, require_home, start_flow
from wgcop.keyboards import (
    BTN_BACK,
    BTN_CHORE_DELETE,
    BTN_CHORE_DELETE_CONFIRM,
    BTN_CHORE_DESCRIPTION,
    BTN_CHORE_POINTS,
    EDIT_CHORE_DELETE,
    EDIT_CHORE_DESCRIPTION,
    EDIT_CHORE_MENU,
    EDIT_CHORE_PICK,
    EDIT_CHORE_POINTS,
    build_pick_keyboard,
    get_back_cancel_keyboard,
    get_confirm_delete_keyboard,
    get_edit_chore_keyboard,
    get_main_keyboard,
)
from wgcop.members import _normalise_member_name, adjust_chore_points, chore_points_for
from wgcop.store import get_store, member_for_uid

CANCEL_WORDS = {"cancel", "back to main menu"}
BACK_WORDS = {"back"}
SKIP_WORDS = {"-", "skip"}
RECENT_LIMIT = 10


def own_entries(home, member_name) -> list:
    """(log index, entry) of the member's most recent chore entries, newest first."""
    norm = _normalise_member_name(member_name)
    log = home.get("chore_log", []) or []
    hits = [(idx, entry) for idx, entry in enumerate(log) if _normalise_member_name(entry.get("member")) == norm]
    return hits[::-1][:RECENT_LIMIT]


def entry_label(number: int, entry) -> str:
    """Button text for one entry; the leading number is what the pick step reads back."""
    stamp = str(entry.get("timestamp", ""))[:16].replace("T", " ")
    text = f"{number}. {stamp} · +{entry.get('points', 0)} pts"
    description = str(entry.get("description") or "").strip()
    return f"{text} – {description}" if description else text


def _locate(home, flow):
    """The drafted entry in ``home``, or None if the log changed underneath the draft."""
    idx = flow.get("chore_index")
    member = flow.get("chore_member")
    log = home.get("chore_log", []) or []
    if idx is None or member is None or not (0 <= idx < len(log)):
        return None
    entry = log[idx]
    if _normalise_member_name(entry.get("member")) != _normalise_member_name(member):
        return None
    if entry.get("timestamp") != flow.get("chore_stamp"):
        return None
    return entry


async def _show_entries(update: Update, context: CallbackContext, home, flow, intro: str = "") -> int:
    member_name = flow.get("chore_member")
    entries = own_entries(home, member_name)
    if not entries:
        end_flow(context, update.effective_chat.id)
        await update.message.reply_text(
            f"{intro}No chore entries recorded for {member_name} yet.".strip(), reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END
    flow["chore_choices"] = [idx for idx, _ in entries]
    for key in ("chore_index", "chore_stamp"):
        flow.pop(key, None)
    labels = [entry_label(number, entry) for number, (_, entry) in enumerate(entries, start=1)]
    noun = "entry" if len(entries) == 1 else "entries"
    await update.message.reply_text(
        f"{intro}Your last {len(entries)} chore {noun} (newest first). Tap the one to correct:",
        reply_markup=build_pick_keyboard(labels),
    )
    return EDIT_CHORE_PICK


async def _show_menu(update: Update, entry) -> int:
    await update.message.reply_html(
        f"{format_chore_entry(entry)}\n\nWhat do you want to change?", reply_markup=get_edit_chore_keyboard()
    )
    return EDIT_CHORE_MENU


async def _lost(update: Update, context: CallbackContext, home, flow) -> int:
    return await _show_entries(
        update, context, home, flow, intro="That chore entry changed or was removed in the meantime. "
    )


async def _back_to_menu(update: Update, context: CallbackContext, home, flow) -> int:
    entry = _locate(home, flow)
    if entry is None:
        return await _lost(update, context, home, flow)
    return await _show_menu(update, entry)


async def start_edit_chores(update: Update, context: CallbackContext) -> int:
    home = await require_home(update, context)
    if home is None:
        return ConversationHandler.END
    member = member_for_uid(home, update.effective_user.id)
    if member is None:
        await update.message.reply_text(
            "I cannot match you to a member of this home. Send /join in the group to link your profile.",
            reply_markup=get_main_keyboard(),
        )
        return ConversationHandler.END
    flow = start_flow(context, update.effective_chat.id, home)
    flow["chore_member"] = member["name"]
    return await _show_entries(update, context, home, flow)


async def edit_chores_pick(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.casefold() in CANCEL_WORDS:
        return await cancel(update, context)
    choices = flow.get("chore_choices") or []
    match = re.match(r"\s*(\d+)", text)
    number = int(match.group(1)) if match else 0
    if not 1 <= number <= len(choices):
        await update.message.reply_text("Please tap one of the entries above, or Cancel.")
        return EDIT_CHORE_PICK
    idx = choices[number - 1]
    log = home.get("chore_log", []) or []
    entry = log[idx] if 0 <= idx < len(log) else None
    if entry is None or _normalise_member_name(entry.get("member")) != _normalise_member_name(flow.get("chore_member")):
        return await _lost(update, context, home, flow)
    flow["chore_index"] = idx
    flow["chore_stamp"] = entry.get("timestamp")
    return await _show_menu(update, entry)


async def edit_chores_menu(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    choice = update.message.text.strip().casefold()
    if choice in CANCEL_WORDS:
        return await cancel(update, context)
    if choice in BACK_WORDS:
        return await _show_entries(update, context, home, flow)
    entry = _locate(home, flow)
    if entry is None:
        return await _lost(update, context, home, flow)
    if choice == BTN_CHORE_POINTS.casefold():
        await update.message.reply_text(
            f"Currently {entry.get('points', 0)} points. Send the corrected points "
            f"(1 point = {_minutes_per_point(home)} minutes; 1–{MAX_POINTS}):",
            reply_markup=get_back_cancel_keyboard(),
        )
        return EDIT_CHORE_POINTS
    if choice == BTN_CHORE_DESCRIPTION.casefold():
        current = str(entry.get("description") or "").strip()
        await update.message.reply_text(
            (f"Current description: {current}\n" if current else "No description yet.\n")
            + "Send the new description, or '-' to remove it:",
            reply_markup=get_back_cancel_keyboard(),
        )
        return EDIT_CHORE_DESCRIPTION
    if choice == BTN_CHORE_DELETE.casefold():
        await update.message.reply_html(
            f"Delete this entry?\n\n{format_chore_entry(entry)}\n\n"
            f"{entry.get('points', 0)} points come off your total. Tap '{BTN_CHORE_DELETE_CONFIRM}' to confirm, "
            f"or '{BTN_BACK}'.",
            reply_markup=get_confirm_delete_keyboard(),
        )
        return EDIT_CHORE_DELETE
    await update.message.reply_text("Please choose one of the options above.")
    return EDIT_CHORE_MENU


async def _finish(update: Update, context: CallbackContext, home, flow, apply, headline: str) -> int:
    """Run ``apply`` inside the store, end the draft, and report the entry and the new total."""
    _, result = get_store().update(home["id"], apply)
    end_flow(context, update.effective_chat.id)
    if result is None:
        await update.message.reply_text(
            "That chore entry changed or was removed in the meantime; nothing was changed.",
            reply_markup=get_main_keyboard(),
        )
        return ConversationHandler.END
    entry, total = result
    await update.message.reply_html(
        f"{headline}\n\n{format_chore_entry(entry)}\n\nYour total is now {total} points.",
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


async def edit_chores_points(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.casefold() in CANCEL_WORDS:
        return await cancel(update, context)
    if text.casefold() in BACK_WORDS:
        return await _back_to_menu(update, context, home, flow)
    try:
        points = int(text)
    except ValueError:
        await update.message.reply_text(f"Please send a whole number of points between 1 and {MAX_POINTS}.")
        return EDIT_CHORE_POINTS
    if not 1 <= points <= MAX_POINTS:
        await update.message.reply_text(f"Points must be between 1 and {MAX_POINTS}.")
        return EDIT_CHORE_POINTS

    def apply(h):
        entry = _locate(h, flow)
        if entry is None:
            return None
        delta = points - int(entry.get("points", 0) or 0)
        entry["points"] = points
        return dict(entry), adjust_chore_points(h, entry["member"], delta)

    return await _finish(update, context, home, flow, apply, "Points updated.")


async def edit_chores_description(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.casefold() in CANCEL_WORDS:
        return await cancel(update, context)
    if text.casefold() in BACK_WORDS:
        return await _back_to_menu(update, context, home, flow)
    description = "" if text.casefold() in SKIP_WORDS else text

    def apply(h):
        entry = _locate(h, flow)
        if entry is None:
            return None
        entry["description"] = description
        return dict(entry), chore_points_for(h, entry["member"])

    return await _finish(update, context, home, flow, apply, "Description updated." if description else "Description removed.")


async def edit_chores_delete(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.casefold() in CANCEL_WORDS:
        return await cancel(update, context)
    if text.casefold() in BACK_WORDS:
        return await _back_to_menu(update, context, home, flow)
    if text.casefold() != BTN_CHORE_DELETE_CONFIRM.casefold():
        await update.message.reply_text(f"Tap '{BTN_CHORE_DELETE_CONFIRM}' to delete the entry, or '{BTN_BACK}'.")
        return EDIT_CHORE_DELETE

    def apply(h):
        entry = _locate(h, flow)
        if entry is None:
            return None
        h["chore_log"].pop(flow["chore_index"])
        return dict(entry), adjust_chore_points(h, entry["member"], -int(entry.get("points", 0) or 0))

    return await _finish(update, context, home, flow, apply, "Entry deleted.")
