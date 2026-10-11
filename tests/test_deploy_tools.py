"""Exercise operational helpers on a fake SSH host; no network or bot imports."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
LEGACY = {"members": ["A"], "expenses": [], "chores": {}, "penalties": {}}
ENVELOPE = {"schema_version": 2, "homes": {
    "-1": dict(LEGACY, id="-1", roles={"admins": [10]})}, "selections": {}}


@pytest.fixture
def host(tmp_path):
    remote = tmp_path / "server"
    remote.mkdir()
    (remote / "ops").mkdir()
    shutil.copyfile(ROOT / "ops" / "validate_state.py", remote / "ops" / "validate_state.py")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    trace = tmp_path / "trace"
    shim = '''#!PYTHON
import os, pathlib, subprocess, sys, shutil
remote = os.environ["TEST_REMOTE"]
name = pathlib.Path(sys.argv[0]).name
with open(os.environ["TEST_TRACE"], "a") as log:
    log.write(name + " " + " ".join(sys.argv[1:]) + "\\n")
if name == "ssh":
    args = sys.argv[2:]
    if args[:2] == ["bash", "-s"]:
        script = sys.stdin.read().replace("/usr/bots/wg_cop", remote)
        result = subprocess.run(["bash", "-s", "--", args[-1].replace("/usr/bots/wg_cop", remote)], input=script, text=True)
        sys.exit(result.returncode)
    script = " ".join(args).replace("/usr/bots/wg_cop", remote)
    result = subprocess.run(["bash", "-c", script], capture_output=True)
    sys.stdout.buffer.write(result.stdout.replace(remote.encode(), b"/usr/bots/wg_cop") if "mktemp" in script else result.stdout)
    sys.stderr.buffer.write(result.stderr)
    sys.exit(result.returncode)
elif name == "scp":
    destination = sys.argv[2].split(":", 1)[1].replace("/usr/bots/wg_cop", remote)
    shutil.copyfile(sys.argv[1], destination)
elif name == "systemctl":
    if "is-active" in sys.argv and os.environ.get("FAIL_START"):
        sys.exit(1)
elif name == "sleep":
    pass
'''.replace("PYTHON", sys.executable)
    for name in ("ssh", "scp", "systemctl", "sleep"):
        executable = binaries / name
        executable.write_text(shim)
        executable.chmod(0o755)
    # The helper intentionally uses python3 independent of a production venv.
    (binaries / "python3").symlink_to(sys.executable)
    # The ssh shim runs tar locally; macOS bsdtar would add "._" AppleDouble entries.
    env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"],
               TEST_REMOTE=str(remote), TEST_TRACE=str(trace), TMPDIR=str(tmp_path),
               COPYFILE_DISABLE="1")
    return remote, trace, env


def run_helper(host, *args):
    return subprocess.run(["bash", str(ROOT / "deploy.sh"), *map(str, args)],
                          env=host[2], text=True, capture_output=True)


def test_invalid_restore_never_connects(host, tmp_path):
    invalid = tmp_path / "bad.json"
    invalid.write_text('{"expenses": []}')
    result = run_helper(host, "push-data", invalid)
    assert result.returncode != 0
    assert not host[1].exists()


def test_legacy_restore_refuses_multi_home_store(host, tmp_path):
    (host[0] / "wg_data_alpha.json").write_text(json.dumps(ENVELOPE))
    source = tmp_path / "input.json"
    source.write_text(json.dumps(LEGACY))
    result = run_helper(host, "push-data", source)
    assert result.returncode != 0
    assert "Restore schema differs" in result.stderr
    assert "systemctl " not in host[1].read_text()
    assert json.loads((host[0] / "wg_data_alpha.json").read_text()) == ENVELOPE


@pytest.mark.parametrize("startup_failure", [False, True])
def test_restore_preserves_original_and_checks_service(host, tmp_path, startup_failure):
    original = json.dumps(dict(LEGACY, recovery_marker="preserve me")).encode()
    (host[0] / "wg_data_alpha.json").write_bytes(original)
    source = tmp_path / "input.json"
    source.write_text(json.dumps(LEGACY))
    if startup_failure:
        host[2]["FAIL_START"] = "1"
    result = run_helper(host, "push-data", source)
    assert (result.returncode != 0) == startup_failure, result.stderr
    assert json.loads((host[0] / "wg_data_alpha.json").read_text()) == LEGACY
    backups = list(host[0].glob("wg_data_alpha.json.pre-restore.*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    trace = host[1].read_text()
    assert trace.index("systemctl stop") < trace.index("systemctl start") < trace.index("systemctl is-active")
    assert not list(host[0].glob(".restore.*"))


def test_snapshot_contains_envelope_and_uses_private_unique_paths(host):
    (host[0] / "wg_data_alpha.json").write_text(json.dumps(ENVELOPE))
    for _ in range(2):
        result = run_helper(host, "data")
        assert result.returncode == 0, result.stderr
    snapshots = list(Path(host[2]["TMPDIR"]).glob("wg-cop-state.*"))
    assert len(snapshots) == 2
    for snapshot in snapshots:
        assert snapshot.stat().st_mode & 0o777 == 0o600
        with tarfile.open(snapshot) as archive:
            assert archive.getnames() == ["wg_data_alpha.json"]
            assert json.load(archive.extractfile("wg_data_alpha.json")) == ENVELOPE


def test_failed_snapshot_removes_partial_download(host):
    result = run_helper(host, "data")
    assert result.returncode != 0
    assert not list(Path(host[2]["TMPDIR"]).glob("wg-cop-state.*"))


def test_restore_refuses_ambiguous_corrupt_current_state(host, tmp_path):
    current = host[0] / "wg_data_alpha.json"
    current.write_text("{broken")
    source = tmp_path / "input.json"
    source.write_text(json.dumps(ENVELOPE))
    result = run_helper(host, "push-data", source)
    assert result.returncode != 0
    assert current.read_text() == "{broken"
    assert "systemctl " not in host[1].read_text()
    assert not list(host[0].glob(".restore.*"))


def test_restore_entire_multi_home_store(host, tmp_path):
    current = host[0] / "wg_data_alpha.json"
    current.write_text(json.dumps(ENVELOPE))
    incoming = json.loads(json.dumps(ENVELOPE))
    incoming["homes"]["-2"] = dict(LEGACY, id="-2", roles={"admins": [20]})
    source = tmp_path / "input.json"
    source.write_text(json.dumps(incoming))
    result = run_helper(host, "push-data", source)
    assert result.returncode == 0, result.stderr
    assert json.loads(current.read_text()) == incoming
    backup = next(host[0].glob("wg_data_alpha.json.pre-restore.*"))
    assert json.loads(backup.read_text()) == ENVELOPE
