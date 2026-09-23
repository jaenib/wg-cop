"""Member management (admin), own profile, username change and vacation status."""
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.handlers.common import admin_home, cancel, continue_admin_flow, continue_flow
from wgcop.homes import HomeError, end_flow, remove_member, require_home, start_flow
from wgcop.keyboards import (
    CHANGE_USERNAME_NEW,
    MANAGE_MEMBER,
    get_cancel_keyboard,
    get_main_keyboard,
    get_manage_self_keyboard,
)
from wgcop.members import _match_member_name, _rename_member_in_data
from wgcop.store import get_store, member_by_name, member_for_uid, new_member

MAX_NAME_LEN = 40


def _member_summary(home) -> str:
    parts = []
    for m in home["members"]:
        flags = []
        if m.get("status") == "vacating":
            flags.append("vacating")
        flags.append("linked" if m.get("uid") else "not linked")
        parts.append(f"{m['name']} ({', '.join(flags)})")
    return ", ".join(parts)


# ------------------------------------------------------------ admin: members
async def manage_members(update: Update, context: CallbackContext) -> int:
    home = await admin_home(update, context)
    if home is None:
        return ConversationHandler.END
    start_flow(context, update.effective_chat.id, home)
    if home["members"]:
        txt = (
            f"Current members: {_member_summary(home)}\n\n"
            "Send a name to add it, or an existing name to remove it "
            "(members with bookkeeping history cannot be removed).\n"
            "Or type 'Back' to return without changes."
        )
    else:
        txt = "No members yet. Send a name to add. Or type 'Back' to return."
    await update.message.reply_text(
        txt, reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Back")]], resize_keyboard=True)
    )
    return MANAGE_MEMBER


async def modify_members(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_admin_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.lower() in {"back", "cancel"}:
        end_flow(context, update.effective_chat.id)
        await update.message.reply_text("Member management closed.", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    if len(text) > MAX_NAME_LEN:
        await update.message.reply_text(f"Names are limited to {MAX_NAME_LEN} characters.")
        return MANAGE_MEMBER

    existing = member_by_name(home, text)

    def apply(h):
        current = member_by_name(h, text)
        if current is not None:
            removed = remove_member(h, current["mid"])
            return f"Removed {removed['name']} from the household."
        h["members"].append(new_member(h, text))
        return f"Added {text} to the household."

    try:
        _, response = get_store().update(home["id"], apply)
    except HomeError as exc:
        await update.message.reply_text(str(exc))
        return MANAGE_MEMBER
    del existing
    end_flow(context, update.effective_chat.id)
    await update.message.reply_text(response, reply_markup=get_main_keyboard())
    return ConversationHandler.END


# ---------------------------------------------------------------- profile
async def open_manage_self(update: Update, context: CallbackContext) -> None:
    home = await require_home(update, context)
    if home is None:
        return
    member = member_for_uid(home, update.effective_user.id)
    if not member:
        await update.message.reply_text(
            "I can't match you to a member of this home. Send /join in the group to link your profile."
        )
        return
    await update.message.reply_text(f"Managing your profile: {member['name']}", reply_markup=get_manage_self_keyboard())


async def start_change_username(update: Update, context: CallbackContext) -> int:
    home = await require_home(update, context)
    if home is None:
        return ConversationHandler.END
    member = member_for_uid(home, update.effective_user.id)
    if not member:
        await update.message.reply_text(
            "I can't match you to a member of this home. Send /join in the group first.",
            reply_markup=get_manage_self_keyboard(),
        )
        return ConversationHandler.END
    flow = start_flow(context, update.effective_chat.id, home)
    flow["change_username_old"] = member["name"]
    await update.message.reply_text(
        f"Current username: {member['name']}\n\nSend your new username:", reply_markup=get_cancel_keyboard()
    )
    return CHANGE_USERNAME_NEW


async def do_change_username(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    new_name = update.message.text.strip()
    if new_name.lower() == "cancel":
        return await cancel(update, context)
    if not new_name or len(new_name) > MAX_NAME_LEN:
        await update.message.reply_text(f"Please send a name of 1–{MAX_NAME_LEN} characters:")
        return CHANGE_USERNAME_NEW
    old_name = flow.get("change_username_old", "")

    def apply(h):
        if _match_member_name(h["members"], new_name) is not None:
            return False
        _rename_member_in_data(h, old_name, new_name)
        return True

    _, ok = get_store().update(home["id"], apply)
    if not ok:
        await update.message.reply_text(f"'{new_name}' is already taken. Try a different name:")
        return CHANGE_USERNAME_NEW
    end_flow(context, update.effective_chat.id)
    await update.message.reply_text(f"Username changed to {new_name}!", reply_markup=get_manage_self_keyboard())
    return ConversationHandler.END


async def set_vacation_status(update: Update, context: CallbackContext) -> None:
    """Toggle vacation status. Button: yourself. /setstatus <name>: any member of the home."""
    home = await require_home(update, context)
    if home is None:
        return
    if context.args:
        target_name = " ".join(context.args)
        target = _match_member_name(home["members"], target_name)
        if target is None:
            await update.message.reply_text(f"Member '{target_name}' not found.")
            return
    else:
        target = member_for_uid(home, update.effective_user.id)
        if target is None:
            await update.message.reply_text(
                "I cannot match you to a member of this home. Send /join in the group to link your profile."
            )
            return

    mid = target["mid"]

    def apply(h):
        for m in h["members"]:
            if m["mid"] == mid:
                m["status"] = "vacating" if m.get("status", "active") == "active" else "active"
                return m["name"], m["status"]
        return None, None

    _, (name, new_status) = get_store().update(home["id"], apply)
    reply_kb = get_main_keyboard() if context.args else get_manage_self_keyboard()
    if new_status is None:
        await update.message.reply_text("That member no longer exists.", reply_markup=reply_kb)
    elif new_status == "vacating":
        await update.message.reply_text(
            f"{name} is now on vacation and will appear at the bottom when selecting splitters, "
            "as a reminder to only include them for long-term expenses.",
            reply_markup=reply_kb,
        )
    else:
        await update.message.reply_text(f"{name} is now active.", reply_markup=reply_kb)
