"""
pipeline/build_crosswalk.py — Phase 1, Day 1 Priority
Build the dim_players crosswalk: ESPN ID ↔ MLB AM ID ↔ FanGraphs ID ↔ BBRef ID.

Run once on day 1. Refresh weekly (Mondays) to pick up new callups.
After this runs, all downstream joins use player_id (ESPN). Name matching never runs again.

Manual override file: data/crosswalk-overrides.json
  Format: {"espn_player_id": {"mlb_id": 123456, "note": "manual - two Max Muncys"}}
"""

import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone

import pandas as pd
from rapidfuzz import fuzz, process

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (
    DB_PATH, DATA_DIR, LEAGUE_ID, SEASON, TEAM_ID,
    ESPN_S2, ESPN_SWID, ESPN_PRO_TEAM_MAP,
)

OVERRIDES_PATH = os.path.join(DATA_DIR, "crosswalk-overrides.json")
FLAGS_PATH     = os.path.join(DATA_DIR, "crosswalk-flags.json")

FUZZY_THRESHOLD = 88   # rapidfuzz WRatio score — below this = no match


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

_SUFFIXES = {' jr.', ' jr', ' sr.', ' sr', ' ii', ' iii', ' iv', ' v'}

def normalize_name(name: str) -> str:
    if not name:
        return ''
    name = unicodedata.normalize('NFKD', str(name))
    name = ''.join(c for c in name if not unicodedata.combining(c))
    name = name.lower().strip()
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
    name = re.sub(r"['\.\,]", '', name)
    return name.strip()


# ---------------------------------------------------------------------------
# Load pybaseball lookup table (SFBB Chadwick register)
# ---------------------------------------------------------------------------

def load_lookup_table() -> pd.DataFrame:
    print("Loading pybaseball lookup table...")
    from pybaseball.playerid_lookup import get_lookup_table
    df = get_lookup_table(save=True)

    df = df.rename(columns={
        'name_last':      'last',
        'name_first':     'first',
        'key_mlbam':      'mlb_id',
        'key_fangraphs':  'fg_id',
        'key_bbref':      'bbref_id',
    })

    df['last']  = df['last'].fillna('').astype(str)
    df['first'] = df['first'].fillna('').astype(str)
    df['mlb_id'] = pd.to_numeric(df['mlb_id'], errors='coerce')

    # Only keep players with a valid MLB AM ID (active/historical MLB players)
    df = df[df['mlb_id'].notna() & (df['mlb_id'] > 0)].copy()
    df['mlb_id'] = df['mlb_id'].astype(int)

    # Build normalized name keys for lookup (both orderings)
    df['norm_fl'] = (df['first'] + ' ' + df['last']).apply(normalize_name)   # "first last"
    df['norm_lf'] = (df['last'] + ' ' + df['first']).apply(normalize_name)   # "last first"

    print(f"  Lookup table: {len(df):,} players with valid MLB AM IDs")
    return df.reset_index(drop=True)


def build_name_indexes(df: pd.DataFrame) -> tuple[dict, dict]:
    """Build fast-lookup dicts: normalized_name → list of row dicts."""
    fl_idx: dict[str, list] = {}
    lf_idx: dict[str, list] = {}
    for row in df.to_dict('records'):
        fl_idx.setdefault(row['norm_fl'], []).append(row)
        lf_idx.setdefault(row['norm_lf'], []).append(row)
    return fl_idx, lf_idx


# ---------------------------------------------------------------------------
# Load ESPN players
# ---------------------------------------------------------------------------

def load_espn_players() -> list[dict]:
    """Return all ESPN players: all 8 rosters + FA pool."""
    print("Connecting to ESPN API...")
    from espn_api.baseball import League
    league = League(
        league_id=LEAGUE_ID,
        year=SEASON,
        espn_s2=ESPN_S2,
        swid=ESPN_SWID,
    )

    players: list[dict] = []

    # Rostered players (all 8 teams)
    team_count = 0
    for team in league.teams:
        team_count += 1
        for player in team.roster:
            players.append(_player_to_dict(player, espn_team_id=team.team_id))

    if team_count == 0:
        raise RuntimeError("ESPN returned 0 teams — auth failure. Check ESPN cookies.")
    if team_count < 8:
        print(f"  WARNING: Only {team_count} teams returned (expected 8)")

    print(f"  Rostered players: {len(players)} across {team_count} teams")

    # FA pool — grab a large batch for crosswalk completeness
    try:
        fa_list = league.free_agents(size=300)
        before = len(players)
        for player in fa_list:
            players.append(_player_to_dict(player, espn_team_id=None))
        print(f"  FA pool: {len(players) - before} players")
    except Exception as e:
        print(f"  WARNING: FA pool fetch failed: {e} — proceeding without FA")

    # Deduplicate by player_id (keep first occurrence = rostered version if dupe)
    seen: set[str] = set()
    unique: list[dict] = []
    for p in players:
        if p['player_id'] not in seen:
            seen.add(p['player_id'])
            unique.append(p)

    print(f"  Total unique ESPN players: {len(unique)}")
    return unique


def _player_to_dict(player, espn_team_id) -> dict:
    pro_team_id = getattr(player, 'proTeamId', None)
    pro_team = ESPN_PRO_TEAM_MAP.get(pro_team_id, '') if pro_team_id is not None else ''
    return {
        'player_id':       str(player.playerId),
        'name':            player.name,
        'position':        getattr(player, 'position', ''),
        'mlb_team':        pro_team,
        'espn_team_id':    espn_team_id,
        'lineup_slot':     getattr(player, 'lineupSlot', ''),
        'injury_status':   getattr(player, 'injuryStatus', ''),
        'acquisition_type': getattr(player, 'acquisitionType', ''),
        'is_rostered':     espn_team_id is not None,
    }


# ---------------------------------------------------------------------------
# Name matching engine
# ---------------------------------------------------------------------------

def match_player(
    espn: dict,
    fl_idx: dict,
    lf_idx: dict,
    all_fl_names: list[str],
    overrides: dict,
) -> tuple[dict | None, str, str]:
    """
    Match one ESPN player to a pybaseball row.
    Returns (row_or_None, status, note).
    status: 'matched' | 'flagged' | 'unmatched'
    """
    pid = espn['player_id']

    # Manual override takes priority
    if pid in overrides:
        ov = overrides[pid]
        return {'mlb_id': ov['mlb_id'], 'fg_id': ov.get('fg_id'), 'bbref_id': ov.get('bbref_id')}, \
               'manual', ov.get('note', 'manual override')

    norm = normalize_name(espn['name'])

    # 1 — exact "first last" match
    result, status, note = _exact_match(norm, fl_idx)
    if result is not None or status == 'flagged':
        return result, status, note

    # 2 — exact "last first" match (rare but happens with some ESPN names)
    result, status, note = _exact_match(norm, lf_idx)
    if result is not None or status == 'flagged':
        return result, status, note

    # 3 — fuzzy match against "first last" index
    hit = process.extractOne(norm, all_fl_names, scorer=fuzz.WRatio, score_cutoff=FUZZY_THRESHOLD)
    if hit:
        best_name, score, _ = hit
        candidates = fl_idx[best_name]
        if len(candidates) == 1:
            return candidates[0], 'matched', f"fuzzy:{score:.0f}"
        else:
            return None, 'flagged', (
                f"Fuzzy match '{best_name}' (score={score:.0f}) is ambiguous "
                f"— {len(candidates)} players. Add to crosswalk-overrides.json."
            )

    return None, 'unmatched', f"No match found for '{espn['name']}' (norm: '{norm}')"


def _exact_match(norm: str, idx: dict) -> tuple[dict | None, str, str]:
    if norm not in idx:
        return None, 'unmatched', ''
    candidates = idx[norm]
    if len(candidates) == 1:
        return candidates[0], 'matched', 'exact'
    # Multiple exact matches (e.g., two players named Max Muncy)
    return None, 'flagged', (
        f"Exact match ambiguous — {len(candidates)} players share the normalized name '{norm}'. "
        f"Add to crosswalk-overrides.json with the correct mlb_id."
    )


# ---------------------------------------------------------------------------
# Write results to DB
# ---------------------------------------------------------------------------

def write_dim_players(matches: list[dict]) -> None:
    conn = sqlite3.connect(DB_PATH)
    now  = datetime.now(timezone.utc).isoformat()

    conn.execute("DELETE FROM dim_players")   # full refresh

    conn.executemany(
        """
        INSERT INTO dim_players
            (player_id, mlb_id, fg_id, bbref_id, name, position, mlb_team,
             crosswalk_status, crosswalk_note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                m['player_id'],
                m.get('mlb_id'),
                str(m.get('fg_id') or ''),
                str(m.get('bbref_id') or ''),
                m['name'],
                m.get('position', ''),
                m.get('mlb_team', ''),
                m['status'],
                m.get('note', ''),
                now,
                now,
            )
            for m in matches
        ],
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(force_refresh: bool = False) -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)

    # Load overrides
    overrides: dict = {}
    if os.path.exists(OVERRIDES_PATH):
        with open(OVERRIDES_PATH) as f:
            overrides = json.load(f)
        print(f"Loaded {len(overrides)} manual override(s) from {OVERRIDES_PATH}")

    # Load lookup table + build indexes
    lookup_df = load_lookup_table()
    fl_idx, lf_idx = build_name_indexes(lookup_df)
    all_fl_names   = list(fl_idx.keys())

    # Load ESPN players
    espn_players = load_espn_players()

    # Match each player
    matches: list[dict] = []
    flags:   list[dict] = []

    for espn in espn_players:
        row, status, note = match_player(espn, fl_idx, lf_idx, all_fl_names, overrides)
        entry = {**espn, 'status': status, 'note': note}
        if row:
            entry['mlb_id']   = row.get('mlb_id')
            entry['fg_id']    = row.get('fg_id')
            entry['bbref_id'] = row.get('bbref_id')
        else:
            entry['mlb_id']   = None
            entry['fg_id']    = None
            entry['bbref_id'] = None

        matches.append(entry)
        if status in ('flagged', 'unmatched'):
            flags.append({
                'player_id': espn['player_id'],
                'name':      espn['name'],
                'team':      espn['mlb_team'],
                'rostered':  espn['is_rostered'],
                'status':    status,
                'note':      note,
            })

    # Write to DB
    write_dim_players(matches)

    # Save flags file for manual review
    with open(FLAGS_PATH, 'w') as f:
        json.dump(flags, f, indent=2)

    # Summary
    by_status: dict[str, int] = {}
    for m in matches:
        by_status[m['status']] = by_status.get(m['status'], 0) + 1

    rostered_unmatched = [
        f for f in flags if f['rostered'] and f['status'] in ('flagged', 'unmatched')
    ]

    print(f"\nCrosswalk complete:")
    for status, count in sorted(by_status.items()):
        print(f"  {status:10s}: {count}")
    print(f"  Flags written to: {FLAGS_PATH}")

    if rostered_unmatched:
        print(f"\n  *** {len(rostered_unmatched)} ROSTERED players could not be matched ***")
        for p in rostered_unmatched:
            print(f"      {p['name']} ({p['team']}) — {p['note']}")
        print(f"  Add these to {OVERRIDES_PATH} with the correct mlb_id.")
        if len(rostered_unmatched) > 3:
            print(f"  WARN: > 3 unmatched rostered players — validate.py will FAIL.")
    else:
        print("  All rostered players matched successfully.")

    return {
        'total':              len(matches),
        'by_status':          by_status,
        'rostered_unmatched': len(rostered_unmatched),
        'flags_path':         FLAGS_PATH,
    }


if __name__ == '__main__':
    result = run()
    if result['rostered_unmatched'] > 0:
        sys.exit(1)
