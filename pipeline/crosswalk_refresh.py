"""
pipeline/crosswalk_refresh.py — daily self-heal of the player crosswalk.

Problem this solves: after fetch_espn, any *rostered* player missing an MLB ID
(a new call-up, a trade acquisition, or an ambiguous name the last full
build_crosswalk left null) trips validate.check_crosswalk_integrity and
hard-stops the whole pipeline (sys.exit) — no data gets committed. That is
exactly what broke the run on 2026-07-01 (Jake McCarthy / Gage Jump were
rostered but absent from the committed crosswalk CSV).

heal() runs each full/light run, right after fetch_espn, and closes that gap:

  1. find rostered players (latest snapshot) with no dim_players row OR null mlb_id
  2. if there are none -> return immediately (no ESPN re-fetch, no pybaseball
     download — the common, healthy case)
  3. otherwise resolve each: crosswalk-overrides.json first (cheap), then, only
     if something is still unresolved, the pybaseball register via
     build_crosswalk's matcher (exact -> unique fuzzy)
  4. write resolved rows into dim_players (fixes THIS run) and persist them to
     data/player-crosswalk.csv (so the committed seed stays current and the
     register isn't re-downloaded for the same player tomorrow)
  5. players that stay ambiguous/unknown are returned as `unresolved` for a
     manual crosswalk-overrides.json entry — validate still gates on them

Non-fatal by contract: the caller wraps this in try/except; if heal fails,
the pipeline continues and validate remains the safety net.
"""

import csv
import io
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DB_PATH, LEAGUE_ID, SEASON, ESPN_S2, ESPN_SWID, ESPN_PRO_TEAM_MAP
from pipeline import build_crosswalk as bc

CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "data", "player-crosswalk.csv")

CSV_COLUMNS = ["player_id", "mlb_id", "fg_id", "bbref_id", "name", "position",
               "mlb_team", "crosswalk_status", "crosswalk_note", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Find rostered players that would fail crosswalk.integrity
# ---------------------------------------------------------------------------

def _unmatched_rostered(conn: sqlite3.Connection) -> list[dict]:
    """Rostered players (latest snapshot) with no dim_players row or a null mlb_id.
    Mirrors validate.check_crosswalk_integrity's LEFT JOIN exactly."""
    rows = conn.execute(
        """
        SELECT DISTINCT fer.player_id, dp.name, dp.position, dp.player_id AS dp_pid
        FROM fact_espn_rosters fer
        LEFT JOIN dim_players dp ON fer.player_id = dp.player_id
        WHERE dp.mlb_id IS NULL
          AND fer.espn_team_id IS NOT NULL
          AND fer.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)
        """
    ).fetchall()
    return [
        {"player_id": str(r["player_id"]), "name": r["name"],
         "position": r["position"] or "", "has_row": r["dp_pid"] is not None}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Current rostered names from ESPN (needed for players not yet in dim_players)
# ---------------------------------------------------------------------------

def _load_rostered_names() -> dict:
    """{player_id(str): {player_id, name, position, mlb_team}} for rostered players
    only (no FA pool — keep it fast; we only need names for the few unmatched)."""
    from espn_api.baseball import League
    league = League(league_id=LEAGUE_ID, year=SEASON, espn_s2=ESPN_S2, swid=ESPN_SWID)
    out: dict = {}
    for team in league.teams:
        for p in team.roster:
            pid = str(p.playerId)
            pro_id = getattr(p, "proTeamId", None)
            out[pid] = {
                "player_id": pid,
                "name": p.name,
                "position": getattr(p, "position", "") or "",
                "mlb_team": ESPN_PRO_TEAM_MAP.get(pro_id, "") if pro_id is not None else "",
            }
    return out


# ---------------------------------------------------------------------------
# Writers: dim_players (this run) + the committed CSV (persist the fix)
# ---------------------------------------------------------------------------

def _upsert_dim_players(conn: sqlite3.Connection, resolved: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for m in resolved:
        conn.execute(
            """INSERT OR REPLACE INTO dim_players
               (player_id, mlb_id, fg_id, bbref_id, name, position, mlb_team,
                crosswalk_status, crosswalk_note, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (m["player_id"], m["mlb_id"], str(m.get("fg_id") or ""),
             str(m.get("bbref_id") or ""), m["name"], m.get("position", ""),
             m.get("mlb_team", ""), m["status"], m.get("note", ""),
             m.get("created_at") or now, now),
        )
    conn.commit()


def _csv_field_row(values: list) -> str:
    """One properly-quoted CSV row (no trailing newline)."""
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(values)
    return buf.getvalue()


def _persist_csv(resolved: list[dict]) -> None:
    """Update existing rows in place / append new ones, preserving the file's
    existing line ending and leaving all other rows byte-for-byte unchanged."""
    if not os.path.exists(CSV_PATH):
        return
    with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
        content = f.read()
    nl = "\r\n" if "\r\n" in content else "\n"
    trailing_nl = content.endswith(nl)
    lines = content.split(nl)
    if trailing_nl:
        lines = lines[:-1]

    # index existing data rows by player_id (first CSV field), and keep each
    # row's original created_at (field 9) so updates don't reset it
    idx: dict[str, int] = {}
    created_at: dict[str, str] = {}
    for i, line in enumerate(lines[1:], start=1):
        if not line:
            continue
        fields = next(csv.reader([line]))
        idx[fields[0]] = i
        if len(fields) > 9:
            created_at[fields[0]] = fields[9]

    now = datetime.now(timezone.utc).isoformat()
    for m in resolved:
        pid = m["player_id"]
        row = _csv_field_row([
            pid, m["mlb_id"], m.get("fg_id") or "", m.get("bbref_id") or "",
            m["name"], m.get("position", ""), m.get("mlb_team", ""),
            m["status"], m.get("note", ""), created_at.get(pid, now), now,
        ])
        if pid in idx:
            lines[idx[pid]] = row
        else:
            lines.append(row)

    out = nl.join(lines) + (nl if trailing_nl else "")
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(out)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def heal(db_path: str = DB_PATH) -> dict:
    """Resolve rostered players missing an MLB ID. Returns a summary dict:
    {resolved: [...], unresolved: [...], checked: N}. Cheap no-op when clean."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    unmatched = _unmatched_rostered(conn)
    if not unmatched:
        conn.close()
        return {"resolved": [], "unresolved": [], "checked": 0}

    overrides: dict = {}
    if os.path.exists(bc.OVERRIDES_PATH):
        with open(bc.OVERRIDES_PATH) as f:
            overrides = json.load(f)

    # ESPN names (authoritative) for the unmatched players — needed especially
    # for those with no dim_players row yet.
    names = _load_rostered_names()

    resolved: list[dict] = []
    unresolved: list[dict] = []
    need_register: list[dict] = []

    for u in unmatched:
        pid = u["player_id"]
        espn = names.get(pid) or (
            {"player_id": pid, "name": u["name"], "position": u["position"], "mlb_team": ""}
            if u["name"] else None
        )
        if not espn:
            unresolved.append({"player_id": pid, "name": f"(unknown {pid})",
                               "note": "no name from ESPN roster or dim_players"})
            continue
        # 1) manual override (cheap — no register needed)
        if pid in overrides:
            ov = overrides[pid]
            resolved.append({**espn, "mlb_id": int(ov["mlb_id"]),
                             "fg_id": ov.get("fg_id"), "bbref_id": ov.get("bbref_id"),
                             "status": "manual", "note": ov.get("note", "manual override")})
        else:
            need_register.append(espn)

    # 2) only download the pybaseball register if overrides didn't cover everything
    if need_register:
        lookup = bc.load_lookup_table()
        fl_idx, lf_idx = bc.build_name_indexes(lookup)
        all_fl = list(fl_idx.keys())
        for espn in need_register:
            row, status, note = bc.match_player(espn, fl_idx, lf_idx, all_fl, {})
            if row and row.get("mlb_id"):
                resolved.append({**espn, "mlb_id": int(row["mlb_id"]),
                                 "fg_id": row.get("fg_id"), "bbref_id": row.get("bbref_id"),
                                 "status": status, "note": note})
            else:
                unresolved.append({"player_id": espn["player_id"],
                                   "name": espn["name"], "note": note})

    if resolved:
        _upsert_dim_players(conn, resolved)
    conn.close()
    if resolved:
        _persist_csv(resolved)

    return {"resolved": resolved, "unresolved": unresolved, "checked": len(unmatched)}


if __name__ == "__main__":
    r = heal()
    print(f"crosswalk self-heal: checked {r['checked']} unmatched rostered player(s)")
    for m in r["resolved"]:
        print(f"  resolved  {m['name']:24} -> mlb_id {m['mlb_id']} [{m['status']}] {m['note']}")
    for u in r["unresolved"]:
        print(f"  UNRESOLVED {u['name']:24} — {u['note']} (add to crosswalk-overrides.json)")
    if not r["checked"]:
        print("  all rostered players already have MLB IDs — nothing to do")
