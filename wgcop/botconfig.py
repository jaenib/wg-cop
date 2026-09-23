"""Secrets loading and file-system settings.

The secrets file is a Python module, historically ``.secrets/config.py``;
override its location with ``WG_COP_CONFIG_FILE``. Only ``TOKEN`` is required.
The household-specific ids that older versions demanded (``GROUP_CHAT_ID``,
``BOT_HANDLER_ID``, ``CHRONICLER_ID``, ``NI_ID`` .. ``JA_ID``) are optional and
are consulted once, when a legacy single-home database is migrated. Runtime
authorization lives in each home's stored roles, never in this file.
"""
import importlib.util
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
_cfg_override = os.environ.get("WG_COP_CONFIG_FILE")
_candidates = (
    [Path(_cfg_override)] if _cfg_override else [ROOT / ".secrets" / "config.py", ROOT / "config.py"]
)
CFG_PATH = next((p for p in _candidates if p.exists()), _candidates[0])

if not CFG_PATH.exists():
    raise FileNotFoundError(
        f"Missing secrets file: {CFG_PATH} (copy config.example.py there or set WG_COP_CONFIG_FILE)"
    )

_spec = importlib.util.spec_from_file_location("wgcop_config", CFG_PATH)
_config = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_config)


def _int_or_none(value):
    """Telegram ids arrive as ints or numeric strings; anything else is 'unset'."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        text = str(value).strip()
    except Exception:  # pragma: no cover - defensive
        return None
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _optional(name, default=None):
    return getattr(_config, name, default)


# Bot token (required)
TOKEN = _optional("TOKEN")
if not TOKEN or not str(TOKEN).strip():
    raise ValueError(f"TOKEN is missing in {CFG_PATH}")

# Legacy single-home ids (optional; migration seed and pre-B5 fallbacks only)
GROUP_CHAT_ID = _optional("GROUP_CHAT_ID")
BOT_HANDLER_ID = _int_or_none(_optional("BOT_HANDLER_ID"))
CHRONICLER_ID = _optional("CHRONICLER_ID")
NI_ID = _optional("NI_ID")
GI_ID = _optional("GI_ID")
GY_ID = _optional("GY_ID")
TO_ID = _optional("TO_ID")
JA_ID = _optional("JA_ID")
UIDS = [NI_ID, GI_ID, GY_ID, TO_ID, JA_ID]
OPENAI_API_KEY = _optional("OPENAI_API_KEY")
RECEIPT_TOLERANCE_CHF = 0.50  # max sum-vs-total gap before retry

# Data storage
DATA_FILE = os.environ.get("WG_COP_DATA_FILE", "wg_data_alpha.json")
BACKUP_DIR = os.environ.get("WG_COP_BACKUP_DIR", "/var/backups/wg_cop")
BACKUP_KEEP = 14  # 7 days × 2 backups/day

# Names the old source hard-wired to the five legacy id keys (typo included,
# deliberately: the migration matches it tolerantly against stored names).
LEGACY_MEMBER_NAMES = {
    "NI_ID": "Nicci Lopez",
    "GI_ID": "Gjango Gmüseshole",
    "GY_ID": "General Guysan",
    "TO_ID": "Thomath Sucker",
    "JA_ID": "Janidputzä",
}


def _get_chronicler_chat_id():
    if not CHRONICLER_ID:
        return None

    chronicler_chat_id = str(CHRONICLER_ID).strip()
    if not chronicler_chat_id or chronicler_chat_id == "your_chronicler_chatid":
        return None

    return chronicler_chat_id


def legacy_seed() -> dict:
    """One-time inputs for migrating a legacy single-home database into a home."""
    member_uids = {}
    for key, name in LEGACY_MEMBER_NAMES.items():
        uid = _int_or_none(_optional(key))
        if uid:
            member_uids[name] = uid
    extra = _optional("LEGACY_MEMBER_IDS", {}) or {}
    if isinstance(extra, dict):
        for name, uid in extra.items():
            uid = _int_or_none(uid)
            if uid:
                member_uids[str(name)] = uid
    return {
        "group_chat_id": _int_or_none(GROUP_CHAT_ID),
        "admin_uid": BOT_HANDLER_ID,
        "handler_uid": BOT_HANDLER_ID,
        "chronicler_uid": _int_or_none(_get_chronicler_chat_id()),
        "member_uids": member_uids,
    }
