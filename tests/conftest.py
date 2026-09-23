"""Offline test environment for wg-cop.

Everything the bot touches at import time (secrets file, data file, backup
directory) is redirected into a throw-away directory *before* `wgcop` is
imported, so tests never read `.secrets/config.py` or write next to the code.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# This module may be executed twice (pytest's conftest plus `import tests.conftest`
# from a test), so the temp session directory is created only once per process.
if os.environ.get("WG_COP_TEST_SESSION_DIR"):
    _SESSION_DIR = Path(os.environ["WG_COP_TEST_SESSION_DIR"])
else:
    _SESSION_DIR = Path(tempfile.mkdtemp(prefix="wgcop-tests-"))
    os.environ["WG_COP_TEST_SESSION_DIR"] = str(_SESSION_DIR)
_CONFIG_FILE = _SESSION_DIR / "config.py"
if not _CONFIG_FILE.exists():
    _CONFIG_FILE.write_text(
        "\n".join(
            [
                'TOKEN = "123456:TEST-TOKEN-NOT-REAL"',
                'GROUP_CHAT_ID = "-1001000000000"',
                'BOT_HANDLER_ID = "1001"',
                'CHRONICLER_ID = ""',
                'NI_ID = "1002"',
                'GI_ID = "1003"',
                'GY_ID = "1004"',
                'TO_ID = "1005"',
                'JA_ID = "1006"',
                'OPENAI_API_KEY = ""',
                "",
            ]
        ),
        encoding="utf-8",
    )
os.environ["WG_COP_CONFIG_FILE"] = str(_CONFIG_FILE)
os.environ["WG_COP_DATA_FILE"] = str(_SESSION_DIR / "wg_data_alpha.json")
os.environ["WG_COP_BACKUP_DIR"] = str(_SESSION_DIR / "backups")


# Test-only identities matching the dummy config above.
ADMIN_UID = 1001
NI_UID, GI_UID, GY_UID, TO_UID, JA_UID = 1002, 1003, 1004, 1005, 1006
LEGACY_GROUP_ID = -1001000000000


@pytest.fixture
def isolated_data_file(tmp_path, monkeypatch):
    """Point the legacy single-file store at a fresh temp file for one test."""
    from wgcop import store

    path = tmp_path / "wg_data_alpha.json"
    monkeypatch.setattr(store, "DATA_FILE", str(path))
    return path


@pytest.fixture
def sample_members():
    return [
        {"name": "Alice", "status": "active", "uid": NI_UID},
        {"name": "Bob", "status": "active", "uid": GI_UID},
        {"name": "Carla", "status": "vacating"},
    ]
