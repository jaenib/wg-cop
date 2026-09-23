"""Application wiring: handlers, conversations, jobs and the polling entry point."""
import logging
from datetime import datetime, timedelta

import pytz
from telegram import ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    PicklePersistence,
    filters,
)

from wgcop.botconfig import TOKEN
from wgcop.handlers.admin import (
    admin_beer_count,
    admin_beer_member,
    admin_beer_start,
    admin_hoeck_date,
    admin_hoeck_start,
    admin_report_time_set,
    admin_report_time_start,
    admin_system_overview,
    admin_trigger_report,
    set_weekly_report,
)
from wgcop.handlers.chore import (
    chore_description,
    chore_minutes,
    chore_user,
    handle_chore,
    list_chores,
    start_chore,
)
from wgcop.handlers.common import (
    back_to_admin,
    back_to_settings,
    cancel,
    on_timeout,
    open_admin_menu,
    open_penalties,
    open_settings,
    open_weekly_report_menu,
    settings_back,
    start,
)
from wgcop.handlers.edit import (
    edit_entries_amount,
    edit_entries_menu,
    edit_entries_pick_member,
    edit_entries_split,
    start_edit_entries,
)
from wgcop.handlers.expense import (
    expense_amount,
    expense_description,
    expense_mode_selection,
    expense_payer_cb,
    expense_receipt_invalid,
    expense_receipt_photo,
    expense_split_cb,
    receipt_items_cb,
    start_expense,
)
from wgcop.handlers.members import (
    do_change_username,
    manage_members,
    modify_members,
    open_manage_self,
    set_vacation_status,
    start_change_username,
)
from wgcop.handlers.penalties import beer_owed, redeem_count, redeem_member, redeem_start
from wgcop.handlers.views import list_expenses, standings
from wgcop.jobs import (
    send_alive,
    send_hoeck_reminder,
    send_missed_weekly_report_on_startup,
    setup_chronicler_backup_job,
    setup_local_backup_job,
    setup_weekly_job,
)
from wgcop.keyboards import (
    ADMIN_BEER_COUNT,
    ADMIN_BEER_MEMBER,
    ADMIN_REPORT_TIME,
    CB_PAYER_PREFIX,
    CHANGE_USERNAME_NEW,
    CHORE_DESCRIPTION,
    CHORE_MINUTES,
    CHORE_USER,
    EDIT_AMOUNT,
    EDIT_MENU,
    EDIT_PICK_MEMBER,
    EDIT_SPLIT,
    EXPENSE_AMOUNT,
    EXPENSE_DESCRIPTION,
    EXPENSE_MODE,
    EXPENSE_PAYER,
    EXPENSE_RECEIPT,
    EXPENSE_RECEIPT_REVIEW,
    EXPENSE_SPLIT,
    MANAGE_MEMBER,
    REDEEM_COUNT,
    REDEEM_MEMBER,
)
from wgcop.reports import _parse_report_time
from wgcop.store import load_data

# Set up logging (was at import time of maBot.py; kept at import time of the app module)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

RECEIPT_IMAGE_FILTER = filters.PHOTO | filters.Document.IMAGE


def main():
    data = load_data()
    persistence = PicklePersistence(filepath="bot_persistence.pkl")
    app = Application.builder().token(TOKEN).persistence(persistence).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("expenses", list_expenses))
    app.add_handler(CommandHandler("chore", handle_chore))
    app.add_handler(CommandHandler("chores", list_chores))
    app.add_handler(CommandHandler("setstatus", set_vacation_status))

    app.add_handler(MessageHandler(filters.Regex("^Standings$"), standings))
    app.add_handler(MessageHandler(filters.Regex("^List Expenses$"), list_expenses))
    app.add_handler(MessageHandler(filters.Regex("^List Chores$"), list_chores))
    app.add_handler(MessageHandler(filters.Regex("^Penalties$"), open_penalties))
    app.add_handler(MessageHandler(filters.Regex("^Check Beer Owed$"), beer_owed))
    app.add_handler(MessageHandler(filters.Regex("^Set Weekly Report$"), set_weekly_report))
    app.add_handler(MessageHandler(filters.Regex("^Set Vacation Status$"), set_vacation_status))
    app.add_handler(MessageHandler(filters.Regex("^Settings$"), open_settings))
    app.add_handler(MessageHandler(filters.Regex("^Back to Main Menu$"), settings_back))
    app.add_handler(MessageHandler(filters.Regex("^Admin Panel$"), open_admin_menu))
    app.add_handler(MessageHandler(filters.Regex("^Back to Settings$"), back_to_settings))
    app.add_handler(MessageHandler(filters.Regex("^Back to Admin$"), back_to_admin))
    app.add_handler(MessageHandler(filters.Regex("^System Overview$"), admin_system_overview))
    app.add_handler(MessageHandler(filters.Regex("^Trigger Weekly Report$"), admin_trigger_report))
    app.add_handler(MessageHandler(filters.Regex("^Manage Weekly Report$"), open_weekly_report_menu))
    app.add_handler(MessageHandler(filters.Regex("^Manage "), open_manage_self))

    admin_beer_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Adjust Beer Count$"), admin_beer_start)],
        states={
            ADMIN_BEER_MEMBER: [
                MessageHandler(filters.Regex("^Cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_beer_member)
            ],
            ADMIN_BEER_COUNT: [
                MessageHandler(filters.Regex("^Cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_beer_count)
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        allow_reentry=True,
        conversation_timeout=300,
    )
    app.add_handler(admin_beer_conv)

    admin_hoeck_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^Set WG-Höck$"), admin_hoeck_start)],
        states={
            0: [
                MessageHandler(filters.Regex("^Cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_hoeck_date),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        allow_reentry=True,
        conversation_timeout=300,
    )
    app.add_handler(admin_hoeck_conv)

    admin_report_time_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Set Report Time$"), admin_report_time_start)],
        states={
            ADMIN_REPORT_TIME: [
                MessageHandler(filters.Regex("^Cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_report_time_set),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        allow_reentry=True,
        conversation_timeout=300,
    )
    app.add_handler(admin_report_time_conv)

    expense_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Add Expense$"), start_expense)],
        states={
            EXPENSE_MODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_mode_selection)
            ],
            EXPENSE_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_description)
            ],
            EXPENSE_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_amount)
            ],
            EXPENSE_RECEIPT: [
                MessageHandler(RECEIPT_IMAGE_FILTER, expense_receipt_photo),
                MessageHandler(filters.Regex("^Cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_receipt_invalid),
            ],
            EXPENSE_RECEIPT_REVIEW: [
                CallbackQueryHandler(
                    receipt_items_cb,
                    pattern=r"^(?:receipt_toggle:.*|receipt_done|receipt_cancel|receipt_accept|receipt_manual)$",
                )
            ],
            EXPENSE_PAYER: [
                CallbackQueryHandler(expense_payer_cb, pattern=f"^{CB_PAYER_PREFIX}")
            ],
            EXPENSE_SPLIT: [
                CallbackQueryHandler(
                    expense_split_cb,
                    pattern=r"^(?:split_toggle:.*|split_all|split_done|split_back|split_cancel)$",
                )
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        conversation_timeout=300,
    )
    app.add_handler(expense_conv)

    manage_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Member Management$"), manage_members)],
        states={
            MANAGE_MEMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, modify_members)
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        conversation_timeout=300,
    )
    app.add_handler(manage_conv)

    change_username_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Change Username$"), start_change_username)],
        states={
            CHANGE_USERNAME_NEW: [
                MessageHandler(filters.Regex("^Cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, do_change_username),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        allow_reentry=True,
        conversation_timeout=300,
    )
    app.add_handler(change_username_conv)

    edit_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Edit Entries$"), start_edit_entries)],
        states={
            EDIT_PICK_MEMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_entries_pick_member)
            ],
            EDIT_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_entries_menu)
            ],
            EDIT_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_entries_amount)
            ],
            EDIT_SPLIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_entries_split)
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        conversation_timeout=300,
    )
    app.add_handler(edit_conv)

    _nav_pattern = filters.Regex(
        "^(Penalties|Check Beer Owed|Back to Main Menu|Settings|"
        "Add Expense|Add Chore|Standings|List Expenses|List Chores|"
        "Manage Members)$"
    )
    redeem_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Redeem Beer$"), redeem_start)],
        states={
            REDEEM_MEMBER: [
                MessageHandler(_nav_pattern, cancel),
                MessageHandler(filters.Regex("^Cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_member),
            ],
            REDEEM_COUNT: [
                MessageHandler(_nav_pattern, cancel),
                MessageHandler(filters.Regex("^Cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_count),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        allow_reentry=True,
        conversation_timeout=300,
    )
    app.add_handler(redeem_conv)

    chore_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Add Chore$"), start_chore)],
        states={
            CHORE_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, chore_user)
            ],
            CHORE_MINUTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, chore_minutes)
            ],
            CHORE_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, chore_description)
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        conversation_timeout=300,
    )

    app.add_handler(chore_conv)

    # Catch stale receipt inline-keyboard callbacks (e.g. after a bot restart)
    async def _stale_receipt_cb(update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()
        await query.message.reply_text(
            "This receipt session has expired (the bot may have restarted).\n"
            "Please start a new expense via Add Expense → Scan Receipt.",
            reply_markup=ReplyKeyboardRemove(),
        )

    app.add_handler(
        CallbackQueryHandler(
            _stale_receipt_cb,
            pattern=r"^(?:receipt_toggle:.*|receipt_done|receipt_cancel)$",
        )
    )

    # Top-level cancel — registered LAST so each ConversationHandler's own
    # fallback gets first shot at /cancel and can properly end the conversation.
    # If we registered these earlier, they would swallow /cancel before the
    # ConversationHandler ever sees it, leaving the user stuck in their state.
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.Regex("^Cancel$"), cancel))

    _startup_data = load_data()
    _rh, _rm = _parse_report_time(_startup_data.get("weekly_report_time", "09:30"))
    setup_weekly_job(app, hour=_rh, minute=_rm)
    app.job_queue.run_once(
        send_missed_weekly_report_on_startup,
        when=0,
        name="weekly_report_catchup",
    )
    setup_chronicler_backup_job(app)
    setup_local_backup_job(app)

    # Daily noon check for WG-Höck reminder
    tz = pytz.timezone("Europe/Berlin")
    now = datetime.now(tz)
    noon_today = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now >= noon_today:
        noon_today += timedelta(days=1)
    seconds_until_noon = (noon_today - now).total_seconds()
    app.job_queue.run_repeating(
        send_hoeck_reminder,
        interval=timedelta(days=1).total_seconds(),
        first=seconds_until_noon,
        name="hoeck_reminder",
    )
    logger.info(f"Höck reminder scheduled, next check at {noon_today.strftime('%Y-%m-%d %H:%M')}")

    app.job_queue.run_repeating(
        send_alive,
        interval=timedelta(hours=50).total_seconds(),
        first=0,
        name="heartbeat",
    )
    logger.info("Bot running...")
    app.run_polling()
