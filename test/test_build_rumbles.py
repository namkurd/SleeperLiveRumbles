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

from build_rumbles import official_points, score_week  # noqa: E402

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


def main():
    test_official_points_prefers_custom_points_when_set()
    test_score_week_applies_override_to_pf()
    test_score_week_applies_override_to_opponent_pa()
    test_score_week_h2h_result_unaffected_here()
    test_score_week_vs_field_outscored_uses_official_score()
    print("\nALL build_rumbles.py UNIT TESTS PASSED")


if __name__ == "__main__":
    main()
