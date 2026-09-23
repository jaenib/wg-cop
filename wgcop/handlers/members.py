"""Member management, profile and vacation-status handlers (relocated verbatim)."""
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.config import BOT_HANDLER_ID
from wgcop.identity import _resolve_member_for_user
from wgcop.keyboards import (
    CHANGE_USERNAME_NEW,
    MANAGE_MEMBER,
    get_main_keyboard,
    get_manage_self_keyboard,
)
from wgcop.members import (
    _get_member_name,
    _get_member_status,
    _match_member_name,
    _member_to_dict,
    _normalise_member_name,
    _rename_member_in_data,
)
from wgcop.store import load_data, save_data

async def open_manage_self(update: Update, context: CallbackContext) -> None:
    member = _resolve_member_for_user(update.effective_user)
    if not member:
        await update.message.reply_text(
            "I can't match you to a household member. Ask an admin to add you."
        )
        return
    username = _get_member_name(member)
    await update.message.reply_text(
        f"Managing your profile: {username}",
        reply_markup=get_manage_self_keyboard(),
    )


# Manage Members
async def manage_members(update: Update, context: CallbackContext) -> int:
    if update.effective_user.id != BOT_HANDLER_ID:
        await update.message.reply_text("Unauthorized.")
        return ConversationHandler.END
    data = load_data()
    if data["members"]:
        members_list = ", ".join(_get_member_name(m) for m in data["members"])
        txt = (
            f"Current members: {members_list}\n\n"
            "Send a name to add/remove.\n"
            "Or type 'Back' to return without changes."
        )
    else:
        txt = "No members yet. Send a name to add. Or type 'Back' to return."

    await update.message.reply_text(
        txt,
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Back")]], resize_keyboard=True),
    )
    return MANAGE_MEMBER


async def modify_members(update: Update, context: CallbackContext) -> int:
    data = load_data()
    text = update.message.text.strip()
    if text.lower() == "back":
        await update.message.reply_text(
            "Member management closed.", reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    name_ci = text.lower()
    existing_index = next(
        (i for i, m in enumerate(data["members"]) if _normalise_member_name(m) == name_ci), None
    )
    if existing_index is not None:
        removed_member = data["members"].pop(existing_index)
        removed_name = _get_member_name(removed_member)
        response = f"Removed {removed_name} from the household."
    else:
        data["members"].append(_member_to_dict(text))
        response = f"Added {text} to the household."

    save_data(data)
    await update.message.reply_text(response, reply_markup=get_main_keyboard())
    return ConversationHandler.END


async def start_change_username(update: Update, context: CallbackContext) -> int:
    member = _resolve_member_for_user(update.effective_user)
    if not member:
        await update.message.reply_text(
            "I can't match you to a household member.",
            reply_markup=get_manage_self_keyboard(),
        )
        return ConversationHandler.END
    current_name = _get_member_name(member)
    context.user_data["change_username_old"] = current_name
    await update.message.reply_text(
        f"Current username: {current_name}\n\nSend your new username:",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Cancel")]], resize_keyboard=True),
    )
    return CHANGE_USERNAME_NEW


async def do_change_username(update: Update, context: CallbackContext) -> int:
    new_name = update.message.text.strip()
    old_name = context.user_data.get("change_username_old", "")
    data = load_data()

    if _match_member_name(data.get("members") or [], new_name):
        await update.message.reply_text(
            f"'{new_name}' is already taken. Try a different name:"
        )
        return CHANGE_USERNAME_NEW

    _rename_member_in_data(data, old_name, new_name)
    save_data(data)
    context.user_data.clear()

    await update.message.reply_text(
        f"Username changed to {new_name}!",
        reply_markup=get_manage_self_keyboard(),
    )
    return ConversationHandler.END


async def set_vacation_status(update: Update, context: CallbackContext) -> None:
    """Toggle vacation status. Usage: /setstatus or /setstatus <name>"""
    data = load_data()
    members = data.get("members", []) or []
    
    # Check if a name was provided as argument
    if context.args:
        # Admin mode: set status for specified member
        member_name_arg = " ".join(context.args)
        member_match = _match_member_name(members, member_name_arg)
        if not member_match:
            await update.message.reply_text(
                f"Member '{member_name_arg}' not found."
            )
            return
    else:
        # Auto-identify caller by Chat ID
        member_uid = _resolve_member_for_user(update.effective_user)
        if not member_uid:
            await update.message.reply_text(
                "I cannot match you to a household member. Please ask to be added to the member list."
            )
            return
        
        # Get the member name from the resolved UID
        member_name_arg = _get_member_name(member_uid)
        member_match = _match_member_name(members, member_name_arg)
        if not member_match:
            await update.message.reply_text(
                "I cannot match you to a household member. Please ask to be added to the member list."
            )
            return
    
    member_name = _get_member_name(member_match)
    current_status = _get_member_status(member_match)
    new_status = "vacating" if current_status == "active" else "active"
    
    # Update the member in the list
    for member in members:
        if _normalise_member_name(member) == _normalise_member_name(member_match):
            if isinstance(member, dict):
                member["status"] = new_status
            else:
                # Convert old string format to dict
                idx = members.index(member)
                members[idx] = {"name": member, "status": new_status}
            break
    
    save_data(data)

    # Return to manage-self submenu when button-triggered; main menu for /setstatus commands
    reply_kb = get_main_keyboard() if context.args else get_manage_self_keyboard()

    if new_status == "vacating":
        await update.message.reply_text(
            f"{member_name}, you are now on vacation. "
            f"You will appear at the bottom when selecting splitters, "
            f"to remind others to only include you for long-term expenses.",
            reply_markup=reply_kb,
        )
    else:
        await update.message.reply_text(
            f"{member_name}, you are now active.",
            reply_markup=reply_kb,
        )
