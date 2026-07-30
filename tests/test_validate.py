"""Ground-truth ESPN comparison check."""

import json
import sqlite3

from pipeline import validate


def _setup(tmp_path, monkeypatch, our_era, espn_era):
    """One rostered pitcher with plenty of IP; our vs ESPN ERA supplied."""
    db = tmp_path / "s.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE fact_player_stats (player_id TEXT, window TEXT, era REAL, "
              "whip REAL, obp REAL, ip REAL, pa REAL)")
    c.execute("INSERT INTO fact_player_stats VALUES ('100','season',?,1.10,NULL,120,NULL)",
              (our_era,))
    c.commit()
    monkeypatch.setattr(validate, "DATA_DIR", str(tmp_path))
    json.dump({"100": {"era": espn_era, "whip": 1.10, "ip": 120}},
              open(tmp_path / "espn-actuals.json", "w"))
    return c


def test_ground_truth_pass_when_matching(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, our_era=3.20, espn_era=3.21)  # within tol
    r = validate.check_ground_truth_espn(c)
    assert r["status"] == "pass"


def test_ground_truth_warn_on_drift(tmp_path, monkeypatch):
    c = _setup(tmp_path, monkeypatch, our_era=3.20, espn_era=4.10)  # 0.90 > 0.20 tol
    r = validate.check_ground_truth_espn(c)
    assert r["status"] == "warn"


def test_ground_truth_warn_when_file_missing(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE fact_player_stats (player_id TEXT, window TEXT, era REAL, "
              "whip REAL, obp REAL, ip REAL, pa REAL)")
    monkeypatch.setattr(validate, "DATA_DIR", str(tmp_path / "nope"))
    r = validate.check_ground_truth_espn(c)
    assert r["status"] == "warn"
