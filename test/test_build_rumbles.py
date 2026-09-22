#!/usr/bin/env python3
"""Unit tests for build_rumbles.py's scoring math -- no network access,
no Playwright/browser needed. Run directly: `python3 test/test_build_rumbles.py`

Covers the `custom_points` commissioner-override handling (official_points /
score_week): a roster's OFFICIAL score for a week is `custom_points` when
Sleeper has one set (a manual override), falling back to the plain
calculated `points` otherwise -- and that this correctly flows through to
PF, PA (the opponent's "points against"), H2H win/loss, and the
vs.-the-field outscored/outscored-by counts, not just the raw score field.

This regression-tests a real bug report: the league's commissioner
manually overrode roster 6's Week 1 score from 140.61 to 160.08 (+19.47,
a custom house-rule bonus) via Sleeper's `custom_points` field, which the
page was previously ignoring entirely (reading only `points`), silently
understating that roster's PF, their opponent's PA, AND -- less obviously
-- how many other teams they'd actually outscored that week.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from build_rumbles import (  # noqa: E402
    build_team_qb_index,
    compute_qb_adjustments_for_week,
    determine_fresh_week,
    dot_product,
    find_backup_qbs,
    is_played,
    official_points,
    score_week,
)

MANAGER_MAP = {1: "Alpha", 2: "Bravo", 3: "Charlie", 4: "Delta"}


def make_matchups():
    """4-team league, week with two H2H pairs (matchup_id 1 and 2).

    Pair 1: roster 1 (Alpha) vs roster 2 (Bravo) -- Alpha has a
    commissioner override (140.61 -> 160.08, +19.47), same real numbers
    as the actual bug report. Bravo has no override.

    Pair 2: roster 3 (Charlie) vs roster 4 (Delta) -- no overrides at
    all, a plain baseline case that must be completely unaffected.

    All-play (vs.-the-field) scores for this week, official:
      Alpha 160.08, Bravo 190.05, Charlie 145.00, Delta 100.00
    So using the OFFICIAL score, Alpha (160.08) outscores Charlie (145.00)
    and Delta (100.00) -- 2 teams -- but does NOT outscore Bravo (190.05).
    Using the STALE, override-ignoring `points` (140.61), Alpha would only
    outscore Delta (100.00) -- 1 team -- since 140.61 < 145.00 (Charlie).
    That's the exact kind of silent mis-scoring this test guards against:
    not just a wrong PF number, but a wrong "teams outscored" count.
    """
    return [
        {"roster_id": 1, "matchup_id": 1, "points": 140.61, "custom_points": 160.08},
        {"roster_id": 2, "matchup_id": 1, "points": 190.05, "custom_points": None},
        {"roster_id": 3, "matchup_id": 2, "points": 145.00, "custom_points": None},
        {"roster_id": 4, "matchup_id": 2, "points": 100.00, "custom_points": None},
    ]


def approx(a, b, tol=0.01):
    return abs(a - b) < tol


def test_official_points_prefers_custom_points_when_set():
    assert official_points({"points": 140.61, "custom_points": 160.08}) == 160.08
    assert official_points({"points": 140.61, "custom_points": None}) == 140.61
    assert official_points({"points": 140.61}) == 140.61  # field absent entirely
    assert official_points({"points": None, "custom_points": None}) == 0.0
    print("PASS: official_points prefers custom_points, falls back to points, then 0.0")


def test_score_week_applies_override_to_pf():
    result = score_week(make_matchups(), MANAGER_MAP)
    assert approx(result[1]["points"], 160.08), f"Alpha's PF should be the override 160.08, got {result[1]['points']}"
    assert approx(result[2]["points"], 190.05), f"Bravo's PF should be untouched 190.05, got {result[2]['points']}"
    assert approx(result[3]["points"], 145.00), f"Charlie's PF should be untouched 145.00, got {result[3]['points']}"
    assert approx(result[4]["points"], 100.00), f"Delta's PF should be untouched 100.00, got {result[4]['points']}"
    print("PASS: score_week applies the override to PF, leaves everyone else untouched")


def test_score_week_applies_override_to_opponent_pa():
    result = score_week(make_matchups(), MANAGER_MAP)
    # Bravo's "points against" (opponent_points) is Alpha's score -- must
    # reflect the OFFICIAL 160.08, not the stale 140.61.
    assert approx(result[2]["opponent_points"], 160.08), (
        f"Bravo's PA should reflect Alpha's official score 160.08, got {result[2]['opponent_points']}"
    )
    print("PASS: score_week applies the override to the opponent's PA too")


def test_score_week_h2h_result_unaffected_here():
    result = score_week(make_matchups(), MANAGER_MAP)
    # Alpha's official 160.08 is still less than Bravo's 190.05 either way
    # -- this override happens not to flip the H2H result. (It CAN flip a
    # result in general; this fixture just isn't built to exercise that,
    # since the real bug report's own numbers didn't flip theirs either.)
    assert result[1]["h2h_win"] is False, "Alpha should still lose the H2H (160.08 < 190.05)"
    assert result[2]["h2h_win"] is True, "Bravo should still win the H2H"
    print("PASS: H2H result matches expectation for this fixture's numbers")


def test_score_week_vs_field_outscored_uses_official_score():
    result = score_week(make_matchups(), MANAGER_MAP)
    # Using the OFFICIAL 160.08, Alpha should outscore Charlie (145.00)
    # and Delta (100.00) = 2 teams, and be outscored only by Bravo
    # (190.05) = 1 team. If the code silently used the stale 140.61
    # instead, Alpha would only outscore Delta (100.00) = 1 team, since
    # 140.61 < 145.00 -- this is the exact silent-miscount bug being
    # guarded against, not just a PF display issue.
    assert result[1]["teams_outscored"] == 2, (
        f"Alpha should outscore 2 teams (Charlie, Delta) using the official score, got {result[1]['teams_outscored']}"
    )
    assert result[1]["teams_outscored_by"] == 1, (
        f"Alpha should be outscored by 1 team (Bravo), got {result[1]['teams_outscored_by']}"
    )
    # Charlie and Delta, in turn, must now show as outscored BY Alpha --
    # this is the ripple effect onto OTHER rosters' own counts.
    assert result[3]["teams_outscored_by"] >= 1, "Charlie should be outscored by Alpha's official score"
    assert result[4]["teams_outscored_by"] >= 1, "Delta should be outscored by Alpha's official score"
    print("PASS: vs.-the-field outscored/outscored-by counts use the official (override-aware) score")


# ---------------------------------------------------------------------------
# QB injury-backup-points adjustment (compute_qb_adjustments_for_week etc)
#
# Same scenario as test/make_fixtures.py's roster-6/"Alex" fixture (real
# numbers mirror the real reported example: a started QB with a modest
# stat line, ruled "Out", and a same-team backup who came in and outscored
# him) -- exercises the tiered confidence system (confirmed / likely /
# detected) and the team-scoping (a same-team QB who didn't play, and a
# same-POSITION QB on a DIFFERENT team who did play, must both be excluded).
# ---------------------------------------------------------------------------

QB_SCORING_SETTINGS = {"pass_yd": 0.04, "pass_td": 4, "pass_int": -2, "rush_yd": 0.1, "rush_td": 6}

QB_PLAYERS_META = {
    "QB_STARTER": {"position": "QB", "team": "MIN", "full_name": "Kyler Murray", "injury_status": "Out"},
    "QB_BACKUP": {"position": "QB", "team": "MIN", "full_name": "Carson Wentz", "injury_status": None},
    "QB_THIRD": {"position": "QB", "team": "MIN", "full_name": "JJ McCarthy", "injury_status": None},
    "QB_OTHER_TEAM": {"position": "QB", "team": "KC", "full_name": "Other Team's QB", "injury_status": None},
}

QB_STATS_MAP = {
    "QB_STARTER": {"pass_att": 10, "pass_yd": 80, "pass_td": 1, "pass_int": 0},  # 80*.04 + 1*4 = 7.2
    "QB_BACKUP": {"pass_att": 25, "pass_yd": 210, "pass_td": 2, "pass_int": 1, "rush_yd": 15, "rush_td": 1},  # 8.4+8-2+1.5+6 = 21.9
    "QB_OTHER_TEAM": {"pass_att": 20, "pass_yd": 150, "pass_td": 1, "pass_int": 0},  # played, but a different team
    # "QB_THIRD" deliberately has NO stats entry -- did not play this week.
}

QB_MATCHUPS = [
    {"roster_id": 1, "matchup_id": 1, "starters": ["QB_STARTER", "WR1"], "points": 100.0},
    {"roster_id": 2, "matchup_id": 1, "starters": ["WR2"], "points": 90.0},
]

QB_MANAGER_MAP = {1: "Alex", 2: "Ben"}


def test_dot_product_matches_hand_computed_totals():
    assert approx(dot_product(QB_STATS_MAP["QB_STARTER"], QB_SCORING_SETTINGS), 7.2)
    assert approx(dot_product(QB_STATS_MAP["QB_BACKUP"], QB_SCORING_SETTINGS), 21.9)
    print("PASS: dot_product reproduces the hand-computed QB totals (7.2 / 21.9)")


def test_is_played():
    assert is_played({"pass_att": 1}) is True
    assert is_played({"gp": 1.0}) is True
    assert is_played({"rush_att": 3}) is True
    assert is_played({}) is False
    assert is_played(None) is False
    assert is_played({"pass_att": 0}) is False
    print("PASS: is_played correctly reads gp/pass_att/rush_att")


def test_build_team_qb_index_scopes_by_team():
    index = build_team_qb_index(QB_PLAYERS_META)
    assert set(index["MIN"]) == {"QB_STARTER", "QB_BACKUP", "QB_THIRD"}
    assert set(index["KC"]) == {"QB_OTHER_TEAM"}
    print("PASS: build_team_qb_index groups QBs by NFL team")


def test_compute_qb_adjustments_no_override_and_not_fresh_logs_nothing():
    # The "detected" tier has been removed -- this log only ever shows
    # commissioner-confirmed or freshly-corroborated entries, never a bare
    # "a backup QB played" observation on its own.
    entries = compute_qb_adjustments_for_week(
        2, QB_MATCHUPS, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), QB_STATS_MAP,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=False, is_most_recent_completed=True, carried_by_roster={},
    )
    assert entries == [], f"no override + not fresh + no carry-forward must log NOTHING (no more 'detected' tier), got {entries}"
    print("PASS: with no override and no fresh/carried corroboration, nothing is logged")


def test_compute_qb_adjustments_likely_tier_when_fresh_and_ruled_out():
    entries = compute_qb_adjustments_for_week(
        2, QB_MATCHUPS, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), QB_STATS_MAP,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=True, is_most_recent_completed=True, carried_by_roster={},
    )
    assert len(entries) == 1, f"expected exactly 1 adjustment (roster 2 has no started QB), got {len(entries)}"
    e = entries[0]
    assert e["roster_id"] == 1 and e["manager"] == "Alex"
    assert e["injured_qb"] == {"player_id": "QB_STARTER", "name": "Kyler Murray", "points": 7.2}
    # Only Carson Wentz -- the 3rd-stringer didn't play, and the other-team
    # QB (despite playing) is on the wrong team entirely.
    assert e["backup_qbs"] == [{"player_id": "QB_BACKUP", "name": "Carson Wentz", "points": 21.9}]
    assert approx(e["backup_points_total"], 21.9)
    assert e["confidence"] == "likely", f"fresh + injury_status 'Out' should be 'likely', got {e['confidence']}"
    assert e["injury_status_at_capture"] == "Out"
    print("PASS: 'likely' tier fires when fresh and the started QB's live injury_status is 'Out', with correct team-scoped backup detection")


def test_compute_qb_adjustments_possible_tier_when_fresh_but_not_corroborated():
    # Exactly Ben's own Caleb Williams/Tyler Bagent example: a same-team
    # backup QB recorded real action, but the started QB's live
    # injury_status never corroborated "Out"/"IR"/"PUP" (here: None, as if
    # it was never marked). This must still surface as an entry -- just the
    # weaker "possible" tier, not "likely", and with no custom_points_delta
    # since nothing but a commissioner override can ever set one.
    meta_not_corroborated = dict(QB_PLAYERS_META, QB_STARTER=dict(QB_PLAYERS_META["QB_STARTER"], injury_status=None))
    entries = compute_qb_adjustments_for_week(
        2, QB_MATCHUPS, meta_not_corroborated, build_team_qb_index(meta_not_corroborated), QB_STATS_MAP,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=True, is_most_recent_completed=True, carried_by_roster={},
    )
    assert len(entries) == 1, f"a backup QB recording action should still log an entry even without corroboration, got {len(entries)}"
    e = entries[0]
    assert e["confidence"] == "possible", f"fresh + uncorroborated injury_status should be 'possible', got {e['confidence']}"
    assert e["backup_qbs"] == [{"player_id": "QB_BACKUP", "name": "Carson Wentz", "points": 21.9}]
    assert approx(e["backup_points_total"], 21.9), "backup_points_total is still recorded for awareness, even though it's never auto-applied to any live/official total"
    assert e["custom_points_delta"] is None
    assert e["injury_status_at_capture"] is None
    print("PASS: 'possible' tier fires when fresh but the started QB's injury_status doesn't corroborate 'Out'/'IR'/'PUP'")


def test_compute_qb_adjustments_possible_tier_also_fires_for_questionable():
    # Same idea, but with an actual (non-null) status that still isn't
    # Out/IR/PUP -- "Questionable" is the single most common real-world
    # case this tier exists for (a starter who's banged up but still
    # active, comes out for a series, and a backup scores in relief).
    meta_questionable = dict(QB_PLAYERS_META, QB_STARTER=dict(QB_PLAYERS_META["QB_STARTER"], injury_status="Questionable"))
    entries = compute_qb_adjustments_for_week(
        2, QB_MATCHUPS, meta_questionable, build_team_qb_index(meta_questionable), QB_STATS_MAP,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=True, is_most_recent_completed=True, carried_by_roster={},
    )
    assert entries[0]["confidence"] == "possible", f"'Questionable' shouldn't corroborate an out/IR/PUP case, got {entries[0]['confidence']}"
    assert entries[0]["injury_status_at_capture"] == "Questionable"
    print("PASS: 'possible' tier also fires for a non-out status like 'Questionable', not just a missing one")


def test_compute_qb_adjustments_possible_tier_not_carried_forward_once_stale():
    # Once a week is no longer the freshest one, an unconfirmed "possible"
    # entry must NOT be carried forward the way "likely" is -- most of
    # these are just normal in-game substitutions that resolve themselves,
    # so they're meant to fade away rather than accumulate permanently in
    # history. Only "likely" (a real, corroborated injury) and "confirmed"
    # (the commissioner explicitly decided it was a real case, which is
    # handled unconditionally regardless of freshness) persist.
    carried_possible = {
        1: [
            {
                "injured_qb": {"player_id": "QB_STARTER", "name": "Kyler Murray", "points": 7.2},
                "confidence": "possible",
                "injury_status_at_capture": None,
            }
        ]
    }
    entries = compute_qb_adjustments_for_week(
        2, QB_MATCHUPS, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), QB_STATS_MAP,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=False, is_most_recent_completed=True, carried_by_roster=carried_possible,
    )
    assert entries == [], f"a stale, never-confirmed 'possible' entry must not be carried forward, got {entries}"
    print("PASS: an unconfirmed 'possible' entry is not carried forward once its week is no longer fresh")


def test_compute_qb_adjustments_confirmed_tier_beats_everything_else():
    matchups_with_override = [dict(QB_MATCHUPS[0], custom_points=129.1), QB_MATCHUPS[1]]
    entries = compute_qb_adjustments_for_week(
        2, matchups_with_override, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), QB_STATS_MAP,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=False, is_most_recent_completed=True, carried_by_roster={},
    )
    assert entries[0]["confidence"] == "confirmed", f"a set custom_points should always mean 'confirmed', got {entries[0]['confidence']}"
    assert approx(entries[0]["custom_points_delta"], 29.1)  # 129.1 - 100.0
    print("PASS: 'confirmed' tier fires whenever custom_points is set, regardless of freshness")


def test_compute_qb_adjustments_confirmed_always_logs_even_without_identifiable_backup():
    # This is exactly the bug being fixed: a commissioner override must
    # ALWAYS show up as a "Confirmed" log entry, even when the stats-based
    # backup-detection heuristic can't independently corroborate it (no
    # other team QB shows up as having played). The override itself is the
    # primary signal -- backup identification is best-effort detail on top,
    # never a gate on whether the event gets logged at all.
    matchups_override_no_backup = [
        {"roster_id": 1, "matchup_id": 1, "starters": ["QB_STARTER"], "points": 100.0, "custom_points": 119.47},
        {"roster_id": 2, "matchup_id": 1, "starters": ["WR2"], "points": 90.0},
    ]
    stats_no_backup = {"QB_STARTER": QB_STATS_MAP["QB_STARTER"]}  # nobody else on MIN played
    entries = compute_qb_adjustments_for_week(
        1, matchups_override_no_backup, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), stats_no_backup,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=False, is_most_recent_completed=True, carried_by_roster={},
    )
    assert len(entries) == 1, f"a commissioner override must ALWAYS produce a log entry, got {len(entries)}"
    e = entries[0]
    assert e["confidence"] == "confirmed"
    assert e["backup_qbs"] == [], "no backup could be identified -- the list should be empty, not fabricated"
    assert approx(e["backup_points_total"], 19.47), f"should fall back to the override delta (19.47) when no backup identified, got {e['backup_points_total']}"
    assert approx(e["injured_qb"]["points"], 7.2)
    print("PASS: a commissioner override ALWAYS logs a 'confirmed' entry, even when no backup QB can be independently identified")


def test_compute_qb_adjustments_carries_forward_stale_likely_tier():
    # Simulates a week that's no longer the freshest one (is_fresh=False)
    # but is STILL the single most-recently-completed week
    # (is_most_recent_completed=True -- i.e. the following week hasn't
    # itself completed yet, so we're still within the one-week grace
    # window) and had already captured "likely" (injury_status "Out") back
    # when it WAS fresh, in a previous script run -- that captured tier
    # must be reused during this whole grace window, not silently
    # downgraded/dropped just because today's current injury_status
    # snapshot (which has since moved on) can't corroborate it anymore.
    carried = {
        1: [
            {
                "injured_qb": {"player_id": "QB_STARTER", "name": "Kyler Murray", "points": 7.2},
                "confidence": "likely",
                "injury_status_at_capture": "Out",
            }
        ]
    }
    entries = compute_qb_adjustments_for_week(
        2, QB_MATCHUPS, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), QB_STATS_MAP,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=False, is_most_recent_completed=True, carried_by_roster=carried,
    )
    assert entries[0]["confidence"] == "likely", f"expected the carried-forward 'likely' tier to be reused, got {entries[0]['confidence']}"
    assert entries[0]["injury_status_at_capture"] == "Out"
    print("PASS: a previously-captured 'likely' tier is carried forward while its week is still the most-recently-completed one")


def test_compute_qb_adjustments_likely_tier_stops_being_carried_once_superseded():
    # The actual reported bug this session: "likely" used to be carried
    # forward FOREVER, all season, with no cutoff -- a manager saw an old,
    # never-confirmed "likely" case still showing up weeks later. Once an
    # even NEWER week has also completed (is_most_recent_completed=False
    # for this now-doubly-stale week), the carry-forward window has closed
    # for good: an unconfirmed "likely" from two or more completed weeks
    # back must stop being carried, exactly like "possible" already never
    # got carried past its own single fresh day (see
    # test_compute_qb_adjustments_possible_tier_not_carried_forward_once_stale).
    # Only "confirmed" (a real commissioner override) persists indefinitely.
    carried = {
        1: [
            {
                "injured_qb": {"player_id": "QB_STARTER", "name": "Kyler Murray", "points": 7.2},
                "confidence": "likely",
                "injury_status_at_capture": "Out",
            }
        ]
    }
    entries = compute_qb_adjustments_for_week(
        2, QB_MATCHUPS, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), QB_STATS_MAP,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=False, is_most_recent_completed=False, carried_by_roster=carried,
    )
    assert entries == [], f"a 'likely' entry from a week that's no longer even the most-recently-completed one must not be carried forward, got {entries}"
    print("PASS: a 'likely' entry stops being carried forward once a newer week has also completed, superseding it")


def test_compute_qb_adjustments_no_entry_without_a_backup():
    matchups_no_backup = [{"roster_id": 1, "matchup_id": 1, "starters": ["QB_STARTER"], "points": 100.0}]
    stats_no_backup = {"QB_STARTER": QB_STATS_MAP["QB_STARTER"]}  # nobody else on MIN played
    entries = compute_qb_adjustments_for_week(
        2, matchups_no_backup, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), stats_no_backup,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=True, is_most_recent_completed=True, carried_by_roster={},
    )
    assert entries == [], f"no other team QB played -- there must be no adjustment entry at all, got {entries}"
    print("PASS: no adjustment entry when no same-team backup QB recorded any action")


def test_find_backup_qbs_excludes_exactly_zero_point_backups():
    # QB_BACKUP records real action (pass_att >= 1, per is_played) but the
    # stat line dot-products to exactly 0.00 -- a kneel-down or a single
    # incompletion, no measurable statistical footprint. Not a meaningful
    # "backup credit" worth surfacing, unlike a genuinely bad (negative)
    # outing -- see the next test.
    meta = dict(QB_PLAYERS_META, QB_BACKUP={**QB_PLAYERS_META["QB_BACKUP"]})
    stats = dict(QB_STATS_MAP, QB_BACKUP={"pass_att": 2, "pass_yd": 0, "pass_td": 0, "pass_int": 0})
    team_qb_index = build_team_qb_index(meta)
    backups = find_backup_qbs("QB_STARTER", meta["QB_STARTER"], meta, team_qb_index, stats, QB_SCORING_SETTINGS)
    assert backups == [], f"expected a 0.00-point backup to be excluded entirely, got {backups}"
    print("PASS: a backup QB who played but scored exactly 0.00 points is excluded from the backup list")


def test_find_backup_qbs_keeps_negative_point_backups():
    # A genuinely bad outing (e.g. a pick-six) is still a real, meaningful
    # backup credit -- only an exact 0.00 gets filtered, never a negative.
    meta = dict(QB_PLAYERS_META, QB_BACKUP={**QB_PLAYERS_META["QB_BACKUP"]})
    stats = dict(QB_STATS_MAP, QB_BACKUP={"pass_att": 5, "pass_yd": 10, "pass_td": 0, "pass_int": 1})  # 10*.04 - 2 = -1.6
    team_qb_index = build_team_qb_index(meta)
    backups = find_backup_qbs("QB_STARTER", meta["QB_STARTER"], meta, team_qb_index, stats, QB_SCORING_SETTINGS)
    assert len(backups) == 1 and backups[0]["points"] == -1.6, f"expected the negative-point backup to still be listed, got {backups}"
    print("PASS: a backup QB with negative (but nonzero) points is still listed")


def test_compute_qb_adjustments_no_entry_when_only_backup_scored_exactly_zero():
    matchups_zero_backup = [{"roster_id": 1, "matchup_id": 1, "starters": ["QB_STARTER"], "points": 100.0}]
    stats_zero_backup = dict(QB_STATS_MAP, QB_BACKUP={"pass_att": 1, "pass_yd": 0, "pass_td": 0, "pass_int": 0})
    entries = compute_qb_adjustments_for_week(
        2, matchups_zero_backup, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), stats_zero_backup,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=True, is_most_recent_completed=True, carried_by_roster={},
    )
    assert entries == [], f"the only candidate backup scored exactly 0.00 -- there must be no adjustment entry at all, got {entries}"
    print("PASS: no adjustment entry when the only candidate backup QB scored exactly 0.00 points")


# ---------------------------------------------------------------------------
# SUPER_FLEX / multi-started-QB regression -- this league's real
# roster_positions has TWO SUPER_FLEX slots and no dedicated QB slot, so a
# manager can start two QBs at once. This is the exact real bug report: a
# manager started BOTH Carson Wentz (MIN) and Caleb Williams (CHI) the same
# week; Williams got hurt and Tyson Bagent (also CHI) came in to relieve
# him, but find_started_qb (singular, old code) only ever checked the FIRST
# QB found in `starters` (Wentz, since he was listed first), so Bagent's
# case was silently never even considered. find_started_qbs (plural) now
# checks every started QB independently, mirroring rumbles.html's JS fix.
# ---------------------------------------------------------------------------

SUPERFLEX_PLAYERS_META = {
    "WENTZ": {"position": "QB", "team": "MIN", "full_name": "Carson Wentz", "injury_status": None},
    "WILLIAMS": {"position": "QB", "team": "CHI", "full_name": "Caleb Williams", "injury_status": None},
    "BAGENT": {"position": "QB", "team": "CHI", "full_name": "Tyson Bagent", "injury_status": None},
    "MIN_OTHER": {"position": "QB", "team": "MIN", "full_name": "MIN 3rd String", "injury_status": None},
}
SUPERFLEX_STATS = {
    "WENTZ": {"pass_att": 30, "pass_yd": 260, "pass_td": 2, "pass_int": 1},  # 16.4 -- played the whole game, no backup
    "WILLIAMS": {"pass_att": 15, "pass_yd": 100, "pass_td": 0, "pass_int": 0},  # 4.0 -- left early
    "BAGENT": {"pass_att": 9, "pass_yd": 54, "pass_td": 0, "pass_int": 0},  # 2.16
}
# Wentz listed FIRST in starters -- exactly what made the old singular
# find_started_qb miss Williams entirely.
SUPERFLEX_MATCHUPS = [{"roster_id": 1, "matchup_id": 1, "starters": ["WENTZ", "WILLIAMS"], "points": 20.4}]
SUPERFLEX_MANAGER_MAP = {1: "Alex"}


def test_superflex_only_the_qb_with_a_real_backup_is_logged():
    team_qb_index = build_team_qb_index(SUPERFLEX_PLAYERS_META)
    entries = compute_qb_adjustments_for_week(
        2, SUPERFLEX_MATCHUPS, SUPERFLEX_PLAYERS_META, team_qb_index, SUPERFLEX_STATS,
        QB_SCORING_SETTINGS, SUPERFLEX_MANAGER_MAP, is_fresh=True, is_most_recent_completed=True, carried_by_roster={},
    )
    assert len(entries) == 1, f"Wentz (no backup) should log nothing, only Williams/Bagent should, got {len(entries)}"
    e = entries[0]
    assert e["injured_qb"]["name"] == "Caleb Williams", f"expected the logged entry to be about Caleb Williams, got {e['injured_qb']['name']}"
    assert [b["name"] for b in e["backup_qbs"]] == ["Tyson Bagent"], f"expected Tyson Bagent as the identified backup, got {e['backup_qbs']}"
    assert e["confidence"] == "possible", f"expected 'possible' (uncorroborated injury_status), got {e['confidence']}"
    print("PASS: a SUPER_FLEX roster's second started QB (not the first one checked) is correctly detected")


def test_superflex_both_started_qbs_can_log_separately():
    meta = dict(SUPERFLEX_PLAYERS_META)
    stats = dict(SUPERFLEX_STATS, MIN_OTHER={"pass_att": 5, "pass_yd": 40, "pass_td": 0, "pass_int": 0})  # 1.6 -- now "played" too
    team_qb_index = build_team_qb_index(meta)
    entries = compute_qb_adjustments_for_week(
        2, SUPERFLEX_MATCHUPS, meta, team_qb_index, stats,
        QB_SCORING_SETTINGS, SUPERFLEX_MANAGER_MAP, is_fresh=True, is_most_recent_completed=True, carried_by_roster={},
    )
    assert len(entries) == 2, f"expected two separate entries (one per started QB), got {len(entries)}"
    by_name = {e["injured_qb"]["name"]: e for e in entries}
    assert set(by_name) == {"Carson Wentz", "Caleb Williams"}
    assert [b["name"] for b in by_name["Carson Wentz"]["backup_qbs"]] == ["MIN 3rd String"], "Wentz's backup should be the MIN 3rd-stringer, not Williams or Bagent"
    assert [b["name"] for b in by_name["Caleb Williams"]["backup_qbs"]] == ["Tyson Bagent"], "Williams's backup should still just be Bagent"
    print("PASS: both started QBs on a SUPER_FLEX roster can independently log their own entry in the same week")


def test_superflex_two_deliberately_started_same_team_qbs_are_not_backups_of_each_other():
    meta = {
        "A": {"position": "QB", "team": "CHI", "full_name": "Started QB A", "injury_status": None},
        "B": {"position": "QB", "team": "CHI", "full_name": "Started QB B", "injury_status": None},
    }
    stats = {
        "A": {"pass_att": 20, "pass_yd": 150, "pass_td": 1, "pass_int": 0},
        "B": {"pass_att": 18, "pass_yd": 140, "pass_td": 1, "pass_int": 0},
    }
    matchups = [{"roster_id": 1, "matchup_id": 1, "starters": ["A", "B"], "points": 20.0}]
    team_qb_index = build_team_qb_index(meta)
    entries = compute_qb_adjustments_for_week(
        2, matchups, meta, team_qb_index, stats,
        QB_SCORING_SETTINGS, {1: "Alex"}, is_fresh=True, is_most_recent_completed=True, carried_by_roster={},
    )
    assert entries == [], f"neither deliberately-started same-team QB should be logged as the other's 'backup', got {entries}"
    print("PASS: two deliberately-started same-team QBs are never mistaken for one another's backup")


# ---- determine_fresh_week: the "was this week's completion just noticed
# THIS run" guard that build_history uses to decide whether a completed
# week's injury_status is worth freshly re-checking at all. Regression-
# tests a real reported case: once a new week starts being played (state's
# current week has already moved on), a previous week's unconfirmed
# "possible" QB-adjustment entries kept reappearing in the persisted
# history for the ENTIRE span the new week was live-but-not-yet-completed,
# because completed_weeks' max (the just-finished week) never itself
# changes again until that NEW week also finishes -- these tests exercise
# the "previously_completed_weeks" carry-forward that fixes it.
def test_determine_fresh_week_first_time_a_week_completes_is_fresh():
    # Week 1 has never appeared in a previous run's weeks_completed -- this
    # is the first run to see it as completed, so it's fresh.
    assert determine_fresh_week([1], previously_completed_weeks=set()) == 1
    print("PASS: a week's very first run as 'completed' is fresh")


def test_determine_fresh_week_already_seen_before_is_not_fresh():
    # Week 1 was ALREADY in weeks_completed as of the last run -- a later
    # run (week 2 now live but not yet itself completed, so completed_weeks
    # is still just [1]) must NOT keep treating week 1 as fresh, even
    # though it's still the max of completed_weeks.
    assert determine_fresh_week([1], previously_completed_weeks={1}) is None
    print("PASS: a week already seen as completed in a prior run is no longer fresh, even while it's still completed_weeks' max")


def test_determine_fresh_week_advances_once_a_newer_week_completes():
    # Week 2 itself has now finished too (completed_weeks == [1, 2]) and
    # this is the first run to see IT as completed -- week 2 becomes the
    # fresh one; week 1 (already-seen) stays not-fresh.
    assert determine_fresh_week([1, 2], previously_completed_weeks={1}) == 2
    print("PASS: freshness moves to the newly-completed week once it finishes, not stuck on the old one")


def test_determine_fresh_week_no_completed_weeks_yet():
    assert determine_fresh_week([], previously_completed_weeks=set()) is None
    print("PASS: no completed weeks at all -> no fresh week")


def test_determine_fresh_week_missing_previous_data_defaults_to_fresh():
    # A missing/corrupt previous history file (load_previous_weeks_completed
    # returns an empty set/None) must never crash and should behave like
    # this feature's very first run -- every currently-completed week looks
    # "new".
    assert determine_fresh_week([3], previously_completed_weeks=None) == 3
    print("PASS: missing previous weeks_completed data defaults to treating the week as fresh, not crashing")


def test_possible_tier_dropped_once_a_new_week_has_started_end_to_end():
    """The full reported scenario, exercised through compute_qb_adjustments_for_week
    directly the way build_history actually drives it across two simulated
    runs: run 1 (week 1 just completed) logs an unconfirmed 'possible' case
    fresh; run 2 (week 2 now live/current but not yet itself completed --
    completed_weeks is still just [1]) must NOT re-log it, and since it was
    never 'likely'/confirmed, carrying forward finds nothing -- the entry
    is gone."""
    meta = {
        "QB1": {"position": "QB", "team": "SF", "full_name": "Started QB", "injury_status": None},
        "QB1B": {"position": "QB", "team": "SF", "full_name": "Backup QB", "injury_status": None},
    }
    stats = {
        "QB1": {"pass_att": 20, "pass_yd": 150, "pass_td": 1, "pass_int": 0},
        "QB1B": {"pass_att": 5, "pass_yd": 30, "pass_td": 0, "pass_int": 0},
    }
    matchups = [{"roster_id": 1, "matchup_id": 1, "starters": ["QB1"], "points": 12.0}]
    team_qb_index = build_team_qb_index(meta)

    # Run 1: week 1 just completed for the first time -- fresh.
    is_fresh_run1 = determine_fresh_week([1], previously_completed_weeks=set()) == 1
    run1_entries = compute_qb_adjustments_for_week(
        1, matchups, meta, team_qb_index, stats, QB_SCORING_SETTINGS, {1: "Alex"},
        is_fresh=is_fresh_run1, is_most_recent_completed=True, carried_by_roster={},
    )
    assert len(run1_entries) == 1 and run1_entries[0]["confidence"] == "possible", (
        f"expected run 1 to freshly log an unconfirmed 'possible' entry, got {run1_entries}"
    )

    # Run 2: week 2 has started (it's now the current week per Sleeper's
    # state) but hasn't itself finished, so completed_weeks is STILL just
    # [1] -- the real-world bug case. previously_completed_weeks now
    # contains {1} (from run 1's own output).
    is_fresh_run2 = determine_fresh_week([1], previously_completed_weeks={1}) == 1
    carried = {1: run1_entries}  # what load_previous_qb_adjustments would hand back from run 1's file
    run2_entries = compute_qb_adjustments_for_week(
        1, matchups, meta, team_qb_index, stats, QB_SCORING_SETTINGS, {1: "Alex"},
        is_fresh=is_fresh_run2, is_most_recent_completed=True, carried_by_roster=carried,
    )
    assert run2_entries == [], (
        f"expected the unconfirmed 'possible' entry to be dropped once a new week has started, got {run2_entries}"
    )
    print("PASS: an unconfirmed 'possible' entry from a prior week is dropped as soon as a new week starts, not carried for the newer week's entire live span")


def main():
    test_official_points_prefers_custom_points_when_set()
    test_score_week_applies_override_to_pf()
    test_score_week_applies_override_to_opponent_pa()
    test_score_week_h2h_result_unaffected_here()
    test_score_week_vs_field_outscored_uses_official_score()
    test_dot_product_matches_hand_computed_totals()
    test_is_played()
    test_build_team_qb_index_scopes_by_team()
    test_compute_qb_adjustments_no_override_and_not_fresh_logs_nothing()
    test_compute_qb_adjustments_likely_tier_when_fresh_and_ruled_out()
    test_compute_qb_adjustments_possible_tier_when_fresh_but_not_corroborated()
    test_compute_qb_adjustments_possible_tier_also_fires_for_questionable()
    test_compute_qb_adjustments_possible_tier_not_carried_forward_once_stale()
    test_compute_qb_adjustments_confirmed_tier_beats_everything_else()
    test_compute_qb_adjustments_confirmed_always_logs_even_without_identifiable_backup()
    test_compute_qb_adjustments_carries_forward_stale_likely_tier()
    test_compute_qb_adjustments_likely_tier_stops_being_carried_once_superseded()
    test_compute_qb_adjustments_no_entry_without_a_backup()
    test_find_backup_qbs_excludes_exactly_zero_point_backups()
    test_find_backup_qbs_keeps_negative_point_backups()
    test_compute_qb_adjustments_no_entry_when_only_backup_scored_exactly_zero()
    test_superflex_only_the_qb_with_a_real_backup_is_logged()
    test_superflex_both_started_qbs_can_log_separately()
    test_superflex_two_deliberately_started_same_team_qbs_are_not_backups_of_each_other()
    test_determine_fresh_week_first_time_a_week_completes_is_fresh()
    test_determine_fresh_week_already_seen_before_is_not_fresh()
    test_determine_fresh_week_advances_once_a_newer_week_completes()
    test_determine_fresh_week_no_completed_weeks_yet()
    test_determine_fresh_week_missing_previous_data_defaults_to_fresh()
    test_possible_tier_dropped_once_a_new_week_has_started_end_to_end()
    print("\nALL build_rumbles.py UNIT TESTS PASSED")


if __name__ == "__main__":
    main()
