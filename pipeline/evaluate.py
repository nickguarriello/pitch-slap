"""
pipeline/evaluate.py -- Phase 4
The decision engine. Reads from all fact tables and produces scored outputs:

  1. Category state model (WIN / FLOPPABLE / FLIPPABLE / LOSS per cat)
  2. Category need weights (0.1-1.0 per cat)
  3. Buy-low flags (batters and pitchers underperforming vs expected)
  4. Sell-high flags (batters and pitchers overperforming expected)
  5. Two-start pitcher scorer
  6. Ownership velocity alerts (rapid ownership gain)
  7. Waiver and IL return recommendations

Writes to: data/evaluation-report.json
Does NOT write to any DB table (read-only from DB).
"""

import json
import math
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (
    DATA_DIR, DB_PATH, TEAM_ID,
    ALL_CATS, CATS_LOWER_IS_BETTER,
    CAT_THRESHOLDS,
    NEED_WEIGHT_HIGH, NEED_WEIGHT_MID, NEED_WEIGHT_LOW,
    NEED_RANK_HIGH_CUTOFF, NEED_RANK_LOW_CUTOFF,
    NEED_WIN_RATE_HIGH, NEED_WIN_RATE_LOW,
    BUY_LOW, SELL_HIGH,
    TWO_START_WEIGHTS,
    ACQ_URGENCY_WARN, ACQ_URGENCY_LAST,
    IP_MINIMUM_PER_WEEK,
    WAIVER_PRIORITY_CONFIDENT, WAIVER_PRIORITY_AT_RISK,
    WEEKLY_ACQUISITION_LIMIT,
)

EVAL_REPORT_PATH = os.path.join(DATA_DIR, "evaluation-report.json")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _f(val) -> float | None:
    if val is None:
        return None
    try:
        v = float(val)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 1. Category state model
# ---------------------------------------------------------------------------

_CAT_TO_STAT = {
    "R": "r", "HR": "hr", "RBI": "rbi", "SB": "sb", "OBP": "obp",
    "K": "k", "QS": "qs", "ERA": "era", "WHIP": "whip", "SvHd": "svhd",
}


def _get_current_matchup_scores(conn: sqlite3.Connection) -> dict[str, dict]:
    """
    Return {cat: {my_val, opp_val}} from the most recent matchup_snapshots row.
    Falls back to fact_matchups for the current week if no live snapshot.
    """
    rows = conn.execute(
        """
        SELECT cat_name, my_val, opp_val
        FROM matchup_snapshots
        WHERE week = (SELECT MAX(week) FROM matchup_snapshots)
          AND snapshot_ts = (SELECT MAX(snapshot_ts) FROM matchup_snapshots)
        """
    ).fetchall()

    if rows:
        return {r["cat_name"]: {"my_val": _f(r["my_val"]), "opp_val": _f(r["opp_val"])} for r in rows}

    # Fall back to current-week fact_matchups (in-progress)
    rows = conn.execute(
        """
        SELECT cat_name, my_val, opp_val
        FROM fact_matchups
        WHERE season = (SELECT MAX(season) FROM fact_matchups)
          AND week  = (SELECT MAX(week)   FROM fact_matchups)
          AND is_final = 0
        """
    ).fetchall()
    return {r["cat_name"]: {"my_val": _f(r["my_val"]), "opp_val": _f(r["opp_val"])} for r in rows}


def compute_cat_states(conn: sqlite3.Connection) -> dict:
    """
    Return {cat: {state, my_val, opp_val, gap}} for each scoring category.
    State: 'WIN' | 'FLOPPABLE' | 'FLIPPABLE' | 'LOSS' | 'TIE' | 'UNKNOWN'

    ERA and WHIP have a weekly IP minimum qualifier. If we haven't reached it,
    we automatically lose those categories regardless of current ERA/WHIP values.
    """
    scores = _get_current_matchup_scores(conn)

    # Current-week IP for my team (to apply ERA/WHIP qualifier)
    ip_row = conn.execute(
        """
        SELECT COALESCE(SUM(fps.ip), 0.0) AS total_ip
        FROM fact_player_stats fps
        JOIN fact_espn_rosters fer ON fps.player_id = fer.player_id
        WHERE fps.window = 'current'
          AND fps.ip IS NOT NULL AND fps.ip > 0
          AND fer.espn_team_id = ?
          AND fer.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)
        """,
        (TEAM_ID,),
    ).fetchone()
    my_ip_week = _f(ip_row["total_ip"]) or 0.0

    states: dict[str, dict] = {}

    for cat in ALL_CATS:
        data = scores.get(cat, {})
        my   = data.get("my_val")
        opp  = data.get("opp_val")

        if my is None or opp is None:
            states[cat] = {"state": "UNKNOWN", "my_val": my, "opp_val": opp, "gap": None}
            continue

        # IP qualifier: ERA and WHIP require IP_MINIMUM_PER_WEEK innings pitched.
        # If we haven't reached it, we auto-lose regardless of current ERA/WHIP.
        if cat in ("ERA", "WHIP") and my_ip_week < IP_MINIMUM_PER_WEEK:
            if opp is not None and opp > 0:
                # Opponent has pitched; we're unqualified → auto-loss
                states[cat] = {
                    "state":      "LOSS",
                    "my_val":     my,
                    "opp_val":    opp,
                    "gap":        round(-opp, 4),  # negative gap = loss
                    "ip_note":    f"Need {IP_MINIMUM_PER_WEEK:.0f} IP to qualify ({my_ip_week:.1f} so far)",
                    "my_ip_week": my_ip_week,
                }
            else:
                # Neither team has pitched yet — genuinely unknown
                states[cat] = {"state": "UNKNOWN", "my_val": my, "opp_val": opp, "gap": None}
            continue

        lower_better = cat in CATS_LOWER_IS_BETTER
        gap          = (opp - my) if lower_better else (my - opp)  # positive = I'm ahead
        thresholds   = CAT_THRESHOLDS.get(cat, {})
        floppable    = thresholds.get("floppable", 0)
        flippable    = thresholds.get("flippable", 0)

        if gap > floppable:
            state = "WIN"
        elif gap > 0:
            state = "FLOPPABLE"
        elif gap == 0:
            state = "TIE"
        elif abs(gap) <= flippable:
            state = "FLIPPABLE"
        else:
            state = "LOSS"

        states[cat] = {
            "state":   state,
            "my_val":  my,
            "opp_val": opp,
            "gap":     round(gap, 4),
        }

    return states


# ---------------------------------------------------------------------------
# 2. Category need weights
# ---------------------------------------------------------------------------

def compute_need_weights(conn: sqlite3.Connection, team_id: int = TEAM_ID) -> dict[str, float]:
    """
    Return {cat: weight (0.1–1.0)} for `team_id` (defaults to my team) derived from:
      - Historical rank (1-10 across league) in each cat this season
      - Win rate in each cat from fact_matchups

    Parameterized by team_id so the trade analyzer can score the *other* team's
    needs for the acceptance gate.
    """
    weights: dict[str, float] = {}

    # Get the team's season win rates per cat from fact_matchups (final matchups only)
    win_rates = {}
    rows = conn.execute(
        """
        SELECT cat_name,
               SUM(CASE WHEN result='W' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as win_rate,
               COUNT(*) as games
        FROM fact_matchups
        WHERE my_team_id = ? AND is_final = 1
        GROUP BY cat_name
        """,
        (team_id,),
    ).fetchall()
    for r in rows:
        win_rates[r["cat_name"]] = _f(r["win_rate"]) or 0.5

    # Get current standings rank approximation per cat from season stats
    # (rank among all teams: avg their rostered players' season stats)
    cat_ranks = _estimate_cat_ranks(conn, team_id)

    for cat in ALL_CATS:
        rank     = cat_ranks.get(cat, 5)         # 1=best, 8=worst
        win_rate = win_rates.get(cat, 0.5)

        # High need: bottom half of league + low win rate
        if rank >= NEED_RANK_HIGH_CUTOFF and win_rate <= NEED_WIN_RATE_HIGH:
            lo, hi = NEED_WEIGHT_HIGH
            # Scale within high band by how bad
            weight = lo + (hi - lo) * min(1.0, (rank - NEED_RANK_HIGH_CUTOFF) / 2)
        # Low need: top of league + high win rate
        elif rank <= NEED_RANK_LOW_CUTOFF and win_rate >= NEED_WIN_RATE_LOW:
            lo, hi = NEED_WEIGHT_LOW
            weight = hi - (hi - lo) * min(1.0, (NEED_RANK_LOW_CUTOFF - rank) / 2)
        else:
            lo, hi = NEED_WEIGHT_MID
            weight = lo + (hi - lo) * ((rank - 1) / 7)

        weights[cat] = round(min(1.0, max(0.1, weight)), 2)

    return weights


def _estimate_cat_ranks(conn: sqlite3.Connection, team_id: int = TEAM_ID) -> dict[str, int]:
    """
    Approximate `team_id`'s rank (1-8) per category by summing rostered players'
    season stats per team, then ranking.
    """
    stat_cols = {
        "R": "r", "HR": "hr", "RBI": "rbi", "SB": "sb",
        "K": "k", "QS": "qs", "SvHd": "svhd",
        "OBP": "obp", "ERA": "era", "WHIP": "whip",
    }
    rate_cats  = {"OBP", "ERA", "WHIP"}

    ranks: dict[str, int] = {}
    for cat, col in stat_cols.items():
        if cat in rate_cats:
            # Weighted average (by IP for pitchers, PA for hitters)
            weight_col = "ip" if cat in {"ERA", "WHIP"} else "pa"
            rows = conn.execute(
                f"""
                SELECT fer.espn_team_id,
                       SUM(fps.{col} * fps.{weight_col}) / NULLIF(SUM(fps.{weight_col}), 0) as val
                FROM fact_player_stats fps
                JOIN fact_espn_rosters fer ON fps.player_id = fer.player_id
                WHERE fps.window = 'season'
                  AND fps.{col} IS NOT NULL
                  AND fps.{weight_col} IS NOT NULL
                  AND fps.{weight_col} > 0
                  AND fer.espn_team_id IS NOT NULL
                  AND fer.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)
                GROUP BY fer.espn_team_id
                ORDER BY val ASC
                """
            ).fetchall()
        else:
            # Sum
            rows = conn.execute(
                f"""
                SELECT fer.espn_team_id,
                       SUM(fps.{col}) as val
                FROM fact_player_stats fps
                JOIN fact_espn_rosters fer ON fps.player_id = fer.player_id
                WHERE fps.window = 'season'
                  AND fps.{col} IS NOT NULL
                  AND fer.espn_team_id IS NOT NULL
                  AND fer.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)
                GROUP BY fer.espn_team_id
                ORDER BY val DESC
                """
            ).fetchall()

        # Find this team's rank
        my_rank = 5  # default mid
        for i, r in enumerate(rows):
            if r["espn_team_id"] == team_id:
                my_rank = i + 1
                break
        ranks[cat] = my_rank

    return ranks


# ---------------------------------------------------------------------------
# 3 & 4. Buy-low and sell-high flags
# ---------------------------------------------------------------------------

_HITTER_CATS  = {"R", "HR", "RBI", "SB", "OBP"}
_PITCHER_CATS = {"K", "QS", "ERA", "WHIP", "SvHd"}


def compute_buy_low_flags(
    conn: sqlite3.Connection,
    need_weights: dict[str, float] | None = None,
) -> list[dict]:
    """
    Flag players underperforming their expected stats.
    need_weights: if provided, scores are multiplied by category need so targets
    that address your weakest categories surface first.
    """
    need_weights = need_weights or {}
    hitter_need  = max((need_weights.get(c, 0.5) for c in _HITTER_CATS),  default=0.5)
    pitcher_need = max((need_weights.get(c, 0.5) for c in _PITCHER_CATS), default=0.5)

    flags: list[dict] = []

    # Hitters: depressed BABIP or OBP lagging xwOBA
    rows = conn.execute(
        """
        SELECT p.player_id, p.name, p.mlb_team, fer.espn_team_id, fer.eligible_slots,
               fps.obp, fps.pa, fps.babip,
               fs.xba, fs.xwoba, fs.sprint_speed, fs.wrc_plus
        FROM dim_players p
        JOIN fact_player_stats fps ON p.player_id = fps.player_id AND fps.window = 'season'
        JOIN fact_statcast fs      ON p.player_id = fs.player_id
        LEFT JOIN fact_espn_rosters fer ON p.player_id = fer.player_id
          AND fer.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)
        WHERE fps.pa >= ? AND fs.xwoba IS NOT NULL
        """,
        (BUY_LOW["min_pa"],),
    ).fetchall()

    for r in rows:
        xwoba = _f(r["xwoba"])

        # Quality floor: only flag hitters with real underlying upside
        if not xwoba or xwoba < BUY_LOW["min_xwoba"]:
            continue

        reasons = []
        score   = 0.0

        obp   = _f(r["obp"])
        babip = _f(r["babip"])
        speed = _f(r["sprint_speed"])

        # BABIP depressed without speed explanation
        babip_floor = BUY_LOW["hitter_babip_floor"]
        if speed and speed >= BUY_LOW["elite_sprint_speed"]:
            babip_floor -= 0.020
        if babip and babip < babip_floor:
            reasons.append(f"BABIP {babip:.3f} below floor ({babip_floor:.3f})")
            score += 0.4

        # OBP lags xwOBA
        if obp and (xwoba - obp) > BUY_LOW["hitter_xba_gap"]:
            reasons.append(f"OBP ({obp:.3f}) lags xwOBA ({xwoba:.3f})")
            score += 0.4

        if reasons:
            # Blend: 60% base score, 40% need-adjusted so weak-cat targets surface higher
            need_adjusted = round(score * (0.6 + 0.4 * hitter_need), 2)
            flags.append({
                "player_id":    r["player_id"],
                "name":         r["name"],
                "team":         r["mlb_team"],
                "espn_team_id": r["espn_team_id"],
                "rostered":     r["espn_team_id"] is not None,
                "eligible_slots": r["eligible_slots"],
                "flag_type":    "buy_low_hitter",
                "reasons":      reasons,
                "score":        need_adjusted,
                "stats": {
                    "obp": obp, "xwoba": xwoba, "babip": babip, "sprint_speed": speed,
                },
            })

    # Pitchers: ERA >> xFIP or SIERA (bad luck, will regress down)
    rows2 = conn.execute(
        """
        SELECT p.player_id, p.name, p.mlb_team, fer.espn_team_id, fer.eligible_slots,
               fps.era, fps.whip, fps.ip,
               fs.xfip, fs.siera, fs.fip
        FROM dim_players p
        JOIN fact_player_stats fps ON p.player_id = fps.player_id AND fps.window = 'season'
        JOIN fact_statcast fs      ON p.player_id = fs.player_id
        LEFT JOIN fact_espn_rosters fer ON p.player_id = fer.player_id
          AND fer.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)
        WHERE fps.ip >= ? AND fps.era IS NOT NULL AND fs.xfip IS NOT NULL
        """,
        (BUY_LOW["min_ip"],),
    ).fetchall()

    for r in rows2:
        reasons = []
        score   = 0.0

        era   = _f(r["era"])
        xfip  = _f(r["xfip"])
        siera = _f(r["siera"])
        fip   = _f(r["fip"])

        if era and xfip and (era - xfip) >= BUY_LOW["pitcher_era_xfip_gap"]:
            reasons.append(f"ERA ({era:.2f}) >> xFIP ({xfip:.2f}), gap={era-xfip:.2f}")
            score += 0.5

        if era and siera and (era - siera) >= BUY_LOW["pitcher_era_xfip_gap"]:
            reasons.append(f"ERA ({era:.2f}) >> SIERA ({siera:.2f}), gap={era-siera:.2f}")
            score += 0.3

        if reasons:
            need_adjusted = round(score * (0.6 + 0.4 * pitcher_need), 2)
            flags.append({
                "player_id":    r["player_id"],
                "name":         r["name"],
                "team":         r["mlb_team"],
                "espn_team_id": r["espn_team_id"],
                "rostered":     r["espn_team_id"] is not None,
                "eligible_slots": r["eligible_slots"],
                "flag_type":    "buy_low_pitcher",
                "reasons":      reasons,
                "score":        need_adjusted,
                "stats": {
                    "era": era, "xfip": xfip, "siera": siera, "ip": _f(r["ip"]),
                },
            })

    flags.sort(key=lambda x: x["score"], reverse=True)
    return flags


def compute_sell_high_flags(conn: sqlite3.Connection) -> list[dict]:
    """
    Flag players overperforming their expected stats (regression candidates).
    Returns list sorted by regression risk score.
    """
    flags: list[dict] = []

    # Hitters: OBP >> xwOBA (luck-driven, likely to regress)
    rows = conn.execute(
        """
        SELECT p.player_id, p.name, p.mlb_team, fer.espn_team_id, fer.eligible_slots,
               fps.obp, fps.pa, fps.babip,
               fs.xwoba
        FROM dim_players p
        JOIN fact_player_stats fps ON p.player_id = fps.player_id AND fps.window = 'season'
        JOIN fact_statcast fs      ON p.player_id = fs.player_id
        LEFT JOIN fact_espn_rosters fer ON p.player_id = fer.player_id
          AND fer.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)
        WHERE fps.pa >= ? AND fs.xwoba IS NOT NULL AND fps.obp IS NOT NULL
        """,
        (BUY_LOW["min_pa"],),
    ).fetchall()

    for r in rows:
        reasons = []
        score   = 0.0

        obp   = _f(r["obp"])
        xwoba = _f(r["xwoba"])
        babip = _f(r["babip"])

        if obp and xwoba and (obp - xwoba) > SELL_HIGH["hitter_xba_gap"]:
            reasons.append(f"OBP ({obp:.3f}) >> xwOBA ({xwoba:.3f}), gap={obp-xwoba:.3f}")
            score += 0.4

        pa = _f(r["pa"])
        min_pa = SELL_HIGH.get("min_babip_pa", 100)
        if babip and babip > SELL_HIGH["babip_ceiling"] and pa and pa >= min_pa:
            reasons.append(f"BABIP ({babip:.3f}) elevated — contact luck likely to normalize")
            score += 0.3

        if reasons:
            flags.append({
                "player_id":   r["player_id"],
                "name":        r["name"],
                "team":        r["mlb_team"],
                "espn_team_id": r["espn_team_id"],
                "rostered":    r["espn_team_id"] is not None,
                "eligible_slots": r["eligible_slots"],
                "flag_type":   "sell_high_hitter",
                "reasons":     reasons,
                "score":       round(score, 2),
                "stats": {
                    "obp": obp, "xwoba": xwoba, "babip": babip,
                },
            })

    # Pitchers: ERA << xFIP/SIERA (due for regression up)
    rows2 = conn.execute(
        """
        SELECT p.player_id, p.name, p.mlb_team, fer.espn_team_id, fer.eligible_slots,
               fps.era, fps.ip,
               fs.xfip, fs.siera
        FROM dim_players p
        JOIN fact_player_stats fps ON p.player_id = fps.player_id AND fps.window = 'season'
        JOIN fact_statcast fs      ON p.player_id = fs.player_id
        LEFT JOIN fact_espn_rosters fer ON p.player_id = fer.player_id
          AND fer.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)
        WHERE fps.ip >= ? AND fps.era IS NOT NULL AND fs.xfip IS NOT NULL
        """,
        (BUY_LOW["min_ip"],),
    ).fetchall()

    for r in rows2:
        reasons = []
        score   = 0.0

        era   = _f(r["era"])
        xfip  = _f(r["xfip"])
        siera = _f(r["siera"])

        if era and xfip and (xfip - era) >= SELL_HIGH["pitcher_era_xfip_gap"]:
            reasons.append(f"ERA ({era:.2f}) << xFIP ({xfip:.2f}), gap={xfip-era:.2f}")
            score += 0.5

        if era and siera and (siera - era) >= SELL_HIGH["pitcher_era_xfip_gap"]:
            reasons.append(f"ERA ({era:.2f}) << SIERA ({siera:.2f}), gap={siera-era:.2f}")
            score += 0.3

        if reasons:
            flags.append({
                "player_id":   r["player_id"],
                "name":        r["name"],
                "team":        r["mlb_team"],
                "espn_team_id": r["espn_team_id"],
                "rostered":    r["espn_team_id"] is not None,
                "eligible_slots": r["eligible_slots"],
                "flag_type":   "sell_high_pitcher",
                "reasons":     reasons,
                "score":       round(score, 2),
                "stats": {
                    "era": era, "xfip": xfip, "siera": siera, "ip": _f(r["ip"]),
                },
            })

    flags.sort(key=lambda x: x["score"], reverse=True)
    return flags


# ---------------------------------------------------------------------------
# 5. Two-start pitcher scorer
# ---------------------------------------------------------------------------

def compute_two_start_pitchers(
    conn: sqlite3.Connection,
    two_starter_ids: set[int] | None = None,
) -> list[dict]:
    """
    Score and rank pitchers with 2+ probable starts this week.
    two_starter_ids: set of MLB AM IDs from fetch_mlb.get_two_start_pitchers().
    If None, falls back to schedule-based detection from fact_schedule.
    """
    if two_starter_ids is None:
        # Detect from fact_schedule: pitchers appearing on 2+ distinct game_dates
        rows = conn.execute(
            """
            SELECT probable_pitcher_id, COUNT(DISTINCT game_date) as starts
            FROM fact_schedule
            WHERE probable_pitcher_id IS NOT NULL
            GROUP BY probable_pitcher_id
            HAVING starts >= 2
            """
        ).fetchall()
        two_starter_mlb_ids = {r["probable_pitcher_id"] for r in rows}
    else:
        two_starter_mlb_ids = two_starter_ids

    if not two_starter_mlb_ids:
        return []

    # Get start dates per pitcher from fact_schedule for days-of-rest scoring
    placeholders_ids = ",".join("?" * len(two_starter_mlb_ids))
    schedule_rows = conn.execute(
        f"""
        SELECT probable_pitcher_id, game_date
        FROM fact_schedule
        WHERE probable_pitcher_id IN ({placeholders_ids})
          AND probable_pitcher_id IS NOT NULL
        ORDER BY probable_pitcher_id, game_date
        """,
        list(two_starter_mlb_ids),
    ).fetchall()
    start_dates: dict[int, list[str]] = {}
    for sr in schedule_rows:
        start_dates.setdefault(sr["probable_pitcher_id"], []).append(sr["game_date"])

    # Fetch pitcher stats
    placeholders = ",".join("?" * len(two_starter_mlb_ids))
    rows = conn.execute(
        f"""
        SELECT p.player_id, p.name, p.mlb_team, p.mlb_id,
               fer.espn_team_id,
               fps_s.era, fps_s.whip, fps_s.ip,
               fps_14.k_pct as k_pct_14d, fps_14.bb_pct as bb_pct_14d,
               fs.xfip, fs.siera, fs.fip
        FROM dim_players p
        LEFT JOIN fact_player_stats fps_s  ON p.player_id = fps_s.player_id  AND fps_s.window  = 'season'
        LEFT JOIN fact_player_stats fps_14 ON p.player_id = fps_14.player_id AND fps_14.window = '14d'
        LEFT JOIN fact_statcast fs         ON p.player_id = fs.player_id
        LEFT JOIN fact_espn_rosters fer    ON p.player_id = fer.player_id
          AND fer.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)
        WHERE p.mlb_id IN ({placeholders})
        """,
        list(two_starter_mlb_ids),
    ).fetchall()

    scored: list[dict] = []
    for r in rows:
        siera  = _f(r["siera"]) or 5.0
        xfip   = _f(r["xfip"])  or 5.0
        k_pct  = _f(r["k_pct_14d"])
        bb_pct = _f(r["bb_pct_14d"])
        mlb_id = r["mlb_id"]

        # Score: 0-10, higher is better
        siera_score = max(0, 10 - siera * 1.5)
        xfip_score  = max(0, 10 - xfip * 1.5)

        # K/BB ratio stability (14-day window)
        stability_score = 5.0
        if k_pct and bb_pct:
            kbb_ratio = k_pct / max(bb_pct, 0.01)
            stability_score = min(10, kbb_ratio * 2)

        # Days of rest between starts (4+ days = regular rest = best)
        rest_score = 5.0
        if mlb_id in start_dates and len(start_dates[mlb_id]) >= 2:
            from datetime import date as _date
            dates = sorted(start_dates[mlb_id])
            try:
                d1 = _date.fromisoformat(dates[0])
                d2 = _date.fromisoformat(dates[1])
                days_between = (d2 - d1).days
                rest_score = 10.0 if days_between >= 4 else (7.0 if days_between == 3 else 3.0)
            except ValueError:
                pass

        used_weights = (
            TWO_START_WEIGHTS["siera"] +
            TWO_START_WEIGHTS["xfip"] +
            TWO_START_WEIGHTS["k_pct_stability"] +
            TWO_START_WEIGHTS["days_of_rest"]
        )
        composite = (
            siera_score     * TWO_START_WEIGHTS["siera"] +
            xfip_score      * TWO_START_WEIGHTS["xfip"] +
            stability_score * TWO_START_WEIGHTS["k_pct_stability"] +
            rest_score      * TWO_START_WEIGHTS["days_of_rest"]
        ) / used_weights

        scored.append({
            "player_id":   r["player_id"],
            "name":        r["name"],
            "team":        r["mlb_team"],
            "espn_team_id": r["espn_team_id"],
            "rostered":    r["espn_team_id"] is not None,
            "score":       round(composite, 2),
            "stats": {
                "era":    _f(r["era"]),
                "siera":  _f(r["siera"]),
                "xfip":   _f(r["xfip"]),
                "ip":     _f(r["ip"]),
                "k_pct_14d": k_pct,
            },
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# 6. Ownership velocity alerts
# ---------------------------------------------------------------------------

def compute_ownership_velocity(conn: sqlite3.Connection) -> list[dict]:
    """
    Flag players whose ownership % jumped 5%+ in the last 2 days.
    Compares the most recent snapshot to the nearest snapshot from ~2 days ago.
    Running the pipeline 3x/day makes consecutive-snapshot comparisons noisy;
    a 2-day window captures meaningful pickup spikes (injury news, hot streaks).
    """
    recent_row = conn.execute(
        "SELECT MAX(snapshot_date) FROM fact_espn_rosters"
    ).fetchone()
    if not recent_row or not recent_row[0]:
        return []
    recent = recent_row[0]

    # Find the nearest snapshot at or before 2 days ago
    two_days_ago = (date.fromisoformat(recent) - timedelta(days=2)).isoformat()
    prev_row = conn.execute(
        "SELECT MAX(snapshot_date) FROM fact_espn_rosters WHERE snapshot_date <= ?",
        (two_days_ago,),
    ).fetchone()
    if not prev_row or not prev_row[0]:
        return []
    prev = prev_row[0]

    rows = conn.execute(
        """
        SELECT p.player_id, p.name, p.mlb_team,
               r.ownership_pct as recent_pct, pr.ownership_pct as prev_pct,
               r.espn_team_id
        FROM fact_espn_rosters r
        JOIN fact_espn_rosters pr ON r.player_id = pr.player_id AND pr.snapshot_date = ?
        JOIN dim_players p         ON r.player_id = p.player_id
        WHERE r.snapshot_date = ?
          AND r.ownership_pct IS NOT NULL AND pr.ownership_pct IS NOT NULL
          AND (r.ownership_pct - pr.ownership_pct) >= 5.0
        ORDER BY (r.ownership_pct - pr.ownership_pct) DESC
        """,
        (prev, recent),
    ).fetchall()

    return [
        {
            "player_id":      r["player_id"],
            "name":           r["name"],
            "team":           r["mlb_team"],
            "espn_team_id":   r["espn_team_id"],
            "rostered":       r["espn_team_id"] is not None,
            "ownership_now":  _f(r["recent_pct"]),
            "ownership_prev": _f(r["prev_pct"]),
            "delta":          round((_f(r["recent_pct"]) or 0) - (_f(r["prev_pct"]) or 0), 1),
            "lookback_days":  (date.fromisoformat(recent) - date.fromisoformat(prev)).days,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 7. Constraint state
# ---------------------------------------------------------------------------

def compute_constraint_state(conn: sqlite3.Connection) -> dict:
    """Return current week's acquisition and IP constraint status."""
    row = conn.execute(
        """
        SELECT acquisitions_used, acquisitions_remaining,
               ip_accumulated, ip_remaining, waiver_priority
        FROM constraint_log
        ORDER BY snapshot_date DESC LIMIT 1
        """
    ).fetchone()

    if not row:
        return {
            "acquisitions_used": None,
            "acquisitions_remaining": None,
            "ip_accumulated": None,
            "ip_remaining": None,
            "waiver_priority": None,
            "acq_alert": None,
            "ip_alert": None,
        }

    used      = row["acquisitions_used"]
    remaining = row["acquisitions_remaining"]
    priority  = row["waiver_priority"]

    # Compute IP from fact_player_stats.current window (pitchers on our roster).
    ip_row = conn.execute(
        """
        SELECT COALESCE(SUM(fps.ip), 0.0) AS total_ip
        FROM fact_player_stats fps
        JOIN fact_espn_rosters fer ON fps.player_id = fer.player_id
        WHERE fps.window = 'current'
          AND fps.ip IS NOT NULL AND fps.ip > 0
          AND fer.espn_team_id = ?
          AND fer.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)
        """,
        (TEAM_ID,),
    ).fetchone()
    ip_accum  = _f(ip_row["total_ip"]) if ip_row else 0.0
    ip_remain = round(max(0.0, IP_MINIMUM_PER_WEEK - ip_accum), 1)

    acq_alert = None
    if used >= ACQ_URGENCY_LAST:
        acq_alert = f"LAST MOVE: {remaining} acquisition remaining this week"
    elif used >= ACQ_URGENCY_WARN:
        acq_alert = f"HIGH SPEND: {used}/{WEEKLY_ACQUISITION_LIMIT} acquisitions used"

    ip_alert = None
    today_dow = date.today().weekday()  # 0=Mon, 3=Thu, 6=Sun
    if ip_accum is not None:
        if today_dow >= 3 and ip_accum < IP_MINIMUM_PER_WEEK * 0.50:
            ip_alert = f"IP PACE: only {ip_accum:.1f} IP by Thursday — at risk of minimum"
        elif today_dow >= 5 and ip_accum < IP_MINIMUM_PER_WEEK:
            ip_alert = f"IP ALERT: {ip_accum:.1f}/{IP_MINIMUM_PER_WEEK} IP with weekend remaining"

    waiver_alert = None
    lo, hi = WAIVER_PRIORITY_CONFIDENT
    at_lo, at_hi = WAIVER_PRIORITY_AT_RISK
    if priority and priority <= hi:
        waiver_alert = f"STRONG: waiver priority #{priority} — act early in week"
    elif priority and priority >= at_lo:
        waiver_alert = f"AT RISK: waiver priority #{priority} — likely to drop post-claim"

    return {
        "acquisitions_used":      used,
        "acquisitions_remaining": remaining,
        "ip_accumulated":         ip_accum,
        "ip_remaining":           ip_remain,
        "waiver_priority":        priority,
        "acq_alert":              acq_alert,
        "ip_alert":               ip_alert,
        "waiver_alert":           waiver_alert,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(two_starter_mlb_ids: set[int] | None = None) -> dict:
    """
    Run all evaluations. Returns the full evaluation report dict.
    Also writes data/evaluation-report.json.

    two_starter_mlb_ids: pass from fetch_mlb.get_two_start_pitchers() if available.
    """
    conn = _conn()

    print("evaluate.py: computing category states...")
    cat_states = compute_cat_states(conn)

    print("  computing need weights...")
    need_weights = compute_need_weights(conn)

    print("  computing buy-low flags...")
    buy_low = compute_buy_low_flags(conn, need_weights=need_weights)

    print("  computing sell-high flags...")
    sell_high = compute_sell_high_flags(conn)

    print("  computing two-start pitchers...")
    two_starters = compute_two_start_pitchers(conn, two_starter_mlb_ids)

    print("  computing ownership velocity...")
    ownership_alerts = compute_ownership_velocity(conn)

    print("  computing constraint state...")
    constraints = compute_constraint_state(conn)

    conn.close()

    report = {
        "run_ts":           datetime.now(timezone.utc).isoformat(),
        "as_of":            date.today().isoformat(),
        "cat_states":       cat_states,
        "need_weights":     need_weights,
        "buy_low":          buy_low[:20],          # top 20
        "sell_high":        sell_high[:20],        # top 20
        "two_starters":     two_starters,
        "ownership_alerts": ownership_alerts,
        "constraints":      constraints,
        "summary": {
            "cats_winning":   sum(1 for s in cat_states.values() if s["state"] == "WIN"),
            "cats_losing":    sum(1 for s in cat_states.values() if s["state"] == "LOSS"),
            "cats_flippable": sum(1 for s in cat_states.values() if s["state"] == "FLIPPABLE"),
            "buy_low_count":  len(buy_low),
            "sell_high_count": len(sell_high),
            "two_starters_count": len(two_starters),
        },
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(EVAL_REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\nEvaluation complete:")
    print(f"  Cat states:   {report['summary']['cats_winning']}W / "
          f"{report['summary']['cats_flippable']}FLIP / "
          f"{report['summary']['cats_losing']}L")
    print(f"  Buy-low:      {report['summary']['buy_low_count']} flags")
    print(f"  Sell-high:    {report['summary']['sell_high_count']} flags")
    print(f"  Two-starters: {report['summary']['two_starters_count']}")
    constraints_str = constraints.get("acq_alert") or constraints.get("ip_alert") or "no alerts"
    print(f"  Constraints:  {constraints_str}")

    return report


if __name__ == "__main__":
    run()
