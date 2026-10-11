"""Admin -> Manage Roles: admins, chronicler, handler, member links, join requests.

Every role that used to be a fixed id in the secrets file is edited here and
stored with the home. Each step re-checks that the caller still administers
the home (continue_admin_flow).
"""
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.handlers.common import admin_home, continue_admin_flow
from wgcop.homes import (
    HomeError,
    add_admin,
    end_flow,
    remove_admin,
    set_single_role,
    start_flow,
    unlink_member,
)
from wgcop.keyboards import (
    BTN_BACK_ADMIN,
    BTN_CANCEL,
    BTN_NONE,
    BTN_ROLE_ADD_ADMIN,
    BTN_ROLE_REMOVE_ADMIN,
    BTN_ROLE_REQUESTS,
    BTN_ROLE_SET_CHRONICLER,
    BTN_ROLE_SET_HANDLER,
    BTN_ROLE_UNLINK,
    ROLES_MENU,
    ROLES_PICK_USER,
    build_join_review_kb,
    build_pick_keyboard,
    get_main_keyboard,
    get_roles_keyboard,
)
from wgcop.members import active_members
from wgcop.store import get_store, member_by_mid, member_for_uid


def _name_for_uid(home, uid) -> str:
    member = member_for_uid(home, uid)
    return member["name"] if member else f"user {uid}"


def roles_summary(home) -> str:
    roles = home.get("roles") or {}
    admins = ", ".join(_name_for_uid(home, uid) for uid in roles.get("admins", [])) or "(none)"
    chron = _name_for_uid(home, roles["chronicler"]) if roles.get("chronicler") else "not set"
    handler = _name_for_uid(home, roles["handler"]) if roles.get("handler") else "not set"
    linked = [m["name"] for m in active_members(home) if m.get("uid")]
    unlinked = [m["name"] for m in active_members(home) if not m.get("uid")]
    retired = [m["name"] for m in home["members"] if m.get("status") == "retired"]
    pending = len(home.get("join_requests") or {})
    return "\n".join(
        [
            f"Roles in '{home['name']}':",
            f"  Admins: {admins}",
            f"  Chronicler (receives backups): {chron}",
            f"  Handler (receives heartbeats): {handler}",
            f"  Linked members: {', '.join(linked) or '(none)'}",
            f"  Not linked yet: {', '.join(unlinked) or '(none)'}",
            *([f"  Retired: {', '.join(retired)}"] if retired else []),
            f"  Pending join requests: {pending}",
        ]
    )


async def open_roles(update: Update, context: CallbackContext) -> int:
    home = await admin_home(update, context)
    if home is None:
        return ConversationHandler.END
    start_flow(context, update.effective_chat.id, home)
    await update.message.reply_text(roles_summary(home), reply_markup=get_roles_keyboard())
    return ROLES_MENU


def _linked_members(home):
    return [m for m in active_members(home) if m.get("uid")]


async def roles_menu(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_admin_flow(update, context)
    if home is None:
        return ConversationHandler.END
    choice = update.message.text.strip()
    roles = home["roles"]

    if choice == BTN_ROLE_ADD_ADMIN:
        candidates = {m["name"]: m["uid"] for m in _linked_members(home) if m["uid"] not in roles["admins"]}
        if not candidates:
            await update.message.reply_text(
                "Every linked member is already an administrator. Members must /join first to become admins.",
                reply_markup=get_roles_keyboard(),
            )
            return ROLES_MENU
        prompt = "Who becomes an administrator?"
    elif choice == BTN_ROLE_REMOVE_ADMIN:
        candidates = {_name_for_uid(home, uid): uid for uid in roles["admins"]}
        prompt = "Which administrator should be removed?"
    elif choice in (BTN_ROLE_SET_CHRONICLER, BTN_ROLE_SET_HANDLER):
        candidates = {m["name"]: m["uid"] for m in _linked_members(home)}
        role = "chronicler" if choice == BTN_ROLE_SET_CHRONICLER else "handler"
        what = "receives the weekly archive of this home" if role == "chronicler" else "receives the bot's heartbeat"
        prompt = f"Who {what}? (only linked members can be chosen; '{BTN_NONE}' clears the role)"
    elif choice == BTN_ROLE_UNLINK:
        candidates = {m["name"]: m["mid"] for m in _linked_members(home)}
        if not candidates:
            await update.message.reply_text("No member is linked to a Telegram account.", reply_markup=get_roles_keyboard())
            return ROLES_MENU
        prompt = "Unlink which member from their Telegram account? (their bookkeeping stays)"
    elif choice == BTN_ROLE_REQUESTS:
        requests = home.get("join_requests") or {}
        if not requests:
            await update.message.reply_text("No pending join requests.", reply_markup=get_roles_keyboard())
            return ROLES_MENU
        for uid_text, req in requests.items():
            member = member_by_mid(home, req.get("mid"))
            target = member["name"] if member else "an unknown profile"
            handle = f"@{req['username']}" if req.get("username") else f"user {uid_text}"
            await update.message.reply_text(
                f"{req.get('display', 'Someone')} ({handle}) wants to be linked to '{target}'.",
                reply_markup=build_join_review_kb(f"{home['id']}:{uid_text}"),
            )
        await update.message.reply_text("Approve or deny each request above.", reply_markup=get_roles_keyboard())
        return ROLES_MENU
    elif choice == BTN_BACK_ADMIN:
        end_flow(context, update.effective_chat.id)
        return ConversationHandler.END
    else:
        await update.message.reply_text("Please choose one of the options above.", reply_markup=get_roles_keyboard())
        return ROLES_MENU

    flow["role_action"] = choice
    flow["role_candidates"] = candidates
    extra = [BTN_NONE] if choice in (BTN_ROLE_SET_CHRONICLER, BTN_ROLE_SET_HANDLER) else None
    await update.message.reply_text(prompt, reply_markup=build_pick_keyboard(list(candidates), extra))
    return ROLES_PICK_USER


async def roles_pick(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_admin_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text == BTN_CANCEL:
        await update.message.reply_text(roles_summary(home), reply_markup=get_roles_keyboard())
        return ROLES_MENU

    action = flow.get("role_action")
    candidates = flow.get("role_candidates") or {}
    if text != BTN_NONE and text not in candidates:
        await update.message.reply_text("Please pick a name from the keyboard.")
        return ROLES_PICK_USER
    target = None if text == BTN_NONE else candidates[text]
    caller_uid = update.effective_user.id

    def apply(h):
        if action == BTN_ROLE_ADD_ADMIN:
            add_admin(h, target)
            return f"{text} is now an administrator."
        if action == BTN_ROLE_REMOVE_ADMIN:
            remove_admin(h, target)
            return f"{text} is no longer an administrator."
        if action == BTN_ROLE_SET_CHRONICLER:
            set_single_role(h, "chronicler", target)
            return f"Chronicler: {text if target else 'cleared'}."
        if action == BTN_ROLE_SET_HANDLER:
            set_single_role(h, "handler", target)
            return f"Handler: {text if target else 'cleared'}."
        if action == BTN_ROLE_UNLINK:
            if target is None:
                raise HomeError("Pick a member to unlink.")
            member = unlink_member(h, target)
            return f"{member['name']} is no longer linked to a Telegram account."
        raise HomeError("Unknown action.")

    try:
        home, message = get_store().update(home["id"], apply)
    except HomeError as exc:
        await update.message.reply_text(str(exc), reply_markup=get_roles_keyboard())
        return ROLES_MENU

    if action == BTN_ROLE_REMOVE_ADMIN and target == caller_uid:
        end_flow(context, update.effective_chat.id)
        await update.message.reply_text(message + " You left the admin panel.", reply_markup=get_main_keyboard())
        return ConversationHandler.END

    if action == BTN_ROLE_SET_CHRONICLER and target:
        try:
            await context.bot.send_message(
                chat_id=target,
                text=f"You are now the chronicler of '{home['name']}': you will receive its archive backups here.",
            )
        except TelegramError:
            message += " (Could not message them yet; they need to /start a chat with me.)"

    await update.message.reply_text(message + "\n\n" + roles_summary(home), reply_markup=get_roles_keyboard())
    return ROLES_MENU
