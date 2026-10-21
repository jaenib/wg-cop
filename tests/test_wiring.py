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
    assert len(handlers) == 46  # + /deletehome command, its confirmation callback, stray-text fallback
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


def test_every_conversation_step_keeps_typed_text():
    """Text in a step that waits for a button tap must stay with its conversation.

    Text that falls through reaches the leftover-text handler, which drops the
    running draft; the user's next tap then finds it gone (see app._conversation)."""
    from tests.telegram_driver import TelegramDriver
    from wgcop.app import register_handlers

    app = Application.builder().token("123456:TEST-TOKEN-NOT-REAL").build()
    register_handlers(app)
    typed = TelegramDriver().message_update(None, 1002, 1002, "something typed mid-draft")
    conversations = [h for h in app.handlers[0] if isinstance(h, ConversationHandler)]
    for conv in conversations:
        entry = conv.entry_points[0].callback.__name__
        for state, handlers in conv.states.items():
            if state == ConversationHandler.TIMEOUT:
                continue
            assert any(h.check_update(typed) for h in handlers), f"{entry}: state {state} drops typed text"
