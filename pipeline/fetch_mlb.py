"""
pipeline/fetch_mlb.py -- Phase 2
Fetches from MLB Stats API (statsapi):
  - 7-day schedule with probable starters
  - IL transactions (last 7 days)
  - Two-start pitcher detection

Writes to: fact_schedule
"""

import os
import sqlite3
import sys
from datetime import date, timedelta

import requests
import statsapi

_MLB_API = "https://statsapi.mlb.com/api/v1"

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (
    DB_PATH,
    ESPN_TEAM_TO_MLB_NAME,
)

# statsapi team IDs -> 3-letter abbreviations (2026 roster)
_STATSAPI_TEAM_ID_TO_ABBREV: dict[int, str] = {
    133: "OAK", 134: "PIT", 135: "SD",  136: "SEA", 137: "SF",
    138: "STL", 139: "TB",  140: "TEX", 141: "TOR", 142: "MIN",
    143: "PHI", 144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY",
    158: "MIL", 108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS",
    112: "CHC", 113: "CIN", 114: "CLE", 115: "COL", 116: "DET",
    117: "HOU", 118: "KC",  119: "LAD", 120: "WSH", 121: "NYM",
}


# ---------------------------------------------------------------------------
# Internal: raw schedule fetch with probable pitcher IDs
# ---------------------------------------------------------------------------

def _raw_schedule(start_str: str, end_str: str) -> list[dict]:
    """Return raw game dicts hydrated with probablePitcher IDs."""
    raw = statsapi.get(
        "schedule",
        {
            "sportId":   1,
            "startDate": start_str,
            "endDate":   end_str,
            "hydrate":   "probablePitcher",
        },
    )
    games: list[dict] = []
    for date_block in raw.get("dates", []):
        games.extend(date_block.get("games", []))
    return games


def _team_abbr(team_block: dict) -> str:
    team_id = team_block.get("team", {}).get("id")
    return _STATSAPI_TEAM_ID_TO_ABBREV.get(
        team_id,
        team_block.get("team", {}).get("name", "")[:3].upper(),
    )


# ---------------------------------------------------------------------------
# Schedule (next 7 days)
# ---------------------------------------------------------------------------

def fetch_schedule(start_date: date | None = None) -> list[dict]:
    """Return one row per team-game for the next 7 days (today inclusive)."""
    if start_date is None:
        start_date = date.today()
    end_date  = start_date + timedelta(days=6)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str   = end_date.strftime("%Y-%m-%d")

    print(f"Fetching MLB schedule {start_str} to {end_str}...")
    games = _raw_schedule(start_str, end_str)

    rows: list[dict] = []
    for game in games:
        game_date = game.get("gameDate", "")[:10]
        teams     = game.get("teams", {})

        for side, opp_side in [("home", "away"), ("away", "home")]:
            team_block  = teams.get(side, {})
            opp_block   = teams.get(opp_side, {})
            pitcher_id  = team_block.get("probablePitcher", {}).get("id") or None

            rows.append({
                "game_date":           game_date,
                "mlb_team":            _team_abbr(team_block),
                "opponent_team":       _team_abbr(opp_block),
                "home_away":           side,
                "probable_pitcher_id": pitcher_id,
                "park_factor":         None,
                "opp_wrc_vs_hand":     None,
            })

    print(f"  {len(rows)} team-game rows ({len(games)} games)")
    return rows


def write_schedule(rows: list[dict]) -> None:
    """Full refresh of fact_schedule."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM fact_schedule")
    conn.executemany(
        """
        INSERT INTO fact_schedule
            (game_date, mlb_team, opponent_team, home_away,
             probable_pitcher_id, park_factor, opp_wrc_vs_hand)
        VALUES (:game_date, :mlb_team, :opponent_team, :home_away,
                :probable_pitcher_id, :park_factor, :opp_wrc_vs_hand)
        """,
        rows,
    )
    conn.commit()
    conn.close()
    print(f"  Wrote {len(rows)} rows to fact_schedule")


# ---------------------------------------------------------------------------
# IL / transactions (last 7 days)
# ---------------------------------------------------------------------------

def fetch_transactions(days_back: int = 7) -> list[dict]:
    """Pull IL placements and activations from the last N days."""
    end_date   = date.today()
    start_date = end_date - timedelta(days=days_back)
    start_str  = start_date.strftime("%Y-%m-%d")
    end_str    = end_date.strftime("%Y-%m-%d")

    print(f"Fetching MLB transactions {start_str} to {end_str}...")

    try:
        # statsapi.get() requires ALL required-param sets; use requests directly
        resp = requests.get(
            f"{_MLB_API}/transactions",
            params={"startDate": start_str, "endDate": end_str, "sportId": 1},
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        print(f"  WARNING: transaction fetch failed: {e}")
        return []

    transactions = raw if isinstance(raw, list) else raw.get("transactions", [])

    il_keywords  = {"placed", "transferred", "injured list", "10-day", "15-day", "60-day"}
    act_keywords = {"activated", "reinstated"}

    parsed: list[dict] = []
    for tx in transactions:
        desc    = tx.get("description", "").lower()
        tx_type = "other"
        if any(k in desc for k in il_keywords):
            tx_type = "il_placement"
        elif any(k in desc for k in act_keywords):
            tx_type = "activation"
        elif "optioned" in desc or "designated" in desc:
            tx_type = "roster_move"

        person   = tx.get("person") or {}
        to_team  = tx.get("toTeam") or {}
        parsed.append({
            "date":        tx.get("date", ""),
            "description": tx.get("description", ""),
            "type":        tx_type,
            "player":      person.get("fullName", "") if isinstance(person, dict) else "",
            "team":        to_team.get("abbreviation", "") if isinstance(to_team, dict) else "",
        })

    il_count  = sum(1 for t in parsed if t["type"] == "il_placement")
    act_count = sum(1 for t in parsed if t["type"] == "activation")
    print(f"  {len(parsed)} transactions: {il_count} IL placements, {act_count} activations")
    return parsed


# ---------------------------------------------------------------------------
# Probable starters lookup (per pitcher, next N days)
# ---------------------------------------------------------------------------

def fetch_probable_starters(days: int = 7) -> dict[int, list[dict]]:
    """
    Return {mlb_id: [{"game_date": str, "opponent": str, "home_away": str}, ...]}
    for all pitchers with a probable start in the next `days` days.
    """
    start_date = date.today()
    end_date   = start_date + timedelta(days=days - 1)
    games = _raw_schedule(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))

    result: dict[int, list[dict]] = {}
    for game in games:
        game_date = game.get("gameDate", "")[:10]
        teams     = game.get("teams", {})
        for side, opp_side in [("home", "away"), ("away", "home")]:
            pitcher_id = teams.get(side, {}).get("probablePitcher", {}).get("id")
            if not pitcher_id:
                continue
            opp_abbr = _team_abbr(teams.get(opp_side, {}))
            result.setdefault(pitcher_id, []).append({
                "game_date": game_date,
                "opponent":  opp_abbr,
                "home_away": side,
            })

    return result


# ---------------------------------------------------------------------------
# Two-start pitcher detection
# ---------------------------------------------------------------------------

def get_two_start_pitchers(week_start: date, week_end: date) -> set[int]:
    """Return MLB AM IDs of pitchers with >=2 probable starts in the window."""
    games = _raw_schedule(week_start.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d"))

    start_count: dict[int, int] = {}
    for game in games:
        teams = game.get("teams", {})
        for side in ("home", "away"):
            pid = teams.get(side, {}).get("probablePitcher", {}).get("id")
            if pid:
                start_count[pid] = start_count.get(pid, 0) + 1

    two_starters = {pid for pid, cnt in start_count.items() if cnt >= 2}
    print(f"  Two-start pitchers ({week_start} to {week_end}): {len(two_starters)}")
    return two_starters


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def check_recent_games_settled(ref_date: date | None = None) -> dict:
    """Soft freshness check: are all of *yesterday's* MLB games Final?

    Run at ~6am ET, this confirms the prior day's slate — including the
    last-starting game (e.g. a late West-coast game like LAD/TB) — has finished
    and posted, so ESPN/stat data should be settled. Non-blocking: returns a
    summary dict; callers decide whether to warn.
    """
    if ref_date is None:
        ref_date = date.today()
    yday = (ref_date - timedelta(days=1)).strftime("%Y-%m-%d")
    games = _raw_schedule(yday, yday)

    incomplete, last_game, last_dt = [], None, ""
    for g in games:
        status  = g.get("status", {}) or {}
        state   = status.get("abstractGameState", "")
        teams   = g.get("teams", {})
        matchup = f"{_team_abbr(teams.get('away', {}))}@{_team_abbr(teams.get('home', {}))}"
        gd      = g.get("gameDate", "")            # ISO UTC — string max == latest start
        if gd > last_dt:
            last_dt, last_game = gd, matchup
        if state != "Final":
            incomplete.append(f"{matchup} ({status.get('detailedState', state) or 'unknown'})")

    return {
        "date":        yday,
        "games":       len(games),
        "final":       len(games) - len(incomplete),
        "incomplete":  incomplete,
        "last_game":   last_game,
        "all_settled": len(incomplete) == 0,        # True on off-days (0 games) too
    }


def run() -> dict:
    """Fetch schedule + transactions, write schedule to DB."""
    schedule_rows = fetch_schedule()
    write_schedule(schedule_rows)

    transactions = fetch_transactions(days_back=7)

    two_starters = get_two_start_pitchers(
        week_start=date.today(),
        week_end=date.today() + timedelta(days=6),
    )

    return {
        "schedule_rows":   len(schedule_rows),
        "transactions":    len(transactions),
        "two_starters":    len(two_starters),
        "two_starter_ids": sorted(two_starters),
    }


if __name__ == "__main__":
    result = run()
    print(f"\nMLB fetch complete:")
    print(f"  Schedule rows:   {result['schedule_rows']}")
    print(f"  Transactions:    {result['transactions']}")
    print(f"  Two-starters:    {result['two_starters']}")
    if result["two_starter_ids"]:
        print(f"  IDs: {result['two_starter_ids']}")
