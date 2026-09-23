"""Menu navigation, /start, cancel and timeout handlers (relocated verbatim)."""
from telegram import Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.botconfig import BOT_HANDLER_ID
from wgcop.identity import _resolve_member_for_user
from wgcop.keyboards import (
    get_admin_keyboard,
    get_main_keyboard,
    get_penalties_keyboard,
    get_settings_keyboard,
    get_weekly_report_keyboard,
)
from wgcop.members import _get_member_name

async def open_penalties(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Penalties menu:", reply_markup=get_penalties_keyboard()
    )


def _settings_keyboard_for_user(user):
    is_admin = user.id == BOT_HANDLER_ID
    member = _resolve_member_for_user(user)
    username = _get_member_name(member) if member else None
    return get_settings_keyboard(is_admin=is_admin, username=username)


async def open_settings(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Settings menu:",
        reply_markup=_settings_keyboard_for_user(update.effective_user),
    )


async def settings_back(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Main menu ready.", reply_markup=get_main_keyboard()
    )


async def open_admin_menu(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != BOT_HANDLER_ID:
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text("Admin panel:", reply_markup=get_admin_keyboard())


async def open_weekly_report_menu(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != BOT_HANDLER_ID:
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text("Weekly report settings:", reply_markup=get_weekly_report_keyboard())


async def back_to_admin(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != BOT_HANDLER_ID:
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text("Admin panel:", reply_markup=get_admin_keyboard())


async def back_to_settings(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Settings menu:",
        reply_markup=_settings_keyboard_for_user(update.effective_user),
    )


# Start
async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "WG Bot is active! Use the buttons below:", reply_markup=get_main_keyboard()
    )


async def cancel(update: Update, context: CallbackContext) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Cancelled. Back to main menu.", reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


async def on_timeout(update: Update, context: CallbackContext) -> int:
    context.user_data.clear()
    chat = update.effective_chat
    if chat:
        await context.bot.send_message(
            chat_id=chat.id,
            text="Session timed out. Back to main menu.",
            reply_markup=get_main_keyboard(),
        )
    return ConversationHandler.END
