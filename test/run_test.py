#!/usr/bin/env python3
"""End-to-end test of rumbles.html against mocked Sleeper API responses.

This sandbox has no direct network access to api.sleeper.app, so we run a
headless browser against the real page, intercept every request that would
go to Sleeper (or to rumbles_history.json), and fulfill it with fixture
JSON built by make_fixtures.py.

Three scenarios:
  1. live_blending      -- a week is genuinely in progress; verifies the
                            actual-vs-live-projection blending and that
                            live figures land on top of cumulative history.
  2. cumulative_only     -- reproduces the reported bug: Week 1 is fully
                            final in rumbles_history.json, but Sleeper's
                            own state.week pointer hasn't rolled over yet
                            and Week 2's matchups aren't posted. The page
                            must still show Week 1's cumulative standings,
                            not a blank table.
  3. history_load_failure -- rumbles_history.json 404s. The page must show
                            a clear error instead of a silent blank table.
"""
import json
import os

from playwright.sync_api import sync_playwright

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
PAGE_URL = "http://127.0.0.1:8123/rumbles.html"


def load(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


def install_routes(page, routes):
    for pattern, payload in routes.items():
        def handler(route, request=None, payload=payload):
            route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
        page.route(pattern, handler)
    # Block anything else that isn't our local server or Sleeper itself
    # (chromium sometimes tries background telemetry/update hosts); routes
    # NOT explicitly registered above but matching 127.0.0.1 (like a
    # deliberately-omitted rumbles_history.json) fall through to the real
    # local static server and 404 naturally.
    def block_other(route):
        route.abort()
    page.route(lambda url: "127.0.0.1" not in url and "sleeper.app" not in url, block_other)


def get_table_rows(page):
    return page.eval_on_selector_all(
        "#standings-body tr",
        "rows => rows.map(r => Array.from(r.querySelectorAll('td')).map(td => td.innerText.trim()))",
    )


def new_page(browser, console_errors, page_errors):
    page = browser.new_page()
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    return page


def scenario_live_blending(browser):
    print("\n" + "=" * 70)
    print("SCENARIO 1: a week is genuinely live -- Actual / Sleeper Projection / Our Custom Scoring")
    print("=" * 70)
    console_errors, page_errors = [], []
    page = new_page(browser, console_errors, page_errors)
    routes = {
        "**/rumbles_history.json": load("rumbles_history.json"),
        "**/v1/state/nfl": load("state.json"),  # week 2, regular
        "**/v1/league/TESTLEAGUE1": load("league.json"),
        "**/v1/league/TESTLEAGUE1/matchups/2": load("matchups_week2.json"),
        # Real Sleeper requires ?season_type=... on these two or it 400s --
        # match with a trailing "*" rather than a literal "?" (Playwright
        # glob patterns treat "?" as "any one character", not literally).
        "**/stats/nfl/2026/2*": load("stats_week2.json"),
        "**/projections/nfl/2026/2*": load("projections_week2.json"),
    }
    install_routes(page, routes)
    page.goto(PAGE_URL, wait_until="load")
    page.wait_for_timeout(1000)

    status_text = page.text_content("#status-text")
    print("Status text:", status_text)
    assert "Live" in status_text and "Week 2" in status_text, f"expected live week 2 status, got: {status_text}"

    # Hand-verified expected totals (see make_fixtures.py's HAND_CRAFTED_*
    # and ZERO_ACTUAL_ROSTERS). "Actual" only ever uses real stats. "Sleeper
    # Projection" and "Our Custom Scoring" now reproduce how Sleeper itself
    # computes its live "projected" total: a player who has an actual-stats
    # entry (their game has started) contributes their REAL performance,
    # not their frozen pregame projection -- only still-pregame players use
    # the projection. This matches what Sleeper's own matchup page shows.
    #   Aidan (roster 1, history PF 92.5): played player has a small actual
    #     stat line (2.5 custom / 3.0 ppr) and a much bigger pregame
    #     projection (17.0 custom / 22.0 ppr) that's now ignored in favor
    #     of the real performance; unplayed player only has a projection
    #     (12.5 custom / 10.0 ppr).
    #     Actual=2.5 -> PF 95.0 | Sleeper Proj=3.0+10.0=13.0 -> PF 105.5 | Custom=2.5+12.5=15.0 -> PF 107.5
    #   Jake (roster 3, history PF 97.5): played player is having a
    #     blowout (30.0 custom / 35.0 ppr actual) that now replaces the much
    #     smaller pregame projection (7.0 custom / 15.0 ppr); unplayed
    #     player only has a projection (4.0 custom / 8.0 ppr).
    #     Actual=30.0 -> PF 127.5 | Sleeper Proj=35.0+8.0=43.0 -> PF 140.5 | Custom=30.0+4.0=34.0 -> PF 131.5
    #   Joe (roster 5, history PF 102.5): entire roster is still pregame,
    #     zero actual stats recorded for anyone -- nothing to swap in, so
    #     this is unchanged from a pure-projection total (proves the
    #     fallback path still works when nobody's played yet).
    #     Actual=0.0 -> PF 102.5 (flat, no addition) | Sleeper Proj=26.75 -> PF 129.25
    # The frozen/actualized baseline is exactly what's already in
    # rumbles_history.json (only Week 1 is final in this fixture) -- pull
    # it straight from there rather than re-deriving the formula, so this
    # stays correct if the fixture's week-1 pattern ever changes.
    hist_standings = load("rumbles_history.json")["standings"]
    BASELINE_RUMBLES = {s["manager"]: s["rumbles"] for s in hist_standings}
    BASELINE_H2H = {s["manager"]: (s["h2h_w"], s["h2h_l"]) for s in hist_standings}

    expected = {
        "actual": {"Aidan": 95.0, "Jake": 127.5, "Joe": 102.5},
        "ppr": {"Aidan": 105.5, "Jake": 140.5, "Joe": 129.25},
        "custom": {"Aidan": 107.5, "Jake": 131.5, "Joe": None},  # Joe's custom PF depends on generic-pattern math; checked separately below
    }
    # "Points This Week" is the raw score for just this week (not the
    # cumulative PF) -- i.e. exactly liveInfo.points for the selected mode.
    expected_points_this_week = {
        "actual": {"Aidan": 2.5, "Jake": 30.0, "Joe": 0.0},
        "ppr": {"Aidan": 13.0, "Jake": 43.0, "Joe": 26.75},
        "custom": {"Aidan": 15.0, "Jake": 34.0, "Joe": None},
    }

    mode_buttons = {"actual": None, "ppr": "#mode-ppr", "custom": "#mode-custom"}
    rows_by_mode = {}
    for mode, selector in mode_buttons.items():
        if selector:
            page.click(selector)
            page.wait_for_timeout(200)
        rows = get_table_rows(page)
        rows_by_mode[mode] = rows
        print(f"\n== {mode} mode ==")
        for r in rows:
            print(r)
        assert len(rows) == 12, f"[{mode}] expected 12 rows, got {len(rows)}"

        th_rumbles = page.text_content("#th-rumbles")
        th_h2h = page.text_content("#th-h2h")
        expected_th = (
            ("Actualized Rumbles", "Actualized H2H W-L") if mode == "actual"
            else ("Projected Rumbles", "Projected H2H W-L")
        )
        assert (th_rumbles, th_h2h) == expected_th, (
            f"[{mode}] expected headers {expected_th}, got {(th_rumbles, th_h2h)}"
        )

        # Column order: 0=#, 1=Manager, 2=Rumbles, 3=This Week, 4=Points
        # This Week, 5=Rumble %, 6=PF, 7=PA, 8=H2H W-L, 9=Vs. Field W-L.
        pf_by_manager = {r[1]: float(r[6]) for r in rows}
        for manager, expected_pf in expected[mode].items():
            if expected_pf is None:
                continue
            got = pf_by_manager[manager]
            # 0.1 tolerance, not 0.05 -- the table only displays one decimal
            # place (toFixed(1)), so a true value ending in .x5 can render
            # either way depending on float rounding (e.g. 129.25 -> "129.3").
            assert abs(got - expected_pf) < 0.1, (
                f"[{mode}] {manager}: expected PF {expected_pf}, got {got}"
            )
        print(f"Hand-verified PF checks passed for {mode} mode:", {k: v for k, v in expected[mode].items() if v is not None})

        # Cell text is e.g. "35.0LIVE" when the LIVE badge is present --
        # strip the badge suffix before parsing.
        pts_by_manager = {r[1]: float(r[4].replace("LIVE", "")) for r in rows}
        for manager, expected_pts in expected_points_this_week[mode].items():
            if expected_pts is None:
                continue
            got = pts_by_manager[manager]
            assert abs(got - expected_pts) < 0.1, (
                f"[{mode}] {manager}: expected Points This Week {expected_pts}, got {got}"
            )
        print(f"Hand-verified Points This Week checks passed for {mode} mode:", {k: v for k, v in expected_points_this_week[mode].items() if v is not None})

        # Actual mode's Rumbles/H2H must be frozen to what's already final
        # in rumbles_history.json -- the in-progress week's actual-score
        # rumbles/H2H must NOT be folded in (only "This Week" shows it).
        # PPR/Custom DO fold the in-progress week's projected rumbles/H2H
        # on top, same as before.
        for r in rows:
            manager = r[1]
            rumbles_val = int(r[2])
            h2h_w, h2h_l = (int(x) for x in r[8].split("-"))
            if mode == "actual":
                assert rumbles_val == BASELINE_RUMBLES[manager], (
                    f"[actual] {manager}: Actualized Rumbles must equal the frozen history value "
                    f"{BASELINE_RUMBLES[manager]} (not folding in this week's live rumbles), got {rumbles_val}"
                )
                assert (h2h_w, h2h_l) == BASELINE_H2H[manager], (
                    f"[actual] {manager}: Actualized H2H W-L must equal the frozen history record "
                    f"{BASELINE_H2H[manager]}, got {(h2h_w, h2h_l)}"
                )
            else:
                this_week_rumbles = int(r[3].replace("LIVE", ""))
                expected_total = BASELINE_RUMBLES[manager] + this_week_rumbles
                assert rumbles_val == expected_total, (
                    f"[{mode}] {manager}: Projected Rumbles must be history ({BASELINE_RUMBLES[manager]}) "
                    f"+ this week's live rumbles ({this_week_rumbles}) = {expected_total}, got {rumbles_val}"
                )
                assert h2h_w + h2h_l == 2, (
                    f"[{mode}] {manager}: Projected H2H W-L must include this week's live matchup "
                    f"on top of the 1 already-final game (total 2 decisions), got {(h2h_w, h2h_l)}"
                )
        print(f"Verified actualized-vs-projected Rumbles/H2H split for {mode} mode.")

        # Joe's roster has ZERO actual stats recorded for anyone (still
        # pregame) -- Actual mode must show exactly the history PF with no
        # addition, while PPR/Custom must still show a real, nonzero
        # projected total (never just falling back to 0).
        joe_pf = pf_by_manager["Joe"]
        if mode == "actual":
            assert abs(joe_pf - 102.5) < 0.05, f"Joe (fully pregame roster) should show flat history PF in Actual mode, got {joe_pf}"
        else:
            assert joe_pf > 102.5 + 1.0, f"Joe (fully pregame roster) should show a real nonzero projection in {mode} mode, got {joe_pf}"

    # Confirm the three modes aren't secretly aliased to each other. We
    # don't require all 3 displayed (1-decimal, rounded) PF values to
    # differ for every single manager -- with arbitrary generic-pattern
    # test data, two modes can land within 0.1 of each other by sheer
    # coincidence and round to the same displayed string (verified: Alex's
    # Actual=139.14 vs Sleeper Projection=139.05, both display "139.1" --
    # genuinely different underlying numbers). What actually matters is
    # that they don't ALL THREE collapse to one value for anybody.
    actual_pf = {r[1]: r[6] for r in rows_by_mode["actual"]}
    ppr_pf = {r[1]: r[6] for r in rows_by_mode["ppr"]}
    custom_pf = {r[1]: r[6] for r in rows_by_mode["custom"]}
    for manager in actual_pf:
        vals = {actual_pf[manager], ppr_pf[manager], custom_pf[manager]}
        assert len(vals) >= 2, (
            f"{manager}: Actual/Sleeper Projection/Custom PF must not all collapse to the same value, got {vals}"
        )
    print("\nConfirmed no manager's PF collapses to a single value across all three modes.")

    live_badges = page.locator(".badge-live").count()
    # Both the "This Week" and "Points This Week" cells carry a LIVE badge
    # now, so it's 2 per roster.
    assert live_badges == 24, f"expected 24 LIVE badges (2 per roster x 12 rosters), got {live_badges}"

    page.click("#refresh-btn")
    page.wait_for_timeout(500)
    assert page.text_content("#refresh-btn") == "Refresh now"

    error_visible = page.eval_on_selector("#error-banner", "el => el.classList.contains('show')")
    assert not error_visible, "error banner should not be showing when all mocked routes succeed"

    page.close()
    assert not console_errors, f"console errors found: {console_errors}"
    assert not page_errors, f"page errors found: {page_errors}"
    print("\nSCENARIO 1 PASSED")


def scenario_cumulative_only(browser):
    print("\n" + "=" * 70)
    print("SCENARIO 2: Week 1 final, Sleeper's pointer lagging, Week 2 not posted")
    print("(this is the reported bug -- table must NOT be blank)")
    print("=" * 70)
    console_errors, page_errors = [], []
    page = new_page(browser, console_errors, page_errors)
    routes = {
        "**/rumbles_history.json": load("rumbles_history.json"),  # weeks_completed: [1]
        "**/v1/state/nfl": load("state_lagging.json"),  # Sleeper still says week 1
        "**/v1/league/TESTLEAGUE1": load("league.json"),
        "**/v1/league/TESTLEAGUE1/matchups/2": load("matchups_week2_empty.json"),  # not posted yet
        "**/stats/nfl/2026/2*": [],
        "**/projections/nfl/2026/2*": [],
    }
    install_routes(page, routes)
    page.goto(PAGE_URL, wait_until="load")
    page.wait_for_timeout(1000)

    status_text = page.text_content("#status-text")
    print("Status text:", status_text)
    assert status_text == "Not live right now", f"expected 'Not live right now', got: {status_text}"

    rows = get_table_rows(page)
    print("\n== Standings (no live week) ==")
    for r in rows:
        print(r)
    assert len(rows) == 12, f"table must show the 12 cumulative standings, not be blank -- got {len(rows)} rows"

    empty_state = page.locator(".empty-state").count()
    assert empty_state == 0, "empty-state message should not be showing when history has real standings"

    live_badges = page.locator(".badge-live").count()
    assert live_badges == 0, f"no live badges expected when no week is in progress, found {live_badges}"

    # Rank 1 should be Kaitlyn (roster 11, 19 rumbles) per the history
    # fixture's Week 1 math (see make_fixtures.py / test/fixtures/rumbles_history.json)
    # -- confirms cumulative-only standings are exactly what history says,
    # nothing blended on top.
    assert rows[0][1] == "Kaitlyn", f"expected Kaitlyn in 1st with no live overlay, got: {rows[0]}"
    assert rows[0][2] == "19", f"expected Kaitlyn's cumulative Rumbles to be 19, got: {rows[0][2]}"

    error_visible = page.eval_on_selector("#error-banner", "el => el.classList.contains('show')")
    assert not error_visible, "no error banner expected -- both fetches succeeded, there's just no live week"

    page.close()
    assert not console_errors, f"console errors found: {console_errors}"
    assert not page_errors, f"page errors found: {page_errors}"
    print("\nSCENARIO 2 PASSED")


def scenario_history_load_failure(browser):
    print("\n" + "=" * 70)
    print("SCENARIO 3: rumbles_history.json fails to load (e.g. 404)")
    print("=" * 70)
    console_errors, page_errors = [], []
    page = new_page(browser, console_errors, page_errors)
    routes = {
        # Deliberately no route for rumbles_history.json -- it falls
        # through to the real local static server and 404s for real, since
        # no such file exists at the served project root.
        "**/v1/state/nfl": load("state_lagging.json"),
    }
    install_routes(page, routes)
    page.goto(PAGE_URL, wait_until="load")
    page.wait_for_timeout(1000)

    empty_state_count = page.locator(".empty-state").count()
    assert empty_state_count == 1, "expected the empty-state placeholder row, not real standings rows"

    empty_text = page.text_content(".empty-state")
    print("Empty-state text:", empty_text)
    assert "Couldn't load rumbles_history.json" in empty_text, f"expected a clear load-failure message, got: {empty_text}"

    error_visible = page.eval_on_selector("#error-banner", "el => el.classList.contains('show')")
    # With zero rows, the load failure is communicated via the empty-state
    # message itself (see render()); the banner path is for when we have
    # older rows still on screen. Either way the failure must be visible
    # somewhere on the page, which the empty-state assertion above covers.
    print("Error banner visible:", error_visible)

    page.close()
    assert not page_errors, f"page errors found: {page_errors}"
    print("\nSCENARIO 3 PASSED")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
            args=[
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-domain-reliability",
                "--no-first-run",
            ],
        )
        scenario_live_blending(browser)
        scenario_cumulative_only(browser)
        scenario_history_load_failure(browser)
        browser.close()

    print("\nALL SCENARIOS PASSED")


if __name__ == "__main__":
    main()
