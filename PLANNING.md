# PLANNING.md — Pitch Slap Decision Engine
## Running Build Log — Updated Every Session

---

## Project Status: LIVE — Phases 1–6 complete. Full dashboard + pipeline overhaul complete 2026-06-04. Phase 7 (manual verification) ongoing.

---

## What Exists Right Now (Full Build — Live as of 2026-06-04)

- `config.py` — full league config, ESPN mappings, all PRD thresholds
- `pipeline/init_db.py` — 11-table SQLite schema + seed_crosswalk() for CI
- `pipeline/fetch_espn.py` — ESPN roster, matchup, constraints, team names, cat records (556 players)
- `pipeline/fetch_mlb.py` — schedule, transactions, two-start detection
- `pipeline/fetch_statcast.py` — Statcast + FanGraphs (499 rows, 7-day cache)
- `pipeline/transform.py` — 5 windows: season / 30d / 14d / 7d / current (~2500 rows)
- `pipeline/validate.py` — 23 checks (typically 19 pass / 3 warn / 0 fail)
- `pipeline/evaluate.py` — cat states, need weights, buy/sell, 2-start, constraints (IP live from DB), ownership velocity (2-day/5%)
- `pipeline/report.py` — writes docs/data/*.json + CSVs + pipeline-log.json; history states; 7d in roster
- `main.py` — orchestrator (--mode full/light)
- `docs/index.html` — home: real W/L/T, buy-low wire+trade, sell-high, constraints, ? tooltips
- `docs/matchup.html` — cat scores + history; opponent name; 4-decimal rate stats; ? tooltips
- `docs/waivers.html` — two-starters, buy-low FA, ownership velocity; ? tooltips
- `docs/players.html` — ESPN slot-order layout (C/C/1B/2B/3B/MI/CI/OF×5/UTIL×2 → active P SP-first → bench P → bench H → IL P → IL H); `BE` bench-slot fix; 5 window buttons (Current Matchup/7D/14D/30D/Season); ? tooltips on each section
- `docs/league.html` — heat map rank colors; Record + Roto Record columns; sort toggle; ? tooltip
- `docs/playoff.html` — standings (Roto Record), bracket, opponent previews, swing cats, improvement targets, weekly add targets; ? tooltips on all 6 sections
- `docs/log.html` — pipeline log tab: run metadata, row counts, validation checks
- `docs/data/*.json` — live data, auto-committed by GitHub Actions after each run
- `data/player-crosswalk.csv` — 556-player crosswalk (committed); seeds DB in CI
- `data/espn_teams.json` — real team names/abbreviations (written by fetch_espn each run)
- `.github/workflows/daily-pipeline.yml` — 3x daily cron (7am full, 12pm light; 6pm light Mon/Tue/Fri-Sun; 11pm light Wed/Thu to catch MLB probables)
- `README.md` — setup instructions (Secrets, Pages, local dev)
- Dashboard live: https://nickguarriello.github.io/pitch-slap/
- Repo: https://github.com/nickguarriello/pitch-slap

---

## Build Order — Follow This Sequence

### Phase 1 — Foundation (Do First, Do Not Skip)
- [x] Initialize repo structure (all directories per PRD Section 4.1)
- [x] Write `config.py` with all league settings — ESPN mappings ported from old engine
- [x] Write `config_history/` snapshot mechanism — `config.snapshot()` built in
- [ ] Build `dim_players` crosswalk: `pybaseball.playerid_lookup()` as spine — NEXT
- [ ] Manual resolution of any ambiguous crosswalk matches
- [x] Initialize SQLite schema — 11 tables per PRD Section 5, verified
- [x] Stub `validate.py` with all check definitions — 20 checks built

### Phase 2 — Data Fetchers
- [x] `fetch_espn.py` — all view params per PRD Section 3.2
- [x] `fetch_mlb.py` — schedule, IL, probables, two-start detection
- [x] `fetch_statcast.py` — Statcast + FanGraphs with weekly cache logic
- [ ] Validate each fetcher output against ESPN UI manually before proceeding

### Phase 3 — Transform & Validate
- [x] `transform.py` — all joins on player_id (never name), all window calculations
- [x] Complete `validate.py` — 23 checks (20 original + 3 new: fact_player_stats, schedule, transform coverage)
- [x] Test validation_report.json output format
- [ ] Ground truth check: ERA, WHIP, OBP, SvHd against ESPN (requires live espn_era/espn_svhd params)

### Phase 4 — Evaluate
- [x] Category state model (WIN / FLOPPABLE / FLIPPABLE / LOSS)
- [x] Category need weight engine
- [x] Buy low / sell high flags (hitter xwOBA gap, pitcher ERA vs xFIP/SIERA)
- [x] Two-start pitcher scorer (SIERA/xFIP weighted composite)
- [x] Ownership velocity alerts
- [x] Constraint tracking (acquisitions, IP, waiver priority)
- [ ] Prospect watchlist logic (deferred to post-MVP)
- [ ] Punt framework (deferred to post-MVP)

### Pipeline Entry Point
- [x] `pipeline.py` — orchestrator with --mode full/light, writes pipeline-meta.json

### Phase 5 — Output & Dashboard
- [x] `report.py` — all CSVs per PRD Section 5.1 + JSON outputs (roster, waivers, matchup, league, status)
- [x] `pipeline_meta.json` and `validation_report.json` output
- [x] Home page (HTML/JS)
- [x] Matchup page
- [x] Waivers page
- [x] Players page
- [x] League page
- [x] Constraint status bar (Home + Waivers)
- [x] Pipeline health banner (all pages)

### Phase 6 — GitHub Actions
- [x] `daily-pipeline.yml` — 3x daily schedule (7am full, 12pm light, 6pm light); workflow_dispatch for manual runs
- [x] Implement --mode flag (full vs light) in pipeline entry point — DONE in pipeline.py
- [x] GitHub Secrets setup — ESPN_SWID + ESPN_S2 documented in README
- [ ] Test full end-to-end run (requires push + secrets configured in GitHub)
- [ ] Verify GitHub Pages deployment (Settings → Pages → main/docs)

### Phase 7 — Manual Verification (First 2 Weeks Live)
- [ ] Daily spot check: 5-10 players against ESPN UI
- [ ] Confirm ERA, WHIP, OBP, SvHd match ESPN displayed values
- [ ] Confirm acquisition count matches ESPN
- [ ] Confirm IP accumulated matches ESPN weekly IP total
- [ ] Log any discrepancies

---

## UAT Backlog — Known Issues & Refinements (Start Here Next Session)

### Priority Fixes — All resolved as of 2026-06-03
1. ✅ SELL_HIGH babip_ceiling → 0.350 with 100 PA guard
2. ✅ Buy-low quality floor → xwOBA ≥ 0.310 added
3. ✅ Buy-low volume → 144→108 flags after quality floor
4. ✅ Category thresholds → recalibrated from 9 weeks real data; symmetric FLOPPABLE=FLIPPABLE
5. ✅ Need weight → wired into buy-low scores (60% base + 40% need-adjusted)
6. ✅ Two-start scorer → days of rest component added

### Remaining Technical Debt
- **Two-start scorer still missing opponent quality + park factor**: `park_factor` in fact_schedule is NULL (no data source connected yet); opponent quality needs pitcher handedness (not in dim_players). Low priority until two-starters populate mid-week.
- **Ground truth checks** — ERA, WHIP, OBP, SvHd validation checks skipped (ESPN values not returned by current API params). Do a one-time manual spot-check (see README or ask Claude for steps); once verified, remove from this list.

### Ongoing Verification
7. **Two-start pitchers = 0 early week** — MLB Stats API only posts probable starters 3–5 days out. Section populates Wed/Thu; empty-state message on waivers.html now explains this. Source: `statsapi` (MLB official, free).
8. ✅ **IP accumulated tracking** — Resolved. `ip_accumulated` computed live from `fact_player_stats.current` in evaluate.py; displays correctly on dashboard. Dead column note removed.

### Data Structure Notes (resolved — for future reference)
- `roster.json` stats are nested: `players[].stats.season.r` not `players[].r_season`
- `waivers.json` keys: `buy_low_fa` / `two_starters_fa` (not `buy_low` / `two_starters`)
- `league.json` stats nested: `teams[].stats.R` (uppercase) not `teams[].r`
- All 4 HTML pages corrected to match actual JSON structure (2026-05-07)

---

## Open Questions (Answer Before or During Phase 1)

All resolved:
1. ESPN credentials — stored in `espn_credentials.py` (gitignored). Must be added to GitHub Secrets before first Actions run.
2. Team ID = 1
3. Current week = Week 6 (May 4–10, 2026)
4. Week 1 = March 25–April 5 (12-day first week). Week 2+ = standard 7-day.
5. Repo = https://github.com/nickguarriello/pitch-slap

---

## Key Decisions Made During Planning

- Weekly lineup lock (Monday) — no daily lineup decisions needed
- Waiver priority order (not FAAB) — speed > bid optimization
- Move to last after claim, 1-day processing period
- 7 acquisitions/week limit, 15 IP minimum/week (not season)
- No matchup calendar page — weekly lock makes it less useful; 2-start flags handle scheduling signal
- Fresh repo — old repo kept as archive
- validate.py is a first-class pipeline step, not optional
- Player ID crosswalk solved on Day 1 — no name matching in production
- config.py versioned in config_history/ on every change

---

## Post-MVP Backlog

1. ✅ **First-half / second-half splits** — BUILT 2026-06-04. `first_half` window in transform/report/players.html. Pre-break = aliases season. Post-break = BBRef date range. `second_half` button auto-shows after ASG break (Jul 16). ASG dates in config.py (`FIRST_HALF_END`, `ASG_BREAK_END`).
2. **Trade helper UI** — next priority. Focus: worst categories first, heavy playoff-week weighting (rounds start Week 22, 2×2-week matchups). Design: show what you can give vs what you need, surface playoff schedule fit.
3. Historical learning Phase B/C/D — hold until trade helper + remaining basics done. Phase B = category win-rate analysis; Phase C = hot/cold trend model; Phase D = predictive ML.
4. Multi-layer player value model — hold, will be integrated into trade helper.
5. Testing suite — post-MVP, after codebase stabilizes.
6. ✅ **Playoff prep tab** — BUILT 2026-06-03.

---

## Session Log

| Date | What Was Done | What Is Open |
|------|--------------|--------------|
| Pre-build | Full planning complete. PRD v2.1, architecture, mockup, CLAUDE.md, PLANNING.md produced. | Everything — build not started. |
| 2026-05-07 | Phase 1: repo connected to github.com/nickguarriello/pitch-slap. All docs ingested. config.py built (full). 11-table SQLite schema init. validate.py stub (20 checks). Naming: snake_case for .py, kebab everywhere else. | dim_players crosswalk (Phase 1 last item). Then Phase 2: fetch_espn.py. |
| 2026-05-07 (cont) | Phase 1 complete: build_crosswalk.py + overrides.json (all 256 rostered matched). Phase 2 complete: fetch_espn.py (556 players, per-cat matchups, constraints), fetch_mlb.py (182 schedule rows, 350 transactions, two-start detection), fetch_statcast.py (499 rows: 261 batters xBA/wRC+, 241 pitchers xFIP/SIERA, 7-day cache). FanGraphs legacy endpoint 403-blocked -- switched to JSON API. Phase 3: transform.py (2074 rows: season/30d/14d/current windows). validate.py (23 checks, 19 pass/3 warn/0 fail). Phase 4: evaluate.py (cat states, need weights, buy/sell, 2-start, constraints). pipeline.py orchestrator (full+light modes, ~29s light run). | Phase 5: report.py + dashboard HTML. |
| 2026-05-07 (cont2) | Phase 5 complete: report.py (roster/waivers/matchup/league/status JSON + 3 CSVs). All 5 dashboard pages: index.html, matchup.html, waivers.html, players.html, league.html. Dark theme, pipeline health banner on all pages, sortable tables on players/league. Open: SELL_HIGH babip_ceiling threshold too low (0.260, should be ~0.350 — tune after 2 weeks live). | Phase 6: daily_pipeline.yml GitHub Actions workflow. |
| 2026-05-07 (cont3) | Phase 6 complete: .github/workflows/daily-pipeline.yml (3x daily cron, workflow_dispatch, writes ESPN creds from secrets, inits DB, auto-commits docs/data/). README.md with first-time setup instructions (Secrets + Pages). Open: configure ESPN_SWID + ESPN_S2 secrets in GitHub repo settings, enable Pages (main/docs), trigger first manual run to verify. | Phase 7: manual verification. |
| 2026-05-07 (cont4) | First CI run failed (exit code 1): dim_players empty in CI because db/pitch-slap.db is gitignored. Fix: exported dim_players to data/player-crosswalk.csv (committed), added seed_crosswalk() to init_db.py. Second CI run succeeded (1m 16s, 499 Statcast rows matched). Dashboard live. UAT round 1: all 4 HTML pages had field name mismatches vs actual JSON structure — fixed in same session. Dashboard now shows correct data on all 5 pages. | UAT round 1 in progress. See UAT Backlog above. |
| 2026-06-03 (cont2) | **Logic + Playoff tab session**: All 6 priority fixes completed. CAT_THRESHOLDS recalibrated from 9 weeks real data then made symmetric (FLOPPABLE=FLIPPABLE=avg gap). buy-low: xwOBA≥0.310 quality floor + need-weight blend (60/40). sell-high: babip_ceiling 0.260→0.350 + 100 PA guard. Two-start scorer: days-of-rest component wired in. Playoff tab built (playoff.html + playoff.json): standings with W-L-T + Cat W-L-T, bracket, category previews vs all 3 potential opponents, swing cats, improvement targets, weekly add targets. Matchup record corrected to include ties (6-2-1). Cumulative cat W-L-T for all 8 teams computed from full history. Wed/Thu pipeline cron shifted to 11pm EDT to catch MLB probable pitcher releases. Config snapshots: 2026-06-02_22-42-52.py and 2026-06-02_23-10-42.py | Two-start scorer still missing opponent quality + park factor (data gaps). Trade helper UI is next priority (see Post-MVP backlog). |
| 2026-06-04 | **Full HTML pass + players restructure** (session recovered after rate-limit interruption). Pipeline: 7d window added to transform.py + build_roster query. evaluate.py: ownership velocity 2-day/5% lookback (was consecutive-snapshot/10%), IP tracking fixed (reads live from fact_player_stats, not stale constraint_log). report.py: history `state` field (WIN/FLOPPABLE/etc per cat per week), pipeline-log.json output, always-include-ties in record strings. log.html: new Pipeline Log tab. index.html: Mode removed from banner, SVHD label, ? tooltip icons on all sections. matchup.html: rate stats 4 decimals, season W/L/T, history state tooltips. waivers.html: velocity fields updated, rostered badge. players.html: full ESPN-style restructure — 6 ordered sections (active H, active P, bench P, bench H, IL P, IL H), `BE` bench-slot fix, 5 window buttons (Current Matchup/7D/14D/30D/Season), ? tooltips on every section. league.html: Record column, heat map rank colors, sort toggle, Roto Record rename. playoff.html: always show ties in record; Roto Record header rename; ? tooltips on all 6 sections (Playoff Picture, Category Profile, Opponent Previews, Swing Categories, Improvement Targets, Weekly Add Targets). All 7 pages: ? icon popup tooltips with full logic/formula/threshold explanations on every section. | Trade helper UI next. Two-start scorer still missing opponent quality + park factor. |
| 2026-06-04 (session 2) | **Verification pass + PLANNING.md sync** (two parallel sessions ran simultaneously; other session committed all outstanding HTML work). Verified: players.html `BE` bench-slot fix confirmed in code; "Current Matchup" button label confirmed; 6-section ESPN layout confirmed; playoff.html all 6 tooltip sections confirmed in HEAD. Corrected PLANNING.md descriptions (players.html layout detail, "Current Matchup" label, playoff tooltip count). No code changes needed — all work already committed by parallel session. | Trade helper UI next. |
| 2026-06-04 (session 3) | **Pipeline fix + UX/splits session**. (1) Fixed CI pipeline failing every run: FanGraphs 403-blocked on GitHub Actions IPs — replaced both season stat fetchers with MLB Stats API (batting + pitching). QS now computed from per-player game logs in batches (~4s). (2) Two-start empty-state message updated to explain Wed/Thu population timing. (3) Stale IP-accumulated comment removed from evaluate.py. (4) Ground truth check item clarified (manual step, remove once verified). (5) First-half/second-half splits built: `ASG_BREAK_END`/`FIRST_HALF_END` in config.py; `first_half` window in transform.py (aliases season pre-break, BBRef date range post-break); `second_half` window auto-added post-break; report.py and players.html updated; 2nd Half button auto-hides until break passes. | Trade helper UI next (playoff-weighted, worst-cat-first). |
| 2026-06-10 | **Pipeline fix: pybaseball Statcast fallback + SafeEncoder default**. Root cause: statcast-cache.json last fetched 2026-06-02; 7-day TTL expired June 9. Pybaseball Savant scrapers (same underlying breakage as the June 8 BBRef fix) threw exceptions → exit code 2. Fix 1: `fetch_statcast.py` now wraps pybaseball calls in try/except; on failure, logs a warning and returns skipped (keeps existing DB data). Fix 2: `report.py` `_SafeEncoder` now defines `default=str` — restores the safety net that `default=str` previously provided to `json.dump`. 2 files changed. | Statcast data is stale (last good fetch June 2) until pybaseball is repaired or replaced with direct Savant API calls. Trade helper UI still next. |
| 2026-06-03 | **Dashboard overhaul** (6 files changed, user explicitly approved >3-file limit). Changes: (1) Home subtitle now shows real W/L/T via gap-based counting — not WIN/LOSS state labels. (2) ERA/WHIP IP qualifier fix in evaluate.py: auto-LOSS when below 15 IP, with ip_note + my_ip_week in cat state; cat grid shows "X.X / 15 IP" amber warning. (3) Real team names via data/espn_teams.json (written by fetch_espn, read by report); league.json now has team_name + team_abbrev. (4) Matchup header shows current opponent name; history table adds Opponent column; driven by data/current-opponent.json (separate file — not overwritten by main.py meta saves). (5) Home Buy Low now pulls wire targets (waivers.buy_low_fa) + trade targets from other rosters (waivers.buy_low_trade); Sell High renamed "Trade Away". (6) Waivers: top 5 buy-low, added OBP/xwOBA/BABIP columns, ownership velocity explained. (7) League: removed Value column (duplicate), removed rank badges, sort always best-first. Banner improved with tooltips. Researched probable pitcher sources — all derive from MLB feed, no faster option exists. Shifted Wed/Thu 6pm pipeline run to 11pm EDT to catch MLB probable releases. | See Priority Fixes above — buy/sell logic needs tuning next session. Playoff prep tab added to backlog. |

| 2026-06-15 | **Pipeline fix: ESPN dict-shaped projections + full refresh**. Root cause of full-run failures since 2026-06-08: ESPN now returns projection stat-id 83 (mapped to `svhd`) as a scoring-period container dict `{points, breakdown}` instead of a scalar, so `float(val)` in `fetch_espn._extract_projections` threw TypeError and aborted the entire fetch — the cloud 7am full cron hit the same break (light runs were the only ones still committing, and they skip stat recompute). Fix: `_extract_projections` now guards the coercion (only floats numeric/str values; skips dict/unexpected shapes → leaves None, matching existing null projection output). 1 file changed (`pipeline/fetch_espn.py`). Ran full pipeline locally to confirm green: 554 players, 515 statcast rows, 2441 stat rows, validation 19 pass / 3 warn / 0 fail. Refreshed data committed. Used for trade analysis (Bauers/Vargas/Muncy/PCA → SP on ICECOLDBEERHERE + I Robbed A Nuke). Note: need-weights shifted vs 6/08 — QS .57→.61, ERA .53→.44, WHIP .53→.40 (staff ratios improved; QS now a top pitching need alongside K). | Trade helper UI still next. Statcast pybaseball path succeeding again as of this run. |

| 2026-06-15 (cont) | **Root-cause fix: ESPN projection mapping was wrong (projections always null)**. Follow-up to the dict-guard above. Investigation showed `player.stats` is keyed by scoring-PERIOD id (0 = season; 82/83 = recent periods), not stat-category id — so the old numeric `ESPN_STAT_IDS` map never matched, and `espn_proj_*` columns were null league-wide (stat-id 83 colliding with period 83 was the only "match," hence the crash). Real projections live in `stats[0]['projected_breakdown']`, keyed by stat abbreviations (R/HR/RBI/SB/OBP, K/QS/ERA/WHIP, and a pre-summed SVHD). Fix: replaced `ESPN_STAT_IDS` with `ESPN_PROJ_KEYS` (abbrev→cat) in config.py (snapshot: config-history/config_2026-06-15_21-01-16.py); removed unused `ESPN_RATE_STAT_IDS`; rewrote `_extract_projections` to read `projected_breakdown` (period 0, with fallback). 2 code files + snapshot. Verified via full run: projections now populate — 278 hitters, 257 pitchers, 175 SP (QS), 74 RP (SVHD); validation 19 pass / 3 warn / 0 fail. | Trade helper UI still next. ESPN projections now usable downstream for the first time (evaluate/report do not yet consume them). |

| 2026-06-16 | **Trade analyzer — logic design (prototype, not yet implemented)**. Started the long-planned trade-helper as a *reusable scoring tool*, not a one-off. Wrote `TRADE_ANALYZER_DESIGN.md` (root): core scoring function `TradeScore = Σ NEED[cat]×(Σ in.contrib − Σ out.contrib)` with `contrib` = per-week pace normalized into category-units; 6-step pipeline (need pass → surplus pass → target pass → enumerate → score+acceptance-gate → rank); full parameter list; data sources (need_weights from matchup.json, fact_player_stats, now-populated `espn_proj_*`). Validated the function by hand-scoring 6 example packages. Key principle surfaced: **spend from lowest-need surplus** (K/QS gain is similar whether you pay with a reliever or a bat, but a surplus RP debits only SvHd ×.10 vs a bat debiting R/HR/RBI/SB ×.44–.53). **Top v2 TODO: replacement-level marginal value** — v1 over-penalizes giving a surplus bat by debiting its full line instead of (player − bench replacement); fixing this re-ranks bat-for-ace deals upward (see design §6). Also open: per-team need-weights for the acceptance gate (§7). No specific trades persisted (per direction). Need-weights snapshot 2026-06-15: OBP .70, K .61, QS .61, RBI .53, HR .49, R/SB .44, ERA .44, WHIP .40, SvHd .10. | **Next: implement trade analyzer** — see TRADE_ANALYZER_DESIGN.md §8. Decide replacement-level approach, generalize evaluate.py need-weights per-team, build pipeline/trade.py + trades.json + trades.html. |

| 2026-06-16 (cont) | **Trade analyzer — scoring model DECIDED: lineup-aware (v3)**. Resolved the §6 open question. TradeScore = change in expected weekly **active-lineup** production: `before=optimize_lineup(roster)`, `after=optimize_lineup(roster−out+in)`, score the need-weighted Δ. Chosen over the simpler VORP/replacement-level model for exactness (no replacement-baseline guesswork); benched-surplus and slot-scarcity fall out by construction. New core dependency to build: `optimize_lineup(roster)` — bipartite player→slot assignment (ROSTER_SLOTS + ESPN eligibility) maximizing need-weighted production; pitchers handled with IP-weighted rate cats over the chosen 10 P slots. VORP to be stood up as a sanity baseline only. v4 (future): probabilistic + opponent-adjusted. Spec updated: TRADE_ANALYZER_DESIGN.md §2 (note), §6 (decision), §8 (build order). | **Next: build `optimize_lineup` first** (TRADE_ANALYZER_DESIGN.md §8), then per-team need-weights, then pipeline/trade.py + trades.json + trades.html. |

| 2026-06-16 (cont2) | **Trade analyzer build — step 1a: persist multi-position eligibility (schema change, approved)**. The v3 lineup-aware optimizer needs true slot eligibility, but the db stored only single `position`. Added `fact_espn_rosters.eligible_slots TEXT` (comma-joined ROSTER_SLOTS codes). `init_db.py`: column in CREATE + idempotent `_migrate()` (PRAGMA check → ALTER ADD COLUMN; runs inside `init_db()`, which CI calls via `python -m pipeline.init_db` before main.py — so cloud migrates too; fresh CI dbs get it from CREATE). `fetch_espn.py`: `_normalize_eligible()` maps ESPN `eligibleSlots` names → our codes (BE→BN, 1B/3B→CI, 2B/SS→MI, LF/CF/RF→OF, DH→UTIL, SP/RP→P), captured in `_build_player_row` + INSERT. Verified via full run: all 554 players populated, multi-pos correct (Vargas→1B,3B,CI,UTIL; Betts→SS,MI,UTIL; Kurtz→1B,CI,UTIL; pitchers→P). Gotcha logged: `main.py` does NOT call init_db, and piping a run to `tail` masks its exit code (a failed fetch_espn looked green). | **Next: build `optimize_lineup(roster)`** on top of eligible_slots (TRADE_ANALYZER_DESIGN.md §6/§8). |

| 2026-06-16 (cont3) | **Trade analyzer build — step 1b: `optimize_lineup` built** (`pipeline/trade.py`). Loads a team from db (eligibility + season stats), scores each player with a need-weighted normalized `player_value`, assigns hitters to ROSTER_SLOTS via transversal-matroid greedy + augmenting-path matching (multi-position aware), takes top-10 pitchers for P, returns the active per-category production vector. Self-test on my roster passes: 15 hitting seats filled correctly (Vargas→1B, Muncy→3B, Bauers→CI, etc.), 3 lowest-value bats benched (Betts/Yelich/Jensen). **Calibration issue found (logged in design §7):** `player_value` over-weights ERA/WHIP vs the QS need → it benches a QS starter (Taj Bradley) for elite-ratio RPs, dropping active QS 43→38. Must calibrate the rate-cat term before the trade scorer is trustworthy. | **Next: calibrate `player_value` rate term** (design §7), then build the before/after trade scorer + enumeration on top of `optimize_lineup`. |

| 2026-06-16 (cont4) | **Trade analyzer build — step 2: rate calibration + `score_trade`** (`pipeline/trade.py`). (1) Calibrated `player_value`: rate cats (ERA/WHIP/OBP) now scaled by the player's volume share (weekly IP/PA over `IP_WEEK`/`PA_WEEK`), so a low-inning elite RP can't outweigh a QS starter — self-test now keeps all 6 SP active, QS 38→43. (2) Built `score_trade(my_roster, incoming, outgoing, need)` = need-weighted Δ of before/after `optimize_lineup` production; added `load_rostered()` (all-teams trade-piece pool) + `RATE_MARGIN` param. Validated the v3 thesis: bat-for-ace (Muncy→Chase Burns) flipped from −0.10 (v1 naive) to +0.33 (v3 lineup-aware), and surplus-RP-for-QS-SP (Duran→Davis Martin) scores +0.126. Open calibration: scorer `RATE_MARGIN` (ERA=0.20) over-weights rate swings — widen/tune before shipping. | **Next (design §8): 4 package enumeration, 5 per-team need-weights + acceptance gate, 6 trades.json/trades.html.** |

| 2026-06-16 (cont5) | **Trade analyzer — FUNCTIONAL END-TO-END (enumeration + acceptance gate + output)**. (#4) `enumerate_trades()` in `pipeline/trade.py`: 1-for-1 over both active rosters + bounded 2-for-2 (extend top anchors), both-sides scored, de-duped, ranked; added talent-balance gate (`FAIR_TOL`, neutral-need valuation) to block lopsided fleece offers. (#5) Acceptance gate: parameterized `evaluate.compute_need_weights(conn, team_id)` + `_estimate_cat_ranks(conn, team_id)` (backward-compatible, my-team default) — validated per-team (I Robbed A Nuke → SvHd .70, confirming their no-closer hole). (#6) Output: `trade.run()` writes `docs/data/trades.json`; new `docs/trades.html` renders ranked proposals + method/limits caveat; wired into `main.py` Phase 6 (non-blocking, full+light). Full pipeline run green: **35 ranked trades across 7 teams**. CLAUDE.md: evaluate.py change was pre-approved by user; trades.json written by trade.py (not report.py) to avoid the CSV-column boundary. **Open (calibration/polish, not plumbing):** rate-scale calibration (ERA-driven swings inflate absolute scores — ranking ok, magnitudes not yet trustworthy); asset/upside valuation (category model under-prices ace pedigree); add trades.html nav link to other 6 pages; protected-players param. | Trade analyzer shipped (v1). Next: calibrate rate scaling + asset valuation; add cross-page nav link. See TRADE_ANALYZER_DESIGN.md §8. |

| 2026-06-16 (cont6) | **Dashboard UI feedback pass.** (1) `report.py` now emits `eligible_slots` in roster.json. (2) `players.html`: reordered sections to Batters (active→bench→IL) then Pitchers (active→bench→IL); **POS column now = all eligible slots** (from `eligible_slots`, BN/IL stripped), **SLOT = actual lineup position** (`lineup_slot`, incl BN/IL). (3) `playoff.html`: Playoff Picture table now sortable on every column (asc/desc toggle, arrow indicator; playoff cutline only shown in default seed order). (4) `league.html`: removed the category filter buttons (cat-tabs); header-click sorting retained, default sort = Roto Record, "#" is now row position. (5) `log.html`: added prominent "Data As Of" hero card with color-coded relative age (fresh/aging/stale) + note that upstream sites (ESPN scores, Statcast) lag. Regenerated JSON; pipeline green. | **Open follow-ups:** (a) POS=eligible on FA/target lists (waivers/playoff/index) — needs `eligible_slots` threaded through evaluate.py buy_low/sell_high flag builders (Pos there currently shows "—"). (b) User wants the full pipeline scheduled to run overnight (after ESPN/Statcast settle) — cron change to daily-pipeline.yml (hard-limit file; needs explicit go-ahead). |

| 2026-06-16 (cont7) | **POS=eligible slots on FA/target lists (follow-up #1 done).** `evaluate.py`: added `fer.eligible_slots` to the 4 buy-low/sell-high flag builders (hitter+pitcher SELECTs + dicts) — flows automatically into waivers.json (buy_low_fa/buy_low_trade) and the playoff trade-stream (reads buy_low_fa). `waivers.html` + `playoff.html`: added `posSlots(p)` helper (eligible_slots minus BN/IL, fallback to position); playoff trade-stream `isPitcher` now derived from eligibility (was reading a `position` field these flags never had → previously treated everyone as a hitter — fixed). Verified: Vientos→3B,CI,UTIL,1B; Crews→OF,UTIL; Morejon→P. NOT changed: two-start + ownership-velocity builders (pitchers/movers, lower value, two-start empty this run) — POS there still falls back to "—". | Overnight-cron change still pending user's finding of ESPN's last nightly update time. |

| 2026-06-16 (cont8) | **Pipeline schedule → once-daily 6am + game-settle soft check** (user-approved workflow change). `daily-pipeline.yml`: replaced the 4 crons (7am full + 3 light) with a single `0 10 * * *` (6:00am EDT) **full** run; mode step simplified (schedule always full, workflow_dispatch can override). Rationale: last MLB game ~10pm ET + ~5h settle ≈ 3am, 6am safe. **Soft freshness check:** `fetch_mlb.check_recent_games_settled()` queries the MLB API for *yesterday's* slate and confirms all games are `Final` (generic — derives the last game from the schedule, no hardcoded teams). Wired into `main.py` full run (non-blocking warn) → stored in pipeline-meta → surfaced via `report.build_pipeline_log` (`games_settled`) → rendered on the Log tab freshness card (✓ complete / ⚠ settling). Verified: 6/15 → 10/10 Final, last game **TB@LAD** (the Rays/Dodgers late game), all_settled true. Note: cron is fixed-UTC (no DST) — fires 5am ET in winter. | Trade analyzer + UI feedback all shipped. Possible future: surface settle-warning on other tabs; DST-aware schedule. |

| 2026-06-16 (cont9) | **Track ESPN's nightly stat-update time** (the real goal — game-final ≠ ESPN stats posted). Discovery: ESPN's league status exposes `standingsUpdateDate` (+ `waiverLastExecutionDate`, same instant) = its nightly batch that reprocesses the prior day's results/posts stats. Read live = **2026-06-16 04:35 EDT** — directly readable, no polling needed. `fetch_espn.get_espn_update_status()` returns these as ISO; `main.py` full run captures into pipeline-meta AND appends a rolling record to `docs/data/espn-update-history.json` (last 60 runs; in docs/data so CI persists it) combining ESPN update time + games_settled. `report.build_pipeline_log` exposes `espn_update`; Log card now shows "ESPN last posted stats: <time>" (replaced the game-conclusion line — that's track-only per user). Over days this history reveals ESPN's nightly update time + variance, and validates the 6am run (ESPN batch ~4:35, we run 6am). | After ~1–2 weeks of history, review `espn-update-history.json` to confirm ESPN's settle time/variance — could then tighten the run time. Game-settle still tracked (not shown). |

| 2026-06-16 (cont10) | **UI feedback round 2.** (1) **ERA/WHIP IP-qualifier rewrite** (`evaluate.compute_cat_states`): now considers BOTH teams' weekly IP (added opponent IP via `current-opponent.json` → opp roster current-window IP). Logic: neither team ≥15 IP → TIE (with dual counter); only one ≥15 → that team wins (other forfeits); both ≥15 → normal value comparison. Checked before the None-guard so early-week null rates still show TIE. `index.html` IP line now shows the full note ("You X/15 IP · Opp Y/15 IP"). Verified: ERA/WHIP → TIE, "You 1.0/15 · Opp 5.0/15". (2) **league.html record sort fixed** — clicking Record did nothing because it sorted a string ("6-3-1"); now sorts by numeric score (wins + 0.5·ties). (3) Matchup "Season W/L/T" question answered (no change): it's the per-category weekly W-L-T record across completed weeks (from cat_records/fact_matchups), e.g. weeks you won the HR category — not the overall matchup record. | — |

| 2026-06-17 | **Bug fix: fact_matchups duplicate accumulation** (matchup tab "Season W/L/T" showed e.g. R 160-139). Root cause: `write_matchups_to_db` used `INSERT OR IGNORE INTO fact_matchups` but the table has no unique constraint, so every run re-inserted the full re-fetched season history → ~30× duplicates (win_rate looked fine because ratios were preserved, hiding it). Fix: full-refresh my final rows (`DELETE FROM fact_matchups WHERE my_team_id=? AND is_final=1`) before the history insert — idempotent, no schema change, and auto-cleans existing dupes on next run. Verified: fact_matchups 2990→110 rows (11 weeks × 10 cats); R now 6-5-0, all cats sum to 11 completed weeks. Need-weights unaffected (ratio-preserved). | — |

---

*Update this table at the end of every session.*
