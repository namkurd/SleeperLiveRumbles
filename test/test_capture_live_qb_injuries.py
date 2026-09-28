#!/usr/bin/env python3
"""Unit tests for capture_live_qb_injuries.py -- no network access needed.
Run directly: `python3 test/test_capture_live_qb_injuries.py`

Covers the two pieces of logic this script adds on top of
build_rumbles.py's existing QB-adjustment helpers:
  1. normalize_game_status / build_team_game_schedule -- the Python port
     of rumbles.html's normalizeGameStatus/buildTeamGameSchedule.
  2. find_new_captures -- the actual "is this a genuinely LIVE
     corroboration worth durably logging" decision, exercised directly
     (no network calls) against synthetic matchups/stats/schedule data.

This is the regression suite for the real gap Ben found (Baker Mayfield/
Jalon Daniels, Week 3 2026): a backup QB who played is only ever captured
here while the started QB's own game is still pre_game or in_progress --
never once it's gone complete, and never when nothing corroborates
Out/IR/PUP in the first place.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capture_live_qb_injuries import (  # noqa: E402
    build_team_game_schedule,
    find_new_captures,
    is_out_status,
    normalize_game_status,
)

MANAGER_MAP = {1: "Alex", 2: "Ben"}
SCORING_SETTINGS = {"pass_yd": 0.04, "pass_td": 4, "pass_int": -2, "rush_yd": 0.1, "rush_td": 6}

PLAYERS_META = {
    "QB_STARTER": {"position": "QB", "team": "MIN", "full_name": "Kyler Murray", "injury_status": "Out"},
    "QB_BACKUP": {"position": "QB", "team": "MIN", "full_name": "Carson Wentz", "injury_status": None},
    "QB_OTHER_TEAM": {"position": "QB", "team": "KC", "full_name": "Other Team's QB", "injury_status": None},
}
TEAM_QB_INDEX = {"MIN": ["QB_STARTER", "QB_BACKUP"], "KC": ["QB_OTHER_TEAM"]}
STATS_MAP = {
    "QB_STARTER": {"pass_att": 10, "pass_yd": 80, "pass_td": 1, "pass_int": 0},  # 7.2
    "QB_BACKUP": {"pass_att": 25, "pass_yd": 210, "pass_td": 2, "pass_int": 1, "rush_yd": 15, "rush_td": 1},  # 21.9
}
MATCHUPS = [
    {"roster_id": 1, "matchup_id": 1, "starters": ["QB_STARTER", "WR1"], "points": 100.0},
    {"roster_id": 2, "matchup_id": 1, "starters": ["WR2"], "points": 90.0},
]


def make_games(min_status_fields):
    """One synthetic Sleeper /scores/nfl game entry for MIN @ KC, with
    `min_status_fields` merged onto its `metadata` so each test can drive
    normalize_game_status through a specific branch."""
    meta = {"away_team": "MIN", "home_team": "KC"}
    meta.update(min_status_fields)
    return [{"metadata": meta, "status": min_status_fields.get("status", "")}]


def test_is_out_status():
    assert is_out_status("Out") and is_out_status("ir") and is_out_status("PUP")
    assert not is_out_status("Questionable")
    assert not is_out_status(None)
    print("PASS: is_out_status matches Out/IR/PUP case-insensitively, nothing else")


def test_normalize_game_status_is_over_flag():
    assert normalize_game_status({"metadata": {"is_over": True}}) == "complete"
    print("PASS: metadata.is_over=True -> complete")


def test_normalize_game_status_is_in_progress_flag():
    assert normalize_game_status({"metadata": {"is_in_progress": True}}) == "in_progress"
    print("PASS: metadata.is_in_progress=True -> in_progress")


def test_normalize_game_status_final_string():
    assert normalize_game_status({"status": "Final", "metadata": {}}) == "complete"
    assert normalize_game_status({"status": "", "metadata": {"quarter": "F"}}) == "complete"
    print("PASS: a 'final' status string or quarter='F' both mean complete")


def test_normalize_game_status_in_progress_string():
    assert normalize_game_status({"status": "in_progress", "metadata": {}}) == "in_progress"
    assert normalize_game_status({"status": "halftime", "metadata": {}}) == "in_progress"
    print("PASS: an in-progress/halftime status string means in_progress")


def test_normalize_game_status_quarter_num_fallback():
    assert normalize_game_status({"status": "", "metadata": {"quarter_num": 2}}) == "in_progress"
    print("PASS: quarter_num >= 1 with no other signal still means in_progress")


def test_normalize_game_status_defaults_to_pre_game():
    assert normalize_game_status({"status": "", "metadata": {}}) == "pre_game"
    assert normalize_game_status(None) == "pre_game"
    print("PASS: no signal at all (or no game object) defaults to pre_game")


def test_build_team_game_schedule_maps_both_teams():
    games = make_games({"is_in_progress": True})
    sched = build_team_game_schedule(games)
    assert sched["MIN"]["status"] == "in_progress"
    assert sched["KC"]["status"] == "in_progress"
    print("PASS: build_team_game_schedule keys the same game entry under both the away and home team abbreviation")


def test_find_new_captures_logs_live_out_with_backup_action():
    games = make_games({"is_in_progress": True})
    schedule = build_team_game_schedule(games)
    already = set()
    captures = find_new_captures(
        3, "2026", MATCHUPS, PLAYERS_META, TEAM_QB_INDEX, STATS_MAP, SCORING_SETTINGS, MANAGER_MAP,
        schedule, already, "2026-09-28T20:14:03+00:00",
    )
    assert len(captures) == 1, f"expected exactly one capture, got {len(captures)}"
    c = captures[0]
    assert c["season"] == "2026" and c["week"] == 3 and c["roster_id"] == 1
    assert c["injured_qb"] == {"player_id": "QB_STARTER", "name": "Kyler Murray", "points": 7.2}
    assert c["backup_qbs"] == [{"player_id": "QB_BACKUP", "name": "Carson Wentz", "points": 21.9}]
    assert c["backup_points_total"] == 21.9
    assert c["team"] == "MIN"
    assert c["injury_status_at_capture"] == "Out"
    assert c["game_status_at_capture"] == "in_progress"
    assert c["captured_at"] == "2026-09-28T20:14:03+00:00"
    assert "2026:3:1:QB_STARTER" in already, "the caller's already_captured_keys set must be updated in place so a re-poll within the same run never double-captures"
    print("PASS: a backup who played while the starter reads 'Out' and the game is still in_progress is captured with the full expected shape")


def test_find_new_captures_pre_game_still_counts_as_live():
    # A pregame inactive (Out before kickoff) is just as legitimate a live
    # corroboration as one seen mid-game -- only "complete" excludes it.
    games = make_games({"is_over": False, "quarter_num": None})
    schedule = build_team_game_schedule(games)
    captures = find_new_captures(
        3, "2026", MATCHUPS, PLAYERS_META, TEAM_QB_INDEX, STATS_MAP, SCORING_SETTINGS, MANAGER_MAP,
        schedule, set(), "2026-09-28T18:00:00+00:00",
    )
    assert len(captures) == 1
    assert captures[0]["game_status_at_capture"] == "pre_game"
    print("PASS: a pre_game team status still yields a capture, not just in_progress")


def test_find_new_captures_skips_once_game_is_complete():
    # This is the exact Baker Mayfield case this whole script exists to
    # avoid mis-handling: once the game has gone final, this is no longer
    # a LIVE observation, so it must never be captured here at all (the
    # existing same-day injury_status fallback in build_rumbles.py, not
    # this script, is what still covers that case at the weaker tier).
    games = make_games({"is_over": True})
    schedule = build_team_game_schedule(games)
    captures = find_new_captures(
        3, "2026", MATCHUPS, PLAYERS_META, TEAM_QB_INDEX, STATS_MAP, SCORING_SETTINGS, MANAGER_MAP,
        schedule, set(), "2026-09-28T23:00:00+00:00",
    )
    assert captures == [], f"a completed game must never be captured, got {captures}"
    print("PASS: a completed game is never captured, even with a backup who played and an 'Out' status")


def test_find_new_captures_skips_missing_schedule_info():
    # No schedule entry at all for the started QB's team (feed failed, bye
    # week, whatever) -- same as "complete": no live observation is
    # possible, so nothing is captured.
    captures = find_new_captures(
        3, "2026", MATCHUPS, PLAYERS_META, TEAM_QB_INDEX, STATS_MAP, SCORING_SETTINGS, MANAGER_MAP,
        {}, set(), "2026-09-28T23:00:00+00:00",
    )
    assert captures == [], f"missing schedule info must never be captured, got {captures}"
    print("PASS: missing team schedule info is treated the same as 'complete' -- never captured")


def test_find_new_captures_skips_when_not_ruled_out():
    games = make_games({"is_in_progress": True})
    schedule = build_team_game_schedule(games)
    meta_questionable = dict(PLAYERS_META, QB_STARTER=dict(PLAYERS_META["QB_STARTER"], injury_status="Questionable"))
    captures = find_new_captures(
        3, "2026", MATCHUPS, meta_questionable, TEAM_QB_INDEX, STATS_MAP, SCORING_SETTINGS, MANAGER_MAP,
        schedule, set(), "2026-09-28T20:14:03+00:00",
    )
    assert captures == [], f"a non-Out/IR/PUP status must never be captured, got {captures}"
    print("PASS: 'Questionable' (or any non-Out/IR/PUP status) is never captured, live game or not")


def test_find_new_captures_skips_when_no_backup_played():
    games = make_games({"is_in_progress": True})
    schedule = build_team_game_schedule(games)
    stats_no_backup = {"QB_STARTER": STATS_MAP["QB_STARTER"]}  # QB_BACKUP recorded nothing
    captures = find_new_captures(
        3, "2026", MATCHUPS, PLAYERS_META, TEAM_QB_INDEX, stats_no_backup, SCORING_SETTINGS, MANAGER_MAP,
        schedule, set(), "2026-09-28T20:14:03+00:00",
    )
    assert captures == [], f"Out with no backup action yet must not be captured (nothing to credit), got {captures}"
    print("PASS: 'Out' with no backup QB having recorded any action yet is not captured -- there's nothing to log")


def test_find_new_captures_skips_already_captured_key():
    games = make_games({"is_in_progress": True})
    schedule = build_team_game_schedule(games)
    already = {"2026:3:1:QB_STARTER"}
    captures = find_new_captures(
        3, "2026", MATCHUPS, PLAYERS_META, TEAM_QB_INDEX, STATS_MAP, SCORING_SETTINGS, MANAGER_MAP,
        schedule, already, "2026-09-28T20:20:00+00:00",
    )
    assert captures == [], f"an already-captured (season, week, roster, player) key must never be re-captured, got {captures}"
    print("PASS: a key already present in already_captured_keys is never re-captured on a later poll")


def main():
    test_is_out_status()
    test_normalize_game_status_is_over_flag()
    test_normalize_game_status_is_in_progress_flag()
    test_normalize_game_status_final_string()
    test_normalize_game_status_in_progress_string()
    test_normalize_game_status_quarter_num_fallback()
    test_normalize_game_status_defaults_to_pre_game()
    test_build_team_game_schedule_maps_both_teams()
    test_find_new_captures_logs_live_out_with_backup_action()
    test_find_new_captures_pre_game_still_counts_as_live()
    test_find_new_captures_skips_once_game_is_complete()
    test_find_new_captures_skips_missing_schedule_info()
    test_find_new_captures_skips_when_not_ruled_out()
    test_find_new_captures_skips_when_no_backup_played()
    test_find_new_captures_skips_already_captured_key()
    print("\nALL capture_live_qb_injuries.py UNIT TESTS PASSED")


if __name__ == "__main__":
    main()
