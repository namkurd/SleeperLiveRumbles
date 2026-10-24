"""Unit test for build_epa.build / player_epa (no network)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import build_epa

HEAD = "player_id,position,season_type,week,attempts,sacks_suffered,carries,targets,passing_epa,rushing_epa,receiving_epa\n"
CSV = HEAD + "\n".join([
    "G-QB,QB,REG,1,30,2,3,0,6.0,0.5,",     # QB: epa 6.5 over 35 plays
    "G-WR,WR,REG,1,0,0,1,9,,0.25,3.0",     # WR: epa 3.25 over 10 plays
    "G-WR,WR,REG,2,0,0,0,5,,,-1.0",        # same player, next week
    "G-K,K,REG,1,0,0,0,0,,,",              # kickers have no EPA
    "G-NOPLAY,WR,REG,1,0,0,0,0,,,",        # no plays: skipped
    "G-UNKNOWN,RB,REG,1,0,0,5,0,,1.0,",    # not in the crosswalk: skipped
    "G-POST,WR,POST,1,0,0,0,5,,,2.0",      # playoffs: skipped
]) + "\n"
out = build_epa.build(2026, CSV, {"G-QB": "100", "G-WR": "200", "G-K": "300", "G-NOPLAY": "400", "G-POST": "500"})
assert out["season"] == 2026
assert out["weeks"]["1"] == {"100": [6.5, 35], "200": [3.25, 10]}, out["weeks"]["1"]
assert out["weeks"]["2"] == {"200": [-1.0, 5]}, out["weeks"]

# kickers and defenses come from play-by-play
PBP = "season_type,week,play_type,epa,defteam,kicker_player_id\n" + "\n".join([
    "REG,1,field_goal,1.5,SEA,G-K",        # kicker +1.5 over 1 attempt
    "REG,1,extra_point,0.25,SEA,G-K",
    "REG,1,pass,-0.5,SEA,",                # offense lost 0.5 -> the SEA defense gained 0.5
    "REG,1,run,0.25,LA,",                  # LA is Sleeper's LAR
    "REG,1,kickoff,3.0,SEA,",              # not a defensive play: skipped
    "REG,1,pass,,SEA,",                    # no EPA: skipped
    "POST,1,pass,9.0,SEA,",                # playoffs: skipped
]) + "\n"
kd = build_epa.kicker_defense(PBP, {"G-K": "300"})
assert kd["1"]["300"] == [1.75, 2], kd
assert kd["1"]["SEA"] == [0.5, 1], kd
assert kd["1"]["LAR"] == [-0.25, 1], kd
print("test_build_epa: OK")
