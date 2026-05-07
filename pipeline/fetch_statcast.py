"""
pipeline/fetch_statcast.py -- Phase 2
Fetches Statcast + FanGraphs advanced metrics.

Sources:
  - Baseball Savant (via pybaseball): xBA, xSLG, xwOBA, barrel%, hard-hit%,
    exit velo, sprint speed, launch angle
  - FanGraphs JSON API (direct): xFIP, SIERA, FIP (pitchers); wRC+ (batters)

Cache: data/statcast-cache.json stores the last fetch date.
Re-fetches only if cache is older than CACHE_TTL_DAYS (default 7).

Writes to: fact_statcast (full refresh -- DELETE + INSERT each fetch)
Joins on mlb_id (from dim_players) to get ESPN player_id.
"""

import json
import os
import sqlite3
import sys
from datetime import date, datetime, timezone

import pandas as pd
import requests
from pybaseball import (
    statcast_batter_expected_stats,
    statcast_batter_exitvelo_barrels,
    statcast_sprint_speed,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DB_PATH, DATA_DIR, SEASON

CACHE_PATH    = os.path.join(DATA_DIR, "statcast-cache.json")
CACHE_TTL_DAYS = 7

_FG_API    = "https://www.fangraphs.com/api/leaders/major-league/data"
_FG_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}


def _save_cache(meta: dict) -> None:
    with open(CACHE_PATH, "w") as f:
        json.dump(meta, f, indent=2)


def _cache_is_fresh(cache: dict) -> bool:
    last = cache.get("last_fetch_date")
    if not last:
        return False
    delta = (date.today() - date.fromisoformat(last)).days
    return delta < CACHE_TTL_DAYS


# ---------------------------------------------------------------------------
# FanGraphs pitching (xFIP, SIERA, FIP) via direct API
# ---------------------------------------------------------------------------

def _fg_pitching() -> pd.DataFrame:
    """Return FanGraphs pitching advanced metrics for the current season."""
    print("  Fetching FanGraphs pitching (xFIP/SIERA/FIP)...")
    params = {
        "age": "", "pos": "all", "stats": "pit", "lg": "all",
        "qual": 1, "season": SEASON, "season1": SEASON,
        "startdate": "", "enddate": "", "month": 0,
        "hand": "", "team": 0, "pageitems": 2000, "pagenum": 1,
        "ind": 0, "rost": 0, "players": 0, "type": 8,
    }
    resp = requests.get(_FG_API, params=params, headers=_FG_HEADERS, timeout=20)
    resp.raise_for_status()
    rows = resp.json().get("data", [])

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["mlb_id", "xfip", "siera", "fip"])

    df = df.rename(columns={
        "xMLBAMID": "mlb_id",
        "xFIP":     "xfip",
        "SIERA":    "siera",
        "FIP":      "fip",
    })
    df["mlb_id"] = pd.to_numeric(df["mlb_id"], errors="coerce").dropna().astype(int)
    df = df[df["mlb_id"] > 0][["mlb_id", "xfip", "siera", "fip"]].drop_duplicates("mlb_id")
    print(f"    {len(df)} pitchers")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# FanGraphs batting (wRC+, xwOBA) via direct API
# ---------------------------------------------------------------------------

def _fg_batting() -> pd.DataFrame:
    """Return FanGraphs batting advanced metrics for the current season."""
    print("  Fetching FanGraphs batting (wRC+/xwOBA)...")
    params = {
        "age": "", "pos": "all", "stats": "bat", "lg": "all",
        "qual": 1, "season": SEASON, "season1": SEASON,
        "startdate": "", "enddate": "", "month": 0,
        "hand": "", "team": 0, "pageitems": 2000, "pagenum": 1,
        "ind": 0, "rost": 0, "players": 0, "type": 8,
    }
    resp = requests.get(_FG_API, params=params, headers=_FG_HEADERS, timeout=20)
    resp.raise_for_status()
    rows = resp.json().get("data", [])

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["mlb_id", "wrc_plus", "xwoba_fg"])

    df = df.rename(columns={
        "xMLBAMID": "mlb_id",
        "wRC+":     "wrc_plus",
        "xwOBA":    "xwoba_fg",
    })
    df["mlb_id"] = pd.to_numeric(df["mlb_id"], errors="coerce").dropna().astype(int)
    df = df[df["mlb_id"] > 0][["mlb_id", "wrc_plus", "xwoba_fg"]].drop_duplicates("mlb_id")
    print(f"    {len(df)} batters")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Baseball Savant: expected stats (xBA, xSLG, xwOBA)
# ---------------------------------------------------------------------------

def _savant_expected() -> pd.DataFrame:
    print("  Fetching Baseball Savant expected stats (xBA/xSLG/xwOBA)...")
    df = statcast_batter_expected_stats(SEASON, minPA=10)
    df = df.rename(columns={
        "player_id": "mlb_id",
        "est_ba":    "xba",
        "est_slg":   "xslg",
        "est_woba":  "xwoba",
    })
    df["mlb_id"] = pd.to_numeric(df["mlb_id"], errors="coerce").dropna().astype(int)
    df = df[df["mlb_id"] > 0][["mlb_id", "xba", "xslg", "xwoba"]].drop_duplicates("mlb_id")
    print(f"    {len(df)} batters")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Baseball Savant: exit velo, barrel%, hard-hit%, launch angle
# ---------------------------------------------------------------------------

def _savant_exitvelo() -> pd.DataFrame:
    print("  Fetching Baseball Savant exit velo / barrel%...")
    df = statcast_batter_exitvelo_barrels(SEASON, minBBE=10)
    df = df.rename(columns={
        "player_id":      "mlb_id",
        "avg_hit_speed":  "exit_velo",
        "avg_hit_angle":  "launch_angle",
        "brl_percent":    "barrel_pct",
        "ev95percent":    "hard_hit_pct",
    })
    df["mlb_id"] = pd.to_numeric(df["mlb_id"], errors="coerce").dropna().astype(int)
    keep = ["mlb_id", "exit_velo", "launch_angle", "barrel_pct", "hard_hit_pct"]
    df = df[df["mlb_id"] > 0][keep].drop_duplicates("mlb_id")
    print(f"    {len(df)} batters")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Baseball Savant: sprint speed
# ---------------------------------------------------------------------------

def _savant_sprint() -> pd.DataFrame:
    print("  Fetching Baseball Savant sprint speed...")
    df = statcast_sprint_speed(SEASON)
    df = df.rename(columns={"player_id": "mlb_id"})
    df["mlb_id"] = pd.to_numeric(df["mlb_id"], errors="coerce").dropna().astype(int)
    df = df[df["mlb_id"] > 0][["mlb_id", "sprint_speed"]].drop_duplicates("mlb_id")
    print(f"    {len(df)} players")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Load dim_players mlb_id -> player_id (ESPN) mapping
# ---------------------------------------------------------------------------

def _load_player_map() -> dict[int, str]:
    """Return {mlb_id: espn_player_id} for all matched players."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT player_id, mlb_id FROM dim_players WHERE mlb_id IS NOT NULL AND mlb_id > 0"
    ).fetchall()
    conn.close()
    return {int(mlb_id): str(player_id) for player_id, mlb_id in rows}


# ---------------------------------------------------------------------------
# Merge all sources and write to DB
# ---------------------------------------------------------------------------

def fetch_and_write(force: bool = False) -> dict:
    """
    Main entry point. Respects 7-day cache unless force=True.
    Returns summary dict.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    cache = _load_cache()

    if not force and _cache_is_fresh(cache):
        age = (date.today() - date.fromisoformat(cache["last_fetch_date"])).days
        print(f"Statcast cache is {age}d old (TTL={CACHE_TTL_DAYS}d) -- skipping fetch.")
        return {"skipped": True, "cache_age_days": age}

    print("Fetching Statcast + FanGraphs data...")

    # Fetch all sources (FanGraphs first -- 403 fails fast if blocked)
    fg_pitch  = _fg_pitching()
    fg_bat    = _fg_batting()
    sv_exp    = _savant_expected()
    sv_ev     = _savant_exitvelo()
    sv_sprint = _savant_sprint()

    # Merge on mlb_id
    merged = (
        sv_exp
        .merge(sv_ev,     on="mlb_id", how="outer")
        .merge(sv_sprint,  on="mlb_id", how="outer")
        .merge(fg_bat,    on="mlb_id", how="outer")
        .merge(fg_pitch,  on="mlb_id", how="outer")
    )
    merged["mlb_id"] = pd.to_numeric(merged["mlb_id"], errors="coerce").dropna().astype(int)

    # Map to ESPN player_id
    player_map = _load_player_map()
    merged["player_id"] = merged["mlb_id"].map(player_map)

    # Drop rows with no ESPN player_id (not in our league universe)
    matched = merged[merged["player_id"].notna()].copy()
    total   = len(merged)
    print(f"  Merged: {total} MLB players, {len(matched)} matched to ESPN universe")

    # Write to DB
    fetch_date = date.today().isoformat()
    now        = datetime.now(timezone.utc).isoformat()
    conn       = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM fact_statcast")

    def _f(row, col):
        v = row.get(col)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)

    rows_inserted = 0
    for _, row in matched.iterrows():
        row = row.to_dict()
        conn.execute(
            """
            INSERT INTO fact_statcast
                (player_id, fetch_date, xba, xslg, xwoba, xfip, siera, fip,
                 wrc_plus, barrel_pct, hard_hit_pct, exit_velo,
                 sprint_speed, launch_angle)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["player_id"],
                fetch_date,
                _f(row, "xba"),
                _f(row, "xslg"),
                # prefer FanGraphs xwOBA for batters (xwoba_fg); fall back to Savant
                _f(row, "xwoba_fg") or _f(row, "xwoba"),
                _f(row, "xfip"),
                _f(row, "siera"),
                _f(row, "fip"),
                _f(row, "wrc_plus"),
                _f(row, "barrel_pct"),
                _f(row, "hard_hit_pct"),
                _f(row, "exit_velo"),
                _f(row, "sprint_speed"),
                _f(row, "launch_angle"),
            ),
        )
        rows_inserted += 1

    conn.commit()
    conn.close()

    # Update cache
    _save_cache({
        "last_fetch_date": fetch_date,
        "rows_written":    rows_inserted,
        "total_mlb":       total,
        "matched_espn":    len(matched),
    })

    print(f"  Wrote {rows_inserted} rows to fact_statcast")
    return {
        "skipped":       False,
        "rows_written":  rows_inserted,
        "total_mlb":     total,
        "matched_espn":  len(matched),
    }


def run(force: bool = False) -> dict:
    return fetch_and_write(force=force)


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    result = run(force=force)
    if result.get("skipped"):
        print(f"Skipped (cache age: {result['cache_age_days']}d)")
    else:
        print(f"\nStatcast fetch complete:")
        print(f"  MLB players fetched: {result['total_mlb']}")
        print(f"  Matched to ESPN:     {result['matched_espn']}")
        print(f"  Rows written:        {result['rows_written']}")
