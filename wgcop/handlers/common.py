"""Menu navigation, /start, cancel/timeout, and the shared handler helpers."""
import logging

from telegram import Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.homes import (
    REASON_TEXT,
    build_home_picker,
    end_flow,
    flow_home,
    flow_state,
    require_home,
    resolve_home,
)
from wgcop.keyboards import (
    get_admin_keyboard,
    get_main_keyboard,
    get_penalties_keyboard,
    get_settings_keyboard,
    get_weekly_report_keyboard,
)
from wgcop.store import get_store, is_admin, member_for_uid

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ helpers
async def continue_flow(update: Update, context: CallbackContext):
    """(home, flow) for the draft running in this chat, or (None, None) after
    telling the user the draft is gone (home switched, access lost, restart)."""
    chat = update.effective_chat
    user = update.effective_user
    home = flow_home(context, chat.id, user.id) if chat and user else None
    if home is None:
        if chat:
            end_flow(context, chat.id)
        message = update.effective_message
        if message is not None:
            await message.reply_text(
                "This draft is no longer valid. Please start again from the main menu.",
                reply_markup=get_main_keyboard(),
            )
        return None, None
    return home, flow_state(context, chat.id)


async def admin_home(update: Update, context: CallbackContext):
    """The resolved home if the caller administers it; otherwise reply and return None."""
    home = await require_home(update, context)
    if home is None:
        return None
    if not is_admin(home, update.effective_user.id):
        await update.effective_message.reply_text("Unauthorized.")
        return None
    return home


async def continue_admin_flow(update: Update, context: CallbackContext):
    """Like continue_flow, but the caller must still administer the draft's home.

    A role revoked between two steps of an admin conversation ends it before
    any write happens."""
    home, flow = await continue_flow(update, context)
    if home is None:
        return None, None
    if not is_admin(home, update.effective_user.id):
        end_flow(context, update.effective_chat.id)
        await update.effective_message.reply_text(
            "You are no longer an administrator of this home.", reply_markup=get_main_keyboard()
        )
        return None, None
    return home, flow


def settings_keyboard_for(home, uid):
    member = member_for_uid(home, uid)
    multi_home = len(get_store().homes_for_user(uid)) > 1
    return get_settings_keyboard(
        is_admin=is_admin(home, uid),
        username=member["name"] if member else None,
        multi_home=multi_home,
    )


# ----------------------------------------------------------------- handlers
async def start(update: Update, context: CallbackContext) -> None:
    res = resolve_home(update)
    if res.home is not None:
        await update.message.reply_text(
            f"WG Bot is active for '{res.home['name']}'! Use the buttons below:",
            reply_markup=get_main_keyboard(),
        )
        return
    if res.reason == "choose":
        await update.message.reply_text(
            "You belong to several homes. Which one do you mean?",
            reply_markup=build_home_picker(res.choices),
        )
        return
    await update.message.reply_text(REASON_TEXT.get(res.reason, "I cannot tell which home you mean."))


async def cancel(update: Update, context: CallbackContext) -> int:
    if update.effective_chat:
        end_flow(context, update.effective_chat.id)
    message = update.effective_message
    if message is not None:
        await message.reply_text("Cancelled. Back to main menu.", reply_markup=get_main_keyboard())
    return ConversationHandler.END


async def on_timeout(update: Update, context: CallbackContext) -> int:
    chat = update.effective_chat
    if chat:
        end_flow(context, chat.id)
        await context.bot.send_message(
            chat_id=chat.id,
            text="Session timed out. Back to main menu.",
            reply_markup=get_main_keyboard(),
        )
    return ConversationHandler.END


async def open_penalties(update: Update, context: CallbackContext) -> None:
    home = await require_home(update, context)
    if home is None:
        return
    await update.message.reply_text("Penalties menu:", reply_markup=get_penalties_keyboard())


async def open_settings(update: Update, context: CallbackContext) -> None:
    home = await require_home(update, context)
    if home is None:
        return
    await update.message.reply_text(
        f"Settings for '{home['name']}':",
        reply_markup=settings_keyboard_for(home, update.effective_user.id),
    )


async def settings_back(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Main menu ready.", reply_markup=get_main_keyboard())


async def open_admin_menu(update: Update, context: CallbackContext) -> None:
    home = await admin_home(update, context)
    if home is None:
        return
    await update.message.reply_text(f"Admin panel for '{home['name']}':", reply_markup=get_admin_keyboard())


async def back_to_settings(update: Update, context: CallbackContext) -> None:
    await open_settings(update, context)


async def open_weekly_report_menu(update: Update, context: CallbackContext) -> None:
    home = await admin_home(update, context)
    if home is None:
        return
    enabled = (home.get("settings") or {}).get("weekly_report_enabled", True)
    await update.message.reply_text(
        f"Weekly report settings ({'enabled' if enabled else 'disabled'}, "
        f"{home['settings'].get('weekly_report_time', '09:30')} {home['settings'].get('timezone', 'Europe/Berlin')}):",
        reply_markup=get_weekly_report_keyboard(),
    )


async def back_to_admin(update: Update, context: CallbackContext) -> None:
    await open_admin_menu(update, context)
