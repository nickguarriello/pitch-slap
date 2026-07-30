"""Report-layer computations that feed the dashboard."""

import sqlite3

from pipeline import report


def _mk_matchups(path, rows):
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE fact_matchups (season INT, week INT, my_team_id INT, "
              "opp_team_id INT, cat_name TEXT, my_val REAL, opp_val REAL, result TEXT, is_final INT)")
    for wk, cat, mv, ov, res in rows:
        c.execute("INSERT INTO fact_matchups VALUES (2026,?,?,?,?,?,?,?,1)",
                  (wk, report.TEAM_ID, 2, cat, mv, ov, res))
    c.commit()
    c.close()


def test_category_profile_classifies(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    rows = (
        [(w, "R", 5, 3, "W") for w in (1, 2, 3)] +        # always win  -> STRENGTH
        [(w, "HR", 1, 3, "L") for w in (1, 2, 3)] +        # always lose -> WEAKNESS
        [(1, "K", 5, 4, "W"), (2, "K", 3, 5, "L"), (3, "K", 4, 4, "T")]  # mixed -> SWING
    )
    _mk_matchups(db, rows)
    monkeypatch.setattr(report, "DB_PATH", str(db))

    prof = report._compute_category_profile()
    assert prof["R"]["class"] == "STRENGTH" and prof["R"]["win_rate"] == 1.0
    assert prof["HR"]["class"] == "WEAKNESS" and prof["HR"]["win_rate"] == 0.0
    assert prof["K"]["class"] == "SWING"
    assert prof["R"]["record"] == "3-0-0"
    # margin is signed in my favor (R: +2 avg)
    assert prof["R"]["avg_margin"] == 2.0


def test_category_profile_empty_when_no_history(tmp_path, monkeypatch):
    db = tmp_path / "empty.db"
    _mk_matchups(db, [])
    monkeypatch.setattr(report, "DB_PATH", str(db))
    assert report._compute_category_profile() == {}
