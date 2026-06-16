"""
pipeline/trade.py — Trade analyzer (v3 lineup-aware).

See TRADE_ANALYZER_DESIGN.md. This module currently implements the core
dependency: optimize_lineup(roster) — assign rostered players to active
ROSTER_SLOTS by ESPN eligibility to maximize need-weighted production, and
return the resulting active per-category production vector. The trade scorer
(later) diffs this vector before vs. after a proposed swap.

Run as a script to sanity-check the optimizer against my current roster:
    python -m pipeline.trade
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DB_PATH, DATA_DIR, TEAM_ID, ROSTER_SLOTS

# --- parameters (see design spec §2/§4) -----------------------------------
WEEKS = 11.5                                                   # season weeks elapsed
SCALE = {'R': 32, 'HR': 11, 'RBI': 32, 'SB': 8, 'K': 45, 'QS': 5, 'SvHd': 5}
RATE_BASE = {'OBP': 0.345, 'ERA': 3.90, 'WHIP': 1.25}          # league rate baselines

HIT_COUNT = {'R': 'r', 'HR': 'hr', 'RBI': 'rbi', 'SB': 'sb'}   # cat -> stats column
PIT_COUNT = {'K': 'k', 'QS': 'qs', 'SvHd': 'svhd'}
ACTIVE_SLOTS = {s: n for s, n in ROSTER_SLOTS.items() if s not in ('BN', 'IL')}

# --- need weights (live from evaluate.py output) ---------------------------

def load_need_weights() -> dict:
    root = os.path.dirname(os.path.dirname(__file__))
    with open(os.path.join(root, 'docs', 'data', 'matchup.json')) as f:
        return json.load(f)['need_weights']


# --- roster loading --------------------------------------------------------

def load_team(team_id: int, db_path: str = DB_PATH, snapshot: str = None) -> list[dict]:
    """One player dict per rostered player on `team_id` (latest snapshot)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if snapshot is None:
        snapshot = conn.execute("SELECT MAX(snapshot_date) FROM fact_espn_rosters").fetchone()[0]
    rows = conn.execute(
        """SELECT r.player_id, p.name, p.position, r.eligible_slots, r.is_il
           FROM fact_espn_rosters r JOIN dim_players p ON p.player_id = r.player_id
           WHERE r.espn_team_id = ? AND r.snapshot_date = ?""",
        (team_id, snapshot),
    ).fetchall()
    players = []
    for r in rows:
        s = conn.execute(
            "SELECT * FROM fact_player_stats WHERE player_id=? AND window='season' "
            "ORDER BY stat_date DESC LIMIT 1", (r['player_id'],)
        ).fetchone()
        players.append(_make_player(r, s))
    conn.close()
    return players


def _make_player(r, s) -> dict:
    elig = set((r['eligible_slots'] or '').split(',')) - {''}
    s = s or {}
    g = lambda k: (s[k] if s and s[k] is not None else 0.0)
    is_pit = 'P' in elig
    return {
        'player_id': r['player_id'], 'name': r['name'], 'position': r['position'],
        'eligible': elig, 'is_il': bool(r['is_il']), 'is_pitcher': is_pit,
        # per-week counting contributions + rate stats with their weighting denom
        'wk': {cat: g(col) / WEEKS for cat, col in (PIT_COUNT if is_pit else HIT_COUNT).items()},
        'obp': g('obp'), 'pa': g('pa'),
        'era': g('era'), 'whip': g('whip'), 'ip': g('ip'),
    }


# --- player value (objective the optimizer maximizes) ----------------------

def player_value(p: dict, need: dict) -> float:
    """Need-weighted, normalized weekly value of a single player."""
    v = 0.0
    if p['is_pitcher']:
        for cat in PIT_COUNT:
            v += need[cat] * (p['wk'][cat] / SCALE[cat])
        if p['ip'] > 0:                       # rate cats: reward lower ERA/WHIP
            v += need['ERA'] * (RATE_BASE['ERA'] - p['era']) / RATE_BASE['ERA']
            v += need['WHIP'] * (RATE_BASE['WHIP'] - p['whip']) / RATE_BASE['WHIP']
    else:
        for cat in HIT_COUNT:
            v += need[cat] * (p['wk'][cat] / SCALE[cat])
        if p['pa'] > 0:                       # OBP: reward higher
            v += need['OBP'] * (p['obp'] - RATE_BASE['OBP']) / RATE_BASE['OBP']
    return v


# --- bipartite matching (max-weight transversal via greedy + augmenting) ---

def _augment(player_idx, seats, edges, seat_match, visited):
    for si in edges[player_idx]:
        if si in visited:
            continue
        visited.add(si)
        if seat_match[si] is None or _augment(seat_match[si], seats, edges, seat_match, visited):
            seat_match[si] = player_idx
            return True
    return False


def _assign_hitters(hitters, need):
    """Assign hitters to hitting seats maximizing total value (transversal matroid greedy)."""
    seats = [s for slot, n in ACTIVE_SLOTS.items() if slot != 'P' for s in [slot] * n]
    order = sorted(range(len(hitters)), key=lambda i: -player_value(hitters[i], need))
    seat_match = [None] * len(seats)            # seat -> hitter index
    chosen = []
    for i in order:
        elig = hitters[i]['eligible']
        edges = {i: [si for si, st in enumerate(seats) if st in elig]}
        # build edges for all currently-chosen players too (needed for augmenting reshuffles)
        for j in chosen:
            edges[j] = [si for si, st in enumerate(seats) if st in hitters[j]['eligible']]
        if _augment(i, seats, edges, seat_match, set()):
            chosen.append(i)
    assignment = {}
    for si, pi in enumerate(seat_match):
        if pi is not None:
            assignment.setdefault(seats[si], []).append(hitters[pi]['name'])
    active = [hitters[pi] for pi in seat_match if pi is not None]
    bench = [hitters[i] for i in range(len(hitters)) if i not in set(seat_match)]
    return assignment, active, bench


def optimize_lineup(players: list[dict], need: dict) -> dict:
    """Return the best legal active lineup and its per-category production vector."""
    pool = [p for p in players if not p['is_il']]
    hitters = [p for p in pool if not p['is_pitcher']]
    pitchers = [p for p in pool if p['is_pitcher']]

    h_assign, h_active, h_bench = _assign_hitters(hitters, need)
    # pitchers: 10 P seats, all P-eligible -> take top-10 by value
    p_sorted = sorted(pitchers, key=lambda p: -player_value(p, need))
    p_active, p_bench = p_sorted[:ACTIVE_SLOTS.get('P', 10)], p_sorted[ACTIVE_SLOTS.get('P', 10):]

    prod = _production(h_active, p_active)
    value = sum(player_value(p, need) for p in h_active + p_active)
    return {
        'hitting_assignment': h_assign,
        'active_pitchers': [p['name'] for p in p_active],
        'bench': [p['name'] for p in h_bench + p_bench],
        'production': prod,
        'value': round(value, 4),
    }


def _production(h_active, p_active) -> dict:
    prod = {}
    for cat in HIT_COUNT:
        prod[cat] = round(sum(p['wk'][cat] for p in h_active) * WEEKS, 1)   # season-equiv total
    for cat in PIT_COUNT:
        prod[cat] = round(sum(p['wk'][cat] for p in p_active) * WEEKS, 1)
    pa = sum(p['pa'] for p in h_active) or 1
    prod['OBP'] = round(sum(p['obp'] * p['pa'] for p in h_active) / pa, 3)
    ip = sum(p['ip'] for p in p_active) or 1
    prod['ERA'] = round(sum(p['era'] * p['ip'] for p in p_active) / ip, 2)
    prod['WHIP'] = round(sum(p['whip'] * p['ip'] for p in p_active) / ip, 2)
    return prod


if __name__ == "__main__":
    need = load_need_weights()
    roster = load_team(TEAM_ID)
    result = optimize_lineup(roster, need)
    print(f"=== Optimized active lineup for team {TEAM_ID} "
          f"({len(roster)} rostered) ===")
    for slot in [s for s in ACTIVE_SLOTS if s != 'P']:
        names = result['hitting_assignment'].get(slot, [])
        print(f"  {slot:5} {', '.join(names)}")
    print(f"  P     {', '.join(result['active_pitchers'])}")
    print(f"  BENCH {', '.join(result['bench'])}")
    print("  active production:", result['production'])
    print("  lineup value:", result['value'])
