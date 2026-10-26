"""Unit test for build_wp_timeline (no network): play scoring, the real-time clock, and reconciliation to Sleeper's finals."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import build_wp_timeline as B

S = dict(B.DEFAULT_SCORING)
G2S = {"G-QB": "1", "G-WR": "2", "G-RB": "3", "G-K": "4"}
POS = {"1": "QB", "2": "WR", "3": "RB", "4": "K", "SEA": "DEF"}


def play(**kw):
    row = {"season_type": "REG", "week": "1", "game_id": "g1", "home_team": "SEA", "away_team": "LA", "posteam": "LA", "defteam": "SEA",
           "qtr": "1", "game_seconds_remaining": "3000", "drive": "1", "play_id": "1", "drive_real_start_time": "2026-09-13T17:00:00.000Z"}
    row.update(kw)
    return row


def comps(row):
    return {(sid, round(p, 2)): label for sid, p, label, team in B.play_components(row, S, G2S, POS)}


# a 30-yard TD pass that is not a first down: QB 30*0.04+4, WR 1+3+6, no first-down bonus on a touchdown
c = comps(play(play_type="pass", complete_pass="1", passing_yards="30", receiving_yards="30", pass_touchdown="1", touchdown="1",
               td_team="LA", passer_player_id="G-QB", receiver_player_id="G-WR", first_down_pass="1"))
assert c == {("1", 5.2): "30 yd TD pass", ("2", 10.0): "30 yd TD catch"}, c
# a first-down completion: position-keyed bonus (QB 0.2, WR 0.5)
c = comps(play(play_type="pass", complete_pass="1", passing_yards="10", receiving_yards="10", passer_player_id="G-QB",
               receiver_player_id="G-WR", first_down_pass="1"))
assert c == {("1", 0.6): "10 yd pass", ("2", 2.5): "10 yd catch"}, c
# interception: QB -2 and the defense +2; a sack: defense +1, QB -0.25
c = comps(play(play_type="pass", interception="1", passer_player_id="G-QB"))
assert c == {("1", -2.0): "interception", ("SEA", 2.0): "interception"}, c
c = comps(play(play_type="pass", sack="1", passer_player_id="G-QB"))
assert c == {("SEA", 1.0): "sack", ("1", -0.25): "sacked"}, c
# fumble lost on a run: rusher yards and -2 (and -0.25 for the fumble); the defense recovers
c = comps(play(play_type="run", rushing_yards="5", rusher_player_id="G-RB", fumble="1", fumble_lost="1", fumbled_1_player_id="G-RB",
               fumble_recovery_1_team="SEA"))
assert c[("3", round(0.5 - 2 - 0.25, 2))] == "fumble lost" and c[("SEA", 2.0)] == "fumble recovery", c
# kicker: 45 yd field goal = 3 + 15 * 0.1; a miss is -1; no_play scores nothing
c = comps(play(play_type="field_goal", field_goal_result="made", kick_distance="45", kicker_player_id="G-K"))
assert c == {("4", 4.5): "45 yd field goal"}, c
c = comps(play(play_type="field_goal", field_goal_result="missed", kick_distance="50", kicker_player_id="G-K"))
assert c == {("4", -1.0): "missed 50 yd field goal"}, c
assert comps(play(play_type="no_play", passer_player_id="G-QB")) == {}
# two-point conversions
c = comps(play(play_type="pass", two_point_attempt="1", two_point_conv_result="success", passer_player_id="G-QB", receiver_player_id="G-WR", passing_yards="2"))
assert c == {("1", 2.0): "2-pt pass", ("2", 2.0): "2-pt catch"}, c

# the real clock: drives carry the real start time, so a play lands between its drive's anchors
rows = [play(drive="1", game_seconds_remaining="3600", drive_real_start_time="2026-09-13T17:00:00.000Z"),
        play(drive="2", game_seconds_remaining="3000", drive_real_start_time="2026-09-13T17:20:00.000Z"),
        play(drive="3", game_seconds_remaining="2400", drive_real_start_time="2026-09-13T17:40:00.000Z")]
clk = B.GameClock(rows)
t0 = B.parse_iso_ms("2026-09-13T17:00:00.000Z")
assert clk.ms(0) == t0 and clk.ms(600) == t0 + 20 * 60 * 1000 and clk.ms(300) == t0 + 10 * 60 * 1000, (clk.ms(0), clk.ms(300))
assert clk.ms(1200) == t0 + 40 * 60 * 1000 and clk.ms(1260) > clk.ms(1200), "past the last drive it keeps going at a game pace"

# a whole week: one game, a TD pass at 2:00 left and a field goal, with Sleeper's finals differing a little
base = dict(week="1", home_team="SEA", away_team="LA", total_home_score="3", total_away_score="7", season_type="REG", game_id="g1")
rows = [
    play(drive="1", play_id="1", game_seconds_remaining="3600", play_type="kickoff", **base),
    play(drive="1", play_id="2", game_seconds_remaining="3000", play_type="pass", complete_pass="1", passing_yards="30", receiving_yards="30",
         pass_touchdown="1", touchdown="1", td_team="LA", passer_player_id="G-QB", receiver_player_id="G-WR", **base),
    play(drive="2", play_id="3", game_seconds_remaining="1200", drive_real_start_time="2026-09-13T18:00:00.000Z", posteam="SEA", defteam="LA",
         play_type="field_goal", field_goal_result="made", kick_distance="30", kicker_player_id="G-K", **base),
    play(drive="3", play_id="4", game_seconds_remaining="0", drive_real_start_time="2026-09-13T19:00:00.000Z", play_type="run", rushing_yards="0", **base),
]
lineup = {"1": [["1", 5.2], ["2", 10.5], ["4", 3.0]], "2": [["SEA", 12.0]]}   # WR was credited +0.5 more by Sleeper
tl = B.build_week(1, rows, lineup, G2S, S, {}, None, 2026, POS)
assert tl["src"] == "pbp" and tl["tm"]["1"] == "LAR" and tl["tm"]["4"] == "SEA" and tl["tg"]["LAR"] == "LAR@SEA", tl["tm"]
tot = {}
for sec, sid, pts, label in tl["ev"]:
    tot[sid] = round(tot.get(sid, 0) + pts, 2)
assert tot == {"1": 5.2, "2": 10.5, "4": 3.0, "SEA": 12.0}, f"every chart ends on Sleeper's real final: {tot}"
assert [e[0] for e in tl["ev"]] == sorted(e[0] for e in tl["ev"]), "time ordered"
adj = [e for e in tl["ev"] if e[3] == "scoring adjustment"]
assert any(e[1] == "2" and e[2] == 0.5 for e in adj), adj
g = tl["g"]["LAR@SEA"]
assert g["clk"][0] == [g["ko"], 1000] and g["clk"][-1][1] == 0 and g["end"] >= g["clk"][-1][0], g
assert 0.5 < tl["q"] <= 1 and tl["pre"]["1"] > 0 and tl["preSrc"] == "avg"

# a week still in progress: players whose game is not in the plays yet are left alone (no end-of-week adjustment)
lineup2 = {"1": [["1", 5.2], ["2", 10.5], ["9", 7.0]], "2": [["SEA", 12.0]]}      # "9" plays in a game that has not been published
pos2 = dict(POS, **{"9": "WR"})
part = B.build_week(1, rows, lineup2, G2S, S, {}, None, 2026, pos2, partial=True)
tot2 = {}
for sec, sid, pts, label in part["ev"]:
    tot2[sid] = round(tot2.get(sid, 0) + pts, 2)
assert "9" not in tot2 and tot2.get("1") == 5.2 and tot2.get("SEA") == 12.0, tot2
full = B.build_week(1, rows, lineup2, G2S, S, {}, None, 2026, pos2)
assert round(sum(e[2] for e in full["ev"] if e[1] == "9"), 2) == 7.0, "a full build still settles him when the week ends"
print("test_build_wp_timeline: OK")
