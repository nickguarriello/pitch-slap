"""
validate.py — Pipeline Step 5 (BLOCKING)
Runs after transform.py, before evaluate.py.
Produces data/validation-report.json.
If any FAIL-level check fires, pipeline halts and last-known-good data is preserved.

Checks can be ADDED but never removed or weakened without explicit approval (CLAUDE.md).
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (
    DATA_DIR, DB_PATH,
    VALIDATION_BOUNDS, VALIDATION_COMPLETENESS,
    STALENESS_WARN_HOURS,
)

REPORT_PATH = os.path.join(DATA_DIR, "validation-report.json")


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

def _pass(name: str, detail: str = "", row_count: int = 0) -> dict:
    return {"check": name, "status": "pass", "detail": detail, "row_count": row_count}

def _warn(name: str, detail: str, row_count: int = 0) -> dict:
    return {"check": name, "status": "warn", "detail": detail, "row_count": row_count}

def _fail(name: str, detail: str, row_count: int = 0) -> dict:
    return {"check": name, "status": "fail", "detail": detail, "row_count": row_count}


# ---------------------------------------------------------------------------
# Schema checks
# ---------------------------------------------------------------------------

def check_schema_dim_players(conn: sqlite3.Connection) -> dict:
    name = "schema.dim_players"
    required = {"player_id", "mlb_id", "fg_id", "name", "position", "crosswalk_status"}
    cur = conn.execute("PRAGMA table_info(dim_players)")
    cols = {r[1] for r in cur.fetchall()}
    missing = required - cols
    if missing:
        return _fail(name, f"Missing columns: {missing}")
    return _pass(name)


def check_schema_fact_player_stats(conn: sqlite3.Connection) -> dict:
    name = "schema.fact_player_stats"
    required = {"player_id", "window", "stat_date", "r", "hr", "rbi", "sb", "obp",
                "k", "qs", "era", "whip", "svhd", "ip"}
    cur = conn.execute("PRAGMA table_info(fact_player_stats)")
    cols = {r[1] for r in cur.fetchall()}
    missing = required - cols
    if missing:
        return _fail(name, f"Missing columns: {missing}")
    return _pass(name)


def check_schema_fact_statcast(conn: sqlite3.Connection) -> dict:
    name = "schema.fact_statcast"
    required = {"player_id", "fetch_date", "xba", "xslg", "xwoba", "xfip", "siera",
                "barrel_pct", "exit_velo"}
    cur = conn.execute("PRAGMA table_info(fact_statcast)")
    cols = {r[1] for r in cur.fetchall()}
    missing = required - cols
    if missing:
        return _fail(name, f"Missing columns: {missing}")
    return _pass(name)


# ---------------------------------------------------------------------------
# Range checks — plausible stat bounds
# ---------------------------------------------------------------------------

def check_range_era(conn: sqlite3.Connection) -> dict:
    name = "range.era"
    lo, hi = VALIDATION_BOUNDS["era"]
    cur = conn.execute(
        "SELECT COUNT(*) FROM fact_player_stats WHERE era < ? AND ip > 20", (lo,)
    )
    neg = cur.fetchone()[0]
    if neg:
        return _fail(name, f"{neg} pitchers with negative ERA (> 20 IP)", neg)
    cur = conn.execute(
        "SELECT COUNT(*) FROM fact_player_stats WHERE era > 12 AND ip > 20"
    )
    high = cur.fetchone()[0]
    if high:
        return _warn(name, f"{high} pitchers with ERA > 12 (> 20 IP) — possible data error", high)
    return _pass(name)


def check_range_whip(conn: sqlite3.Connection) -> dict:
    name = "range.whip"
    lo, _ = VALIDATION_BOUNDS["whip"]
    cur = conn.execute(
        "SELECT COUNT(*) FROM fact_player_stats WHERE whip < ? AND ip > 5", (lo,)
    )
    neg = cur.fetchone()[0]
    if neg:
        return _fail(name, f"{neg} pitchers with negative WHIP", neg)
    cur = conn.execute(
        "SELECT COUNT(*) FROM fact_player_stats WHERE whip > 3.50 AND ip > 5"
    )
    high = cur.fetchone()[0]
    if high:
        return _warn(name, f"{high} pitchers with WHIP > 3.50", high)
    return _pass(name)


def check_range_obp(conn: sqlite3.Connection) -> dict:
    name = "range.obp"
    lo, hi = VALIDATION_BOUNDS["obp"]
    cur = conn.execute(
        "SELECT COUNT(*) FROM fact_player_stats WHERE (obp < ? OR obp > ?) AND pa > 50",
        (lo, hi)
    )
    bad = cur.fetchone()[0]
    if bad:
        return _fail(name, f"{bad} players with OBP outside [{lo}, {hi}] (> 50 PA)", bad)
    return _pass(name)


def check_range_ownership(conn: sqlite3.Connection) -> dict:
    name = "range.ownership"
    lo, hi = VALIDATION_BOUNDS["ownership"]
    cur = conn.execute(
        "SELECT COUNT(*) FROM fact_espn_rosters WHERE ownership_pct < ? OR ownership_pct > ?",
        (lo, hi)
    )
    bad = cur.fetchone()[0]
    if bad:
        return _fail(name, f"{bad} rows with ownership % outside [0, 100]", bad)
    return _pass(name)


def check_range_barrel_pct(conn: sqlite3.Connection) -> dict:
    name = "range.barrel_pct"
    _, hi = VALIDATION_BOUNDS["barrel_pct"]
    cur = conn.execute(
        "SELECT COUNT(*) FROM fact_statcast WHERE barrel_pct > ?", (hi,)
    )
    bad = cur.fetchone()[0]
    if bad:
        return _warn(name, f"{bad} players with barrel% > {hi}% — possible data error", bad)
    return _pass(name)


def check_range_xfip_siera(conn: sqlite3.Connection) -> dict:
    name = "range.xfip_siera"
    lo, hi = VALIDATION_BOUNDS["xfip"]
    siera_hi = VALIDATION_BOUNDS["siera"][1]
    # Require join to fact_player_stats to enforce minimum IP (10+ IP)
    cur = conn.execute(
        """
        SELECT COUNT(DISTINCT fs.player_id)
        FROM fact_statcast fs
        JOIN fact_player_stats ps ON fs.player_id = ps.player_id AND ps.window = 'season'
        WHERE ps.ip > 10
          AND (fs.xfip < ? OR fs.xfip > ? OR fs.siera < ? OR fs.siera > ?)
        """,
        (lo, hi, lo, siera_hi)
    )
    bad = cur.fetchone()[0]
    if bad:
        return _warn(name, f"{bad} pitchers (>10 IP) with xFIP/SIERA outside bounds — review", bad)
    return _pass(name)


# ---------------------------------------------------------------------------
# Completeness checks
# ---------------------------------------------------------------------------

def check_completeness_rosters(conn: sqlite3.Connection) -> dict:
    name = "completeness.rosters"
    min_teams = VALIDATION_COMPLETENESS["expected_rosters"]
    cur = conn.execute(
        "SELECT COUNT(DISTINCT espn_team_id) FROM fact_espn_rosters "
        "WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)"
    )
    n = cur.fetchone()[0]
    if n == 0:
        return _fail(name, "0 teams returned — ESPN auth failure (cookie expired)", 0)
    if n < min_teams:
        return _fail(name, f"Only {n}/{min_teams} team rosters returned", n)
    return _pass(name, f"{n} team rosters", n)


def check_completeness_my_roster(conn: sqlite3.Connection) -> dict:
    name = "completeness.my_roster"
    from config import TEAM_ID
    min_players = VALIDATION_COMPLETENESS["min_active_players"]
    cur = conn.execute(
        "SELECT COUNT(*) FROM fact_espn_rosters "
        "WHERE espn_team_id = ? AND snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)",
        (TEAM_ID,)
    )
    n = cur.fetchone()[0]
    if n < min_players:
        return _warn(name, f"Only {n} players on my roster (expected >= {min_players})", n)
    return _pass(name, f"{n} players on roster", n)


def check_completeness_fa_pool(conn: sqlite3.Connection) -> dict:
    name = "completeness.fa_pool"
    min_fa = VALIDATION_COMPLETENESS["min_fa_pool"]
    cur = conn.execute(
        "SELECT COUNT(*) FROM fact_espn_rosters "
        "WHERE espn_team_id IS NULL AND snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)"
    )
    n = cur.fetchone()[0]
    if n < 50:
        return _fail(name, f"FA pool has only {n} players — likely API failure", n)
    if n < min_fa:
        return _warn(name, f"FA pool has {n} players (expected > {min_fa})", n)
    return _pass(name, f"{n} FA players", n)


def check_completeness_statcast(conn: sqlite3.Connection) -> dict:
    name = "completeness.statcast"
    min_sc = VALIDATION_COMPLETENESS["min_statcast"]
    cur = conn.execute(
        "SELECT COUNT(DISTINCT player_id) FROM fact_statcast "
        "WHERE fetch_date = (SELECT MAX(fetch_date) FROM fact_statcast)"
    )
    n = cur.fetchone()[0]
    if n == 0:
        return _warn(name, "No Statcast data — using cached values", 0)
    if n < min_sc:
        return _warn(name, f"Statcast covers {n} players (expected > {min_sc})", n)
    return _pass(name, f"{n} players with Statcast data", n)


# ---------------------------------------------------------------------------
# Crosswalk integrity
# ---------------------------------------------------------------------------

def check_crosswalk_integrity(conn: sqlite3.Connection) -> dict:
    name = "crosswalk.integrity"
    fail_threshold = VALIDATION_COMPLETENESS["crosswalk_fail_threshold"]
    cur = conn.execute(
        """
        SELECT COUNT(DISTINCT fer.player_id)
        FROM fact_espn_rosters fer
        LEFT JOIN dim_players dp ON fer.player_id = dp.player_id
        WHERE dp.mlb_id IS NULL
          AND fer.espn_team_id IS NOT NULL
          AND fer.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)
        """
    )
    unmatched = cur.fetchone()[0]
    if unmatched == 0:
        return _pass(name, "All rostered players have matched MLB IDs")
    if unmatched > fail_threshold:
        return _fail(name, f"{unmatched} rostered players without MLB ID (threshold: {fail_threshold})", unmatched)
    return _warn(name, f"{unmatched} rostered players without MLB ID — check data-gap section", unmatched)


# ---------------------------------------------------------------------------
# Ground truth spot checks
# ---------------------------------------------------------------------------

def check_ground_truth_era(conn: sqlite3.Connection, espn_era: float | None = None) -> dict:
    name = "ground_truth.era"
    if espn_era is None:
        return _warn(name, "ESPN ERA not provided for comparison — skipping ground truth check")
    cur = conn.execute(
        "SELECT AVG(era) FROM fact_player_stats "
        "WHERE window = 'season' AND era IS NOT NULL AND ip > 0"
    )
    our_era = cur.fetchone()[0]
    if our_era is None:
        return _warn(name, "No ERA data available for comparison")
    diff = abs(our_era - espn_era)
    if diff > 0.10:
        return _warn(name, f"ERA divergence: ours={our_era:.3f}, ESPN={espn_era:.3f}, diff={diff:.3f}")
    return _pass(name, f"ERA within tolerance: ours={our_era:.3f}, ESPN={espn_era:.3f}")


def check_ground_truth_svhd(conn: sqlite3.Connection, espn_svhd: float | None = None) -> dict:
    name = "ground_truth.svhd"
    if espn_svhd is None:
        return _warn(name, "ESPN SvHd not provided for comparison — skipping ground truth check")
    cur = conn.execute(
        "SELECT SUM(svhd) FROM fact_player_stats "
        "WHERE window = 'season' AND svhd IS NOT NULL"
    )
    our_svhd = cur.fetchone()[0] or 0
    if our_svhd != espn_svhd:
        return _warn(name, f"SvHd mismatch: ours={our_svhd}, ESPN={espn_svhd} — check SV+HLD combination")
    return _pass(name, f"SvHd matches ESPN: {our_svhd}")


def check_ground_truth_obp_definition(conn: sqlite3.Connection) -> dict:
    """Flag implausibly low OBP values that may indicate BA is being stored instead."""
    name = "ground_truth.obp_definition"
    # < 0.180 over 150+ PA is statistically almost impossible in MLB (not just bad)
    cur = conn.execute(
        "SELECT COUNT(*) FROM fact_player_stats "
        "WHERE window = 'season' AND obp IS NOT NULL AND obp < 0.180 AND pa > 150"
    )
    suspicious = cur.fetchone()[0]
    if suspicious:
        return _warn(name, f"{suspicious} players with OBP < .180 over 150+ PA — confirm OBP definition matches ESPN")
    return _pass(name)


# ---------------------------------------------------------------------------
# Staleness checks
# ---------------------------------------------------------------------------

def check_staleness_espn(pipeline_meta: dict, conn: sqlite3.Connection) -> dict:
    name = "staleness.espn"
    # Prefer pipeline_meta timestamp; fall back to most recent snapshot_date in DB
    last_fetch = pipeline_meta.get("espn_last_fetch")
    if not last_fetch:
        row = conn.execute(
            "SELECT MAX(snapshot_date) FROM fact_espn_rosters"
        ).fetchone()
        last_fetch = (row[0] + "T00:00:00") if row and row[0] else None
    if not last_fetch:
        return _warn(name, "No ESPN fetch timestamp found")
    age_h = _hours_since(last_fetch)
    if age_h > STALENESS_WARN_HOURS:
        return _warn(name, f"ESPN data is {age_h:.1f}h old (threshold: {STALENESS_WARN_HOURS}h)")
    return _pass(name, f"ESPN data is {age_h:.1f}h old")


def check_staleness_statcast(pipeline_meta: dict, conn: sqlite3.Connection) -> dict:
    name = "staleness.statcast"
    last_fetch = pipeline_meta.get("statcast_last_fetch")
    if not last_fetch:
        row = conn.execute(
            "SELECT MAX(fetch_date) FROM fact_statcast"
        ).fetchone()
        last_fetch = (row[0] + "T00:00:00") if row and row[0] else None
    if not last_fetch:
        return _warn(name, "No Statcast fetch timestamp found")
    age_h = _hours_since(last_fetch)
    if age_h > STALENESS_WARN_HOURS * 24:   # Statcast is weekly -- warn at 25 days
        return _warn(name, f"Statcast data is {age_h/24:.1f} days old")
    return _pass(name, f"Statcast data is {age_h:.1f}h old")


def check_completeness_fact_player_stats(conn: sqlite3.Connection) -> dict:
    """All required windows must have rows. 'current' may be 0 on week-start Mondays."""
    from datetime import date
    name = "completeness.fact_player_stats"
    required_windows = {"season", "30d", "14d", "current"}
    rows = conn.execute(
        "SELECT window, COUNT(*) FROM fact_player_stats GROUP BY window"
    ).fetchall()
    present   = {r[0] for r in rows}
    counts    = {r[0]: r[1] for r in rows}
    missing   = required_windows - present
    # 'current' window is legitimately empty on Mondays (week just started, no games yet)
    is_monday = date.today().weekday() == 0
    if missing == {"current"} and is_monday:
        missing = set()
        present.add("current")
        counts["current"] = 0
    if missing:
        return _fail(name, f"Missing windows: {missing} — transform.py may not have run", 0)
    non_current = {w: c for w, c in counts.items() if w != "current"}
    min_rows = min(non_current.values()) if non_current else 0
    if min_rows < 100:
        return _warn(name, f"Smallest non-current window has only {min_rows} rows — transform output thin", min_rows)
    total = sum(counts.values())
    return _pass(name, f"{total} rows across {len(present)} windows", total)


def check_completeness_schedule(conn: sqlite3.Connection) -> dict:
    """fact_schedule must have upcoming game rows."""
    name = "completeness.schedule"
    from datetime import date
    today = date.today().isoformat()
    cur = conn.execute(
        "SELECT COUNT(*) FROM fact_schedule WHERE game_date >= ?", (today,)
    )
    n = cur.fetchone()[0]
    if n == 0:
        return _fail(name, "No upcoming schedule rows — fetch_mlb.py may not have run", 0)
    if n < 20:
        return _warn(name, f"Only {n} schedule rows for today+ — schedule may be incomplete", n)
    return _pass(name, f"{n} upcoming team-game slots", n)


def check_transform_completeness(conn: sqlite3.Connection) -> dict:
    """Season window must cover the majority of rostered players."""
    name = "completeness.transform_coverage"
    rostered = conn.execute(
        "SELECT COUNT(DISTINCT player_id) FROM fact_espn_rosters "
        "WHERE espn_team_id IS NOT NULL AND snapshot_date = (SELECT MAX(snapshot_date) FROM fact_espn_rosters)"
    ).fetchone()[0]
    if rostered == 0:
        return _warn(name, "No rostered players in fact_espn_rosters — run fetch_espn.py first")

    covered = conn.execute(
        "SELECT COUNT(DISTINCT fps.player_id) FROM fact_player_stats fps "
        "JOIN fact_espn_rosters fer ON fps.player_id = fer.player_id "
        "WHERE fps.window = 'season' AND fer.espn_team_id IS NOT NULL"
    ).fetchone()[0]

    pct = covered / rostered * 100
    if pct < 60:
        return _fail(name, f"Only {covered}/{rostered} ({pct:.0f}%) rostered players in transform output", covered)
    if pct < 80:
        return _warn(name, f"{covered}/{rostered} ({pct:.0f}%) rostered players covered — some missing stats", covered)
    return _pass(name, f"{covered}/{rostered} ({pct:.0f}%) rostered players have season stats", covered)


def _hours_since(ts_str: str) -> float:
    try:
        ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    except Exception:
        return 9999.0


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run(
    pipeline_meta: dict | None = None,
    espn_era: float | None = None,
    espn_svhd: float | None = None,
) -> dict:
    """
    Run all validation checks. Returns a report dict.
    Raises SystemExit if any FAIL-level check fires (blocking pipeline).
    """
    if pipeline_meta is None:
        pipeline_meta = {}

    conn    = sqlite3.connect(DB_PATH)
    results = []

    # Schema
    results += [
        check_schema_dim_players(conn),
        check_schema_fact_player_stats(conn),
        check_schema_fact_statcast(conn),
    ]

    # Ranges
    results += [
        check_range_era(conn),
        check_range_whip(conn),
        check_range_obp(conn),
        check_range_ownership(conn),
        check_range_barrel_pct(conn),
        check_range_xfip_siera(conn),
    ]

    # Completeness
    results += [
        check_completeness_rosters(conn),
        check_completeness_my_roster(conn),
        check_completeness_fa_pool(conn),
        check_completeness_statcast(conn),
        check_completeness_fact_player_stats(conn),
        check_completeness_schedule(conn),
        check_transform_completeness(conn),
    ]

    # Crosswalk
    results += [
        check_crosswalk_integrity(conn),
    ]

    # Ground truth
    results += [
        check_ground_truth_era(conn, espn_era),
        check_ground_truth_svhd(conn, espn_svhd),
        check_ground_truth_obp_definition(conn),
    ]

    # Staleness
    results += [
        check_staleness_espn(pipeline_meta, conn),
        check_staleness_statcast(pipeline_meta, conn),
    ]

    conn.close()

    # Persist to validation_log table
    _log_to_db(results)

    # Write validation-report.json
    report = _build_report(results, pipeline_meta)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    _print_summary(results)

    # Block pipeline on any FAIL
    fails = [r for r in results if r["status"] == "fail"]
    if fails:
        print(f"\nPIPELINE BLOCKED — {len(fails)} critical check(s) failed:")
        for r in fails:
            print(f"  FAIL  {r['check']}: {r['detail']}")
        sys.exit(1)

    return report


def _build_report(results: list[dict], pipeline_meta: dict) -> dict:
    statuses = [r["status"] for r in results]
    overall  = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    return {
        "run_ts":   datetime.now(timezone.utc).isoformat(),
        "overall":  overall,
        "checks":   results,
        "meta":     pipeline_meta,
    }


def _log_to_db(results: list[dict]) -> None:
    try:
        conn = sqlite3.connect(DB_PATH)
        ts   = datetime.now(timezone.utc).isoformat()
        conn.executemany(
            "INSERT INTO validation_log (run_ts, check_name, status, detail, row_count) "
            "VALUES (?, ?, ?, ?, ?)",
            [(ts, r["check"], r["status"], r.get("detail", ""), r.get("row_count", 0))
             for r in results],
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Warning: could not write to validation_log: {e}")


def _print_summary(results: list[dict]) -> None:
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for r in results:
        counts[r["status"]] += 1
        if r["status"] != "pass":
            print(f"  {r['status'].upper():4s}  {r['check']}: {r['detail']}")
    print(f"Validation: {counts['pass']} pass | {counts['warn']} warn | {counts['fail']} fail")


if __name__ == "__main__":
    run()
