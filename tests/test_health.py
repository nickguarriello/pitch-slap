"""Codifies the health-check branches that were hand-verified when the layer was
built — so a future refactor can't silently reopen the silent-degradation hole.

Covers the airtight matchup logic (values / empty / no-rows-in-season /
offseason / report-side bug), output_files, and the validate surface."""

import json
import sqlite3

from pipeline import health

CATS = ["R", "HR", "RBI", "SB", "OBP", "K", "QS", "ERA", "WHIP", "SvHd"]


def _mkdb(path, rows):
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE matchup_snapshots "
              "(snapshot_ts TEXT, week INT, cat_name TEXT, my_val REAL, opp_val REAL)")
    for cat, mv, ov in rows:
        c.execute("INSERT INTO matchup_snapshots VALUES ('t1',17,?,?,?)", (cat, mv, ov))
    c.commit()
    c.close()


def _write_matchup(dirpath, state):
    json.dump({"cat_states": {c: {"state": state} for c in CATS}},
              open(dirpath / "matchup.json", "w"))


# --- matchup_populated branches --------------------------------------------

def test_matchup_values_present_ok(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    _mkdb(db, [(c, 1.0, 2.0) for c in CATS])
    monkeypatch.setattr(health, "DOCS_DATA_DIR", str(tmp_path))
    _write_matchup(tmp_path, "WIN")
    assert health.check_matchup_populated(str(db))["status"] == "ok"


def test_matchup_rows_but_empty_degraded(tmp_path):
    db = tmp_path / "d.db"
    _mkdb(db, [(c, None, None) for c in CATS])
    assert health.check_matchup_populated(str(db))["status"] == "degraded"


def test_matchup_no_rows_in_season_degraded(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    _mkdb(db, [])
    monkeypatch.setattr(health, "_in_season", lambda d: True)
    assert health.check_matchup_populated(str(db))["status"] == "degraded"


def test_matchup_no_rows_offseason_ok(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    _mkdb(db, [])
    monkeypatch.setattr(health, "_in_season", lambda d: False)
    assert health.check_matchup_populated(str(db))["status"] == "ok"


def test_matchup_report_bug_degraded(tmp_path, monkeypatch):
    """DB has values but the dashboard JSON shows all UNKNOWN -> report bug."""
    db = tmp_path / "d.db"
    _mkdb(db, [(c, 1.0, 2.0) for c in CATS])
    monkeypatch.setattr(health, "DOCS_DATA_DIR", str(tmp_path))
    _write_matchup(tmp_path, "UNKNOWN")
    assert health.check_matchup_populated(str(db))["status"] == "degraded"


# --- output_files -----------------------------------------------------------

def test_output_files_missing_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "DOCS_DATA_DIR", str(tmp_path))
    assert health.check_output_files()["status"] == "degraded"


def test_output_files_present_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "DOCS_DATA_DIR", str(tmp_path))
    for fn in health._EXPECTED_FILES:
        json.dump({"x": 1}, open(tmp_path / fn, "w"))
    assert health.check_output_files()["status"] == "ok"


# --- validation surface -----------------------------------------------------

def test_validation_fail_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "DOCS_DATA_DIR", str(tmp_path))
    json.dump({"validate_overall": "fail"}, open(tmp_path / "pipeline-log.json", "w"))
    assert health.check_validation()["status"] == "degraded"


def test_validation_warn_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "DOCS_DATA_DIR", str(tmp_path))
    json.dump({"validate_overall": "warn"}, open(tmp_path / "pipeline-log.json", "w"))
    assert health.check_validation()["status"] == "ok"


# --- overall aggregation ----------------------------------------------------

def test_run_overall_degraded_when_any_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "DOCS_DATA_DIR", str(tmp_path))  # no files -> output_files degraded
    db = tmp_path / "d.db"
    _mkdb(db, [(c, 1.0, 2.0) for c in CATS])
    res = health.run(db_path=str(db), write=False)
    assert res["overall"] == "degraded"
    assert "output_files" in res["degraded_checks"]
