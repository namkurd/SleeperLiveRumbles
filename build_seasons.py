#!/usr/bin/env python3
"""Builds seasons/<year>/ for every PAST season of the league, so the page can show any of them like the current one.

The league's earlier seasons are found by following Sleeper's previous_league_id from the current league. For each
finished season this writes (all static; a season never changes once it is over):

  seasons/<year>/rumbles_history.json   the same standings / weekly log the page reads for the current season
  seasons/<year>/epa_weekly.json        every week's starters and points (what Scenarios needs) plus EPA per player
  seasons/<year>/wp_timeline/           the play-by-play win-probability timelines (build_wp_timeline.py)
  seasons/index.json                    { "seasons": [ {season, league_id, teams, weeks, leader}, ... ] } newest first

Rumbles is scored exactly as for the current season (9 for the head-to-head win, 1 for every other team outscored that
week), over the regular season only: the weeks before the league's playoffs began. A 10-team league tops out at 18 a week.

Safe to run any time: a season already built is left alone (pass --force to rebuild). update.yml runs it so that when a
new season starts, the one that just ended is archived without anyone remembering to.
"""
from __future__ import annotations

import json
import os
import sys

import build_epa
import build_rumbles as BR
import build_wp_timeline as WP

HERE = os.path.dirname(os.path.abspath(__file__))
SEASONS_DIR = os.path.join(HERE, "seasons")
DEFAULT_LAST_REGULAR_WEEK = 15   # when a league never recorded when its playoffs start


def league_chain(current_league_id: str) -> list[dict]:
    """The current league's earlier seasons, newest first (the current league itself is not included)."""
    out, lid, seen = [], current_league_id, set()
    while lid and lid not in seen and lid != "0":
        seen.add(lid)
        league = BR.get_json(f"{BR.API_BASE}/league/{lid}")
        if not league:
            break
        out.append(league)
        lid = league.get("previous_league_id")
    return out[1:]


def regular_weeks(league: dict) -> list[int]:
    """Weeks 1..N before the playoffs began (N is playoff_week_start - 1 where the league recorded it)."""
    start = int((league.get("settings") or {}).get("playoff_week_start") or 0)
    last = start - 1 if start > 1 else DEFAULT_LAST_REGULAR_WEEK
    return list(range(1, last + 1))


def build_one(league: dict, force: bool = False) -> dict | None:
    season, lid = str(league["season"]), league["league_id"]
    if league.get("status") != "complete":
        print(f"[skip] {season}: league status is {league.get('status')!r}, not complete")
        return None
    out_dir = os.path.join(SEASONS_DIR, season)
    os.makedirs(out_dir, exist_ok=True)
    hist_path = os.path.join(out_dir, "rumbles_history.json")
    weeks = [w for w in regular_weeks(league) if BR.get_json(f"{BR.API_BASE}/league/{lid}/matchups/{w}")]
    if not weeks:
        print(f"[skip] {season}: no matchups found")
        return None
    manager_map = BR.build_manager_map(lid)
    old = json.load(open(hist_path)) if os.path.exists(hist_path) else {}
    if force or old.get("weeks_completed") != weeks or "extra_weekly" not in old:
        hist = BR.build_history(lid, season, "regular", weeks, manager_map)
        hist["playoff_spots"] = int((league.get("settings") or {}).get("playoff_teams") or 6)   # a 10-team league sent 4
        # the playoff weeks after the counted ones can be viewed in Scenarios but never count
        hist["extra_weekly"] = BR.build_extra_weekly(lid, list(range(weeks[-1] + 1, BR.LAST_WEEK + 1)), manager_map)
        with open(hist_path, "w") as fh:
            json.dump(hist, fh, separators=(",", ":"))
        print(f"[info] {season}: standings for {len(weeks)} weeks, {len(manager_map)} managers")
    else:
        hist = json.load(open(hist_path))
    epa_path = os.path.join(out_dir, "epa_weekly.json")
    all_weeks = weeks + sorted(int(w) for w in hist.get("extra_weekly") or {})
    have = []
    try:
        have = sorted(int(w) for w in json.load(open(epa_path)).get("lineups", {}))
    except Exception:  # noqa: BLE001
        pass
    if force or have != all_weeks:
        build_epa.build_season(int(season), lid, all_weeks, epa_path)
    if os.path.exists(epa_path):
        WP.run(epa_path, os.path.join(out_dir, "wp_timeline"), lid)
    top = hist["standings"][0] if hist.get("standings") else {}
    return {"season": season, "league_id": lid, "teams": len(manager_map), "weeks": len(weeks), "leader": top.get("manager"),
            "rumbles": top.get("rumbles")}


def main(argv: list[str]) -> None:
    force = "--force" in argv
    only = [a for a in argv if a.isdigit()]
    current = json.load(open(os.path.join(HERE, "rumbles_history.json")))["league_id"]
    built = []
    for league in league_chain(current):
        if only and str(league["season"]) not in only:
            continue
        try:
            row = build_one(league, force)
        except Exception as e:  # noqa: BLE001 - one bad season must not stop the others
            print(f"[warn] {league.get('season')}: {e}", file=sys.stderr)
            continue
        if row:
            built.append(row)
    # the index lists every season that has a history file on disk (so a partial run never drops one)
    index = {}
    try:
        for r in json.load(open(os.path.join(SEASONS_DIR, "index.json")))["seasons"]:
            index[r["season"]] = r
    except Exception:  # noqa: BLE001
        pass
    for r in built:
        index[r["season"]] = r
    index = {k: v for k, v in index.items() if os.path.exists(os.path.join(SEASONS_DIR, k, "rumbles_history.json"))}
    os.makedirs(SEASONS_DIR, exist_ok=True)
    with open(os.path.join(SEASONS_DIR, "index.json"), "w") as fh:
        json.dump({"current": str(json.load(open(os.path.join(HERE, "rumbles_history.json"))).get("season") or ""),
                   "seasons": sorted(index.values(), key=lambda r: r["season"], reverse=True)}, fh, separators=(",", ":"))
    print(f"[info] seasons/index.json lists {len(index)} past season(s)")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as e:  # noqa: BLE001 - archiving is an extra; never fail the standings build
        print(f"[warn] season archive failed: {e}", file=sys.stderr)
