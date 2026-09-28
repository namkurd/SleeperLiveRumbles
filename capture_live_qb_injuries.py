#!/usr/bin/env python3
"""
capture_live_qb_injuries.py

Durable, timestamped audit log of genuinely-LIVE QB-injury-backup
corroborations for the DTF Club Rumbles page (see rumbles.html's
detectQbAdjustmentsForWeek and build_rumbles.py's
compute_qb_adjustments_for_week -- both read this script's output via
build_rumbles.load_live_qb_captures).

THE PROBLEM THIS SOLVES (Sep 28 2026, Baker Mayfield/Jalon Daniels):
Sleeper's injury_status field is a live-only, non-historical snapshot --
it has no timestamp and no memory of WHEN a player was actually marked
"Out", so there's no way to tell "ruled out live, mid-game" apart from "a
routine post-game roster-status update that happened to land on the same
field". Both rumbles.html's live client-side detection and
build_rumbles.py's own once-a-day "is_fresh" check share this exact blind
spot, because neither one can observe injury_status DURING the game --
build_rumbles.py in particular only ever runs after a week is already
fully complete, so its same-day check can *never* draw that distinction on
its own, no matter how "fresh" the run is.

THE FIX: this script runs on a much tighter schedule (a GitHub Actions
workflow, every ~15 minutes, only during actual NFL game windows -- see
.github/workflows/live_qb_capture.yml) and watches for the one thing
that's unambiguous: a backup QB recording real statistical action
(is_played, via find_backup_qbs) while Sleeper's injury_status for the
started QB in front of him reads Out/IR/PUP AND that started QB's own
game has not yet gone final. The instant that combination is observed,
it's durably logged with a UTC timestamp to live_qb_captures.json
(committed straight to the repo, so every visitor -- not just this one
polling run -- can see it). Both consumers above treat a matching entry
here as the single most trustworthy signal available, strictly
outranking the old same-day injury_status fallback, which remains in
place only to cover whatever gap this script's own polling window might
miss (a game that goes final between two 15-minute polls, this workflow
being paused, etc).

Design notes:
  - Only ever looks at the CURRENT live/upcoming week (state['week']) --
    once a week goes fully complete there is nothing left to catch live;
    every game in a "completed" week is, by definition, already final by
    the time any later poll would run, the exact trap this script exists
    to avoid falling into.
  - Entries are additive and de-duplicated by (season, week, roster_id,
    player_id) -- once a case is captured it's captured for good; later
    polls skip it rather than re-logging or overwriting it, since
    injury_status/game state can keep changing after that point without
    needing (or wanting) a second capture.
  - Never fatal: any fetch/parse error is logged to stderr and the script
    exits without touching the existing file, so a transient Sleeper API
    hiccup during a live window never breaks the scheduled workflow or
    clobbers previously-captured entries.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from build_rumbles import (
    API_BASE,
    STATS_BASE,
    LIVE_CAPTURES_PATH,
    get_json,
    discover_current_league_id,
    build_manager_map,
    build_team_qb_index,
    find_started_qbs,
    find_backup_qbs,
    array_to_player_map,
    player_name,
    dot_product,
    is_played,
)

# Sleeper's live-scoreboard feed -- same endpoint rumbles.html's own
# SCORES_BASE reads client-side, used here purely for each team's
# pre_game/in_progress/complete status (see normalize_game_status below).
SCORES_BASE = "https://api.sleeper.app/scores/nfl"


def is_out_status(status: str | None) -> bool:
    """Mirrors rumbles.html's isOutStatus() -- the one signal this script
    (and both its consumers) trust for "currently ruled out"."""
    s = (status or "").strip().lower()
    return s in ("out", "ir", "pup")


def normalize_game_status(game: dict | None) -> str:
    """Ports rumbles.html's normalizeGameStatus() to Python line-for-line
    -- see that function's own comment for the fallback chain's reasoning
    (is_over/is_in_progress flags first, then status-string matching, then
    a quarter_num fallback for a payload shape that has neither)."""
    game = game or {}
    meta = game.get("metadata") or {}
    if meta.get("is_over") is True:
        return "complete"
    if meta.get("is_in_progress") is True:
        return "in_progress"

    status = str(game.get("status") or "").lower()
    if (
        "final" in status
        or status == "complete"
        or status == "post_game"
        or game.get("quarter") == "F"
        or meta.get("quarter") == "F"
    ):
        return "complete"
    if "progress" in status or status in ("halftime", "live", "in_game"):
        return "in_progress"

    try:
        q_num = int(meta.get("quarter_num"))
    except (TypeError, ValueError):
        q_num = None
    if q_num is not None and q_num >= 1:
        return "in_progress"
    return "pre_game"


def build_team_game_schedule(games: list[dict] | None) -> dict[str, dict]:
    """Ports rumbles.html's buildTeamGameSchedule() to Python -- only the
    `status` field is actually consulted by this script (it never blends
    live projections the way rumbles.html does), but the lookup is built
    the same way for parity with the client and easier debugging."""
    out: dict[str, dict] = {}
    for g in games or []:
        if not g:
            continue
        meta = g.get("metadata") or {}
        status = normalize_game_status(g)
        entry = {"status": status}
        away = meta.get("away_team") or g.get("away_team")
        home = meta.get("home_team") or g.get("home_team")
        if away:
            out[away] = entry
        if home:
            out[home] = entry
    return out


def load_existing_captures() -> dict:
    """The full {"captures": [...]} document as it currently exists on
    disk, or a fresh empty one if the file is missing/unreadable -- never
    raises, since a corrupt/missing file just means "nothing captured
    yet", exactly like load_live_qb_captures's own fallback."""
    if not os.path.exists(LIVE_CAPTURES_PATH):
        return {"captures": []}
    try:
        with open(LIVE_CAPTURES_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("captures"), list):
            return {"captures": []}
        return data
    except Exception as e:  # noqa: BLE001
        print(f"[warn] couldn't read existing {LIVE_CAPTURES_PATH} ({e}); starting from an empty log", file=sys.stderr)
        return {"captures": []}


def find_new_captures(
    week: int,
    season: str,
    matchups: list[dict],
    players_meta: dict[str, dict],
    team_qb_index: dict[str, list[str]],
    stats_map: dict[str, dict],
    scoring_settings: dict,
    manager_map: dict[int, str],
    team_game_schedule: dict[str, dict],
    already_captured_keys: set[str],
    now_iso: str,
) -> list[dict]:
    """The actual detection logic, factored out from main() so it's
    directly unit-testable without any network calls -- given already-
    fetched data, returns every NEW capture-worthy entry this poll finds
    (empty list if none)."""
    new_captures: list[dict] = []
    for m in matchups:
        roster_id = m.get("roster_id")
        if roster_id is None:
            continue
        started_qbs = find_started_qbs(m, players_meta)
        if not started_qbs:
            continue
        started_pids = {pid for pid, _ in started_qbs}

        for pid, meta in started_qbs:
            key = f"{season}:{week}:{roster_id}:{pid}"
            if key in already_captured_keys:
                continue  # already durably captured on a prior poll -- nothing new to log

            team = meta.get("team")
            game = team_game_schedule.get(team) if team else None
            game_status = game["status"] if game else None
            if game_status != "in_progress":
                # The rule only covers in-game injuries, so only a game that
                # is actually being played counts. A finished game (or one
                # with no schedule info) can't be observed live, and a
                # pre-kickoff "Out" is a pregame ruling that never
                # qualifies -- mirrors detectQbAdjustmentsForWeek's
                # client-side gameStillLive gate.
                continue

            if not is_played(stats_map.get(pid)):
                # The started QB never took the field himself (inactive or
                # benched before kickoff), so a backup playing in his place
                # is not an in-game injury.
                continue

            injury_status = meta.get("injury_status")
            if not is_out_status(injury_status):
                continue  # not (yet) ruled out -- nothing to capture this poll

            backups = find_backup_qbs(pid, meta, players_meta, team_qb_index, stats_map, scoring_settings, started_pids)
            if not backups:
                continue  # Out, but no backup has recorded any action yet -- keep watching on the next poll

            manager = manager_map.get(roster_id, f"Roster {roster_id}")
            entry = {
                "season": season,
                "week": week,
                "roster_id": roster_id,
                "manager": manager,
                "injured_qb": {
                    "player_id": pid,
                    "name": player_name(meta),
                    "points": round(dot_product(stats_map.get(pid), scoring_settings), 2),
                },
                "backup_qbs": backups,
                "backup_points_total": round(sum(b["points"] for b in backups), 2),
                "team": team,
                "injury_status_at_capture": injury_status,
                "game_status_at_capture": game_status,
                "captured_at": now_iso,
            }
            new_captures.append(entry)
            already_captured_keys.add(key)
            print(
                f"[capture] week {week} roster {roster_id} ({manager}): {entry['injured_qb']['name']} "
                f"({injury_status}, game {game_status}) -> backup(s) {[b['name'] for b in backups]}"
            )
    return new_captures


def main() -> None:
    try:
        state = get_json(f"{API_BASE}/state/nfl")
        season = state["season"]
        season_type = state.get("season_type") or "regular"
        week = state.get("week")
        if not week or season_type not in ("regular", "post"):
            print(f"[info] no live week to watch right now (season_type={season_type!r}, week={week!r}); nothing to do")
            return

        league_id = discover_current_league_id(season)
        manager_map = build_manager_map(league_id)
        league = get_json(f"{API_BASE}/league/{league_id}")
        scoring_settings = league.get("scoring_settings") or {}
        players_meta = get_json(f"{API_BASE}/players/nfl") or {}
        team_qb_index = build_team_qb_index(players_meta)

        matchups = get_json(f"{API_BASE}/league/{league_id}/matchups/{week}")
        if not matchups:
            print(f"[info] no matchups posted yet for week {week}; nothing to do")
            return

        stats_arr = get_json(f"{STATS_BASE}/{season}/{week}?season_type={season_type}")
        stats_map = array_to_player_map(stats_arr)

        games = get_json(f"{SCORES_BASE}/{season_type}/{season}/{week}")
        team_game_schedule = build_team_game_schedule(games)
    except Exception as e:  # noqa: BLE001 - a best-effort poll; never allowed to break the scheduled workflow
        print(f"[warn] couldn't fetch data for this poll ({e}); leaving the capture log untouched", file=sys.stderr)
        return

    log = load_existing_captures()
    already_captured_keys = {
        f"{entry.get('season')}:{entry.get('week')}:{entry.get('roster_id')}:{(entry.get('injured_qb') or {}).get('player_id')}"
        for entry in log["captures"]
    }

    new_captures = find_new_captures(
        week,
        season,
        matchups,
        players_meta,
        team_qb_index,
        stats_map,
        scoring_settings,
        manager_map,
        team_game_schedule,
        already_captured_keys,
        datetime.now(timezone.utc).isoformat(),
    )

    if not new_captures:
        print(f"[info] poll complete -- no new live QB-injury corroborations this run (week {week}, {len(log['captures'])} existing capture(s))")
        return

    log["captures"].extend(new_captures)
    with open(LIVE_CAPTURES_PATH, "w") as f:
        json.dump(log, f, indent=2)
    print(f"[info] wrote {len(new_captures)} new capture(s) to {LIVE_CAPTURES_PATH}")


if __name__ == "__main__":
    main()
