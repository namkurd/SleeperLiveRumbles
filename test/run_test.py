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
import datetime
import json
import os

from playwright.sync_api import sync_playwright

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
PAGE_URL = "http://127.0.0.1:8123/rumbles.html"


def load(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


# The MIN (Kyler Murray) game's kickoff time, read back out of the scores
# fixture rather than hardcoded a second time here -- see make_fixtures.py's
# MNF_START_MS comment. Used by scenario_live_blending's Alex assertion via
# format_game_start_label (defined below).
MNF_START_UTC = datetime.datetime.fromtimestamp(
    next(g["start_time"] for g in load("scores_week2.json") if "start_time" in g) / 1000,
    tz=datetime.timezone.utc,
)

# The DAL/PHI game's kickoff time (Joe's roster, P9/P10) -- a SECOND,
# distinct displayed kickoff label ("Sun 1pm") used to verify
# buildTimeSlotColors assigns different colors to different labels. Picked
# out by away_team "DAL" rather than just "the other entry with a
# start_time" so this stays correct if a third timed game is ever added.
SUN_START_UTC = datetime.datetime.fromtimestamp(
    next(g["start_time"] for g in load("scores_week2.json") if g.get("metadata", {}).get("away_team") == "DAL") / 1000,
    tz=datetime.timezone.utc,
)


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


def get_qb_adjustment_manager_colors(page):
    """manager name -> inline matchup-name text color in the QB Injury
    Backup Adjustments table, or None if uncolored (see get_matchup_colors
    -- same idea, different table)."""
    pairs = page.eval_on_selector_all(
        "#qb-adj-body tr",
        """rows => rows.map(r => {
            var cell = r.querySelector('td.manager-cell');
            if (!cell) return null;
            var name = cell.innerText.trim();
            var span = cell.querySelector('.matchup-name');
            var color = span ? span.style.color : null;
            return [name, color || null];
        }).filter(p => p !== null)""",
    )
    return dict(pairs)


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


def get_pts_this_week_colors(page):
    """manager name -> inline color style of their 'Pts This Week' cell
    (td.thisweek-pts), or None if uncolored -- same idea as
    get_matchup_colors, but for the win-coloring feature: only the team
    currently AHEAD in its live H2H matchup (under whichever mode is
    currently selected) gets colored (see render() in rumbles.html)."""
    pairs = page.eval_on_selector_all(
        "#standings-body tr",
        """rows => rows.map(r => {
            var name = r.querySelector('td.manager').innerText.trim();
            var cell = r.querySelector('td.thisweek-pts');
            var color = cell ? cell.style.color : null;
            return [name, color || null];
        })""",
    )
    return dict(pairs)


def get_thisweek_rumbles_colors(page):
    """manager name -> inline color style of their 'This Week' (Rumbles
    earned so far this week) cell (td.thisweek), or None if uncolored --
    same win-coloring feature/idea as get_pts_this_week_colors, applied to
    the OTHER per-week cell."""
    pairs = page.eval_on_selector_all(
        "#standings-body tr",
        """rows => rows.map(r => {
            var name = r.querySelector('td.manager').innerText.trim();
            var cell = r.querySelector('td.thisweek');
            var color = cell ? cell.style.color : null;
            return [name, color || null];
        })""",
    )
    return dict(pairs)


def get_tooltip_row_computed_colors(page, manager):
    """Hovers the given manager's 'Pts This Week' cell (same interaction as
    get_thisweek_pts_tooltip) and returns a list of {name, time_color,
    name_color, actual_color, proj_color} -- real COMPUTED (getComputedStyle)
    colors as rgb() strings, one dict per visible tooltip row, in display
    order, or None if that cell has no tooltip. Computed style is needed
    here (rather than the raw inline style.color that get_matchup_colors/
    get_pts_this_week_colors read) because the live-row Name/Actual
    green-coloring is a CSS class rule (`tr.pts-tooltip-live td...`), not an
    inline style -- only the per-timeslot time-column color is actually set
    inline (see renderPtsTooltipContent), but computed style reads both
    uniformly. Moves the mouse away afterward so the next check starts
    clean."""
    idx = page.eval_on_selector_all(
        "#standings-body tr td.manager",
        "cells => cells.map(c => c.innerText.trim())",
    ).index(manager)
    cell = page.locator("#standings-body tr").nth(idx).locator("td.thisweek-pts")
    classes = cell.get_attribute("class") or ""
    if "has-tooltip" not in classes:
        return None
    cell.hover()
    page.wait_for_timeout(150)
    tip = page.locator("#pts-tooltip")
    is_visible = tip.evaluate("el => el.classList.contains('visible')")
    rows = None
    if is_visible:
        rows = page.eval_on_selector_all(
            "#pts-tooltip tbody tr",
            """trs => trs.map(tr => {
                var tds = tr.querySelectorAll('td');
                var hasTime = tds.length === 4;
                var i = hasTime ? 1 : 0;
                var timeCell = hasTime ? tds[0] : null;
                return {
                    name: tds[i].innerText.trim(),
                    time_color: timeCell ? getComputedStyle(timeCell).color : null,
                    name_color: getComputedStyle(tds[i]).color,
                    actual_color: getComputedStyle(tds[i + 1]).color,
                    proj_color: getComputedStyle(tds[i + 2]).color,
                };
            })""",
        )
    page.mouse.move(0, 0)
    page.wait_for_timeout(150)
    return rows


DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def format_game_start_label(dt_utc):
    """Python mirror of rumbles.html's formatGameStartLabel, given a
    timezone-aware UTC datetime -- valid as a mirror only because every
    test page/context is pinned to timezone_id="UTC" (see new_page/
    new_mobile_page below), so the browser's local Date methods and this
    function's UTC fields agree. Minutes are deliberately never shown
    ("1pm", not "1:00pm")."""
    hour = dt_utc.hour
    hour12 = hour % 12 or 12
    ampm = "pm" if hour >= 12 else "am"
    js_weekday = (dt_utc.weekday() + 1) % 7  # Python: Mon=0 -> JS: Sun=0
    return f"{DAY_NAMES[js_weekday]} {hour12}{ampm}"


def get_thisweek_pts_tooltip(page, manager):
    """Hovers the given manager's 'Pts This Week' cell (real mouse hover,
    exercising the actual show/position logic, not just the underlying
    state) and returns the custom #pts-tooltip's visible table content as a
    list of {time, name, actual, proj, live} dicts (one per body row, in
    DOM/display order), or None if that cell has no tooltip to show. `time`
    is the row's own separate kickoff-time column ("Mon 1pm", Projected
    mode only) -- "" for a row without a resolvable game, and "" for every
    row in Actual mode (that column doesn't exist there at all -- see
    showStartTime in rumbles.html). Moves the mouse away afterward so the
    next check starts clean."""
    idx = page.eval_on_selector_all(
        "#standings-body tr td.manager",
        "cells => cells.map(c => c.innerText.trim())",
    ).index(manager)
    cell = page.locator("#standings-body tr").nth(idx).locator("td.thisweek-pts")
    classes = cell.get_attribute("class") or ""
    if "has-tooltip" not in classes:
        return None
    cell.hover()
    page.wait_for_timeout(150)
    tip = page.locator("#pts-tooltip")
    is_visible = tip.evaluate("el => el.classList.contains('visible')")
    rows = None
    if is_visible:
        rows = page.eval_on_selector_all(
            "#pts-tooltip tbody tr",
            """trs => trs.map(tr => {
                var tds = tr.querySelectorAll('td');
                var hasTime = tds.length === 4;
                var i = hasTime ? 1 : 0;
                return {
                    time: hasTime ? tds[0].innerText.trim() : "",
                    name: tds[i].innerText.trim(),
                    actual: tds[i + 1].innerText.trim(),
                    proj: tds[i + 2].innerText.trim(),
                    live: tr.classList.contains('pts-tooltip-live'),
                };
            })""",
        )
    page.mouse.move(0, 0)
    page.wait_for_timeout(150)
    return rows


def new_page(browser, console_errors, page_errors):
    # timezone_id="UTC" pins Date's local-time methods (getHours/getDay,
    # used by rumbles.html's formatGameStartLabel) to a known offset, so
    # the "Mon 8pm"-style kickoff-label assertions are deterministic
    # regardless of whatever timezone the machine running this test is in.
    page = browser.new_page(timezone_id="UTC")
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    return page


def new_mobile_page(browser, console_errors, page_errors):
    """A touch-primary context (no mouse) -- Chromium reports (hover: none)
    and (pointer: coarse) for this, exactly like a real phone, which is
    what makes rumbles.html pick the tap-to-toggle code path for the
    'Pts This Week' tooltip instead of the hover path (see
    supportsHover in rumbles.html). timezone_id="UTC" for the
    same determinism reason as new_page above."""
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, timezone_id="UTC")
    page = context.new_page()
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
        "**/scores/nfl/regular/2026/2": load("scores_week2.json"),
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
    colors_by_mode = {}
    for mode, selector in mode_buttons.items():
        if selector:
            page.click(selector)
            page.wait_for_timeout(200)
        rows = get_table_rows(page)
        rows_by_mode[mode] = rows
        colors_by_mode[mode] = get_matchup_colors(page)
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

        # ---- "Pts This Week" hover tooltip: a table of per-starter actual/
        # projected rows -- which starters are even included depends on the
        # Actual/Projected toggle; every row shows both an Actual and a Proj
        # column regardless of mode.
        # Aidan (roster 1) is hand-verified above: played starter "P1" (now
        # given real metadata -- "Amon-Ra St. Brown" on team DET, actual
        # 2.5, pregame projection 17.0) and unplayed starter "P2" (no
        # metadata -- falls back to the raw player_id, actual 0, projection
        # 12.5 per HAND_CRAFTED_PROJ_UNPLAYED[1]). scores_week2.json marks
        # DET as "complete" -- Amon-Ra's real game is fully over, exactly
        # the reported scenario -- so Projected mode must exclude him
        # entirely even though Actual mode still shows him.
        aidan_rows = get_thisweek_pts_tooltip(page, "Aidan")
        assert aidan_rows, f"[{mode}] Aidan's Pts This Week cell should have a hover tooltip, got {aidan_rows!r}"
        if mode == "actual":
            # Only played-or-live starters -- P2 hasn't played, so it must
            # NOT appear at all (a flat "0.00" row here would be noise in
            # a view that's explicitly about banked, real production). A
            # completed game does NOT get excluded in Actual mode -- that
            # exclusion is Projected-mode-only (see below). No time column
            # at all in Actual mode (kickoff time is a Projected-only
            # concept), and "complete" isn't "in_progress" so this row
            # isn't live.
            assert aidan_rows == [{"time": "", "name": "Amon-Ra St. Brown", "actual": "2.50", "proj": "17.00", "live": False}], (
                f"[actual] expected Aidan's tooltip to list only the played starter (by real name now that P1 has metadata), got {aidan_rows}"
            )
        else:
            # Amon-Ra St. Brown's game (DET) is marked "complete" -- must
            # be excluded from Projected entirely, leaving only P2 (whose
            # team/game is unresolvable -- blank time cell, not live).
            assert aidan_rows == [{"time": "", "name": "P2", "actual": "0.00", "proj": "12.50", "live": False}], (
                f"[{mode}] expected Aidan's tooltip to exclude the finished starter (Amon-Ra St. Brown / DET, complete) "
                f"and show only the still-pregame one, got {aidan_rows}"
            )
        print(f"Verified Pts This Week tooltip content for {mode} mode (Aidan):", aidan_rows)

        # Alex (roster 6): Kyler Murray's team (MIN) is "in_progress", NOT
        # "complete" -- Projected mode must still include him (only a fully
        # finished game gets excluded, not one that's still being played),
        # colored live (green), with his game's "Mon 1pm"-style kickoff
        # label (from scores_week2.json's MNF_START_MS) in its OWN column,
        # separate from his name. His roster-mate P12 (unresolvable game --
        # blank time cell, not live) must come FIRST despite being
        # starters[1] -- league.json's roster_positions is deliberately
        # reversed (see make_fixtures.py) so this only passes if the
        # slot-order re-sort actually ran.
        if mode != "actual":
            alex_rows = get_thisweek_pts_tooltip(page, "Alex")
            expected_kickoff = format_game_start_label(MNF_START_UTC)
            assert alex_rows == [
                {"time": "", "name": "P12", "actual": "0.00", "proj": "21.23", "live": False},
                {"time": expected_kickoff, "name": "Kyler Murray", "actual": "7.20", "proj": "21.09", "live": True},
            ], (
                f"[{mode}] expected Alex's Projected tooltip to list P12 first (slot re-sort), then a live "
                f"Kyler Murray with a separate kickoff-time column (MIN, in_progress -- not complete), got {alex_rows}"
            )
            print(f"Verified Pts This Week tooltip content for {mode} mode (Alex, slot order + live color + kickoff column):", alex_rows)

            # ---- Live-row coloring is scoped to Name + Actual only, NOT
            # Proj -- Kyler Murray's row (still in progress) should have his
            # Name and Actual cells computed-colored the same fixed green
            # (--matchup-4, #008300 -- unchanged between light/dark mode, so
            # this is safe to hardcode) that the CSS class rule applies,
            # while his Proj cell must NOT be that color (it keeps the
            # tooltip's default text color instead).
            LIVE_GREEN_RGB = "rgb(0, 131, 0)"  # #008300, i.e. var(--matchup-4)
            alex_colors = get_tooltip_row_computed_colors(page, "Alex")
            kyler_colors = next(r for r in alex_colors if r["name"] == "Kyler Murray")
            assert kyler_colors["name_color"] == LIVE_GREEN_RGB, (
                f"[{mode}] expected Kyler Murray's live-row Name cell to be colored green ({LIVE_GREEN_RGB}), got {kyler_colors['name_color']}"
            )
            assert kyler_colors["actual_color"] == LIVE_GREEN_RGB, (
                f"[{mode}] expected Kyler Murray's live-row Actual cell to be colored green ({LIVE_GREEN_RGB}), got {kyler_colors['actual_color']}"
            )
            assert kyler_colors["proj_color"] != LIVE_GREEN_RGB, (
                f"[{mode}] expected Kyler Murray's live-row Proj cell to NOT be colored green (only Name+Actual go green on a live row), got {kyler_colors['proj_color']}"
            )
            print(f"Verified live-row green coloring is scoped to Name+Actual (not Proj) for {mode} mode (Alex/Kyler Murray).")

            # ---- Per-timeslot kickoff-time-column coloring: Joe's two
            # starters (P9/DAL, P10/PHI) share the SAME game ("Sun 1pm",
            # chronologically before Alex's "Mon 8pm" MIN game) and so must
            # share the same time-column color; that color must differ from
            # Kyler Murray's "Mon 8pm" time-column color, proving
            # buildTimeSlotColors assigns colors per DISPLAYED label, not
            # just one flat color for every game.
            joe_colors = get_tooltip_row_computed_colors(page, "Joe")
            assert joe_colors and len(joe_colors) == 2, f"[{mode}] expected exactly 2 tooltip rows for Joe (P9 + P10), got {joe_colors}"
            joe_time_colors = {r["time_color"] for r in joe_colors}
            assert len(joe_time_colors) == 1, (
                f"[{mode}] expected Joe's two starters (same game, same displayed kickoff label) to share ONE time-column color, got {joe_time_colors}"
            )
            joe_time_color = next(iter(joe_time_colors))
            kyler_time_color = kyler_colors["time_color"]
            assert joe_time_color and kyler_time_color, f"[{mode}] expected both Joe's and Kyler Murray's time cells to be colored, got {joe_time_color!r} / {kyler_time_color!r}"
            assert joe_time_color != kyler_time_color, (
                f"[{mode}] expected Joe's 'Sun 1pm' time-column color to differ from Kyler Murray's 'Mon 8pm' time-column color, both got {joe_time_color}"
            )
            print(f"Verified per-timeslot kickoff-time-column coloring for {mode} mode (Joe's 'Sun 1pm' pair share a color, differing from Alex's 'Mon 8pm').")

            # Sanity check the premise via the plain text too (not just
            # color): Joe's rows really do show the "Sun 1pm" label (read
            # back from the fixture, not hardcoded a second time), distinct
            # from Alex's "Mon 8pm"-equivalent label.
            expected_sun_label = format_game_start_label(SUN_START_UTC)
            joe_rows_for_label_check = get_thisweek_pts_tooltip(page, "Joe")
            joe_labels = {r["time"] for r in joe_rows_for_label_check}
            assert joe_labels == {expected_sun_label}, (
                f"[{mode}] expected both of Joe's starters to show the '{expected_sun_label}' kickoff label, got {joe_labels}"
            )

        # ---- "This Week"/"Pts This Week" cell win-coloring, in BOTH modes:
        # the team currently AHEAD in this week's live H2H matchup (under
        # whichever mode is selected) gets both cells colored to match its
        # own manager-name color; the trailing team keeps the default
        # (uncolored -> falls back to CSS blue). Joe (roster 5) vs Alex
        # (roster 6) are this week's H2H pair, and the two modes disagree
        # on who's ahead -- exercising that the coloring is genuinely
        # mode-scoped, not just "whoever won under one particular mode":
        #   - actual: Joe 0.00 vs Alex 7.20 -> ALEX is ahead (nobody on
        #     Joe's roster has any actual stats yet).
        #   - custom/Projected: Joe 42.04 vs Alex 28.43 -> JOE is ahead.
        winner, loser = ("Alex", "Joe") if mode == "actual" else ("Joe", "Alex")
        pts_colors = get_pts_this_week_colors(page)
        rumbles_colors = get_thisweek_rumbles_colors(page)
        for label, colors in (("Pts This Week", pts_colors), ("This Week", rumbles_colors)):
            assert colors.get(winner) is not None, f"[{mode}] expected {winner}'s {label!r} cell to be colored (they're ahead in this week's live H2H matchup)"
            assert colors[winner] == colors_by_mode[mode][winner], (
                f"[{mode}] expected {winner}'s winning {label!r} color ({colors[winner]}) to match their own matchup-name color ({colors_by_mode[mode][winner]})"
            )
            assert colors.get(loser) is None, (
                f"[{mode}] expected {loser}'s (trailing) {label!r} cell to stay uncolored (default color), got {colors.get(loser)}"
            )
        print(f"Verified This Week/Pts This Week win-coloring for {mode} mode ({winner} colored to match their matchup color, {loser} left default).")

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

        # Joe's whole roster is still pregame -- in Actual mode every one of
        # his starters gets filtered out of the tooltip (nobody's played or
        # live yet), so there must be NO tooltip at all rather than an empty
        # or all-zero one. In Projected mode his starters are still shown
        # (with actual 0.00 alongside a real projection).
        joe_rows = get_thisweek_pts_tooltip(page, "Joe")
        if mode == "actual":
            assert joe_rows is None, f"[actual] Joe (fully pregame roster) should have no Pts This Week tooltip, got {joe_rows}"
        else:
            assert joe_rows, f"[{mode}] Joe should still have a tooltip showing his pregame projections, got {joe_rows}"
            assert all(r["actual"] == "0.00" and not r["live"] for r in joe_rows), (
                f"[{mode}] Joe's tooltip rows should all show Actual 0.00 and not be live (nobody on his roster has played or kicked off), got {joe_rows}"
            )

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

    # A manager's matchup color must be the SAME regardless of which mode
    # (Actual/Projected) is selected -- the color is assigned by roster_id
    # pairing, not by rank, specifically so it can't shuffle when the two
    # modes produce different scores (and therefore different rank order).
    for manager in colors_by_mode["actual"]:
        assert colors_by_mode["actual"][manager] == colors_by_mode["custom"][manager], (
            f"{manager}'s matchup color changed between Actual ({colors_by_mode['actual'][manager]}) "
            f"and Projected ({colors_by_mode['custom'][manager]}) mode -- it must stay the same in both"
        )
    print("Confirmed matchup colors are IDENTICAL between Actual and Projected mode (not rank-dependent).")

    # The QB Injury Backup Adjustments table's Manager column must reuse
    # these exact same colors for the same managers -- keyed by the
    # manager's CURRENT week's matchup, not by which week that particular
    # log row happens to be about. Alex's row IS this week's (week 2);
    # Ben's row is the historical week-1 entry, but Ben is STILL part of
    # this week's live matchup (paired with Aidan) so his name should be
    # colored too -- proving the color follows "who's playing whom right
    # now", not just "rows about the live week".
    qb_colors = get_qb_adjustment_manager_colors(page)
    assert qb_colors.get("Alex") == colors["Alex"], (
        f"expected Alex's QB-log manager color ({qb_colors.get('Alex')}) to match the standings table's ({colors['Alex']})"
    )
    assert qb_colors.get("Ben") == colors["Ben"], (
        f"expected Ben's QB-log manager color ({qb_colors.get('Ben')}) to match the standings table's ({colors['Ben']}) "
        f"even though his log row is about week 1, not the current live week"
    )
    print("Confirmed the QB Injury Backup Adjustments table reuses the standings table's matchup colors for the same managers (by current matchup, not by the log row's own week).")

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
    assert len(qb_rows) == 3, f"expected exactly 3 QB-adjustment rows (1 historical + 2 live), got {len(qb_rows)}"

    # Roster 8 (Ankit) has a commissioner override but no identifiable
    # backup QB in the fixtures at all -- the override must STILL log a
    # "Confirmed" row, falling back to the override delta as the backup
    # total rather than silently vanishing because the stats-based
    # heuristic found nothing. This is the exact bug class being
    # regression-tested (a real override not showing up in the log).
    override_only_row = next((r for r in qb_rows if r["manager"] == "Ankit"), None)
    assert override_only_row is not None, f"expected a 'Confirmed' row for Ankit's override even with no identifiable backup, got: {qb_rows}"
    assert override_only_row["confidence"] == "Confirmed", f"expected 'Confirmed', got: {override_only_row['confidence']}"
    assert override_only_row["backups"] == [], f"no backup could be identified -- expected an empty backup list, got: {override_only_row['backups']}"
    assert override_only_row["backup_points"] == "15.00", f"expected the backup total to fall back to the override delta (15.00), got: {override_only_row['backup_points']}"

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

    assert [r["week"] for r in qb_rows] == ["2", "2", "1"], f"expected rows sorted week descending (both week-2 rows, then week-1), got weeks: {[r['week'] for r in qb_rows]}"
    assert [r["manager"] for r in qb_rows[:2]] == ["Alex", "Ankit"], f"expected the two week-2 rows sorted by manager A-Z, got: {[r['manager'] for r in qb_rows[:2]]}"

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


def scenario_mobile_tap_tooltip(browser):
    print("\n" + "=" * 70)
    print("SCENARIO 1b: same live week, but a touch-primary (mobile) context --")
    print("'Pts This Week' tooltip must be tap-to-toggle, not hover-only")
    print("=" * 70)
    console_errors, page_errors = [], []
    page = new_mobile_page(browser, console_errors, page_errors)
    routes = {
        "**/rumbles_history.json": load("rumbles_history.json"),
        "**/v1/state/nfl": load("state.json"),
        "**/v1/league/TESTLEAGUE1": load("league.json"),
        "**/v1/league/TESTLEAGUE1/matchups/2": load("matchups_week2.json"),
        "**/stats/nfl/2026/2*": load("stats_week2.json"),
        "**/projections/nfl/2026/2*": load("projections_week2.json"),
        "**/v1/players/nfl": load("players.json"),
        "**/scores/nfl/regular/2026/2": load("scores_week2.json"),
    }
    install_routes(page, routes)
    page.goto(PAGE_URL, wait_until="load")
    page.wait_for_timeout(1000)

    # Sanity check the premise: this context genuinely looks like a phone
    # to the page's own hover-capability check, so it's actually exercising
    # the tap-to-toggle branch and not silently falling back to hover.
    supports_hover = page.evaluate("window.matchMedia('(hover: hover) and (pointer: fine)').matches")
    assert not supports_hover, "this context should NOT report itself as hover-capable -- the mobile emulation isn't taking effect"

    idx = page.eval_on_selector_all(
        "#standings-body tr td.manager",
        "cells => cells.map(c => c.innerText.trim())",
    ).index("Aidan")
    cell = page.locator("#standings-body tr").nth(idx).locator("td.thisweek-pts")
    assert "has-tooltip" in (cell.get_attribute("class") or ""), "Aidan's Pts This Week cell should be tappable (has-tooltip)"

    tip = page.locator("#pts-tooltip")
    assert not tip.evaluate("el => el.classList.contains('visible')"), "tooltip should start out hidden"

    # A plain hover (mouse-only event) must NOT show it in this context --
    # only a real tap should, since supportsHover is false here.
    cell.hover(force=True)
    page.wait_for_timeout(200)
    assert not tip.evaluate("el => el.classList.contains('visible')"), "a hover-only event must not show the tooltip on a touch-primary device"

    # Tap #1: shows it.
    cell.tap()
    page.wait_for_timeout(200)
    assert tip.evaluate("el => el.classList.contains('visible')"), "tapping the cell should show the tooltip"
    assert "Amon-Ra St. Brown" in tip.inner_text(), f"expected Aidan's tooltip content, got: {tip.inner_text()!r}"
    print("Tap #1 correctly showed the tooltip.")

    # Tap #2 on the SAME cell: toggles it back off.
    cell.tap()
    page.wait_for_timeout(200)
    assert not tip.evaluate("el => el.classList.contains('visible')"), "tapping the same cell again should toggle the tooltip closed"
    print("Tap #2 on the same cell correctly toggled it closed.")

    # Tap it open again, then tap somewhere else entirely -- must dismiss.
    cell.tap()
    page.wait_for_timeout(200)
    assert tip.evaluate("el => el.classList.contains('visible')"), "tooltip should be showing again after re-tapping"
    page.locator("#subtitle").tap()
    page.wait_for_timeout(200)
    assert not tip.evaluate("el => el.classList.contains('visible')"), "tapping elsewhere on the page should dismiss an open tooltip"
    print("Tapping elsewhere on the page correctly dismissed the open tooltip.")

    # ---- "How to Use" button: same tap-to-toggle behavior on a touch-
    # primary device (a plain hover must do nothing here, only a real tap).
    howto_btn = page.locator("#howto-btn")
    howto_tip = page.locator("#howto-tooltip")
    assert not howto_tip.evaluate("el => el.classList.contains('visible')"), "How to Use tooltip should start out hidden"

    howto_btn.hover(force=True)
    page.wait_for_timeout(200)
    assert not howto_tip.evaluate("el => el.classList.contains('visible')"), "a hover-only event must not show the How to Use tooltip on a touch-primary device"

    howto_btn.tap()
    page.wait_for_timeout(200)
    assert howto_tip.evaluate("el => el.classList.contains('visible')"), "tapping the How to Use button should show the tooltip"
    print("How to Use: tap correctly showed the tooltip on a touch-primary device.")

    howto_btn.tap()
    page.wait_for_timeout(200)
    assert not howto_tip.evaluate("el => el.classList.contains('visible')"), "tapping the How to Use button again should toggle it closed"

    howto_btn.tap()
    page.wait_for_timeout(200)
    assert howto_tip.evaluate("el => el.classList.contains('visible')"), "How to Use tooltip should be showing again after re-tapping"
    page.locator("#subtitle").tap()
    page.wait_for_timeout(200)
    assert not howto_tip.evaluate("el => el.classList.contains('visible')"), "tapping elsewhere should dismiss an open How to Use tooltip"
    print("How to Use: tap-to-toggle and tap-elsewhere-dismisses both work correctly on a touch-primary device.")

    page.close()
    assert not console_errors, f"console errors found: {console_errors}"
    assert not page_errors, f"page errors found: {page_errors}"
    print("\nSCENARIO 1b PASSED")


def scenario_howto_tooltip(browser):
    print("\n" + "=" * 70)
    print("SCENARIO 1c: 'How to Use' button, on a real-hover (desktop) device")
    print("=" * 70)
    console_errors, page_errors = [], []
    page = new_page(browser, console_errors, page_errors)
    # This scenario doesn't need any live-week data -- the How to Use
    # button/tooltip are static and always present regardless of whether a
    # week is live -- but every route the page might touch still needs a
    # response (an unmocked one 404s against the local static server and
    # trips this scenario's own console-error check), so this reuses the
    # same "not live" fixture set as scenario_cumulative_only.
    routes = {
        "**/rumbles_history.json": load("rumbles_history.json"),
        "**/v1/state/nfl": load("state_lagging.json"),
        "**/v1/league/TESTLEAGUE1": load("league.json"),
        "**/v1/league/TESTLEAGUE1/matchups/2": load("matchups_week2_empty.json"),
        "**/stats/nfl/2026/2*": [],
        "**/projections/nfl/2026/2*": [],
        "**/scores/nfl/regular/2026/2": [],
    }
    install_routes(page, routes)
    page.goto(PAGE_URL, wait_until="load")
    page.wait_for_timeout(1000)

    btn = page.locator("#howto-btn")
    assert btn.count() == 1, "expected a 'How to Use' button in the header"
    assert btn.inner_text().strip() == "How to Use", f"expected the button's label to read 'How to Use', got {btn.inner_text()!r}"

    tip = page.locator("#howto-tooltip")
    assert not tip.evaluate("el => el.classList.contains('visible')"), "How to Use tooltip should start out hidden"

    # Hover shows it.
    btn.hover()
    page.wait_for_timeout(150)
    assert tip.evaluate("el => el.classList.contains('visible')"), "hovering the How to Use button should show the tooltip"
    tip_text = tip.inner_text()
    print("How to Use tooltip content:\n", tip_text)
    # The headings render visually uppercase (text-transform: uppercase in
    # CSS), which innerText reflects -- compare case-insensitively.
    tip_text_lower = tip_text.lower()
    assert "actual" in tip_text_lower and "projected" in tip_text_lower, f"expected the tooltip to explain both the Actual and Projected tabs, got: {tip_text!r}"
    # No em dashes anywhere in the explainer copy.
    assert "—" not in tip_text, f"expected no em dashes in the How to Use tooltip copy, got: {tip_text!r}"
    # Shouldn't mention the QB Injury Backup Adjustments table -- that's a
    # separate feature with its own on-page description already.
    assert "QB" not in tip_text and "backup" not in tip_text.lower(), f"expected the How to Use tooltip to NOT mention the QB injury backup adjustments feature, got: {tip_text!r}"
    print("Confirmed hover shows the tooltip, with Actual/Projected explainer content, no em dashes, and no mention of the QB adjustments table.")

    # Moving the mouse away hides it again.
    page.mouse.move(0, 0)
    page.wait_for_timeout(150)
    assert not tip.evaluate("el => el.classList.contains('visible')"), "moving the mouse away from the How to Use button should hide the tooltip"
    print("Confirmed moving the mouse away hides the tooltip.")

    # Clicking toggles it open even without hovering first (force=True skips
    # Playwright's actionability hover step, isolating the click itself).
    btn.click(force=True)
    page.wait_for_timeout(150)
    assert tip.evaluate("el => el.classList.contains('visible')"), "clicking the How to Use button should show the tooltip"
    print("Confirmed clicking the button shows the tooltip.")

    # Clicking it again toggles it back closed.
    btn.click(force=True)
    page.wait_for_timeout(150)
    assert not tip.evaluate("el => el.classList.contains('visible')"), "clicking the How to Use button again should toggle the tooltip closed"
    print("Confirmed clicking the button again toggles the tooltip closed.")

    # Click to open, then click elsewhere on the page -- must dismiss.
    btn.click(force=True)
    page.wait_for_timeout(150)
    assert tip.evaluate("el => el.classList.contains('visible')"), "tooltip should be showing again after re-clicking"
    page.locator("#subtitle").click()
    page.wait_for_timeout(150)
    assert not tip.evaluate("el => el.classList.contains('visible')"), "clicking elsewhere on the page should dismiss an open How to Use tooltip"
    print("Confirmed clicking elsewhere on the page dismisses an open tooltip.")

    page.close()
    assert not console_errors, f"console errors found: {console_errors}"
    assert not page_errors, f"page errors found: {page_errors}"
    print("\nSCENARIO 1c PASSED")


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
        "**/scores/nfl/regular/2026/2": [],
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
        scenario_mobile_tap_tooltip(browser)
        scenario_howto_tooltip(browser)
        scenario_cumulative_only(browser)
        scenario_history_load_failure(browser)
        browser.close()

    print("\nALL SCENARIOS PASSED")


if __name__ == "__main__":
    main()
