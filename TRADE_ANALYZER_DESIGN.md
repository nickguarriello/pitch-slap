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

> `contrib`/`score` above are the **normalization primitives** only. The decided scoring
> model (§6) feeds incoming/outgoing through `optimize_lineup` first, then scores the
> before/after **active-lineup** delta — not raw totals. This `score()` is the v1 baseline.

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

## 6. DECIDED (2026-06-16): marginal value = **lineup-aware** (model v3)

**Problem the model must solve:** v1 debits an outgoing player's *full* stat line, but a
surplus bat (your 8th–9th hitter) mostly rides the bench — trading him changes your actual
weekly totals very little. The score must reflect change in **active-lineup** output, not
raw totals.

**Decision:** the TradeScore measures the change in **expected weekly ACTIVE-lineup
production**, computed by re-optimizing the lineup before and after the trade.
(Chosen over the simpler VORP/replacement-level model — VORP is a good ~90% approximation,
but the lineup-aware model is exact and avoids replacement-baseline guesswork.)

```
before = optimize_lineup(roster)
after  = optimize_lineup(roster − outgoing + incoming)
Δ[cat] = after[cat] − before[cat]
TradeScore = Σ_cat  NEED[cat] × normalize(Δ[cat])
```

**Why it's correct by construction:**
- A benched surplus player isn't in the optimal lineup before *or* after → his removal
  moves the score ~0. The phantom-loss problem disappears with no special-casing.
- An incoming player is credited only for what he *adds to the active lineup*, net of
  whomever he bumps to the bench (Δ = incoming − displaced player).
- Slot scarcity (only N OF, 10 P, etc.) is enforced, so you can't "count" production that
  would never reach your lineup.

**`optimize_lineup(roster)` — the new component to build:**
- Assign rostered players to active slots per `ROSTER_SLOTS` (config.py), respecting ESPN
  slot eligibility, to **maximize need-weighted production**; return the active per-cat vector.
- **Hitters:** weighted bipartite assignment (player→slot) with eligibility. Greedy-by-value
  is a fine first cut; upgrade to Hungarian/LP if greedy mis-assigns multi-eligible players.
- **Pitchers (10 P slots, ~12–13 arms):** K/QS/SvHd are additive counting cats; **ERA/WHIP
  are IP-weighted rates → nonlinear in the chosen set.** Score each arm by need-weighted
  value and fill the 10 slots, IP-weighting the rate cats over the chosen set. In practice
  only the weakest 1–2 arms sit, so the marginal effect of an acquisition is "bump the
  weakest active arm."
- **Cadence:** lineup locks weekly (Monday) → use per-week pace; optionally fold in
  two-start weeks for QS/K (already detected in `fetch_mlb`). Keep the optimizer
  deterministic and fast — it runs once per candidate package.

**Build order / safety net:** implement VORP (production over best-available-wire at slot)
as a quick **sanity baseline** to validate the optimizer's outputs against — but v3
(lineup-aware) is the scoring model of record.

**Possible v4 (future, only if needed):** probabilistic lineup (injury/role uncertainty),
opponent-adjusted swing (score Δ against the specific week's opponent, not league-average).

---

## 7. Other open design questions

- **Value window blend** — weight season vs 30d/14d vs ESPN projection vs playoff-window
  (playoffs start week 22). Recommend a configurable blend; default lean on projection +
  recent for ROS value.
- **Inferring opponent need-weights** — `evaluate.py` computes `need_weights` for *my* team
  only. To run the acceptance gate we need the same computation per team (generalize
  `evaluate.py` to accept a team_id, or approximate from standings/roster).
- **Multi-cat rate handling / value calibration** — finalize the OBP/ERA/WHIP rate-cat
  term. **Observed 2026-06-16:** with the current `player_value`, elite-ratio relievers
  outscore QS-producing starters, so `optimize_lineup` benches a QS source (e.g. Taj
  Bradley) and active QS drops below the full-staff total. The rate term over-weights
  ERA/WHIP vs the counting QS need (.61). **Must calibrate before trusting trade scores**
  (else a QS-starter acquisition may not change the active lineup → invisible to the score).
  Likely fix: normalize the rate term into the same category-units as counting cats and/or
  weight by category competitiveness; consider a minimum-SP floor for the P slots.
- **Output surface** — likely a `trades.html` dashboard page + a `trades.json` writer in
  `report.py`, fed by a new `pipeline/trade.py` (or a section in `evaluate.py`). Respect
  CLAUDE.md: new file is fine; touching evaluate/report logic needs the usual agreement.

---

## 8. How to pick this up next session

1. Re-read this spec + the 2026-06-16 PLANNING.md rows.
2. ✅ **`optimize_lineup(roster)` built** in `pipeline/trade.py` (2026-06-16) — transversal-
   matroid greedy assigns hitters to slots by eligibility (uses `eligible_slots`), top-10 by
   value for P; returns active per-cat production vector. Self-test passes.
   ✅ **`player_value` rate term calibrated** — rate cats now volume-weighted (IP/PA share),
   so QS starters stay active (active QS 38→43). `IP_WEEK`/`PA_WEEK` params added.
3. ✅ **`score_trade(...)` built** — re-optimizes before/after, scores need-weighted Δ.
   Validated: bat-for-ace flipped −0.10 (v1) → +0.33 (v3), confirming the lineup-aware model.
   ⚠️ OPEN calibration: scorer `RATE_MARGIN` (esp. ERA=0.20) over-weights rate swings vs
   counting cats — widen the rate margins / tune against more examples before shipping.
4. **Package enumeration** (§3 steps 2–4) — auto-generate candidate give/get packages from
   surplus→need, respecting protected list + roster legality; rank by `score_trade`.
5. Generalize `evaluate.py` need-weights to **per-team** (§7) → wire the **acceptance gate**
   (mirror score from the other team's need vector). `score_trade` is my-side only today.
6. **Output surface** — `trades.json` writer (in report.py or trade.py) + `trades.html`.
