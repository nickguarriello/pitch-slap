# PLANNING.md — Pitch Slap Decision Engine
## Running Build Log — Updated Every Session

---

## Project Status: LIVE — Phases 1–6 complete. Dashboard overhauled 2026-06-03. Phase 7 (manual verification) ongoing.

---

## What Exists Right Now (Full Build — Live as of 2026-05-07)

- `config.py` — full league config, ESPN mappings, all PRD thresholds
- `pipeline/init_db.py` — 11-table SQLite schema + seed_crosswalk() for CI
- `pipeline/fetch_espn.py` — ESPN roster, matchup, constraints (556 players)
- `pipeline/fetch_mlb.py` — schedule, transactions, two-start detection
- `pipeline/fetch_statcast.py` — Statcast + FanGraphs (499 rows, 7-day cache)
- `pipeline/transform.py` — 4 windows: season / 30d / 14d / current (~2000 rows)
- `pipeline/validate.py` — 23 checks (typically 19 pass / 3 warn / 0 fail)
- `pipeline/evaluate.py` — cat states, need weights, buy/sell, 2-start, constraints
- `pipeline/report.py` — writes docs/data/*.json + CSVs
- `main.py` — orchestrator (--mode full/light, renamed from pipeline.py)
- `docs/*.html` — 5 dashboard pages (index, matchup, waivers, players, league)
- `docs/data/*.json` — live data, auto-committed by GitHub Actions after each run
- `data/player-crosswalk.csv` — 556-player crosswalk (committed); seeds DB in CI
- `.github/workflows/daily-pipeline.yml` — 3x daily cron (7am full, 12pm/6pm light)
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
- **Ground truth checks** — ERA, WHIP, OBP, SvHd validation checks skipped (ESPN values not returned by current API params). Spot-check manually weekly.

### Ongoing Verification
7. **Two-start pitchers = 0 early week** — MLB Stats API only posts probable pitchers ~3–5 days out. Section populates by Wed/Thu. Wed/Thu pipeline now runs 11pm EDT to catch releases. Source: `statsapi` (MLB official, free).
8. **IP accumulated tracking** — `constraint_log.ip_accumulated` always 0.0 (never written by fetch_espn). ERA/WHIP qualifier computed live from `fact_player_stats.current` window in evaluate.py. If IP tracking needed elsewhere, fix write_constraints_to_db.
9. **Ground truth checks** — ERA, WHIP, OBP, SvHd validation checks skipped (ESPN values not returned by current API params). Spot-check manually weekly.

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

1. First-half frozen snapshot (pre_asg) + 60D window — scope together
2. Historical learning Phase B/C/D — 10+ weeks of data now available, enough to start Phase B
3. ✅ **Playoff prep tab** — BUILT 2026-06-03. Standings (W-L-T + Cat W-L-T), bracket, category matchup previews vs all 3 potential opponents, swing categories, improvement targets, weekly add targets. Year-round visibility.
4. **Trade helper UI** — next priority. You're seed 2, 5-5 projected vs R1 opponent. Swing cats are R, HR, K, QS. A trade helper scoping "what can I give to get HR/K help" would be directly useful now.
5. Multi-layer player value model
6. Testing suite

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
| 2026-06-03 | **Dashboard overhaul** (6 files changed, user explicitly approved >3-file limit). Changes: (1) Home subtitle now shows real W/L/T via gap-based counting — not WIN/LOSS state labels. (2) ERA/WHIP IP qualifier fix in evaluate.py: auto-LOSS when below 15 IP, with ip_note + my_ip_week in cat state; cat grid shows "X.X / 15 IP" amber warning. (3) Real team names via data/espn_teams.json (written by fetch_espn, read by report); league.json now has team_name + team_abbrev. (4) Matchup header shows current opponent name; history table adds Opponent column; driven by data/current-opponent.json (separate file — not overwritten by main.py meta saves). (5) Home Buy Low now pulls wire targets (waivers.buy_low_fa) + trade targets from other rosters (waivers.buy_low_trade); Sell High renamed "Trade Away". (6) Waivers: top 5 buy-low, added OBP/xwOBA/BABIP columns, ownership velocity explained. (7) League: removed Value column (duplicate), removed rank badges, sort always best-first. Banner improved with tooltips. Researched probable pitcher sources — all derive from MLB feed, no faster option exists. Shifted Wed/Thu 6pm pipeline run to 11pm EDT to catch MLB probable releases. | See Priority Fixes above — buy/sell logic needs tuning next session. Playoff prep tab added to backlog. |

---

*Update this table at the end of every session.*
