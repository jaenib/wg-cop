# wg-cop: multi-home build + maintenance plan

Written 2026-09-23 by the "build" agent after reading the whole codebase and
inspecting the live server. Both agents work from this document. Update it in
place when a decision changes; keep the commit series at the bottom current.

## 1. What the project is (founded understanding)

* One Telegram bot (`maBot.py`, ~3.35k lines, python-telegram-bot 22.x) for one
  shared flat ("WG"). Features: expense splitting (manual or GPT-4o receipt
  scan), chore minutes -> points, weekly "Mario's Monday Mauling" report with
  beer penalties, penalty redemption, vacation status, member management,
  admin panel, chronicler backups, heartbeat.
* State: a single JSON file `wg_data_alpha.json` re-read and re-written by
  every handler (`load_data()` / `save_data()`), plus `bot_persistence.pkl`
  (PTB PicklePersistence for conversation state and `user_data`).
* Identity: members are `{"name", "status", "uid"?}` objects. Linking a
  Telegram user to a member is **hard-coded**: `_match_member_UID()` maps five
  config keys (`NI_ID, GI_ID, GY_ID, TO_ID, JA_ID`) to five literal names in
  source. Roles are also config singletons: `BOT_HANDLER_ID` (the only admin),
  `CHRONICLER_ID` (gets DB copies), `GROUP_CHAT_ID` (report target, overridable
  by `data["group_chat_id"]`).
* Scheduling: one weekly report job, one daily Höck check, one weekly
  chronicler dispatch, twice-daily local backup, heartbeat every 50h.

### Production topology (this matters for every merge)

* Server `root@82.165.45.100`, dir `/usr/bots/wg_cop`, systemd `wg_cop`,
  Python 3.10.12, PTB 22.7. Secrets in `.secrets/config.py`. Backups in
  `/var/backups/wg_cop`.
* **Deploy = push to `main` of `jaenib/wg-cop`** (local remote `jaenib-wgcop`).
  `.github/workflows/deploy.yml` then does `git reset --hard origin/main`,
  `git clean -fd -e <protected files>`, pip install, `systemctl restart`.
  There is no test gate. Merging a PR is a live deploy.
* `origin` (usukenshin/wg-cop) is a fork; pushes there deploy nothing.
  `gh` is logged in as `jaenib`, so PRs on upstream can be opened and merged.
* On 2026-09-23 the server ran upstream `59b857c` (PR #47); the local checkout's
  `main` was 7 commits behind (`c328bb4`). Always branch from
  `jaenib-wgcop/main`.
* The service unit is **disabled** (not enabled at boot). Live data: 5
  members, ~524 expenses, 68 chore log entries, penalties in play.

## 2. Roles for the two agents

Only one file exists today, so parallel editing of `maBot.py` guarantees
conflicts. The first commit (B1) splits it into a package; after that the two
tracks own disjoint modules.

| Track | Owner | Owns (after B1) |
|-------|-------|-----------------|
| **B — build** (multi-home, roles, settings) | build agent (this session, `worktree-multi-home`) | `wgcop/store.py`, `wgcop/homes.py`, `wgcop/botconfig.py`, `wgcop/jobs.py`, `wgcop/handlers/{settings,admin,home}.py`, `wgcop/app.py` (wiring), `maBot.py` entry |
| **A — maintenance** (stability, bugs, hygiene, deploy, ops) | second agent | `tests/`, `.github/`, `README.md`, `docs/`, `deploy.sh`, `wgcop/reports.py`, `wgcop/receipts.py`, `wgcop/handlers/{expense,chore,edit,penalties}.py` bug fixes, logging setup, `ops/` |

Rules of engagement:

1. B1 lands alone first (fast, mechanical). Nobody else touches `maBot.py`
   until B1 is merged into the shared integration branch.
2. Integration branch: `integration/multi-home` on `jaenib-wgcop`. Both tracks
   branch from it and open PRs **into it**, never into `main`. One final PR
   `integration/multi-home -> main` deploys everything after the test gate.
3. A change to a module you do not own: open a PR into the integration branch
   and message the owner; do not push to their branch.
4. Every PR: `pytest` green, `python -m compileall wgcop` green, and the
   legacy-migration test green.

## 3. Track A seed: findings from the code read (bugs, smells, hygiene)

Bugs (ordered by user impact):

1. `edit_entries_menu`: `choice.startswith("slitt")` never matches
   "splitters", so the *Splitters* button is dead; the branch would also crash
   on `", ".join(members)` with dict members.
2. `_send_weekly_report`: penalties are mutated and saved **before** the
   Telegram send; if the send fails, `last_sent` stays unset and the startup
   catch-up applies penalties again (double beers).
3. `handle_chore` (`/chore`): matches `str(user.id)` against *names*; falls back
   to `user.full_name`, creating ghost entries in `chores` for non-members.
   `chore_user` likewise accepts any free text as a member.
4. `load_data()` unconditionally calls `save_data()` — a full rewrite of a
   190 KB file on every read, several times per update. Also widens the
   lost-update window (`_resolve_member_for_user` loads+saves on its own while
   callers hold a stale copy).
5. Hard-coded member names in `_match_member_UID` include a typo
   ("Gmüseshole" vs data "Gmüeshole"), so that member can never be
   auto-linked. Removed entirely by track B.
6. `_build_expense_fun_facts` prints `€`; everything else is CHF.
7. `MANAGE_MEMBER = range(1)` (a range object used as a state key).
8. `_expenses_in_period` uses naive `datetime.now()`; rest of file is
   Europe/Berlin aware.
9. Weekly job ignores the `GROUP_CHAT_ID` fallback that the Höck reminder
   honours.

Security / ops:

10. httpx INFO logging writes the **bot token** into journald on every poll
    (`.../bot<TOKEN>/getUpdates`). Silence httpx and apscheduler to WARNING.
11. `systemctl is-enabled wg_cop` -> `disabled`. A reboot leaves the bot down.
12. `/usr/bots/wg_cop` root holds 38 stray `wg_data_alpha.json.*.bak` files
    from 2026-05-11, plus `.recovered`, `.pre-restore-backup`, and an
    October-2025 `wg_data_beta.json`. Also a duplicate `config.py` next to
    `.secrets/config.py` (code reads only the latter).
13. `deploy.yml` restarts without any import check; a syntax error deploys and
    crash-loops (`Restart=always`).

Repo hygiene:

14. `.DS_Store` is tracked (shows as modified). Three overlapping feature docs
    (`IMPLEMENTATION_SUMMARY.md`, `QUICK_START_GUIDE.md`,
    `VACATION_STATUS_FEATURE.md`) should fold into README.
15. `config.example.py` omits keys the loader hard-requires
    (`NI_ID..JA_ID`, `OPENAI_API_KEY`) and README says `config.py` while code
    reads `.secrets/config.py`.
16. Local `.venv` is Python 3.9; code uses `str | None` (3.10+). Local runs
    fail; use a 3.10+ venv.
17. No tests, no CI besides deploy. `README` says heartbeat is daily; it is
    every 50 h.

## 4. Track B design: multiple homes, roles set in Telegram

Decisions agreed with the maintenance agent on 2026-09-23 (see the mailbox
`COORDINATION.md` in the main checkout). Earlier drafts of this section
(per-home files, implicit bot-owner privileges, invite codes) are superseded.

### Data layout

One versioned envelope at `WG_COP_DATA_FILE` (default `wg_data_alpha.json`,
already covered by the deploy and rsync protections):

```
{"schema_version": 2,
 "homes": {"<group chat id>": {
     "id", "name", "group_chat_id", "created", "revision", "member_seq",
     "settings": {"currency", "timezone", "weekly_report_time", "weekly_report_enabled",
                  "penalty_threshold_points", "minutes_per_point", "penalty_unit", "wg_hoeck_date"},
     "roles": {"admins": [uid], "chronicler": uid|null, "handler": uid|null},
     "members": [{"mid", "name", "status", "uid"?}],
     "expenses", "chores", "chore_log", "penalties", "last_week_violators",
     "join_requests", "weekly_report_meta", "chronicler_backup"}},
 "selections": {"<uid>": "<home id>"}}
```

* Home id = Telegram group chat id; a home always belongs to a group.
* Writes are atomic (temp file, fsync, rename) and the whole envelope is
  validated before the rename. `Store.update(home_id, fn)` is the synchronous
  read-modify-write used by jobs; `Store.save_home(snapshot)` rejects stale
  snapshots through the per-home `revision`.
* Corrupt or unversioned files are refused at startup and never replaced.

### Migration

First start with a legacy (schema-less) file: archive it as
`<file>.pre-multihome.bak` (exclusive create), build one home keyed by the
legacy `group_chat_id` (fallback `GROUP_CHAT_ID`), seed
`roles.admins=[BOT_HANDLER_ID]`, `roles.handler=BOT_HANDLER_ID`,
`roles.chronicler=CHRONICLER_ID`, stamp member `uid`s from the old
`NI_ID..JA_ID` keys (typo-tolerant name match), validate the full envelope,
write, re-read. Rollback = restore the archive and redeploy the old code
(changes made after migration then need reconciliation).

### Identity and roles

* Authorization lives only in `home.roles`. The config `BOT_HANDLER_ID` has
  no runtime privilege; it is a migration seed. Each home has a `handler`
  role (receives that home's heartbeat), editable like admins and chronicler.
* Anonymous senders (`sender_chat`, bots) are never identities.
* The last administrator of a home cannot be removed.
* Removing a member is refused while the member appears in expenses, chores
  or penalties; unlink or set vacating instead.

### Resolution

Group chat: the home registered for that chat (non-members are told to
`/join`). Private chat: the caller's single home, else their stored selection
(`Settings -> Switch Home`), else an inline picker.

### Joining (no invite codes)

`/newhome <name>` inside a group, by a Telegram group admin or creator, creates
the home and makes them its first admin. `/join` in the home's group links the
sender: a new member from their Telegram name immediately, or a request to
claim an existing unlinked member that a home admin approves. `/join` in a DM
checks `get_chat_member` against every home's group.

### Conversation state

Drafts live in `user_data["flow:<chat_id>"]` together with the home id they
started in; every step re-checks access; callback handlers verify that the
keyboard message id belongs to the caller's draft; callback data carries the
member `mid`, never the name (Telegram's 64-byte limit). Switching home in a
DM clears that chat's draft.

### Jobs

Per-home weekly report at the home's own time and timezone. Penalties are
applied once, inside `Store.update`, and the rendered text is kept in
`weekly_report_meta.pending` until delivered; delivery is retried by the
catch-up job. Daily Höck sweep and weekly chronicler export (that home only)
iterate all homes. The local backup copies the whole file. The heartbeat goes
to each home's `handler`.

## 5. Test suite: needed, and what it covers

Yes. Penalty/beer logic and balance maths are the household's money and
social score; migration touches all of it at once. Scope:

* Pure logic: `calculate_balances`, weekly report penalty transitions
  (warning -> beer -> comeback -> debt remains), `_adjust_items_to_total`,
  `_rename_member_in_data`, `_weekly_report_is_due` around Monday boundaries.
* Store: migration from a synthetic legacy fixture shaped like production
  (members with/without uid, ~30 expenses, penalties, violators) produces an
  equivalent home; balances before == balances after.
* Handlers: a fake `Update`/`Context` harness drives the expense and chore
  conversations end to end without Telegram.
* CI: `.github/workflows/test.yml` on every push/PR; `deploy.yml` gains
  `needs: test`.

## 6. Commit series (fine-grained, parallel after B1)

| # | Track | Commit | Depends on |
|---|-------|--------|------------|
| B1 | B | refactor: split maBot.py into wgcop/ package, no behaviour change | — |
| A1 | A | chore: untrack .DS_Store, fold feature docs into README, complete config.example.py | — (touches no python) |
| A2 | A | fix(log): silence httpx/apscheduler so the bot token stays out of journald | B1 |
| B2 | B | test: pytest harness, pure-logic tests, synthetic legacy fixture, CI test workflow | B1 |
| A3 | A | fix: Splitters button, /chore identity, ghost chore members, CHF in fun facts, naive datetime | B1, B2 |
| A4 | A | fix(report): send before persisting penalties; honour config group fallback | B1, B2 |
| B3 | B | feat(store): per-home files, registry, legacy migration, no write-on-read | B2 |
| B4 | B | feat(homes): resolve_home, roles model, invite codes, data-driven uid links | B3 |
| B5 | B | refactor: convert every handler and job to Home-scoped data | B4 |
| A5 | A | ci(deploy): test gate, protect data/, import smoke test, post-restart health check | B3 |
| B6 | B | feat(ui): /newhome, Switch Home, Manage Roles, Home Settings, per-home System Overview | B5 |
| A6 | A | docs+ops: README for multi-home, ops/server_checklist.md, enable service at boot | B6 |
| — | both | integration/multi-home -> main (production deploy, watch journal) | all |

## 7. Status as of 2026-09-23 (branch `worktree-multi-home`)

Delivered, in order, each commit green under pytest and pyflakes:

| Commit | What |
|--------|------|
| `d68e057` | docs: this plan |
| `b0c8113` + `f68c9d5` | B1 package split (+ fix: `wgcop/config.py` was git-ignored, renamed to `botconfig.py`; the fix commit also carries the B2 harness) |
| `ef3ef09` | B3 store: schema-2 envelope, atomic writes, revisions, legacy migration |
| `63b63ca` | B4 homes: identity, resolution, per-chat flows, roles, joining |
| `0bf900d` | B5: every handler/job on home-scoped data; navigation hand-off; data-driven sweep job |
| `04fc4b2`, `9fb2da7`, `70d1fb4`, `1fcd52e` | cherry-picked from `codex/multi-home`: store/homes boundary fixes, Telegram driver, dispatch + scheduler regressions |
| `30e57a2` | fix(bookkeeping): zero-rounding edits, non-finite receipt lines (receipts hunks from Codex 7d70518) |
| B6 | feat(ui): Manage Roles and Home Settings menus |

Behavioural summary for release review:

* First start on the server migrates `wg_data_alpha.json` in place (archive
  `wg_data_alpha.json.pre-multihome.bak`), keeps every expense/chore/penalty,
  and seeds the existing group as the first home with `BOT_HANDLER_ID` as its
  admin and handler. Members already carrying `uid` stay linked; the two
  without one are linked from the config ids (typo-tolerant), so all five are
  linked after migration in production.
* Config keys other than `TOKEN` are optional from now on.
* New homes: `/newhome <name>` in a group (Telegram group admins), `/join`
  for members, admin approval for claiming an existing profile.
* Jobs: one `sweep` every minute (weekly report per home, Höck, chronicler,
  heartbeat) plus two daily local backups. No restart needed for any setting.
* Known limitation: a home always belongs to exactly one Telegram group.
