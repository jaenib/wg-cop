"""Secrets loading and file-system settings (relocated verbatim from maBot.py)."""
import importlib.util
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Secrets loader

ROOT = Path(__file__).resolve().parent.parent
_cfg_override = os.environ.get("WG_COP_CONFIG_FILE")
CFG_PATH = Path(_cfg_override) if _cfg_override else ROOT / ".secrets" / "config.py"

if not CFG_PATH.exists():
    raise FileNotFoundError(f"Missing secrets file: {CFG_PATH}")

_spec = importlib.util.spec_from_file_location("wgcop_config", CFG_PATH)
_config = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_config)

# Bot token & UUID
TOKEN = getattr(_config, "TOKEN")
GROUP_CHAT_ID = getattr(_config, "GROUP_CHAT_ID")
try:
    BOT_HANDLER_ID = int(getattr(_config, "BOT_HANDLER_ID"))
except (TypeError, ValueError):
    BOT_HANDLER_ID = None  # admin features disabled when unconfigured
CHRONICLER_ID = getattr(_config, "CHRONICLER_ID")
NI_ID = getattr(_config, "NI_ID")
GI_ID = getattr(_config, "GI_ID")
GY_ID = getattr(_config, "GY_ID")
TO_ID = getattr(_config, "TO_ID")
JA_ID = getattr(_config, "JA_ID")
UIDS = [NI_ID, GI_ID, GY_ID, TO_ID, JA_ID]
OPENAI_API_KEY = getattr(_config, "OPENAI_API_KEY", None)
RECEIPT_TOLERANCE_CHF = 0.50  # max sum-vs-total gap before retry

# Data storage
DATA_FILE = os.environ.get("WG_COP_DATA_FILE", "wg_data_alpha.json")
BACKUP_DIR = os.environ.get("WG_COP_BACKUP_DIR", "/var/backups/wg_cop")
BACKUP_KEEP = 14  # 7 days × 2 backups/day


def _get_chronicler_chat_id():
    if not CHRONICLER_ID:
        return None

    chronicler_chat_id = str(CHRONICLER_ID).strip()
    if not chronicler_chat_id or chronicler_chat_id == "your_chronicler_chatid":
        return None

    return chronicler_chat_id
