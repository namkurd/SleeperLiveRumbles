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
    print("SCENARIO 1: a week is genuinely live")
    print("=" * 70)
    console_errors, page_errors = [], []
    page = new_page(browser, console_errors, page_errors)
    routes = {
        "**/rumbles_history.json": load("rumbles_history.json"),
        "**/v1/state/nfl": load("state.json"),  # week 2, regular
        "**/v1/league/TESTLEAGUE1": load("league.json"),
        "**/v1/league/TESTLEAGUE1/matchups/2": load("matchups_week2.json"),
        "**/stats/nfl/2026/2": load("stats_week2.json"),
        "**/projections/nfl/2026/2": load("projections_week2.json"),
    }
    install_routes(page, routes)
    page.goto(PAGE_URL, wait_until="load")
    page.wait_for_timeout(1000)

    status_text = page.text_content("#status-text")
    print("Status text:", status_text)
    assert "Live" in status_text and "Week 2" in status_text, f"expected live week 2 status, got: {status_text}"

    ppr_rows = get_table_rows(page)
    print("\n== Generic PPR mode ==")
    for r in ppr_rows:
        print(r)
    assert len(ppr_rows) == 12, f"expected 12 rows, got {len(ppr_rows)}"

    # PF column is index 5. Hand-verified expected totals for the two
    # rosters make_fixtures.py hand-crafted specifically to exercise the
    # max(actual-so-far, live-projection) blending:
    #   Aidan (roster 1): played player is early in their game with a low
    #     actual stat line but a much higher live projection -> the LIVE
    #     PROJECTION should win -> PF = 92.5 (history) + 22.0 (played,
    #     projection wins) + 10.0 (unplayed, projection) = 124.5
    #   Jake (roster 3): played player is having a blowout, actual already
    #     exceeds their pregame projection -> ACTUAL should win -> PF =
    #     97.5 (history) + 35.0 (played, actual wins) + 8.0 (unplayed,
    #     projection) = 140.5
    expected_ppr_pf = {"Aidan": 124.5, "Jake": 140.5}
    ppr_pf_by_manager_check = {r[1]: float(r[5]) for r in ppr_rows}
    for manager, expected in expected_ppr_pf.items():
        actual = ppr_pf_by_manager_check[manager]
        assert abs(actual - expected) < 0.05, (
            f"{manager}: expected PPR-mode PF {expected} (verifying live-projection-vs-actual "
            f"blending), got {actual}"
        )
    print("\nHand-verified live-blending PF (PPR mode) checks passed:", expected_ppr_pf)

    live_badges = page.locator(".badge-live").count()
    assert live_badges == 12, f"expected 12 LIVE badges (one per roster), got {live_badges}"

    page.click("#mode-custom")
    page.wait_for_timeout(200)
    custom_rows = get_table_rows(page)
    print("\n== Custom Scoring mode ==")
    for r in custom_rows:
        print(r)

    # Same hand-verified check, custom-scoring mode:
    #   Aidan (roster 1): custom score of played player -> projection
    #     (17.0) beats actual (2.5) -> PF = 92.5 + 17.0 + 12.5 = 122.0
    #   Jake (roster 3): custom score of played player -> actual (30.0)
    #     beats projection (7.0) -> PF = 97.5 + 30.0 + 4.0 = 131.5
    expected_custom_pf = {"Aidan": 122.0, "Jake": 131.5}
    custom_pf_by_manager_check = {r[1]: float(r[5]) for r in custom_rows}
    for manager, expected in expected_custom_pf.items():
        actual = custom_pf_by_manager_check[manager]
        assert abs(actual - expected) < 0.05, (
            f"{manager}: expected custom-mode PF {expected} (verifying live-projection-vs-actual "
            f"blending), got {actual}"
        )
    print("Hand-verified live-blending PF (custom mode) checks passed:", expected_custom_pf)

    ppr_pf_by_manager = {r[1]: r[5] for r in ppr_rows}
    custom_pf_by_manager = {r[1]: r[5] for r in custom_rows}
    differing = [m for m in ppr_pf_by_manager if ppr_pf_by_manager[m] != custom_pf_by_manager[m]]
    print("\nManagers with different PF between modes:", len(differing), "/", len(ppr_pf_by_manager))
    assert len(differing) == len(ppr_pf_by_manager), (
        f"expected ALL managers to have different PF between PPR/custom modes, only {len(differing)} did: {differing}"
    )

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
        "**/stats/nfl/2026/2": {},
        "**/projections/nfl/2026/2": {},
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
