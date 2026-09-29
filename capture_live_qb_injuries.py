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
import subprocess
import sys
import time
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
                # Every backup seen playing so far (see update_chain_watch):
                # a NEW name showing up here later means the first backup
                # may have been hurt too, which starts watch mode again.
                "backups_seen": [b["player_id"] for b in backups],
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


# ---- Watch mode -----------------------------------------------------------
# The scheduled workflow only fires every 15 minutes (a GitHub Actions
# minutes-budget choice), which can miss an injury that happens late in a
# game. Watch mode closes that gap without polling fast all the time: when
# a check sees a situation that's about to matter, this same run keeps
# re-checking every WATCH_INTERVAL_SECONDS until it resolves, then exits.
#
#   1. Starter watch: a started QB who has played, whose same-team backup
#      has now recorded action, while the game is in progress, but whose
#      status isn't Out yet. Watch until his status flips to Out (that
#      poll's find_new_captures logs it with a timestamp) or the game ends.
#   2. Chain watch: after a capture, if a DIFFERENT backup enters the game
#      (a new name among the backups who've played), the earlier backup may
#      have been hurt too. Watch until that earlier backup's status flips to
#      Out (logged under the capture's backup_injuries with its own
#      timestamp) or the game ends.
#
# Minutes are only spent while one of these is actually happening, so the
# cost is a few extra minutes on the rare weeks it triggers.
WATCH_INTERVAL_SECONDS = 5 * 60
# Hard stop for a single run, well under the workflow's own timeout, so a
# stuck/very long game can never burn unbounded Actions minutes.
MAX_WATCH_MINUTES = 150


def capture_key(season, week, roster_id, player_id) -> str:
    return f"{season}:{week}:{roster_id}:{player_id}"


def find_starter_watch_targets(
    week: int,
    season: str,
    matchups: list[dict],
    players_meta: dict[str, dict],
    team_qb_index: dict[str, list[str]],
    stats_map: dict[str, dict],
    scoring_settings: dict,
    team_game_schedule: dict[str, dict],
    already_captured_keys: set[str],
) -> list[str]:
    """Starter watch (see above): one description per started QB who has
    played, has a same-team backup who's now recorded action, is in a game
    that's in progress, and isn't listed Out yet (not captured)."""
    targets: list[str] = []
    for m in matchups:
        roster_id = m.get("roster_id")
        if roster_id is None:
            continue
        started_qbs = find_started_qbs(m, players_meta)
        started_pids = {pid for pid, _ in started_qbs}
        for pid, meta in started_qbs:
            if capture_key(season, week, roster_id, pid) in already_captured_keys:
                continue
            team = meta.get("team")
            game = team_game_schedule.get(team) if team else None
            if not game or game["status"] != "in_progress":
                continue
            if not is_played(stats_map.get(pid)):
                continue
            if is_out_status(meta.get("injury_status")):
                continue  # find_new_captures handles this one on this same poll
            backups = find_backup_qbs(pid, meta, players_meta, team_qb_index, stats_map, scoring_settings, started_pids)
            if backups:
                targets.append(
                    f"roster {roster_id}: {player_name(meta)} not Out yet, but {[b['name'] for b in backups]} "
                    f"has come in -- watching for his status to change"
                )
    return targets


def update_chain_watch(
    log: dict,
    week: int,
    season: str,
    matchups: list[dict],
    players_meta: dict[str, dict],
    team_qb_index: dict[str, list[str]],
    stats_map: dict[str, dict],
    scoring_settings: dict,
    team_game_schedule: dict[str, dict],
    now_iso: str,
) -> tuple[bool, list[str]]:
    """Chain watch (see above), applied in place to this week's existing
    captures. Returns (changed, watch descriptions)."""
    changed = False
    targets: list[str] = []
    started_by_roster = {}
    for m in matchups:
        if m.get("roster_id") is not None:
            started_by_roster[m["roster_id"]] = {pid for pid, _ in find_started_qbs(m, players_meta)}
    for entry in log.get("captures", []):
        if str(entry.get("season")) != str(season) or entry.get("week") != week:
            continue
        starter = (entry.get("injured_qb") or {}).get("player_id")
        meta = players_meta.get(starter) if starter else None
        if not meta:
            continue
        team = meta.get("team") or entry.get("team")
        game = team_game_schedule.get(team) if team else None
        game_live = bool(game) and game["status"] == "in_progress"
        watch_for = list(entry.get("chain_watch_for") or [])
        if not game_live:
            if watch_for:
                # Game's over: nothing more can be caught live.
                entry["chain_watch_for"] = []
                changed = True
            continue

        started_pids = started_by_roster.get(entry.get("roster_id"), {starter})
        current = find_backup_qbs(starter, meta, players_meta, team_qb_index, stats_map, scoring_settings, started_pids)
        current_ids = [b["player_id"] for b in current]
        seen = list(entry.get("backups_seen") or [b.get("player_id") for b in entry.get("backup_qbs") or []])
        already_hurt = {b.get("player_id") for b in entry.get("backup_injuries") or []}
        newcomers = [pid for pid in current_ids if pid not in seen]
        if newcomers:
            for pid in seen:
                if pid not in already_hurt and pid not in watch_for:
                    watch_for.append(pid)
            entry["backups_seen"] = seen + newcomers
            changed = True
            names = [player_name(players_meta.get(pid)) for pid in newcomers]
            print(f"[watch] week {week} roster {entry.get('roster_id')}: new backup {names} entered -- watching the earlier backup(s)")

        still_watching = []
        for pid in watch_for:
            b_meta = players_meta.get(pid) or {}
            status = b_meta.get("injury_status")
            if is_out_status(status):
                entry.setdefault("backup_injuries", []).append({
                    "player_id": pid,
                    "name": player_name(b_meta),
                    "injury_status_at_capture": status,
                    "captured_at": now_iso,
                })
                changed = True
                print(f"[capture] week {week} roster {entry.get('roster_id')}: backup {player_name(b_meta)} ({status}) during the game")
            else:
                still_watching.append(pid)
        if still_watching != list(entry.get("chain_watch_for") or []):
            entry["chain_watch_for"] = still_watching
            changed = True
        for pid in still_watching:
            targets.append(
                f"roster {entry.get('roster_id')}: a new backup entered, watching {player_name(players_meta.get(pid))} for a status change to Out"
            )
    return changed, targets


def fetch_poll_data() -> dict | None:
    """Everything one poll needs, or None when there's nothing to watch or
    a fetch failed (never raises -- a best-effort poll must never break
    the scheduled workflow)."""
    try:
        state = get_json(f"{API_BASE}/state/nfl")
        season = state["season"]
        season_type = state.get("season_type") or "regular"
        week = state.get("week")
        if not week or season_type not in ("regular", "post"):
            print(f"[info] no live week to watch right now (season_type={season_type!r}, week={week!r}); nothing to do")
            return None

        league_id = discover_current_league_id(season)
        manager_map = build_manager_map(league_id)
        league = get_json(f"{API_BASE}/league/{league_id}")
        scoring_settings = league.get("scoring_settings") or {}
        players_meta = get_json(f"{API_BASE}/players/nfl") or {}
        team_qb_index = build_team_qb_index(players_meta)

        matchups = get_json(f"{API_BASE}/league/{league_id}/matchups/{week}")
        if not matchups:
            print(f"[info] no matchups posted yet for week {week}; nothing to do")
            return None

        stats_arr = get_json(f"{STATS_BASE}/{season}/{week}?season_type={season_type}")
        stats_map = array_to_player_map(stats_arr)

        games = get_json(f"{SCORES_BASE}/{season_type}/{season}/{week}")
        team_game_schedule = build_team_game_schedule(games)
    except Exception as e:  # noqa: BLE001 - a best-effort poll; never allowed to break the scheduled workflow
        print(f"[warn] couldn't fetch data for this poll ({e}); leaving the capture log untouched", file=sys.stderr)
        return None
    return {
        "season": season, "week": week, "manager_map": manager_map, "scoring_settings": scoring_settings,
        "players_meta": players_meta, "team_qb_index": team_qb_index, "matchups": matchups,
        "stats_map": stats_map, "team_game_schedule": team_game_schedule,
    }


def commit_captures_now() -> None:
    """Commit and push live_qb_captures.json right away, so a capture made
    partway through a long watch shows up on the page without waiting for
    the whole run to finish. Only when CAPTURE_COMMIT_EACH_POLL=1 (set by
    the workflow); never fatal -- the workflow's final commit step is the
    backstop."""
    if os.environ.get("CAPTURE_COMMIT_EACH_POLL") != "1":
        return
    try:
        subprocess.run(["git", "add", LIVE_CAPTURES_PATH], check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        subprocess.run(["git", "commit", "-m", f"Capture live QB-injury corroboration ({stamp})"], check=True)
        for _ in range(3):
            subprocess.run(["git", "pull", "--rebase", "--quiet"], check=False)
            if subprocess.run(["git", "push", "--quiet"]).returncode == 0:
                print("[info] pushed live_qb_captures.json")
                return
            time.sleep(5)
        print("[warn] couldn't push live_qb_captures.json yet; the workflow's final step will retry", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] commit of live_qb_captures.json failed ({e}); the workflow's final step will retry", file=sys.stderr)


def run_poll() -> list[str]:
    """One check. Logs any new captures (starter or chain) and returns the
    watch descriptions still open after it (empty = nothing to watch)."""
    data = fetch_poll_data()
    if data is None:
        return []
    season, week = data["season"], data["week"]
    now_iso = datetime.now(timezone.utc).isoformat()

    log = load_existing_captures()
    already_captured_keys = {
        capture_key(entry.get("season"), entry.get("week"), entry.get("roster_id"), (entry.get("injured_qb") or {}).get("player_id"))
        for entry in log["captures"]
    }

    new_captures = find_new_captures(
        week, season, data["matchups"], data["players_meta"], data["team_qb_index"], data["stats_map"],
        data["scoring_settings"], data["manager_map"], data["team_game_schedule"], already_captured_keys, now_iso,
    )
    log["captures"].extend(new_captures)

    chain_changed, chain_targets = update_chain_watch(
        log, week, season, data["matchups"], data["players_meta"], data["team_qb_index"], data["stats_map"],
        data["scoring_settings"], data["team_game_schedule"], now_iso,
    )
    starter_targets = find_starter_watch_targets(
        week, season, data["matchups"], data["players_meta"], data["team_qb_index"], data["stats_map"],
        data["scoring_settings"], data["team_game_schedule"], already_captured_keys,
    )

    if new_captures or chain_changed:
        with open(LIVE_CAPTURES_PATH, "w") as f:
            json.dump(log, f, indent=2)
        print(f"[info] wrote {len(new_captures)} new capture(s) to {LIVE_CAPTURES_PATH}" + (" (and updated backup tracking)" if chain_changed else ""))
        commit_captures_now()
    else:
        print(f"[info] poll complete -- no new live QB-injury corroborations this run (week {week}, {len(log['captures'])} existing capture(s))")
    return starter_targets + chain_targets


def main() -> None:
    started = time.monotonic()
    while True:
        targets = run_poll()
        if not targets:
            return
        for t in targets:
            print(f"[watch] {t}")
        elapsed_min = (time.monotonic() - started) / 60
        if elapsed_min + WATCH_INTERVAL_SECONDS / 60 > MAX_WATCH_MINUTES:
            print(f"[watch] reached the {MAX_WATCH_MINUTES}-minute cap for one run; the next scheduled check picks it back up")
            return
        print(f"[watch] checking again in {WATCH_INTERVAL_SECONDS // 60} minutes")
        time.sleep(WATCH_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
