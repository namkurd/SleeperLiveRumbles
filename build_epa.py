"""Builds epa_weekly.json: each player's total EPA and number of plays, per week.

Sleeper has no EPA, so this reads the nflverse weekly player stats
(https://github.com/nflverse/nflverse-data, refreshed daily, a game's numbers
show up the day after it ends) and re-keys them by Sleeper player id.

  { "season": 2026, "generated_at": "...",
    "weeks": { "1": { "<sleeper_id>": [total_epa, plays], ... }, ... } }

plays: QB = pass attempts + sacks + carries; everyone else = carries + targets.
EPA: QB = passing + rushing, RB/WR/TE = rushing + receiving. Kickers and team
defenses have no EPA in this source, so they are left out. The page divides
the two numbers (and sums them across a team's starters first) to get EPA/play.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "epa_weekly.json")
NFLVERSE_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
SLEEPER_STATE = "https://api.sleeper.app/v1/state/nfl"
SLEEPER_PLAYERS = "https://api.sleeper.app/v1/players/nfl"
# Id crosswalk (Sleeper id <-> NFL gsis id); Sleeper's own gsis_id field is missing for many players.
PLAYER_IDS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"


def f(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except ValueError:
        return 0.0


def player_epa(row: dict) -> tuple[float, float]:
    """(total EPA, plays) for one player-week row, or (0, 0) if none."""
    carries = f(row, "carries")
    if (row.get("position") or "") == "QB":
        epa = f(row, "passing_epa") + f(row, "rushing_epa")
        plays = f(row, "attempts") + f(row, "sacks_suffered") + carries
    else:
        epa = f(row, "rushing_epa") + f(row, "receiving_epa")
        plays = carries + f(row, "targets")
    return epa, plays


def build(season: int, csv_text: str, gsis_to_sleeper: dict[str, str]) -> dict:
    weeks: dict[str, dict[str, list]] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        if (row.get("season_type") or "REG") != "REG":
            continue
        if row.get("position") not in ("QB", "RB", "WR", "TE", "FB"):
            continue
        sid = gsis_to_sleeper.get(row.get("player_id") or "")
        if not sid:
            continue
        epa, plays = player_epa(row)
        if plays <= 0:
            continue
        wk = str(int(float(row["week"])))
        cell = weeks.setdefault(wk, {}).setdefault(sid, [0.0, 0])
        cell[0] += epa
        cell[1] += int(plays)
    for wk in weeks.values():
        for cell in wk.values():
            cell[0] = round(cell[0], 3)
    return {
        "season": season,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weeks": dict(sorted(weeks.items(), key=lambda kv: int(kv[0]))),
    }


def id_crosswalk() -> dict[str, str]:
    """gsis id -> Sleeper id. The community crosswalk first, Sleeper's own gsis_id as a fallback."""
    out: dict[str, str] = {}
    try:
        text = requests.get(PLAYER_IDS_URL, timeout=120).text
        for row in csv.DictReader(io.StringIO(text)):
            g, s = row.get("gsis_id"), row.get("sleeper_id")
            if g and s and g != "NA" and s != "NA":
                out[g] = s
    except Exception as e:  # noqa: BLE001
        print(f"[warn] player id crosswalk unavailable ({e}); using Sleeper's gsis_id only", file=sys.stderr)
    players = requests.get(SLEEPER_PLAYERS, timeout=120).json()
    for pid, p in players.items():
        if p.get("gsis_id") and p["gsis_id"] not in out:
            out[p["gsis_id"]] = pid
    return out


def main() -> None:
    season = int(requests.get(SLEEPER_STATE, timeout=30).json()["season"])
    gsis_to_sleeper = id_crosswalk()
    resp = requests.get(NFLVERSE_URL.format(season=season), timeout=120)
    if resp.status_code != 200:
        print(f"[warn] nflverse has no stats file for {season} yet (HTTP {resp.status_code}); leaving {OUTPUT_PATH} as is")
        return
    out = build(season, resp.text, gsis_to_sleeper)
    with open(OUTPUT_PATH, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    n = sum(len(w) for w in out["weeks"].values())
    print(f"[info] wrote {OUTPUT_PATH}: {len(out['weeks'])} weeks, {n} player-weeks")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - EPA is an optional extra; never fail the standings build
        print(f"[warn] EPA build failed: {e}", file=sys.stderr)
