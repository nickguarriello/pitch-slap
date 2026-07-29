"""
pipeline/health.py — output completeness / health checks.

validate.py gates the pipeline on data-INTEGRITY failures and hard-stops the
run. These checks are different and complementary: they run on the FINISHED
output at the end of a run and catch *silent degradation* — a run that
"succeeded" and committed but whose dashboard is actually incomplete. Every
check maps to a failure this project has actually shipped silently:

  matchup_populated  — "This Week's Matchup" cat_states aren't (mostly) UNKNOWN.
                       The ASG-break off-by-one broke this for 16 days with zero
                       signal (validate passed the whole time).
  statcast_fresh     — fact_statcast isn't stale (pybaseball breakage / cache).
  stat_rows          — per-window stat + roster counts didn't collapse.
  projections        — ESPN espn_proj_* actually populated (the June null-proj bug).

Contract with the workflow (user chose "commit + email"): the pipeline always
commits whatever it produced, then the workflow reads docs/data/health.json and
fails the job (→ GitHub email) iff overall == "degraded". So data is never lost
and a degraded run still alerts same-day. This module is non-fatal to the run.

Run standalone against the committed output:  python -m pipeline.health
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DB_PATH

_ROOT = os.path.dirname(os.path.dirname(__file__))
DOCS_DATA_DIR = os.path.join(_ROOT, "docs", "data")

# thresholds (calibrated against healthy output 2026-07-29)
MIN_KNOWN_CAT_STATES = 6      # of 10; a healthy matchup has ~10, the broken one had 2
STATCAST_STALE_DAYS  = 10
MIN_ROSTER_ROWS      = 100
MIN_SEASON_STAT_ROWS = 300
MIN_STATCAST_ROWS    = 100
MIN_PROJ_FRACTION    = 0.10   # near-total absence of projections = the bug


def _ok(name, detail):        return {"check": name, "status": "ok",       "detail": detail}
def _degraded(name, detail):  return {"check": name, "status": "degraded", "detail": detail}


def _load_json(name: str):
    with open(os.path.join(DOCS_DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_matchup_populated() -> dict:
    """The current matchup must have real per-category values, not all UNKNOWN."""
    name = "matchup_populated"
    try:
        cs = _load_json("matchup.json").get("cat_states", {})
    except Exception as e:
        return _degraded(name, f"could not read matchup.json cat_states ({e})")
    if not cs:
        return _degraded(name, "matchup.json has no cat_states")
    known = [c for c, v in cs.items() if v.get("state") != "UNKNOWN"]
    if len(known) < MIN_KNOWN_CAT_STATES:
        unknown = [c for c, v in cs.items() if v.get("state") == "UNKNOWN"]
        return _degraded(name, f"only {len(known)}/{len(cs)} categories have a known "
                               f"state — UNKNOWN: {', '.join(unknown)} "
                               "(current matchup values not populated)")
    return _ok(name, f"{len(known)}/{len(cs)} categories populated")


def check_statcast_fresh() -> dict:
    name = "statcast_fresh"
    try:
        last = _load_json("pipeline-log.json").get("statcast_last_fetch")
    except Exception as e:
        return _degraded(name, f"could not read pipeline-log.json ({e})")
    if not last:
        return _degraded(name, "no statcast_last_fetch recorded")
    try:
        age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).days
    except Exception as e:
        return _degraded(name, f"unparseable statcast_last_fetch '{last}' ({e})")
    if age_days > STATCAST_STALE_DAYS:
        return _degraded(name, f"fact_statcast is {age_days}d old (threshold {STATCAST_STALE_DAYS}d)")
    return _ok(name, f"statcast {age_days}d old")


def check_stat_rows() -> dict:
    name = "stat_rows"
    try:
        rc = _load_json("pipeline-log.json").get("row_counts", {})
    except Exception as e:
        return _degraded(name, f"could not read pipeline-log.json row_counts ({e})")
    problems = []
    if rc.get("fact_espn_rosters", 0) < MIN_ROSTER_ROWS:
        problems.append(f"rosters {rc.get('fact_espn_rosters')} < {MIN_ROSTER_ROWS}")
    if rc.get("stats_season", 0) < MIN_SEASON_STAT_ROWS:
        problems.append(f"season stats {rc.get('stats_season')} < {MIN_SEASON_STAT_ROWS}")
    if rc.get("fact_statcast", 0) < MIN_STATCAST_ROWS:
        problems.append(f"statcast {rc.get('fact_statcast')} < {MIN_STATCAST_ROWS}")
    if problems:
        return _degraded(name, "; ".join(problems))
    return _ok(name, f"rosters={rc.get('fact_espn_rosters')} season={rc.get('stats_season')} "
                     f"statcast={rc.get('fact_statcast')}")


def check_projections(db_path: str = DB_PATH) -> dict:
    """ESPN projections populated for the rostered pool (graceful if DB unavailable)."""
    name = "projections"
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN espn_proj_r IS NOT NULL OR espn_proj_k IS NOT NULL
                            THEN 1 ELSE 0 END) AS populated
            FROM fact_espn_rosters
            WHERE espn_team_id IS NOT NULL
              AND snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)
            """
        ).fetchone()
        conn.close()
    except Exception as e:
        return _ok(name, f"skipped (DB unavailable: {e})")
    total, populated = (row[0] or 0), (row[1] or 0)
    if total == 0:
        return _ok(name, "skipped (no rostered rows in DB)")
    frac = populated / total
    if frac < MIN_PROJ_FRACTION:
        return _degraded(name, f"only {populated}/{total} rostered players have ESPN "
                               f"projections ({frac:.0%} < {MIN_PROJ_FRACTION:.0%})")
    return _ok(name, f"{populated}/{total} rostered have projections ({frac:.0%})")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(db_path: str = DB_PATH, write: bool = True) -> dict:
    checks = [
        check_matchup_populated(),
        check_statcast_fresh(),
        check_stat_rows(),
        check_projections(db_path),
    ]
    degraded = [c for c in checks if c["status"] == "degraded"]
    result = {
        "run_ts": datetime.now(timezone.utc).isoformat(),
        "overall": "degraded" if degraded else "ok",
        "degraded_checks": [c["check"] for c in degraded],
        "checks": checks,
    }
    if write:
        os.makedirs(DOCS_DATA_DIR, exist_ok=True)
        with open(os.path.join(DOCS_DATA_DIR, "health.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
    return result


def _print(result: dict) -> None:
    icon = {"ok": "OK  ", "degraded": "DEGRADED"}
    for c in result["checks"]:
        print(f"  {icon.get(c['status'], c['status']):8} {c['check']}: {c['detail']}")
    print(f"  => overall: {result['overall'].upper()}")


if __name__ == "__main__":
    res = run(write=False)
    _print(res)
    sys.exit(1 if res["overall"] == "degraded" else 0)
