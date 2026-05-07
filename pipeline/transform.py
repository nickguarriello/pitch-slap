"""
pipeline/transform.py -- Phase 3
Joins all data sources on player_id (ESPN) and writes fact_player_stats.

Windows computed:
  'season'  -- full season to date (FanGraphs JSON API, month=0)
  '30d'     -- last 30 days (pybaseball batting/pitching_stats_range)
  '14d'     -- last 14 days (pybaseball batting/pitching_stats_range)
  'current' -- current week start to today (same range functions)

Hitter stats:  R, HR, RBI, SB, OBP, BABIP, K%, BB%, PA
Pitcher stats: K, QS, ERA, WHIP, SvHd, BABIP, K%, BB%, IP
  - QS and SvHd fully available for 'season' window only
  - Rolling windows: QS=None, SvHd=SV only (HLD not in BBRef range data)

All joins on mlb_id -> player_id. Name matching never used.
"""

import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests
from pybaseball import batting_stats_range, pitching_stats_range

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (
    DB_PATH, SEASON,
    SEASON_START, WEEK_1_END, WEEK_2_START,
)

_FG_API    = "https://www.fangraphs.com/api/leaders/major-league/data"
_FG_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ---------------------------------------------------------------------------
# Player map: mlb_id -> ESPN player_id
# ---------------------------------------------------------------------------

def _load_player_map() -> dict[int, str]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT player_id, mlb_id FROM dim_players WHERE mlb_id IS NOT NULL AND mlb_id > 0"
    ).fetchall()
    conn.close()
    return {int(mlb_id): str(pid) for pid, mlb_id in rows}


# ---------------------------------------------------------------------------
# Current week start
# ---------------------------------------------------------------------------

def _current_week_start() -> date:
    """Return the Monday start of the current fantasy week."""
    today = date.today()
    week1_end   = date.fromisoformat(WEEK_1_END)
    week2_start = date.fromisoformat(WEEK_2_START)

    if today <= week1_end:
        return date.fromisoformat(SEASON_START)

    days_since_w2 = (today - week2_start).days
    weeks_elapsed = days_since_w2 // 7
    return week2_start + timedelta(weeks=weeks_elapsed)


# ---------------------------------------------------------------------------
# FanGraphs season stats (month=0 -- full season, has QS + HLD)
# ---------------------------------------------------------------------------

def _fg_batting_season() -> pd.DataFrame:
    """Full-season batting stats from FanGraphs."""
    params = {
        "pos": "all", "stats": "bat", "lg": "all",
        "qual": 0, "season": SEASON, "season1": SEASON,
        "month": 0, "team": 0, "pageitems": 3000, "pagenum": 1,
        "ind": 0, "rost": 0, "players": 0, "type": 0,
    }
    resp = requests.get(_FG_API, params=params, headers=_FG_HEADERS, timeout=20)
    resp.raise_for_status()
    rows = resp.json().get("data", [])
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame()

    df = df.rename(columns={"xMLBAMID": "mlb_id", "SO": "k", "PA": "pa"})
    df["mlb_id"] = pd.to_numeric(df["mlb_id"], errors="coerce")
    df["svhd"]   = None
    df["qs"]     = None
    df["ip"]     = None

    keep = ["mlb_id", "R", "HR", "RBI", "SB", "OBP", "BABIP", "pa", "svhd", "qs", "ip"]
    cols_present = [c for c in keep if c in df.columns]
    return df[cols_present].rename(columns={
        "R": "r", "HR": "hr", "RBI": "rbi", "SB": "sb", "OBP": "obp",
        "BABIP": "babip",
    }).drop_duplicates("mlb_id")


def _fg_pitching_season() -> pd.DataFrame:
    """Full-season pitching stats from FanGraphs (includes QS, HLD, SV)."""
    params = {
        "pos": "all", "stats": "pit", "lg": "all",
        "qual": 0, "season": SEASON, "season1": SEASON,
        "month": 0, "team": 0, "pageitems": 3000, "pagenum": 1,
        "ind": 0, "rost": 0, "players": 0, "type": 0,
    }
    resp = requests.get(_FG_API, params=params, headers=_FG_HEADERS, timeout=20)
    resp.raise_for_status()
    rows = resp.json().get("data", [])
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame()

    df = df.rename(columns={"xMLBAMID": "mlb_id"})
    df["mlb_id"] = pd.to_numeric(df["mlb_id"], errors="coerce")

    # SvHd = SV + HLD
    sv  = pd.to_numeric(df.get("SV",  0), errors="coerce").fillna(0)
    hld = pd.to_numeric(df.get("HLD", 0), errors="coerce").fillna(0)
    df["svhd"] = sv + hld

    # K% and BB% (FanGraphs returns as decimal: 0.25 = 25%)
    df["k_pct"]  = pd.to_numeric(df.get("K%",  None), errors="coerce")
    df["bb_pct"] = pd.to_numeric(df.get("BB%", None), errors="coerce")

    df = df.rename(columns={
        "SO": "k", "QS": "qs", "ERA": "era", "WHIP": "whip",
        "IP": "ip", "BAbip": "babip",
    })

    keep = ["mlb_id", "k", "qs", "era", "whip", "svhd", "ip", "babip", "k_pct", "bb_pct"]
    cols_present = [c for c in keep if c in df.columns]
    return df[cols_present].drop_duplicates("mlb_id")


# ---------------------------------------------------------------------------
# BBRef rolling window stats (14d / 30d / current-week)
# ---------------------------------------------------------------------------

def _bbref_batting(start: date, end: date) -> pd.DataFrame:
    """Batting stats for a date range from Baseball Reference via pybaseball."""
    df = batting_stats_range(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if df.empty:
        return pd.DataFrame()

    df = df.rename(columns={"mlbID": "mlb_id"})
    df["mlb_id"] = pd.to_numeric(df["mlb_id"], errors="coerce")

    # Compute K% and BB% where possible (not in BBRef range data -- set None)
    df["k_pct"]  = None
    df["bb_pct"] = None
    df["babip"]  = None

    keep = ["mlb_id", "R", "HR", "RBI", "SB", "OBP", "PA", "k_pct", "bb_pct", "babip"]
    cols_present = [c for c in keep if c in df.columns]
    return df[cols_present].rename(columns={
        "R": "r", "HR": "hr", "RBI": "rbi", "SB": "sb",
        "OBP": "obp", "PA": "pa",
    }).drop_duplicates("mlb_id")


def _bbref_pitching(start: date, end: date) -> pd.DataFrame:
    """Pitching stats for a date range from Baseball Reference via pybaseball."""
    df = pitching_stats_range(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if df.empty:
        return pd.DataFrame()

    df = df.rename(columns={"mlbID": "mlb_id", "SO": "k"})
    df["mlb_id"] = pd.to_numeric(df["mlb_id"], errors="coerce")

    # SvHd: BBRef range only has SV (no HLD) -- use SV as best available
    df["svhd"] = pd.to_numeric(df.get("SV", 0), errors="coerce").fillna(0)

    # QS, k_pct, bb_pct, babip not in BBRef range data
    df["qs"]     = None
    df["k_pct"]  = None
    df["bb_pct"] = None
    df["babip"]  = None

    keep = ["mlb_id", "k", "qs", "era", "whip", "svhd", "ip",
            "k_pct", "bb_pct", "babip"]
    col_map = {"ERA": "era", "WHIP": "whip", "IP": "ip", "BAbip": "babip"}
    df = df.rename(columns=col_map)
    cols_present = [c for c in keep if c in df.columns]
    return df[cols_present].drop_duplicates("mlb_id")


# ---------------------------------------------------------------------------
# Build one window's rows
# ---------------------------------------------------------------------------

def _build_window(
    window: str,
    bat_df: pd.DataFrame,
    pit_df: pd.DataFrame,
    player_map: dict[int, str],
    stat_date: str,
) -> list[tuple]:
    """Merge batting + pitching frames and produce DB insert tuples."""
    rows: list[tuple] = []

    def _f(val):
        if val is None:
            return None
        try:
            import math
            if math.isnan(float(val)):
                return None
            return float(val)
        except (TypeError, ValueError):
            return None

    # --- Hitters ---
    for _, row in bat_df.iterrows():
        mlb_id = row.get("mlb_id")
        if not mlb_id or pd.isna(mlb_id):
            continue
        pid = player_map.get(int(mlb_id))
        if not pid:
            continue
        rows.append((
            pid, window, stat_date,
            _f(row.get("r")),
            _f(row.get("hr")),
            _f(row.get("rbi")),
            _f(row.get("sb")),
            _f(row.get("obp")),
            None, None, None, None, None,           # k, qs, era, whip, svhd
            _f(row.get("babip")),
            _f(row.get("k_pct")),
            _f(row.get("bb_pct")),
            None,                                   # ip
            _f(row.get("pa")),
        ))

    # --- Pitchers ---
    for _, row in pit_df.iterrows():
        mlb_id = row.get("mlb_id")
        if not mlb_id or pd.isna(mlb_id):
            continue
        pid = player_map.get(int(mlb_id))
        if not pid:
            continue
        rows.append((
            pid, window, stat_date,
            None, None, None, None, None,           # r, hr, rbi, sb, obp
            _f(row.get("k")),
            _f(row.get("qs")),
            _f(row.get("era")),
            _f(row.get("whip")),
            _f(row.get("svhd")),
            _f(row.get("babip")),
            _f(row.get("k_pct")),
            _f(row.get("bb_pct")),
            _f(row.get("ip")),
            None,                                   # pa
        ))

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> dict:
    today       = date.today()
    stat_date   = today.isoformat()
    player_map  = _load_player_map()
    week_start  = _current_week_start()

    print(f"transform.py: stat_date={stat_date}, week_start={week_start}, {len(player_map)} players in map")

    windows: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}

    # Season window (FanGraphs -- has QS, HLD)
    print("  Season stats (FanGraphs)...")
    windows["season"] = (_fg_batting_season(), _fg_pitching_season())

    # Rolling windows (BBRef range)
    for label, start in [
        ("30d",     today - timedelta(days=30)),
        ("14d",     today - timedelta(days=14)),
        ("current", week_start),
    ]:
        print(f"  {label} stats ({start} to {today})...")
        windows[label] = (
            _bbref_batting(start, today),
            _bbref_pitching(start, today),
        )

    # Build all rows
    all_rows: list[tuple] = []
    for window, (bat_df, pit_df) in windows.items():
        window_rows = _build_window(window, bat_df, pit_df, player_map, stat_date)
        print(f"    {window}: {len(window_rows)} rows")
        all_rows.extend(window_rows)

    # Write to DB (full refresh)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM fact_player_stats")
    conn.executemany(
        """
        INSERT INTO fact_player_stats
            (player_id, window, stat_date,
             r, hr, rbi, sb, obp,
             k, qs, era, whip, svhd,
             babip, k_pct, bb_pct, ip, pa)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        all_rows,
    )
    conn.commit()

    # Spot check: count per window
    counts = {}
    for window in windows:
        n = conn.execute(
            "SELECT COUNT(*) FROM fact_player_stats WHERE window=?", (window,)
        ).fetchone()[0]
        counts[window] = n

    conn.close()

    print(f"\n  fact_player_stats written: {len(all_rows)} total rows")
    for w, n in counts.items():
        print(f"    {w:10s}: {n}")

    return {
        "stat_date":    stat_date,
        "total_rows":   len(all_rows),
        "by_window":    counts,
        "week_start":   str(week_start),
    }


if __name__ == "__main__":
    result = run()
    print(f"\nTransform complete: {result['total_rows']} rows across {len(result['by_window'])} windows")
