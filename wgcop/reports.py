"""Weekly report: construction, penalty bookkeeping and due-time logic.

All functions take a home dict (or a legacy data dict with the same keys).
Per-home settings (currency, timezone, penalty threshold, penalty unit) are
read from ``home["settings"]`` with the historical defaults, so legacy-shaped
dicts keep working in tests.

Generating a report mutates penalty state exactly once; delivery is a separate
concern handled by the sweep job, which keeps the rendered text in
``weekly_report_meta.pending`` until it reaches the group.
"""
import logging
from datetime import datetime, timedelta

import pytz

from wgcop.members import _get_member_name, _normalise_member_name, active_members

logger = logging.getLogger(__name__)

DEFAULT_TZ = pytz.timezone("Europe/Berlin")


def _settings(data) -> dict:
    return data.get("settings") or {}


def report_tz(data):
    name = _settings(data).get("timezone") or "Europe/Berlin"
    try:
        return pytz.timezone(name)
    except pytz.UnknownTimeZoneError:
        return DEFAULT_TZ


def penalty_threshold(data) -> int:
    value = _settings(data).get("penalty_threshold_points", 4)
    return value if isinstance(value, int) and value > 0 else 4


def penalty_unit(data) -> str:
    return _settings(data).get("penalty_unit") or "beer"


def report_currency(data) -> str:
    return _settings(data).get("currency") or "CHF"


def _get_chronicler_meta(data):
    return data.setdefault("chronicler_backup", {"greeting_sent": False, "last_sent": None})


def _get_weekly_report_meta(data):
    return data.setdefault("weekly_report_meta", {"last_sent": None})


def _parse_report_time(time_str):
    """Parse HH:MM string into (hour, minute) tuple, defaulting to (9, 30)."""
    try:
        h, m = time_str.strip().split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        return h, m
    except Exception:
        return 9, 30


def weekly_report_time(data) -> str:
    """HH:MM from settings (new) or the legacy top-level key."""
    return _settings(data).get("weekly_report_time") or data.get("weekly_report_time") or "09:30"


# ------------------------------------------------------------ time helpers
def _chore_entries_this_week(chore_log, tz=None, reference=None):
    tz = tz or DEFAULT_TZ
    now = reference or datetime.now(tz)
    cutoff = now - timedelta(days=7)
    result = []
    for entry in chore_log or []:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts.tzinfo is None:
                ts = tz.localize(ts)
        except (KeyError, ValueError, TypeError):
            continue
        if ts >= cutoff:
            result.append(entry)
    return result


def _expenses_in_period(expenses, days=28, tz=None, reference=None):
    tz = tz or DEFAULT_TZ
    now = reference or datetime.now(tz)
    cutoff = (now - timedelta(days=days)).date()
    result = []
    for e in expenses or []:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError, TypeError):
            continue
        if d >= cutoff:
            result.append(e)
    return result


def _weekly_report_target_for_week(reference=None, hour=9, minute=30, tz=None):
    tz = tz or DEFAULT_TZ
    now = reference or datetime.now(tz)
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _parse_local_datetime(value, tz=None):
    tz = tz or DEFAULT_TZ
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return tz.localize(parsed)
    return parsed.astimezone(tz)


_parse_berlin_datetime = _parse_local_datetime  # old name


def week_id(reference) -> str:
    """Local Monday date identifying the report week of ``reference``."""
    monday = reference - timedelta(days=reference.weekday())
    return monday.date().isoformat()


def _weekly_report_is_due(data, reference=None):
    """True when this week's report has not been generated yet and its time has passed.

    Idempotence is keyed by week (``generated_week``), so moving the report
    time later in the same week never generates or charges twice. Legacy
    metadata without ``generated_week`` falls back to ``last_sent`` compared
    against the start of the current week.
    """
    tz = report_tz(data)
    now = reference or datetime.now(tz)
    hour, minute = _parse_report_time(weekly_report_time(data))
    target = _weekly_report_target_for_week(now, hour=hour, minute=minute, tz=tz)
    meta = _get_weekly_report_meta(data)
    this_week = week_id(now)
    if meta.get("generated_week") == this_week:
        return False
    if meta.get("generated_week") is None:
        last_sent = _parse_local_datetime(meta.get("last_sent"), tz)
        week_start = target.replace(hour=0, minute=0, second=0, microsecond=0)
        if last_sent is not None and last_sent >= week_start:
            return False
    return now >= target


# ---------------------------------------------------------------- content
def _build_expense_fun_facts(expenses, currency="CHF", tz=None, reference=None):
    recent = _expenses_in_period(expenses, days=28, tz=tz, reference=reference)
    if not recent:
        return None

    payer_count = {}
    payer_total = {}
    for e in recent:
        payer = e.get("payer", "?")
        try:
            amount = float(e.get("amount", 0) or 0)
        except (TypeError, ValueError):
            amount = 0.0
        payer_count[payer] = payer_count.get(payer, 0) + 1
        payer_total[payer] = payer_total.get(payer, 0) + amount

    total_spend = sum(payer_total.values())
    total_entries = sum(payer_count.values())

    lines = [f"Expense snapshot (last 28 days): {currency} {total_spend:.2f} across {total_entries} entries"]

    if len(payer_count) >= 2:
        most_frequent = max(payer_count, key=payer_count.get)
        lines.append(f"  • {most_frequent} paid most often ({payer_count[most_frequent]}x)")

        avg_per = {p: payer_total[p] / payer_count[p] for p in payer_count}
        highest = max(avg_per, key=avg_per.get)
        lowest = min(avg_per, key=avg_per.get)
        if highest != lowest:
            lines.append(
                f"  • Avg per entry: {highest} {currency} {avg_per[highest]:.2f} vs {lowest} {currency} {avg_per[lowest]:.2f}"
            )

        top_payer = max(payer_total, key=payer_total.get)
        share_pct = payer_total[top_payer] / total_spend * 100 if total_spend else 0
        if share_pct >= 40:
            lines.append(f"  • {top_payer} covered {share_pct:.0f}% of total spending")
    elif len(payer_count) == 1:
        sole = next(iter(payer_count))
        lines.append(f"  • {sole} paid for everything this month – someone owes them!")

    return "\n".join(lines)


def _build_weekly_report(data, reference=None):
    """Build the weekly report text, mutating data to apply penalty changes."""
    tz = report_tz(data)
    now = reference or datetime.now(tz)
    threshold = penalty_threshold(data)
    unit = penalty_unit(data)
    currency = report_currency(data)

    chores_normalized = {}
    for chore_user, points in (data.get("chores") or {}).items():
        for member in active_members(data):
            member_name = _get_member_name(member)
            if _normalise_member_name(member_name) == _normalise_member_name(chore_user):
                chores_normalized[member_name] = chores_normalized.get(member_name, 0) + points
                break

    member_names = [_get_member_name(m) for m in active_members(data)]
    leaderboard = sorted(
        [(name, chores_normalized.get(name, 0)) for name in member_names],
        key=lambda x: -x[1],
    )

    if not leaderboard:
        return "Mario's Monday Mauling: No data yet."

    leader, leader_points = leaderboard[0]
    current_date = now.strftime("%Y-%m-%d")
    sections = [f"Mario's Monday Mauling, {current_date}", f"Leader: {leader} with {leader_points} points"]

    # --- Standings & penalties ---
    penalty_lines = []
    comeback_kids = []
    currently_penalised = set()
    violators = data.setdefault("last_week_violators", {})
    for member, points in leaderboard[1:]:
        key = _normalise_member_name(member)
        if leader_points - points > threshold:
            currently_penalised.add(member)
            if violators.get(key, False):
                weeks_lagging = data.setdefault("penalties", {}).get(member, 0) + 1
                data["penalties"][member] = weeks_lagging
                penalty_lines.append(f"  • {member} owes {weeks_lagging} {unit}(s)!")
            else:
                violators[key] = True
                penalty_lines.append(
                    f"  • {member} is {leader_points - points} pts behind {leader} — shape up or {unit} incoming!"
                )
        elif key in violators:
            violators.pop(key, None)
            comeback_kids.append(member)

    penalties = data.get("penalties", {}) or {}
    active_norm = {_normalise_member_name(name) for name in member_names}
    for member, owed in penalties.items():
        if owed > 0 and member not in currently_penalised and _normalise_member_name(member) in active_norm:
            penalty_lines.append(f"  • {member} still owes {owed} {unit}(s) (caught up, debt remains).")

    if penalty_lines:
        sections.append("Standings & Penalties:\n" + "\n".join(penalty_lines))
    else:
        sections.append("Everyone is keeping up — no penalties this week!")

    if comeback_kids:
        names = ", ".join(comeback_kids)
        sections.append(f"Comeback of the week: {names} turned it around after lagging last week!")

    # --- Big moves this week ---
    week_entries = _chore_entries_this_week(data.get("chore_log") or [], tz=tz, reference=now)
    member_week_pts = {}
    member_week_entries = {}
    for e in week_entries:
        member = e.get("member", "?")
        member_week_pts[member] = member_week_pts.get(member, 0) + e.get("points", 0)
        member_week_entries.setdefault(member, []).append(e)
    minutes_per_point = _settings(data).get("minutes_per_point", 15)
    big_movers = {m: pts for m, pts in member_week_pts.items() if pts > threshold}
    if big_movers:
        leap_lines = []
        for member, pts in sorted(big_movers.items(), key=lambda x: -x[1]):
            leap_lines.append(f"  • {member}: {pts * minutes_per_point} min total")
            descs = [
                e.get("description", "").strip()
                for e in member_week_entries[member]
                if e.get("description", "").strip()
            ]
            if descs:
                leap_lines.append(f"  ({', '.join(descs)})")
        sections.append("Big moves this week:\n" + "\n".join(leap_lines))

    fun_facts = _build_expense_fun_facts(data.get("expenses") or [], currency=currency, tz=tz, reference=now)
    if fun_facts:
        sections.append(fun_facts)

    # --- WG-Höck reminder ---
    hoeck_date_str = _settings(data).get("wg_hoeck_date") or data.get("wg_hoeck_date")
    if hoeck_date_str:
        try:
            hoeck_date = datetime.strptime(hoeck_date_str, "%Y-%m-%d").date()
            days_until = (hoeck_date - now.date()).days
            if 0 <= days_until <= 7:
                day_name = hoeck_date.strftime("%A, %d.%m.")
                if days_until == 0:
                    sections.append("📅 WG-Höck is TODAY!")
                else:
                    sections.append(f"📅 WG-Höck this week: {day_name} ({days_until} day{'s' if days_until != 1 else ''} away)")
        except ValueError:
            pass

    return "\n\n".join(sections)


def generate_weekly_report(home, reference=None) -> str:
    """Apply this week's penalties once and park the text for delivery.

    Meant to run inside ``Store.update`` (synchronous). Returns the text.
    """
    tz = report_tz(home)
    now = reference or datetime.now(tz)
    if not home.get("members") or not home.get("chores"):
        text = "Weekly Report: Not enough data yet. Add members and log some chores first."
    else:
        text = _build_weekly_report(home, reference=now)
    meta = _get_weekly_report_meta(home)
    if meta.get("pending"):
        logger.warning("Home %s: previous weekly report was never delivered; replacing it", home.get("id"))
    meta["last_sent"] = now.isoformat()
    meta["generated_week"] = week_id(now)
    meta["pending"] = text
    meta["pending_since"] = now.isoformat()
    meta["attempts"] = 0
    meta["next_try"] = None
    return text
