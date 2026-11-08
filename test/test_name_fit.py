#!/usr/bin/env python3
"""A name is never cut off on a Rumblin' card, even when the layout changes after the card is drawn (score tiles, the possession football, fonts):
a live player with the ball icon gets a long name, then the Act column grows with no resize event; the names must refit by themselves.
Needs `python3 -m http.server 8123` in the repo root:  python3 test/test_name_fit.py"""
import sys, json, os, copy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_test as T
from playwright.sync_api import sync_playwright
W=1000
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=os.environ["CHROME_PATH"]); page=T.new_page(b,[],[])
    pl=T.load("players.json"); sc=copy.deepcopy(T.load("scores_week2.json"))
    for g in sc:
        if g["status"]=="in_progress": g["metadata"]["possession"]="MIN"
    routes={"**/rumbles_history.json":T.load("rumbles_history.json"),"**/v1/state/nfl":T.load("state.json"),"**/v1/league/TESTLEAGUE1":T.load("league.json"),"**/v1/league/TESTLEAGUE1/matchups/2":T.load("matchups_week2.json"),"**/v1/league/TESTLEAGUE1/matchups/1":T.load("matchups_week1.json"),"**/stats/nfl/2026/1*":T.load("stats_week1.json"),"**/projections/nfl/2026/1*":T.load("projections_week1.json"),"**/stats/nfl/2026/2*":T.load("stats_week2.json"),"**/projections/nfl/2026/2*":T.load("projections_week2.json"),"**/v1/players/nfl":pl,"**/scores/nfl/regular/2026/2":sc,"**/wp_timeline/index.json":{"weeks":[]}}
    T.install_routes(page,routes); page.set_viewport_size({"width":W,"height":900})
    page.goto(T.PAGE_URL+"?tab=rumblin&me=Alex",wait_until="load"); page.wait_for_timeout(2500)
    js="""()=>[...document.querySelectorAll('#rb-grid tr')].filter(r=>r.querySelector('.fld')&&r.querySelector('.rb-n')).map(r=>{const n=r.querySelector('.rb-n');return {t:n.textContent,cut:n.scrollWidth>n.clientWidth+1,fs:n.style.fontSize||'100%',cls:n.className,w:Math.round(n.clientWidth),sw:n.scrollWidth}})"""
    page.evaluate("""()=>{document.querySelectorAll('#rb-grid tr').forEach(r=>{if(!r.querySelector('.fld'))return;const n=r.querySelector('.rb-n');n.querySelectorAll('.nk').forEach(x=>x.remove());n.querySelector('.nf').textContent='Brock Purdy';n.querySelector('.nl').textContent='Purdy'})}""")
    page.add_style_tag(content="#rb-grid td.rb-act{padding-left:34px !important}")
    page.wait_for_timeout(900)
    res = page.evaluate(js)
    assert res and not any(r["cut"] for r in res), f"a name with the football beside it is cut off: {res}"
    print("PASS: names refit by themselves when the column narrows after the card is drawn (with the possession football present)")
    b.close()
