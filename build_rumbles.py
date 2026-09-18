#!/usr/bin/env python3
"""
build_rumbles.py

Computes the DTF Club "Rumbles" standings for the CURRENT season and writes
rumbles_history.json, which rumbles.html loads for everything already
finished before it takes over with live, client-side computation for the
current in-progress week.

Rumbles formula (verified against the "2026 TRUE STANDINGS" Google Sheet,
Week 1, exact match for all 12 managers):
    +9 Rumbles for winning your scheduled head-to-head matchup
    +1 Rumble for every OTHER team in the league you outscore that week
In a 12-team league that's up to 9 + 11 = 20 Rumbles in a single week.

Rumbles is a single-SEASON stat and resets each year, so this script only
ever scores weeks from the current season, and only weeks that are fully
complete -- the live/in-progress week is handled entirely in the browser
by rumbles.html (which also references Sleeper's own live-updating,
in-game player projections while games are underway, not just pregame
projections -- see rumbles.html for that logic).

This is a fully standalone project -- a separate repo from the career H2H
matrix project, with no dependency on it. League discovery and manager
display names are both derived at runtime from Sleeper's API (see
discover_current_league_id / build_manager_map below); edit
FALLBACK_LEAGUE_ID / FALLBACK_USERNAME / DISPLAY_NAME_OVERRIDES for your
own league.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests

API_BASE = "https://api.sleeper.app/v1"
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rumbles_history.json")

# Used if auto-discovery can't find the league by name (see
# discover_current_league_id below).
FALLBACK_LEAGUE_ID = "1389416556617801728"  # 2026 season DTF Club league
FALLBACK_USERNAME = "namkurd"  # Ben's Sleeper username, used to auto-discover the league each year
DISPLAY_NAME_OVERRIDES = {
    # Sleeper display_name (lowercase) -> preferred display name. Add an
    # entry here for anyone whose Sleeper display name isn't what you want
    # shown on the standings page.
    "namkurd": "Ben",
}

RUMBLES_PER_WIN = 9
MAX_RUMBLES_PER_WEEK = 20  # 9 for the H2H win + up to 11 for outscoring the field (12-team league)

session = requests.Session()
session.headers.update({"User-Agent": "dtf-club-rumbles-builder/1.0"})


def get_json(url: str, retries: int = 3, backoff: float = 1.5) -> Any:
    last_err = None
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001 - we want to retry on anything transient
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"Failed to GET {url} after {retries} attempts: {last_err}")


# ---------------------------------------------------------------------------
# League discovery + manager names
# ---------------------------------------------------------------------------


def discover_current_league_id(season: str) -> str:
    """Find this season's DTF Club league_id.

    Walks FALLBACK_USERNAME's leagues for the season and matches on the
    league name; falls back to a hardcoded id if that fails for any reason
    (renamed league, API hiccup, etc).
    """
    try:
        user = get_json(f"{API_BASE}/user/{FALLBACK_USERNAME}")
        user_id = user["user_id"]
        leagues = get_json(f"{API_BASE}/user/{user_id}/leagues/nfl/{season}")
        for league in leagues:
            if "dtf" in league.get("name", "").lower():
                return league["league_id"]
        if leagues:
            # Only one league for this user this season -- good enough odds.
            return leagues[0]["league_id"]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] league auto-discovery failed ({e}); using FALLBACK_LEAGUE_ID", file=sys.stderr)

    return FALLBACK_LEAGUE_ID


def build_manager_map(league_id: str) -> dict[int, str]:
    """roster_id -> display name, for the CURRENT active rosters only.

    Names come straight from Sleeper's /users endpoint, with cosmetic
    overrides from DISPLAY_NAME_OVERRIDES above.
    """
    users = get_json(f"{API_BASE}/league/{league_id}/users")
    rosters = get_json(f"{API_BASE}/league/{league_id}/rosters")
    user_by_id = {u["user_id"]: u for u in users}
    out = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        u = user_by_id.get(owner_id, {})
        raw_name = u.get("display_name", f"Roster {r['roster_id']}")
        name = DISPLAY_NAME_OVERRIDES.get(raw_name.lower(), raw_name)
        out[r["roster_id"]] = name
    return out


# ---------------------------------------------------------------------------
# Rumbles math
# ---------------------------------------------------------------------------


def get_completed_weeks(state: dict) -> list[int]:
    """Weeks of the CURRENT season that are fully finished.

    The in-progress/current week is deliberately excluded -- rumbles.html
    computes that one live, in the browser. We treat every week strictly
    before state['week'] during/after the regular season as complete; if
    we're in the preseason there are no completed weeks yet.
    """
    if state.get("season_type") == "pre":
        return []
    current_week = state.get("week") or 1
    return list(range(1, current_week))


def score_week(matchups: list[dict], manager_map: dict[int, str]) -> dict[int, dict]:
    """Given raw /matchups/{week} data, compute each roster's Rumbles etc.

    Returns roster_id -> {points, opponent_roster_id, opponent_points,
    h2h_win, rumbles, teams_outscored, teams_outscored_by}
    """
    by_roster = {m["roster_id"]: m for m in matchups}
    # Pair up rosters that share a matchup_id (the scheduled H2H game).
    pairs: dict[int, list[dict]] = {}
    for m in matchups:
        pairs.setdefault(m["matchup_id"], []).append(m)

    result: dict[int, dict] = {}
    all_scores = {rid: (by_roster[rid].get("points") or 0.0) for rid in by_roster}

    for _, pair in pairs.items():
        if len(pair) != 2:
            # Bye week or malformed data -- no H2H opponent to score against.
            for m in pair:
                result[m["roster_id"]] = {
                    "points": m.get("points") or 0.0,
                    "opponent_roster_id": None,
                    "opponent_points": None,
                    "h2h_win": None,
                }
            continue
        a, b = pair
        pa, pb = a.get("points") or 0.0, b.get("points") or 0.0
        a_win = pa > pb
        b_win = pb > pa
        result[a["roster_id"]] = {
            "points": pa,
            "opponent_roster_id": b["roster_id"],
            "opponent_points": pb,
            "h2h_win": a_win,
        }
        result[b["roster_id"]] = {
            "points": pb,
            "opponent_roster_id": a["roster_id"],
            "opponent_points": pa,
            "h2h_win": b_win,
        }

    # Vs-the-field: for each roster, how many of the OTHER rosters did it outscore?
    for rid, info in result.items():
        my_score = info["points"]
        outscored = sum(1 for other_rid, s in all_scores.items() if other_rid != rid and my_score > s)
        outscored_by = sum(1 for other_rid, s in all_scores.items() if other_rid != rid and s > my_score)
        rumbles = outscored  # +1 per team outscored
        if info["h2h_win"]:
            rumbles += RUMBLES_PER_WIN
        info["teams_outscored"] = outscored
        info["teams_outscored_by"] = outscored_by
        info["rumbles"] = rumbles

    return result


def build_history(league_id: str, season: str, completed_weeks: list[int], manager_map: dict[int, str]) -> dict:
    weekly: dict[str, dict] = {}
    cumulative: dict[int, dict] = {
        rid: {"rumbles": 0, "pf": 0.0, "pa": 0.0, "h2h_w": 0, "h2h_l": 0, "vs_field_w": 0, "vs_field_l": 0}
        for rid in manager_map
    }

    for week in completed_weeks:
        matchups = get_json(f"{API_BASE}/league/{league_id}/matchups/{week}")
        if not matchups:
            continue
        week_result = score_week(matchups, manager_map)
        weekly[str(week)] = week_result

        for rid, info in week_result.items():
            if rid not in cumulative:
                continue
            c = cumulative[rid]
            c["rumbles"] += info["rumbles"]
            c["pf"] += info["points"]
            if info["opponent_points"] is not None:
                c["pa"] += info["opponent_points"]
            if info["h2h_win"] is True:
                c["h2h_w"] += 1
            elif info["h2h_win"] is False:
                c["h2h_l"] += 1
            c["vs_field_w"] += info["teams_outscored"]
            c["vs_field_l"] += info["teams_outscored_by"]

    weeks_played = len(completed_weeks)
    max_possible = weeks_played * MAX_RUMBLES_PER_WEEK

    standings = []
    for rid, c in cumulative.items():
        last_week_key = str(completed_weeks[-1]) if completed_weeks else None
        last_week_info = weekly.get(last_week_key, {}).get(rid, {}) if last_week_key else {}
        this_week_rumbles = last_week_info.get("rumbles", 0)
        this_week_points = last_week_info.get("points", 0.0)
        standings.append(
            {
                "roster_id": rid,
                "manager": manager_map.get(rid, f"Roster {rid}"),
                "rumbles": c["rumbles"],
                "rumble_pct": round((c["rumbles"] / max_possible) * 100, 1) if max_possible else 0.0,
                "last_completed_week_rumbles": this_week_rumbles,
                "last_completed_week_points": round(this_week_points, 2),
                "pf": round(c["pf"], 2),
                "pa": round(c["pa"], 2),
                "h2h_w": c["h2h_w"],
                "h2h_l": c["h2h_l"],
                "vs_field_w": c["vs_field_w"],
                "vs_field_l": c["vs_field_l"],
            }
        )

    standings.sort(key=lambda s: (-s["rumbles"], -s["pf"]))
    for i, s in enumerate(standings, start=1):
        s["rank"] = i

    return {
        "season": season,
        "league_id": league_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weeks_completed": completed_weeks,
        "max_rumbles_per_week": MAX_RUMBLES_PER_WEEK,
        "rumbles_per_h2h_win": RUMBLES_PER_WIN,
        "standings": standings,
        "weekly": weekly,
    }


def main() -> None:
    state = get_json(f"{API_BASE}/state/nfl")
    season = state["season"]
    league_id = discover_current_league_id(season)
    manager_map = build_manager_map(league_id)
    completed_weeks = get_completed_weeks(state)

    print(f"[info] season={season} league_id={league_id} completed_weeks={completed_weeks}")
    print(f"[info] managers: {list(manager_map.values())}")

    if not completed_weeks:
        print("[info] no fully completed weeks yet this season -- writing an empty-but-valid history file")
        history = {
            "season": season,
            "league_id": league_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "weeks_completed": [],
            "max_rumbles_per_week": MAX_RUMBLES_PER_WEEK,
            "rumbles_per_h2h_win": RUMBLES_PER_WIN,
            "standings": [
                {
                    "roster_id": rid,
                    "manager": name,
                    "rumbles": 0,
                    "rumble_pct": 0.0,
                    "last_completed_week_rumbles": 0,
                    "last_completed_week_points": 0.0,
                    "pf": 0.0,
                    "pa": 0.0,
                    "h2h_w": 0,
                    "h2h_l": 0,
                    "vs_field_w": 0,
                    "vs_field_l": 0,
                    "rank": i,
                }
                for i, (rid, name) in enumerate(sorted(manager_map.items(), key=lambda kv: kv[1]), start=1)
            ],
            "weekly": {},
        }
    else:
        history = build_history(league_id, season, completed_weeks, manager_map)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(history, f, indent=2)
    print(f"[info] wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
