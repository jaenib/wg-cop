"""Application wiring: store startup, handlers, conversations, jobs, polling entry point."""
import logging
import re
import sys

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

from wgcop.botconfig import TOKEN, legacy_seed
from wgcop.handlers.admin import (
    admin_hoeck_date,
    admin_hoeck_start,
    admin_penalty_count,
    admin_penalty_member,
    admin_penalty_start,
    admin_report_time_set,
    admin_report_time_start,
    admin_system_overview,
    admin_toggle_weekly,
    admin_trigger_report,
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
from wgcop.handlers.home import (
    delete_home,
    delete_home_cb,
    home_select_cb,
    join,
    join_cb,
    newhome,
    on_migrate,
    switch_home,
)
from wgcop.handlers.members import (
    do_change_username,
    manage_members,
    member_action,
    modify_members,
    open_manage_self,
    set_vacation_status,
    start_change_username,
)
from wgcop.handlers.penalties import check_penalties, redeem_count, redeem_member, redeem_start
from wgcop.handlers.roles import open_roles, roles_menu, roles_pick
from wgcop.handlers.settings import home_settings_menu, home_settings_value, open_home_settings
from wgcop.handlers.views import list_expenses, standings
from wgcop.homes import CB_HOME_SELECT, end_flow
from wgcop.jobs import setup_jobs
from wgcop.keyboards import (
    ADMIN_HOECK_DATE,
    ADMIN_PENALTY_COUNT,
    ADMIN_PENALTY_MEMBER,
    ADMIN_REPORT_TIME,
    BTN_ADD_CHORE,
    BTN_ADD_EXPENSE,
    BTN_ADJUST_PENALTIES,
    BTN_ADMIN_PANEL,
    BTN_BACK_ADMIN,
    BTN_BACK_MAIN,
    BTN_BACK_SETTINGS,
    BTN_CANCEL,
    BTN_CHANGE_USERNAME,
    BTN_CHECK_PENALTIES,
    BTN_EDIT_ENTRIES,
    BTN_HOME_SETTINGS,
    BTN_LIST_CHORES,
    BTN_LIST_EXPENSES,
    BTN_MANAGE_PREFIX,
    BTN_MANAGE_ROLES,
    BTN_MANAGE_WEEKLY,
    BTN_MEMBER_MANAGEMENT,
    BTN_PENALTIES,
    BTN_REDEEM_PENALTY,
    BTN_SET_HOECK,
    BTN_SET_REPORT_TIME,
    BTN_SET_VACATION,
    BTN_SETTINGS,
    BTN_STANDINGS,
    BTN_SWITCH_HOME,
    BTN_SYSTEM_OVERVIEW,
    BTN_TOGGLE_WEEKLY,
    BTN_TRIGGER_WEEKLY,
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
    HOME_SETTINGS_MENU,
    HOME_DELETE_CALLBACK_PATTERN,
    HOME_SETTINGS_VALUE,
    JOIN_CALLBACK_PATTERN,
    MANAGE_MEMBER,
    MANAGE_MEMBER_ACTION,
    RECEIPT_CALLBACK_PATTERN,
    REDEEM_COUNT,
    REDEEM_MEMBER,
    ROLES_MENU,
    ROLES_PICK_USER,
    SPLIT_CALLBACK_PATTERN,
)
from wgcop.store import StoreError, get_store

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
# httpx logs every request URL, which contains the bot token; keep it out of the journal.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

RECEIPT_IMAGE_FILTER = filters.PHOTO | filters.Document.IMAGE
CONVERSATION_TIMEOUT = 300

# Every menu button that starts something new. Pressing one of these (or sending
# any command except /cancel) while a conversation is active ends that draft and
# re-queues the update, so it is handled by the button's own handler instead of
# being swallowed as free-text input by the stale conversation.
NAV_LABELS = (
    BTN_ADD_EXPENSE, BTN_ADD_CHORE, BTN_LIST_EXPENSES, BTN_LIST_CHORES, BTN_STANDINGS, BTN_PENALTIES,
    BTN_SETTINGS, BTN_BACK_MAIN, BTN_BACK_SETTINGS, BTN_BACK_ADMIN, BTN_CHECK_PENALTIES,
    BTN_REDEEM_PENALTY, BTN_SWITCH_HOME, BTN_ADMIN_PANEL, BTN_SET_VACATION, BTN_EDIT_ENTRIES,
    BTN_CHANGE_USERNAME, BTN_MEMBER_MANAGEMENT, BTN_MANAGE_WEEKLY, BTN_ADJUST_PENALTIES, BTN_SET_HOECK,
    BTN_SYSTEM_OVERVIEW, BTN_SET_REPORT_TIME, BTN_TOGGLE_WEEKLY, BTN_TRIGGER_WEEKLY,
    BTN_MANAGE_ROLES, BTN_HOME_SETTINGS,
)
NAV_FILTER = (
    filters.Regex("^(" + "|".join(re.escape(label) for label in NAV_LABELS) + ")$")
    | filters.Regex(f"^{re.escape(BTN_MANAGE_PREFIX)}")
    | (filters.COMMAND & ~filters.Regex(r"^/cancel(@\w+)?(\s|$)"))
)


def _conversation_handlers(app: Application):
    return [h for h in app.handlers.get(0, []) if isinstance(h, ConversationHandler)]


async def _end_conversation(conv: ConversationHandler, key) -> None:
    """End one conversation for ``key`` and drop its pending timeout job.

    Uses ConversationHandler internals (``_conversations``, ``_update_state``,
    ``timeout_jobs``); python-telegram-bot is pinned in requirements.txt and
    tests/test_wiring.py asserts these attributes exist.
    """
    if key in conv._conversations:
        conv._update_state(ConversationHandler.END, key)
    async with conv._timeout_jobs_lock:
        job = conv.timeout_jobs.pop(key, None)
    if job is not None:
        job.schedule_removal()


async def handoff(update: Update, context: CallbackContext):
    """A menu button or command arrived mid-conversation.

    End every active conversation of this chat+user, clear the draft, and run
    the update again from the top so the button's real handler (or another
    conversation's entry point) handles it now, in-process. If the very same
    conversation was restarted by its own entry point, keep that new state.
    """
    app = context.application
    if update.effective_chat:
        end_flow(context, update.effective_chat.id)
    active = []
    for conv in _conversation_handlers(app):
        try:
            key = conv._get_key(update)
        except Exception:  # pragma: no cover - defensive against exotic updates
            continue
        if key in conv._conversations:
            active.append((conv, key))
        await _end_conversation(conv, key)
    await app.process_update(update)
    if any(key in conv._conversations for conv, key in active):
        return None  # restarted: leave the fresh state untouched
    return ConversationHandler.END


def _exact(label: str):
    return filters.Regex(f"^{re.escape(label)}$")


def _conversation(entry_label, states, *, allow_reentry=False, entry_filter=None):
    nav = MessageHandler(NAV_FILTER, handoff)
    guarded = {state: [nav, *handlers] for state, handlers in states.items() if state != "__entry__"}
    timeout_state = {ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, on_timeout)]}
    return ConversationHandler(
        entry_points=[MessageHandler(entry_filter or _exact(entry_label), states["__entry__"])],
        states={**guarded, **timeout_state},
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(_exact(BTN_CANCEL), cancel)],
        allow_reentry=allow_reentry,
        conversation_timeout=CONVERSATION_TIMEOUT,
    )


def _text_steps(*handlers, cancel_first=True):
    steps = [MessageHandler(_exact(BTN_CANCEL), cancel)] if cancel_first else []
    steps += [MessageHandler(filters.TEXT & ~filters.COMMAND, h) for h in handlers]
    return steps


def register_handlers(app: Application) -> None:
    # Conversations come first: while one is active it must see every update for
    # its chat+user, so a menu button pressed mid-draft reaches `handoff` instead
    # of a plain handler that would leave the stale conversation running.
    _register_conversations(app)

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newhome", newhome))
    app.add_handler(CommandHandler("deletehome", delete_home))
    app.add_handler(CommandHandler("join", join))
    app.add_handler(CommandHandler("expenses", list_expenses))
    app.add_handler(CommandHandler("chore", handle_chore))
    app.add_handler(CommandHandler("chores", list_chores))
    app.add_handler(CommandHandler("setstatus", set_vacation_status))
    app.add_handler(MessageHandler(filters.StatusUpdate.MIGRATE, on_migrate))

    # Plain menu buttons
    app.add_handler(MessageHandler(_exact(BTN_STANDINGS), standings))
    app.add_handler(MessageHandler(_exact(BTN_LIST_EXPENSES), list_expenses))
    app.add_handler(MessageHandler(_exact(BTN_LIST_CHORES), list_chores))
    app.add_handler(MessageHandler(_exact(BTN_PENALTIES), open_penalties))
    app.add_handler(MessageHandler(_exact(BTN_CHECK_PENALTIES), check_penalties))
    app.add_handler(MessageHandler(_exact(BTN_SET_VACATION), set_vacation_status))
    app.add_handler(MessageHandler(_exact(BTN_SETTINGS), open_settings))
    app.add_handler(MessageHandler(_exact(BTN_SWITCH_HOME), switch_home))
    app.add_handler(MessageHandler(_exact(BTN_BACK_MAIN), settings_back))
    app.add_handler(MessageHandler(_exact(BTN_ADMIN_PANEL), open_admin_menu))
    app.add_handler(MessageHandler(_exact(BTN_BACK_SETTINGS), back_to_settings))
    app.add_handler(MessageHandler(_exact(BTN_BACK_ADMIN), back_to_admin))
    app.add_handler(MessageHandler(_exact(BTN_SYSTEM_OVERVIEW), admin_system_overview))
    app.add_handler(MessageHandler(_exact(BTN_TRIGGER_WEEKLY), admin_trigger_report))
    app.add_handler(MessageHandler(_exact(BTN_TOGGLE_WEEKLY), admin_toggle_weekly))
    app.add_handler(MessageHandler(_exact(BTN_MANAGE_WEEKLY), open_weekly_report_menu))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_MANAGE_PREFIX)}"), open_manage_self))

    # Inline callbacks outside conversations
    app.add_handler(CallbackQueryHandler(home_select_cb, pattern=f"^{re.escape(CB_HOME_SELECT)}"))
    app.add_handler(CallbackQueryHandler(join_cb, pattern=JOIN_CALLBACK_PATTERN))
    app.add_handler(CallbackQueryHandler(delete_home_cb, pattern=HOME_DELETE_CALLBACK_PATTERN))

    # Stale inline keyboards (after a restart or a finished draft)
    async def _stale_cb(update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()
        await query.message.reply_text(
            "This keyboard has expired (the draft ended or the bot restarted). Please start again from the main menu.",
            reply_markup=ReplyKeyboardRemove(),
        )

    app.add_handler(CallbackQueryHandler(_stale_cb, pattern=RECEIPT_CALLBACK_PATTERN))
    app.add_handler(CallbackQueryHandler(_stale_cb, pattern=SPLIT_CALLBACK_PATTERN))
    app.add_handler(CallbackQueryHandler(_stale_cb, pattern=f"^{CB_PAYER_PREFIX}"))

    # Top-level cancel — registered LAST so each ConversationHandler's own fallback
    # gets first shot at /cancel and can properly end the conversation.
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(_exact(BTN_CANCEL), cancel))


def _register_conversations(app: Application) -> None:
    app.add_handler(_conversation(BTN_MANAGE_ROLES, {
        "__entry__": open_roles,
        ROLES_MENU: _text_steps(roles_menu, cancel_first=False),
        ROLES_PICK_USER: _text_steps(roles_pick, cancel_first=False),
    }, allow_reentry=True))

    app.add_handler(_conversation(BTN_HOME_SETTINGS, {
        "__entry__": open_home_settings,
        HOME_SETTINGS_MENU: _text_steps(home_settings_menu, cancel_first=False),
        HOME_SETTINGS_VALUE: _text_steps(home_settings_value, cancel_first=False),
    }, allow_reentry=True))

    app.add_handler(_conversation(BTN_ADJUST_PENALTIES, {
        "__entry__": admin_penalty_start,
        ADMIN_PENALTY_MEMBER: _text_steps(admin_penalty_member),
        ADMIN_PENALTY_COUNT: _text_steps(admin_penalty_count),
    }, allow_reentry=True))

    app.add_handler(_conversation(BTN_SET_HOECK, {
        "__entry__": admin_hoeck_start,
        ADMIN_HOECK_DATE: _text_steps(admin_hoeck_date),
    }, allow_reentry=True))

    app.add_handler(_conversation(BTN_SET_REPORT_TIME, {
        "__entry__": admin_report_time_start,
        ADMIN_REPORT_TIME: _text_steps(admin_report_time_set),
    }, allow_reentry=True))

    app.add_handler(_conversation(BTN_ADD_EXPENSE, {
        "__entry__": start_expense,
        EXPENSE_MODE: _text_steps(expense_mode_selection, cancel_first=False),
        EXPENSE_DESCRIPTION: _text_steps(expense_description, cancel_first=False),
        EXPENSE_AMOUNT: _text_steps(expense_amount, cancel_first=False),
        EXPENSE_RECEIPT: [
            MessageHandler(RECEIPT_IMAGE_FILTER, expense_receipt_photo),
            MessageHandler(_exact(BTN_CANCEL), cancel),
            MessageHandler(filters.TEXT & ~filters.COMMAND, expense_receipt_invalid),
        ],
        EXPENSE_RECEIPT_REVIEW: [CallbackQueryHandler(receipt_items_cb, pattern=RECEIPT_CALLBACK_PATTERN)],
        EXPENSE_PAYER: [CallbackQueryHandler(expense_payer_cb, pattern=f"^{CB_PAYER_PREFIX}")],
        EXPENSE_SPLIT: [CallbackQueryHandler(expense_split_cb, pattern=SPLIT_CALLBACK_PATTERN)],
    }))

    app.add_handler(_conversation(BTN_MEMBER_MANAGEMENT, {
        "__entry__": manage_members,
        MANAGE_MEMBER: _text_steps(modify_members, cancel_first=False),
        MANAGE_MEMBER_ACTION: _text_steps(member_action, cancel_first=False),
    }))

    app.add_handler(_conversation(BTN_CHANGE_USERNAME, {
        "__entry__": start_change_username,
        CHANGE_USERNAME_NEW: _text_steps(do_change_username),
    }, allow_reentry=True))

    app.add_handler(_conversation(BTN_EDIT_ENTRIES, {
        "__entry__": start_edit_entries,
        EDIT_PICK_MEMBER: _text_steps(edit_entries_pick_member, cancel_first=False),
        EDIT_MENU: _text_steps(edit_entries_menu, cancel_first=False),
        EDIT_AMOUNT: _text_steps(edit_entries_amount, cancel_first=False),
        EDIT_SPLIT: _text_steps(edit_entries_split, cancel_first=False),
    }))

    app.add_handler(_conversation(BTN_REDEEM_PENALTY, {
        "__entry__": redeem_start,
        REDEEM_MEMBER: _text_steps(redeem_member),
        REDEEM_COUNT: _text_steps(redeem_count),
    }, allow_reentry=True))

    app.add_handler(_conversation(BTN_ADD_CHORE, {
        "__entry__": start_chore,
        CHORE_USER: _text_steps(chore_user, cancel_first=False),
        CHORE_MINUTES: _text_steps(chore_minutes, cancel_first=False),
        CHORE_DESCRIPTION: _text_steps(chore_description, cancel_first=False),
    }))


def build_application() -> Application:
    store = get_store()
    try:
        outcome = store.initialize(legacy_seed())
    except StoreError as exc:
        logger.critical("Refusing to start: %s", exc)
        raise SystemExit(2) from exc
    logger.info("Store %s: %s (%d home(s))", store.path, outcome, len(store.list_homes()))

    persistence = PicklePersistence(filepath="bot_persistence.pkl")
    app = Application.builder().token(TOKEN).persistence(persistence).build()
    register_handlers(app)
    setup_jobs(app)
    return app


def main():
    app = build_application()
    logger.info("Bot running...")
    app.run_polling()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
