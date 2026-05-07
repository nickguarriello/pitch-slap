"""
Initialize SQLite database with full schema per PRD Section 5.
Safe to run multiple times — uses CREATE TABLE IF NOT EXISTS.
"""

import sqlite3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DB_PATH

SCHEMA = """

-- ---------------------------------------------------------------------------
-- Dimension: player crosswalk (ESPN <-> MLB <-> FanGraphs <-> BBRef)
-- Built on Day 1. Refreshed weekly (Mondays). No name joins downstream.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_players (
    player_id         TEXT PRIMARY KEY,   -- ESPN player ID (canonical key)
    mlb_id            INTEGER,
    fg_id             TEXT,
    bbref_id          TEXT,
    name              TEXT NOT NULL,
    position          TEXT,
    mlb_team          TEXT,
    crosswalk_status  TEXT NOT NULL DEFAULT 'unmatched',
                      -- 'matched' | 'unmatched' | 'flagged' | 'manual'
    crosswalk_note    TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dim_players_mlb_id ON dim_players(mlb_id);
CREATE INDEX IF NOT EXISTS idx_dim_players_name   ON dim_players(name);

-- ---------------------------------------------------------------------------
-- Fact: ESPN roster snapshots (one row per player per daily run)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_espn_rosters (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date         TEXT NOT NULL,
    player_id             TEXT NOT NULL,
    espn_team_id          INTEGER,
    lineup_slot           TEXT,
    is_active             INTEGER NOT NULL DEFAULT 0,  -- 1/0
    is_il                 INTEGER NOT NULL DEFAULT 0,
    ownership_pct         REAL,
    ownership_delta_7d    REAL,
    start_pct             REAL,
    espn_rating           REAL,
    espn_proj_r           REAL,
    espn_proj_hr          REAL,
    espn_proj_rbi         REAL,
    espn_proj_sb          REAL,
    espn_proj_obp         REAL,
    espn_proj_k           REAL,
    espn_proj_qs          REAL,
    espn_proj_era         REAL,
    espn_proj_whip        REAL,
    espn_proj_svhd        REAL,
    acquisition_type      TEXT,   -- 'draft' | 'waiver' | 'trade'
    injury_status         TEXT,
    FOREIGN KEY (player_id) REFERENCES dim_players(player_id)
);

CREATE INDEX IF NOT EXISTS idx_fact_espn_rosters_date_player
    ON fact_espn_rosters(snapshot_date, player_id);

-- ---------------------------------------------------------------------------
-- Fact: player stats across temporal windows
-- One row per player × window per stat_date
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_player_stats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id   TEXT NOT NULL,
    window      TEXT NOT NULL,   -- 'current' | '14d' | '30d' | 'season' | 'post_asg'
    stat_date   TEXT NOT NULL,
    -- hitting
    r           REAL,
    hr          REAL,
    rbi         REAL,
    sb          REAL,
    obp         REAL,
    -- pitching
    k           REAL,
    qs          REAL,
    era         REAL,
    whip        REAL,
    svhd        REAL,
    -- supporting
    babip       REAL,
    k_pct       REAL,
    bb_pct      REAL,
    ip          REAL,
    pa          REAL,
    FOREIGN KEY (player_id) REFERENCES dim_players(player_id)
);

CREATE INDEX IF NOT EXISTS idx_fact_player_stats_player_window
    ON fact_player_stats(player_id, window, stat_date);

-- ---------------------------------------------------------------------------
-- Fact: Statcast + FanGraphs advanced metrics (weekly refresh)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_statcast (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id     TEXT NOT NULL,
    fetch_date    TEXT NOT NULL,
    xba           REAL,
    xslg          REAL,
    xwoba         REAL,
    xfip          REAL,
    siera         REAL,
    fip           REAL,
    wrc_plus      REAL,
    barrel_pct    REAL,
    hard_hit_pct  REAL,
    exit_velo     REAL,
    sprint_speed  REAL,
    launch_angle  REAL,
    FOREIGN KEY (player_id) REFERENCES dim_players(player_id)
);

CREATE INDEX IF NOT EXISTS idx_fact_statcast_player_date
    ON fact_statcast(player_id, fetch_date);

-- ---------------------------------------------------------------------------
-- Fact: matchup results (final + in-progress)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_matchups (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    my_team_id    INTEGER NOT NULL,
    opp_team_id   INTEGER NOT NULL,
    cat_name      TEXT NOT NULL,
    my_val        REAL,
    opp_val       REAL,
    result        TEXT,   -- 'W' | 'L' | 'T' | NULL (in progress)
    is_final      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_fact_matchups_week
    ON fact_matchups(season, week);

-- ---------------------------------------------------------------------------
-- Fact: daily matchup cat snapshots (append-only, drives trend arrows)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matchup_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_ts  TEXT NOT NULL DEFAULT (datetime('now')),
    week         INTEGER NOT NULL,
    cat_name     TEXT NOT NULL,
    my_val       REAL,
    opp_val      REAL
);

-- ---------------------------------------------------------------------------
-- Fact: daily constraint tracking
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS constraint_log (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date         TEXT NOT NULL,
    matchup_week          INTEGER NOT NULL,
    acquisitions_used     INTEGER NOT NULL DEFAULT 0,
    acquisitions_remaining INTEGER NOT NULL DEFAULT 7,
    ip_accumulated        REAL NOT NULL DEFAULT 0.0,
    ip_remaining          REAL NOT NULL DEFAULT 15.0,
    waiver_priority       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_constraint_log_date
    ON constraint_log(snapshot_date);

-- ---------------------------------------------------------------------------
-- Fact: game schedule (next 7 days, refreshed 3x daily)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_schedule (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    game_date            TEXT NOT NULL,
    mlb_team             TEXT NOT NULL,
    opponent_team        TEXT,
    home_away            TEXT,   -- 'home' | 'away'
    probable_pitcher_id  INTEGER,
    park_factor          REAL,
    opp_wrc_vs_hand      REAL
);

CREATE INDEX IF NOT EXISTS idx_fact_schedule_date_team
    ON fact_schedule(game_date, mlb_team);

-- ---------------------------------------------------------------------------
-- Dimension: park factors
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_parks (
    team               TEXT PRIMARY KEY,
    park_name          TEXT,
    park_factor_overall REAL,
    park_factor_hr     REAL,
    park_factor_avg    REAL,
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Validation audit log (every check, every run)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS validation_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_ts      TEXT NOT NULL DEFAULT (datetime('now')),
    check_name  TEXT NOT NULL,
    status      TEXT NOT NULL,   -- 'pass' | 'warn' | 'fail'
    detail      TEXT,
    row_count   INTEGER
);

-- ---------------------------------------------------------------------------
-- Config version history
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS config_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    version_ts  TEXT NOT NULL DEFAULT (datetime('now')),
    config_hash TEXT NOT NULL,
    changed_by  TEXT,
    notes       TEXT
);

"""


def init_db(db_path: str = DB_PATH) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"Database initialised: {db_path}")
    _verify(db_path)


def _verify(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    cur  = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    conn.close()
    expected = {
        "dim_players", "dim_parks",
        "fact_espn_rosters", "fact_player_stats", "fact_statcast",
        "fact_matchups", "matchup_snapshots",
        "fact_schedule", "constraint_log",
        "validation_log", "config_versions",
    }
    missing = expected - set(tables)
    if missing:
        raise RuntimeError(f"Schema incomplete — missing tables: {missing}")
    print(f"  {len(tables)} tables verified: {', '.join(sorted(tables))}")


def seed_crosswalk(db_path: str = DB_PATH, csv_path: str = None) -> None:
    """Load dim_players from the committed crosswalk CSV if the table is empty."""
    import csv as _csv
    if csv_path is None:
        csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "player-crosswalk.csv")
    if not os.path.exists(csv_path):
        print(f"  [seed_crosswalk] CSV not found: {csv_path} — skipping")
        return
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM dim_players").fetchone()[0]
    if count > 0:
        print(f"  [seed_crosswalk] dim_players already has {count} rows — skipping")
        conn.close()
        return
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = _csv.DictReader(f)
        rows = list(reader)
    conn.executemany(
        """INSERT OR IGNORE INTO dim_players
           (player_id, mlb_id, fg_id, bbref_id, name, position, mlb_team,
            crosswalk_status, crosswalk_note, created_at, updated_at)
           VALUES (:player_id, :mlb_id, :fg_id, :bbref_id, :name, :position, :mlb_team,
                   :crosswalk_status, :crosswalk_note, :created_at, :updated_at)""",
        [{k: (int(v) if k == "mlb_id" and v not in ("", "None", None) else (None if v in ("", "None") else v))
          for k, v in row.items()} for row in rows]
    )
    conn.commit()
    conn.close()
    print(f"  [seed_crosswalk] Seeded {len(rows)} players into dim_players from {csv_path}")


if __name__ == "__main__":
    init_db()
    seed_crosswalk()
