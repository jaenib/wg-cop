"""Run the workflow's deployment shell against disposable local Git clones."""
import os
from pathlib import Path
import subprocess
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True, stderr=subprocess.DEVNULL).strip()


@pytest.fixture
def deployment(tmp_path):
    source = tmp_path / "upstream"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.name", "Offline test")
    git(source, "config", "user.email", "test@example.invalid")
    (source / "deploy.sh").write_text("#!/bin/sh\nexit 0\n")
    (source / "version.txt").write_text("old")
    git(source, "add", ".")
    git(source, "commit", "-m", "old")
    previous = git(source, "rev-parse", "HEAD")
    remote = tmp_path / "production"
    subprocess.run(["git", "clone", str(source), str(remote)], check=True, capture_output=True)
    (source / "version.txt").write_text("new")
    git(source, "add", ".")
    git(source, "commit", "-m", "new")
    target = git(source, "rev-parse", "HEAD")
    for name in ("config.py", "wg_data_alpha.json", "wg_data_alpha.json.pre-multihome.bak", "bot_persistence.pkl"):
        (remote / name).write_text("private sentinel " + name)
    (remote / ".secrets").mkdir()
    (remote / ".secrets" / "config.py").write_text("private sentinel")
    python = remote / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\ncase "$*" in *pytest*) exit "${FAIL_TESTS:-0}" ;; esac\nexit 0\n')
    python.chmod(0o755)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name, script in {
        "sudo": '#!/bin/sh\nexec "$@"\n',
        "systemctl": '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_TRACE"\nexit 0\n',
        "sleep": '#!/bin/sh\nexit 0\n',
    }.items():
        path = binaries / name
        path.write_text(script)
        path.chmod(0o755)
    trace = tmp_path / "trace"
    env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"],
               DEPLOY_SHA=target, TEST_TRACE=str(trace))
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    script = textwrap.dedent(workflow.split("          script: |\n", 1)[1])
    script = script.replace("/usr/bots/wg_cop", str(remote))
    return source, remote, previous, target, trace, env, script


def execute(deployment):
    return subprocess.run(["bash"], input=deployment[6], env=deployment[5], text=True, capture_output=True)


def test_deploy_preserves_state_and_checks_health(deployment):
    _, remote, _, target, trace, _, _ = deployment
    result = execute(deployment)
    assert result.returncode == 0, result.stderr
    assert git(remote, "rev-parse", "HEAD") == target
    assert (remote / "version.txt").read_text() == "new"
    assert (remote / "wg_data_alpha.json").read_text().startswith("private sentinel")
    assert (remote / "wg_data_alpha.json.pre-multihome.bak").exists()
    assert (remote / ".secrets" / "config.py").read_text() == "private sentinel"
    assert (remote / "venv" / "bin" / "python").exists()
    assert trace.read_text().splitlines() == ["restart wg_cop", "is-active --quiet wg_cop"]


def test_failed_preflight_restores_code_without_restart(deployment):
    _, remote, previous, _, trace, env, _ = deployment
    env["FAIL_TESTS"] = "1"
    result = execute(deployment)
    assert result.returncode != 0
    assert git(remote, "rev-parse", "HEAD") == previous
    assert (remote / "version.txt").read_text() == "old"
    assert (remote / "wg_data_alpha.json").read_text().startswith("private sentinel")
    assert not trace.exists()


def test_superseded_revision_is_skipped(deployment):
    _, remote, previous, _, trace, env, _ = deployment
    env["DEPLOY_SHA"] = previous
    result = execute(deployment)
    assert result.returncode == 0, result.stderr
    assert "superseded" in result.stdout
    assert git(remote, "rev-parse", "HEAD") == previous
    assert not trace.exists()
