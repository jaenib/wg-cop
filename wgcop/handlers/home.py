"""Homes: /newhome, /join (with admin-approved claims), home selection, group migration."""
import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import CallbackContext

from wgcop.handlers.common import answers_callback
from wgcop.homes import (
    CB_HOME_SELECT,
    HomeError,
    approve_claim,
    build_home_picker,
    caller_from_update,
    deny_claim,
    end_flow,
    flow_state,
    is_group_admin,
    is_group_chat,
    is_group_member,
    join_as_new_member,
    request_claim,
    unclaimed_members,
)
from wgcop.keyboards import (
    CB_JOIN_APPROVE_PREFIX,
    CB_JOIN_CANCEL,
    CB_JOIN_CLAIM_PREFIX,
    CB_JOIN_DENY_PREFIX,
    CB_JOIN_NEW,
    build_join_choice_kb,
    build_join_review_kb,
    get_main_keyboard,
)
from wgcop.store import StoreError, get_store, has_access, is_admin, member_by_mid, member_for_uid

logger = logging.getLogger(__name__)

MAX_HOME_NAME = 40


# ---------------------------------------------------------------- /newhome
async def newhome(update: Update, context: CallbackContext) -> None:
    chat = update.effective_chat
    caller = caller_from_update(update)
    if caller is None:
        await update.message.reply_text("I need a personal Telegram account to create a home.")
        return
    if not is_group_chat(chat):
        await update.message.reply_text("Run /newhome inside your household's Telegram group (with me added to it).")
        return
    store = get_store()
    existing = store.home_for_group(chat.id)
    if existing is not None:
        await update.message.reply_text(f"This group already has the home '{existing['name']}'. Send /join to get set up.")
        return
    if not await is_group_admin(context.bot, chat.id, caller.uid):
        await update.message.reply_text("Only an administrator of this Telegram group can create its home.")
        return
    name = " ".join(context.args).strip() if context.args else ""
    name = (name or chat.title or "Home").strip()[:MAX_HOME_NAME]
    try:
        home = store.create_home(chat.id, name, caller.uid, caller.name)
    except StoreError as exc:
        await update.message.reply_text(f"Could not create the home: {exc}")
        return
    await update.message.reply_text(
        f"Home '{home['name']}' created. You are its first administrator and member "
        f"('{home['members'][0]['name']}'). Everyone else: send /join here.",
        reply_markup=get_main_keyboard(),
    )


# ------------------------------------------------------------------- /join
async def _offer_join(update, context, home, caller) -> None:
    unclaimed = unclaimed_members(home)
    sent = await update.message.reply_text(
        f"Joining '{home['name']}'. Are you a new member, or one of the existing profiles?",
        reply_markup=build_join_choice_kb(unclaimed, caller.name),
    )
    flow_state(context, update.effective_chat.id)["join"] = {
        "home_id": home["id"],
        "kb_msg": getattr(sent, "message_id", None),
    }


async def join(update: Update, context: CallbackContext) -> None:
    chat = update.effective_chat
    caller = caller_from_update(update)
    if caller is None:
        await update.message.reply_text("I need a personal Telegram account to link you to a home.")
        return
    store = get_store()

    if is_group_chat(chat):
        home = store.home_for_group(chat.id)
        if home is None:
            await update.message.reply_text("This group has no home yet. A group administrator can create one with /newhome <name>.")
            return
        linked = member_for_uid(home, caller.uid)
        if linked is not None:
            await update.message.reply_text(f"You are already linked as '{linked['name']}' in '{home['name']}'.")
            return
        if str(caller.uid) in (home.get("join_requests") or {}):
            await update.message.reply_text("Your request is waiting for an administrator's approval.")
            return
        await _offer_join(update, context, home, caller)
        return

    candidates = []
    for home in store.list_homes():
        if member_for_uid(home, caller.uid) is not None:
            continue
        if await is_group_member(context.bot, home["group_chat_id"], caller.uid):
            candidates.append(home)
    if not candidates:
        await update.message.reply_text(
            "I couldn't find a home whose Telegram group you are in. Send /join inside your household's group."
        )
        return
    if len(candidates) > 1:
        names = ", ".join(f"'{h['name']}'" for h in candidates)
        await update.message.reply_text(f"You are in several groups with homes ({names}). Send /join inside the group you want to join.")
        return
    await _offer_join(update, context, candidates[0], caller)


async def _notify_admins(context, home, text, reply_markup=None) -> None:
    for uid in (home.get("roles") or {}).get("admins", []):
        try:
            await context.bot.send_message(chat_id=uid, text=text, reply_markup=reply_markup)
        except TelegramError as exc:
            logger.info("Home %s: could not DM admin %s: %s", home["id"], uid, exc)


@answers_callback
async def join_cb(update: Update, context: CallbackContext, ack) -> None:
    query = update.callback_query
    caller = caller_from_update(update)
    if caller is None:
        return
    data = query.data
    store = get_store()

    # ---- admin review buttons: join:approve:<hid>:<uid> / join:deny:<hid>:<uid>
    if data.startswith(CB_JOIN_APPROVE_PREFIX) or data.startswith(CB_JOIN_DENY_PREFIX):
        approve = data.startswith(CB_JOIN_APPROVE_PREFIX)
        rest = data[len(CB_JOIN_APPROVE_PREFIX if approve else CB_JOIN_DENY_PREFIX):]
        try:
            hid, uid_text = rest.split(":", 1)
            uid = int(uid_text)
            home = store.get_home(hid)
        except (ValueError, StoreError):
            await query.edit_message_text("That request no longer exists.")
            return
        if not is_admin(home, caller.uid):
            await ack("Only a home administrator can review requests.", show_alert=True)
            return
        try:
            if approve:
                _, member = store.update(hid, lambda h: approve_claim(h, uid))
                await query.edit_message_text(f"Approved: user {uid} is now linked to '{member['name']}' in '{home['name']}'.")
                note = f"Your request was approved. You are now '{member['name']}' in '{home['name']}'."
            else:
                store.update(hid, lambda h: deny_claim(h, uid))
                await query.edit_message_text(f"Denied the request from user {uid}.")
                note = f"Your request to claim a profile in '{home['name']}' was denied. You can /join as a new member."
        except HomeError as exc:
            await query.edit_message_text(str(exc))
            return
        try:
            await context.bot.send_message(chat_id=uid, text=note, reply_markup=get_main_keyboard())
        except TelegramError:
            pass
        return

    # ---- the joining user's own buttons
    chat_id = update.effective_chat.id
    flow = flow_state(context, chat_id)
    info = flow.get("join")
    tapped = getattr(query.message, "message_id", None)
    if not info or info.get("kb_msg") is None or tapped is None or info["kb_msg"] != tapped:
        await ack("This join dialog is not yours. Send /join to start your own.", show_alert=True)
        return
    try:
        home = store.get_home(info["home_id"])
    except StoreError:
        flow.pop("join", None)
        await query.edit_message_text("That home no longer exists.")
        return

    if data == CB_JOIN_CANCEL:
        flow.pop("join", None)
        await query.edit_message_text("Join cancelled.")
        return

    # The user may have left the group since the dialog was shown.
    if not await is_group_member(context.bot, home["group_chat_id"], caller.uid):
        flow.pop("join", None)
        await query.edit_message_text(f"You are not a member of the Telegram group of '{home['name']}' (any more).")
        return

    try:
        if data == CB_JOIN_NEW:
            _, member = store.update(home["id"], lambda h: join_as_new_member(h, caller))
            flow.pop("join", None)
            await query.edit_message_text(f"Welcome! You are now '{member['name']}' in '{home['name']}'.")
            await query.message.reply_text("Use the buttons below:", reply_markup=get_main_keyboard())
            return
        if data.startswith(CB_JOIN_CLAIM_PREFIX):
            mid = data[len(CB_JOIN_CLAIM_PREFIX):]
            _, member = store.update(home["id"], lambda h: request_claim(h, caller, mid))
            flow.pop("join", None)
            await query.edit_message_text(
                f"Request sent: you asked to be linked to '{member['name']}'. An administrator of '{home['name']}' has to approve it."
            )
            handle = f"@{caller.username}" if caller.username else f"user {caller.uid}"
            await _notify_admins(
                context,
                home,
                f"Join request in '{home['name']}': {caller.name} ({handle}) wants to be linked to '{member['name']}'.",
                reply_markup=build_join_review_kb(f"{home['id']}:{caller.uid}"),
            )
            return
    except HomeError as exc:
        flow.pop("join", None)
        await query.edit_message_text(str(exc))


# ----------------------------------------------------------- home picker
@answers_callback
async def home_select_cb(update: Update, context: CallbackContext, ack) -> None:
    query = update.callback_query
    caller = caller_from_update(update)
    if caller is None:
        return
    hid = query.data[len(CB_HOME_SELECT):]
    store = get_store()
    try:
        home = store.get_home(hid)
        if not has_access(home, caller.uid):
            raise StoreError("no access")
        store.select_home(caller.uid, hid)
    except StoreError:
        await ack("You do not have access to that home.", show_alert=True)
        return
    chat_id = update.effective_chat.id
    end_flow(context, chat_id)  # a draft from the previous home must not leak into this one
    await query.edit_message_text(f"Now using '{home['name']}'.")
    await query.message.reply_text("Use the buttons below:", reply_markup=get_main_keyboard())


async def switch_home(update: Update, context: CallbackContext) -> None:
    caller = caller_from_update(update)
    if caller is None:
        return
    homes = get_store().homes_for_user(caller.uid)
    if not homes:
        await update.message.reply_text("You are not in any home yet.")
        return
    end_flow(context, update.effective_chat.id)  # only this chat's draft; group drafts stay
    await update.message.reply_text("Which home do you want to use in this chat?", reply_markup=build_home_picker(homes))


# ------------------------------------------------------- group migration
async def on_migrate(update: Update, context: CallbackContext) -> None:
    """Telegram upgraded a group to a supergroup: keep the home under its new id."""
    message = update.effective_message
    new_id = getattr(message, "migrate_to_chat_id", None)
    if not new_id or not update.effective_chat:
        return
    try:
        home = get_store().migrate_group(update.effective_chat.id, new_id)
    except StoreError as exc:
        logger.error("Group migration %s -> %s failed: %s", update.effective_chat.id, new_id, exc)
        return
    if home is not None:
        logger.info("Home '%s' re-keyed from %s to %s", home["name"], update.effective_chat.id, new_id)


def member_display(home, uid) -> str:
    member = member_for_uid(home, uid) or member_by_mid(home, uid)
    return member["name"] if member else f"user {uid}"
