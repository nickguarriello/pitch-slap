# Trade Analyzer — Design Spec

**Status:** design / prototype (logic validated by hand on 2026-06-15/16, not yet implemented in pipeline)
**Owner area:** this is the "Trade helper UI" listed as the next priority in PLANNING.md.
**Goal:** given my roster + every other roster, surface trade packages that maximize my
**need-weighted category gain** while remaining **acceptable to the other manager**.

> Scope note: this doc intentionally contains **no specific player trade recommendations** —
> only the reusable logic. Recommendations are generated at run time, not stored.

---

## 1. Inputs / data sources (all already produced by the pipeline)

| Input | Source | Notes |
|---|---|---|
| `NEED[cat]` (need-weights) | `docs/data/matchup.json` → `need_weights` (from `evaluate.py`) | live, recomputed each run |
| Player production (per window) | `fact_player_stats` (windows: season / first_half / 30d / 14d / 7d) | counting + rate cats |
| ESPN projections | `fact_espn_rosters.espn_proj_*` | **now populated** (fixed 2026-06-15, see PLANNING) |
| Roster membership / team / slot | `fact_espn_rosters` (latest `snapshot_date`) + `dim_players` | maps players↔teams |
| Team standings / records | `data/espn_teams.json` | used to infer other teams' needs |
| Categories | `config.py` HITTING_CATS / PITCHING_CATS | R,HR,RBI,SB,OBP / K,QS,ERA,WHIP,SvHd |

---

## 2. Core scoring function

```
TradeScore(side) = Σ_cat  NEED[cat] × ( Σ_incoming contrib(p,cat) − Σ_outgoing contrib(p,cat) )

contrib(p,cat) = (window_total[cat] / weeks_elapsed) / category_scale[cat]
                 # player's weekly output, normalized into comparable "category-units"
```

- **Counting cats** (R,HR,RBI,SB,K,QS,SvHd): use the per-week pace as above.
- **Rate cats** (OBP,ERA,WHIP): handle separately as a deviation from a league baseline
  (`RATE[cat]`), scaled small — a single added/removed player barely moves a team rate.
- `TradeScore` is **dimensionless** so categories with very different magnitudes
  (K ≈ 7/wk vs QS ≈ 0.8/wk) are comparable.

### Reference implementation (validated 2026-06-15)
```python
NEED  = json.load(open('docs/data/matchup.json'))['need_weights']
WEEKS = 11.5                                  # PARAM: season weeks elapsed
SCALE = {'R':32,'HR':11,'RBI':32,'SB':8,'K':45,'QS':5,'SvHd':5}   # PARAM: weekly team-level scale
RATE  = {'OBP':0.345,'ERA':3.90,'WHIP':1.25}                      # PARAM: league rate baselines
COUNT = ['R','HR','RBI','SB','K','QS','SvHd']

def contrib(p):
    return {c:(p.get(c,0)/WEEKS)/SCALE[c] for c in COUNT}

def score(incoming, outgoing):
    total=0.0; breakdown={}
    for c in COUNT:
        d = sum(contrib(p)[c] for p in incoming) - sum(contrib(p)[c] for p in outgoing)
        breakdown[c] = NEED[c]*d; total += NEED[c]*d
    return total, breakdown
```

---

## 3. Pipeline (6 steps)

1. **Need pass** — load `NEED[cat]`. Rank cats; highest = targets, lowest = surplus.
2. **Surplus pass** — your cheap currency = players whose value concentrates in *low-need*
   cats + positional depth (roster-slot oversupply). These are the preferred "give" pool.
3. **Target pass** — other rosters' players strong in your *high-need* cats that are *also*
   that team's surplus (so a deal is mutually motivated).
4. **Enumerate** candidate packages, respecting: protected list, roster legality
   (`ROSTER_SLOTS` in config), max players/side, weekly acquisition + 15-IP rules.
5. **Score** each package with `TradeScore` (mine). Compute the **mirror score using the
   other team's need vector** as an acceptance gate.
6. **Rank** by my score; filter to acceptance ≥ 0 (and constraints). Optionally report
   **efficiency = my_gain / cost_given**.

---

## 4. Parameters (what makes it reusable, not one-off)

`NEED` (live) · `weeks_elapsed` · `category_scale[cat]` · `RATE[cat]` baselines ·
`protected_players` (never offer) · `injury_discount` (haircut IL/DTD targets) ·
`max_players_per_side` · `value_window_blend` (how to mix season / projection / recent /
playoff-window when computing `contrib`).

---

## 5. Principles the logic surfaces (validated, generalizable)

- **Spend from your lowest-need surplus.** The K/QS *gain* from acquiring a starter is
  about the same whether you pay with a reliever or a bat — but the **cost** differs:
  a surplus reliever debits only SvHd (×low need), a bat debits R/HR/RBI/SB (×mid need).
  The function therefore prefers surplus-RP-for-SP when a save-needy partner exists.
- **Acceptance gate is mandatory.** A package that scores great for me but negative for
  them is not proposable — score both sides.
- **Need-weights move week to week.** As of 2026-06-15: OBP .70 (top overall), K .61 / QS .61
  (top pitching), RBI .53, HR .49, R .44 / SB .44, ERA .44, WHIP .40, **SvHd .10 (saturated)**.
  Do not hardcode — always read live.

---

## 6. OPEN — highest-priority v2 refinement: replacement-level marginal value

**Problem:** v1 debits an outgoing player's *full* stat line. But a surplus bat (e.g. your
8th–9th hitter) mostly rides the bench — his real contribution to your weekly category
totals is only **(player − bench/replacement level)**, which is small.

**Fix:** score outgoing players at **marginal value over replacement**, not raw totals:
`contrib_out(p,cat) = max(0, contrib(p,cat) − replacement_level[cat,slot])`.

**Effect:** bat-for-arm packages (giving a redundant hitter for an ace) currently grade
neutral/negative purely because of phantom hitting "loss." With replacement-level applied,
the K/QS gain stays while that loss shrinks → ace-for-surplus-bat deals re-rank upward.
This is the single biggest accuracy upgrade. Needs a `replacement_level` estimate per
cat × roster slot (e.g., median of freely-available FAs at that slot, or your own bench).

---

## 7. Other open design questions

- **Value window blend** — weight season vs 30d/14d vs ESPN projection vs playoff-window
  (playoffs start week 22). Recommend a configurable blend; default lean on projection +
  recent for ROS value.
- **Inferring opponent need-weights** — `evaluate.py` computes `need_weights` for *my* team
  only. To run the acceptance gate we need the same computation per team (generalize
  `evaluate.py` to accept a team_id, or approximate from standings/roster).
- **Multi-cat rate handling** — finalize the OBP/ERA/WHIP rate-cat term (currently
  approximate; low-need right now so low impact, but needed for correctness).
- **Output surface** — likely a `trades.html` dashboard page + a `trades.json` writer in
  `report.py`, fed by a new `pipeline/trade.py` (or a section in `evaluate.py`). Respect
  CLAUDE.md: new file is fine; touching evaluate/report logic needs the usual agreement.

---

## 8. How to pick this up next session

1. Re-read this spec + the 2026-06-16 PLANNING.md row.
2. Decide v2 replacement-level approach (§6) — that unblocks accurate bat-for-arm scoring.
3. Generalize `evaluate.py` need-weights to per-team (§7) for the acceptance gate.
4. Implement `pipeline/trade.py` with the §2 function + §3 pipeline; write `trades.json`.
5. Build `trades.html`. Validate against a hand-scored example before trusting output.
