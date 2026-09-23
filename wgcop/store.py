"""Data file access (relocated verbatim from maBot.py; multi-home store lands in B3)."""
import json
import logging
import os

from wgcop.botconfig import DATA_FILE

logger = logging.getLogger(__name__)

def load_data():
    try:
        with open(DATA_FILE, "r") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        default_data = {
            "expenses": [],
            "chores": {},
            "chore_log": [],
            "penalties": {},
            "members": [],
            "chronicler_backup": {"greeting_sent": False, "last_sent": None},
            "weekly_report_meta": {"last_sent": None},
            "weekly_report_time": "09:30",
        }
        with open(DATA_FILE, "w") as file:
            json.dump(default_data, file, indent=4)
        return default_data

    if "chronicler_backup" not in data:
        data["chronicler_backup"] = {
            "greeting_sent": False,
            "last_sent": None,
        }
    if "chore_log" not in data:
        data["chore_log"] = []
    if "weekly_report_meta" not in data:
        data["weekly_report_meta"] = {"last_sent": None}
    if "weekly_report_time" not in data:
        data["weekly_report_time"] = "09:30"

    # Migrate members from strings to objects with status field
    if data.get("members"):
        needs_migration = False
        for member in data["members"]:
            if isinstance(member, str):
                needs_migration = True
                break
        
        if needs_migration:
            data["members"] = [
                {"name": m, "status": "active"} if isinstance(m, str) else m
                for m in data["members"]
            ]
            save_data(data)
            logger.info("Migrated members to new object format with status field")
    
    save_data(data)
    return data


def save_data(data):
    """Atomically overwrite the data file. Backups are written by a scheduled job, not here."""
    tmp_file = DATA_FILE + ".tmp"
    with open(tmp_file, "w") as file:
        json.dump(data, file, indent=4)
    os.replace(tmp_file, DATA_FILE)
