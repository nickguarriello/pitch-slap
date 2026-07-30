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

import itertools
import json
import os
import sqlite3
import sys

from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DB_PATH, DATA_DIR, TEAM_ID, ROSTER_SLOTS, SEASON_START
from pipeline.evaluate import compute_need_weights

# --- parameters (see design spec §2/§4) -----------------------------------
WEEKS = 11.5                                                   # season weeks elapsed
SCALE = {'R': 32, 'HR': 11, 'RBI': 32, 'SB': 8, 'K': 45, 'QS': 5, 'SvHd': 5}
RATE_BASE = {'OBP': 0.345, 'ERA': 3.90, 'WHIP': 1.25}          # league rate baselines
IP_WEEK = 55.0          # team weekly IP scale — rate impact is weighted by volume share
PA_WEEK = 230.0         # team weekly PA scale
# One "unit" of team-rate swing. Calibrated 2026-06-29: the prior values
# (ERA 0.20 / WHIP 0.04 / OBP 0.010) let a single-trade rate swing dwarf the
# counting cats — e.g. Muncy->Burns scored ERA+WHIP 0.30 vs only 0.08 from all
# counting cats combined, inflating totals past 1.0. Widened ~2.5x so a realistic
# one-trade team-rate swing contributes on the same order as a counting-cat gain.
RATE_MARGIN = {'OBP': 0.025, 'ERA': 0.50, 'WHIP': 0.10}
MY_MIN_SCORE = 0.03     # only surface trades that meaningfully help me
ACCEPT_TOL = -0.02      # other team must net ~>= 0 (small tolerance) to plausibly accept
FAIR_TOL = 0.15         # max raw-talent edge I may receive (blocks lopsided "fleece" offers)
TOP_N = 5               # ranked trades kept per target team
NEUTRAL_NEED = {c: 0.5 for c in ('R', 'HR', 'RBI', 'SB', 'OBP', 'K', 'QS', 'ERA', 'WHIP', 'SvHd')}
PROTECTED_PLAYERS: set[str] = set()   # players I will never offer (by name); never appear on the give side

# --- rest-of-season projection blend (design §7) ---------------------------
# Player value blends actual season production with ESPN's full-season
# projection, scaled to an "expected-to-date" total so it is directly comparable
# to the actual season total. This regresses over/under-performers toward their
# talent and stops the model under-pricing pedigree (a projected ace with few
# games logged). A player exactly on his projection is unchanged (expected ==
# actual), so the existing score thresholds stay valid. Missing projection ->
# fall back to actual only.
SEASON_WEEKS = 26.0                                            # full MLB season length
_elapsed_weeks = max(0.1, (date.today() - date.fromisoformat(SEASON_START)).days / 7.0)
SEASON_FRACTION = min(1.0, _elapsed_weeks / SEASON_WEEKS)      # share of season elapsed
PROJ_BLEND = 0.5                                               # weight on projection vs actual
PROJ_COL = {'R': 'espn_proj_r', 'HR': 'espn_proj_hr', 'RBI': 'espn_proj_rbi',
            'SB': 'espn_proj_sb', 'OBP': 'espn_proj_obp', 'K': 'espn_proj_k',
            'QS': 'espn_proj_qs', 'ERA': 'espn_proj_era', 'WHIP': 'espn_proj_whip',
            'SvHd': 'espn_proj_svhd'}

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
        """SELECT r.player_id, p.name, p.position, r.eligible_slots, r.is_il,
                  r.espn_proj_r, r.espn_proj_hr, r.espn_proj_rbi, r.espn_proj_sb,
                  r.espn_proj_obp, r.espn_proj_k, r.espn_proj_qs, r.espn_proj_era,
                  r.espn_proj_whip, r.espn_proj_svhd
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


def load_rostered(db_path: str = DB_PATH, snapshot: str = None) -> dict:
    """{name: player_dict} for every rostered player (any team) — the trade-piece pool."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if snapshot is None:
        snapshot = conn.execute("SELECT MAX(snapshot_date) FROM fact_espn_rosters").fetchone()[0]
    rows = conn.execute(
        """SELECT r.player_id, p.name, p.position, r.eligible_slots, r.is_il, r.espn_team_id,
                  r.espn_proj_r, r.espn_proj_hr, r.espn_proj_rbi, r.espn_proj_sb,
                  r.espn_proj_obp, r.espn_proj_k, r.espn_proj_qs, r.espn_proj_era,
                  r.espn_proj_whip, r.espn_proj_svhd
           FROM fact_espn_rosters r JOIN dim_players p ON p.player_id = r.player_id
           WHERE r.espn_team_id IS NOT NULL AND r.snapshot_date = ?""",
        (snapshot,),
    ).fetchall()
    out = {}
    for r in rows:
        s = conn.execute(
            "SELECT * FROM fact_player_stats WHERE player_id=? AND window='season' "
            "ORDER BY stat_date DESC LIMIT 1", (r['player_id'],)
        ).fetchone()
        out[r['name']] = _make_player(r, s)
    conn.close()
    return out


def _make_player(r, s) -> dict:
    elig = set((r['eligible_slots'] or '').split(',')) - {''}
    s = s or {}
    g = lambda k: (s[k] if s and s[k] is not None else 0.0)
    is_pit = 'P' in elig

    def _proj(cat):
        """ESPN full-season projected total for `cat`, or None if not projected."""
        try:
            v = r[PROJ_COL[cat]]
        except (IndexError, KeyError):
            return None
        return None if v is None else float(v)

    def _blend_count(cat, col):
        """Blend actual season total with the projection's expected-to-date total,
        then normalize to the per-week pace. Falls back to actual if unprojected."""
        actual_total = g(col)
        pt = _proj(cat)
        total = actual_total if pt is None else \
            PROJ_BLEND * (pt * SEASON_FRACTION) + (1 - PROJ_BLEND) * actual_total
        return total / WEEKS

    def _blend_rate(actual, cat):
        """Rates aren't cumulative — blend the rate value directly (regress toward
        the projected rate). Falls back to actual if unprojected."""
        pv = _proj(cat)
        if pv is None or pv == 0:
            return actual
        return PROJ_BLEND * pv + (1 - PROJ_BLEND) * actual

    return {
        'player_id': r['player_id'], 'name': r['name'], 'position': r['position'],
        'eligible': elig, 'is_il': bool(r['is_il']), 'is_pitcher': is_pit,
        # per-week counting contributions + rate stats with their weighting denom
        'wk': {cat: _blend_count(cat, col)
               for cat, col in (PIT_COUNT if is_pit else HIT_COUNT).items()},
        'obp': _blend_rate(g('obp'), 'OBP'), 'pa': g('pa'),
        'era': _blend_rate(g('era'), 'ERA'), 'whip': _blend_rate(g('whip'), 'WHIP'), 'ip': g('ip'),
    }


# --- player value (objective the optimizer maximizes) ----------------------

def player_value(p: dict, need: dict) -> float:
    """Need-weighted weekly value of a single player, in comparable category-units.

    Counting cats: per-week pace / team-weekly scale.
    Rate cats: signed deviation from baseline, *scaled by the player's volume share*
    (weekly IP/PA over team-weekly volume) so a low-inning reliever can't swing a rate
    category like a full starter — keeps rate and counting terms the same magnitude.
    """
    v = 0.0
    if p['is_pitcher']:
        for cat in PIT_COUNT:
            v += need[cat] * (p['wk'][cat] / SCALE[cat])
        if p['ip'] > 0:                       # rate cats: reward lower ERA/WHIP, volume-weighted
            ip_share = (p['ip'] / WEEKS) / IP_WEEK
            v += need['ERA'] * (RATE_BASE['ERA'] - p['era']) / RATE_BASE['ERA'] * ip_share
            v += need['WHIP'] * (RATE_BASE['WHIP'] - p['whip']) / RATE_BASE['WHIP'] * ip_share
    else:
        for cat in HIT_COUNT:
            v += need[cat] * (p['wk'][cat] / SCALE[cat])
        if p['pa'] > 0:                       # OBP: reward higher, volume-weighted
            pa_share = (p['pa'] / WEEKS) / PA_WEEK
            v += need['OBP'] * (p['obp'] - RATE_BASE['OBP']) / RATE_BASE['OBP'] * pa_share
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


def score_trade(my_players: list[dict], incoming: list[dict], outgoing_names: set,
                need: dict) -> dict:
    """Need-weighted category swing from re-optimizing the active lineup before vs after.

    Positive score = the trade improves my need-weighted active production.
    (My-side value only — the acceptance gate using the other team's need vector is
    a separate step, pending per-team need-weights; see design §7.)
    """
    before = optimize_lineup(my_players, need)
    after_roster = [p for p in my_players if p['name'] not in outgoing_names] + incoming
    after = optimize_lineup(after_roster, need)

    delta = {c: round(after['production'][c] - before['production'][c], 2)
             for c in before['production']}
    contrib, total = {}, 0.0
    for c in ('R', 'HR', 'RBI', 'SB', 'K', 'QS', 'SvHd'):          # counting cats
        u = need[c] * ((delta[c] / WEEKS) / SCALE[c])
        contrib[c] = round(u, 3); total += u
    for c, lower_better in (('OBP', False), ('ERA', True), ('WHIP', True)):  # rate cats
        signed = (-delta[c] if lower_better else delta[c]) / RATE_MARGIN[c]
        u = need[c] * signed
        contrib[c] = round(u, 3); total += u
    return {
        'score': round(total, 3),
        'drivers': {k: v for k, v in contrib.items() if abs(v) >= 0.005},
        'delta': delta,
    }


def _team_names(db_path: str = DB_PATH) -> dict:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'espn_teams.json')
    try:
        teams = json.load(open(path))['teams']
        return {int(k): v['name'].strip() for k, v in teams.items()}
    except Exception:
        return {}


def _active(players):
    return [p for p in players if not p['is_il']]


def _evaluate_package(my_roster, my_need, their_roster, their_need,
                      gives, gets, pool):
    """Score one package from both sides; return dict or None if it fails the gates."""
    give_names = {p['name'] for p in gives}
    get_names = {p['name'] for p in gets}
    mine = score_trade(my_roster, gets, give_names, my_need)
    if mine['score'] < MY_MIN_SCORE:
        return None
    theirs = score_trade(their_roster, gives, get_names, their_need)
    if theirs['score'] < ACCEPT_TOL:
        return None
    # Talent-balance gate: block deals where I receive far more raw (need-neutral)
    # talent than I give — those are category-rational but no manager would accept.
    talent_edge = (sum(player_value(p, NEUTRAL_NEED) for p in gets)
                   - sum(player_value(p, NEUTRAL_NEED) for p in gives))
    if talent_edge > FAIR_TOL:
        return None
    return {
        'give': sorted(give_names), 'get': sorted(get_names),
        'my_score': mine['score'], 'their_score': theirs['score'],
        'drivers': mine['drivers'], 'delta': mine['delta'],
    }


def enumerate_trades(my_team_id: int, target_team_id: int, pool: dict,
                     my_roster=None, my_need=None, protected: set = None) -> list[dict]:
    """Generate, score, and acceptance-gate candidate packages with one target team.

    1-for-1 over both active rosters, then 2-for-2 by extending the top anchors
    (bounded so it stays fast). Returns accepted trades ranked by my_score.
    `protected` players (default PROTECTED_PLAYERS) are never offered on the give side.
    """
    if protected is None:
        protected = PROTECTED_PLAYERS
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if my_roster is None:
        my_roster = load_team(my_team_id)
    if my_need is None:
        my_need = compute_need_weights(conn, my_team_id)
    their_roster = load_team(target_team_id)
    their_need = compute_need_weights(conn, target_team_id)
    conn.close()

    mine_pool = [p for p in _active(my_roster) if p['name'] not in protected]
    theirs_pool = _active(their_roster)

    accepted = []
    # 1-for-1
    for g in mine_pool:
        for t in theirs_pool:
            r = _evaluate_package(my_roster, my_need, their_roster, their_need, [g], [t], pool)
            if r:
                accepted.append(r)

    # 2-for-2: extend the top 1-for-1 anchors with my cheapest give + their best-for-me get
    anchors = sorted(accepted, key=lambda r: -r['my_score'])[:3]
    give2 = sorted(mine_pool, key=lambda p: player_value(p, my_need))[:6]      # my cheapest
    get2 = sorted(theirs_pool, key=lambda p: -player_value(p, my_need))[:6]    # most valuable to me
    seen = set()
    for a in anchors:
        g1 = next(p for p in mine_pool if p['name'] == a['give'][0])
        t1 = next(p for p in theirs_pool if p['name'] == a['get'][0])
        for g2 in give2:
            for t2 in get2:
                names = (g1['name'], g2['name'], t1['name'], t2['name'])
                if len({g1['name'], g2['name']}) < 2 or len({t1['name'], t2['name']}) < 2:
                    continue
                key = (frozenset((g1['name'], g2['name'])), frozenset((t1['name'], t2['name'])))
                if key in seen:
                    continue
                seen.add(key)
                r = _evaluate_package(my_roster, my_need, their_roster, their_need,
                                      [g1, g2], [t1, t2], pool)
                if r:
                    accepted.append(r)

    # de-dup identical give/get sets, keep best, rank
    best = {}
    for r in accepted:
        key = (tuple(r['give']), tuple(r['get']))
        if key not in best or r['my_score'] > best[key]['my_score']:
            best[key] = r
    ranked = sorted(best.values(), key=lambda r: -r['my_score'])
    return ranked[:TOP_N]


def run(my_team_id: int = TEAM_ID, target_team_ids: list = None) -> dict:
    """Pipeline entry: build ranked, acceptance-gated trade proposals → docs/data/trades.json."""
    names = _team_names()
    pool = load_rostered()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    my_need = compute_need_weights(conn, my_team_id)
    snapshot = conn.execute("SELECT MAX(snapshot_date) FROM fact_espn_rosters").fetchone()[0]
    conn.close()
    if target_team_ids is None:
        target_team_ids = [tid for tid in names if tid != my_team_id]
    my_roster = load_team(my_team_id)

    out_teams = []
    for tid in target_team_ids:
        trades = enumerate_trades(my_team_id, tid, pool, my_roster, my_need)
        _c = sqlite3.connect(DB_PATH); _c.row_factory = sqlite3.Row
        tneed = compute_need_weights(_c, tid); _c.close()
        out_teams.append({
            'team_id': tid,
            'team_name': names.get(tid, str(tid)),
            'their_top_needs': sorted(tneed, key=lambda c: -tneed[c])[:3],
            'trades': trades,
        })
    out_teams.sort(key=lambda t: -(t['trades'][0]['my_score'] if t['trades'] else 0))

    result = {
        'generated_for_snapshot': snapshot,
        'my_team_id': my_team_id,
        'my_team_name': names.get(my_team_id, str(my_team_id)),
        'my_need_weights': my_need,
        'teams': out_teams,
    }
    out_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            'docs', 'data', 'trades.json')
    with open(out_path, 'w') as f:
        json.dump(result, f, indent=2)
    total = sum(len(t['trades']) for t in out_teams)
    print(f"trade.py: wrote {total} ranked trades across {len(out_teams)} teams -> {out_path}")
    return result


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

    # --- trade-scorer demo (validation) ---
    pool = load_rostered()
    demos = [
        ("Jhoan Duran -> Davis Martin (surplus RP for QS starter)", ["Davis Martin"], {"Jhoan Duran"}),
        ("Max Muncy -> Chase Burns (redundant bat for ace)",        ["Chase Burns"],  {"Max Muncy"}),
    ]
    print("\n=== trade scorer ===")
    for label, inc, out in demos:
        incoming = [pool[n] for n in inc if n in pool]
        if len(incoming) != len(inc):
            print(f"  [skip] {label} — missing: {[n for n in inc if n not in pool]}")
            continue
        r = score_trade(roster, incoming, out, need)
        print(f"  {label}\n    score {r['score']:+.3f}  drivers={r['drivers']}")
