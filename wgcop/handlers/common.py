"""Menu navigation, /start, cancel/timeout, and the shared handler helpers."""
import functools
import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.homes import (
    REASON_TEXT,
    build_home_picker,
    end_flow,
    flow_home,
    flow_state,
    is_group_chat,
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
class CallbackAck:
    """Answer a callback query exactly once.

    Telegram accepts a single answerCallbackQuery per tap; a second one fails
    with "query is too old ... or query ID is invalid". A handler that
    acknowledged the tap up front could therefore never show a refusal alert.
    Handlers call ``ack(text, show_alert=True)`` for a refusal and leave the
    plain acknowledgement to ``answers_callback``.
    """

    def __init__(self, query):
        self._query = query
        self.sent = False

    async def __call__(self, text=None, show_alert=False) -> None:
        if self.sent or self._query is None:
            return
        self.sent = True
        await self._query.answer(text, show_alert=show_alert)


def answers_callback(handler):
    """Wrap ``handler(update, context, ack)`` so the query is answered exactly once:
    with the alert the handler sent, or plainly after it returned."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: CallbackContext):
        ack = CallbackAck(update.callback_query)
        try:
            return await handler(update, context, ack)
        finally:
            try:
                await ack()
            except TelegramError as exc:  # the user only sees the spinner a moment longer
                logger.warning("Could not acknowledge callback query: %s", exc)

    return wrapper


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


async def awaiting_button(update: Update, context: CallbackContext) -> None:
    """Text while a running draft waits for a tap on its inline keyboard (payer, split, receipt).

    The conversation keeps this text so the draft survives; otherwise it would reach
    ``stray_text``, which drops the draft as a leftover and the next tap finds it gone.
    Returning None keeps the conversation in its current state. Groups stay quiet."""
    message = update.effective_message
    if message is not None and not is_group_chat(update.effective_chat):
        await message.reply_text("Please tap one of the buttons above to continue, or send /cancel.")
    return None


STRAY_LABELS = {"back"}  # conversation-internal buttons that mean nothing outside a conversation


async def stray_text(update: Update, context: CallbackContext) -> None:
    """Text that no conversation or menu claimed, e.g. a 'Back' tapped after a restart.

    A restart forgets every running conversation, but the reply keyboard it showed
    stays on the user's screen. Instead of silence, drop the leftover draft and offer
    the menu. In groups only the known leftover labels are answered; chat is ignored.
    """
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None:
        return
    text = (message.text or "").strip().casefold()
    if is_group_chat(chat) and text not in STRAY_LABELS:
        return
    end_flow(context, chat.id)
    home = await require_home(update, context)
    if home is None:
        return  # require_home already said why (no home yet, or a picker to choose one)
    await message.reply_text(
        "Nothing is in progress (perhaps I was restarted). Back to the main menu:",
        reply_markup=get_main_keyboard(),
    )


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
