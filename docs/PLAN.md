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
| **B — build** (multi-home, roles, settings) | build agent (this session, `worktree-multi-home`) | `wgcop/store.py`, `wgcop/homes.py`, `wgcop/config.py`, `wgcop/jobs.py`, `wgcop/handlers/{settings,admin,home}.py`, `wgcop/app.py` (wiring), `maBot.py` entry |
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

### Data layout

```
data/                        (env WG_COP_DATA_DIR, default ./data; git-ignored, deploy-protected)
  registry.json              {"users": {uid: {"active_home": id}}, "groups": {chat_id: home_id}}
  homes/<home_id>.json       {"id","name","created","settings":{...},"roles":{...},
                              "members":[...],"expenses":[...],"chores":{},"chore_log":[],
                              "penalties":{},"last_week_violators":{},
                              "weekly_report_meta":{},"chronicler_backup":{}}
```

Per-home `settings`: `group_chat_id`, `currency` (CHF), `timezone`
(Europe/Berlin), `weekly_report_time` (HH:MM), `penalty_threshold_points` (4),
`minutes_per_point` (15), `penalty_unit` ("beer"), `wg_hoeck_date`.

Per-home `roles`: `admins: [uid]`, `chronicler: uid|null`. Member objects keep
`uid` for the Telegram link. The config `BOT_HANDLER_ID` becomes the **bot
owner**: implicit admin of every home and heartbeat recipient. All other
config IDs become optional and are only used once, by the migration.

### Migration (automatic, idempotent, on first start)

If `data/homes/` is empty and the legacy `wg_data_alpha.json` exists: create
one home from it, seed `settings` from config (`GROUP_CHAT_ID`,
`weekly_report_time`, `wg_hoeck_date`), `roles.admins=[BOT_HANDLER_ID]`,
`roles.chronicler=CHRONICLER_ID`, and stamp `uid` on members using the old
`NI_ID..JA_ID` map (normalised name match, tolerant of the typo). The legacy
file is left in place and never written again; a marker
`data/migrated_from_legacy.json` records what happened. Rollback = redeploy old
code (it still reads the legacy file; changes made after migration would need
the export tool `tools/export_home.py`).

### Resolution: which home is this update for?

`resolve_home(update, context)`:

1. Group/supergroup chat -> `registry.groups[chat_id]`; if none, reply with
   how to link (`/linkhome` by an admin) and stop.
2. Private chat -> homes where the user is a member or admin. One -> that.
   Several -> `registry.users[uid].active_home` if valid, else an inline
   picker (and *Settings -> Switch Home*). None -> offer `/newhome <name>` or
   ask for an invite code.
3. The bot owner may address any home via the picker.

### Joining and linking (replaces the hard-coded UID map)

* Admin -> Member Management -> *Invite* shows a code and a deep link
  `t.me/<bot>?start=join_<code>` (rotatable). `/start join_<code>` or
  `/join <code>` in DM lets the user pick an unclaimed member name or create a
  new member from their Telegram name; the uid is stored on the member.
* Admin -> Manage Roles: add/remove admin (pick from linked members), set
  chronicler, unlink a member, transfer ownership. Every role change is a
  data write; no restart, no config edit.

### Jobs

`jobs.py` reschedules per home: weekly report `weekly:<home_id>` at that
home's time/timezone; daily Höck sweep and weekly chronicler sweep iterate all
homes; local backup copies the whole `data/` tree; heartbeat stays a singleton
to the owner.

### Systemic reconstruction that pays off

* The package split (B1) is the enabler for parallel work and for tests; it
  is not optional.
* A `Home` object (`home.data`, `home.settings`, `home.save()`) replaces the
  global `load_data()/save_data()` pair. Handlers load once and save once,
  which also removes the lost-update pattern (finding 4).
* Currency, timezone, thresholds and labels move from literals to
  `home.settings`, which is what "everything settable from Telegram" needs.

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
