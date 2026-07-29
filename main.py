"""
pipeline.py -- Main entry point for the Pitch Slap pipeline.

Usage:
  python pipeline.py              # full run
  python pipeline.py --mode full  # same
  python pipeline.py --mode light # skip Statcast (uses cache) + skip crosswalk

Schedule (GitHub Actions):
  7am  ET: full
  12pm ET: light
  6pm  ET: light

Exit codes:
  0 -- success (pass or warn)
  1 -- validation failure (pipeline blocked)
  2 -- unexpected runtime error
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone

from config import DATA_DIR, DB_PATH

PIPELINE_META_PATH = os.path.join(DATA_DIR, "pipeline-meta.json")
# docs/data/ is the only dir the CI commit step persists across runs, so the
# ESPN-update history (used to learn ESPN's nightly stat-posting time) lives here.
ESPN_UPDATE_HISTORY_PATH = os.path.join(
    os.path.dirname(__file__), "docs", "data", "espn-update-history.json"
)


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_meta() -> dict:
    if os.path.exists(PIPELINE_META_PATH):
        with open(PIPELINE_META_PATH) as f:
            return json.load(f)
    return {}


def _save_meta(meta: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PIPELINE_META_PATH, "w") as f:
        json.dump(meta, f, indent=2)


def _append_espn_update_history(record: dict, cap: int = 60) -> None:
    """Append one run's freshness record to the rolling history (last `cap`)."""
    runs = []
    if os.path.exists(ESPN_UPDATE_HISTORY_PATH):
        try:
            with open(ESPN_UPDATE_HISTORY_PATH) as f:
                runs = json.load(f).get("runs", [])
        except Exception:
            runs = []
    runs.append(record)
    os.makedirs(os.path.dirname(ESPN_UPDATE_HISTORY_PATH), exist_ok=True)
    with open(ESPN_UPDATE_HISTORY_PATH, "w") as f:
        json.dump({"runs": runs[-cap:]}, f, indent=2)


def _heal_crosswalk() -> dict:
    """Self-heal newly-rostered players missing an MLB ID, before validate can
    hard-stop on them (crosswalk drift is what broke the 2026-07-01 run).
    Non-fatal — on any failure the pipeline continues and validate gates."""
    print("\n--- crosswalk self-heal ---")
    try:
        from pipeline.crosswalk_refresh import heal
        result = heal()
        if not result["checked"]:
            print("  all rostered players have MLB IDs — nothing to do")
        else:
            if result["resolved"]:
                print(f"  resolved {len(result['resolved'])} player(s): "
                      + ", ".join(f"{m['name']}({m['mlb_id']})" for m in result["resolved"]))
            if result["unresolved"]:
                print(f"  WARNING: {len(result['unresolved'])} still unresolved — "
                      "add to crosswalk-overrides.json: "
                      + ", ".join(f"{u['name']} [{u['note']}]" for u in result["unresolved"]))
        return result
    except Exception as e:
        print(f"  WARNING: crosswalk self-heal failed ({e}) — continuing; validate will gate.")
        return {"resolved": [], "unresolved": [], "checked": 0, "error": str(e)}


def _health_check(meta: dict) -> None:
    """Output completeness check (non-fatal). Writes docs/data/health.json; the
    workflow commits the data first, then fails the job (→ email) if degraded,
    so silent degradation surfaces same-day without losing the run's output."""
    print("\n--- health ---")
    try:
        from pipeline.health import run as health_run
        result = health_run()
        meta["health_overall"] = result["overall"]
        meta["health_degraded"] = result["degraded_checks"]
        for c in result["checks"]:
            if c["status"] == "degraded":
                print(f"  DEGRADED {c['check']}: {c['detail']}")
        print(f"  health: {result['overall'].upper()}")
    except Exception as e:
        print(f"  WARNING: health check failed ({e})")
    _save_meta(meta)


def run_full() -> dict:
    """Full pipeline: crosswalk refresh + all fetchers + transform + validate + evaluate + report."""
    from pipeline.fetch_espn import run as fetch_espn, get_espn_update_status
    from pipeline.fetch_mlb import run as fetch_mlb, get_two_start_pitchers, check_recent_games_settled
    from pipeline.fetch_statcast import run as fetch_statcast
    from pipeline.transform import run as transform
    from pipeline.validate import run as validate
    from pipeline.evaluate import run as evaluate
    from pipeline.report import run as report
    from datetime import date, timedelta

    meta = _load_meta()
    start_ts = _ts()
    print(f"[{start_ts}] FULL pipeline starting...")

    # Phase 2a: ESPN
    print("\n--- fetch_espn ---")
    espn_result = fetch_espn()
    meta["espn_last_fetch"] = _ts()
    meta["espn_rows"] = len(espn_result.get("roster_rows") or [])
    # Capture ESPN's own nightly update timestamp (when it last posted stats).
    espn_update = {}
    try:
        espn_update = get_espn_update_status()
        meta["espn_update"] = espn_update
        if espn_update.get("standings_update"):
            print(f"  ESPN stats last updated (standingsUpdateDate): {espn_update['standings_update']}")
    except Exception as e:
        print(f"  (espn update-status check skipped: {e})")
    _save_meta(meta)

    # Phase 2a+: self-heal crosswalk — resolve newly-rostered players missing an
    # MLB ID so validate.check_crosswalk_integrity doesn't hard-stop the run.
    heal_result = _heal_crosswalk()
    meta["crosswalk_heal"] = {"resolved": len(heal_result["resolved"]),
                              "unresolved": len(heal_result["unresolved"])}
    _save_meta(meta)

    # Phase 2b: MLB schedule + transactions
    print("\n--- fetch_mlb ---")
    mlb_result = fetch_mlb()
    meta["mlb_last_fetch"] = _ts()
    meta["schedule_rows"] = mlb_result.get("schedule_rows", 0)
    _save_meta(meta)

    # Soft freshness check: confirm yesterday's slate is Final (data should be settled).
    # Non-blocking — just warns; we never abort over it.
    settle = {}
    try:
        settle = check_recent_games_settled()
        meta["games_settled"] = settle
        if settle["all_settled"]:
            print(f"  Freshness OK — all {settle['games']} games on {settle['date']} Final "
                  f"(last: {settle['last_game'] or 'n/a'}).")
        else:
            print(f"  WARNING: {len(settle['incomplete'])}/{settle['games']} games on "
                  f"{settle['date']} not Final — stats may still be settling: "
                  f"{', '.join(settle['incomplete'][:5])}")
        _save_meta(meta)
    except Exception as e:
        print(f"  (freshness check skipped: {e})")

    # Record this run's freshness signals to the rolling history — over days this
    # reveals ESPN's nightly stat-update time (standings_update) and its variance.
    try:
        _append_espn_update_history({
            "run_ts":                 start_ts,
            "espn_standings_update":  espn_update.get("standings_update"),
            "espn_waiver_execution":  espn_update.get("waiver_last_execution"),
            "scoring_period":         espn_update.get("scoring_period"),
            "games_final_date":       settle.get("date"),
            "all_games_final":        settle.get("all_settled"),
            "last_game":              settle.get("last_game"),
        })
    except Exception as e:
        print(f"  (freshness history append skipped: {e})")

    # Phase 2c: Statcast + FanGraphs (7-day cache)
    print("\n--- fetch_statcast ---")
    sc_result = fetch_statcast(force=False)
    if not sc_result.get("skipped"):
        meta["statcast_last_fetch"] = _ts()
        meta["statcast_rows"] = sc_result.get("rows_written", 0)
        _save_meta(meta)

    # Phase 3: Transform
    print("\n--- transform ---")
    transform_result = transform()
    meta["transform_last_run"] = _ts()
    meta["transform_rows"] = transform_result.get("total_rows", 0)
    _save_meta(meta)

    # Phase 3b: Validate (blocking)
    print("\n--- validate ---")
    validation_report = validate(pipeline_meta=meta)
    meta["validate_last_run"] = _ts()
    meta["validate_overall"] = validation_report.get("overall", "unknown")
    _save_meta(meta)

    # Phase 4: Evaluate
    print("\n--- evaluate ---")
    two_starters = set(mlb_result.get("two_starter_ids", []))
    eval_report = evaluate(two_starter_mlb_ids=two_starters if two_starters else None)
    meta["evaluate_last_run"] = _ts()
    meta["cats_winning"] = eval_report["summary"]["cats_winning"]
    meta["cats_losing"]  = eval_report["summary"]["cats_losing"]
    _save_meta(meta)

    # Mark the run BEFORE report() so build_pipeline_log captures it in the
    # committed pipeline-log.json (data/pipeline-meta.json isn't committed by CI,
    # so the log file is the only place these timestamps persist).
    meta["last_full_run"] = start_ts
    meta["last_run_mode"] = "full"
    _save_meta(meta)

    # Phase 5: Report
    print("\n--- report ---")
    report()
    meta["report_last_run"] = _ts()

    # Phase 6: Trade analyzer (non-blocking — never fail the pipeline over trades)
    print("\n--- trades ---")
    try:
        from pipeline.trade import run as trades
        trades()
        meta["trades_last_run"] = _ts()
    except Exception as e:
        print(f"  WARNING: trade analyzer failed ({e}) — skipping trades.json")
    _save_meta(meta)

    # Phase 7: output health check (non-fatal; workflow alerts on degraded)
    _health_check(meta)

    print(f"\n[{_ts()}] Full pipeline complete.")
    return meta


def run_light() -> dict:
    """Light pipeline: ESPN + MLB (no Statcast pull) + transform + validate + evaluate + report."""
    from pipeline.fetch_espn import run as fetch_espn
    from pipeline.fetch_mlb import run as fetch_mlb
    from pipeline.fetch_statcast import run as fetch_statcast
    from pipeline.transform import run as transform
    from pipeline.validate import run as validate
    from pipeline.evaluate import run as evaluate
    from pipeline.report import run as report

    meta = _load_meta()
    start_ts = _ts()
    print(f"[{start_ts}] LIGHT pipeline starting...")

    print("\n--- fetch_espn ---")
    espn_result = fetch_espn()
    meta["espn_last_fetch"] = _ts()
    meta["espn_rows"] = len(espn_result.get("roster_rows") or [])
    _save_meta(meta)

    # Self-heal crosswalk before validate (same as the full run).
    heal_result = _heal_crosswalk()
    meta["crosswalk_heal"] = {"resolved": len(heal_result["resolved"]),
                              "unresolved": len(heal_result["unresolved"])}
    _save_meta(meta)

    print("\n--- fetch_mlb ---")
    mlb_result = fetch_mlb()
    meta["mlb_last_fetch"] = _ts()
    _save_meta(meta)

    # Statcast: honor cache (no force)
    print("\n--- fetch_statcast (cache check) ---")
    sc_result = fetch_statcast(force=False)
    if not sc_result.get("skipped"):
        meta["statcast_last_fetch"] = _ts()
        _save_meta(meta)

    print("\n--- transform ---")
    transform()
    meta["transform_last_run"] = _ts()
    _save_meta(meta)

    print("\n--- validate ---")
    validation_report = validate(pipeline_meta=meta)
    meta["validate_last_run"] = _ts()
    meta["validate_overall"] = validation_report.get("overall", "unknown")
    _save_meta(meta)

    print("\n--- evaluate ---")
    two_starters = set(mlb_result.get("two_starter_ids", []))
    eval_report = evaluate(two_starter_mlb_ids=two_starters if two_starters else None)
    meta["evaluate_last_run"] = _ts()
    meta["cats_winning"] = eval_report["summary"]["cats_winning"]
    _save_meta(meta)

    # Mark the run BEFORE report() so it lands in the committed pipeline-log.json.
    meta["last_light_run"] = start_ts
    meta["last_run_mode"] = "light"
    _save_meta(meta)

    print("\n--- report ---")
    report()
    meta["report_last_run"] = _ts()

    # Phase 6: Trade analyzer (non-blocking)
    print("\n--- trades ---")
    try:
        from pipeline.trade import run as trades
        trades()
        meta["trades_last_run"] = _ts()
    except Exception as e:
        print(f"  WARNING: trade analyzer failed ({e}) — skipping trades.json")
    _save_meta(meta)

    # Output health check (non-fatal; workflow alerts on degraded)
    _health_check(meta)

    print(f"\n[{_ts()}] Light pipeline complete.")
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Pitch Slap pipeline")
    parser.add_argument(
        "--mode",
        choices=["full", "light"],
        default="full",
        help="full (default): all fetchers + Statcast. light: ESPN + MLB + cached Statcast.",
    )
    args = parser.parse_args()

    try:
        if args.mode == "light":
            run_light()
        else:
            run_full()
        return 0
    except SystemExit as e:
        # validate.py sys.exit(1) on FAIL
        return int(e.code) if e.code is not None else 1
    except Exception:
        print(f"\nPIPELINE ERROR:", file=sys.stderr)
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
