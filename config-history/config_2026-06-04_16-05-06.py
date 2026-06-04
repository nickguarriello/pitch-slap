"""
Pitch Slap Decision Engine — config.py
League: ESPN H2H Categories | League #1985887220 | Season 2026
IMPORTANT: Do not edit without first saving a snapshot to config-history/.
"""

import hashlib, shutil, os
from datetime import datetime

# ---------------------------------------------------------------------------
# League
# ---------------------------------------------------------------------------

LEAGUE_ID   = 1985887220
TEAM_ID     = 1
SEASON      = 2026
TEAM_NAME   = "Pitch Slap"

SEASON_START   = "2026-03-25"   # Week 1 Monday
WEEK_1_START   = "2026-03-25"
WEEK_1_END     = "2026-04-05"   # First week was 12 days
WEEK_2_START   = "2026-04-06"   # Standard 7-day weeks from here

# ---------------------------------------------------------------------------
# Scoring categories
# ---------------------------------------------------------------------------

HITTING_CATS = ["R", "HR", "RBI", "SB", "OBP"]
PITCHING_CATS = ["K", "QS", "ERA", "WHIP", "SvHd"]
ALL_CATS = HITTING_CATS + PITCHING_CATS

CATS_LOWER_IS_BETTER = {"ERA", "WHIP"}   # all others: higher is better

H2H_CATEGORIES = {
    "hitters": [
        {"name": "R",   "display": "Runs",             "stat_key": "runs",          "higher_is_better": True},
        {"name": "HR",  "display": "Home Runs",         "stat_key": "home_runs",     "higher_is_better": True},
        {"name": "RBI", "display": "RBIs",              "stat_key": "rbis",          "higher_is_better": True},
        {"name": "SB",  "display": "Stolen Bases",      "stat_key": "stolen_bases",  "higher_is_better": True},
        {"name": "OBP", "display": "On-Base Percentage","stat_key": "obp",           "higher_is_better": True},
    ],
    "pitchers": [
        {"name": "K",    "display": "Strikeouts",    "stat_key": "strikeouts",     "higher_is_better": True},
        {"name": "QS",   "display": "Quality Starts","stat_key": "quality_starts", "higher_is_better": True},
        {"name": "ERA",  "display": "ERA",            "stat_key": "era",            "higher_is_better": False},
        {"name": "WHIP", "display": "WHIP",           "stat_key": "whip",           "higher_is_better": False},
        {"name": "SvHd", "display": "Saves + Holds",  "stat_key": "svhd",           "higher_is_better": True},
    ],
}

# ---------------------------------------------------------------------------
# Roster slots
# ---------------------------------------------------------------------------

ROSTER_SLOTS = {
    "C":    2,
    "1B":   1,
    "2B":   1,
    "3B":   1,
    "SS":   1,
    "MI":   1,   # 2B or SS
    "CI":   1,   # 1B or 3B
    "OF":   5,
    "UTIL": 2,   # any hitter
    "P":    10,  # SP or RP
    "BN":   5,
    "IL":   3,
}

FLEX_ELIGIBILITY = {
    "MI":   {"2B", "SS"},
    "CI":   {"1B", "3B"},
    "UTIL": {"C", "1B", "2B", "3B", "SS", "OF", "DH"},
    "P":    {"SP", "RP"},
}

# ---------------------------------------------------------------------------
# Weekly constraints
# ---------------------------------------------------------------------------

WEEKLY_ACQUISITION_LIMIT = 7
IP_MINIMUM_PER_WEEK      = 15.0
WAIVER_TYPE              = "priority"
WAIVER_PROCESSING_DAYS   = 1
LINEUP_LOCK              = "weekly"    # Monday

# Acquisition urgency thresholds
ACQ_URGENCY_WARN  = 5   # at 5+ used: flag high-need only
ACQ_URGENCY_LAST  = 6   # at 6 used: "last move" warning

# IP pace thresholds
IP_BEHIND_PACE_DAY = 4   # Thursday — alert if trending below 15
IP_SAFE_DAY        = 3   # Wednesday — safe to sit risky starters if above 15

# Waiver priority urgency bands
WAIVER_PRIORITY_CONFIDENT = (1, 3)   # top-3: recommend confidently
WAIVER_PRIORITY_AT_RISK   = (6, 8)   # bottom-3: flag AT RISK

# ---------------------------------------------------------------------------
# Category state thresholds (gap required to call a state)
# Tune after first 2 weeks of live use per PRD Section 6.2
# ---------------------------------------------------------------------------

CAT_THRESHOLDS = {
    # Calibrated 2026-06-03 from 9 weeks of real fact_matchups data.
    # Symmetric: FLOPPABLE == FLIPPABLE (equal danger/opportunity zones).
    # A lead or deficit within this range is considered live — neither safe nor lost.
    # LOSS ≈ 1.75× the avg weekly gap (too far back to realistically recover).
    # Avg gaps observed: R=11.5, HR=4.3, RBI=6.5, SB=4.9, OBP=.030,
    #                    K=8.9, QS=1.6, ERA=0.81, WHIP=0.20, SvHd=2.1
    "R":    {"floppable": 12,    "flippable": 12,   "loss": 20},
    "HR":   {"floppable": 4,     "flippable": 4,    "loss": 8},
    "RBI":  {"floppable": 6,     "flippable": 6,    "loss": 14},
    "SB":   {"floppable": 5,     "flippable": 5,    "loss": 9},
    "OBP":  {"floppable": 0.030, "flippable": 0.030,"loss": 0.055},
    "K":    {"floppable": 9,     "flippable": 9,    "loss": 16},
    "QS":   {"floppable": 2,     "flippable": 2,    "loss": 4},
    "ERA":  {"floppable": 0.80,  "flippable": 0.80, "loss": 1.50},
    "WHIP": {"floppable": 0.20,  "flippable": 0.20, "loss": 0.38},
    "SvHd": {"floppable": 2,     "flippable": 2,    "loss": 5},
}

# ---------------------------------------------------------------------------
# Need weight calculation parameters (PRD Section 7.1)
# ---------------------------------------------------------------------------

NEED_WEIGHT_HIGH = (0.8, 1.0)   # rank 7-8 + win rate < 40%
NEED_WEIGHT_MID  = (0.4, 0.7)
NEED_WEIGHT_LOW  = (0.1, 0.2)   # rank 1-2 + win rate > 65%

NEED_RANK_HIGH_CUTOFF    = 6    # rank 7 or 8 → high need
NEED_RANK_LOW_CUTOFF     = 3    # rank 1 or 2 → low need
NEED_WIN_RATE_HIGH       = 0.40
NEED_WIN_RATE_LOW        = 0.65

# ---------------------------------------------------------------------------
# Buy low / sell high thresholds (PRD Section 7.6)
# ---------------------------------------------------------------------------

BUY_LOW = {
    "hitter_xba_gap":      0.025,   # OBP lags xwOBA by more than this
    "hitter_babip_floor":  0.260,   # BABIP below this without speed explanation
    "pitcher_era_xfip_gap":0.75,    # ERA exceeds xFIP or SIERA by more than this
    "min_pa":              50,      # minimum PA before hitter flag shown
    "min_ip":              20,      # minimum IP before pitcher flag shown
    "elite_sprint_speed":  28.0,    # ft/sec — adjusts BABIP floor down by .020
    "min_xwoba":           0.310,   # quality floor — only flag hitters with real upside
}

SELL_HIGH = {
    "hitter_xba_gap":      0.025,   # OBP exceeds xwOBA by more than this
    "pitcher_era_xfip_gap":0.50,    # ERA significantly better than xFIP/SIERA
    "babip_ceiling":       0.350,   # elevated BABIP — luck-driven, likely to normalize
    "min_babip_pa":        100,     # min PA before elevated BABIP flag fires
}

# ---------------------------------------------------------------------------
# Two-start pitcher scoring weights (PRD Section 7.7)
# ---------------------------------------------------------------------------

TWO_START_WEIGHTS = {
    "has_two_starts": 1.0,   # binary gate — must have 2 starts to appear in list
    "siera":          0.35,  # primary skill signal
    "xfip":           0.15,  # secondary skill check
    "opp_quality":    0.20,  # opponent wRC+/OPS vs same handedness last 30D
    "park_factor":    0.10,  # FanGraphs park factor (Coors, GABP penalise)
    "k_pct_stability":0.10,  # 14D K%/BB% — decline flags risk
    "days_of_rest":   0.05,  # 4+ days rest both starts scores higher
    "need_weight":    0.05,  # multiplied through cat need weights post-score
}

# ---------------------------------------------------------------------------
# Prospect / callup intelligence (PRD Section 7.8)
# ---------------------------------------------------------------------------

PROSPECT = {
    "aaa_obp_threshold":   0.300,
    "aaa_k_pct_threshold": 0.250,
    "ownership_velocity":  10.0,    # % gain in 48h triggers immediate alert
    "il_return_flag_days": 5,       # flag N days before expected return
}

# ---------------------------------------------------------------------------
# Validation bounds (PRD Section 8.1)
# ---------------------------------------------------------------------------

VALIDATION_BOUNDS = {
    "era":        (0.00, 15.00),
    "whip":       (0.00, 4.00),
    "obp":        (0.100, 0.600),
    "xfip":       (1.00, 12.00),   # raised: bad pitchers or small samples can exceed 8
    "siera":      (1.00, 10.00),   # raised: same rationale
    "barrel_pct": (0.0, 35.0),
    "ownership":  (0.0, 100.0),
}

VALIDATION_COMPLETENESS = {
    "expected_rosters":   8,
    "min_active_players": 20,
    "min_fa_pool":        200,
    "min_statcast":       400,     # lowered: ESPN universe ~556, Statcast requires min BBE/PA
    "crosswalk_fail_threshold": 3,   # FAIL if more than this many unmatched
}

STALENESS_WARN_HOURS = 25

# ---------------------------------------------------------------------------
# Data source & cache settings
# ---------------------------------------------------------------------------

DB_PATH            = "db/pitch-slap.db"
DATA_DIR           = "data"
DOCS_DIR           = "docs"
CONFIG_HISTORY_DIR = "config-history"

STATCAST_CACHE_TTL_DAYS = 7    # skip Statcast pull if cache < 7 days old
CROSSWALK_REFRESH = "monday"   # rebuild dim_players weekly

API_CONFIG = {
    "espn_base_url":  "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb",
    "mlb_stats_url":  "https://statsapi.mlb.com/api/v1",
    "request_timeout": 30,
    "max_retries":     3,
    "retry_delay":     2,
}

# ---------------------------------------------------------------------------
# ESPN credentials — loaded from espn-credentials.py (gitignored)
# ---------------------------------------------------------------------------

try:
    from espn_credentials import ESPN_SWID, ESPN_S2  # type: ignore
except ImportError:
    ESPN_SWID = None
    ESPN_S2   = None

# ---------------------------------------------------------------------------
# ESPN API field mappings (confirmed via live API inspection)
# ---------------------------------------------------------------------------

ESPN_STAT_IDS = {
    "20": "runs",
    "5":  "home_runs",
    "21": "rbis",
    "23": "stolen_bases",
    "17": "obp",
    "48": "strikeouts",
    "63": "quality_starts",
    "47": "era",
    "41": "whip",
    "83": "svhd",
}

ESPN_RATE_STAT_IDS = {"47", "41", "17"}   # ERA, WHIP, OBP — stored as floats, not summed

ESPN_PRO_TEAM_MAP = {
    0:  "FA",
    1:  "BAL",  2:  "BOS",  3:  "LAA",  4:  "CWS",  5:  "CLE",
    6:  "DET",  7:  "KC",   8:  "MIL",  9:  "MIN",  10: "NYY",
    11: "ATH",  12: "SEA",  13: "TEX",  14: "TOR",  15: "ATL",
    16: "CHC",  17: "CIN",  18: "HOU",  19: "LAD",  20: "WSH",
    21: "NYM",  22: "PHI",  23: "PIT",  24: "STL",  25: "SD",
    26: "SF",   27: "COL",  28: "MIA",  29: "ARI",  30: "TB",
}

ESPN_TEAM_TO_MLB_NAME = {
    "BAL": "Baltimore",    "BOS": "Boston",       "LAA": "Angels",
    "CWS": "White Sox",    "CLE": "Guardians",    "DET": "Detroit",
    "KC":  "Kansas City",  "MIL": "Milwaukee",    "MIN": "Minnesota",
    "NYY": "Yankees",      "ATH": "Athletics",    "SEA": "Seattle",
    "TEX": "Texas",        "TOR": "Toronto",      "ATL": "Atlanta",
    "CHC": "Cubs",         "CIN": "Cincinnati",   "HOU": "Houston",
    "LAD": "Dodgers",      "WSH": "Washington",   "NYM": "Mets",
    "PHI": "Philadelphia", "PIT": "Pittsburgh",   "STL": "St. Louis",
    "SD":  "San Diego",    "SF":  "San Francisco","COL": "Colorado",
    "MIA": "Miami",        "ARI": "Arizona",      "TB":  "Tampa Bay",
}

ESPN_POSITION_MAP = {
    1:  "SP",
    2:  "C",
    3:  "1B",
    4:  "2B",
    5:  "3B",
    6:  "SS",
    7:  "OF",
    8:  "OF",
    9:  "OF",
    10: "DH",
    11: "RP",
    12: "RP",
    13: "P",
}

ESPN_LINEUP_SLOT_MAP = {
    0:  "C",
    1:  "1B",
    2:  "2B",
    3:  "3B",
    4:  "SS",
    5:  "OF",
    6:  "2B/SS",
    7:  "1B/3B",
    8:  "LF",
    9:  "CF",
    10: "RF",
    11: "DH",
    12: "UTIL",
    13: "SP",
    14: "P",
    15: "BN",
    16: "BN",
    17: "IL",
    18: "IL",
    19: "IL",
}

ESPN_PRIMARY_SLOTS    = {"C", "1B", "2B", "3B", "SS", "OF", "DH", "SP", "RP"}
ESPN_INACTIVE_SLOT_IDS = {15, 16, 17, 18, 19}
PITCHER_POSITION_IDS  = {1, 11, 12, 13}
OF_POSITIONS          = {"OF", "CF", "LF", "RF"}

INJURED_STATUSES = {
    "INJURY_RESERVE", "FIFTEEN_DAY_IL", "SIXTY_DAY_IL",
    "SEVEN_DAY_IL", "TEN_DAY_IL", "OUT", "SUSPENSION", "BEREAVEMENT",
}

QUESTIONABLE_STATUSES = {"DAY_TO_DAY", "QUESTIONABLE", "DOUBTFUL"}

# ---------------------------------------------------------------------------
# Playoffs
# ---------------------------------------------------------------------------

PLAYOFFS = {
    "teams":            4,
    "rounds":           2,
    "weeks_per_round":  2,
    "tiebreaker":       "h2h_record",
}

# Set after ESPN posts the full schedule; auto-detected from ESPN settings when possible.
PLAYOFF_START_WEEK = 22   # override if ESPN shows a different value

# ---------------------------------------------------------------------------
# Config versioning — call snapshot() before any change to this file
# ---------------------------------------------------------------------------

def snapshot(note: str = "") -> str:
    """Save a timestamped copy of this file to config-history/ before changes."""
    ts   = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    src  = __file__
    dest = os.path.join(CONFIG_HISTORY_DIR, f"config_{ts}.py")
    os.makedirs(CONFIG_HISTORY_DIR, exist_ok=True)
    shutil.copy2(src, dest)
    with open(src, "rb") as f:
        h = hashlib.sha256(f.read()).hexdigest()[:12]
    print(f"Config snapshot saved: {dest} (sha256: {h}) — {note}")
    return dest


if __name__ == "__main__":
    print("Config loaded.")
    print(f"  League: {LEAGUE_ID} | Team ID: {TEAM_ID} | Season: {SEASON}")
    print(f"  Hitting:  {HITTING_CATS}")
    print(f"  Pitching: {PITCHING_CATS}")
    print(f"  ESPN auth: {'OK' if ESPN_SWID else 'MISSING — check espn-credentials.py'}")
