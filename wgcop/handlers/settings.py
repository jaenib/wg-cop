"""Admin -> Home Settings: the constants that used to be literals in the source.

Name, currency label, timezone, minutes per chore point, penalty threshold and
the penalty unit are per home and take effect immediately (the sweep job reads
them on every pass). Weekly report time/enabled and the WG-Höck date have their
own menus.
"""
import pytz
from telegram import Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.handlers.common import admin_home, continue_admin_flow
from wgcop.homes import end_flow, start_flow
from wgcop.keyboards import (
    BTN_BACK_ADMIN,
    BTN_CANCEL,
    BTN_SETTING_CURRENCY,
    BTN_SETTING_MINUTES,
    BTN_SETTING_NAME,
    BTN_SETTING_THRESHOLD,
    BTN_SETTING_TIMEZONE,
    BTN_SETTING_UNIT,
    HOME_SETTINGS_MENU,
    HOME_SETTINGS_VALUE,
    get_cancel_keyboard,
    get_home_settings_keyboard,
)
from wgcop.store import get_store


class Invalid(ValueError):
    pass


def _text(value, limit, what):
    value = " ".join(str(value).split())
    if not value or len(value) > limit:
        raise Invalid(f"{what} must be 1–{limit} characters.")
    return value


def _int(value, lo, hi, what):
    try:
        number = int(str(value).strip())
    except ValueError as exc:
        raise Invalid(f"{what} must be a whole number between {lo} and {hi}.") from exc
    if not lo <= number <= hi:
        raise Invalid(f"{what} must be between {lo} and {hi}.")
    return number


def _timezone(value):
    name = str(value).strip()
    lookup = {tz.casefold(): tz for tz in pytz.all_timezones}
    if name.casefold() not in lookup:
        raise Invalid("Unknown timezone. Use an IANA name such as Europe/Berlin, Europe/Zurich or America/New_York.")
    return lookup[name.casefold()]


# label -> (where, key, parser, hint)
FIELDS = {
    BTN_SETTING_NAME: ("home", "name", lambda v: _text(v, 40, "The home name"), "e.g. 'WG Sonnenberg'"),
    BTN_SETTING_CURRENCY: ("settings", "currency", lambda v: _text(v, 8, "The currency label"), "e.g. CHF, EUR or €"),
    BTN_SETTING_TIMEZONE: ("settings", "timezone", _timezone, "IANA name, e.g. Europe/Berlin"),
    BTN_SETTING_MINUTES: (
        "settings", "minutes_per_point", lambda v: _int(v, 1, 240, "Minutes per point"),
        "how many chore minutes earn one point (default 15)",
    ),
    BTN_SETTING_THRESHOLD: (
        "settings", "penalty_threshold_points", lambda v: _int(v, 1, 100, "The penalty threshold"),
        "points behind the leader before a penalty (default 4)",
    ),
    BTN_SETTING_UNIT: ("settings", "penalty_unit", lambda v: _text(v, 20, "The penalty unit"), "e.g. beer, pizza, coffee"),
}


def _current(home, label):
    where, key, _, _ = FIELDS[label]
    return home[key] if where == "home" else (home.get("settings") or {}).get(key)


def settings_summary(home) -> str:
    s = home.get("settings") or {}
    return "\n".join(
        [
            f"Settings of '{home['name']}':",
            f"  {BTN_SETTING_CURRENCY}: {s.get('currency', 'CHF')}",
            f"  {BTN_SETTING_TIMEZONE}: {s.get('timezone', 'Europe/Berlin')}",
            f"  {BTN_SETTING_MINUTES}: {s.get('minutes_per_point', 15)}",
            f"  {BTN_SETTING_THRESHOLD}: {s.get('penalty_threshold_points', 4)} points",
            f"  {BTN_SETTING_UNIT}: {s.get('penalty_unit', 'beer')}",
            f"  Weekly report: {'enabled' if s.get('weekly_report_enabled', True) else 'disabled'} at "
            f"{s.get('weekly_report_time', '09:30')} (Manage Weekly Report)",
            f"  WG-Höck date: {s.get('wg_hoeck_date') or 'not set'} (Set WG-Höck)",
        ]
    )


async def open_home_settings(update: Update, context: CallbackContext) -> int:
    home = await admin_home(update, context)
    if home is None:
        return ConversationHandler.END
    start_flow(context, update.effective_chat.id, home)
    await update.message.reply_text(settings_summary(home), reply_markup=get_home_settings_keyboard())
    return HOME_SETTINGS_MENU


async def home_settings_menu(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_admin_flow(update, context)
    if home is None:
        return ConversationHandler.END
    choice = update.message.text.strip()
    if choice == BTN_BACK_ADMIN:
        end_flow(context, update.effective_chat.id)
        return ConversationHandler.END
    if choice not in FIELDS:
        await update.message.reply_text("Please choose one of the options above.", reply_markup=get_home_settings_keyboard())
        return HOME_SETTINGS_MENU
    flow["setting"] = choice
    hint = FIELDS[choice][3]
    await update.message.reply_text(
        f"{choice} is currently: {_current(home, choice)}\nSend the new value ({hint}):",
        reply_markup=get_cancel_keyboard(),
    )
    return HOME_SETTINGS_VALUE


async def home_settings_value(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_admin_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    label = flow.get("setting")
    if text == BTN_CANCEL or label not in FIELDS:
        await update.message.reply_text(settings_summary(home), reply_markup=get_home_settings_keyboard())
        return HOME_SETTINGS_MENU
    where, key, parse, _ = FIELDS[label]
    try:
        value = parse(text)
    except Invalid as exc:
        await update.message.reply_text(str(exc))
        return HOME_SETTINGS_VALUE

    def apply(h):
        if where == "home":
            h[key] = value
        else:
            h.setdefault("settings", {})[key] = value

    home, _ = get_store().update(home["id"], apply)
    flow.pop("setting", None)
    await update.message.reply_text(
        f"{label} set to {value}.\n\n" + settings_summary(home), reply_markup=get_home_settings_keyboard()
    )
    return HOME_SETTINGS_MENU
