# PLANNING.md — Pitch Slap Decision Engine
## Running Build Log — Updated Every Session

---

## Project Status: IN PROGRESS — Phase 1 partially complete

---

## What Exists Right Now

- `config.py` — full league config, ESPN mappings, all PRD thresholds, snapshot() mechanism
- `pipeline/init_db.py` — 11-table SQLite schema, verified clean init
- `pipeline/validate.py` — 20 validation checks (schema, range, completeness, crosswalk, ground truth, staleness)
- `db/pitch-slap.db` — initialized (gitignored — local only)
- `espn_credentials.py` — ESPN cookies loaded (gitignored)
- Planning docs: CLAUDE.md, PLANNING.md, architecture HTML
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
- [ ] `fetch_statcast.py` — Statcast + FanGraphs with weekly cache logic
- [ ] Validate each fetcher output against ESPN UI manually before proceeding

### Phase 3 — Transform & Validate
- [ ] `transform.py` — all joins on player_id (never name), all window calculations
- [ ] Complete `validate.py` — all checks per PRD Section 8
- [ ] Test validation_report.json output format
- [ ] Ground truth check: ERA, WHIP, OBP, SvHd against ESPN

### Phase 4 — Evaluate
- [ ] Category need weight engine
- [ ] Buy low / sell high flags
- [ ] Two-start pitcher scorer
- [ ] Prospect watchlist logic
- [ ] Ownership velocity alerts
- [ ] Waiver priority-aware recommendations
- [ ] Constraint tracking (acquisitions, IP, roster slots)
- [ ] Category state model (WIN / FLOPPABLE / FLIPPABLE / LOSS)
- [ ] Punt framework

### Phase 5 — Output & Dashboard
- [ ] `report.py` — all CSVs per PRD Section 5.1
- [ ] `pipeline_meta.json` and `validation_report.json` output
- [ ] Home page (HTML/JS)
- [ ] Matchup page
- [ ] Waivers page
- [ ] Players page
- [ ] League page
- [ ] Constraint status bar (Home + Waivers)
- [ ] Pipeline health banner (all pages)

### Phase 6 — GitHub Actions
- [ ] `daily_pipeline.yml` — 3x daily schedule (7am full, 12pm light, 6pm light)
- [ ] Implement --mode flag (full vs light) in pipeline entry point
- [ ] GitHub Secrets setup (ESPN cookies, documented in README)
- [ ] Test full end-to-end run
- [ ] Verify GitHub Pages deployment

### Phase 7 — Manual Verification (First 2 Weeks Live)
- [ ] Daily spot check: 5-10 players against ESPN UI
- [ ] Confirm ERA, WHIP, OBP, SvHd match ESPN displayed values
- [ ] Confirm acquisition count matches ESPN
- [ ] Confirm IP accumulated matches ESPN weekly IP total
- [ ] Log any discrepancies

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

## Post-MVP Backlog (Do Not Build During MVP)

1. First-half frozen snapshot (pre_asg) + 60D window — scope together
2. Historical learning Phase B/C/D — Phase A table built now, analysis deferred until 6+ weeks of data
3. Playoff mode logic
4. Trade helper UI
5. Multi-layer player value model
6. Testing suite

---

## Session Log

| Date | What Was Done | What Is Open |
|------|--------------|--------------|
| Pre-build | Full planning complete. PRD v2.1, architecture, mockup, CLAUDE.md, PLANNING.md produced. | Everything — build not started. |
| 2026-05-07 | Phase 1: repo connected to github.com/nickguarriello/pitch-slap. All docs ingested. config.py built (full). 11-table SQLite schema init. validate.py stub (20 checks). Naming: snake_case for .py, kebab everywhere else. | dim_players crosswalk (Phase 1 last item). Then Phase 2: fetch_espn.py. |
| 2026-05-07 (cont) | Phase 1 complete: build_crosswalk.py + overrides.json (all 256 rostered matched). Phase 2: fetch_espn.py (556 players, per-cat matchups, constraints). fetch_mlb.py (182 schedule rows, 350 transactions, two-start detection). | fetch_statcast.py (Phase 2 final). Then Phase 3: transform.py. |

---

*Update this table at the end of every session.*
