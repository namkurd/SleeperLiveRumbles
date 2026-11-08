#!/usr/bin/env python3
"""Rumblin' cards put an orange QUES in the Projected column for a starter whose game is on and whom Sleeper lists Questionable (gone once he scores again), and a red OUT for a starter ruled out in his game.
Reuses the e2e fixtures (needs `python3 -m http.server 8123` in the repo root, like run_test.py):  python3 test/test_injury_tags.py"""
import sys, json
sys.path.insert(0, "test")
import run_test as T
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    import os; b = p.chromium.launch(executable_path=os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")); ce, pe = [], []
    page = T.new_page(b, ce, pe)
    players = T.load("players.json")
    # make one live, un-struck starter Questionable
    routes = {
        "**/rumbles_history.json": T.load("rumbles_history.json"), "**/v1/state/nfl": T.load("state.json"), "**/v1/league/TESTLEAGUE1": T.load("league.json"),
        "**/v1/league/TESTLEAGUE1/matchups/2": T.load("matchups_week2.json"), "**/v1/league/TESTLEAGUE1/matchups/1": T.load("matchups_week1.json"),
        "**/stats/nfl/2026/1*": T.load("stats_week1.json"), "**/projections/nfl/2026/1*": T.load("projections_week1.json"),
        "**/stats/nfl/2026/2*": T.load("stats_week2.json"), "**/projections/nfl/2026/2*": T.load("projections_week2.json"),
        "**/v1/players/nfl": ({k: (dict(v, injury_status="Questionable") if k == "P11" else v) for k, v in players.items()}),
        "**/scores/nfl/regular/2026/2": T.load("scores_week2.json"),
    }
    T.install_routes(page, routes)
    page.goto(T.PAGE_URL + "?tab=rumblin", wait_until="load"); page.wait_for_timeout(2500)
    res = page.evaluate("""() => ({
      out: [...document.querySelectorAll('#rumblin-view tr.rb-out')].map(r => !!r.querySelector('.rb-tag-out')),
      q: [...document.querySelectorAll('#rumblin-view .rb-tag-q')].map(e => e.closest('tr').classList.contains('rb-live') && e.closest('td').classList.contains('rb-proj') && e.textContent === 'QUES') })""")
    assert res["q"] == [True], f"one live Questionable starter should carry QUES in the Projected column, got {res}"
    # he scores again: cleared, the tag goes (the first sighting's points are remembered on the device)
    import copy
    stats = copy.deepcopy(T.load("stats_week2.json"))
    for e in stats:
        if e["player_id"] == "P11": e["stats"]["pass_yd"] += 60
    page.unroute("**/stats/nfl/2026/2*"); page.route("**/stats/nfl/2026/2*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(stats)))
    page.reload(wait_until="load"); page.wait_for_timeout(2500)
    n = page.evaluate("document.querySelectorAll('#rumblin-view .rb-tag-q').length")
    assert n == 0, f"a Questionable starter who scored again is cleared, but {n} QUES tag(s) remain"
    b.close()
    # a second run with nobody Questionable and the QB ruled out: the Out row carries OUT
    b = p.chromium.launch(executable_path=os.environ.get("CHROME_PATH", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"))
    page = T.new_page(b, [], [])
    routes["**/v1/players/nfl"] = T.load("players.json")
    T.install_routes(page, routes)
    page.goto(T.PAGE_URL + "?tab=rumblin", wait_until="load"); page.wait_for_timeout(2500)
    res = page.evaluate("""() => ({ out: [...document.querySelectorAll('#rumblin-view tr.rb-out')].map(r => !!r.querySelector('.rb-tag-out')), q: document.querySelectorAll('#rumblin-view .rb-tag-q').length })""")
    assert res["out"] and all(res["out"]) and res["q"] == 0, f"every ruled-out row should carry OUT and nobody a Q, got {res}"
    print("PASS: QUES in the Projected column for a live Questionable starter, cleared once he scores; OUT for a starter ruled out in his game")
    b.close()
