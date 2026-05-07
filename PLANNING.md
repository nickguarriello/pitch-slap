# PLANNING.md — Pitch Slap Decision Engine
## Running Build Log — Updated Every Session

---

## Project Status: PRE-BUILD — READY TO START

All planning and requirements are complete. See PRD v2.0 for full specifications.

---

## What Exists Right Now

- PRD v2.0 (pitch-slap-prd-v2.docx) — full requirements, architecture, business logic
- Architecture diagram (pitch-slap-architecture.html) — visual system overview
- Dashboard mockup (pitch-slap-mockup.html) — approved UI reference
- CLAUDE.md — session rules
- PLANNING.md — this file

**Nothing has been built yet. The repository is empty.**

---

## Build Order — Follow This Sequence

### Phase 1 — Foundation (Do First, Do Not Skip)
- [ ] Initialize repo structure (all directories per PRD Section 4.1)
- [ ] Write `config.py` with all league settings (values in CLAUDE.md)
- [ ] Write `config_history/` snapshot mechanism
- [ ] Build `dim_players` crosswalk: `pybaseball.playerid_lookup()` as spine
- [ ] Manual resolution of any ambiguous crosswalk matches
- [ ] Initialize SQLite schema — all tables per PRD Section 5
- [ ] Stub `validate.py` with all check definitions (implement checks as each fetch is built)

### Phase 2 — Data Fetchers
- [ ] `fetch_espn.py` — all view params per PRD Section 3.2
- [ ] `fetch_mlb.py` — schedule, IL, 40-man, probables, lineup order
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

1. ESPN swid + espn_s2 cookies — retrieve from browser dev tools and add to GitHub Secrets
2. ESPN team ID within league #1985887220 — check ESPN URL when viewing your team
3. Current week number in the season
4. Season start date (Week 1 Monday)
5. Fresh repo name — recommended: `pitch-slap` or `pitch-slap-engine`

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

---

*Update this table at the end of every session.*
