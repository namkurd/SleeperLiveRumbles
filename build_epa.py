"""Builds epa_weekly.json: each player's total EPA and number of plays, per week, plus the
league's past-week lineups so the page needs no live API calls for them.

Sleeper has no EPA, so this reads nflverse data (https://github.com/nflverse/nflverse-data,
refreshed daily; a game's numbers show up the day after it ends) and re-keys it by Sleeper id.

  { "season": 2026, "generated_at": "...",
    "weeks":   { "1": { "<sleeper_id>": [total_epa, plays], ... }, ... },
    "slots":   ["QB", "RB", ...],                          # league roster_positions
    "lineups": { "1": { "<roster_id>": [["<sleeper_id>", points], ...] } },   # completed weeks
    "names":   { "<sleeper_id>": ["Name", "POS"] } }

EPA / plays:
  QB          passing + rushing EPA; pass attempts + sacks + carries
  RB, WR, TE  rushing + receiving EPA; carries + targets
  K           EPA of his field goal and extra point attempts; number of attempts
  DEF         minus the EPA of the opposing offense's pass and run plays; number of those plays
              (positive = the defense helped), keyed by team abbreviation like Sleeper does.
The page shows each player's total EPA and sums it across a team's starters.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import sys
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(HERE, "epa_weekly.json")
NFLVERSE_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
PBP_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz"
SLEEPER_API = "https://api.sleeper.app/v1"
TEAM_FIX = {"LA": "LAR"}   # nflverse -> Sleeper team abbreviations
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


def kicker_defense(pbp_text: str, gsis_to_sleeper: dict[str, str]) -> dict[str, dict[str, list]]:
    """Kicker and team-defense EPA per week from nflverse play-by-play."""
    weeks: dict[str, dict[str, list]] = {}

    def add(wk: str, sid: str, epa: float) -> None:
        cell = weeks.setdefault(wk, {}).setdefault(sid, [0.0, 0])
        cell[0] += epa
        cell[1] += 1

    for row in csv.DictReader(io.StringIO(pbp_text)):
        if (row.get("season_type") or "REG") != "REG" or not row.get("epa"):
            continue
        try:
            epa = float(row["epa"])
            wk = str(int(float(row["week"])))
        except ValueError:
            continue
        ptype = row.get("play_type")
        if ptype in ("field_goal", "extra_point"):
            sid = gsis_to_sleeper.get(row.get("kicker_player_id") or "")
            if sid:
                add(wk, sid, epa)
        elif ptype in ("pass", "run") and row.get("defteam"):
            add(wk, TEAM_FIX.get(row["defteam"], row["defteam"]), -epa)
    for wkd in weeks.values():
        for cell in wkd.values():
            cell[0] = round(cell[0], 3)
    return weeks


def lineups_for(weeks_done: list[int], previous: dict) -> tuple[dict, dict, list]:
    """Starters and points per roster for completed weeks. Weeks already in the previous file are reused,
    so a normal night makes no Sleeper calls at all and a new week makes a few."""
    lineups = dict(previous.get("lineups") or {})
    names = dict(previous.get("names") or {})
    slots = list(previous.get("slots") or [])
    todo = [w for w in weeks_done if str(w) not in lineups]
    if not todo:
        return lineups, names, slots
    hist = json.load(open(os.path.join(HERE, "rumbles_history.json")))
    lid = hist["league_id"]
    slots = requests.get(f"{SLEEPER_API}/league/{lid}", timeout=30).json().get("roster_positions") or slots
    meta = requests.get(SLEEPER_PLAYERS, timeout=120).json()
    for w in todo:
        ms = requests.get(f"{SLEEPER_API}/league/{lid}/matchups/{w}", timeout=30).json()
        by = {}
        for m in ms:
            pts = m.get("players_points") or {}
            row = []
            for pid in m.get("starters") or []:
                if not pid or pid == "0":
                    continue
                row.append([pid, round(float(pts.get(pid) or 0), 2)])
                pm = meta.get(pid) or {}
                nm = pm.get("full_name") or ((pm.get("first_name") or "") + " " + (pm.get("last_name") or "")).strip() or pid
                names[pid] = [nm, pm.get("position") or ""]
            by[str(m["roster_id"])] = row
        lineups[str(w)] = by
    return lineups, names, slots


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
    try:
        pbp = requests.get(PBP_URL.format(season=season), timeout=180)
        if pbp.status_code == 200:
            kd = kicker_defense(gzip.decompress(pbp.content).decode("utf-8"), gsis_to_sleeper)
            for wk, players in kd.items():
                out["weeks"].setdefault(wk, {}).update(players)
            out["weeks"] = dict(sorted(out["weeks"].items(), key=lambda kv: int(kv[0])))
        else:
            print(f"[warn] no play-by-play yet (HTTP {pbp.status_code}); kicker/defense EPA skipped")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] kicker/defense EPA skipped ({e})", file=sys.stderr)
    previous = {}
    try:
        previous = json.load(open(OUTPUT_PATH))
        if previous.get("season") != season:
            previous = {}
    except Exception:  # noqa: BLE001
        previous = {}
    try:
        done = json.load(open(os.path.join(HERE, "rumbles_history.json"))).get("weeks_completed") or []
        out["lineups"], out["names"], out["slots"] = lineups_for(done, previous)
    except Exception as e:  # noqa: BLE001 - lineups are a convenience; keep whatever we had
        print(f"[warn] lineups skipped ({e})", file=sys.stderr)
        for k in ("lineups", "names", "slots"):
            if k in previous:
                out[k] = previous[k]
    with open(OUTPUT_PATH, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    n = sum(len(w) for w in out["weeks"].values())
    print(f"[info] wrote {OUTPUT_PATH}: {len(out['weeks'])} weeks, {n} player-weeks")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - EPA is an optional extra; never fail the standings build
        print(f"[warn] EPA build failed: {e}", file=sys.stderr)
