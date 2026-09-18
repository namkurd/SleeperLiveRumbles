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
    dot_product,
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


def test_compute_qb_adjustments_detected_tier_and_team_scoping():
    entries = compute_qb_adjustments_for_week(
        2, QB_MATCHUPS, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), QB_STATS_MAP,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=False, carried_by_roster={},
    )
    assert len(entries) == 1, f"expected exactly 1 adjustment (roster 2 has no started QB), got {len(entries)}"
    e = entries[0]
    assert e["roster_id"] == 1 and e["manager"] == "Alex"
    assert e["injured_qb"] == {"player_id": "QB_STARTER", "name": "Kyler Murray", "points": 7.2}
    # Only Carson Wentz -- the 3rd-stringer didn't play, and the other-team
    # QB (despite playing) is on the wrong team entirely.
    assert e["backup_qbs"] == [{"player_id": "QB_BACKUP", "name": "Carson Wentz", "points": 21.9}]
    assert approx(e["backup_points_total"], 21.9)
    assert e["confidence"] == "detected", f"no override + not fresh + no carry-forward should be 'detected', got {e['confidence']}"
    print("PASS: compute_qb_adjustments_for_week finds the right backup, excludes the 3rd-stringer and the other team's QB, 'detected' tier")


def test_compute_qb_adjustments_likely_tier_when_fresh_and_ruled_out():
    entries = compute_qb_adjustments_for_week(
        2, QB_MATCHUPS, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), QB_STATS_MAP,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=True, carried_by_roster={},
    )
    assert entries[0]["confidence"] == "likely", f"fresh + injury_status 'Out' should be 'likely', got {entries[0]['confidence']}"
    assert entries[0]["injury_status_at_capture"] == "Out"
    print("PASS: 'likely' tier fires when fresh and the started QB's live injury_status is 'Out'")


def test_compute_qb_adjustments_confirmed_tier_beats_everything_else():
    matchups_with_override = [dict(QB_MATCHUPS[0], custom_points=129.1), QB_MATCHUPS[1]]
    entries = compute_qb_adjustments_for_week(
        2, matchups_with_override, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), QB_STATS_MAP,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=False, carried_by_roster={},
    )
    assert entries[0]["confidence"] == "confirmed", f"a set custom_points should always mean 'confirmed', got {entries[0]['confidence']}"
    assert approx(entries[0]["custom_points_delta"], 29.1)  # 129.1 - 100.0
    print("PASS: 'confirmed' tier fires whenever custom_points is set, regardless of freshness")


def test_compute_qb_adjustments_carries_forward_stale_likely_tier():
    # Simulates a week that's no longer the freshest one (is_fresh=False)
    # but had already captured "likely" (injury_status "Out") back when it
    # WAS fresh, in a previous script run -- that captured tier must be
    # reused, not silently downgraded to "detected" just because today's
    # current injury_status snapshot (which has since moved on) can't
    # corroborate it anymore.
    carried = {
        1: {
            "injured_qb": {"player_id": "QB_STARTER", "name": "Kyler Murray", "points": 7.2},
            "confidence": "likely",
            "injury_status_at_capture": "Out",
        }
    }
    entries = compute_qb_adjustments_for_week(
        2, QB_MATCHUPS, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), QB_STATS_MAP,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=False, carried_by_roster=carried,
    )
    assert entries[0]["confidence"] == "likely", f"expected the carried-forward 'likely' tier to be reused, got {entries[0]['confidence']}"
    assert entries[0]["injury_status_at_capture"] == "Out"
    print("PASS: a previously-captured 'likely' tier is carried forward for an old (no-longer-fresh) week")


def test_compute_qb_adjustments_no_entry_without_a_backup():
    matchups_no_backup = [{"roster_id": 1, "matchup_id": 1, "starters": ["QB_STARTER"], "points": 100.0}]
    stats_no_backup = {"QB_STARTER": QB_STATS_MAP["QB_STARTER"]}  # nobody else on MIN played
    entries = compute_qb_adjustments_for_week(
        2, matchups_no_backup, QB_PLAYERS_META, build_team_qb_index(QB_PLAYERS_META), stats_no_backup,
        QB_SCORING_SETTINGS, QB_MANAGER_MAP, is_fresh=True, carried_by_roster={},
    )
    assert entries == [], f"no other team QB played -- there must be no adjustment entry at all, got {entries}"
    print("PASS: no adjustment entry when no same-team backup QB recorded any action")


def main():
    test_official_points_prefers_custom_points_when_set()
    test_score_week_applies_override_to_pf()
    test_score_week_applies_override_to_opponent_pa()
    test_score_week_h2h_result_unaffected_here()
    test_score_week_vs_field_outscored_uses_official_score()
    test_dot_product_matches_hand_computed_totals()
    test_is_played()
    test_build_team_qb_index_scopes_by_team()
    test_compute_qb_adjustments_detected_tier_and_team_scoping()
    test_compute_qb_adjustments_likely_tier_when_fresh_and_ruled_out()
    test_compute_qb_adjustments_confirmed_tier_beats_everything_else()
    test_compute_qb_adjustments_carries_forward_stale_likely_tier()
    test_compute_qb_adjustments_no_entry_without_a_backup()
    print("\nALL build_rumbles.py UNIT TESTS PASSED")


if __name__ == "__main__":
    main()
