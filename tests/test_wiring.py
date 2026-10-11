"""Regression guard for the Application wiring produced by wgcop.app.main()."""
from telegram.ext import Application, ConversationHandler

from wgcop import store as store_mod


def test_main_builds_expected_handlers_and_jobs(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(Application, "run_polling", lambda self, *a, **k: captured.setdefault("app", self))
    monkeypatch.chdir(tmp_path)  # bot_persistence.pkl lands here
    store_mod.set_store(store_mod.Store(tmp_path / "wg_data_alpha.json"))
    try:
        from wgcop import app as appmod

        appmod.main()
    finally:
        store_mod.set_store(None)
    app = captured["app"]

    handlers = app.handlers[0]
    conversations = [h for h in handlers if isinstance(h, ConversationHandler)]
    assert len(handlers) == 45  # + /deletehome command and its confirmation callback
    assert len(conversations) == 11
    assert all(c.conversation_timeout == 300 for c in conversations)
    # conversations are registered before every plain handler (see app.handoff)
    assert all(isinstance(h, ConversationHandler) for h in handlers[:11])
    # handoff relies on these ConversationHandler internals of the pinned PTB release
    for attr in ("_get_key", "_conversations", "_update_state", "timeout_jobs", "_timeout_jobs_lock"):
        assert hasattr(conversations[0], attr), attr

    job_names = sorted(j.name for j in app.job_queue.jobs())
    assert job_names == ["local_backup_04h", "local_backup_16h", "sweep"]
    assert (tmp_path / "wg_data_alpha.json").exists()
