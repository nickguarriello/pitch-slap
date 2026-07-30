"""
pipeline/fetch_espn.py — Pipeline Step 1
Fetch all ESPN Fantasy data: rosters, matchups, FA pool, ownership,
transactions, waiver priority, acquisitions used this week.

Writes to: fact_espn_rosters, fact_matchups, constraint_log
Returns: structured dict consumed by transform.py
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (
    DB_PATH, DATA_DIR, LEAGUE_ID, SEASON, TEAM_ID,
    ESPN_S2, ESPN_SWID,
    ESPN_PRO_TEAM_MAP, ESPN_LINEUP_SLOT_MAP, ESPN_PROJ_KEYS,
    WEEKLY_ACQUISITION_LIMIT, API_CONFIG, ALL_CATS, CATS_LOWER_IS_BETTER,
)

ESPN_API_BASE = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"

META_PATH = os.path.join(DATA_DIR, "pipeline-meta.json")


# ---------------------------------------------------------------------------
# Connect to ESPN
# ---------------------------------------------------------------------------

def connect(retries: int = API_CONFIG['max_retries']) -> object:
    from espn_api.baseball import League
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            league = League(
                league_id=LEAGUE_ID,
                year=SEASON,
                espn_s2=ESPN_S2,
                swid=ESPN_SWID,
            )
            # Auth check — 0 teams = cookie expired
            if not league.teams:
                raise RuntimeError("AUTH_FAILURE: ESPN returned 0 teams — cookies expired")
            return league
        except RuntimeError:
            raise
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(API_CONFIG['retry_delay'])
    raise RuntimeError(f"ESPN connection failed after {retries} attempts: {last_err}")


# ---------------------------------------------------------------------------
# Roster fetch
# ---------------------------------------------------------------------------

def fetch_rosters(league) -> list[dict]:
    """Return one dict per player across all 8 rosters + FA pool."""
    rows: list[dict] = []
    today = datetime.now(timezone.utc).date().isoformat()

    for team in league.teams:
        for player in team.roster:
            rows.append(_build_player_row(player, team.team_id, today, is_fa=False))

    # FA pool
    try:
        fa_players = league.free_agents(size=300)
        for player in fa_players:
            rows.append(_build_player_row(player, None, today, is_fa=True))
    except Exception as e:
        print(f"  WARNING: FA pool fetch failed: {e}")

    return rows


# ESPN eligibleSlots (espn_api position names) -> our ROSTER_SLOTS codes.
_ESPN_SLOT_TO_ROSTER = {
    'C': 'C', '1B': '1B', '2B': '2B', '3B': '3B', 'SS': 'SS',
    '1B/3B': 'CI', '2B/SS': 'MI',
    'OF': 'OF', 'LF': 'OF', 'CF': 'OF', 'RF': 'OF',
    'DH': 'UTIL', 'UTIL': 'UTIL',
    'P': 'P', 'SP': 'P', 'RP': 'P',
    'BE': 'BN', 'IL': 'IL',
    # 'IF' (generic infield) intentionally dropped — specific IF slots already present.
}


def _normalize_eligible(player) -> str:
    """Comma-joined ROSTER_SLOTS codes a player is eligible for (multi-position)."""
    raw = getattr(player, 'eligibleSlots', None) or []
    out = []
    for s in raw:
        # espn_api usually returns slot-name strings; tolerate ints defensively.
        name = ESPN_LINEUP_SLOT_MAP.get(s, str(s)) if isinstance(s, int) else s
        code = _ESPN_SLOT_TO_ROSTER.get(name)
        if code and code not in out:
            out.append(code)
    return ','.join(out)


def _build_player_row(player, espn_team_id, snapshot_date: str, is_fa: bool) -> dict:
    slot_id    = getattr(player, 'lineupSlot', None)
    lineup_slot = ESPN_LINEUP_SLOT_MAP.get(slot_id, str(slot_id) if slot_id is not None else '')

    pro_team_id = getattr(player, 'proTeamId', None)
    mlb_team    = ESPN_PRO_TEAM_MAP.get(pro_team_id, '') if pro_team_id is not None else ''

    injury      = getattr(player, 'injuryStatus', '') or ''
    acq_type    = getattr(player, 'acquisitionType', '') or ''
    pct_owned   = getattr(player, 'percent_owned', None)
    pct_owned_delta = getattr(player, 'percent_owned_change', None)
    pct_started = getattr(player, 'percent_started', None)
    espn_rating = getattr(player, 'total_points', None)

    # Projections from player.stats (ESPN's rest-of-season projections)
    proj = _extract_projections(player)

    inactive_slots = {15, 16, 17, 18, 19}
    is_active = int(slot_id not in inactive_slots) if slot_id is not None else 1
    is_il     = int(slot_id in {17, 18, 19}) if slot_id is not None else 0

    return {
        'snapshot_date':      snapshot_date,
        'player_id':          str(player.playerId),
        'name':               player.name,
        'position':           getattr(player, 'position', ''),
        'mlb_team':           mlb_team,
        'espn_team_id':       espn_team_id,
        'lineup_slot':        lineup_slot,
        'is_active':          is_active,
        'is_il':              is_il,
        'ownership_pct':      pct_owned,
        'ownership_delta_7d': pct_owned_delta,
        'start_pct':          pct_started,
        'espn_rating':        espn_rating,
        'injury_status':      injury.upper() if injury else '',
        'acquisition_type':   acq_type.upper() if acq_type else '',
        'eligible_slots':     _normalize_eligible(player),
        **proj,
    }


def _extract_projections(player) -> dict:
    proj = {f'espn_proj_{cat}': None for cat in ['r','hr','rbi','sb','obp','k','qs','era','whip','svhd']}
    # Projections live in the season period entry (key 0) under
    # 'projected_breakdown', keyed by stat abbreviations (R, HR, ERA, SVHD...).
    stats = getattr(player, 'stats', {}) or {}
    breakdown = (stats.get(0) or {}).get('projected_breakdown')
    if not breakdown:
        # Fallback: any period entry that carries a projection breakdown.
        for entry in stats.values():
            if isinstance(entry, dict) and entry.get('projected_breakdown'):
                breakdown = entry['projected_breakdown']
                break
    breakdown = breakdown or {}
    for abbr, cat in ESPN_PROJ_KEYS.items():
        val = breakdown.get(abbr)
        if val is None:
            continue
        try:
            proj[f'espn_proj_{cat}'] = float(val)
        except (TypeError, ValueError):
            proj[f'espn_proj_{cat}'] = None
    return proj


def _extract_espn_actuals(player) -> dict | None:
    """ESPN's OWN actual season ERA / WHIP / OBP from stats[0]['breakdown'],
    used to ground-truth our MLB-derived stats. Returns None if no usable line.
      ERA  = ER * 9 / (OUTS/3)      WHIP = ESPN's WHIP field
      OBP  = (H + BB + HBP) / (AB + BB + HBP + SF)
    """
    bd = ((getattr(player, 'stats', {}) or {}).get(0) or {}).get('breakdown') or {}
    if not bd:
        return None
    out: dict = {}
    outs = bd.get('OUTS')
    if outs:  # pitcher line
        ip = outs / 3.0
        er = bd.get('ER')
        if ip > 0 and er is not None:
            out['era'] = round(er * 9.0 / ip, 3)
        if bd.get('WHIP') is not None:
            out['whip'] = round(float(bd['WHIP']), 3)
        out['ip'] = round(ip, 1)
    ab = bd.get('AB')
    if ab:  # hitter line
        bb  = bd.get('B_BB', 0) or 0
        hbp = bd.get('HBP', 0) or 0
        sf  = bd.get('SF', 0) or 0
        h   = bd.get('H', 0) or 0
        denom = ab + bb + hbp + sf
        if denom > 0:
            out['obp'] = round((h + bb + hbp) / denom, 3)
        out['pa'] = bd.get('PA')
    return out or None


def write_espn_actuals(league) -> int:
    """Write ESPN's actual season ERA/WHIP/OBP per rostered player to
    data/espn-actuals.json for validate.check_ground_truth_espn to compare
    our computed stats against. Rostered players only (the ones that matter)."""
    actuals: dict = {}
    for team in league.teams:
        for player in team.roster:
            a = _extract_espn_actuals(player)
            if a:
                actuals[str(player.playerId)] = a
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, 'espn-actuals.json'), 'w') as f:
        json.dump(actuals, f, indent=2)
    return len(actuals)


# ---------------------------------------------------------------------------
# Direct ESPN API helper
# ---------------------------------------------------------------------------

def _espn_get(views: list[str], params: dict | None = None) -> dict:
    """Direct request to ESPN Fantasy API with cookie auth."""
    cookies = {'espn_s2': ESPN_S2, 'SWID': ESPN_SWID}
    qparams = {'view': views}
    if params:
        qparams.update(params)
    resp = requests.get(
        ESPN_API_BASE,
        params=qparams,
        cookies=cookies,
        timeout=API_CONFIG['request_timeout'],
    )
    resp.raise_for_status()
    return resp.json()


def get_espn_update_status() -> dict:
    """ESPN's own 'last updated' timestamps from the league status view.

    `standingsUpdateDate` is ESPN's nightly batch — it reprocesses the prior
    day's results (posting fantasy stats), updates standings, then runs waivers
    (`waiverLastExecutionDate`, same instant). So standings_update ≈ "when ESPN
    finished posting last night's stats". Tracked over time to learn ESPN's
    nightly update timing — directly readable, no polling required.
    """
    d  = _espn_get(['mStatus'])
    st = d.get('status', {}) or {}

    def _iso(ms):
        if isinstance(ms, (int, float)) and ms > 1e12:
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
        return None

    return {
        "standings_update":      _iso(st.get('standingsUpdateDate')),
        "waiver_last_execution": _iso(st.get('waiverLastExecutionDate')),
        "scoring_period":        d.get('scoringPeriodId'),
        "checked_at":            datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Matchup fetch (live per-cat + historical)
# ---------------------------------------------------------------------------

# ESPN stat ID → our cat name mapping (subset used in matchup response)
_MATCHUP_STAT_MAP = {v: k for k, v in {
    'R': '20', 'HR': '5', 'RBI': '21', 'SB': '23', 'OBP': '17',
    'K': '48', 'QS': '63', 'ERA': '47', 'WHIP': '41', 'SvHd': '83',
}.items()}   # cat_name → stat_id


def fetch_matchups(league) -> dict:
    """
    Return current week live per-cat scores + high-level scoreboard.
    Uses direct API call for per-cat data since espn-api doesn't expose it.
    """
    week = _current_week_number()
    result = {
        'current_week': week,
        'current_scores': [],   # per-cat dicts for each matchup
        'scoreboard': [],       # high-level (total cats won) from library
        'history': [],
    }

    # High-level scoreboard (total cats won per team)
    try:
        sb = league.scoreboard()
        for m in sb:
            result['scoreboard'].append({
                'home_team_id':   m.home_team.team_id,
                'away_team_id':   m.away_team.team_id,
                'home_cats_won':  m.home_team_live_score,
                'away_cats_won':  m.away_team_live_score,
                'winner':         str(m.winner),
            })
    except Exception as e:
        print(f"  WARNING: Scoreboard fetch failed: {e}")

    # Per-cat breakdown via direct API
    try:
        data = _espn_get(['mMatchup', 'mMatchupScore'])
        schedule = data.get('schedule', [])
        # Identify the current matchup period from ESPN's own data, not the
        # date-based week number: ESPN pauses matchupPeriodId over the All-Star
        # break, so from mid-July on the date math (_current_week_number) runs one
        # ahead of the real period. The old `period != week` filter then matched an
        # empty *future* matchup and every my_val/opp_val came back None — the
        # dashboard showed UNKNOWN for all categories (since 2026-07-13). The
        # current week is the earliest matchup not yet decided (completed weeks
        # carry a winner; current + future weeks are UNDECIDED).
        undecided = sorted(
            m.get('matchupPeriodId') for m in schedule
            if m.get('winner', 'UNDECIDED') == 'UNDECIDED'
            and m.get('matchupPeriodId') is not None
        )
        current_period = undecided[0] if undecided else week
        if current_period != week:
            print(f"  Note: current matchup period is {current_period} "
                  f"(date-based week {week}; ASG-break offset)")
        result['current_week'] = current_period
        for matchup in schedule:
            period = matchup.get('matchupPeriodId')
            if period != current_period:
                continue
            home = matchup.get('home', {})
            away = matchup.get('away', {})
            home_id = home.get('teamId')
            away_id = away.get('teamId')

            home_cats = _parse_cat_scores(home.get('cumulativeScore', {}))
            away_cats = _parse_cat_scores(away.get('cumulativeScore', {}))

            result['current_scores'].append({
                'home_team_id': home_id,
                'away_team_id': away_id,
                'home_cats':    home_cats,
                'away_cats':    away_cats,
                'period':       period,
            })

        print(f"  Per-cat matchup data: {len(result['current_scores'])} matchups")
    except Exception as e:
        print(f"  WARNING: Per-cat matchup fetch failed: {e}")

    # Historical — fetch completed weeks one at a time (requires scoringPeriodId)
    if week > 1:
        print(f"  Fetching historical matchup data (weeks 1-{week-1})...")
        for past_week in range(1, week):
            try:
                hist_data = _espn_get(['mMatchup', 'mMatchupScore'],
                                      params={'scoringPeriodId': past_week})
                for matchup in hist_data.get('schedule', []):
                    if matchup.get('matchupPeriodId') != past_week:
                        continue
                    winner_val = matchup.get('winner', 'UNDECIDED')
                    if winner_val == 'UNDECIDED':
                        continue
                    home = matchup.get('home', {})
                    away = matchup.get('away', {})
                    result['history'].append({
                        'week':         past_week,
                        'home_team_id': home.get('teamId'),
                        'away_team_id': away.get('teamId'),
                        'home_cats':    _parse_cat_scores(home.get('cumulativeScore', {})),
                        'away_cats':    _parse_cat_scores(away.get('cumulativeScore', {})),
                        'winner':       winner_val,
                        'is_final':     1,
                    })
            except Exception as e:
                print(f"  WARNING: Historical week {past_week} failed: {e}")
        print(f"  Historical matchups fetched: {len(result['history'])} entries")

    return result


# ESPN stat_id → our display cat name
_STAT_ID_TO_CAT = {
    '20': 'R', '5': 'HR', '21': 'RBI', '23': 'SB', '17': 'OBP',
    '48': 'K', '63': 'QS', '47': 'ERA', '41': 'WHIP', '83': 'SvHd',
}


def _parse_cat_scores(cumulative: dict) -> dict:
    """Extract per-cat values from ESPN cumulativeScore dict.
    scoreByStat keys ARE the stat IDs (strings); score is in entry['score'].
    """
    scores: dict = {}
    for stat_id_str, entry in cumulative.get('scoreByStat', {}).items():
        cat = _STAT_ID_TO_CAT.get(str(stat_id_str))
        if cat:
            scores[cat] = entry.get('score')
    return scores


# ---------------------------------------------------------------------------
# Transactions / constraints
# ---------------------------------------------------------------------------

def fetch_constraints(league) -> dict:
    """
    Pull acquisitions used this week + waiver priority position.
    Actions are tuples: (Team, action_type_str, player_name_str).
    """
    week        = _current_week_number()
    acq_used    = 0
    wv_priority = None

    # Waiver priority from mTeam view (waiverRank per team)
    try:
        data = _espn_get(['mTeam'])
        for team_data in data.get('teams', []):
            if team_data.get('id') == TEAM_ID:
                wv_priority = team_data.get('waiverRank')
                break
    except Exception as e:
        print(f"  WARNING: Waiver priority fetch failed: {e}")

    # Count this-week add transactions for MY team
    # activity.date is epoch milliseconds (int)
    try:
        from datetime import date, timedelta
        from config import WEEK_1_START, WEEK_1_END, WEEK_2_START
        today      = date.today()
        w1_end     = date.fromisoformat(WEEK_1_END)
        w2_start   = date.fromisoformat(WEEK_2_START)
        if today <= w1_end:
            week_start = date.fromisoformat(WEEK_1_START)
        else:
            days_since_w2 = (today - w2_start).days
            weeks_offset  = days_since_w2 // 7
            week_start    = w2_start + timedelta(weeks=weeks_offset)

        week_start_ms = int(datetime(week_start.year, week_start.month, week_start.day,
                                     tzinfo=timezone.utc).timestamp() * 1000)

        recent = league.recent_activity(size=100)
        for activity in recent:
            act_ms = getattr(activity, 'date', 0) or 0
            if act_ms < week_start_ms:
                break   # activities are newest-first; past this week
            for action_tuple in getattr(activity, 'actions', []):
                team_obj, action_type, player_name = action_tuple
                if getattr(team_obj, 'team_id', None) != TEAM_ID:
                    continue
                if 'ADDED' in str(action_type).upper():
                    acq_used += 1
    except Exception as e:
        print(f"  WARNING: Transaction fetch failed (acq count may be 0): {e}")

    return {
        'matchup_week':           week,
        'acquisitions_used':      acq_used,
        'acquisitions_remaining': max(0, WEEKLY_ACQUISITION_LIMIT - acq_used),
        'waiver_priority':        wv_priority,
    }


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------

def write_rosters_to_db(rows: list[dict]) -> None:
    conn = sqlite3.connect(DB_PATH)

    today = rows[0]['snapshot_date'] if rows else datetime.now(timezone.utc).date().isoformat()
    conn.execute("DELETE FROM fact_espn_rosters WHERE snapshot_date = ?", (today,))

    conn.executemany(
        """
        INSERT INTO fact_espn_rosters (
            snapshot_date, player_id, espn_team_id, lineup_slot, is_active, is_il,
            ownership_pct, ownership_delta_7d, start_pct, espn_rating, injury_status,
            acquisition_type, eligible_slots,
            espn_proj_r, espn_proj_hr, espn_proj_rbi, espn_proj_sb, espn_proj_obp,
            espn_proj_k, espn_proj_qs, espn_proj_era, espn_proj_whip, espn_proj_svhd
        ) VALUES (
            :snapshot_date, :player_id, :espn_team_id, :lineup_slot, :is_active, :is_il,
            :ownership_pct, :ownership_delta_7d, :start_pct, :espn_rating, :injury_status,
            :acquisition_type, :eligible_slots,
            :espn_proj_r, :espn_proj_hr, :espn_proj_rbi, :espn_proj_sb, :espn_proj_obp,
            :espn_proj_k, :espn_proj_qs, :espn_proj_era, :espn_proj_whip, :espn_proj_svhd
        )
        """,
        rows,
    )
    conn.commit()
    conn.close()


def write_constraints_to_db(constraints: dict) -> None:
    conn = sqlite3.connect(DB_PATH)
    today = datetime.now(timezone.utc).date().isoformat()
    conn.execute(
        """
        INSERT INTO constraint_log
            (snapshot_date, matchup_week, acquisitions_used, acquisitions_remaining, waiver_priority)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            today,
            constraints['matchup_week'],
            constraints['acquisitions_used'],
            constraints['acquisitions_remaining'],
            constraints['waiver_priority'],
        ),
    )
    conn.commit()
    conn.close()


def write_matchups_to_db(matchup_data: dict) -> None:
    conn = sqlite3.connect(DB_PATH)
    week = matchup_data['current_week']
    ts   = datetime.now(timezone.utc).isoformat()

    # Per-cat snapshot from direct API data
    for m in matchup_data.get('current_scores', []):
        home_id = m['home_team_id']
        away_id = m['away_team_id']
        if TEAM_ID not in (home_id, away_id):
            continue
        my_cats  = m['home_cats'] if home_id == TEAM_ID else m['away_cats']
        opp_cats = m['away_cats'] if home_id == TEAM_ID else m['home_cats']
        for cat in ALL_CATS:
            conn.execute(
                "INSERT INTO matchup_snapshots (snapshot_ts, week, cat_name, my_val, opp_val) VALUES (?,?,?,?,?)",
                (ts, week, cat, my_cats.get(cat), opp_cats.get(cat)),
            )
        break   # only one matchup involves my team

    # Historical matchup results → fact_matchups.
    # Full-refresh my final rows first: the entire season history is re-fetched
    # every run, so without this the table accumulates a duplicate copy per run
    # (INSERT OR IGNORE can't dedup — there's no unique constraint), inflating
    # the per-category W/L records ~Nx where N = number of runs.
    conn.execute("DELETE FROM fact_matchups WHERE my_team_id = ? AND is_final = 1", (TEAM_ID,))
    for m in matchup_data.get('history', []):
        home_id = m['home_team_id']
        away_id = m['away_team_id']
        if TEAM_ID not in (home_id, away_id):
            continue
        my_cats  = m['home_cats'] if home_id == TEAM_ID else m['away_cats']
        opp_cats = m['away_cats'] if home_id == TEAM_ID else m['home_cats']
        opp_id   = away_id if home_id == TEAM_ID else home_id
        for cat in ALL_CATS:
            my_val  = my_cats.get(cat)
            opp_val = opp_cats.get(cat)
            if my_val is None and opp_val is None:
                continue
            # Determine W/L/T for this cat
            higher_better = cat not in ('ERA', 'WHIP')
            if my_val is None or opp_val is None:
                result = None
            elif my_val > opp_val:
                result = 'W' if higher_better else 'L'
            elif my_val < opp_val:
                result = 'L' if higher_better else 'W'
            else:
                result = 'T'
            conn.execute(
                """INSERT OR IGNORE INTO fact_matchups
                   (season, week, my_team_id, opp_team_id, cat_name, my_val, opp_val, result, is_final)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (SEASON, m['week'], TEAM_ID, opp_id, cat, my_val, opp_val, result, 1),
            )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_week_number() -> int:
    from config import WEEK_1_START, WEEK_1_END, WEEK_2_START
    from datetime import date
    today = date.today()
    w1_start = date.fromisoformat(WEEK_1_START)
    w1_end   = date.fromisoformat(WEEK_1_END)
    w2_start = date.fromisoformat(WEEK_2_START)

    if today <= w1_end:
        return 1
    # From week 2 onward: standard 7-day weeks
    days_since_w2 = (today - w2_start).days
    return 2 + days_since_w2 // 7


def _compute_cat_records(history: list[dict]) -> dict[int, dict]:
    """
    Compute cumulative category W/L/T AND matchup W/L/T for all teams from history.
    Matchup W/L/T is derived from per-category results each week (more cats won = W).
    ESPN library team.ties is unreliable; this gives the true record including ties.
    """
    records: dict[int, dict] = {}

    def _ensure(tid):
        if tid not in records:
            records[tid] = {
                'cat_wins': 0, 'cat_losses': 0, 'cat_ties': 0,
                'matchup_wins': 0, 'matchup_losses': 0, 'matchup_ties': 0,
            }

    for m in history:
        home_id   = m.get('home_team_id')
        away_id   = m.get('away_team_id')
        home_cats = m.get('home_cats', {})
        away_cats = m.get('away_cats', {})
        if home_id is None or away_id is None:
            continue

        _ensure(home_id)
        _ensure(away_id)

        home_cw = home_cl = 0
        for cat in ALL_CATS:
            hv = home_cats.get(cat)
            av = away_cats.get(cat)
            if hv is None or av is None:
                continue
            lower_better = cat in CATS_LOWER_IS_BETTER
            if hv == av:
                records[home_id]['cat_ties'] += 1
                records[away_id]['cat_ties'] += 1
            elif (lower_better and hv < av) or (not lower_better and hv > av):
                records[home_id]['cat_wins']   += 1
                records[away_id]['cat_losses'] += 1
                home_cw += 1
            else:
                records[home_id]['cat_losses'] += 1
                records[away_id]['cat_wins']   += 1
                home_cl += 1

        # Matchup-level result from who won more categories
        if home_cw > home_cl:
            records[home_id]['matchup_wins']    += 1
            records[away_id]['matchup_losses']  += 1
        elif home_cw < home_cl:
            records[home_id]['matchup_losses']  += 1
            records[away_id]['matchup_wins']    += 1
        else:
            records[home_id]['matchup_ties']    += 1
            records[away_id]['matchup_ties']    += 1

    return records


def _update_team_cat_records(cat_records: dict[int, dict]) -> None:
    """Merge per-team category W/L/T into espn_teams.json."""
    path = os.path.join(DATA_DIR, 'espn_teams.json')
    if not os.path.exists(path):
        return
    with open(path) as f:
        data = json.load(f)
    teams = data.get('teams', data)
    for tid_int, rec in cat_records.items():
        tid_str = str(tid_int)
        if tid_str in teams:
            teams[tid_str].update(rec)
    if 'teams' in data:
        data['teams'] = teams
    else:
        data = {'teams': teams, 'league_meta': {}}
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"  Category records stored for {len(cat_records)} teams")


def _save_team_names(league) -> None:
    """Write team names, abbrevs, W/L records, and league meta to data/espn_teams.json."""
    teams: dict = {}
    for team in league.teams:
        tid  = str(team.team_id)
        wins   = int(getattr(team, 'wins',   0) or 0)
        losses = int(getattr(team, 'losses', 0) or 0)
        ties   = int(getattr(team, 'ties',   0) or 0)
        teams[tid] = {
            'name':   getattr(team, 'team_name',   None) or f'Team {team.team_id}',
            'abbrev': getattr(team, 'team_abbrev', None) or tid,
            'wins':   wins,
            'losses': losses,
            'ties':   ties,
            'pct':    round(wins / max(wins + losses, 1), 3),
        }

    # Try to detect playoff start week from league settings
    league_meta: dict = {}
    try:
        data = _espn_get(['mSettings'])
        sched = data.get('settings', {}).get('scheduleSettings', {})
        reg_weeks = sched.get('matchupPeriodCount')
        playoff_len = sched.get('playoffMatchupPeriodLength', 2)
        if reg_weeks:
            league_meta['playoff_start_week']      = reg_weeks + 1
            league_meta['regular_season_weeks']    = reg_weeks
            league_meta['playoff_weeks_per_round'] = playoff_len
            print(f"  Playoff start week detected: {reg_weeks + 1}")
    except Exception as e:
        print(f"  WARNING: Could not detect playoff week from mSettings: {e}")

    path = os.path.join(DATA_DIR, 'espn_teams.json')
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, 'w') as f:
        json.dump({'teams': teams, 'league_meta': league_meta}, f, indent=2)
    print(f"  Team names + records saved ({len(teams)} teams)")


def _update_meta(status: str, fetch_ts: str) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    meta: dict = {}
    if os.path.exists(META_PATH):
        with open(META_PATH) as f:
            try:
                meta = json.load(f)
            except Exception:
                meta = {}
    meta['espn_last_fetch']  = fetch_ts
    meta['espn_status']      = status
    with open(META_PATH, 'w') as f:
        json.dump(meta, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(mode: str = 'full') -> dict:
    """
    mode: 'full' | 'light'
    Both modes fetch ESPN. Light skips nothing on the ESPN side.
    """
    fetch_ts = datetime.now(timezone.utc).isoformat()
    print(f"[fetch_espn] mode={mode} ts={fetch_ts}")

    try:
        league = connect()
        print(f"  Connected — {len(league.teams)} teams")
    except RuntimeError as e:
        _update_meta('AUTH_FAILURE', fetch_ts)
        raise

    # Save team names for dashboard
    print("  Saving team names...")
    _save_team_names(league)

    # Rosters + FA pool
    print("  Fetching rosters...")
    roster_rows = fetch_rosters(league)
    rostered_count = sum(1 for r in roster_rows if r['espn_team_id'] is not None)
    fa_count       = len(roster_rows) - rostered_count
    print(f"  {rostered_count} rostered, {fa_count} FA")

    # ESPN's own actual season stats — ground-truth reference for validate.py
    n_actuals = write_espn_actuals(league)
    print(f"  ESPN actual stats extracted for {n_actuals} rostered players")

    # Matchups
    print("  Fetching matchup scores...")
    matchup_data = fetch_matchups(league)

    # Constraints
    print("  Fetching constraints / transactions...")
    constraints = fetch_constraints(league)
    print(f"  Acquisitions used: {constraints['acquisitions_used']}/{WEEKLY_ACQUISITION_LIMIT} | "
          f"Waiver priority: #{constraints['waiver_priority']}")

    # Write to DB
    write_rosters_to_db(roster_rows)
    write_constraints_to_db(constraints)
    write_matchups_to_db(matchup_data)

    # Compute + store cumulative category W/L/T records for all teams
    cat_records = _compute_cat_records(matchup_data.get('history', []))
    _update_team_cat_records(cat_records)

    # Write current opponent to its own file so main.py's _save_meta can't clobber it
    current_opp_id = None
    for m in matchup_data.get('current_scores', []):
        hid, aid = m['home_team_id'], m['away_team_id']
        if TEAM_ID in (hid, aid):
            current_opp_id = aid if hid == TEAM_ID else hid
            break
    opp_path = os.path.join(DATA_DIR, 'current-opponent.json')
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(opp_path, 'w') as f:
        json.dump({'current_opp_team_id': current_opp_id}, f)

    _update_meta('ok', fetch_ts)

    result = {
        'fetch_ts':      fetch_ts,
        'rostered':      rostered_count,
        'fa_pool':       fa_count,
        'matchup_week':  matchup_data.get('current_week'),
        'constraints':   constraints,
        'roster_rows':   roster_rows,
    }

    print(f"[fetch_espn] done — {len(roster_rows)} players written")
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='full', choices=['full', 'light'])
    args = parser.parse_args()
    run(mode=args.mode)
