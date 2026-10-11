"""Synthetic legacy (schema-less) database shaped like the production file.

Five members like production: three already carry a uid, one matches a seed
name exactly, one only matches the seed name with a typo. Never real data.
"""
import copy

LEGACY_GROUP_ID = -1001000000000

LEGACY_DATA = {
    "expenses": [
        {"date": "2026-08-01", "description": "Groceries", "amount": 60.0,
         "payer": "Nicci Lopez", "split_with": ["Nicci Lopez", "General Guysan", "Janidputzä"]},
        {"date": "2026-08-03", "description": "Beer", "amount": 24.0,
         "payer": "Thomath Sucker", "split_with": ["Thomath Sucker", "Gjango Gmüeshole"]},
        {"date": "2026-08-05", "description": "Rent share", "amount": 100.0,
         "payer": "General Guysan", "split_with": ["Nicci Lopez", "General Guysan", "Thomath Sucker", "Gjango Gmüeshole", "Janidputzä"]},
        {"date": "2026-08-09", "description": "Receipt", "amount": 12.5,
         "payer": "Janidputzä", "split_with": ["Janidputzä", "Nicci Lopez"],
         "items": [{"name": "Milk", "amount": 2.5}, {"name": "Bread", "amount": 10.0}]},
    ],
    "chores": {"Nicci Lopez": 12, "General Guysan": 4, "Thomath Sucker": 9, "Gjango Gmüeshole": 1, "Janidputzä": 7},
    "penalties": {"General Guysan": 2, "Gjango Gmüeshole": 1},
    "members": [
        {"name": "General Guysan", "status": "active"},
        {"name": "Gjango Gmüeshole", "status": "active"},
        {"name": "Janidputzä", "status": "active", "uid": 1006},
        {"name": "Nicci Lopez", "status": "vacating", "uid": 1002},
        {"name": "Thomath Sucker", "status": "active", "uid": 1005},
    ],
    "group_chat_id": LEGACY_GROUP_ID,
    "chronicler_backup": {"greeting_sent": True, "last_sent": "2026-09-14T10:00:00+02:00"},
    "last_week_violators": {"gjango gmüeshole": True},
    "chore_log": [
        {"timestamp": "2026-09-20T18:00:00+02:00", "member": "Nicci Lopez", "points": 2, "description": "kitchen"},
        {"timestamp": "2026-09-21T09:00:00+02:00", "member": "Janidputzä", "points": 1, "description": ""},
    ],
    "weekly_report_meta": {"last_sent": "2026-09-21T13:12:05+02:00"},
    "weekly_report_time": "13:12",
    "wg_hoeck_date": "2026-06-25",
}

# What wgcop.botconfig.legacy_seed() yields for the test config in conftest.py.
LEGACY_SEED = {
    "group_chat_id": LEGACY_GROUP_ID,
    "admin_uid": 1001,
    "handler_uid": 1001,
    "chronicler_uid": None,
    "member_uids": {
        "Nicci Lopez": 1002,
        "Gjango Gmüseshole": 1003,   # typo as in the old source, on purpose
        "General Guysan": 1004,
        "Thomath Sucker": 1005,
        "Janidputzä": 1006,
    },
}


def legacy_data():
    return copy.deepcopy(LEGACY_DATA)


def legacy_seed():
    return copy.deepcopy(LEGACY_SEED)
