"""Weekly report construction, due logic and delivery (relocated verbatim)."""
import logging
from datetime import datetime, timedelta

import pytz
from telegram.error import TelegramError
from telegram.ext import CallbackContext

from wgcop.members import _get_member_name, _normalise_member_name
from wgcop.store import save_data

logger = logging.getLogger(__name__)

def _chore_entries_this_week(chore_log):
    tz = pytz.timezone("Europe/Berlin")
    cutoff = datetime.now(tz) - timedelta(days=7)
    result = []
    for entry in chore_log or []:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts.tzinfo is None:
                ts = tz.localize(ts)
        except (KeyError, ValueError):
            continue
        if ts >= cutoff:
            result.append(entry)
    return result


def _expenses_in_period(expenses, days=28):
    cutoff = datetime.now() - timedelta(days=days)
    result = []
    for e in expenses or []:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        if d >= cutoff:
            result.append(e)
    return result


def _build_expense_fun_facts(expenses):
    recent = _expenses_in_period(expenses, days=28)
    if not recent:
        return None

    payer_count = {}
    payer_total = {}
    for e in recent:
        payer = e.get("payer", "?")
        amount = float(e.get("amount", 0))
        payer_count[payer] = payer_count.get(payer, 0) + 1
        payer_total[payer] = payer_total.get(payer, 0) + amount

    total_spend = sum(payer_total.values())
    total_entries = sum(payer_count.values())

    lines = [f"Expense snapshot (last 28 days): €{total_spend:.2f} across {total_entries} entries"]

    if len(payer_count) >= 2:
        most_frequent = max(payer_count, key=payer_count.get)
        lines.append(f"  • {most_frequent} paid most often ({payer_count[most_frequent]}x)")

        avg_per = {p: payer_total[p] / payer_count[p] for p in payer_count}
        highest = max(avg_per, key=avg_per.get)
        lowest = min(avg_per, key=avg_per.get)
        if highest != lowest:
            lines.append(
                f"  • Avg per entry: {highest} €{avg_per[highest]:.2f} vs {lowest} €{avg_per[lowest]:.2f}"
            )

        top_payer = max(payer_total, key=payer_total.get)
        share_pct = payer_total[top_payer] / total_spend * 100 if total_spend else 0
        if share_pct >= 40:
            lines.append(
                f"  • {top_payer} covered {share_pct:.0f}% of total spending"
            )
    elif len(payer_count) == 1:
        sole = next(iter(payer_count))
        lines.append(f"  • {sole} paid for everything this month – someone owes them!")

    return "\n".join(lines)


def _build_weekly_report(data):
    """Build the weekly report text, mutating data to apply penalty changes."""
    chores_normalized = {}
    for chore_user, points in (data.get("chores") or {}).items():
        for member in data.get("members") or []:
            member_name = _get_member_name(member)
            if _normalise_member_name(member_name) == _normalise_member_name(chore_user):
                chores_normalized[member_name] = points
                break

    member_names = [_get_member_name(m) for m in (data.get("members") or [])]
    leaderboard = sorted(
        [(name, chores_normalized.get(name, 0)) for name in member_names],
        key=lambda x: -x[1],
    )

    if not leaderboard:
        return "Mario's Monday Mauling: No data yet."

    leader, leader_points = leaderboard[0]
    current_date = datetime.now().strftime("%Y-%m-%d")
    sections = [f"Mario's Monday Mauling, {current_date}", f"Leader: {leader} with {leader_points} points"]

    # --- Standings & penalties ---
    penalty_lines = []
    comeback_kids = []
    currently_penalised = set()
    for member, points in leaderboard[1:]:
        if leader_points - points > 4:
            currently_penalised.add(member)
            last_week_violator = data.get("last_week_violators", {}).get(
                _normalise_member_name(member), False
            )
            if last_week_violator:
                weeks_lagging = data.setdefault("penalties", {}).get(member, 0) + 1
                data["penalties"][member] = weeks_lagging
                penalty_lines.append(f"  • {member} owes {weeks_lagging} beer(s)!")
            else:
                data.setdefault("last_week_violators", {})[_normalise_member_name(member)] = True
                penalty_lines.append(
                    f"  • {member} is {leader_points - points} pts behind {leader} — shape up or beer incoming!"
                )
        elif _normalise_member_name(member) in data.get("last_week_violators", {}):
            data["last_week_violators"].pop(_normalise_member_name(member), None)
            comeback_kids.append(member)

    # Include members who caught up but still owe unredeemed beers
    penalties = data.get("penalties", {}) or {}
    for member, owed in penalties.items():
        if owed > 0 and member not in currently_penalised:
            penalty_lines.append(f"  • {member} still owes {owed} beer(s) (caught up, debt remains).")

    if penalty_lines:
        sections.append("Standings & Penalties:\n" + "\n".join(penalty_lines))
    else:
        sections.append("Everyone is keeping up — no penalties this week!")

    # --- Comeback kids ---
    if comeback_kids:
        names = ", ".join(comeback_kids)
        sections.append(f"Comeback of the week: {names} turned it around after lagging last week!")

    # --- Big moves this week: members with >1h total across all sessions ---
    week_entries = _chore_entries_this_week(data.get("chore_log") or [])
    member_week_pts = {}
    member_week_entries = {}
    for e in week_entries:
        member = e.get("member", "?")
        member_week_pts[member] = member_week_pts.get(member, 0) + e.get("points", 0)
        member_week_entries.setdefault(member, []).append(e)
    big_movers = {m: pts for m, pts in member_week_pts.items() if pts > 4}
    if big_movers:
        leap_lines = []
        for member, pts in sorted(big_movers.items(), key=lambda x: -x[1]):
            mins = pts * 15
            leap_lines.append(f"  • {member}: {mins} min total")
            descs = [
                e.get("description", "").strip()
                for e in member_week_entries[member]
                if e.get("description", "").strip()
            ]
            if descs:
                leap_lines.append(f"  ({', '.join(descs)})")
        sections.append("Big moves this week:\n" + "\n".join(leap_lines))

    # --- Expense fun facts ---
    fun_facts = _build_expense_fun_facts(data.get("expenses") or [])
    if fun_facts:
        sections.append(fun_facts)

    # --- WG-Höck reminder ---
    hoeck_date_str = data.get("wg_hoeck_date")
    if hoeck_date_str:
        try:
            hoeck_date = datetime.strptime(hoeck_date_str, "%Y-%m-%d").date()
            today = datetime.now(pytz.timezone("Europe/Berlin")).date()
            days_until = (hoeck_date - today).days
            if 0 <= days_until <= 7:
                day_name = hoeck_date.strftime("%A, %d.%m.")
                if days_until == 0:
                    sections.append(f"📅 WG-Höck is TODAY!")
                else:
                    sections.append(f"📅 WG-Höck this week: {day_name} ({days_until} day{'s' if days_until != 1 else ''} away)")
        except ValueError:
            pass

    return "\n\n".join(sections)


def _parse_report_time(time_str):
    """Parse HH:MM string into (hour, minute) tuple, defaulting to (9, 30)."""
    try:
        h, m = time_str.strip().split(":")
        return int(h), int(m)
    except Exception:
        return 9, 30


def _get_chronicler_meta(data):
    return data.setdefault(
        "chronicler_backup",
        {"greeting_sent": False, "last_sent": None},
    )


def _get_weekly_report_meta(data):
    return data.setdefault("weekly_report_meta", {"last_sent": None})


def _weekly_report_target_for_week(reference=None, hour=9, minute=30):
    tz = pytz.timezone("Europe/Berlin")
    now = reference or datetime.now(tz)
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _parse_berlin_datetime(value):
    if not value:
        return None

    tz = pytz.timezone("Europe/Berlin")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return tz.localize(parsed)
    return parsed.astimezone(tz)


def _weekly_report_is_due(data, reference=None):
    tz = pytz.timezone("Europe/Berlin")
    now = reference or datetime.now(tz)
    hour, minute = _parse_report_time(data.get("weekly_report_time", "09:30"))
    target = _weekly_report_target_for_week(now, hour=hour, minute=minute)
    last_sent = _parse_berlin_datetime(_get_weekly_report_meta(data).get("last_sent"))
    return now >= target and (last_sent is None or last_sent < target)


async def _send_weekly_report(context: CallbackContext, data) -> bool:
    group_id = data.get("group_chat_id")
    if not group_id:
        logger.warning("No group chat ID set for weekly reports")
        return False

    if not data.get("members") or not data.get("chores"):
        report = "Weekly Report: Not enough data yet. Add members and log some chores first."
    else:
        report = _build_weekly_report(data)
        save_data(data)

    try:
        await context.bot.send_message(chat_id=group_id, text=report)
    except TelegramError as e:
        logger.error(f"Failed to send weekly report: {e}")
        return False

    _get_weekly_report_meta(data)["last_sent"] = datetime.now(
        pytz.timezone("Europe/Berlin")
    ).isoformat()
    save_data(data)
    return True
