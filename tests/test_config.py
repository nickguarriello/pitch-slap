"""Config invariants. A bad date or missing category here silently corrupts
week math, windows, and scoring — the ASG-break off-by-one lived in this area."""

from datetime import date

import config as C


def _d(s):
    return date.fromisoformat(s)


def test_season_dates_parse_and_order():
    for attr in ("SEASON_START", "WEEK_1_START", "WEEK_1_END", "WEEK_2_START",
                 "FIRST_HALF_END", "ASG_BREAK_END"):
        _d(getattr(C, attr))  # raises if not ISO
    assert _d(C.WEEK_1_START) <= _d(C.WEEK_1_END) < _d(C.WEEK_2_START)
    assert _d(C.FIRST_HALF_END) < _d(C.ASG_BREAK_END)


def test_categories():
    assert C.HITTING_CATS and C.PITCHING_CATS
    assert set(C.HITTING_CATS) == {"R", "HR", "RBI", "SB", "OBP"}
    assert set(C.PITCHING_CATS) == {"K", "QS", "ERA", "WHIP", "SvHd"}
    # no accidental overlap
    assert not (set(C.HITTING_CATS) & set(C.PITCHING_CATS))


def test_roster_slots():
    assert isinstance(C.ROSTER_SLOTS, dict) and C.ROSTER_SLOTS
    assert all(isinstance(v, int) and v >= 0 for v in C.ROSTER_SLOTS.values())
    for slot in ("P", "BN", "OF"):
        assert slot in C.ROSTER_SLOTS


def test_scalars():
    assert isinstance(C.LEAGUE_ID, int) and C.LEAGUE_ID > 0
    assert isinstance(C.TEAM_ID, int)
    assert isinstance(C.SEASON, int) and C.SEASON >= 2024
    assert isinstance(C.PLAYOFF_START_WEEK, int) and C.PLAYOFF_START_WEEK > 1
    assert C.IP_MINIMUM_PER_WEEK > 0
    assert C.WEEKLY_ACQUISITION_LIMIT > 0


def test_playoffs_shape():
    for k in ("teams", "rounds", "weeks_per_round"):
        assert k in C.PLAYOFFS and isinstance(C.PLAYOFFS[k], int)
