#!/usr/bin/env python3
"""Builds mock Sleeper API fixtures + a matching rumbles_history.json for
end-to-end testing rumbles.html without real network access."""
import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
os.makedirs(OUT, exist_ok=True)

MANAGERS = ["Aidan", "Ben", "Jake", "Rohaan", "Joe", "Alex", "Steven", "Ankit", "Christian", "Ryan", "Kaitlyn", "Stephanie"]
LEAGUE_ID = "TESTLEAGUE1"

# ---- rumbles_history.json (as if week 1 is complete, week 2 is live) ----
history = {
    "season": "2026",
    "league_id": LEAGUE_ID,
    "generated_at": "2026-09-11T00:00:00+00:00",
    "weeks_completed": [1],
    "max_rumbles_per_week": 20,
    "rumbles_per_h2h_win": 9,
    "standings": [],
    "weekly": {"1": {}},
}
# Week 1: roster i beat roster i+1 within each pair (1v2, 3v4, ...), and
# scores increase with roster_id so higher roster_id = more teams outscored.
for i, name in enumerate(MANAGERS, start=1):
    pf = 90.0 + i * 2.5
    pa = 95.0
    win = (i % 2 == 1)
    outscored = i - 1  # roster i outscores the i-1 rosters below it
    rumbles = outscored + (9 if win else 0)
    history["standings"].append({
        "roster_id": i, "manager": name, "rumbles": rumbles,
        "rumble_pct": round(rumbles / 20 * 100, 1),
        "last_completed_week_rumbles": rumbles,
        "last_completed_week_points": pf,  # only 1 week done so far, so this == pf
        "pf": pf, "pa": pa,
        "h2h_w": 1 if win else 0, "h2h_l": 0 if win else 1,
        "vs_field_w": outscored, "vs_field_l": 11 - outscored,
        "rank": 0,
    })
history["standings"].sort(key=lambda s: (-s["rumbles"], -s["pf"]))
for idx, s in enumerate(history["standings"], start=1):
    s["rank"] = idx

with open(os.path.join(OUT, "rumbles_history.json"), "w") as f:
    json.dump(history, f, indent=2)

# ---- /v1/state/nfl : week 2 is live ----
with open(os.path.join(OUT, "state.json"), "w") as f:
    json.dump({"season": "2026", "week": 2, "season_type": "regular"}, f)

# ---- /v1/state/nfl variant : Sleeper's pointer is LAGGING ----
# Reproduces the exact bug report: Week 1 is fully final in
# rumbles_history.json, but Sleeper's own state.week hasn't rolled over to
# 2 yet (this can lag behind the real calendar by a day or more). The page
# should still show Week 1's cumulative standings, not a blank table.
with open(os.path.join(OUT, "state_lagging.json"), "w") as f:
    json.dump({"season": "2026", "week": 1, "season_type": "regular"}, f)

# ---- /v1/league/{id}/matchups/2 variant : week 2 matchups not posted yet ----
with open(os.path.join(OUT, "matchups_week2_empty.json"), "w") as f:
    json.dump([], f)

# ---- /v1/league/{id} : scoring_settings with non-PPR + first-down bonus ----
scoring_settings = {
    "pass_yd": 0.04, "pass_td": 4, "pass_int": -2,
    "rush_yd": 0.1, "rush_td": 6,
    "rec": 0.0, "rec_yd": 0.1, "rec_td": 6,
    "rec_fd": 0.5, "rush_fd": 0.5, "pass_fd": 0.25,
    "fum_lost": -2,
}
with open(os.path.join(OUT, "league.json"), "w") as f:
    json.dump({"league_id": LEAGUE_ID, "season": "2026", "scoring_settings": scoring_settings}, f)

# ---- /v1/league/{id}/matchups/2 : 12 rosters, 6 matchups, 2 starters each ----
# roster_id 1 has player "P1" (already played) + "P2" (not yet played)
# roster_id 2 has "P3" (already played) + "P4" (not yet played)
# ... pattern repeats for other pairs using distinct player ids.
matchups = []
matchup_id = 1
pid_counter = 1
roster_players = {}
for i in range(1, 13, 2):
    a, b = i, i + 1
    pa_players = [f"P{pid_counter}", f"P{pid_counter+1}"]
    pid_counter += 2
    pb_players = [f"P{pid_counter}", f"P{pid_counter+1}"]
    pid_counter += 2
    roster_players[a] = pa_players
    roster_players[b] = pb_players
    matchups.append({"roster_id": a, "matchup_id": matchup_id, "starters": pa_players, "points": 0})
    matchups.append({"roster_id": b, "matchup_id": matchup_id, "starters": pb_players, "points": 0})
    matchup_id += 1

with open(os.path.join(OUT, "matchups_week2.json"), "w") as f:
    json.dump(matchups, f, indent=2)

# ---- bulk actual stats for week 2 : only the FIRST player on each roster has played ----
#
# rumbles.html now has three separate, non-blended scoring lenses ("Actual",
# "Generic PPR", "Our Custom Scoring") rather than one blended number, so
# these fixtures are hand-crafted (round numbers, hand-verifiable totals)
# to make actual and projected deliberately far apart -- proving the three
# modes are truly independent (Actual never touches projections; PPR/Custom
# never touch actual stats):
#   - Roster 1's played player: small actual stat line, much bigger
#     projection. Roster 5: nobody has ANY actual stats yet (full pregame
#     roster) -- Actual mode must show a flat 0 for it while PPR/Custom
#     still show a real, nonzero projected total.
#   - Roster 3's played player is having a blowout: actual stats far
#     exceed the pregame projection.
# All other rosters use the original generic pattern, just to produce
# plausible, varied scores.
HAND_CRAFTED_ACTUAL = {
    1: {"rec": 2, "rec_yd": 20, "rec_fd": 1, "pts_ppr": 3.0},  # low actual so far
    3: {"rec": 10, "rec_yd": 150, "rec_td": 2, "rec_fd": 6, "pts_ppr": 35.0},  # blowout actual
    # roster 5 deliberately has NO actual-stats entry at all for anyone --
    # see ZERO_ACTUAL_ROSTERS below.
}
HAND_CRAFTED_PROJ_PLAYED = {
    1: {"rec": 6, "rec_yd": 90, "rec_td": 1, "rec_fd": 4, "pts_ppr": 22.0},
    3: {"rec_yd": 60, "rec_td": 0, "rec_fd": 2, "pts_ppr": 15.0},
}
HAND_CRAFTED_PROJ_UNPLAYED = {
    1: {"rush_yd": 50, "rush_td": 1, "rush_fd": 3, "pts_ppr": 10.0},
    3: {"rush_yd": 30, "rush_fd": 2, "pts_ppr": 8.0},
}
ZERO_ACTUAL_ROSTERS = {5}  # entire roster is still pregame -- no stats rows for anyone on it

stats = {}
for rid, players in roster_players.items():
    played_pid = players[0]
    if rid in ZERO_ACTUAL_ROSTERS:
        continue  # no stats entry for this roster's players at all
    if rid in HAND_CRAFTED_ACTUAL:
        stats[played_pid] = HAND_CRAFTED_ACTUAL[rid]
        continue
    # Distinct raw stat lines per roster so PPR vs custom actually differ,
    # and so different rosters produce different scores.
    base = rid
    stats[played_pid] = {
        "pass_yd": 220 + base, "pass_td": 2, "pass_int": 0,
        "rush_yd": 10, "rush_td": 0,
        "rec": 4, "rec_yd": 55 + base, "rec_td": 1,
        "rec_fd": 3, "rush_fd": 1, "pass_fd": 8,
        "fum_lost": 0,
        "pts_ppr": 18.5 + base * 0.3,  # Sleeper's own generic-PPR total for this stat line
    }
    # second player (not yet played) has no stats entry at all -> {} would also
    # count as "not played" per hasPlayed check, so simply omit it.

# Sleeper's REAL bulk stats/projections endpoints do NOT return a simple
# {player_id: statsObj} map -- they return a JSON ARRAY of entries shaped
# like {player_id, stats: {...}, week, season, category, ...}, and 400
# without a ?season_type= query param. Confirmed against the live API
# (see rumbles.html's arrayToPlayerMap). Wrap the convenient intermediate
# dicts above into that real shape so this fixture actually matches what
# the browser will really receive -- a flat-map fixture here is exactly
# how the original bug shipped without a test catching it.
def to_sleeper_array(player_stats, category):
    return [
        {"player_id": pid, "stats": s, "week": 2, "season": "2026", "season_type": "regular", "category": category}
        for pid, s in player_stats.items()
    ]


with open(os.path.join(OUT, "stats_week2.json"), "w") as f:
    json.dump(to_sleeper_array(stats, "stat"), f, indent=2)

# ---- bulk projections for week 2 : every rostered player has a projection ----
projections = {}
for rid, players in roster_players.items():
    played_pid, unplayed_pid = players[0], players[1]
    if rid in HAND_CRAFTED_PROJ_PLAYED:
        projections[played_pid] = HAND_CRAFTED_PROJ_PLAYED[rid]
        projections[unplayed_pid] = HAND_CRAFTED_PROJ_UNPLAYED[rid]
        continue
    for j, pid in enumerate(players):
        base = rid + j
        projections[pid] = {
            "pass_yd": 200 + base, "pass_td": 1, "pass_int": 0,
            "rush_yd": 15, "rush_td": 0,
            "rec": 3, "rec_yd": 40 + base, "rec_td": 0,
            "rec_fd": 2, "rush_fd": 1, "pass_fd": 5,
            "fum_lost": 0,
            "pts_ppr": 12.0 + base * 0.25,
        }

with open(os.path.join(OUT, "projections_week2.json"), "w") as f:
    json.dump(to_sleeper_array(projections, "proj"), f, indent=2)

print("Fixtures written to", OUT)
print("Managers:", MANAGERS)
