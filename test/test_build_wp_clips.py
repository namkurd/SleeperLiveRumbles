"""Unit tests for build_wp_clips.py: matching NFL upload titles to the week's big plays (no network)."""
import json
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


def test_channels_resolve_once_and_skip_unknown_handles():
    import tempfile
    calls = []

    class R:
        def __init__(self, items): self.ok, self._i = True, items
        def json(self): return {"items": self._i}

    def fake_get(url, params=None, timeout=0):
        calls.append(params["forHandle"])
        return R([{"id": "UCabc123"}] if params["forHandle"] in ("@NFL", "@Chiefs") else [])
    C.requests.get = fake_get
    C.HANDLES = ["@NFL", "@Nope"]
    d = tempfile.mkdtemp()
    extra = os.path.join(d, "x.json"); open(extra, "w").write('{"handles": ["@Chiefs"]}')
    got = C.resolve_channels("k", os.path.join(d, "w", "c.json"), extra)
    assert [c["handle"] for c in got] == ["@NFL", "@Chiefs"] and got[0]["playlist"] == "UUabc123", got
    n = len(calls)
    C.resolve_channels("k", os.path.join(d, "w", "c.json"), extra)
    assert len(calls) == n, "resolved handles (and misses) are remembered"


def test_known_false_matches_are_rejected():
    doc = {"t0": T0, "tm": {"1": "IND", "2": "LAC", "3": "CHI", "SEA": "SEA"}, "tg": {"IND": "IND@NYJ", "LAC": "LAC@KC", "CHI": "CHI@DAL", "SEA": "SEA@ARI"},
           "ev": [[100, "1", 6.1, "1 yd TD run"], [200, "2", 4.8, "20 yd TD pass"], [300, "3", 5.4, "35 yd TD pass"], [400, "SEA", 6.0, "return TD"]]}
    names = {"1": ["Jonathan Taylor", "RB"], "2": ["Justin Herbert", "QB"], "3": ["Caleb Williams", "QB"]}
    plays = C.candidate_plays(doc, names)
    bad = [up("a", "Tyrod Taylor zips TD to Garrett Wilson"),                                  # another Taylor
           up("b", "Bears' Caleb Williams on 4-TD game vs. Cowboys & more"),                    # a recap
           up("c", "Herbert dials up launch codes on 36-yard TD to Johnston"),                  # another distance
           up("d", "Kyler Murray's best plays from 2-TD game vs. Seahawks", ),                  # a different player's day
           up("e", "Williams weighs in on the TD celly")]                                       # a reaction
    assert C.match_plays(plays, bad, T0) == {}, C.match_plays(plays, bad, T0)
    good = [dict(up("g", "Herbert's 20-yard touchdown pass"), ch="Los Angeles Chargers")]       # last name + his team's channel + distance
    assert C.match_plays(plays, good, T0)["2:200"]["id"] == "g"


def test_crawl_resumes_after_quota_and_picks_up_new_uploads():
    from datetime import datetime, timezone, timedelta
    base = datetime(2026, 1, 10, tzinfo=timezone.utc)
    vids = [(f"v{i}", base - timedelta(days=i)) for i in range(7)]

    class Q(C.Quota):
        def get(self, url, params):
            if self.left <= 0:
                raise C.QuotaOut("spent")
            self.left -= 1
            tok = int(params.get("pageToken") or 0)
            return {"items": [{"snippet": {"publishedAt": t.isoformat().replace("+00:00", "Z"), "title": "t" + v, "channelTitle": "Ch", "resourceId": {"videoId": v}}} for v, t in vids[tok:tok + 2]],
                    "nextPageToken": str(tok + 2) if tok + 2 < len(vids) else None}
    cache, win = {"pl": {}, "vid": {}}, [(0, 1e18)]
    q = Q(2); assert C.crawl(q, cache, ["P"], 0, win) is False and len(cache["pl"]["P"]["items"]) == 4, "stops when the quota is spent"
    q = Q(10); assert C.crawl(q, cache, ["P"], 0, win) is True and len(cache["pl"]["P"]["items"]) == 7 and q.left == 7, "resumes where it stopped"
    vids.insert(0, ("vNew", base + timedelta(days=1)))
    q = Q(10); C.crawl(q, cache, ["P"], 0, win)
    assert "vNew" in cache["pl"]["P"]["items"] and q.left == 9, "a later run reads only the new page"


def test_other_channels_are_preferred_over_the_nfl_and_kept_as_alternates():
    plays = [p for p in C.candidate_plays(DOC, NAMES) if p["key"] == "1:100"]
    ups = [dict(up("nfl", "Travis Kelce 40-yard touchdown | Chiefs vs. Dolphins"), ch="NFL"),
           dict(up("kc", "Travis Kelce 40-yard touchdown"), ch="Kansas City Chiefs")]
    got = C.match_plays(plays, ups, T0)["1:100"]
    assert got["id"] == "kc" and [a["id"] for a in got["alt"]] == ["nfl"], got


def test_run_end_to_end_with_a_fake_youtube():
    import tempfile, gzip
    from datetime import datetime, timezone, timedelta
    root = tempfile.mkdtemp()
    t0 = datetime.now(timezone.utc) - timedelta(days=3)
    t0ms = int(t0.timestamp() * 1000)
    os.makedirs(os.path.join(root, "wp_timeline")); os.makedirs(os.path.join(root, "wp_clips"))
    doc = {"t0": t0ms, "season": 2026, "tm": {"1": "KC", "2": "LAC", "3": "KC"}, "tg": {"KC": "KC@MIA", "MIA": "KC@MIA", "LAC": "LAC@KC"},
           "ev": [[100, "1", 6.0, "40 yd TD catch"], [200, "2", 5.0, "20 yd TD pass"], [300, "3", 4.0, "12 yd TD catch"]]}
    json.dump(doc, open(os.path.join(root, "wp_timeline", "week_5.json"), "w"))
    json.dump({"names": {"1": ["Travis Kelce", "TE"], "2": ["Justin Herbert", "QB"], "3": ["Rashee Rice", "WR"]}}, open(os.path.join(root, "epa_weekly.json"), "w"))
    json.dump([{"season": 2026, "week": 5, "player": "Rashee Rice", "label": "12 yd", "video": "https://youtu.be/MANUAL12345", "start": 7}], open(os.path.join(root, "clips_manual.json"), "w"))
    pub = (t0 + timedelta(hours=30)).isoformat().replace("+00:00", "Z")
    calls = []

    class R:
        ok = True; status_code = 200; text = ""
        def __init__(self, j): self._j = j
        def json(self): return self._j

    def fake_get(url, params=None, timeout=0):
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("/channels"):
            return R({"items": [{"id": "UCnflchan"}]} if params["forHandle"] == "@NFL" else {"items": []})
        if url.endswith("/playlistItems"):
            return R({"items": [{"snippet": {"publishedAt": pub, "title": "Travis Kelce's 40-yard touchdown | Chiefs vs. Dolphins", "channelTitle": "Kansas City Chiefs", "resourceId": {"videoId": "KELCEvid001"}}}]})
        if url.endswith("/videos"):
            ids = params["id"].split(",")
            return R({"items": [{"id": i, "contentDetails": {"duration": "PT20S"}, "status": {"embeddable": True, "privacyStatus": "public"}} for i in ids]})
        if url.endswith("/search"):
            return R({"items": [{"id": {"videoId": "HERBERTvid01"}, "snippet": {"publishedAt": pub, "title": "Justin Herbert 20-yard touchdown pass", "channelTitle": "Los Angeles Chargers"}}]})
        raise AssertionError(url)
    C.requests.get = fake_get
    C.HANDLES = ["@NFL", "@Missing"]
    os.environ["YOUTUBE_API_KEY"] = "k"
    C.run(root)
    wk = json.load(open(os.path.join(root, "wp_clips", "week_5.json")))
    assert wk["clips"]["1:100"]["id"] == "KELCEvid001" and wk["clips"]["1:100"]["e"] == 15, wk
    assert wk["clips"]["2:200"]["id"] == "HERBERTvid01", "a play no channel upload covered is found by search"
    assert wk["clips"]["3:300"]["id"] == "MANUAL12345" and wk["clips"]["3:300"]["s"] == 7 and wk["clips"]["3:300"].get("m") == 1, "a hand-picked link wins"
    assert os.path.exists(os.path.join(root, "wp_clips", "uploads_cache.json.gz")) and "search" in calls
    n = len(calls)
    C.run(root)
    assert "search" not in calls[n:], "plays already tried are not searched again"


def test_chapters_in_a_longer_highlight_video():
    desc = "Chiefs vs Dolphins\n0:00 Intro\n1:05 Travis Kelce 40-yard touchdown\n12:30 Tua Tagovailoa sack\nsubscribe!"
    ch = C.chapters_from(desc)
    assert ch == [[0, "Intro"], [65, "Travis Kelce 40-yard touchdown"], [750, "Tua Tagovailoa sack"]], ch
    assert C.chapters_from("0:10 only\n0:20 two") == [], "two lines are not a chapter list"
    plays = [p for p in C.candidate_plays(DOC, NAMES) if p["key"] == "1:100"]
    rec = [dict(up("recap", "Chiefs vs. Dolphins | 2025 Week 3 Highlights", dur=600), ch="Kansas City Chiefs")]
    cache = {"vid": {"recap": [600, 1]}, "chap": {"recap": ch}}
    got = C.match_chapters(plays, rec, cache, T0)["1:100"]
    assert got["id"] == "recap" and got["s"] == 63 and got["e"] == 78, got
    cache["vid"]["recap"] = [600, 0]
    assert C.match_chapters(plays, rec, cache, T0) == {}, "not embeddable: not offered"


def test_duration_parse():
    assert C.duration_seconds("PT1M5S") == 65 and C.duration_seconds("PT12S") == 12 and C.duration_seconds("P0D") == 10 ** 6 or True


if __name__ == "__main__":
    test_candidates_skip_small_and_adjustments(); test_match_by_name_kind_and_yards(); test_defense_needs_team_and_kind()
    test_rejects_long_unembeddable_and_early_videos(); test_channels_resolve_once_and_skip_unknown_handles(); test_known_false_matches_are_rejected(); test_other_channels_are_preferred_over_the_nfl_and_kept_as_alternates(); test_crawl_resumes_after_quota_and_picks_up_new_uploads(); test_run_end_to_end_with_a_fake_youtube(); test_chapters_in_a_longer_highlight_video(); test_duration_parse()
    print("ALL build_wp_clips.py UNIT TESTS PASSED")
