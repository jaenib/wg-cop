# wg-cop

A Telegram bot for shared homes: split expenses, scan receipts, record chores,
track penalty debts, and send weekly reports. One bot account can serve several
households ("homes"). Each home has its own members, roles, settings and records.

## Install

Use Python **3.10 or newer**. The entry point remains `maBot.py`.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p .secrets
cp config.example.py .secrets/config.py
# Edit .secrets/config.py privately, setting the Telegram bot token.
WG_COP_BACKUP_DIR=./backups .venv/bin/python maBot.py
```

The configuration is read from `.secrets/config.py`, falling back to a root
`config.py`; set `WG_COP_CONFIG_FILE` to use another path. Only `TOKEN` is
required. Receipt scanning additionally needs `OPENAI_API_KEY`; manual expense
entry works without it. Secrets and household data are ignored by git.

For local runs, set `WG_COP_BACKUP_DIR=./backups` if `/var/backups/wg_cop` is
not writable by your account.

Use a separate bot token and temporary state for development. Running two
polling processes with the production token interferes with message delivery.

## Homes and access

A home is bound to its Telegram group; the group's chat id is the home's id.

- **Create a home**: add the bot to the group and send `/newhome <name>` there.
  Only a Telegram group administrator or creator can do this; they become the
  home's first administrator and member.
- **Join**: send `/join` in the group. Choose "I'm new" to be added under your
  Telegram name, or "I am <profile>" to claim an existing unlinked profile.
  Claims need approval by a home administrator, who receives a DM with
  Approve/Deny and can also review requests under Admin Panel → Manage Roles
  → Join Requests. In a private chat, `/join` finds homes whose group you are
  in.
- **Private chats**: with one home the bot just works; with several, pick one
  (Settings → Switch Home). Switching clears only that chat's draft; drafts in
  group chats are untouched. Pressing a menu button while an entry is in
  progress ends that entry and runs the button.
- A home's administrator has no implicit access to other homes, and the ids in
  the configuration file grant no runtime privileges.

## Daily use

- **Add Expense**: enter a description and amount, select the payer, then
  toggle the people sharing the cost. **All** toggles everyone; **Done** saves.
  Receipt mode extracts items from a photo, lets you accept the result or
  switch to manual entry, and exclude personal items before splitting.
- **Add Chore**: choose a member, enter minutes and optionally describe the
  work. `/chore <points> [description]` records points for your linked profile.
- **Standings**, **List Expenses**, **List Chores** and **Penalties** show
  records for the current home. **Redeem Penalty** records a paid penalty debt.
- **Settings → Manage <your profile>** lets you rename your profile, edit your
  last expense and toggle vacation status. Vacating members remain selectable
  for expenses but appear at the bottom as a reminder about shared costs.
  `/setstatus <name>` toggles another member's vacation status.
- `/cancel` ends an entry. Expired or interrupted drafts should be restarted
  from the main menu.

By default, 15 minutes earns one chore point. A gap greater than four points
behind the leader first produces a warning; continued lag produces cumulative
penalty debt in subsequent weekly reports. Catching up does not erase debt
already owed. Homes can agree what redeeming a penalty means.

## Roles and settings (Admin Panel)

Everything below is stored with the home and changed from Telegram; no config
edit or restart is involved.

- **Manage Roles**: add or remove administrators (the last one cannot be
  removed; removing yourself leaves the panel), set or clear the *chronicler*
  (receives the home's weekly JSON archive by DM) and the *handler* (receives
  the bot's heartbeat), unlink a member from a Telegram account while keeping
  their bookkeeping, and review pending join requests. Only linked members can
  hold a role.
- **Home Settings**: name, currency label, timezone (IANA name), minutes per
  chore point, penalty threshold (points behind the leader) and penalty unit
  (default "beer"). Changes apply immediately.
- **Manage Weekly Report**: report time, on/off, and a dry run of this week's
  report. **Set WG-Höck** stores the next house-meeting date. **Adjust
  Penalties** corrects a member's debt. **Member Management** adds profiles,
  removes ones without bookkeeping history, and *retires* a member who moved
  out: send their name, then tap *Retire*. Their expenses, chore points and
  balance stay in the books frozen (shown under "Retired" in Standings), they
  disappear from every menu, standing and report, their Telegram link is
  removed, and they receive a farewell note by DM with their totals. A retired
  member can still be removed later if nothing in the books references them.
  **System Overview** shows a sanitized status of the home, storage and jobs.

## Scheduled jobs

One sweep runs every minute and, per home, sends the weekly report (generated
once per week; delivery is retried if Telegram is unreachable, penalties are
never applied twice), the WG-Höck reminder at noon local time, the chronicler's
export, and the handler's heartbeat. The whole data file is copied to the
backup directory twice a day.

## Existing installation and data

On first startup, the legacy single-household JSON is migrated to a
schema-version-2 envelope at the same `WG_COP_DATA_FILE` path (normally
`wg_data_alpha.json`). The old group id identifies the first home; the legacy
`BOT_HANDLER_ID` becomes its first administrator and handler, `CHRONICLER_ID`
its chronicler, and the legacy per-member ids link the existing profiles.

The original JSON is retained as `wg_data_alpha.json.pre-multihome.bak` (using
your data filename when customized; a timestamped copy is written instead when
that name is already taken). Unknown or malformed state is refused; it
is never replaced with an empty database. Keep that archive and a private
backup before upgrading. Older bot code cannot read the new envelope. Returning
to old code requires a compatible exported household or recovery snapshot,
including reconciliation of changes since migration.

The full envelope is a private backup of **all** homes. A chronicler receives
only their own home's export. Local backup storage is configurable through
`WG_COP_BACKUP_DIR`; production currently uses `/var/backups/wg_cop`.

## Development and release

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
shellcheck deploy.sh
```

Tests run offline with synthetic users and temporary storage. CI checks
Python 3.10 and 3.12. A successful push to `main` of **jaenib/wg-cop** triggers
automatic deployment and a service restart. Local `origin` may point to a
different fork; confirm the remote before publishing.

See [maintenance and recovery instructions](docs/MAINTENANCE.md) for the active
clone, deployment safeguards, backup/restore commands and release checklist,
and [docs/PLAN.md](docs/PLAN.md) for the design and the delivered commit series.
