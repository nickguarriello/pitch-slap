# CLAUDE.md — Pitch Slap Decision Engine
## AI Session Rules — Read This First, Every Session

This file governs every Claude Code session on this project. These rules exist because a prior build was corrupted by an unconstrained session making unauthorized changes across multiple files simultaneously. They are non-negotiable.

---

## Before Touching Any File

1. Read this file in full
2. Read PLANNING.md to understand current state
3. Run `git status` — confirm working tree is clean before starting
4. State explicitly: which file you are editing, which function, and why

---

## Hard Limits — Never Do These Without Explicit Instruction

- Do not change `.github/workflows/daily_pipeline.yml` (runs 3x daily: 7am full, 12pm light, 6pm light)
- Do not change the SQLite schema (add/remove/rename columns or tables)
- Do not change CSV output column names or structure
- Do not remove or weaken any check in `pipeline/validate.py`
- Do not force push to `main`
- Do not change `config.py` without first saving a versioned snapshot to `config_history/`
- Do not change more than 3 files in a single session without a checkpoint review

---

## Boundaries Per File

| File | Rule |
|------|------|
| `pipeline/fetch_espn.py` | Only change if the specific API call or field mapping is explicitly agreed upon |
| `pipeline/fetch_mlb.py` | Same as above |
| `pipeline/fetch_statcast.py` | Same as above |
| `pipeline/transform.py` | Schema joins cannot change without schema approval |
| `pipeline/validate.py` | Checks can be added, never removed or weakened |
| `pipeline/evaluate.py` | Logic changes must be described and agreed before implementation |
| `pipeline/report.py` | CSV column changes require explicit approval |
| `config.py` | Snapshot to config_history/ before any change |
| `docs/*.html` | UI changes require approval before merge |
| `db/pitch_slap.db` | Schema changes require migration plan — no silent column additions |

---

## When Something Is Unclear

Ask. Do not assume and build. A wrong assumption that gets built and committed is harder to fix than a clarifying question.

---

## End of Every Session

1. Run `git status` — confirm only expected files changed
2. Update `PLANNING.md`:
   - What was completed this session
   - What is currently open / in progress
   - Any issues found or decisions made
3. Commit with a clear message: `feat: [what was built]` or `fix: [what was fixed]`
4. Do not leave uncommitted changes

---

## Data Integrity Rules

- The validate.py step runs after transform and before evaluate — never skip it
- If validation returns FAIL on a critical check, stop the pipeline — do not write stale outputs
- Every player in scored outputs must have a matched MLB ID — unmatched players go to the data gap section, never silently through
- Stat calculation ground truth: spot check ERA, WHIP, OBP, SvHd against ESPN's displayed values weekly

---

## League Configuration (Do Not Guess — Use These Values)

```python
LEAGUE_ID = 1985887220
SEASON = 2026
TEAM_NAME = "Pitch Slap"

HITTING_CATS = ["R", "HR", "RBI", "SB", "OBP"]
PITCHING_CATS = ["K", "QS", "ERA", "WHIP", "SvHd"]

WEEKLY_ACQUISITION_LIMIT = 7
IP_MINIMUM_PER_WEEK = 15.0
WAIVER_TYPE = "priority"  # not FAAB
WAIVER_PROCESSING_DAYS = 1
LINEUP_LOCK = "weekly"  # Monday

ROSTER_SLOTS = {
    "C": 2, "1B": 1, "2B": 1, "3B": 1, "SS": 1,
    "MI": 1,   # 2B or SS
    "CI": 1,   # 1B or 3B
    "OF": 5,
    "UTIL": 2, # any hitter
    "P": 10,   # SP or RP
    "BN": 5,
    "IL": 3
}

PLAYOFFS = {
    "teams": 4,
    "rounds": 2,
    "weeks_per_round": 2,
    "tiebreaker": "h2h_record"
}
```

---

*Last updated: May 2026 — v2.0*
