#!/usr/bin/env python3
"""find_new_injuries (capture_live_qb_injuries.py): every STARTER, any position, ruled out during his own game. No network.
Run directly: `python3 test/test_capture_live_injuries.py`"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from capture_live_qb_injuries import find_new_injuries, build_team_game_schedule  # noqa: E402

META = {
    "WR1": {"position": "WR", "team": "AAA", "full_name": "Wide One", "injury_status": "Out"},        # ruled out in a live game, played
    "RB1": {"position": "RB", "team": "AAA", "full_name": "Back One", "injury_status": "Questionable"},  # not ruled out
    "RB2": {"position": "RB", "team": "AAA", "full_name": "Back Two", "injury_status": "Doubtful"},      # Doubtful does not count
    "TE1": {"position": "TE", "team": "AAA", "full_name": "Tight One", "injury_status": "Out"},         # Out but never took the field
    "K1": {"position": "K", "team": "BBB", "full_name": "Kick One", "injury_status": "IR"},             # his game is over
    "WR2": {"position": "WR", "team": "CCC", "full_name": "Wide Two", "injury_status": "Out"},         # his game has not started
    "QB1": {"position": "QB", "team": "AAA", "full_name": "Quarter One", "injury_status": "PUP"},      # a QB counts like anyone else
    "BN1": {"position": "WR", "team": "AAA", "full_name": "Bench One", "injury_status": "Out"},        # on the bench: not a starter
}
STATS = {"WR1": {"gp": 1, "rec": 2, "rec_yd": 10}, "RB1": {"gp": 1, "rush_att": 5}, "RB2": {"gp": 1, "rush_att": 3}, "K1": {"gp": 1},
         "QB1": {"gp": 1, "pass_att": 4}, "BN1": {"gp": 1, "rec": 1}}
SCHED = {"AAA": {"status": "in_progress", "second_half": False, "quarter": "2"}, "BBB": {"status": "complete"}, "CCC": {"status": "pre_game"}}
MATCHUPS = [{"roster_id": 1, "starters": ["WR1", "RB1", "RB2", "TE1", "K1", "WR2", "QB1", "NYG", "0"]},
            {"roster_id": 2, "starters": ["BN1x"]}]
SETTINGS = {"rec": 1.0, "rec_yd": 0.1, "rush_att": 0, "pass_yd": 0.04}


def run(keys=None):
    return find_new_injuries(8, "2026", MATCHUPS, META, STATS, SETTINGS, {1: "Alex", 2: "Ben"}, SCHED, set() if keys is None else keys, "2026-11-12T01:00:00+00:00")


got = run()
assert sorted(e["player"]["player_id"] for e in got) == ["QB1", "WR1"], [e["player"]["player_id"] for e in got]
wr = next(e for e in got if e["player"]["player_id"] == "WR1")
assert wr["manager"] == "Alex" and wr["roster_id"] == 1 and wr["player"]["position"] == "WR" and wr["player"]["points"] == 3.0, wr
assert wr["second_half_started"] is False and wr["quarter_at_capture"] == "2", wr
assert wr["injury_status_at_capture"] == "Out" and wr["game_status_at_capture"] == "in_progress" and wr["captured_at"].startswith("2026-11-12"), wr
late = dict(SCHED, AAA={"status": "in_progress", "second_half": True, "quarter": "3"})
assert all(e["second_half_started"] for e in find_new_injuries(8, "2026", MATCHUPS, META, STATS, SETTINGS, {1: "Alex"}, late, set(), "x")), "a ruling after the 2nd half began is marked as such"
keys = {"2026:8:1:WR1"}
assert [e["player"]["player_id"] for e in run(keys)] == ["QB1"], "an already-logged player is not logged twice"
sch = build_team_game_schedule([{"metadata": {"away_team": "AAA", "home_team": "BBB", "is_in_progress": True, "has3rd_quarter_started": True, "quarter": "3"}}])
assert sch["AAA"]["second_half"] is True and sch["BBB"]["quarter"] == "3" and sch["AAA"]["status"] == "in_progress", sch
print("PASS: every starter ruled out in his own game is captured (once); pregame, final, bench, Questionable and Doubtful are not")
