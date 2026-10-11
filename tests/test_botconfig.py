"""Config loader: only TOKEN is required; legacy ids feed the migration seed."""
import importlib
import os
import sys

import pytest

from wgcop import botconfig


def test_legacy_seed_from_test_config():
    seed = botconfig.legacy_seed()
    assert seed["group_chat_id"] == -1001000000000
    assert seed["admin_uid"] == 1001
    assert seed["handler_uid"] == 1001
    assert seed["chronicler_uid"] is None
    assert seed["member_uids"] == {
        "Nicci Lopez": 1002,
        "Gjango Gmüseshole": 1003,
        "General Guysan": 1004,
        "Thomath Sucker": 1005,
        "Janidputzä": 1006,
    }


def _reload_with(tmp_path, monkeypatch, body):
    cfg = tmp_path / "config.py"
    cfg.write_text(body, encoding="utf-8")
    monkeypatch.setenv("WG_COP_CONFIG_FILE", str(cfg))
    sys.modules.pop("wgcop.botconfig", None)
    try:
        return importlib.import_module("wgcop.botconfig")
    finally:
        # restore the session-wide module (and the package attribute) for the other tests
        import wgcop

        sys.modules["wgcop.botconfig"] = botconfig
        wgcop.botconfig = botconfig


def test_token_only_config_is_enough(tmp_path, monkeypatch):
    mod = _reload_with(tmp_path, monkeypatch, 'TOKEN = "1:x"\n')
    assert mod.BOT_HANDLER_ID is None
    assert mod.legacy_seed()["member_uids"] == {}
    assert mod.legacy_seed()["admin_uid"] is None


def test_missing_token_is_an_error(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="TOKEN"):
        _reload_with(tmp_path, monkeypatch, 'TOKEN = ""\n')


def test_extra_legacy_member_ids_are_merged(tmp_path, monkeypatch):
    mod = _reload_with(
        tmp_path, monkeypatch,
        'TOKEN = "1:x"\nNI_ID = "7"\nLEGACY_MEMBER_IDS = {"Zoe": "8", "Bad": "x"}\n',
    )
    assert mod.legacy_seed()["member_uids"] == {"Nicci Lopez": 7, "Zoe": 8}


def test_env_override_points_at_the_file():
    session_dir = os.environ["WG_COP_TEST_SESSION_DIR"]
    assert str(botconfig.CFG_PATH) == os.environ["WG_COP_CONFIG_FILE"]
    assert str(botconfig.CFG_PATH).startswith(session_dir)
