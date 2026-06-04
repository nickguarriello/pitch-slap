"""
pipeline/transform.py -- Phase 3
Joins all data sources on player_id (ESPN) and writes fact_player_stats.

Windows computed:
  'season'  -- full season to date (MLB Stats API)
  '30d'     -- last 30 days (pybaseball batting/pitching_stats_range)
  '14d'     -- last 14 days
  '7d'      -- last 7 days
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
    FIRST_HALF_END, ASG_BREAK_END,
)

_MLB_STATS_API = "https://statsapi.mlb.com/api/v1"


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
# MLB Stats API season stats (replaces FanGraphs, no IP blocking)
# ---------------------------------------------------------------------------

def _ip_to_float(ip_str) -> float:
    """Convert '6.2' (6 innings + 2 outs) to fractional innings (6.667)."""
    try:
        parts = str(ip_str).split(".")
        return int(parts[0]) + (int(parts[1]) if len(parts) > 1 else 0) / 3
    except (ValueError, IndexError):
        return 0.0


def _mlb_batting_season() -> pd.DataFrame:
    """Full-season batting stats from MLB Stats API."""
    resp = requests.get(
        f"{_MLB_STATS_API}/stats",
        params={"stats": "season", "group": "hitting", "season": SEASON,
                "playerPool": "all", "limit": 2000},
        timeout=30,
    )
    resp.raise_for_status()
    splits = resp.json().get("stats", [{}])[0].get("splits", [])
    rows = []
    for s in splits:
        st = s.get("stat", {})
        rows.append({
            "mlb_id": s["player"]["id"],
            "r":      st.get("runs", 0),
            "hr":     st.get("homeRuns", 0),
            "rbi":    st.get("rbi", 0),
            "sb":     st.get("stolenBases", 0),
            "obp":    pd.to_numeric(st.get("obp"), errors="coerce"),
            "babip":  pd.to_numeric(st.get("babip"), errors="coerce"),
            "pa":     st.get("plateAppearances", 0),
            "svhd":   None,
            "qs":     None,
            "ip":     None,
        })
    df = pd.DataFrame(rows)
    return df.drop_duplicates("mlb_id") if not df.empty else df


def _compute_qs_from_gamelogs(pitcher_ids: list[int]) -> dict[int, int]:
    """Fetch game logs in batches and count quality starts (6+ IP, <=3 ER)."""
    qs_map: dict[int, int] = {}
    batch_size = 50
    for i in range(0, len(pitcher_ids), batch_size):
        ids = ",".join(str(p) for p in pitcher_ids[i:i + batch_size])
        resp = requests.get(
            f"{_MLB_STATS_API}/people",
            params={"personIds": ids,
                    "hydrate": f"stats(group=pitching,type=gameLog,season={SEASON})"},
            timeout=30,
        )
        if not resp.ok:
            continue
        for person in resp.json().get("people", []):
            qs = 0
            for grp in person.get("stats", []):
                for game in grp.get("splits", []):
                    st = game.get("stat", {})
                    if st.get("gamesStarted", 0):
                        if _ip_to_float(st.get("inningsPitched", 0)) >= 6.0 and st.get("earnedRuns", 0) <= 3:
                            qs += 1
            qs_map[person["id"]] = qs
    return qs_map


def _mlb_pitching_season() -> pd.DataFrame:
    """Full-season pitching stats from MLB Stats API (K, ERA, WHIP, SvHd, QS, IP)."""
    resp = requests.get(
        f"{_MLB_STATS_API}/stats",
        params={"stats": "season", "group": "pitching", "season": SEASON,
                "playerPool": "all", "limit": 2000},
        timeout=30,
    )
    resp.raise_for_status()
    splits = resp.json().get("stats", [{}])[0].get("splits", [])

    rows = []
    starter_ids = []
    for s in splits:
        st = s.get("stat", {})
        pid = s["player"]["id"]
        sv  = st.get("saves", 0) or 0
        hld = st.get("holds", 0) or 0
        ip  = _ip_to_float(st.get("inningsPitched", 0))
        rows.append({
            "mlb_id": pid,
            "k":      st.get("strikeOuts", 0),
            "era":    pd.to_numeric(st.get("era"), errors="coerce"),
            "whip":   pd.to_numeric(st.get("whip"), errors="coerce"),
            "svhd":   sv + hld,
            "ip":     ip,
            "babip":  None,
            "k_pct":  None,
            "bb_pct": None,
            "qs":     0,
        })
        if st.get("gamesStarted", 0) > 0:
            starter_ids.append(pid)

    if starter_ids:
        qs_map = _compute_qs_from_gamelogs(starter_ids)
        for row in rows:
            if row["mlb_id"] in qs_map:
                row["qs"] = qs_map[row["mlb_id"]]

    df = pd.DataFrame(rows)
    return df.drop_duplicates("mlb_id") if not df.empty else df


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

    # Season window (MLB Stats API)
    print("  Season stats (MLB Stats API)...")
    windows["season"] = (_mlb_batting_season(), _mlb_pitching_season())

    # Half-season splits
    first_half_end  = date.fromisoformat(FIRST_HALF_END)
    second_half_start = date.fromisoformat(ASG_BREAK_END)
    if today <= first_half_end:
        # Pre-break: first half == season (no extra fetch needed)
        print("  First half stats (pre-break, aliased to season)...")
        windows["first_half"] = windows["season"]
    else:
        # Post-break: first half is frozen date range via BBRef
        season_start = date.fromisoformat(SEASON_START)
        print(f"  First half stats ({season_start} to {first_half_end})...")
        windows["first_half"] = (
            _bbref_batting(season_start, first_half_end),
            _bbref_pitching(season_start, first_half_end),
        )
        print(f"  Second half stats ({second_half_start} to {today})...")
        windows["second_half"] = (
            _bbref_batting(second_half_start, today),
            _bbref_pitching(second_half_start, today),
        )

    # Rolling windows (BBRef range)
    for label, start in [
        ("30d",     today - timedelta(days=30)),
        ("14d",     today - timedelta(days=14)),
        ("7d",      today - timedelta(days=7)),
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
