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


def run_full() -> dict:
    """Full pipeline: crosswalk refresh + all fetchers + transform + validate + evaluate + report."""
    from pipeline.fetch_espn import run as fetch_espn
    from pipeline.fetch_mlb import run as fetch_mlb, get_two_start_pitchers
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
    _save_meta(meta)

    # Phase 2b: MLB schedule + transactions
    print("\n--- fetch_mlb ---")
    mlb_result = fetch_mlb()
    meta["mlb_last_fetch"] = _ts()
    meta["schedule_rows"] = mlb_result.get("schedule_rows", 0)
    _save_meta(meta)

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

    meta["last_full_run"] = start_ts
    meta["last_run_mode"] = "full"
    _save_meta(meta)

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

    meta["last_light_run"] = start_ts
    meta["last_run_mode"] = "light"
    _save_meta(meta)

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
