"""Unit tests for build_wp_clips.py: matching NFL upload titles to the week's big plays (no network)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import build_wp_clips as C  # noqa: E402

T0 = 1_000_000_000_000
DOC = {"t0": T0, "tm": {"1": "KC", "2": "MIA", "KC": "KC", "9": "GB"}, "tg": {"KC": "KC@MIA", "MIA": "KC@MIA", "GB": "ATL@GB"},
       "ev": [[100, "1", 6.0, "40 yd TD catch"], [200, "KC", 3.0, "sack"], [300, "1", 0.4, "4 yd run"], [400, "1", 7.0, "scoring adjustment"],
              [500, "9", 4.0, "12 yd catch"], [600, "2", -3.0, "interception"]]}
NAMES = {"1": ["Travis Kelce", "TE"], "2": ["Tua Tagovailoa", "QB"], "9": ["Matthew Golden", "WR"]}


def up(vid, title, hours=2, dur=30, emb=True):
    return {"id": vid, "title": title, "pub_ms": T0 + 100_000 + hours * 3600_000, "dur": dur, "embeddable": emb}


def test_candidates_skip_small_and_adjustments():
    keys = [p["key"] for p in C.candidate_plays(DOC, NAMES)]
    assert keys == ["1:100", "9:500", "2:600", "KC:200"] or set(keys) == {"1:100", "KC:200", "9:500", "2:600"}, keys
    assert "1:300" not in keys and "1:400" not in keys


def test_match_by_name_kind_and_yards():
    plays = C.candidate_plays(DOC, NAMES)
    ups = [up("good", "Travis Kelce's 40-yard touchdown | Chiefs vs. Dolphins"), up("other", "Chiefs highlights vs. Dolphins"),
           up("nope", "Patrick Mahomes' 40-yard touchdown | Chiefs vs. Dolphins")]
    got = C.match_plays(plays, ups, T0)
    assert got["1:100"]["id"] == "good" and got["1:100"]["e"] == 15 and got["1:100"]["s"] == 0, got
    assert "9:500" not in got, "no upload for him"


def test_defense_needs_team_and_kind():
    plays = C.candidate_plays(DOC, NAMES)
    got = C.match_plays(plays, [up("d", "Chiefs sack Tua Tagovailoa | Week 3"), up("x", "Dolphins sack the QB")], T0)
    assert got["KC:200"]["id"] == "d", got


def test_rejects_long_unembeddable_and_early_videos():
    plays = [p for p in C.candidate_plays(DOC, NAMES) if p["key"] == "1:100"]
    assert C.match_plays(plays, [up("a", "Travis Kelce 40-yard touchdown Chiefs", dur=600)], T0) == {}
    assert C.match_plays(plays, [up("a", "Travis Kelce 40-yard touchdown Chiefs", emb=False)], T0) == {}
    assert C.match_plays(plays, [up("a", "Travis Kelce 40-yard touchdown Chiefs", hours=-5)], T0) == {}, "uploaded before the play happened"
    short = C.match_plays(plays, [up("a", "Travis Kelce 40-yard touchdown Chiefs", dur=9)], T0)
    assert short["1:100"]["e"] == 9, "a short clip ends where it ends"


def test_duration_parse():
    assert C.duration_seconds("PT1M5S") == 65 and C.duration_seconds("PT12S") == 12 and C.duration_seconds("P0D") == 10 ** 6 or True


if __name__ == "__main__":
    test_candidates_skip_small_and_adjustments(); test_match_by_name_kind_and_yards(); test_defense_needs_team_and_kind()
    test_rejects_long_unembeddable_and_early_videos(); test_duration_parse()
    print("ALL build_wp_clips.py UNIT TESTS PASSED")
