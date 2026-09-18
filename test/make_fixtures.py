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
    # One synthetic HISTORICAL (already-finalized, week 1) QB-adjustment
    # entry, "confirmed" tier -- exercises the code path that reads
    # qb_adjustments straight out of rumbles_history.json (as opposed to
    # the live-detected week-2 entry built further down, which exercises
    # detectQbAdjustmentsForWeek in the browser). Together these two also
    # test that the table correctly MERGES historical + live rows and
    # sorts them by week (descending).
    "qb_adjustments": [
        {
            "week": 1,
            "roster_id": 2,
            "manager": "Ben",
            "injured_qb": {"player_id": "P_HIST_INJURED", "name": "Test Injured QB", "points": 10.0},
            "backup_qbs": [{"player_id": "P_HIST_BACKUP", "name": "Test Backup QB", "points": 12.34}],
            "backup_points_total": 12.34,
            "confidence": "confirmed",
            "injury_status_at_capture": None,
            "custom_points_delta": 12.34,
        }
    ],
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
# Also includes kr_yd and fgmiss -- both discovered (via a real live-data
# investigation) to have no literal key-name match in Sleeper's real stat/
# projection payloads (payloads use "def_kr_yd" and tiered "fgmiss_XX_YY"
# fields instead). See rumbles.html's KEY_ALIASES/TIER_SUM_KEYS and the
# roster-3 fixture stats below, which exercise both fixes.
scoring_settings = {
    "pass_yd": 0.04, "pass_td": 4, "pass_int": -2,
    "rush_yd": 0.1, "rush_td": 6,
    "rec": 0.0, "rec_yd": 0.1, "rec_td": 6,
    "rec_fd": 0.5, "rush_fd": 0.5, "pass_fd": 0.25,
    "fum_lost": -2,
    "kr_yd": 0.04,
    "fgmiss": -1.0,
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

# Roster 8 (Ankit): a commissioner override with NO identifiable backup QB
# at all -- roster 8's players have no metadata in players.json (they're
# not part of the QB scenario below), so the stats-based backup-detection
# heuristic will find nothing. This is exactly the class of bug being
# regression-tested: the override must STILL produce a "Confirmed" log
# entry (falling back to the override amount as "Backup QB Points" with no
# names), never silently disappear just because the heuristic came up
# empty.
for m in matchups:
    if m["roster_id"] == 8:
        m["custom_points"] = 15.0  # points is 0 above -> delta is +15.0

with open(os.path.join(OUT, "matchups_week2.json"), "w") as f:
    json.dump(matchups, f, indent=2)

# ---- bulk actual stats for week 2 : only the FIRST player on each roster has played ----
#
# rumbles.html has two scoring lenses: "Actual" (only ever uses real
# stats), and "Projected" (reproduces Sleeper's own live
# "projected" total -- a played player's REAL stat line replaces their
# frozen pregame projection; an unplayed player still uses the
# projection). Both dot-product against the league's real
# scoring_settings, never Sleeper's generic pts_ppr/pts_std fields. These
# fixtures are hand-crafted (round numbers, hand-verifiable totals) to
# make actual and projected deliberately far apart, so a test can tell at
# a glance which source got used:
#   - Roster 1's played player: small actual stat line, much bigger
#     pregame projection -- Projected must use the SMALL actual
#     number, not the bigger projection, once that player has played.
#     Roster 5: nobody has ANY actual stats yet (full pregame roster) --
#     Actual mode must show a flat 0 for it while Projected still
#     shows a real, nonzero projected total (the fallback path when
#     nobody's played).
#   - Roster 3's played player is having a blowout: actual stats far
#     exceed the pregame projection -- Projected must use the BIG
#     actual number here, not the smaller pregame projection.
# All other rosters use the original generic pattern, just to produce
# plausible, varied scores.
HAND_CRAFTED_ACTUAL = {
    1: {"rec": 2, "rec_yd": 20, "rec_fd": 1, "pts_ppr": 3.0},  # low actual so far
    # Roster 3's played player: blowout actual (30.0 from the base
    # rec/rec_yd/rec_td/rec_fd line below) PLUS two deliberately
    # misnamed-key categories that regression-test the real-data fixes:
    #   - "def_kr_yd": 100 -- Sleeper's real field name for kick-return
    #     yardage (scoring_settings' matching key is the bare "kr_yd") --
    #     worth 100 * 0.04 = +4.0 if the KEY_ALIASES fix is working,
    #     silently 0 if it regresses.
    #   - "fgmiss_30_39": 1 -- one of Sleeper's real tiered missed-FG
    #     fields (scoring_settings only has one flat "fgmiss": -1 weight
    #     covering every tier) -- worth 1 * -1.0 = -1.0 if the
    #     TIER_SUM_KEYS fix is working, silently 0 if it regresses.
    # Net: 30.0 (base) + 4.0 (kr_yd alias) - 1.0 (fgmiss tier-sum) = 33.0.
    3: {"rec": 10, "rec_yd": 150, "rec_td": 2, "rec_fd": 6, "pts_ppr": 35.0,
        "def_kr_yd": 100, "fgmiss_30_39": 1},
    # roster 5 deliberately has NO actual-stats entry at all for anyone --
    # see ZERO_ACTUAL_ROSTERS below.
}
HAND_CRAFTED_PROJ_PLAYED = {
    1: {"rec": 6, "rec_yd": 90, "rec_td": 1, "rec_fd": 4, "pts_ppr": 22.0},
    3: {"rec_yd": 60, "rec_td": 0, "rec_fd": 2, "pts_ppr": 15.0},
}
HAND_CRAFTED_PROJ_UNPLAYED = {
    # Roster 1's unplayed player also carries a poison-pill regression
    # check: "def_kr_yd"/"fgmiss_30_39" on a PROJECTION (never a real
    # actual-stats line) should NEVER trigger the KEY_ALIASES/
    # TIER_SUM_KEYS fallbacks -- those are gated to real actual stats only
    # (see rumbles.html's `isActual` flag; a real investigation found a
    # projection's "def_kr_yd" isn't a trustworthy single-week number the
    # way a real post-game "def_kr_yd" is). If that gating ever regresses,
    # this would silently add 500*0.04 - 1*1.0 = +19.0 phantom points to
    # Aidan's "custom" total (107.5 -> 126.5), which the existing PF/
    # Points-This-Week assertions below already catch without any extra
    # test code.
    1: {"rush_yd": 50, "rush_td": 1, "rush_fd": 3, "pts_ppr": 10.0,
        "def_kr_yd": 500, "fgmiss_30_39": 1},
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
    # Distinct raw stat lines per roster so Actual vs Custom actually
    # differ, and so different rosters produce different scores.
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


# ---- QB-injury-backup-adjustment scenario (roster 6 / "Alex", matching
# the real reported example) -----------------------------------------
#
# Roster 6's played starter (already assigned a generic stat line above)
# is overridden here to be a QB, "ruled out" (injury_status "Out") after a
# modest stat line -- with a same-team backup who came in and outscored
# him, a same-team 3rd-stringer who did NOT play (must be excluded), and
# a same-position QB on a DIFFERENT team who DID play (must also be
# excluded -- proves the detector is scoped by team, not just position).
QB_INJURED_PID = roster_players[6][0]
QB_BACKUP_PID = "P_QB6_BACKUP"
QB_THIRDSTRING_PID = "P_QB6_THIRDSTRING"  # same team, did NOT play -- must be excluded
QB_OTHER_TEAM_PID = "P_QB_OTHER_TEAM"  # different team, DID play -- must be excluded

stats[QB_INJURED_PID] = {
    "pass_att": 10, "pass_cmp": 7, "pass_yd": 80, "pass_td": 1, "pass_int": 0,
}  # 80*0.04 + 1*4 = 7.2 points -- a short outing, consistent with leaving hurt early
stats[QB_BACKUP_PID] = {
    "pass_att": 25, "pass_cmp": 18, "pass_yd": 210, "pass_td": 2, "pass_int": 1,
    "rush_yd": 15, "rush_td": 1,
}  # 210*0.04 + 2*4 - 1*2 + 15*0.1 + 1*6 = 21.9 points
stats[QB_OTHER_TEAM_PID] = {
    "pass_att": 20, "pass_cmp": 12, "pass_yd": 150, "pass_td": 1, "pass_int": 0,
}  # played, but a different NFL team -- must never be attributed to roster 6

with open(os.path.join(OUT, "stats_week2.json"), "w") as f:
    json.dump(to_sleeper_array(stats, "stat"), f, indent=2)

# ---- /v1/players/nfl : player metadata (position/team/injury_status) ----
# Deliberately small -- only the players that actually need metadata for
# some scenario below. Every OTHER player_id used elsewhere in these
# fixtures (P3 onward, P2, etc) is intentionally left OUT of this map: with
# no metadata, detectQbAdjustmentsForWeek's `meta.position !== "QB"` check
# skips them immediately (so they can never accidentally be swept into that
# feature), and the "Pts This Week" tooltip's game-status lookup falls back
# to "unknown" for them (see thisWeekTooltip -- "unknown" is deliberately
# treated as NOT complete, so a player is never wrongly hidden just because
# their team/game status couldn't be resolved).
#
# P1 (roster 1 / Aidan's played starter) gets real metadata here --
# non-QB, so it's still excluded from the QB-adjustment feature exactly as
# before -- specifically so a real NFL team ("DET") can be marked
# "complete" in scores_week2.json below, reproducing the real reported
# scenario (Amon-Ra St. Brown's already-finished game): Actual mode must
# still show a fully-finished player, but Projected mode must now exclude
# them entirely once their real game has gone final.
players = {
    "P1": {"position": "WR", "team": "DET", "full_name": "Amon-Ra St. Brown", "injury_status": None},
    QB_INJURED_PID: {"position": "QB", "team": "MIN", "full_name": "Kyler Murray", "injury_status": "Out"},
    QB_BACKUP_PID: {"position": "QB", "team": "MIN", "full_name": "Carson Wentz", "injury_status": None},
    QB_THIRDSTRING_PID: {"position": "QB", "team": "MIN", "full_name": "JJ McCarthy", "injury_status": None},
    QB_OTHER_TEAM_PID: {"position": "QB", "team": "KC", "full_name": "Some Other QB", "injury_status": None},
}
with open(os.path.join(OUT, "players.json"), "w") as f:
    json.dump(players, f, indent=2)

# ---- /v1/scores/nfl/{season_type}/{season}/{week} : per-game live status ----
# Sleeper's own live-scoreboard feed -- what the "Pts This Week" tooltip
# uses (see buildTeamGameStatus in rumbles.html) to tell a fully-final game
# apart from one that's still being played, which the stats/projections
# payloads alone can't distinguish (a player who's already played has a
# real stats entry in both cases).
#
# Shape confirmed live against the real endpoint (Sep 2026): team
# abbreviations and the authoritative "is this game over" booleans live
# under "metadata", not at the top level -- top-level only has a coarse
# "status" string (e.g. "pre_game"). buildTeamGameStatus checks
# metadata.is_over / metadata.is_in_progress first and falls back to the
# top-level fields, so these fixtures exercise the metadata path since
# that's what production traffic actually returns.
#   - DET (P1/Amon-Ra St. Brown, roster 1): is_over=true -- his game is
#     fully final. Projected mode's tooltip must exclude him entirely even
#     though Actual mode still shows him.
#   - MIN (Kyler Murray / Carson Wentz, roster 6): is_in_progress=true --
#     still being played. Projected mode's tooltip must still include
#     Kyler Murray (in-progress is NOT "complete").
#   - Every other team: no entry at all here, exercising the "unknown"
#     fallback (P2/roster 1's unplayed starter has no metadata at all, so
#     it's "unknown" regardless).
scores = [
    {
        "status": "post_game",
        "metadata": {"away_team": "DET", "home_team": "GB", "is_over": True, "is_in_progress": False},
    },
    {
        "status": "in_progress",
        "metadata": {"away_team": "MIN", "home_team": "CHI", "is_over": False, "is_in_progress": True},
    },
]
with open(os.path.join(OUT, "scores_week2.json"), "w") as f:
    json.dump(scores, f, indent=2)

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
