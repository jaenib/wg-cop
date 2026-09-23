"""Chore conversation, /chore command and chore listing (relocated verbatim)."""
from telegram import ReplyKeyboardRemove, Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.formatting import _format_chore_entry
from wgcop.keyboards import (
    CHORE_DESCRIPTION,
    CHORE_MINUTES,
    CHORE_USER,
    get_main_keyboard,
    get_member_keyboard,
)
from wgcop.members import _get_member_name, _match_member_name, add_chore_entry
from wgcop.store import load_data

# Chore flow
async def start_chore(update: Update, context: CallbackContext) -> int:
    data = load_data()
    keyboard = get_member_keyboard(data)
    if keyboard:
        await update.message.reply_text(
            "Who completed the chore?", reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
            "No members found. Please add members first.",
            reply_markup=get_main_keyboard(),
        )
        return ConversationHandler.END
    return CHORE_USER


async def chore_user(update: Update, context: CallbackContext) -> int:
    choice = update.message.text.strip()
    if choice.lower() in {"done", "cancel", "back to main menu"}:
        await update.message.reply_text(
            "Chore entry cancelled.", reply_markup=get_main_keyboard()
        )
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data["user"] = choice
    await update.message.reply_text(
        "How many minutes did it take?", reply_markup=ReplyKeyboardRemove()
    )
    return CHORE_MINUTES


async def chore_minutes(update: Update, context: CallbackContext) -> int:
    try:
        minutes = int(update.message.text)
    except ValueError:
        await update.message.reply_text(
            "Invalid input. Enter the minutes again."
        )
        return CHORE_MINUTES

    points = minutes // 15
    user = context.user_data["user"]

    # Remember values for the next step
    context.user_data["minutes"] = minutes
    context.user_data["points"] = points

    # Ask for an optional description
    await update.message.reply_text(
        (
            f"{user} earned {points} points.\n"
            "Optional: send a short description of the chore "
            "(what was done). Send '-' to skip."
        ),
        reply_markup=ReplyKeyboardRemove(),
    )
    return CHORE_DESCRIPTION


async def chore_description(update: Update, context: CallbackContext) -> int:
    data = load_data()

    user = context.user_data.get("user")
    points = context.user_data.get("points")

    if user is None or points is None:
        # Something went wrong in the flow, bail out cleanly
        await update.message.reply_text(
            "Something went wrong with the chore entry. Please try again.",
            reply_markup=get_main_keyboard(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    raw_desc = update.message.text.strip()
    # Allow skipping via '-' or 'skip'
    if raw_desc in {"-", "skip", "Skip"}:
        description = ""
    else:
        description = raw_desc

    # Single source of truth: logs + totals
    add_chore_entry(data, user, points, description)

    text = f"{user} earned {points} points!"
    if description:
        text += f"\n📝 {description}"

    await update.message.reply_text(text, reply_markup=get_main_keyboard())
    context.user_data.clear()
    return ConversationHandler.END


async def handle_chore(update: Update, context: CallbackContext) -> None:
    # Usage: /chore <points> <optional description...>
    if not context.args:
        await update.message.reply_text(
            "Usage: /chore <points> [description...]\n"
            "Example: /chore 2 cleaned bathroom"
        )
        return

    points_str, *desc_parts = context.args
    try:
        points = int(points_str)
    except ValueError:
        await update.message.reply_text("First argument has to be the number of points.")
        return

    description = " ".join(desc_parts) if desc_parts else ""

    user = update.effective_user
    data = load_data()

    member_obj = _match_member_name(
        data["members"],
        str(user.id),
    ) or _match_member_name(data["members"], user.full_name)
    
    member_name = _get_member_name(member_obj) if member_obj else user.full_name

    add_chore_entry(data, member_name, points, description)

    text = f"Recorded {points} points for {member_name}."
    if description:
        text += f"\n📝 {description}"

    await update.message.reply_text(text)


async def list_chores(update: Update, context: CallbackContext) -> None:
    data = load_data()
    entries = data.get("chore_log", []) or []
    if not entries:
        await update.message.reply_text(
            "No chores recorded yet.", reply_markup=get_main_keyboard()
        )
        return

    # Show last 10
    entries = entries[-10:]
    formatted = "\n\n".join(_format_chore_entry(e) for e in entries)
    await update.message.reply_html(formatted, reply_markup=get_main_keyboard())
