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


def get_qb_adjustment_rows(page):
    """One dict per row of the QB Injury Backup Adjustments table."""
    return page.eval_on_selector_all(
        "#qb-adj-body tr",
        """rows => rows.map(r => {
            var tds = Array.from(r.querySelectorAll('td'));
            if (tds.length < 7) return null; // the empty-state placeholder row
            var backups = Array.from(tds[3].querySelectorAll('.backup-list span')).map(s => s.innerText.trim());
            var pill = tds[6].querySelector('.confidence-pill');
            return {
                week: tds[0].innerText.trim(),
                manager: tds[1].innerText.trim(),
                injured_qb: tds[2].innerText.trim(),
                backups: backups,
                injured_points: tds[4].innerText.trim(),
                backup_points: tds[5].innerText.trim(),
                confidence: pill ? pill.innerText.trim() : null,
                live: !!tds[6].querySelector('.badge-live'),
            };
        }).filter(r => r !== null)""",
    )


def get_matchup_colors(page):
    """manager name -> inline matchup-name text color (e.g. 'var(--matchup-2)'),
    or None if that row's name isn't colored (not in a live matchup this week)."""
    pairs = page.eval_on_selector_all(
        "#standings-body tr",
        """rows => rows.map(r => {
            var name = r.querySelector('td.manager').innerText.trim();
            var span = r.querySelector('td.manager .matchup-name');
            var color = span ? span.style.color : null;
            return [name, color || null];
        })""",
    )
    return dict(pairs)


def new_page(browser, console_errors, page_errors):
    page = browser.new_page()
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    return page


def scenario_live_blending(browser):
    print("\n" + "=" * 70)
    print("SCENARIO 1: a week is genuinely live -- Actual / Projected")
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
        "**/v1/players/nfl": load("players.json"),
    }
    install_routes(page, routes)
    page.goto(PAGE_URL, wait_until="load")
    page.wait_for_timeout(1000)

    status_text = page.text_content("#status-text")
    print("Status text:", status_text)
    assert "Live" in status_text and "Week 2" in status_text, f"expected live week 2 status, got: {status_text}"

    # Hand-verified expected totals (see make_fixtures.py's HAND_CRAFTED_*
    # and ZERO_ACTUAL_ROSTERS). "Actual" only ever uses real stats.
    # "Projected" (formerly "Our Custom Scoring") reproduces how Sleeper
    # itself computes its live "projected" total: a player who has an
    # actual-stats entry (their
    # game has started) contributes their REAL performance, not their
    # frozen pregame projection -- only still-pregame players use the
    # projection. This matches what Sleeper's own matchup page shows
    # (verified directly against a real live matchup: reproduced Sleeper's
    # own displayed number to the penny -- see rumbles.html's scoreFrom
    # comment). Both lenses dot-product against the league's real
    # scoring_settings -- never Sleeper's generic pts_ppr/pts_std fields,
    # which are a different scoring system entirely for a non-PPR league
    # like this fixture's (rec weight is 0.0, rec_fd/rush_fd/pass_fd carry
    # the real weight instead).
    #   Aidan (roster 1, history PF 92.5): played player has a small actual
    #     stat line (2.5) and a much bigger pregame projection (17.0)
    #     that's now ignored in favor of the real performance; unplayed
    #     player only has a projection (12.5).
    #     Actual PF stays frozen at history (92.5) -- Actual mode no longer
    #     folds the in-progress week into PF/PA/Vs.Field at all, only
    #     "Points This Week" shows the live 2.5 figure.
    #     Custom=2.5+12.5=15.0 folded on top of history -> PF 92.5+15.0=107.5
    #   Jake (roster 3, history PF 97.5): played player is having a
    #     blowout (33.0 actual -- 30.0 base plus the kr_yd-alias (+4.0) and
    #     fgmiss-tier-sum (-1.0) regression checks, see make_fixtures.py)
    #     that now replaces the much smaller pregame projection (7.0);
    #     unplayed player only has a projection (4.0).
    #     Actual PF stays frozen at history (97.5).
    #     Custom=33.0+4.0=37.0 folded on top of history -> PF 97.5+37.0=134.5
    #   Joe (roster 5, history PF 102.5): entire roster is still pregame,
    #     zero actual stats recorded for anyone -- nothing to swap in, so
    #     Actual PF stays frozen at 102.5 (same number Custom's fallback
    #     path also happens to start from, before folding its projection).
    # The frozen/actualized baseline is exactly what's already in
    # rumbles_history.json (only Week 1 is final in this fixture) -- pull
    # it straight from there rather than re-deriving the formula, so this
    # stays correct if the fixture's week-1 pattern ever changes.
    hist_standings = load("rumbles_history.json")["standings"]
    BASELINE_RUMBLES = {s["manager"]: s["rumbles"] for s in hist_standings}
    BASELINE_H2H = {s["manager"]: (s["h2h_w"], s["h2h_l"]) for s in hist_standings}
    BASELINE_PA = {s["manager"]: s["pa"] for s in hist_standings}
    BASELINE_VS_FIELD = {s["manager"]: (s["vs_field_w"], s["vs_field_l"]) for s in hist_standings}

    # Actual mode: PF is frozen to history, full stop (no more history + this
    # week's actual points -- that only happens in Custom/Projected mode now).
    expected = {
        "actual": {"Aidan": 92.5, "Jake": 97.5, "Joe": 102.5},
        "custom": {"Aidan": 107.5, "Jake": 134.5, "Joe": None},  # Joe's custom PF depends on generic-pattern math; checked separately below
    }
    # "Points This Week" is the raw score for just this week (not the
    # cumulative PF) -- i.e. exactly liveInfo.points for the selected mode.
    # Displayed to 2 decimal places now (was 1).
    expected_points_this_week = {
        "actual": {"Aidan": 2.5, "Jake": 33.0, "Joe": 0.0},
        "custom": {"Aidan": 15.0, "Jake": 37.0, "Joe": None},
    }

    mode_buttons = {"actual": None, "custom": "#mode-custom"}
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

        # Both the Rumbles and H2H headers are now static text -- no more
        # "Actualized"/"Projected" prefix on either, since the mode toggle
        # itself already covers that distinction. Confirm neither changes
        # with mode (only a sort-arrow suffix would ever change this text,
        # and no column is sorted here). H2H/Vs. Field also dropped the
        # trailing "W-L" from their header labels (the cells themselves
        # still show "W-L" records like "2-0").
        th_rumbles = page.text_content("#th-rumbles")
        assert th_rumbles == "Rumbles", f"[{mode}] expected static 'Rumbles' header, got {th_rumbles!r}"
        th_h2h = page.text_content("#th-h2h")
        assert th_h2h == "H2H", f"[{mode}] expected static 'H2H' header, got {th_h2h!r}"
        th_vsfield = page.text_content("#th-vsfield")
        assert th_vsfield == "Vs. Field", f"[{mode}] expected static 'Vs. Field' header, got {th_vsfield!r}"

        # Column order: 0=#, 1=Manager, 2=Rumbles, 3=This Week, 4=Points
        # This Week, 5=Rumble %, 6=PF, 7=PA, 8=H2H, 9=Vs. Field.
        pf_by_manager = {r[1]: float(r[6]) for r in rows}
        for manager, expected_pf in expected[mode].items():
            if expected_pf is None:
                continue
            got = pf_by_manager[manager]
            # PF now displays to 2 decimal places (toFixed(2)).
            assert abs(got - expected_pf) < 0.01, (
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
            assert abs(got - expected_pts) < 0.01, (
                f"[{mode}] {manager}: expected Points This Week {expected_pts}, got {got}"
            )
        print(f"Hand-verified Points This Week checks passed for {mode} mode:", {k: v for k, v in expected_points_this_week[mode].items() if v is not None})

        # Actual mode's Rumbles/H2H/PF/PA/Vs.Field W-L must ALL be frozen to
        # what's already final in rumbles_history.json -- the in-progress
        # week's actual-score outcome must NOT be folded into any of the
        # five season-cumulative columns (only "This Week"/"Points This
        # Week" show it live). Projected mode DOES fold the in-progress
        # week's numbers into all five, same as before.
        for r in rows:
            manager = r[1]
            rumbles_val = int(r[2])
            pa_val = float(r[7])
            h2h_w, h2h_l = (int(x) for x in r[8].split("-"))
            vf_w, vf_l = (int(x) for x in r[9].split("-"))
            if mode == "actual":
                assert rumbles_val == BASELINE_RUMBLES[manager], (
                    f"[actual] {manager}: Actualized Rumbles must equal the frozen history value "
                    f"{BASELINE_RUMBLES[manager]} (not folding in this week's live rumbles), got {rumbles_val}"
                )
                assert (h2h_w, h2h_l) == BASELINE_H2H[manager], (
                    f"[actual] {manager}: H2H W-L must equal the frozen history record "
                    f"{BASELINE_H2H[manager]}, got {(h2h_w, h2h_l)}"
                )
                assert abs(pa_val - BASELINE_PA[manager]) < 0.01, (
                    f"[actual] {manager}: PA must equal the frozen history value "
                    f"{BASELINE_PA[manager]} (not folding in this week's live PA), got {pa_val}"
                )
                assert (vf_w, vf_l) == BASELINE_VS_FIELD[manager], (
                    f"[actual] {manager}: Vs. Field W-L must equal the frozen history record "
                    f"{BASELINE_VS_FIELD[manager]}, got {(vf_w, vf_l)}"
                )
            else:
                this_week_rumbles = int(r[3].replace("LIVE", ""))
                expected_total = BASELINE_RUMBLES[manager] + this_week_rumbles
                assert rumbles_val == expected_total, (
                    f"[{mode}] {manager}: Projected Rumbles must be history ({BASELINE_RUMBLES[manager]}) "
                    f"+ this week's live rumbles ({this_week_rumbles}) = {expected_total}, got {rumbles_val}"
                )
                assert h2h_w + h2h_l == 2, (
                    f"[{mode}] {manager}: H2H W-L must include this week's live matchup "
                    f"on top of the 1 already-final game (total 2 decisions), got {(h2h_w, h2h_l)}"
                )
                assert pa_val > BASELINE_PA[manager] + 0.01, (
                    f"[{mode}] {manager}: Projected PA must fold in this week's live opponent score "
                    f"on top of the frozen history value {BASELINE_PA[manager]}, got {pa_val}"
                )
                assert vf_w + vf_l == 22, (
                    f"[{mode}] {manager}: Projected Vs. Field W-L must include this week's 11 live "
                    f"decisions on top of the 11 already-final ones (total 22), got {(vf_w, vf_l)}"
                )
        print(f"Verified actualized-vs-projected Rumbles/H2H/PA/Vs.Field split for {mode} mode.")

        # Joe's roster has ZERO actual stats recorded for anyone (still
        # pregame) -- Actual mode must show exactly the history PF with no
        # addition, while Projected must still show a real, nonzero
        # projected total (never just falling back to 0).
        joe_pf = pf_by_manager["Joe"]
        if mode == "actual":
            assert abs(joe_pf - 102.5) < 0.05, f"Joe (fully pregame roster) should show flat history PF in Actual mode, got {joe_pf}"
        else:
            assert joe_pf > 102.5 + 1.0, f"Joe (fully pregame roster) should show a real nonzero projection in {mode} mode, got {joe_pf}"

    # Confirm the two modes aren't secretly aliased to each other. Now that
    # PF displays to 2 decimal places, an arbitrary generic-pattern
    # coincidence is astronomically unlikely, so this can be a strict
    # distinctness check.
    actual_pf = {r[1]: r[6] for r in rows_by_mode["actual"]}
    custom_pf = {r[1]: r[6] for r in rows_by_mode["custom"]}
    for manager in actual_pf:
        assert actual_pf[manager] != custom_pf[manager], (
            f"{manager}: Actual and Custom PF must differ, got {actual_pf[manager]} for both"
        )
    print("\nConfirmed every manager's PF differs between Actual and Projected.")

    # ---- Matchup color-coding: this week's H2H pairs share a dot color ----
    # Pairing in the fixture is (1,2) (3,4) (5,6) (7,8) (9,10) (11,12) by
    # roster_id -- i.e. (Aidan,Ben) (Jake,Rohaan) (Joe,Alex) (Steven,Ankit)
    # (Christian,Ryan) (Kaitlyn,Stephanie). Check via the manager NAMES
    # (not roster_id, which isn't exposed in the table) using that mapping.
    colors = get_matchup_colors(page)
    expected_pairs = [
        ("Aidan", "Ben"), ("Jake", "Rohaan"), ("Joe", "Alex"),
        ("Steven", "Ankit"), ("Christian", "Ryan"), ("Kaitlyn", "Stephanie"),
    ]
    for a, b in expected_pairs:
        assert colors.get(a) is not None, f"{a}'s name should be colored (live week in progress)"
        assert colors[a] == colors[b], (
            f"{a} and {b} are this week's H2H opponents and their names should share a color, "
            f"got {colors[a]!r} vs {colors[b]!r}"
        )
    distinct_colors = {colors[a] for a, _ in expected_pairs}
    assert len(distinct_colors) == 6, (
        f"expected 6 distinct matchup colors (one per pair), got {len(distinct_colors)}: {distinct_colors}"
    )
    print("\nConfirmed matchup color-coding: each H2H pair shares a name color, all 6 pairs distinct.")

    # ---- Column sorting: "#" must stay pinned to season standing ----
    baseline_rank = {r[1]: r[0] for r in get_table_rows(page)}  # manager -> "#" before any sort

    def current_rows():
        return get_table_rows(page)

    def assert_rank_unchanged(rows, label):
        for r in rows:
            manager = r[1]
            assert r[0] == baseline_rank[manager], (
                f"[{label}] {manager}'s '#' changed from {baseline_rank[manager]} to {r[0]} after "
                f"sorting by a different column -- '#' must always reflect the fixed season standing"
            )

    # Sort by PF: first click defaults to descending (highest first).
    page.click("#th-pf")
    page.wait_for_timeout(150)
    rows = current_rows()
    pf_values = [float(r[6]) for r in rows]
    assert pf_values == sorted(pf_values, reverse=True), f"PF column not sorted descending: {pf_values}"
    assert_rank_unchanged(rows, "sort by PF desc")
    th_pf_text = page.text_content("#th-pf")
    assert "▼" in th_pf_text, f"expected a descending-sort arrow on the PF header, got {th_pf_text!r}"

    # Click PF again: toggles to ascending.
    page.click("#th-pf")
    page.wait_for_timeout(150)
    rows = current_rows()
    pf_values = [float(r[6]) for r in rows]
    assert pf_values == sorted(pf_values), f"PF column not sorted ascending after second click: {pf_values}"
    assert_rank_unchanged(rows, "sort by PF asc")
    th_pf_text = page.text_content("#th-pf")
    assert "▲" in th_pf_text, f"expected an ascending-sort arrow on the PF header, got {th_pf_text!r}"

    # Sort by Manager: text column, first click defaults to ascending A-Z.
    page.click("#th-manager")
    page.wait_for_timeout(150)
    rows = current_rows()
    managers = [r[1] for r in rows]
    assert managers == sorted(managers), f"Manager column not sorted A-Z: {managers}"
    assert_rank_unchanged(rows, "sort by Manager asc")

    # "#" itself IS sortable now too -- clicking it should restore/reverse
    # natural standings order. It must still never take on a DIFFERENT
    # value for a given manager just because some other column was sorted
    # (already exhaustively checked above); here just confirm clicking it
    # actually sorts and the values themselves are the untouched baseline.
    assert "sortable" in (page.get_attribute("#th-rank", "class") or ""), (
        "the '#' header should be sortable like any other column"
    )
    # Numeric columns (rank included) default to descending on first click,
    # same convention as PF/Rumbles/etc above -- worst standing first.
    page.click("#th-rank")
    page.wait_for_timeout(150)
    rows = current_rows()
    ranks = [int(r[0]) for r in rows]
    assert ranks == sorted(ranks, reverse=True), f"'#' column not sorted descending after first click: {ranks}"
    for r in rows:
        assert r[0] == baseline_rank[r[1]], f"sorting by '#' changed {r[1]}'s own rank value, got {r[0]}"

    # Second click flips to ascending -- natural standings order, i.e.
    # exactly the baseline order captured before any sorting happened.
    page.click("#th-rank")
    page.wait_for_timeout(150)
    rows = current_rows()
    ranks = [int(r[0]) for r in rows]
    assert ranks == sorted(ranks), f"'#' column not sorted ascending after second click: {ranks}"

    print("\nConfirmed column sorting works (including '#' itself) and every team's '#' value never changes.")

    live_badges = page.locator("#standings-body .badge-live").count()
    # Both the "This Week" and "Points This Week" cells carry a LIVE badge
    # now, so it's 2 per roster.
    assert live_badges == 24, f"expected 24 LIVE badges (2 per roster x 12 rosters), got {live_badges}"

    # ---- QB Injury Backup Adjustments table --------------------------
    # Roster 6 (Alex)'s started QB ("Kyler Murray", stats/players fixtures
    # above) has a same-team backup ("Carson Wentz") who recorded real
    # action this week -- must show up as a live "Likely" row (his
    # injury_status is "Out" in the fixture, no custom_points override
    # exists yet). The same-team 3rd-stringer who didn't play, and the
    # same-position QB on a DIFFERENT team who did, must both be excluded.
    # The synthetic week-1 "Confirmed" row from rumbles_history.json must
    # also be present, sorted below week 2 (weeks sort descending).
    qb_rows = get_qb_adjustment_rows(page)
    print("\n== QB Injury Backup Adjustments ==")
    for r in qb_rows:
        print(r)
    assert len(qb_rows) == 2, f"expected exactly 2 QB-adjustment rows (1 historical + 1 live), got {len(qb_rows)}"

    live_row = next((r for r in qb_rows if r["manager"] == "Alex"), None)
    assert live_row is not None, f"expected a live QB-adjustment row for Alex (roster 6), got: {qb_rows}"
    assert live_row["week"] == "2", f"expected the live row to be week 2, got: {live_row['week']}"
    assert live_row["injured_qb"] == "Kyler Murray", f"expected injured QB 'Kyler Murray', got: {live_row['injured_qb']}"
    assert live_row["backups"] == ["Carson Wentz (21.90)"], f"expected only Carson Wentz as backup (21.90 pts) -- 3rd-stringer and other-team QB must be excluded, got: {live_row['backups']}"
    assert live_row["injured_points"] == "7.20", f"expected Kyler Murray's own points to be 7.20, got: {live_row['injured_points']}"
    assert live_row["backup_points"] == "21.90", f"expected backup total 21.90, got: {live_row['backup_points']}"
    assert live_row["confidence"] == "Likely", f"expected 'Likely' confidence (injury_status 'Out', no override yet), got: {live_row['confidence']}"
    assert live_row["live"], "expected the live-detected row to carry a LIVE badge"

    hist_row = next((r for r in qb_rows if r["manager"] == "Ben"), None)
    assert hist_row is not None, f"expected the historical week-1 row for Ben, got: {qb_rows}"
    assert hist_row["week"] == "1", f"expected the historical row to be week 1, got: {hist_row['week']}"
    assert hist_row["confidence"] == "Confirmed", f"expected 'Confirmed' confidence for the historical override row, got: {hist_row['confidence']}"
    assert not hist_row["live"], "the historical (already-finalized) row must NOT carry a LIVE badge"

    assert qb_rows[0]["week"] == "2" and qb_rows[1]["week"] == "1", f"expected rows sorted week descending (2 then 1), got weeks: {[r['week'] for r in qb_rows]}"

    print("\nConfirmed QB Injury Backup Adjustments table: live detection (team-scoped, injury-status-corroborated) + historical merge + correct sort order.")

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

    # Both tables show their own empty-state now (the QB-adjustments table
    # has nothing to show either, since it also reads from the same failed
    # rumbles_history.json) -- scope this to the standings table specifically.
    empty_state_count = page.locator("#standings-body .empty-state").count()
    assert empty_state_count == 1, "expected the empty-state placeholder row, not real standings rows"

    empty_text = page.text_content("#standings-body .empty-state")
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
