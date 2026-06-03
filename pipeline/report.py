"""
pipeline/report.py -- Phase 5
Generates all dashboard data files from DB + evaluation-report.json.

Writes to docs/data/:
  roster.json      -- my roster with multi-window stats + flags
  waivers.json     -- FA buy-low candidates + two-starters + ownership alerts
  matchup.json     -- current week cat states + need weights
  league.json      -- all-team cat standings
  playoff.json     -- playoff picture, bracket, opponent previews, swing categories
  status.json      -- pipeline health banner data

Also writes CSVs to docs/data/:
  roster.csv, waivers.csv, matchup.csv

All column names are frozen -- changes require explicit approval (CLAUDE.md).
"""

import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (
    DATA_DIR, DB_PATH, DOCS_DIR, TEAM_ID,
    HITTING_CATS, PITCHING_CATS, ALL_CATS,
)

DOCS_DATA_DIR    = os.path.join(DOCS_DIR, "data")
EVAL_REPORT_PATH = os.path.join(DATA_DIR, "evaluation-report.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _f(val, digits: int = 3):
    if val is None:
        return None
    try:
        import math
        v = float(val)
        return None if math.isnan(v) else round(v, digits)
    except (TypeError, ValueError):
        return None


def _load_eval() -> dict:
    if os.path.exists(EVAL_REPORT_PATH):
        with open(EVAL_REPORT_PATH) as f:
            return json.load(f)
    return {}


def _load_espn_teams() -> dict:
    """Return {team_id_str: {name, abbrev, wins, losses, ...}} from data/espn_teams.json."""
    path = os.path.join(DATA_DIR, 'espn_teams.json')
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        raw = json.load(f)
    # Support both old flat format and new nested {teams: {}, league_meta: {}} format
    if 'teams' in raw and isinstance(raw['teams'], dict):
        return raw['teams']
    return raw


def _load_league_meta() -> dict:
    """Return league_meta block from espn_teams.json (playoff week, season length)."""
    path = os.path.join(DATA_DIR, 'espn_teams.json')
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        raw = json.load(f)
    return raw.get('league_meta', {})


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def _write_csv(path: str, rows: list[dict], fieldnames: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# roster.json + roster.csv
# ---------------------------------------------------------------------------

def build_roster(conn: sqlite3.Connection, eval_report: dict) -> list[dict]:
    """My current roster with stats across all windows + evaluation flags."""
    snapshot_date_row = conn.execute(
        "SELECT MAX(snapshot_date) FROM fact_espn_rosters"
    ).fetchone()
    snapshot_date = snapshot_date_row[0] if snapshot_date_row else None

    # Base roster
    roster_rows = conn.execute(
        """
        SELECT p.player_id, p.name, p.position, p.mlb_team,
               r.lineup_slot, r.injury_status, r.is_active, r.is_il,
               r.ownership_pct, r.espn_rating,
               r.espn_proj_r, r.espn_proj_hr, r.espn_proj_rbi,
               r.espn_proj_sb, r.espn_proj_obp,
               r.espn_proj_k, r.espn_proj_qs, r.espn_proj_era,
               r.espn_proj_whip, r.espn_proj_svhd
        FROM fact_espn_rosters r
        JOIN dim_players p ON r.player_id = p.player_id
        WHERE r.espn_team_id = ? AND r.snapshot_date = ?
        ORDER BY r.lineup_slot, p.name
        """,
        (TEAM_ID, snapshot_date),
    ).fetchall()

    # Stats by window
    stats_rows = conn.execute(
        """
        SELECT player_id, window,
               r, hr, rbi, sb, obp, k, qs, era, whip, svhd,
               babip, k_pct, bb_pct, ip, pa
        FROM fact_player_stats
        WHERE window IN ('season', '14d', '30d', 'current')
        """,
    ).fetchall()

    # Group stats by player_id + window
    stats: dict[str, dict[str, dict]] = {}
    for row in stats_rows:
        pid = row["player_id"]
        w   = row["window"]
        stats.setdefault(pid, {})[w] = {
            "r": _f(row["r"], 0), "hr": _f(row["hr"], 0),
            "rbi": _f(row["rbi"], 0), "sb": _f(row["sb"], 0),
            "obp": _f(row["obp"]), "k": _f(row["k"], 0),
            "qs": _f(row["qs"], 0), "era": _f(row["era"]),
            "whip": _f(row["whip"]), "svhd": _f(row["svhd"], 0),
            "babip": _f(row["babip"]), "k_pct": _f(row["k_pct"]),
            "bb_pct": _f(row["bb_pct"]), "ip": _f(row["ip"], 1),
            "pa": _f(row["pa"], 0),
        }

    # Statcast
    sc_rows = conn.execute(
        "SELECT player_id, xba, xslg, xwoba, xfip, siera, fip, wrc_plus, "
        "barrel_pct, hard_hit_pct, exit_velo, sprint_speed FROM fact_statcast"
    ).fetchall()
    sc: dict[str, dict] = {}
    for row in sc_rows:
        sc[row["player_id"]] = {
            "xba": _f(row["xba"]), "xslg": _f(row["xslg"]),
            "xwoba": _f(row["xwoba"]), "xfip": _f(row["xfip"]),
            "siera": _f(row["siera"]), "fip": _f(row["fip"]),
            "wrc_plus": _f(row["wrc_plus"], 0),
            "barrel_pct": _f(row["barrel_pct"]),
            "hard_hit_pct": _f(row["hard_hit_pct"]),
            "exit_velo": _f(row["exit_velo"]),
            "sprint_speed": _f(row["sprint_speed"]),
        }

    # Buy/sell flags indexed by player_id
    buy_low_ids  = {f["player_id"]: f for f in eval_report.get("buy_low", [])}
    sell_high_ids = {f["player_id"]: f for f in eval_report.get("sell_high", [])}

    players = []
    for row in roster_rows:
        pid = row["player_id"]
        p = {
            "player_id":    pid,
            "name":         row["name"],
            "position":     row["position"],
            "team":         row["mlb_team"],
            "lineup_slot":  row["lineup_slot"],
            "injury_status": row["injury_status"],
            "is_active":    bool(row["is_active"]),
            "is_il":        bool(row["is_il"]),
            "ownership_pct": _f(row["ownership_pct"], 1),
            "espn_rating":  _f(row["espn_rating"]),
            "projections": {
                "r": _f(row["espn_proj_r"], 1), "hr": _f(row["espn_proj_hr"], 1),
                "rbi": _f(row["espn_proj_rbi"], 1), "sb": _f(row["espn_proj_sb"], 1),
                "obp": _f(row["espn_proj_obp"]),
                "k": _f(row["espn_proj_k"], 1), "qs": _f(row["espn_proj_qs"], 1),
                "era": _f(row["espn_proj_era"]), "whip": _f(row["espn_proj_whip"]),
                "svhd": _f(row["espn_proj_svhd"], 1),
            },
            "stats":    stats.get(pid, {}),
            "statcast": sc.get(pid, {}),
            "buy_low":  pid in buy_low_ids,
            "sell_high": pid in sell_high_ids,
            "buy_low_reasons":  buy_low_ids.get(pid, {}).get("reasons", []),
            "sell_high_reasons": sell_high_ids.get(pid, {}).get("reasons", []),
        }
        players.append(p)

    return players


# ---------------------------------------------------------------------------
# waivers.json + waivers.csv
# ---------------------------------------------------------------------------

def build_waivers(conn: sqlite3.Connection, eval_report: dict) -> dict:
    """FA candidates: buy-low unrostered + two-starters + ownership alerts + trade targets."""
    buy_low_fa = [
        f for f in eval_report.get("buy_low", [])
        if not f.get("rostered")
    ][:30]

    # Buy-low players on OTHER teams' rosters — trade acquisition targets
    buy_low_trade = [
        f for f in eval_report.get("buy_low", [])
        if f.get("rostered") and f.get("espn_team_id") is not None
        and f.get("espn_team_id") != TEAM_ID
    ][:20]

    two_starters_fa = [
        p for p in eval_report.get("two_starters", [])
        if not p.get("rostered")
    ]

    ownership_alerts = eval_report.get("ownership_alerts", [])

    return {
        "buy_low_fa":       buy_low_fa,
        "buy_low_trade":    buy_low_trade,
        "two_starters_fa":  two_starters_fa,
        "ownership_alerts": ownership_alerts,
    }


# ---------------------------------------------------------------------------
# matchup.json
# ---------------------------------------------------------------------------

def build_matchup(conn: sqlite3.Connection, eval_report: dict) -> dict:
    """Current week cat states, need weights, and recent matchup history."""
    # Historical weekly W/L/T per cat
    history = conn.execute(
        """
        SELECT week, cat_name, result, my_val, opp_val
        FROM fact_matchups
        WHERE my_team_id = ? AND is_final = 1
        ORDER BY week DESC
        """,
        (TEAM_ID,),
    ).fetchall()

    history_by_week: dict[int, list] = {}
    for row in history:
        wk = row["week"]
        history_by_week.setdefault(wk, []).append({
            "cat": row["cat_name"],
            "result": row["result"],
            "my_val": _f(row["my_val"]),
            "opp_val": _f(row["opp_val"]),
        })

    # Overall W/L/T record per cat
    records = conn.execute(
        """
        SELECT cat_name,
               SUM(CASE WHEN result='W' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='L' THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN result='T' THEN 1 ELSE 0 END) as ties
        FROM fact_matchups
        WHERE my_team_id = ? AND is_final = 1
        GROUP BY cat_name
        """,
        (TEAM_ID,),
    ).fetchall()

    cat_records = {
        r["cat_name"]: {
            "wins": r["wins"], "losses": r["losses"], "ties": r["ties"],
            "win_rate": round(r["wins"] / max(r["wins"] + r["losses"] + r["ties"], 1), 3),
        }
        for r in records
    }

    # Opponent info per history week
    espn_teams = _load_espn_teams()
    opp_rows = conn.execute(
        "SELECT DISTINCT week, opp_team_id FROM fact_matchups WHERE my_team_id = ? AND is_final = 1",
        (TEAM_ID,),
    ).fetchall()
    history_opponents = {
        row["week"]: espn_teams.get(str(row["opp_team_id"]), {
            "name": f"Team {row['opp_team_id']}",
            "abbrev": str(row["opp_team_id"]),
        })
        for row in opp_rows
    }

    # Current week opponent from dedicated file (not pipeline-meta, which main.py overwrites)
    opp_path = os.path.join(DATA_DIR, "current-opponent.json")
    opp_meta: dict = {}
    if os.path.exists(opp_path):
        with open(opp_path) as f:
            try:
                opp_meta = json.load(f)
            except Exception:
                pass
    raw_opp_id = opp_meta.get("current_opp_team_id")
    current_opp = espn_teams.get(str(raw_opp_id), {}) if raw_opp_id is not None else {}

    return {
        "cat_states":              eval_report.get("cat_states", {}),
        "need_weights":            eval_report.get("need_weights", {}),
        "cat_records":             cat_records,
        "history":                 history_by_week,
        "history_opponents":       history_opponents,
        "current_opponent_name":   current_opp.get("name"),
        "current_opponent_abbrev": current_opp.get("abbrev"),
        "constraints":             eval_report.get("constraints", {}),
    }


# ---------------------------------------------------------------------------
# league.json
# ---------------------------------------------------------------------------

def build_league(conn: sqlite3.Connection) -> dict:
    """All-team aggregated season stats per scoring category."""
    snapshot_date_row = conn.execute(
        "SELECT MAX(snapshot_date) FROM fact_espn_rosters"
    ).fetchone()
    snapshot_date = snapshot_date_row[0] if snapshot_date_row else None

    espn_teams = _load_espn_teams()

    team_ids = conn.execute(
        "SELECT DISTINCT espn_team_id FROM fact_espn_rosters "
        "WHERE espn_team_id IS NOT NULL AND snapshot_date = ?",
        (snapshot_date,),
    ).fetchall()

    stat_cols = {
        "R": ("r", "sum"), "HR": ("hr", "sum"), "RBI": ("rbi", "sum"),
        "SB": ("sb", "sum"), "OBP": ("obp", "avg_pa"),
        "K": ("k", "sum"), "QS": ("qs", "sum"), "SvHd": ("svhd", "sum"),
        "ERA": ("era", "avg_ip"), "WHIP": ("whip", "avg_ip"),
    }

    teams = []
    for row in team_ids:
        tid = row["espn_team_id"]
        team_stats: dict[str, float | None] = {}

        for cat, (col, agg_type) in stat_cols.items():
            if agg_type == "sum":
                val = conn.execute(
                    f"""
                    SELECT SUM(fps.{col})
                    FROM fact_player_stats fps
                    JOIN fact_espn_rosters fer ON fps.player_id = fer.player_id
                    WHERE fps.window = 'season' AND fps.{col} IS NOT NULL
                      AND fer.espn_team_id = ? AND fer.snapshot_date = ?
                    """,
                    (tid, snapshot_date),
                ).fetchone()[0]
            elif agg_type == "avg_pa":
                val = conn.execute(
                    f"""
                    SELECT SUM(fps.{col} * COALESCE(fps.pa, 0)) / NULLIF(SUM(COALESCE(fps.pa, 0)), 0)
                    FROM fact_player_stats fps
                    JOIN fact_espn_rosters fer ON fps.player_id = fer.player_id
                    WHERE fps.window = 'season' AND fps.{col} IS NOT NULL
                      AND fer.espn_team_id = ? AND fer.snapshot_date = ?
                    """,
                    (tid, snapshot_date),
                ).fetchone()[0]
            else:  # avg_ip
                val = conn.execute(
                    f"""
                    SELECT SUM(fps.{col} * COALESCE(fps.ip, 0)) / NULLIF(SUM(COALESCE(fps.ip, 0)), 0)
                    FROM fact_player_stats fps
                    JOIN fact_espn_rosters fer ON fps.player_id = fer.player_id
                    WHERE fps.window = 'season' AND fps.{col} IS NOT NULL
                      AND fer.espn_team_id = ? AND fer.snapshot_date = ?
                    """,
                    (tid, snapshot_date),
                ).fetchone()[0]
            team_stats[cat] = _f(val)

        team_info = espn_teams.get(str(tid), {})
        cat_w = team_info.get("cat_wins",   0)
        cat_l = team_info.get("cat_losses", 0)
        cat_t = team_info.get("cat_ties",   0)
        teams.append({
            "team_id":    tid,
            "team_name":  team_info.get("name", f"Team {tid}"),
            "team_abbrev": team_info.get("abbrev", str(tid)),
            "wins":       team_info.get("wins",   0),
            "losses":     team_info.get("losses", 0),
            "cat_wins":   cat_w,
            "cat_losses": cat_l,
            "cat_ties":   cat_t,
            "cat_record": f"{cat_w}-{cat_l}-{cat_t}" if (cat_w + cat_l + cat_t) > 0 else "—",
            "is_my_team": tid == TEAM_ID,
            "stats":      team_stats,
        })

    # Rank each team per cat (1=best)
    from config import CATS_LOWER_IS_BETTER
    for cat in stat_cols:
        lower_better = cat in CATS_LOWER_IS_BETTER
        valid = [t for t in teams if t["stats"].get(cat) is not None]
        valid.sort(key=lambda t: t["stats"][cat], reverse=not lower_better)
        for rank, t in enumerate(valid, 1):
            t.setdefault("ranks", {})[cat] = rank

    return {"teams": teams, "as_of": snapshot_date}


# ---------------------------------------------------------------------------
# playoff.json
# ---------------------------------------------------------------------------

def build_playoff(league_data: dict) -> dict:
    """
    Build playoff picture: standings, bracket, category matchup previews vs
    each potential opponent, swing categories, and improvement targets.
    Reads from espn_teams.json (W/L records + league_meta) and league.json output.
    """
    from config import PLAYOFFS, PLAYOFF_START_WEEK

    espn_teams  = _load_espn_teams()
    league_meta = _load_league_meta()

    playoff_start = (
        league_meta.get("playoff_start_week")
        or PLAYOFF_START_WEEK
        or 22
    )
    playoff_teams = PLAYOFFS["teams"]

    # Current week from fact_matchups max week
    # (already computed by the time report.py runs)
    # We use the league data's as_of date as a proxy
    from config import WEEK_1_START, WEEK_1_END, WEEK_2_START
    from datetime import date, timedelta
    today = date.today()
    w1_end   = date.fromisoformat(WEEK_1_END)
    w2_start = date.fromisoformat(WEEK_2_START)
    if today <= w1_end:
        current_week = 1
    else:
        current_week = 2 + (today - w2_start).days // 7

    weeks_until_playoffs = max(0, playoff_start - current_week - 1)
    in_playoff_weeks     = current_week >= playoff_start

    # Build standings from espn_teams W/L records
    all_teams = league_data.get("teams", [])
    team_map  = {t["team_id"]: t for t in all_teams}

    standings = []
    for t in all_teams:
        tid_str  = str(t["team_id"])
        et       = espn_teams.get(tid_str, {})
        wins     = et.get("wins",   0)
        losses   = et.get("losses", 0)
        ties     = et.get("ties",   0)
        pct      = et.get("pct",    round(wins / max(wins + losses, 1), 3))
        cat_w = et.get("cat_wins",   0)
        cat_l = et.get("cat_losses", 0)
        cat_t = et.get("cat_ties",   0)
        cat_record = f"{cat_w}-{cat_l}-{cat_t}" if (cat_w + cat_l + cat_t) > 0 else "—"
        standings.append({
            "team_id":   t["team_id"],
            "team_name": t.get("team_name", f"Team {t['team_id']}"),
            "abbrev":    t.get("team_abbrev", str(t["team_id"])),
            "wins":      wins,
            "losses":    losses,
            "ties":      ties,
            "pct":       pct,
            "cat_wins":  cat_w,
            "cat_losses": cat_l,
            "cat_ties":  cat_t,
            "cat_record": cat_record,
            "is_my_team": t.get("is_my_team", False),
            "ranks":     t.get("ranks", {}),
        })

    # Sort by pct desc, then fewer losses as tiebreaker
    standings.sort(key=lambda x: (-x["pct"], x["losses"]))
    for i, s in enumerate(standings):
        s["seed"] = i + 1
        s["in_playoffs"] = i < playoff_teams

    my_entry  = next((s for s in standings if s["is_my_team"]), standings[0])
    my_seed   = my_entry["seed"]
    my_ranks  = my_entry.get("ranks", {})

    # Standard bracket: 1 vs 4, 2 vs 3
    bracket_map = {1: 4, 4: 1, 2: 3, 3: 2}

    # Build matchup previews vs all 3 other potential playoff opponents
    playoff_entries  = [s for s in standings if s["in_playoffs"] and not s["is_my_team"]]
    opponent_previews = []

    for opp in playoff_entries:
        opp_ranks = opp.get("ranks", {})
        cat_matchup: dict = {}
        swing_cats: list = []
        proj_wins = 0
        proj_losses = 0

        from config import ALL_CATS, CATS_LOWER_IS_BETTER
        for cat in ALL_CATS:
            my_r   = my_ranks.get(cat)
            opp_r  = opp_ranks.get(cat)
            if my_r is None or opp_r is None:
                cat_matchup[cat] = {"my_rank": my_r, "their_rank": opp_r, "winner": "unknown"}
                continue

            lower_better = cat in CATS_LOWER_IS_BETTER
            # Lower rank number = better (rank 1 is best)
            if my_r < opp_r:
                winner = "mine"
                proj_wins += 1
            elif my_r > opp_r:
                winner = "theirs"
                proj_losses += 1
            else:
                winner = "toss"

            rank_gap = abs(my_r - opp_r)
            if rank_gap <= 2:
                swing_cats.append(cat)

            cat_matchup[cat] = {
                "my_rank":    my_r,
                "their_rank": opp_r,
                "winner":     winner,
                "rank_gap":   rank_gap,
            }

        is_likely_r1 = bracket_map.get(my_seed) == opp["seed"]
        opponent_previews.append({
            "seed":             opp["seed"],
            "team_id":          opp["team_id"],
            "team_name":        opp["team_name"],
            "abbrev":           opp["abbrev"],
            "is_likely_r1_opp": is_likely_r1,
            "projected_score":  f"{proj_wins}-{proj_losses}",
            "category_matchup": cat_matchup,
            "swing_categories": swing_cats,
        })

    # Sort: likely R1 opp first, then others
    opponent_previews.sort(key=lambda x: (0 if x["is_likely_r1_opp"] else 1, x["seed"]))

    # Overall swing categories across all potential opponents (appear in swing for 2+ opps)
    from collections import Counter
    swing_counter = Counter(c for p in opponent_previews for c in p["swing_categories"])
    global_swings = [c for c, cnt in swing_counter.most_common() if cnt >= 2]

    # Categories where I'm projected to lose vs my likely R1 opponent
    likely_r1 = next((p for p in opponent_previews if p["is_likely_r1_opp"]), None)
    improvement_targets = (
        [c for c, v in (likely_r1 or {}).get("category_matchup", {}).items()
         if v.get("winner") in ("theirs", "toss")]
        if likely_r1 else []
    )

    return {
        "as_of":                   league_data.get("as_of", ""),
        "playoff_start_week":      playoff_start,
        "current_week":            current_week,
        "weeks_until_playoffs":    weeks_until_playoffs,
        "in_playoff_weeks":        in_playoff_weeks,
        "my_seed":                 my_seed,
        "in_playoffs":             my_seed <= playoff_teams,
        "standings":               standings,
        "bracket":                 [
            {"seed_high": 1, "seed_low": 4},
            {"seed_high": 2, "seed_low": 3},
        ],
        "my_category_ranks":       my_ranks,
        "opponent_previews":       opponent_previews,
        "swing_categories":        global_swings,
        "improvement_targets":     improvement_targets,
    }


# ---------------------------------------------------------------------------
# status.json (pipeline health banner)
# ---------------------------------------------------------------------------

def build_status(eval_report: dict) -> dict:
    meta_path = os.path.join(DATA_DIR, "pipeline-meta.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)

    val_path = os.path.join(DATA_DIR, "validation-report.json")
    val_overall = "unknown"
    val_warnings = 0
    if os.path.exists(val_path):
        with open(val_path) as f:
            val_data = json.load(f)
        val_overall  = val_data.get("overall", "unknown")
        val_warnings = sum(1 for c in val_data.get("checks", []) if c["status"] == "warn")

    return {
        "as_of":           eval_report.get("as_of", ""),
        "last_run_mode":   meta.get("last_run_mode", "unknown"),
        "last_full_run":   meta.get("last_full_run"),
        "last_light_run":  meta.get("last_light_run"),
        "espn_last_fetch": meta.get("espn_last_fetch"),
        "validate_overall": val_overall,
        "validate_warnings": val_warnings,
        "cats_winning":    eval_report.get("summary", {}).get("cats_winning", 0),
        "cats_losing":     eval_report.get("summary", {}).get("cats_losing", 0),
        "cats_flippable":  eval_report.get("summary", {}).get("cats_flippable", 0),
        "constraints":     eval_report.get("constraints", {}),
    }


# ---------------------------------------------------------------------------
# CSVs
# ---------------------------------------------------------------------------

def write_roster_csv(roster: list[dict]) -> None:
    fieldnames = [
        "name", "position", "team", "lineup_slot", "injury_status",
        "ownership_pct", "season_r", "season_hr", "season_rbi", "season_sb",
        "season_obp", "season_k", "season_qs", "season_era", "season_whip",
        "season_svhd", "14d_r", "14d_hr", "14d_k", "14d_era", "14d_whip",
        "xwoba", "xfip", "siera", "wrc_plus", "buy_low", "sell_high",
    ]
    rows = []
    for p in roster:
        s = p.get("stats", {}).get("season", {})
        d14 = p.get("stats", {}).get("14d", {})
        sc  = p.get("statcast", {})
        rows.append({
            "name":         p["name"],
            "position":     p["position"],
            "team":         p["team"],
            "lineup_slot":  p["lineup_slot"],
            "injury_status": p["injury_status"],
            "ownership_pct": p["ownership_pct"],
            "season_r":     s.get("r"), "season_hr": s.get("hr"),
            "season_rbi":   s.get("rbi"), "season_sb": s.get("sb"),
            "season_obp":   s.get("obp"), "season_k":  s.get("k"),
            "season_qs":    s.get("qs"), "season_era": s.get("era"),
            "season_whip":  s.get("whip"), "season_svhd": s.get("svhd"),
            "14d_r":        d14.get("r"), "14d_hr": d14.get("hr"),
            "14d_k":        d14.get("k"), "14d_era": d14.get("era"),
            "14d_whip":     d14.get("whip"),
            "xwoba":        sc.get("xwoba"), "xfip": sc.get("xfip"),
            "siera":        sc.get("siera"), "wrc_plus": sc.get("wrc_plus"),
            "buy_low":      p["buy_low"], "sell_high": p["sell_high"],
        })
    _write_csv(os.path.join(DOCS_DATA_DIR, "roster.csv"), rows, fieldnames)


def write_waivers_csv(waivers: dict) -> None:
    fieldnames = [
        "name", "team", "flag_type", "score", "rostered",
        "era", "xfip", "siera", "obp", "xwoba", "babip", "reasons",
    ]
    rows = []
    for f in waivers.get("buy_low_fa", []):
        stats = f.get("stats", {})
        rows.append({
            "name":     f["name"], "team": f.get("team", ""),
            "flag_type": f["flag_type"], "score": f["score"],
            "rostered": f["rostered"],
            "era":      stats.get("era"), "xfip": stats.get("xfip"),
            "siera":    stats.get("siera"), "obp": stats.get("obp"),
            "xwoba":    stats.get("xwoba"), "babip": stats.get("babip"),
            "reasons":  "; ".join(f.get("reasons", [])),
        })
    for p in waivers.get("two_starters_fa", []):
        stats = p.get("stats", {})
        rows.append({
            "name":     p["name"], "team": p.get("team", ""),
            "flag_type": "two_starter", "score": p["score"],
            "rostered": p["rostered"],
            "era":      stats.get("era"), "xfip": stats.get("xfip"),
            "siera":    stats.get("siera"), "obp": None,
            "xwoba":    None, "babip": None,
            "reasons":  f"2 starts | SIERA {stats.get('siera')} xFIP {stats.get('xfip')}",
        })
    _write_csv(os.path.join(DOCS_DATA_DIR, "waivers.csv"), rows, fieldnames)


def write_matchup_csv(matchup: dict) -> None:
    fieldnames = ["cat", "state", "my_val", "opp_val", "gap", "need_weight",
                  "season_wins", "season_losses", "win_rate"]
    rows = []
    cat_states  = matchup.get("cat_states", {})
    need_weights = matchup.get("need_weights", {})
    cat_records  = matchup.get("cat_records", {})
    for cat in ALL_CATS:
        s  = cat_states.get(cat, {})
        cr = cat_records.get(cat, {})
        rows.append({
            "cat":         cat,
            "state":       s.get("state"),
            "my_val":      s.get("my_val"),
            "opp_val":     s.get("opp_val"),
            "gap":         s.get("gap"),
            "need_weight": need_weights.get(cat),
            "season_wins":   cr.get("wins"),
            "season_losses": cr.get("losses"),
            "win_rate":    cr.get("win_rate"),
        })
    _write_csv(os.path.join(DOCS_DATA_DIR, "matchup.csv"), rows, fieldnames)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> dict:
    os.makedirs(DOCS_DATA_DIR, exist_ok=True)
    conn        = _conn()
    eval_report = _load_eval()

    print("report.py: generating dashboard data files...")

    print("  roster.json / roster.csv...")
    roster = build_roster(conn, eval_report)
    _write_json(os.path.join(DOCS_DATA_DIR, "roster.json"), {
        "as_of": eval_report.get("as_of", ""),
        "players": roster,
    })
    write_roster_csv(roster)

    print("  waivers.json / waivers.csv...")
    waivers = build_waivers(conn, eval_report)
    _write_json(os.path.join(DOCS_DATA_DIR, "waivers.json"), waivers)
    write_waivers_csv(waivers)

    print("  matchup.json / matchup.csv...")
    matchup = build_matchup(conn, eval_report)
    _write_json(os.path.join(DOCS_DATA_DIR, "matchup.json"), matchup)
    write_matchup_csv(matchup)

    print("  league.json...")
    league = build_league(conn)
    _write_json(os.path.join(DOCS_DATA_DIR, "league.json"), league)

    print("  playoff.json...")
    playoff = build_playoff(league)
    _write_json(os.path.join(DOCS_DATA_DIR, "playoff.json"), playoff)

    print("  status.json...")
    status = build_status(eval_report)
    _write_json(os.path.join(DOCS_DATA_DIR, "status.json"), status)

    conn.close()

    print(f"  Done: {len(roster)} roster players, "
          f"{len(waivers.get('buy_low_fa', []))} FA buy-low, "
          f"{len(waivers.get('two_starters_fa', []))} two-starters FA")

    return {
        "roster_players":    len(roster),
        "buy_low_fa":        len(waivers.get("buy_low_fa", [])),
        "two_starters_fa":   len(waivers.get("two_starters_fa", [])),
        "ownership_alerts":  len(waivers.get("ownership_alerts", [])),
    }


if __name__ == "__main__":
    run()
