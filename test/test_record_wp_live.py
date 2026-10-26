"""Unit test for record_wp_live (no network): clocks, labels, and folding polls into a recording."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import record_wp_live as R

# game clock: same arithmetic as rumbles.html remainingFraction
assert R.remaining_permille({"quarter_num": "1", "time_remaining": "15:00"}, "in_progress") == 1000
assert R.remaining_permille({"quarter_num": "2", "time_remaining": "07:30"}, "in_progress") == 625   # 22.5 of 60 minutes gone
assert R.remaining_permille({"quarter_num": "4", "time_remaining": "00:00"}, "in_progress") == 0
assert R.remaining_permille({"quarter_num": "5", "time_remaining": "05:00"}, "in_progress") == 0     # overtime
assert R.remaining_permille({}, "pre_game") == 1000 and R.remaining_permille({}, "complete") == 0

# labels from a stat-line change
L = R.label_from_stats
assert L({"rec_yd": 20}, {"rec_yd": 55, "rec_td": 1}) == "35 yd TD catch"
assert L({}, {"rush_yd": 12}) == "12 yd run"
assert L({"pass_yd": 100}, {"pass_yd": 100, "pass_int": 1}) == "interception thrown"
assert L({}, {"fgm_40_49": 1}) == "field goal" and L({"fgm_40_49": 1}, {"fgm_40_49": 1, "fgmiss_30_39": 1}) == "missed field goal"
assert L({}, {"sack": 1}) == "sack" and L({"sack": 1}, {"sack": 1, "def_td": 1}) == "defensive / return TD"
assert R.scoring_stats({"rec_yd": 5, "gp": 1, "pass_att": 3, "rec": 0, "fgm_20_29": 1}) == {"rec_yd": 5, "fgm_20_29": 1}

# folding polls in
def game(status, rem, start=1_000_000_000_000):
    return {"key": "DAL@PHI", "away": "DAL", "home": "PHI", "status": status, "start_ms": start, "rem": rem}

doc = R.empty_doc(2026, 2)
t0 = 1_000_000_000_000
assert R.apply_poll(doc, {"games": [game("pre_game", 1000)], "points": {"A": 0.0, "SEA": 0.0}, "team": {"A": "DAL", "SEA": "SEA"},
                          "stats": {}, "pre": {"A": 14.5}}, t0 - 600_000)
assert doc["t0"] == t0 and doc["pre"] == {"A": 14.5} and doc["tg"]["DAL"] == "DAL@PHI" and doc["ev"] == [] and doc["g"]["DAL@PHI"]["clk"] == []
# kickoff, then a 35-yard TD catch (6 + 3.5 + first-down bonus the page doesn't need to know about)
R.apply_poll(doc, {"games": [game("in_progress", 900)], "points": {"A": 0.0}, "team": {}, "stats": {}}, t0 + 60_000)
R.apply_poll(doc, {"games": [game("in_progress", 800)], "points": {"A": 9.5}, "team": {},
                   "stats": {"A": {"rec_yd": 35, "rec_td": 1, "rec": 1}}, "pre": {"A": 99}}, t0 + 130_000)
assert doc["ev"] == [[130, "A", 9.5, "35 yd TD catch"]], doc["ev"]
assert doc["pre"]["A"] == 14.5, "the last PREGAME projection is kept once the game has started"
clk = doc["g"]["DAL@PHI"]["clk"]
assert clk[0] == [0, 1000] and clk[-1] == [130, 800], clk
# a poll that sees nothing new changes nothing; a later change records only the difference
assert not R.apply_poll(doc, {"games": [game("in_progress", 800)], "points": {"A": 9.5}, "team": {}, "stats": {"A": {"rec_yd": 35, "rec_td": 1}}}, t0 + 140_000)
R.apply_poll(doc, {"games": [game("complete", 0)], "points": {"A": 11.5}, "team": {}, "stats": {"A": {"rec_yd": 55, "rec_td": 1}}}, t0 + 9_000_000)
assert doc["ev"][-1] == [9000, "A", 2.0, "20 yd catch"] and doc["g"]["DAL@PHI"]["end"] == 9000 and doc["g"]["DAL@PHI"]["clk"][-1] == [9000, 0]
assert round(sum(e[2] for e in doc["ev"]), 2) == 11.5, "the recording adds up to the player's real total"

# pacing
assert R.seconds_until_next([game("in_progress", 500)], t0) == R.LIVE_INTERVAL_SECONDS
assert R.seconds_until_next([game("pre_game", 1000, t0 + 3600_000)], t0) == 15 * 60
assert R.seconds_until_next([game("pre_game", 1000, t0 + 100_000)], t0) == 40
assert R.seconds_until_next([game("pre_game", 1000, t0 + 7 * 3600_000)], t0) is None
assert R.seconds_until_next([game("complete", 0)], t0) is None
print("test_record_wp_live: OK")
