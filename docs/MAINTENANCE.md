# Maintenance and operating notes

## Runtime and deployment boundary

Read-only inspection on 2026-09-23 confirmed the active production service:

| Item | Value |
| --- | --- |
| Host | `root@82.165.45.100` |
| Checkout | `/usr/bots/wg_cop` |
| Service | `wg_cop.service` |
| Entry point | `/usr/bots/wg_cop/venv/bin/python /usr/bots/wg_cop/maBot.py` |
| Working directory | `/usr/bots/wg_cop` |
| Production revision before this work | `59b857c` |
| Python / PTB / OpenAI | `3.10.12` / `22.7` / `2.36.0` |
| Secrets | `.secrets/config.py` |
| Local backups | `/var/backups/wg_cop` |

The local `jaenib-wgcop` remote is the production repository
(`jaenib/wg-cop`). Local `origin` points to a different fork. The server's
`origin` is the production repository. A push or merge to production `main`
automatically deploys and restarts the bot. A local commit does not deploy.

Do not start another polling process with the live token: both processes would
compete for Telegram updates. All regression tests use synthetic identities,
temporary state and fake Telegram calls.

## Maintenance plan and ownership

Claude owns household storage, routing, roles/settings UI and the package
split. Codex owns deployment, hygiene, independent tests and review. The shared
`COORDINATION.md` records interface decisions and handoffs. Both start from the
production revision so receipt accept/reject and split-all behavior is retained.

The maintenance priorities are:

1. Stop destructive recovery on malformed JSON and avoid writes during reads.
2. Isolate home data, identity, drafts, jobs and chronicler exports.
3. Make weekly penalties idempotent when Telegram fails or the service restarts.
4. Validate financial and chore input and fix the existing Splitters edit path.
5. Test migration, bookkeeping, authorization, interleaved chats and deployment.
6. Gate deployment on tests and retain secrets, data and recovery copies.

## Deploy safeguards

The workflow tests Python 3.10 and 3.12 on pushes and pull requests. Only a
successful `main` run can deploy. Production deployments are serialized and
use the tested SHA; a superseded run exits without changing the server.

Deployment never runs `git clean`. Untracked state, private configuration,
backups and environments remain on disk. Dependency checks and offline tests
run before restart; failed pre-restart checks restore the previous code.
The workflow checks the service is active after restart.

This is an in-place deployment: restoring code cannot undo dependency changes
to the existing venv. Once startup may have migrated data, automatic code-only
rollback is disabled. Diagnose a failed startup and deliberately restore a
compatible code/dependency/state combination. An active systemd service is a
process health check, not proof of Telegram delivery.

## Operations checklist

These items were observed and documented; no production changes were made:

- The service is disabled at boot. Before the next maintenance window, enable
  it with `systemctl enable wg_cop`, then verify `systemctl is-enabled wg_cop`.
- The checkout contains historical `wg_data_alpha.json.*` recovery files.
  Preserve them; archive only after comparing them with current backups.
- A duplicate root `config.py` may exist beside `.secrets/config.py`. Establish
  which loader path is used before moving either file. Never paste credentials
  or unfiltered HTTP request logs into issues or PRs.
- Keep the out-of-repository emergency rsync script's excludes synchronized:
  `.secrets/`, `config.py`, `wg_data_*.json*`, `bot_persistence.pkl`,
  `backups/`, `venv/`. Normal releases use GitHub Actions.

## Release verification

Run `python -m pip install -r requirements-dev.txt`, `python -m pip check`,
`python -m pytest -q`, `bash -n deploy.sh`, and `shellcheck deploy.sh` in an
isolated Python 3.10+ environment. Inspect the final migration fixture and
home-boundary tests before a release. Capture a private state backup immediately
before the first multi-home deployment. After release, verify service health,
the migrated household's totals, and a settings interaction before adding a
second home.

## Backup and restore

`./deploy.sh data` downloads `wg_data_alpha.json` into a unique mode-0600 gzip
tar file. After migration this file contains the complete versioned envelope
with all households. Treat it as private household data. Atomic replacement of
the envelope keeps the file internally consistent during online backup.

`./deploy.sh push-data /path/to/repaired.json` restores the **whole store**.
It validates JSON locally and remotely, requires the same schema as the current
file, stages the upload, stops the service, retains the original, atomically
promotes the staged file, and checks startup. A failed start preserves the
original for recovery. It does not silently roll back potentially migrated
state. A single-home chronicler export cannot replace a multi-home store.
The helper uses the standard server/path; adapt it before use if the service
sets a custom `WG_COP_DATA_FILE`.

For corrupt state or deliberate schema recovery, work during a stopped-service
maintenance window (the helper refuses to guess the schema of corrupt state):

1. Preserve a new private copy of the current JSON file, even if corrupt.
2. Extract the trusted recovery archive to a separate directory. Inspect the
   complete envelope using `ops/validate_state.py` and the store loader.
3. Replace the complete JSON file by renaming a staged file on the same
   filesystem. Keep both the prior state and `.pre-multihome.bak` archive.
4. Start the service with compatible code and dependencies, check its journal
   and service state, and verify household totals and group bindings.

Returning to single-home code does not bring back changes made after migration;
it reads the old legacy file. Export/reconcile the desired home first. Never
run the emergency parent rsync deployment without reviewing its exclusions:
its original exclusions omit `config.py` and `bot_persistence.pkl`.
