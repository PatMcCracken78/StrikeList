#
# Strike List - Copyright (C) 2026 _Storm_ [2678385]
# Free software under the GNU General Public License v3 or later (see LICENSE.txt).
# Includes the awards engine from YATA by Kivou [2000607] (GPL-3).
"""
Strike List engine: settings, local database and Torn API calls. No GUI code here.

Each person who runs the app gets their own data folder, so your friend's key and
targets never mix with yours:
  macOS   ~/Library/Application Support/StrikeList
  Windows %APPDATA%\\StrikeList
"""
import json
import os
import re
import sqlite3
import sys
import threading
import time
from collections import deque
from pathlib import Path

import requests

API_ROOT = os.environ.get("TORN_API_ROOT", "https://api.torn.com")
FFS_ROOT = os.environ.get("FFS_API_ROOT", "https://ffscouter.com")
FFS_HOME = "https://ffscouter.com/"


def data_dir() -> Path:
    if os.environ.get("STRIKE_HOME"):
        d = Path(os.environ["STRIKE_HOME"])
    elif sys.platform == "win32":
        d = Path(os.environ.get("APPDATA", Path.home())) / "StrikeList"
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "StrikeList"
    else:
        d = Path.home() / ".strikelist"
    d.mkdir(parents=True, exist_ok=True)
    return d


DB_PATH = data_dir() / "strike.db"
CFG_PATH = data_dir() / "config.json"

DEFAULT_CFG = {
    "api_key": "",
    # Torn allows 100 calls/min per *player* across ALL keys and tools you run.
    "max_calls_per_min": 40,
    "attack_sync_minutes": 5,
    "auto_refresh_minutes": 5,
    "auto_add_from_attacks": False,
    "ffscouter_enabled": False,   # opt-in: sends your Torn key to ffscouter.com
    "war_refresh_seconds": 30,
    "target_min_ff": 2.5,         # targets below this FF get flagged for clean-up
    "cleanup_keep_flagged": True,
    # header shortcut buttons - plain links that open Torn in your browser (no actions are taken)
    "chain_alert_seconds": 60,    # 0 = off
    "chain_alert_sound": True,
    "chain_alert_notify": True,
    "armory_medical_url": "https://www.torn.com/factions.php?step=your#/tab=armoury&start=0&sub=medical",
    "armory_drugs_url": "https://www.torn.com/factions.php?step=your#/tab=armoury&start=0&sub=drugs",
}

_db_lock = threading.Lock()


# ----------------------------------------------------------------- settings
def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if CFG_PATH.exists():
        try:
            cfg.update(json.loads(CFG_PATH.read_text()))
        except Exception:
            pass
    return cfg


def save_cfg(cfg):
    CFG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(CFG_PATH, 0o600)
    except OSError:
        pass


# ----------------------------------------------------------------- database
def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


SCHEMA = 2


def init_db():
    with _db_lock, db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        r = con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
        if not r or int(r["v"] or 0) < SCHEMA:
            # attacks are just a cache of Torn's log: rebuild them with the v2 API
            con.execute("DROP TABLE IF EXISTS attacks")
            con.execute("DELETE FROM meta WHERE k IN ('attacks_since','attacks_synced')")
            con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('schema',?)", (str(SCHEMA),))
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS attacks (
                id INTEGER PRIMARY KEY, code TEXT, started INTEGER, ended INTEGER,
                attacker_id INTEGER, attacker_name TEXT, attacker_faction TEXT,
                defender_id INTEGER, defender_name TEXT, defender_faction TEXT,
                result TEXT, respect REAL, base_respect REAL, fair_fight REAL,
                chain INTEGER, stealthed INTEGER, ranked_war INTEGER, raid INTEGER,
                outgoing INTEGER, attacker_level INTEGER, defender_level INTEGER
            );
            CREATE INDEX IF NOT EXISTS ix_att_def ON attacks(defender_id);
            CREATE INDEX IF NOT EXISTS ix_att_ended ON attacks(ended);
            CREATE TABLE IF NOT EXISTS fchains (
                id INTEGER PRIMARY KEY, length INTEGER, respect REAL, start INTEGER, end INTEGER
            );
            CREATE TABLE IF NOT EXISTS enemy (
                id INTEGER PRIMARY KEY, faction_id INTEGER, name TEXT, level INTEGER, position TEXT,
                days_in_faction INTEGER, last_action_status TEXT, last_action_ts INTEGER,
                status_state TEXT, status_desc TEXT, status_until INTEGER, updated INTEGER
            );
            CREATE TABLE IF NOT EXISTS ffstats (
                id INTEGER PRIMARY KEY, fair_fight REAL, bs_estimate REAL, bs_human TEXT,
                source TEXT, last_updated INTEGER, fetched INTEGER
            );
            CREATE TABLE IF NOT EXISTS warlist (
                id INTEGER PRIMARY KEY, faction_id INTEGER, added INTEGER, note TEXT DEFAULT '',
                color INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS targets (
                id INTEGER PRIMARY KEY, name TEXT, level INTEGER, faction TEXT,
                status_state TEXT, status_desc TEXT, status_color TEXT, status_until INTEGER,
                life_cur INTEGER, life_max INTEGER,
                last_action_status TEXT, last_action_ts INTEGER,
                note TEXT DEFAULT '', color INTEGER DEFAULT 0,
                added INTEGER, updated INTEGER, error TEXT
            );
            """
        )


def meta_get(k, default=None):
    with db() as con:
        r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r and r["v"] != "" else default


def meta_set(k, v):
    with _db_lock, db() as con:
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, str(v)))


# ----------------------------------------------------------------- Torn API
class TornError(Exception):
    FATAL = {-1, 2, 10, 13, 18}  # missing / bad / paused / inactive key

    def __init__(self, code, msg):
        super().__init__(f"Torn API error {code}: {msg}")
        self.code = code


class RateLimiter:
    """Sliding 60-second window so we never exceed the configured calls/min."""

    def __init__(self):
        self.calls = deque()
        self.lock = threading.Lock()

    def wait(self, per_min):
        while True:
            with self.lock:
                now = time.time()
                while self.calls and now - self.calls[0] > 60:
                    self.calls.popleft()
                if len(self.calls) < per_min:
                    self.calls.append(now)
                    return
                sleep_for = 60 - (now - self.calls[0]) + 0.05
            time.sleep(max(sleep_for, 0.05))

    def used_last_minute(self):
        now = time.time()
        with self.lock:
            return sum(1 for t in self.calls if now - t <= 60)


limiter = RateLimiter()


def torn(path, key=None, **params):
    cfg = load_cfg()
    key = key or cfg["api_key"]
    if not key:
        raise TornError(-1, "No API key set - open Settings")
    limiter.wait(max(1, int(cfg["max_calls_per_min"])))
    params["key"] = key
    params.setdefault("comment", "StrikeList")  # shows in your Torn API usage log
    try:
        r = requests.get(f"{API_ROOT}/{path}", params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        raise TornError(0, f"network problem ({e.__class__.__name__})") from None
    if "error" in data:
        e = data["error"]
        raise TornError(e.get("code"), e.get("error"))
    return data


def check_key(key):
    """Validate a key and return (player_id, name). Also resets data if it's a different player."""
    d = torn("user/", key=key, selections="basic")
    pid = str(d["player_id"])
    if meta_get("player_id") and meta_get("player_id") != pid:
        with _db_lock, db() as con:  # different account: don't mix histories
            con.execute("DELETE FROM attacks")
            con.execute("DELETE FROM meta WHERE k IN ('attacks_since','attacks_synced')")
    meta_set("player_id", pid)
    meta_set("player_name", d["name"])
    return pid, d["name"]


# ----------------------------------------------------------------- attacks
def _mod(mods, *names):
    for n in names:
        v = mods.get(n)
        if v not in (None, 0, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 1.0


def parse_attack(a, me):
    """One attack from GET /v2/user/attacks."""
    mods = a.get("modifiers") or {}
    respect = float(a.get("respect_gain") or 0)
    ff = _mod(mods, "fair_fight")
    bonus = (_mod(mods, "chain") * _mod(mods, "war") * _mod(mods, "retaliation")
             * _mod(mods, "group") * _mod(mods, "overseas") * _mod(mods, "warlord"))
    att = a.get("attacker") or {}   # null when someone attacked you stealthed
    dfd = a.get("defender") or {}
    fac = lambda p: (p.get("faction") or {}).get("name") or ""
    return (
        int(a["id"]), a.get("code"), a.get("started"), a.get("ended"),
        att.get("id"), att.get("name"), fac(att),
        dfd.get("id"), dfd.get("name"), fac(dfd),
        a.get("result"), respect, round(respect / bonus, 2) if bonus else respect, ff,
        a.get("chain"), int(bool(a.get("is_stealthed"))), int(bool(a.get("is_ranked_war"))),
        int(bool(a.get("is_raid"))), 1 if str(att.get("id")) == str(me) else 0,
        att.get("level"), dfd.get("level"),
    )


WIN_RESULTS = ("Attacked", "Mugged", "Hospitalized", "Arrested", "Special", "Bounty")


PAGE = 100


def sync_attacks(progress=None):
    """
    Pull attacks newest-first from /v2/user/attacks, walking back with `to=` until we
    overlap what is already stored. The first run backfills your whole history.
    """
    me = meta_get("player_id")
    if not me:
        me, _ = check_key(load_cfg()["api_key"])
    cfg = load_cfg()
    added, pages = 0, 0
    since = int(meta_get("attacks_since", 0))
    newest_seen, to = since, None
    while True:
        params = {"limit": PAGE, "sort": "DESC", "timestamp": int(time.time())}
        if to:
            params["to"] = to
        atks = torn("v2/user/attacks", **params).get("attacks") or []
        pages += 1
        if not atks:
            break
        rows = [parse_attack(a, me) for a in atks]
        with _db_lock, db() as con:
            before = con.total_changes
            con.executemany("INSERT OR IGNORE INTO attacks VALUES (" + ",".join("?" * 21) + ")", rows)
            new_here = con.total_changes - before
            added += new_here
            if cfg.get("auto_add_from_attacks"):
                for r in rows:
                    if r[18] == 1 and r[10] in WIN_RESULTS:
                        con.execute("INSERT OR IGNORE INTO targets(id,name,level,added) VALUES(?,?,?,?)",
                                    (r[7], r[8], r[20], int(time.time())))
        if progress:
            progress(f"Pulling attacks: page {pages}, {added} new…")
        started = [r[2] or 0 for r in rows]
        newest_seen = max(newest_seen, max(started))
        oldest = min(started)
        # stop: last page, reached what we already had, or no progress
        if len(atks) < PAGE or (since and oldest <= since):
            break
        nxt = oldest  # `to` is inclusive, duplicates are ignored by INSERT OR IGNORE
        if to is not None and nxt >= to:
            nxt = to - 1
        if pages >= 2000:  # safety net
            break
        to = nxt
    meta_set("attacks_since", max(0, newest_seen - 600))  # small overlap for late-finishing attacks
    meta_set("attacks_synced", int(time.time()))
    return added


def list_attacks(direction="all", limit=500):
    where = {"out": "WHERE outgoing=1", "in": "WHERE outgoing=0"}.get(direction, "")
    with db() as con:
        rows = [dict(r) for r in con.execute(f"SELECT * FROM attacks {where} ORDER BY ended DESC LIMIT ?", (limit,))]
        tset = {r["id"] for r in con.execute("SELECT id FROM targets")}
    for r in rows:
        r["other_id"] = r["defender_id"] if r["outgoing"] else r["attacker_id"]
        r["is_target"] = r["other_id"] in tset
    return rows


def stats():
    with db() as con:
        s = dict(con.execute(
            """SELECT SUM(outgoing=1) out_n, SUM(outgoing=0) in_n,
                 ROUND(SUM(CASE WHEN outgoing=1 THEN respect END),2) respect,
                 ROUND(AVG(CASE WHEN outgoing=1 AND respect>0 THEN fair_fight END),2) avg_ff
               FROM attacks""").fetchone())
        s["results"] = [dict(r) for r in con.execute(
            "SELECT result, COUNT(*) n FROM attacks WHERE outgoing=1 GROUP BY result ORDER BY n DESC")]
    return s


# ----------------------------------------------------------------- targets
TARGET_SQL = """
SELECT t.*,
  (SELECT MAX(ended) FROM attacks a WHERE a.defender_id=t.id AND a.outgoing=1) AS last_attack,
  (SELECT result FROM attacks a WHERE a.defender_id=t.id AND a.outgoing=1 ORDER BY ended DESC LIMIT 1) AS last_result,
  (SELECT fair_fight FROM attacks a WHERE a.defender_id=t.id AND a.outgoing=1 AND a.respect>0 ORDER BY ended DESC LIMIT 1) AS ff,
  (SELECT base_respect FROM attacks a WHERE a.defender_id=t.id AND a.outgoing=1 AND a.respect>0 ORDER BY ended DESC LIMIT 1) AS base_respect,
  (SELECT COUNT(*) FROM attacks a WHERE a.defender_id=t.id AND a.outgoing=1) AS n_attacks,
  (SELECT defender_name FROM attacks a WHERE a.defender_id=t.id ORDER BY ended DESC LIMIT 1) AS hist_name,
  (SELECT defender_level FROM attacks a WHERE a.defender_id=t.id ORDER BY ended DESC LIMIT 1) AS hist_level,
  (SELECT fair_fight FROM ffstats f WHERE f.id=t.id) AS ff_now,
  (SELECT fetched FROM ffstats f WHERE f.id=t.id) AS ff_now_ts
FROM targets t
"""


def list_targets():
    with db() as con:
        rows = [dict(r) for r in con.execute(TARGET_SQL)]
    for r in rows:
        r["name"] = r.get("name") or r.get("hist_name")
        r["level"] = r.get("level") or r.get("hist_level")
    return rows


def parse_ids(text):
    """Accepts '123, 456' or pasted profile links (…profiles.php?XID=789)."""
    return sorted({int(x) for x in re.findall(r"(?<!\d)\d{1,8}(?!\d)", str(text))})


def add_targets(ids):
    now = int(time.time())
    with _db_lock, db() as con:
        for i in ids:
            con.execute("INSERT OR IGNORE INTO targets(id, added) VALUES(?,?)", (i, now))


def remove_target(tid):
    with _db_lock, db() as con:
        con.execute("DELETE FROM targets WHERE id=?", (tid,))


def set_note(tid, note):
    with _db_lock, db() as con:
        con.execute("UPDATE targets SET note=? WHERE id=?", (str(note)[:500], tid))


def set_color(tid, color):
    with _db_lock, db() as con:
        con.execute("UPDATE targets SET color=? WHERE id=?", (int(color) % 4, tid))


def refresh_target(tid):
    now = int(time.time())
    try:
        p = torn(f"user/{int(tid)}", selections="profile")
    except TornError as e:
        with _db_lock, db() as con:
            con.execute("UPDATE targets SET error=?, updated=? WHERE id=?", (str(e), now, tid))
        raise
    st, life, la, fac = (p.get(k) or {} for k in ("status", "life", "last_action", "faction"))
    with _db_lock, db() as con:
        con.execute(
            """UPDATE targets SET name=?, level=?, faction=?, status_state=?, status_desc=?,
                 status_color=?, status_until=?, life_cur=?, life_max=?,
                 last_action_status=?, last_action_ts=?, updated=?, error=NULL WHERE id=?""",
            (p.get("name"), p.get("level"), fac.get("faction_name") or "",
             st.get("state"), st.get("description"), st.get("color"), st.get("until") or 0,
             life.get("current"), life.get("maximum"), la.get("status"), la.get("timestamp"), now, tid),
        )


def target_ids_oldest_first():
    with db() as con:
        return [r["id"] for r in con.execute("SELECT id FROM targets ORDER BY updated IS NOT NULL, updated")]


# ----------------------------------------------------------------- awards (engine: YATA, GPL-3)
AWARD_USER_SELECTIONS = ("personalstats,crimes,education,battlestats,workstats,skills,perks,gym,"
                         "networth,merits,profile,medals,honors,icons,bars,weaponexp,hof")
AWARD_DEFS_MAX_AGE = 24 * 3600


def _award_defs(force=False):
    """Torn's list of all honors + medals, cached for a day (it rarely changes)."""
    import copy
    ts = int(meta_get("award_defs_ts", 0) or 0)
    if not force and time.time() - ts < AWARD_DEFS_MAX_AGE and meta_get("award_defs"):
        return json.loads(meta_get("award_defs"))
    from yata_awards import HONORS_UNREACH
    req = torn("torn/", selections="medals,honors")
    defs = {"honors": {}, "medals": {}}
    for kind in ("honors", "medals"):
        for k, v in (req.get(kind) or {}).items():
            if v.get("type") in (1,):                     # same exclusions YATA applies
                continue
            if kind == "medals" and v.get("type") == "CRM" and int(k) < 241:   # retired crimes 1.0 medals
                continue
            v = copy.deepcopy(v)
            v["id"] = int(k)
            if kind == "honors":
                v["unreach"] = 1 if int(k) in HONORS_UNREACH else 0
            defs[kind][k] = v
    meta_set("award_defs", json.dumps(defs))
    meta_set("award_defs_ts", int(time.time()))
    return defs


def _plain(html):
    t = re.sub(r"<br\s*/?>", "\n", str(html or ""))
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def compute_awards(defs, user):
    """Run YATA's award engine over every category. Returns (rows, errors)."""
    import copy
    from yata_awards import AWARDS_CAT, createAwards
    rows, errors = [], []
    seen = set()
    for cat in AWARDS_CAT:
        try:
            awards, _ = createAwards(copy.deepcopy(defs), user, cat)
        except Exception as e:  # one broken category must not hide the others
            errors.append(f"{cat}: {e.__class__.__name__}: {e}")
            continue
        for subcat, items in awards.items():
            for key, a in items.items():
                if key in seen:
                    continue
                seen.add(key)
                goal, cur = a.get("goal"), a.get("current")
                left = a.get("left")
                rows.append({
                    "key": key, "type": "Honor" if key.startswith("h_") else "Medal",
                    "category": cat.title(), "subcategory": subcat,
                    "name": a.get("name", ""), "description": a.get("description", ""),
                    "goal": goal if isinstance(goal, (int, float)) else None,
                    "current": cur if isinstance(cur, (int, float)) else None,
                    "achieve": float(a.get("achieve") or 0),
                    "left": float(left) if isinstance(left, (int, float)) and left >= 0 else None,
                    "comment": _plain(a.get("comment") if not isinstance(a.get("comment"), list) else a["comment"][0]),
                    "awarded_time": int(a.get("awarded_time") or 0),
                    "circulation": int(a.get("circulation") or 0),
                    "rarity": a.get("rarity", ""),
                    "unreach": int(a.get("unreach") or 0),
                })
    return rows, errors


def fetch_awards(progress=None):
    """2-3 API calls: award list (daily), your stats, and crimes 2.0 progress."""
    if progress:
        progress("Loading Torn's award list…")
    defs = _award_defs()
    if progress:
        progress("Loading your stats…")
    try:
        user = torn("user/", selections=AWARD_USER_SELECTIONS, cat="all")
    except TornError as e:
        if e.code == 16:
            raise TornError(16, "Awards need a key with at least Limited Access") from None
        raise
    try:
        user["crimes2"] = torn("v2/user/7/crimes").get("crimes", {})
    except TornError:
        user["crimes2"] = {}
    rows, errors = compute_awards(defs, user)
    out = {"rows": rows, "errors": errors, "ts": int(time.time()),
           "n_honors": len(defs["honors"]), "n_medals": len(defs["medals"]),
           "have_honors": len(user.get("honors_awarded") or []),
           "have_medals": len(user.get("medals_awarded") or [])}
    meta_set("awards_cache", json.dumps(out))
    return out


def cached_awards():
    raw = meta_get("awards_cache")
    return json.loads(raw) if raw else None


# ----------------------------------------------------------------- chains (from stored attacks)
def sync_faction_chains():
    """Your faction's finished chains (id, length, respect, start, end). Returns count, or None if not allowed."""
    try:
        data = torn("v2/faction/chains", limit=100, sort="DESC", timestamp=int(time.time()))
    except TornError as e:
        meta_set("fchains_error", str(e))
        return None
    rows = []
    for c in data.get("chains") or []:
        try:
            rows.append((int(c["id"]), int(c.get("chain") or 0), float(c.get("respect") or 0),
                         int(c.get("start") or 0), int(c.get("end") or 0)))
        except (KeyError, TypeError, ValueError):
            continue
    with _db_lock, db() as con:
        con.executemany("INSERT OR REPLACE INTO fchains VALUES (?,?,?,?,?)", rows)
    meta_set("fchains_error", "")
    meta_set("fchains_synced", int(time.time()))
    return len(rows)


def _summarise(c, hits):
    n = len(hits)
    if not n:
        return None
    resp = sum(h["respect"] or 0 for h in hits)
    return {
        "id": c.get("id"), "faction_chain": c.get("faction_chain", False),
        "start": c.get("start") or hits[0]["ended"], "end": c.get("end") or hits[-1]["ended"],
        "length": c.get("length") or max(h["chain"] for h in hits),
        "faction_respect": c.get("respect"),
        "hits": n, "respect": round(resp, 2), "avg_respect": round(resp / n, 2),
        "avg_fr": round(sum(h["base_respect"] or 0 for h in hits) / n, 2),
        "avg_ff": round(sum(h["fair_fight"] or 0 for h in hits) / n, 2),
        "best": round(max(h["respect"] or 0 for h in hits), 2),
        "war_hits": sum(1 for h in hits if h["ranked_war"]),
        "share": round(100.0 * n / c["length"], 1) if c.get("length") else None,
    }


def list_chains():
    """
    Your chain hits grouped per chain, newest first.
    Uses your faction's real chain list when available (exact windows); otherwise splits
    your hits wherever the chain count goes back down (a new chain started).
    """
    with db() as con:
        hits = [dict(r) for r in con.execute(
            """SELECT ended, chain, respect, base_respect, fair_fight, ranked_war
               FROM attacks WHERE outgoing=1 AND chain>0 ORDER BY ended""")]
        fchains = [dict(r) for r in con.execute("SELECT * FROM fchains ORDER BY start")]
    out, used = [], set()
    for fc in fchains:
        grp = []
        for i, h in enumerate(hits):
            # inside the chain's time window and not beyond its final length
            if i not in used and fc["start"] <= h["ended"] <= fc["end"] and h["chain"] <= max(fc["length"], 1):
                grp.append(h)
                used.add(i)
        s = _summarise({**fc, "faction_chain": True}, grp)
        if s:
            out.append(s)
    # hits not inside a known faction chain (ongoing chain, or no faction-chain access)
    rest = [h for i, h in enumerate(hits) if i not in used]
    grp, last = [], None
    for h in rest:
        if last is not None and h["chain"] < last:
            out.append(_summarise({}, grp))
            grp = []
        grp.append(h)
        last = h["chain"]
    if grp:
        out.append(_summarise({}, grp))
    out = [c for c in out if c]
    out.sort(key=lambda c: c["end"], reverse=True)
    return out


# ----------------------------------------------------------------- war: matchup + enemy roster
def _members_list(raw):
    """v1 returns {id: {...}}, v2 returns [{id:..., ...}] - normalise to a list of dicts with 'id'."""
    if isinstance(raw, dict):
        return [{**v, "id": int(k)} for k, v in raw.items()]
    return [m for m in (raw or []) if isinstance(m, dict) and m.get("id")]


def detect_war():
    """
    Your faction's current or upcoming ranked war -> dict or None.
    Uses v1 faction basic (includes ranked_wars) with a v2 fallback.
    """
    my_fac = None
    war = None
    try:
        d = torn("faction/", selections="basic")
        my_fac = int(d.get("ID") or d.get("id") or 0) or None
        meta_set("my_faction_id", my_fac or "")
        meta_set("my_faction_name", d.get("name") or "")
        for wid, w in (d.get("ranked_wars") or {}).items():
            info = w.get("war") or {}
            if info.get("end") and info["end"] < time.time():
                continue
            facs = w.get("factions") or {}
            enemy = [(int(k), v) for k, v in facs.items() if int(k) != my_fac]
            if enemy:
                war = {"war_id": int(wid), "enemy_id": enemy[0][0], "enemy_name": enemy[0][1].get("name", ""),
                       "start": int(info.get("start") or 0), "end": int(info.get("end") or 0),
                       "target": info.get("target"), "scores": {int(k): v.get("score") for k, v in facs.items()}}
    except TornError as e:
        if e.code in TornError.FATAL:
            raise
    if war is None:
        try:
            d = torn("v2/faction/wars")
            r = (d.get("wars") or {}).get("ranked")
            if r and not (r.get("end") and r["end"] < time.time()):
                facs = r.get("factions") or []
                enemy = [f for f in facs if int(f.get("id", 0)) != (my_fac or -1)]
                if enemy:
                    war = {"war_id": r.get("war_id") or r.get("id"), "enemy_id": int(enemy[0]["id"]),
                           "enemy_name": enemy[0].get("name", ""), "start": int(r.get("start") or 0),
                           "end": int(r.get("end") or 0), "target": r.get("target"),
                           "scores": {int(f["id"]): f.get("score") for f in facs}}
        except TornError:
            pass
    meta_set("war_detected", json.dumps(war) if war else "")
    meta_set("war_checked", int(time.time()))
    return war


def load_enemy(faction_id):
    """One call: the whole roster of a faction with status + last action. Returns faction name."""
    faction_id = int(faction_id)
    d = torn(f"faction/{faction_id}", selections="basic")
    members = _members_list(d.get("members"))
    if not members:
        raise TornError(0, f"Faction {faction_id} has no members or does not exist")
    now = int(time.time())
    rows = []
    for m in members:
        st, la = m.get("status") or {}, m.get("last_action") or {}
        rows.append((m["id"], faction_id, m.get("name"), m.get("level"), m.get("position"),
                     m.get("days_in_faction"), la.get("status"), la.get("timestamp"),
                     st.get("state"), st.get("description"), st.get("until") or 0, now))
    with _db_lock, db() as con:
        old_fac = con.execute("SELECT v FROM meta WHERE k='war_faction_id'").fetchone()
        if not old_fac or str(old_fac["v"]) != str(faction_id):
            con.execute("DELETE FROM enemy")  # switched to a different faction
        else:
            ids = [r[0] for r in rows]  # drop people who left the faction
            con.execute(f"DELETE FROM enemy WHERE id NOT IN ({','.join('?' * len(ids))})", ids)
        con.executemany("INSERT OR REPLACE INTO enemy VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('war_faction_id',?)", (str(faction_id),))
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('war_faction_name',?)", (d.get("name") or "",))
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('enemy_updated',?)", (str(now),))
    return d.get("name") or str(faction_id)


def my_battlestats():
    """Your total battle stats (needs Limited key) - cached 6h, used for the 'vs you' column."""
    ts = int(meta_get("my_bs_ts", 0) or 0)
    if time.time() - ts < 6 * 3600 and meta_get("my_bs_total"):
        return float(meta_get("my_bs_total"))
    try:
        d = torn("user/", selections="battlestats")
    except TornError:
        return None
    total = d.get("total") or sum(float(d.get(k) or 0) for k in ("strength", "speed", "dexterity", "defense"))
    meta_set("my_bs_total", total)
    meta_set("my_bs_ts", int(time.time()))
    return float(total)


# ----------------------------------------------------------------- FF Scouter (opt-in)
class FFSError(Exception):
    pass


def _ffs(path, **params):
    key = load_cfg()["api_key"]
    if not key:
        raise FFSError("No API key set")
    params["key"] = key
    try:
        r = requests.get(f"{FFS_ROOT}{path}", params=params, timeout=25)
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise FFSError(f"FF Scouter unreachable ({e.__class__.__name__})") from None
    if isinstance(data, dict) and data.get("error"):
        code = data.get("code")
        hint = " - register your key at ffscouter.com (Settings tab)" if code in (6, 7) else ""
        if code == 21:
            hint = f" - rate limited, retry in {data.get('retry_after_seconds', 60)}s"
        raise FFSError(f"FF Scouter error {code}: {data['error']}{hint}")
    return data


def ffs_check_key():
    d = _ffs("/api/v1/check-key")
    return bool(d.get("is_registered")), d


def ffs_register():
    """Only called after the user ticks that they read FF Scouter's policy/terms."""
    key = load_cfg()["api_key"]
    try:
        r = requests.post(f"{FFS_ROOT}/api/v1/register", timeout=25,
                          json={"key": key, "agree_to_data_policy": True, "signup_source": "StrikeList"})
        d = r.json()
    except (requests.RequestException, ValueError) as e:
        raise FFSError(f"FF Scouter unreachable ({e.__class__.__name__})") from None
    if d.get("success") or d.get("code") == 8:  # 8 = already registered
        return True
    raise FFSError(f"FF Scouter error {d.get('code')}: {d.get('error')}")


def fetch_ffstats(ids, max_age=6 * 3600):
    """Estimated battle stats + fair fight (vs you) for up to any number of players, 205 per call."""
    if not load_cfg().get("ffscouter_enabled"):
        return 0
    now = int(time.time())
    with db() as con:
        fresh = {r["id"] for r in con.execute("SELECT id FROM ffstats WHERE fetched>?", (now - max_age,))}
    todo = [int(i) for i in ids if int(i) not in fresh]
    got = 0
    for i in range(0, len(todo), 205):
        chunk = todo[i:i + 205]
        data = _ffs("/api/v1/get-stats", targets=",".join(map(str, chunk)))
        rows = []
        for p in data if isinstance(data, list) else []:
            if not p.get("player_id"):
                continue
            rows.append((int(p["player_id"]), p.get("fair_fight"), p.get("bs_estimate"),
                         p.get("bs_estimate_human"), p.get("source"), p.get("last_updated"), now))
        with _db_lock, db() as con:
            con.executemany("INSERT OR REPLACE INTO ffstats VALUES (?,?,?,?,?,?,?)", rows)
        got += len(rows)
    return got


# ----------------------------------------------------------------- war lists
import math as _math


def est_fr(ff, level):
    """Rough flat respect for a win: FF x (1 + ln level) / 4 (matches FR on your history within a few %)."""
    if not ff or not level:
        return None
    return round(float(ff) * (1 + _math.log(max(int(level), 1))) / 4, 2)


ENEMY_SQL = """
SELECT e.*, f.fair_fight, f.bs_estimate, f.bs_human, f.last_updated AS ff_updated,
  (w.id IS NOT NULL) AS on_list, w.note, w.color,
  (SELECT COUNT(*) FROM attacks a WHERE a.defender_id=e.id AND a.outgoing=1
      AND a.result IN ('Attacked','Mugged','Hospitalized','Arrested','Special','Bounty')) AS my_wins,
  (SELECT COUNT(*) FROM attacks a WHERE a.defender_id=e.id AND a.outgoing=1 AND a.result='Lost') AS my_losses,
  (SELECT base_respect FROM attacks a WHERE a.defender_id=e.id AND a.outgoing=1 AND a.respect>0
      ORDER BY ended DESC LIMIT 1) AS my_fr
FROM enemy e
LEFT JOIN ffstats f ON f.id=e.id
LEFT JOIN warlist w ON w.id=e.id
"""


def list_enemy(only_list=False):
    my_bs = float(meta_get("my_bs_total", 0) or 0) or None
    with db() as con:
        rows = [dict(r) for r in con.execute(ENEMY_SQL + (" WHERE w.id IS NOT NULL" if only_list else ""))]
        if only_list:  # list members who left the faction still show (from warlist), without live data
            have = {r["id"] for r in rows}
            for w in con.execute("SELECT * FROM warlist"):
                if w["id"] not in have:
                    rows.append({"id": w["id"], "name": None, "on_list": 1, "note": w["note"], "color": w["color"],
                                 "status_desc": "Not in faction any more", "status_state": "Gone"})
    for r in rows:
        r["est_fr"] = est_fr(r.get("fair_fight"), r.get("level"))
        bs = r.get("bs_estimate")
        r["vs_me"] = round(bs / my_bs, 2) if bs and my_bs else None
        r["on_list"] = bool(r.get("on_list"))
    return rows


def warlist_add(ids):
    fac = meta_get("war_faction_id")
    now = int(time.time())
    with _db_lock, db() as con:
        for i in ids:
            con.execute("INSERT OR IGNORE INTO warlist(id, faction_id, added) VALUES(?,?,?)", (int(i), fac, now))


def warlist_remove(ids):
    with _db_lock, db() as con:
        con.executemany("DELETE FROM warlist WHERE id=?", [(int(i),) for i in ids])


def warlist_clear():
    with _db_lock, db() as con:
        con.execute("DELETE FROM warlist")


def warlist_set(tid, note=None, color=None):
    with _db_lock, db() as con:
        if note is not None:
            con.execute("UPDATE warlist SET note=? WHERE id=?", (str(note)[:300], tid))
        if color is not None:
            con.execute("UPDATE warlist SET color=? WHERE id=?", (int(color) % 4, tid))


def warlist_text():
    """Plain-text list to paste into Discord / faction chat."""
    rows = sorted(list_enemy(only_list=True), key=lambda r: -(r.get("est_fr") or 0))
    fac = meta_get("war_faction_name") or ""
    lines = [f"War list vs {fac} ({len(rows)} targets)"]
    for r in rows:
        bits = [f"{r.get('name') or '?'} [{r['id']}]", f"lvl {r.get('level') or '?'}"]
        if r.get("bs_human"):
            bits.append(f"~{r['bs_human']} stats")
        if r.get("fair_fight"):
            bits.append(f"FF {r['fair_fight']:.2f}")
        if r.get("note"):
            bits.append(r["note"])
        lines.append(" · ".join(bits) + f"  https://www.torn.com/page.php?sid=attack&user2ID={r['id']}")
    return "\n".join(lines)


# ----------------------------------------------------------------- target clean-up
def effective_ff(t):
    """(ff, source): FF Scouter's current estimate if we have it, else FF from your last win on them."""
    if t.get("ff_now") is not None:
        return float(t["ff_now"]), "FF Scouter (now)"
    if t.get("ff") is not None:
        return float(t["ff"]), "your last hit"
    return None, None


def cleanup_candidates(min_ff, keep_flagged=True):
    """Targets whose FF has dropped below min_ff. Targets with no FF data are never suggested."""
    out = []
    for t in list_targets():
        ff, src = effective_ff(t)
        if ff is None or ff >= min_ff:
            continue
        if keep_flagged and (t.get("color") or 0):
            continue
        out.append({**t, "eff_ff": ff, "ff_source": src})
    out.sort(key=lambda t: t["eff_ff"])
    return out


def remove_targets(ids):
    with _db_lock, db() as con:
        con.executemany("DELETE FROM targets WHERE id=?", [(int(i),) for i in ids])


# ----------------------------------------------------------------- your bars + cooldowns (header)
def fetch_status():
    """Energy bar and drug/booster/medical cooldowns in one call. Times are converted to
    absolute timestamps so the window can count down locally between polls."""
    d = torn("user/", selections="bars,cooldowns,profile")
    now = int(time.time())
    e = d.get("energy") or {}
    cd = d.get("cooldowns") or {}
    st = d.get("status") or {}
    life = d.get("life") or {}
    fac = d.get("faction") or {}
    return {
        "name": d.get("name"), "player_id": d.get("player_id"), "level": d.get("level"),
        "faction": fac.get("faction_name") or "",
        "status_state": st.get("state"), "status_desc": st.get("description"),
        "status_until": int(st.get("until") or 0),
        "life": int(life.get("current") or 0), "life_max": int(life.get("maximum") or 0),
        "energy": int(e.get("current") or 0), "energy_max": int(e.get("maximum") or 0),
        "energy_full_at": now + int(e.get("fulltime") or 0),
        "drug_until": now + int(cd.get("drug") or 0),
        "booster_until": now + int(cd.get("booster") or 0),
        "medical_until": now + int(cd.get("medical") or 0),
        "fetched": now,
    }


# ----------------------------------------------------------------- live faction chain
def fetch_chain():
    """Your faction's live chain: count, next bonus, and seconds until it drops (absolute time)."""
    now = int(time.time())
    d = torn("faction/", selections="chain", timestamp=now)  # timestamp = skip Torn's ~30s cache
    c = d.get("chain") or {}
    timeout = int(c.get("timeout") or 0)
    return {
        "current": int(c.get("current") or 0), "max": int(c.get("max") or 0),
        "timeout": timeout, "drops_at": now + timeout if timeout else 0,
        "cooldown_until": now + int(c.get("cooldown") or 0),
        "modifier": c.get("modifier"), "fetched": now,
    }


# ----------------------------------------------------------------- share codes (same format as Strike List Mobile)
# "SL1:" + base64url(JSON {"v":1, "t":[[id, color, note], ...]})  - works across any phone / computer
import base64 as _b64


def export_code():
    rows = []
    with db() as con:
        for r in con.execute("SELECT id, color, note FROM targets ORDER BY id"):
            row = [r["id"]]
            if r["color"] or r["note"]:
                row.append(r["color"] or 0)
            if r["note"]:
                row.append(r["note"])
            rows.append(row)
    raw = json.dumps({"v": 1, "t": rows}, separators=(",", ":"), ensure_ascii=False).encode()
    return "SL1:" + _b64.urlsafe_b64encode(raw).decode().rstrip("=")


def _find_code(text):
    """Finds a share code even if a phone/app added spaces or line breaks, changed the case or dropped 'SL1:'."""
    flat = re.sub(r"\s+", "", str(text or ""))
    m = re.search(r"SL1[:：]?([A-Za-z0-9_\-+/]{8,}=*)", flat, re.I) or re.search(r"(eyJ[A-Za-z0-9_\-+/]{8,}=*)", flat)
    return m.group(1).replace("+", "-").replace("/", "_").rstrip("=") if m else None


def _strict_ids(text):
    """IDs only from clear patterns (profile/attack links, [1234], digit-only tokens) - never from inside other text."""
    text = str(text or "")
    ids = {int(x) for x in re.findall(r"(?:XID|user2ID|ID)=(\d{1,8})", text, re.I)}
    ids |= {int(x) for x in re.findall(r"\[(\d{1,8})\]", text)}
    ids |= {int(t) for t in re.split(r"[\s,;]+", text) if re.fullmatch(r"\d{3,8}", t)}  # bare numbers: 3+ digits
    return sorted(i for i in ids if i > 0)


def read_import(text):
    """Returns rows [[id, color, note], ...] without changing anything. Raises ValueError if nothing usable."""
    code = _find_code(text)
    if code:
        try:
            data = json.loads(_b64.urlsafe_b64decode(code + "=" * (-len(code) % 4)).decode())
        except Exception:
            raise ValueError("That share code is incomplete - copy all of it, starting with SL1:") from None
        if not isinstance(data, dict) or not isinstance(data.get("t"), list):
            raise ValueError("That isn't a Strike List code")
        rows = [r if isinstance(r, list) else [r] for r in data["t"]]
        return [r for r in rows if str(r[0]).isdigit() and int(r[0]) > 0]
    ids = _strict_ids(text)
    if not ids:
        raise ValueError("No share code or player IDs found")
    return [[i] for i in ids]


def import_rows(rows):
    added = updated = 0
    now = int(time.time())
    with _db_lock, db() as con:
        for r in rows:
            tid = int(r[0])
            color = int(r[1]) % 4 if len(r) > 1 and r[1] else 0
            note = str(r[2])[:500] if len(r) > 2 and r[2] else ""
            cur = con.execute("SELECT color, note FROM targets WHERE id=?", (tid,)).fetchone()
            if not cur:
                con.execute("INSERT INTO targets(id, added, color, note) VALUES(?,?,?,?)", (tid, now, color, note))
                added += 1
            else:
                if color:
                    con.execute("UPDATE targets SET color=? WHERE id=?", (color, tid))
                if note and not cur["note"]:
                    con.execute("UPDATE targets SET note=? WHERE id=?", (note, tid))
                updated += 1
    return added, updated


def import_code(text):
    """Accepts a share code (from desktop or mobile) or plain IDs / profile links. Returns (added, updated)."""
    return import_rows(read_import(text))
