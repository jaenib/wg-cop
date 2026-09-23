"""Chore conversation, /chore command and chore listing."""
from telegram import ReplyKeyboardRemove, Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.formatting import format_chore_entry
from wgcop.handlers.common import cancel, continue_flow
from wgcop.homes import end_flow, home_tz, require_home, start_flow
from wgcop.keyboards import (
    CHORE_DESCRIPTION,
    CHORE_MINUTES,
    CHORE_USER,
    get_main_keyboard,
    get_member_keyboard,
)
from wgcop.members import _match_member_name, add_chore_entry
from wgcop.store import get_store, member_for_uid

MAX_MINUTES = 24 * 60
MAX_POINTS = 1000
CANCEL_WORDS = {"done", "cancel", "back to main menu"}


def _minutes_per_point(home) -> int:
    value = (home.get("settings") or {}).get("minutes_per_point", 15)
    return value if isinstance(value, int) and value > 0 else 15


async def start_chore(update: Update, context: CallbackContext) -> int:
    home = await require_home(update, context)
    if home is None:
        return ConversationHandler.END
    keyboard = get_member_keyboard(home)
    if keyboard is None:
        await update.message.reply_text("No members found. Please add members first.", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    start_flow(context, update.effective_chat.id, home)
    await update.message.reply_text("Who completed the chore?", reply_markup=keyboard)
    return CHORE_USER


async def chore_user(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    choice = update.message.text.strip()
    if choice.lower() in CANCEL_WORDS:
        return await cancel(update, context)
    member = _match_member_name(home["members"], choice)
    if member is None:
        await update.message.reply_text("Please pick a member from the keyboard.")
        return CHORE_USER
    flow["member"] = member["name"]
    await update.message.reply_text("How many minutes did it take?", reply_markup=ReplyKeyboardRemove())
    return CHORE_MINUTES


async def chore_minutes(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    try:
        minutes = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Invalid input. Enter the minutes again.")
        return CHORE_MINUTES
    if minutes <= 0 or minutes > MAX_MINUTES:
        await update.message.reply_text(f"Please enter a number of minutes between 1 and {MAX_MINUTES}.")
        return CHORE_MINUTES

    points = minutes // _minutes_per_point(home)
    flow["minutes"] = minutes
    flow["points"] = points
    await update.message.reply_text(
        (
            f"{flow['member']} earned {points} points.\n"
            "Optional: send a short description of the chore (what was done). Send '-' to skip."
        ),
        reply_markup=ReplyKeyboardRemove(),
    )
    return CHORE_DESCRIPTION


async def chore_description(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    member = flow.get("member")
    points = flow.get("points")
    if member is None or points is None:
        await update.message.reply_text(
            "Something went wrong with the chore entry. Please try again.", reply_markup=get_main_keyboard()
        )
        end_flow(context, update.effective_chat.id)
        return ConversationHandler.END

    raw_desc = update.message.text.strip()
    description = "" if raw_desc in {"-", "skip", "Skip"} else raw_desc

    get_store().update(home["id"], lambda h: add_chore_entry(h, member, points, description, tz=home_tz(h)))
    end_flow(context, update.effective_chat.id)  # clear before replying: a retry must not double-book

    text = f"{member} earned {points} points!"
    if description:
        text += f"\n📝 {description}"
    await update.message.reply_text(text, reply_markup=get_main_keyboard())
    return ConversationHandler.END


async def handle_chore(update: Update, context: CallbackContext) -> None:
    """/chore <points> [description...] — records points for the caller's linked profile."""
    if not context.args:
        await update.message.reply_text("Usage: /chore <points> [description...]\nExample: /chore 2 cleaned bathroom")
        return
    home = await require_home(update, context)
    if home is None:
        return
    member = member_for_uid(home, update.effective_user.id)
    if member is None:
        await update.message.reply_text(
            "I cannot match you to a member of this home. Send /join in the group to link your profile."
        )
        return

    points_str, *desc_parts = context.args
    try:
        points = int(points_str)
    except ValueError:
        await update.message.reply_text("First argument has to be the number of points.")
        return
    if points <= 0 or points > MAX_POINTS:
        await update.message.reply_text(f"Points must be between 1 and {MAX_POINTS}.")
        return
    description = " ".join(desc_parts) if desc_parts else ""
    name = member["name"]
    get_store().update(home["id"], lambda h: add_chore_entry(h, name, points, description, tz=home_tz(h)))

    text = f"Recorded {points} points for {name}."
    if description:
        text += f"\n📝 {description}"
    await update.message.reply_text(text)


async def list_chores(update: Update, context: CallbackContext) -> None:
    home = await require_home(update, context)
    if home is None:
        return
    entries = home.get("chore_log", []) or []
    if not entries:
        await update.message.reply_text("No chores recorded yet.", reply_markup=get_main_keyboard())
        return
    formatted = "\n\n".join(format_chore_entry(e) for e in entries[-10:])
    await update.message.reply_html(formatted, reply_markup=get_main_keyboard())
