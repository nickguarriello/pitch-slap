# Pitch Slap — Fantasy Baseball Decision Engine

Automated daily analytics pipeline for an ESPN H2H fantasy baseball league.
Runs 3x daily via GitHub Actions and publishes a static dashboard to GitHub Pages.

## Dashboard

Live at: `https://nickguarriello.github.io/pitch-slap/`

Pages: Home · Matchup · Waivers · Players · League

## Pipeline Schedule

| Time (EDT) | Mode  | What runs |
|------------|-------|-----------|
| 7:00am     | full  | Statcast refresh + all fetchers + transform + validate + evaluate + report |
| 12:00pm    | light | ESPN + MLB + cached Statcast + transform + validate + evaluate + report |
| 6:00pm     | light | Same as noon |

## First-Time Setup

### 1. GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name | Value |
|-------------|-------|
| `ESPN_SWID` | Your ESPN SWID cookie (with curly braces, e.g. `{3B78...}`) |
| `ESPN_S2`   | Your ESPN_S2 cookie value |

To get these cookies: log into ESPN Fantasy, open DevTools → Application → Cookies → `fantasy.espn.com`.

### 2. GitHub Pages

Go to **Settings → Pages**:
- Source: **Deploy from a branch**
- Branch: `main` / folder: `/docs`

The pipeline writes all data to `docs/data/*.json` and auto-commits after each run.

### 3. Manual trigger

To run immediately without waiting for schedule:
**Actions → Daily Pipeline → Run workflow** — choose `full` or `light`.

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize database (safe to re-run)
python -m pipeline.init_db

# Full run
python pipeline.py

# Light run (uses cached Statcast)
python pipeline.py --mode light
```

ESPN credentials must be in `espn_credentials.py` (gitignored):
```python
ESPN_SWID = "{your-swid}"
ESPN_S2 = "your-espn-s2"
```

## League Config

- **League:** ESPN H2H Categories, 10 teams
- **Hitting:** R, HR, RBI, SB, OBP
- **Pitching:** K, QS, ERA, WHIP, SvHd
- **Roster lock:** Weekly (Monday)
- **Acquisitions:** 7/week limit
- **Waivers:** Priority order (not FAAB)
