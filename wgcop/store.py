"""Multi-home storage: one versioned JSON envelope, atomic writes, per-home revisions.

Envelope layout (schema_version 2)::

    {"schema_version": 2,
     "homes": {"<group chat id>": <home>},
     "selections": {"<user id>": "<home id>"},
     "migrated_from_legacy": {...} | absent}

A home id is the Telegram group chat id it belongs to (a negative int, stored
as a string key). Only one bot process may write a store. Writes are
synchronous and must never span an ``await``; ``Store.update`` is the
read-modify-write primitive and ``Store.save_home`` rejects stale snapshots via
the per-home ``revision`` counter instead of silently losing data.

"""
from __future__ import annotations

import copy
import difflib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from wgcop.botconfig import DATA_FILE

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
LEGACY_ARCHIVE_SUFFIX = ".pre-multihome.bak"
NAME_MATCH_THRESHOLD = 0.9

DEFAULT_SETTINGS = {
    "currency": "CHF",
    "timezone": "Europe/Berlin",
    "weekly_report_time": "09:30",
    "weekly_report_enabled": True,
    "penalty_threshold_points": 4,
    "minutes_per_point": 15,
    "penalty_unit": "beer",
    "wg_hoeck_date": None,
}

SINGLE_ROLES = ("chronicler", "handler")
MEMBER_STATUSES = ("active", "vacating", "retired")
LEGACY_REQUIRED_KEYS = ("members", "expenses", "chores", "penalties")
HOME_LIST_KEYS = ("members", "expenses", "chore_log")
HOME_DICT_KEYS = ("chores", "penalties", "last_week_violators", "join_requests",
                  "weekly_report_meta", "chronicler_backup", "settings", "roles")


class StoreError(RuntimeError):
    """The store is unreadable, invalid, or an operation would corrupt it."""


class StaleHomeError(StoreError):
    """The home changed on disk since this snapshot was taken."""


class HomeNotFound(StoreError):
    """No home is registered under that id."""


# --------------------------------------------------------------------------- helpers
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def numeric_id(value):
    """Coerce a Telegram id given as int or numeric string; None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        text = str(value).strip()
    except Exception:  # pragma: no cover - defensive
        return None
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def is_group_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value < 0


def is_user_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def normalise_name(name) -> str:
    if isinstance(name, dict):
        name = name.get("name", "")
    return str(name).strip().casefold() if name else ""


def valid_time(text) -> bool:
    try:
        h, m = str(text).strip().split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except (ValueError, AttributeError):
        return False


def new_home(chat_id: int, name: str) -> dict:
    return {
        "id": str(chat_id),
        "name": name,
        "group_chat_id": int(chat_id),
        "created": _now_iso(),
        "revision": 0,
        "member_seq": 0,
        "settings": dict(DEFAULT_SETTINGS),
        "roles": {"admins": [], "chronicler": None, "handler": None},
        "members": [],
        "expenses": [],
        "chores": {},
        "chore_log": [],
        "penalties": {},
        "last_week_violators": {},
        "join_requests": {},
        "weekly_report_meta": {"last_sent": None, "pending": None},
        "chronicler_backup": {"greeting_sent": False, "last_sent": None},
    }


def next_member_id(home: dict) -> str:
    home["member_seq"] = int(home.get("member_seq", 0)) + 1
    return f"m{home['member_seq']}"


def new_member(home: dict, name: str, uid=None, status: str = "active") -> dict:
    member = {"mid": next_member_id(home), "name": name, "status": status}
    if is_user_id(uid):
        member["uid"] = uid
    return member


def assign_member_ids(home: dict) -> None:
    """Give every member a stable ``mid`` (idempotent; keeps existing ids)."""
    used = {m.get("mid") for m in home["members"] if isinstance(m, dict) and m.get("mid")}
    for member in home["members"]:
        if not member.get("mid"):
            mid = next_member_id(home)
            while mid in used:
                mid = next_member_id(home)
            member["mid"] = mid
            used.add(mid)


def member_by_mid(home: dict, mid: str):
    for member in home.get("members") or []:
        if member.get("mid") == mid:
            return member
    return None


def member_for_uid(home: dict, uid):
    if not is_user_id(uid):
        return None
    for member in home.get("members") or []:
        if member.get("uid") == uid:
            return member
    return None


def member_by_name(home: dict, name):
    target = normalise_name(name)
    for member in home.get("members") or []:
        if normalise_name(member) == target:
            return member
    return None


def is_admin(home: dict, uid) -> bool:
    return is_user_id(uid) and uid in (home.get("roles") or {}).get("admins", [])


def has_access(home: dict, uid) -> bool:
    if not is_user_id(uid):
        return False
    roles = home.get("roles") or {}
    if uid in roles.get("admins", []):
        return True
    if uid in (roles.get("chronicler"), roles.get("handler")):
        return True
    return member_for_uid(home, uid) is not None


# ------------------------------------------------------------------------ validation
def validate_home(home, key=None) -> None:
    if not isinstance(home, dict):
        raise StoreError(f"Home {key!r} is not an object")
    hid = home.get("id")
    if key is not None and hid != key:
        raise StoreError(f"Home key {key!r} does not match its id {hid!r}")
    gid = home.get("group_chat_id")
    if not is_group_id(gid) or str(gid) != str(hid):
        raise StoreError(f"Home {hid!r} must be keyed by its negative group chat id")
    if not isinstance(home.get("name"), str) or not home["name"].strip():
        raise StoreError(f"Home {hid!r} needs a name")
    if not isinstance(home.get("revision"), int) or home["revision"] < 0:
        raise StoreError(f"Home {hid!r} has an invalid revision")
    for k in HOME_LIST_KEYS:
        if not isinstance(home.get(k), list):
            raise StoreError(f"Home {hid!r}: {k} must be a list")
    for k in HOME_DICT_KEYS:
        if not isinstance(home.get(k), dict):
            raise StoreError(f"Home {hid!r}: {k} must be an object")
    roles = home["roles"]
    admins = roles.get("admins")
    if not isinstance(admins, list) or not admins or not all(is_user_id(a) for a in admins):
        raise StoreError(f"Home {hid!r} must keep at least one administrator (positive user id)")
    if len(set(admins)) != len(admins):
        raise StoreError(f"Home {hid!r} lists an administrator twice")
    for role in SINGLE_ROLES:
        value = roles.get(role)
        if value is not None and not is_user_id(value):
            raise StoreError(f"Home {hid!r}: role {role} must be a positive user id or null")
    seen_mids, seen_names, seen_uids = set(), set(), set()
    for member in home["members"]:
        if not isinstance(member, dict) or not isinstance(member.get("name"), str) or not member["name"].strip():
            raise StoreError(f"Home {hid!r} has a member without a name")
        mid = member.get("mid")
        if not isinstance(mid, str) or not mid or mid in seen_mids:
            raise StoreError(f"Home {hid!r}: member {member['name']!r} needs a unique mid")
        seen_mids.add(mid)
        norm = normalise_name(member)
        if norm in seen_names:
            raise StoreError(f"Home {hid!r}: duplicate member name {member['name']!r}")
        seen_names.add(norm)
        uid = member.get("uid")
        if uid is not None:
            if not is_user_id(uid):
                raise StoreError(f"Home {hid!r}: member {member['name']!r} has an invalid uid")
            if uid in seen_uids:
                raise StoreError(f"Home {hid!r}: user {uid} is linked to two members")
            seen_uids.add(uid)
        if member.get("status") not in MEMBER_STATUSES:
            raise StoreError(f"Home {hid!r}: member {member['name']!r} has an unknown status")
        if member.get("status") == "retired" and uid is not None:
            raise StoreError(f"Home {hid!r}: retired member {member['name']!r} must not stay linked")
        retired_uid = member.get("retired_uid")
        if retired_uid is not None and not is_user_id(retired_uid):
            raise StoreError(f"Home {hid!r}: member {member['name']!r} has an invalid retired_uid")
    settings = home["settings"]
    if not valid_time(settings.get("weekly_report_time", "09:30")):
        raise StoreError(f"Home {hid!r}: weekly_report_time must be HH:MM")
    for k in ("penalty_threshold_points", "minutes_per_point"):
        v = settings.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
            raise StoreError(f"Home {hid!r}: {k} must be a positive integer")


def validate_envelope(env) -> None:
    if not isinstance(env, dict):
        raise StoreError("Store must be a JSON object")
    if env.get("schema_version") != SCHEMA_VERSION:
        raise StoreError(
            f"Store schema_version {env.get('schema_version')!r} is not {SCHEMA_VERSION}; "
            "explicit migration required"
        )
    homes = env.get("homes")
    selections = env.get("selections")
    if not isinstance(homes, dict) or not isinstance(selections, dict):
        raise StoreError("Store needs 'homes' and 'selections' objects")
    for key, home in homes.items():
        validate_home(home, key)
    for uid, hid in selections.items():
        if numeric_id(uid) is None or hid not in homes:
            raise StoreError(f"Selection {uid!r} -> {hid!r} points at an unknown home")


# ------------------------------------------------------------------------- migration
def _match_seed_name(seed_name: str, members: list):
    """Exact normalised match first, then a single near match (tolerates typos)."""
    target = normalise_name(seed_name)
    for member in members:
        if normalise_name(member) == target:
            return member
    close = [
        m for m in members
        if difflib.SequenceMatcher(None, target, normalise_name(m)).ratio() >= NAME_MATCH_THRESHOLD
    ]
    return close[0] if len(close) == 1 else None


def migrate_legacy(old: dict, seed: dict | None = None, home_name: str = "WG") -> dict:
    """Turn a legacy single-home file into a schema-2 envelope. Pure; raises StoreError."""
    seed = seed or {}
    if not isinstance(old, dict):
        raise StoreError("Legacy database must be a JSON object")
    if "schema_version" in old:
        raise StoreError("File already carries a schema_version; not a legacy database")
    missing = [k for k in LEGACY_REQUIRED_KEYS if k not in old]
    if missing:
        raise StoreError(f"Unrecognised legacy database (missing {', '.join(missing)}); migration refused")
    for key, expected in (("members", list), ("expenses", list), ("chores", dict), ("penalties", dict)):
        if not isinstance(old[key], expected):
            raise StoreError(f"Legacy {key} must be a {expected.__name__}; migration refused")

    chat_id = numeric_id(old.get("group_chat_id")) or numeric_id(seed.get("group_chat_id"))
    if not is_group_id(chat_id):
        raise StoreError("Legacy migration needs a numeric group chat id (data group_chat_id or GROUP_CHAT_ID)")
    admin = numeric_id(seed.get("admin_uid"))
    if not is_user_id(admin):
        raise StoreError("Legacy migration needs BOT_HANDLER_ID (numeric) to seed the first administrator")

    home = new_home(chat_id, home_name)
    for key in ("expenses", "chore_log"):
        home[key] = copy.deepcopy(old.get(key) or [])
    for key in ("chores", "penalties", "last_week_violators"):
        home[key] = copy.deepcopy(old.get(key) or {})
    meta = copy.deepcopy(old.get("weekly_report_meta") or {})
    home["weekly_report_meta"] = {"last_sent": meta.get("last_sent"), "pending": None}
    cb = copy.deepcopy(old.get("chronicler_backup") or {})
    home["chronicler_backup"] = {
        "greeting_sent": bool(cb.get("greeting_sent", False)),
        "last_sent": cb.get("last_sent"),
    }
    if valid_time(old.get("weekly_report_time")):
        home["settings"]["weekly_report_time"] = str(old["weekly_report_time"]).strip()
    if old.get("wg_hoeck_date"):
        home["settings"]["wg_hoeck_date"] = str(old["wg_hoeck_date"])

    members = []
    for raw in old.get("members") or []:
        if isinstance(raw, str):
            member = {"name": raw, "status": "active"}
        elif isinstance(raw, dict) and isinstance(raw.get("name"), str):
            member = {"name": raw["name"], "status": raw.get("status") or "active"}
            uid = numeric_id(raw.get("uid"))
            if is_user_id(uid):
                member["uid"] = uid
        else:
            raise StoreError(f"Legacy member record not understood: {raw!r}")
        if member["status"] not in MEMBER_STATUSES:
            member["status"] = "active"
        members.append(member)
    home["members"] = members
    assign_member_ids(home)

    linked = {m["uid"] for m in members if "uid" in m}
    for seed_name, raw_uid in (seed.get("member_uids") or {}).items():
        uid = numeric_id(raw_uid)
        if not is_user_id(uid) or uid in linked:
            continue
        member = _match_seed_name(seed_name, members)
        if member is not None and "uid" not in member:
            member["uid"] = uid
            linked.add(uid)

    home["roles"]["admins"] = [admin]
    handler = numeric_id(seed.get("handler_uid"))
    home["roles"]["handler"] = handler if is_user_id(handler) else admin
    chronicler = numeric_id(seed.get("chronicler_uid"))
    home["roles"]["chronicler"] = chronicler if is_user_id(chronicler) else None

    env = {
        "schema_version": SCHEMA_VERSION,
        "homes": {home["id"]: home},
        "selections": {},
        "migrated_from_legacy": {
            "at": _now_iso(),
            "members": len(members),
            "linked_members": len(linked),
            "expenses": len(home["expenses"]),
        },
    }
    validate_envelope(env)
    return env


# ------------------------------------------------------------------------------ store
class Store:
    def __init__(self, path):
        self.path = Path(path)

    # -- raw file access -------------------------------------------------------
    def exists(self) -> bool:
        return self.path.exists()

    def _load_raw(self):
        def reject_constant(value):
            raise ValueError(f"Non-finite JSON number: {value}")
        try:
            return json.loads(self.path.read_text(encoding="utf-8"), parse_constant=reject_constant)
        except (ValueError, OSError) as exc:
            raise StoreError(f"Cannot read {self.path}: {exc}") from exc

    def read(self) -> dict:
        """Return the validated envelope (a fresh copy every call)."""
        if not self.path.exists():
            return {"schema_version": SCHEMA_VERSION, "homes": {}, "selections": {}}
        env = self._load_raw()
        validate_envelope(env)
        return env

    def _write(self, env: dict) -> None:
        validate_envelope(env)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(env, fh, indent=2, ensure_ascii=False, allow_nan=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def initialize(self, seed: dict | None = None, home_name: str = "WG") -> str:
        """Create, validate, or migrate the file. Returns 'created' | 'ok' | 'migrated'."""
        if not self.path.exists():
            self._write(self.read())
            return "created"
        old = self._load_raw()
        if isinstance(old, dict) and "schema_version" in old:
            validate_envelope(old)
            return "ok"
        env = migrate_legacy(old, seed, home_name=home_name)  # validated
        archive = self.path.with_name(self.path.name + LEGACY_ARCHIVE_SUFFIX)
        if archive.exists():
            # After a rollback (archive restored, old code kept writing) the legacy
            # file is newer than the first archive: keep every pre-migration copy.
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            archive = self.path.with_name(f"{self.path.name}{LEGACY_ARCHIVE_SUFFIX}.{stamp}")
        fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(self.path.read_bytes())
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(archive, 0o600)
        self._write(env)
        validate_envelope(self._load_raw())
        logger.info("Migrated legacy database to schema %s; original kept at %s", SCHEMA_VERSION, archive)
        return "migrated"

    # -- reads ---------------------------------------------------------------------
    def list_homes(self) -> list[dict]:
        return list(self.read()["homes"].values())

    def get_home(self, home_id) -> dict:
        env = self.read()
        try:
            return env["homes"][str(home_id)]
        except KeyError as exc:
            raise HomeNotFound(f"No home {home_id!r}") from exc

    def home_for_group(self, chat_id):
        return self.read()["homes"].get(str(chat_id))

    def homes_for_user(self, uid) -> list[dict]:
        return [h for h in self.read()["homes"].values() if has_access(h, uid)]

    # -- writes --------------------------------------------------------------------
    def update(self, home_id, fn):
        """Synchronous read-modify-write. ``fn(home)`` mutates in place; its return
        value is returned alongside the saved home. Never call across an await."""
        env = self.read()
        hid = str(home_id)
        if hid not in env["homes"]:
            raise HomeNotFound(f"No home {home_id!r}")
        home = env["homes"][hid]
        result = fn(home)
        home["revision"] = int(home["revision"]) + 1
        self._write(env)
        return home, result

    def save_home(self, home: dict) -> dict:
        """Persist a snapshot obtained from get_home(); refuses if it went stale."""
        env = self.read()
        hid = str(home.get("id"))
        current = env["homes"].get(hid)
        if current is None:
            raise HomeNotFound(f"No home {hid!r}")
        if current["revision"] != home.get("revision"):
            raise StaleHomeError(f"Home {hid!r} changed on disk; reload and retry")
        saved = copy.deepcopy(home)
        saved["revision"] = int(saved["revision"]) + 1
        env["homes"][hid] = saved
        self._write(env)
        home["revision"] = saved["revision"]
        return home

    def create_home(self, chat_id, name: str, creator_uid, creator_name: str) -> dict:
        chat_id = numeric_id(chat_id)
        creator_uid = numeric_id(creator_uid)
        if not is_group_id(chat_id):
            raise StoreError("A home needs a Telegram group chat id")
        if not is_user_id(creator_uid):
            raise StoreError("A home needs an identified creator")
        name = (name or "").strip()
        if not name:
            raise StoreError("A home needs a name")
        env = self.read()
        hid = str(chat_id)
        if hid in env["homes"]:
            raise StoreError("This group already has a home")
        home = new_home(chat_id, name)
        home["members"].append(new_member(home, creator_name.strip() or "Admin", uid=creator_uid))
        home["roles"]["admins"] = [creator_uid]
        home["roles"]["handler"] = creator_uid
        env["homes"][hid] = home
        self._write(env)
        return home

    def delete_home(self, home_id) -> None:
        env = self.read()
        hid = str(home_id)
        if hid not in env["homes"]:
            raise HomeNotFound(f"No home {home_id!r}")
        del env["homes"][hid]
        env["selections"] = {u: h for u, h in env["selections"].items() if h != hid}
        self._write(env)

    def select_home(self, uid, home_id) -> None:
        env = self.read()
        hid = str(home_id)
        home = env["homes"].get(hid)
        if home is None or not has_access(home, uid):
            raise StoreError("Not a member of that home")
        env["selections"][str(uid)] = hid
        self._write(env)

    def clear_selection(self, uid) -> None:
        env = self.read()
        if env["selections"].pop(str(uid), None) is not None:
            self._write(env)

    def selected_home(self, uid):
        env = self.read()
        hid = env["selections"].get(str(uid))
        home = env["homes"].get(hid) if hid else None
        return home if home and has_access(home, uid) else None

    def migrate_group(self, old_chat_id, new_chat_id) -> dict | None:
        """Re-key a home after Telegram upgrades a group to a supergroup."""
        env = self.read()
        old, new = str(old_chat_id), str(new_chat_id)
        home = env["homes"].get(old)
        if home is None:
            return None
        if new in env["homes"]:
            raise StoreError("Cannot re-key: the new group already has a home")
        del env["homes"][old]
        home["id"] = new
        home["group_chat_id"] = int(new_chat_id)
        home["revision"] = int(home["revision"]) + 1
        env["homes"][new] = home
        env["selections"] = {u: (new if h == old else h) for u, h in env["selections"].items()}
        self._write(env)
        return home

    def export_home(self, home_id) -> str:
        """JSON text of exactly one home (for that home's chronicler)."""
        home = self.get_home(home_id)
        return json.dumps(
            {"schema_version": SCHEMA_VERSION, "exported": _now_iso(), "home": home},
            indent=2,
            ensure_ascii=False,
        )


_STORE: Store | None = None


def get_store() -> Store:
    global _STORE
    if _STORE is None:
        _STORE = Store(DATA_FILE)
    return _STORE


def set_store(store: Store | None) -> None:
    global _STORE
    _STORE = store
