"""Regression guard for the Application wiring produced by wgcop.app.main()."""
from telegram.ext import Application, ConversationHandler


def test_main_builds_expected_handlers_and_jobs(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(Application, "run_polling", lambda self, *a, **k: captured.setdefault("app", self))
    monkeypatch.chdir(tmp_path)  # bot_persistence.pkl lands here

    from wgcop import app as appmod

    appmod.main()
    app = captured["app"]

    handlers = app.handlers[0]
    conversations = [h for h in handlers if isinstance(h, ConversationHandler)]
    assert len(handlers) == 33
    assert len(conversations) == 9
    assert all(c.conversation_timeout == 300 for c in conversations)

    job_names = sorted(j.name for j in app.job_queue.jobs())
    assert job_names == [
        "heartbeat",
        "hoeck_reminder",
        "local_backup_04h",
        "local_backup_16h",
        "weekly_penalty_check",
        "weekly_report_catchup",
    ]
