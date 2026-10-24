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
print("test_build_epa: OK")
