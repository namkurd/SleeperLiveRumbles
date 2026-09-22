#!/usr/bin/env python3
"""End-to-end test of rumbles.html against mocked Sleeper API responses.

This sandbox has no direct network access to api.sleeper.app, so we run a
headless browser against the real page, intercept every request that would
go to Sleeper (or to rumbles_history.json), and fulfill it with fixture
JSON built by make_fixtures.py.

Scenarios (1a-1f share one live/pregame Week-2 fixture set and its variants,
2 and 3 are standalone):
  1. live_blending      -- a week is genuinely in progress; verifies the
                            actual-vs-live-projection blending and that
                            live figures land on top of cumulative history.
                            (1b/1c/1d/1e are mobile/touch/how-to/pregame
                            variants of this same fixture set.)
  1f. pointer_ahead_of_history -- reproduces THE reported production bug:
                            Week 2 has genuinely ended and Sleeper's own
                            state.week pointer has already advanced to 3,
                            but rumbles_history.json (only rewritten once a
                            day) still only has Week 1 finalized. The page
                            must still target/show Week 2's real live data,
                            not a blank Week 3.
  2. cumulative_only     -- the mirror-image case: Week 1 is fully final in
                            rumbles_history.json, but Sleeper's own
                            state.week pointer hasn't rolled over yet
                            (LAGGING, not ahead) and Week 2's matchups
                            aren't posted. The page must still show Week
                            1's cumulative standings, not a blank table.
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
    # The manager cell (column 1) can carry a trailing " *" (see
    # qb-adj-asterisk in rumbles.html -- a live, still-"likely" QB-injury
    # adjustment) and/or a trailing " W"/" L" (see lastweek-h2h-badge --
    # last week's H2H result, shown only pregame in Actual mode) --
    # stripped back out here so every manager-name-keyed dict built from
    # these rows elsewhere in this file keeps working off the plain
    # manager name. The "Pts This Week"/"Pts Last Week" cell (column 4) can
    # similarly carry a trailing " *" of its own (pts-lastweek-yellow -- a
    # still-"likely" QB-injury adjustment folded into THAT number, pregame
    # only), stripped the same way so numeric comparisons against this
    # column keep working. get_qb_adj_asterisk/get_qb_adj_tooltip_text,
    # get_lastweek_h2h_badge, and get_lastweek_pts_asterisk above/below are
    # what actually test those markers themselves.
    return page.eval_on_selector_all(
        "#standings-body tr",
        """rows => rows.map(r => Array.from(r.querySelectorAll('td')).map((td, i) => {
            var text = td.innerText.trim();
            if (i === 1) return text.replace(/\\s*QB Inj\\*$/, '').replace(/\\s+[WL]$/, '');
            if (i === 4) return text.replace(/\\s*\\*$/, '');
            return text;
        }))""",
    )


def get_qb_adjustment_rows(page):
    """One dict per row of the QB Injury Backup Adjustments table. Column
    order (7, since the Injured QB's own points were folded into its name
    cell -- "Name (7.20)", same format Backup QB(s) already used -- to
    condense the table down from 8 columns): 0=week, 1=manager, 2=injured
    QB (name+points), 3=backup QB(s), 4=backup QB points (total),
    5=Commissioner Adjustment, 6=Commissioner Status (just the confidence
    pill -- this table deliberately carries no LIVE badge at all, unlike
    the standings table; see the no-badge-live-at-all assertion right
    after this is called). `injured_qb`/`injured_points` are still split
    back apart here so existing assertions reading them separately don't
    have to change."""
    return page.eval_on_selector_all(
        "#qb-adj-body tr",
        """rows => rows.map(r => {
            var tds = Array.from(r.querySelectorAll('td'));
            if (tds.length < 7) return null; // the empty-state placeholder row
            var backups = Array.from(tds[3].querySelectorAll('.backup-list span')).map(s => s.innerText.trim());
            var pill = tds[6].querySelector('.confidence-pill');
            var injuredText = tds[2].innerText.trim();
            var im = injuredText.match(/^(.*) \\(([-\\d.]+)\\)$/);
            return {
                week: tds[0].innerText.trim(),
                manager: tds[1].innerText.trim(),
                injured_qb: im ? im[1] : injuredText,
                injured_points: im ? im[2] : null,
                backups: backups,
                backup_points: tds[4].innerText.trim(),
                commissioner_adjustment: tds[5].innerText.trim(),
                confidence: pill ? pill.innerText.trim() : null,
            };
        }).filter(r => r !== null)""",
    )


def get_qb_adjustment_row_colors(page):
    """manager -> {week_color, injured_color, backup_name_color,
    backup_points_color, commissioner_adj_color, status_color, pill_color}
    -- real COMPUTED (getComputedStyle) text colors as rgb() strings for
    every cell in that manager's QB Injury Backup Adjustments row, keyed
    by manager (assumes one row per manager in the fixture used, same
    assumption get_qb_adjustment_manager_colors already makes).
    `injured_color` covers the whole merged "Name (points)" cell (one
    color for both, same as the merged cell itself -- see
    get_qb_adjustment_rows). `status_color` is the Commissioner Status
    <td>'s own color (the row's manager color, same as week_color);
    `pill_color` is the Likely/Confirmed pill span's own color
    specifically (it sets its own `color` via
    .confidence-likely/.confidence-confirmed, so it's never the same as
    the td's inherited manager color)."""
    pairs = page.eval_on_selector_all(
        "#qb-adj-body tr",
        """rows => rows.map(r => {
            var tds = Array.from(r.querySelectorAll('td'));
            if (tds.length < 7) return null;
            var manager = tds[1].innerText.trim();
            var colorOf = (el) => getComputedStyle(el).color;
            var pill = tds[6].querySelector('.confidence-pill');
            return [manager, {
                manager_color: colorOf(tds[1].querySelector('.matchup-name') || tds[1]),
                week_color: colorOf(tds[0]),
                injured_color: colorOf(tds[2]),
                backup_name_color: colorOf(tds[3].querySelector('.backup-list span') || tds[3]),
                backup_points_color: colorOf(tds[4]),
                commissioner_adj_color: colorOf(tds[5]),
                status_color: colorOf(tds[6]),
                pill_color: pill ? colorOf(pill) : null,
            }];
        }).filter(p => p !== null)""",
    )
    return dict(pairs)


def get_css_var_color(page, var_name):
    """The real COMPUTED (getComputedStyle) color a plain element gets from
    `color: var(--{var_name})`, as an rgb() string -- resolved live in
    whichever light/dark scheme this browser context actually prefers
    (--good/--bad, unlike --matchup-4, differ between the two -- see
    :root's @media (prefers-color-scheme: light) override in rumbles.html),
    rather than hardcoding one scheme's hex value and hoping it matches."""
    return page.evaluate(
        """(varName) => {
            var probe = document.createElement('div');
            probe.style.color = 'var(--' + varName + ')';
            document.body.appendChild(probe);
            var c = getComputedStyle(probe).color;
            document.body.removeChild(probe);
            return c;
        }""",
        var_name,
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
            var name = r.querySelector('td.manager').innerText.trim().replace(/\\s*QB Inj\\*$/, '').replace(/\\s+[WL]$/, '');
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
            var name = r.querySelector('td.manager').innerText.trim().replace(/\\s*QB Inj\\*$/, '').replace(/\\s+[WL]$/, '');
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
            var name = r.querySelector('td.manager').innerText.trim().replace(/\\s*QB Inj\\*$/, '').replace(/\\s+[WL]$/, '');
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
    get_pts_this_week_colors read) because the live-row Name/Actual/Proj
    green-coloring is a CSS class rule (`tr.pts-tooltip-live td...`), not an
    inline style -- only the per-timeslot time-column color is actually set
    inline (see renderPtsTooltipContent), but computed style reads both
    uniformly. Moves the mouse away afterward so the next check starts
    clean."""
    idx = page.eval_on_selector_all(
        "#standings-body tr td.manager",
        "cells => cells.map(c => c.innerText.trim().replace(/\\s*QB Inj\\*$/, \'\').replace(/\\s+[WL]$/, \'\'))",
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
        "cells => cells.map(c => c.innerText.trim().replace(/\\s*QB Inj\\*$/, \'\').replace(/\\s+[WL]$/, \'\'))",
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


def get_thisweek_pts_tooltip_header(page, manager):
    """Hovers the given manager's 'Pts This Week' cell (same pattern as
    get_thisweek_pts_tooltip) and returns the tooltip's own header-row
    reference line (tooltip.actualPointsLabel in rumbles.html, e.g. '2.50:
    Actual Pts') -- Projected mode only, see thisWeekTooltip -- or None if
    that header cell is empty/absent (Actual mode, or no tooltip at all).
    Moves the mouse away afterward so the next check starts clean."""
    idx = page.eval_on_selector_all(
        "#standings-body tr td.manager",
        "cells => cells.map(c => c.innerText.trim().replace(/\\s*QB Inj\\*$/, '').replace(/\\s+[WL]$/, ''))",
    ).index(manager)
    cell = page.locator("#standings-body tr").nth(idx).locator("td.thisweek-pts")
    classes = cell.get_attribute("class") or ""
    if "has-tooltip" not in classes:
        return None
    cell.hover()
    page.wait_for_timeout(150)
    tip = page.locator("#pts-tooltip")
    is_visible = tip.evaluate("el => el.classList.contains('visible')")
    header = None
    if is_visible:
        header = page.evaluate(
            "() => { var el = document.querySelector('#pts-tooltip thead th.pts-tooltip-actual-total'); return el ? el.textContent.trim() : null; }"
        )
    page.mouse.move(0, 0)
    page.wait_for_timeout(150)
    return header


def get_qb_adj_asterisk(page, manager):
    """Locator for the given manager's manager-name '*' (see qb-adj-
    asterisk in rumbles.html), or None if they don't have one -- only a
    "likely" (not yet commissioner-confirmed) live QB-injury adjustment
    gets one (see applyQbAdjustmentsToScores/render())."""
    idx = page.eval_on_selector_all(
        "#standings-body tr td.manager",
        "cells => cells.map(c => c.innerText.replace('QB Inj*', '').trim())",
    ).index(manager)
    row = page.locator("#standings-body tr").nth(idx)
    el = row.locator("span.qb-adj-asterisk")
    return el if el.count() else None


def get_qb_adj_tooltip_text(page, manager):
    """Hovers the given manager's '*' (real mouse hover, same pattern as
    get_thisweek_pts_tooltip above) and returns the #qb-adj-tooltip's
    visible inner text, or None if they have no asterisk to hover. Moves
    the mouse away afterward so the next check starts clean."""
    el = get_qb_adj_asterisk(page, manager)
    if el is None:
        return None
    el.hover()
    page.wait_for_timeout(150)
    tip = page.locator("#qb-adj-tooltip")
    is_visible = tip.evaluate("el => el.classList.contains('visible')")
    text = tip.inner_text() if is_visible else None
    page.mouse.move(0, 0)
    page.wait_for_timeout(150)
    return text


def get_qb_adj_tooltip_points_color(page, manager):
    """Hovers the given manager's '*' and returns the COMPUTED (getComputedStyle)
    text color of the points-added figure inside the #qb-adj-tooltip (see
    .qb-adj-tooltip-yellow in rumbles.html), or None if they have no
    asterisk/tooltip to check. Same hover/move-away pattern as
    get_qb_adj_tooltip_text."""
    el = get_qb_adj_asterisk(page, manager)
    if el is None:
        return None
    el.hover()
    page.wait_for_timeout(150)
    tip = page.locator("#qb-adj-tooltip")
    is_visible = tip.evaluate("el => el.classList.contains('visible')")
    color = tip.evaluate(
        "el => { var s = el.querySelector('.qb-adj-tooltip-yellow'); return s ? getComputedStyle(s).color : null; }"
    ) if is_visible else None
    page.mouse.move(0, 0)
    page.wait_for_timeout(150)
    return color


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
        # Week 1's own matchups/stats -- fetched by loadLastWeekBreakdown
        # (always kicked off in parallel, independent of whatever the
        # CURRENT target week is doing -- see its own comment in
        # rumbles.html) for the "Pts Last Week" tooltip's underlying data.
        # Every scenario below whose history fixture has weeks_completed:
        # [1] needs this mocked too, or that fetch falls through to a real
        # (blocked) network request and fails the "no console errors"
        # assertion at the end of each scenario.
        "**/v1/league/TESTLEAGUE1/matchups/1": load("matchups_week1.json"),
        "**/stats/nfl/2026/1*": load("stats_week1.json"),
        "**/projections/nfl/2026/1*": load("projections_week1.json"),
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
    # "Projected" (formerly "Our Custom Scoring") uses a player's real stat
    # line once their game is CONFIRMED COMPLETE, their pregame projection
    # while it's still pregame, and while their game is CONFIRMED IN
    # PROGRESS, a clock-weighted BLEND of both: actualPts +
    # remainingFraction * pregameProjectionPts, where remainingFraction is
    # the fraction of the 60-minute game clock still left (1 at kickoff, 0
    # at the final whistle) -- see remainingFraction()/playerPoints'
    # comment in rumbles.html for the full derivation and how it was
    # validated against Sleeper's own live-displayed numbers for 8 real
    # players. A player whose game status can't be resolved at all
    # ("unknown" -- no team metadata, or the live-status feed came back
    # empty) falls back to the plain has-actual-stats check. Both lenses
    # dot-product against the league's real scoring_settings -- never
    # Sleeper's generic pts_ppr/pts_std fields, which are a different
    # scoring system entirely for a non-PPR league like this fixture's (rec
    # weight is 0.0, rec_fd/rush_fd/pass_fd carry the real weight instead).
    #   Aidan (roster 1, history PF 92.5): played player's game (DET) is
    #     marked COMPLETE in scores_week2.json, so his small actual stat
    #     line (2.5) is used, not his much bigger pregame projection
    #     (17.0); unplayed player only has a projection (12.5).
    #     Actual PF stays frozen at history (92.5) -- Actual mode no longer
    #     folds the in-progress week into PF/PA/Vs.Field at all, only
    #     "Points This Week" shows the live 2.5 figure.
    #     Custom=2.5+12.5=15.0 folded on top of history -> PF 92.5+15.0=107.5
    #   Jake (roster 3, history PF 97.5): played player has no team
    #     metadata at all (game status resolves to "unknown"), so the
    #     plain has-actual-stats fallback applies -- his blowout actual
    #     (29.0 -- 30.0 base, plus a "def_kr_yd" field that must
    #     contribute 0 now (a reversed alias -- see make_fixtures.py and
    #     rumbles.html's KEY_ALIASES comment) minus the fgmiss-tier-sum
    #     regression check (-1.0)) replaces the much smaller pregame
    #     projection (7.0); unplayed player only has a projection (4.0).
    #     Actual PF stays frozen at history (97.5).
    #     Custom=29.0+4.0=33.0 folded on top of history -> PF 97.5+33.0=130.5
    #   Alex (roster 6, history PF 105.0): played starter Kyler Murray's
    #     game (MIN) is marked IN_PROGRESS in scores_week2.json, with
    #     "quarter_num": 2 / "time_remaining": "9:00" -- 21:00 elapsed of
    #     60:00, i.e. remainingFraction = 39/60 = 0.65 (he's a QB, not a
    #     DEF, so effectiveRemainingFraction leaves this untouched -- see
    #     its comment). His actual so far is 7.20 and his pregame
    #     projection is 21.09, so his pace is 7.20/21.09=0.3414, giving a
    #     dampening of 0.40+0.60*exp(-1.10*0.3414)=0.8122 (see
    #     blendedProjection()'s comment in rumbles.html for why the
    #     remaining share gets dampened at all, and its current FLOOR/K
    #     constants). Blend = 7.20 + 0.65*21.09*0.8122 = 18.3334 -> rounds
    #     to 18.33. On top of his other starter's projection (21.23, still
    #     fully pregame, untouched by any of this) -> 18.33+21.23=39.56.
    #     ON TOP OF THAT: Kyler Murray is a live, "likely" QB-injury-backup
    #     scenario (injury_status "Out", same-team backup Carson Wentz
    #     scored 21.90 -- see the QB Injury Backup Adjustments checks
    #     below) -- applyQbAdjustmentsToScores now folds that 21.90 credit
    #     into BOTH his Actual and Custom live totals before Rumbles/PF/etc
    #     are computed, so: Actual this week = 7.20+0+21.90=29.10 (his
    #     other starter, P12, hasn't played), Custom this week =
    #     39.56+21.90=61.46 -> Custom PF = 105.0+61.46=166.46. Actual mode's
    #     PF itself stays frozen at history (105.0) regardless -- the QB
    #     credit only ever shows up in "Points This Week" and (in Custom
    #     mode) the season-cumulative columns, exactly like any other live
    #     point, never in Actual mode's frozen PF/Rumbles/H2H/PA/Vs.Field.
    #   Ankit (roster 8, history PF 110.0): a CONFIRMED live QB adjustment
    #     (the commissioner already set custom_points -- see the QB Injury
    #     Backup Adjustments checks below) with no identifiable backup QB
    #     in these fixtures. His one played starter (generic-pattern stats,
    #     no hand-crafted override) actually scores 34.42, and his
    #     unplayed starter's projection is 21.51 -- but applyQbAdjustments
    #     ToScores uses the OFFICIAL override delta (custom_points - points
    #     = 15.0 - 0 = 15.0) here, not an estimated backup total, per its
    #     "confirmed" tier. Actual this week = 34.42+15.0=49.42 (his
    #     second starter has an unresolvable game status, same as Jake's,
    #     so his actual stat line is used as-is rather than blended).
    #     Custom this week = 34.42+21.51+15.0=70.93 -> Custom PF =
    #     110.0+70.93=180.93. No asterisk/tooltip for Ankit -- that UI cue
    #     is "likely"-tier only (see render()).
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
        "actual": {"Aidan": 92.5, "Jake": 97.5, "Joe": 102.5, "Alex": 105.0},  # Alex's is unchanged by the live QB credit -- Actual mode's PF never folds the in-progress week in at all
        "custom": {"Aidan": 107.5, "Jake": 130.5, "Alex": 166.46, "Ankit": 180.93, "Joe": None},  # Joe's custom PF depends on generic-pattern math; checked separately below
    }
    # "Points This Week" is the raw score for just this week (not the
    # cumulative PF) -- i.e. exactly liveInfo.points for the selected mode.
    # Displayed to 2 decimal places now (was 1).
    expected_points_this_week = {
        "actual": {"Aidan": 2.5, "Jake": 29.0, "Joe": 0.0, "Alex": 29.10},
        "custom": {"Aidan": 15.0, "Jake": 33.0, "Alex": 61.46, "Ankit": 70.93, "Joe": None},
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
            # isn't live. "proj" now shows "2.50", matching "actual" exactly
            # rather than his 17.0 pregame projection -- once a game is
            # confirmed complete, blendedProjection's remainingFraction=0
            # collapses straight to actualPts (no more game left to
            # project), so this column becomes this page's live-projection
            # ESTIMATE rather than a fixed "what was expected of them"
            # figure (see buildPlayerBreakdown's comment on "proj").
            assert aidan_rows == [{"time": "", "name": "Amon-Ra St. Brown", "actual": "2.50", "proj": "2.50", "live": False}], (
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

        # ---- Projected-mode tooltip only: the header row's own real-
        # actual-total reference line ("2.50: Actual Pts" -- tooltip.
        # actualPointsLabel in rumbles.html) -- a fixed point of reference
        # sharing the header row with Actual/Proj, above an otherwise
        # all-projected table. Kept short (no manager name) so it fits that
        # row neatly. Actual mode's tooltip must NOT show this (its own
        # headline "Points This Week" figure right next to the cell already
        # IS the actual total, so restating it in the tooltip would be
        # redundant).
        aidan_header = get_thisweek_pts_tooltip_header(page, "Aidan")
        if mode == "actual":
            assert aidan_header is None, f"[actual] expected no actual-points header line in Actual mode's tooltip, got: {aidan_header!r}"
        else:
            assert aidan_header == "2.50: Actual Pts", (
                f"[{mode}] expected the tooltip header to read '2.50: Actual Pts' (Aidan's real actual "
                f"total this week, independent of the Projected total shown elsewhere), got: {aidan_header!r}"
            )
        print(f"Verified Pts This Week tooltip header for {mode} mode (Aidan): {aidan_header!r}")

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
        #
        # ---- QB-injury replacement row (Carson Wentz) in the "Points This
        # Week" tooltip -- appended after Alex's own (slot-sorted) starters,
        # same actual/proj math and Actual/Projected filtering as any other
        # player (see appendQbReplacementRows), shown in BOTH modes since
        # Carson Wentz has real actual stats recorded (played=true). His
        # "proj" (21.90) equals his "actual" exactly -- he has no pregame
        # projection at all (he's not a rostered player anywhere in these
        # fixtures), so blendedProjection's remaining-game term is
        # 0.65*0*dampening = 0, collapsing to just his actual points.
        # "live" is False here despite his game (MIN) genuinely being
        # in-progress -- get_thisweek_pts_tooltip reads the DOM's
        # "pts-tooltip-live" class, and a replacement row always renders
        # "pts-tooltip-replacement" instead (see renderPtsTooltipContent's
        # rowClass logic), never both -- the yellow replacement color
        # always wins over the plain live-green, by design.
        carson_wentz_row = {"time": "", "name": "Carson Wentz", "actual": "21.90", "proj": "21.90", "live": False}
        expected_kickoff = format_game_start_label(MNF_START_UTC)
        if mode != "actual":
            alex_rows = get_thisweek_pts_tooltip(page, "Alex")
            # Kyler Murray's "proj" is now 18.33, not his raw 21.09 pregame
            # projection -- see blendedProjection()'s comment in
            # rumbles.html: actual (7.20) + 65% of pregame (21.09) *
            # pace-dampening(7.20/21.09=0.3414) = 7.20 + 0.65*21.09*0.8122
            # = 18.33. P12 (unresolvable game -- "unknown" status, no
            # remainingFraction to blend with) keeps showing his raw
            # pregame projection unchanged, same as before this change.
            assert alex_rows == [
                {"time": "", "name": "P12", "actual": "0.00", "proj": "21.23", "live": False},
                {"time": expected_kickoff, "name": "Kyler Murray", "actual": "7.20", "proj": "18.33", "live": True},
                dict(carson_wentz_row, time=expected_kickoff),
            ], (
                f"[{mode}] expected Alex's Projected tooltip to list P12 first (slot re-sort), then a live "
                f"Kyler Murray with a separate kickoff-time column (MIN, in_progress -- not complete), then the "
                f"QB-injury replacement row (Carson Wentz) appended last, got {alex_rows}"
            )
            print(f"Verified Pts This Week tooltip content for {mode} mode (Alex, slot order + live color + kickoff column + replacement row):", alex_rows)
        else:
            # Actual mode excludes anyone who hasn't played at all (P12) --
            # only Kyler Murray (played) and the appended Carson Wentz
            # (also played) remain, no kickoff-time column at all in this
            # mode.
            alex_rows = get_thisweek_pts_tooltip(page, "Alex")
            assert alex_rows == [
                {"time": "", "name": "Kyler Murray", "actual": "7.20", "proj": "18.33", "live": True},
                carson_wentz_row,
            ], (
                f"[actual] expected Alex's Actual tooltip to list only played starters (Kyler Murray) plus the "
                f"QB-injury replacement row (Carson Wentz), got {alex_rows}"
            )
            print("Verified Pts This Week tooltip content for actual mode (Alex, replacement row included):", alex_rows)

        # The replacement row's Name/Actual/Proj must be this page's
        # distinct yellow (--replacement), not the usual live-green -- even
        # though Carson Wentz's own game (MIN) is genuinely still in
        # progress, same as Kyler Murray's.
        replacement_ref = get_css_var_color(page, "replacement")
        alex_colors_for_replacement = get_tooltip_row_computed_colors(page, "Alex")
        wentz_colors = next(r for r in alex_colors_for_replacement if r["name"] == "Carson Wentz")
        assert wentz_colors["name_color"] == replacement_ref, f"[{mode}] expected Carson Wentz's replacement-row Name to be yellow ({replacement_ref}), got {wentz_colors['name_color']}"
        assert wentz_colors["actual_color"] == replacement_ref, f"[{mode}] expected Carson Wentz's replacement-row Actual to be yellow ({replacement_ref}), got {wentz_colors['actual_color']}"
        assert wentz_colors["proj_color"] == replacement_ref, f"[{mode}] expected Carson Wentz's replacement-row Proj to be yellow ({replacement_ref}), got {wentz_colors['proj_color']}"
        print(f"Verified the QB-injury replacement row's Name/Actual/Proj are colored yellow ({replacement_ref}) in {mode} mode, not the usual live-green.")

        if mode != "actual":

            # ---- Live-row coloring covers Name + Actual + Proj -- Kyler
            # Murray's row (still in progress) should have all three cells
            # computed-colored the same fixed green (--matchup-4, #008300 --
            # unchanged between light/dark mode, so this is safe to
            # hardcode) that the CSS class rule applies. The Proj cell used
            # to be deliberately excluded from this rule; it was added so an
            # in-progress player's still-updating blended estimate reads as
            # visibly "live" too, matching the other two cells instead of
            # looking static next to them.
            LIVE_GREEN_RGB = "rgb(0, 131, 0)"  # #008300, i.e. var(--matchup-4)
            alex_colors = get_tooltip_row_computed_colors(page, "Alex")
            kyler_colors = next(r for r in alex_colors if r["name"] == "Kyler Murray")
            assert kyler_colors["name_color"] == LIVE_GREEN_RGB, (
                f"[{mode}] expected Kyler Murray's live-row Name cell to be colored green ({LIVE_GREEN_RGB}), got {kyler_colors['name_color']}"
            )
            assert kyler_colors["actual_color"] == LIVE_GREEN_RGB, (
                f"[{mode}] expected Kyler Murray's live-row Actual cell to be colored green ({LIVE_GREEN_RGB}), got {kyler_colors['actual_color']}"
            )
            assert kyler_colors["proj_color"] == LIVE_GREEN_RGB, (
                f"[{mode}] expected Kyler Murray's live-row Proj cell to ALSO be colored green ({LIVE_GREEN_RGB}), got {kyler_colors['proj_color']}"
            )
            print(f"Verified live-row green coloring covers Name+Actual+Proj for {mode} mode (Alex/Kyler Murray).")

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

        # ---- "This Week"/"Pts This Week" cell win-coloring: the team
        # currently AHEAD in this week's live H2H matchup (under whichever
        # mode is selected) gets both cells colored to match its own
        # manager-name color; the trailing team keeps the default
        # (uncolored -> falls back to CSS blue). Joe (roster 5) vs Alex
        # (roster 6) are this week's H2H pair. Alex leads in BOTH modes:
        # in Actual mode 29.10-0.00 (his played starter Kyler Murray's 7.20
        # PLUS the live 21.90 QB-injury-backup credit -- see
        # expected_points_this_week's comment above -- against Joe's whole
        # roster, still pregame with zero actual stats recorded for
        # anyone). In Projected mode Kyler Murray's game is "in_progress"
        # with 65% of the game clock left (see the MIN game's
        # quarter_num/time_remaining in make_fixtures.py) and his actual-
        # so-far (7.20) is already running at 34% of his full pregame
        # projection (21.09) -- a well-above-flat pace this early in the
        # game -- so blendedProjection's pace dampening (see its comment in
        # rumbles.html) pulls his remaining-game share down to about 81%
        # credit: 7.20 + 0.65*21.09*0.8122 = 18.33, +21.23 for his other
        # (fully pregame) starter = 39.56, +21.90 for the same live QB
        # credit = 61.46. That's well clear of Joe's Projected total (42.04,
        # his own two starters are both still pregame so none of this
        # touches him) -- before the QB-injury-backup-points feature added
        # that 21.90 credit to the live totals, Joe used to lead here
        # instead (42.04 vs 39.56); this scenario is what regression-tests
        # that the credit actually flows through detectQbAdjustmentsForWeek
        # -> applyQbAdjustmentsToScores -> computeRumblesForWeek and flips
        # who's actually ahead, not just a cosmetic log-table entry.
        winner, loser = "Alex", "Joe"
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
    # The "This Week" (Rumbles) column never carries a LIVE badge at all
    # any more -- it was removed entirely so mobile portrait view has room
    # to show #/Manager/Rumbles/This Week/Points This Week without
    # horizontal scrolling. Only "Points This Week" still shows one, for
    # every roster EXCEPT Steven's (roster 7): his two starters (P13/P14,
    # "SEA" in the fixtures) are the only ones in this whole fixture set
    # whose team's game is positively confirmed "complete" -- see
    # hasIncompletePlayer's comment in rumbles.html. That's 1 badge x 12
    # rosters, minus the one that's now missing = 11.
    assert live_badges == 11, f"expected 11 LIVE badges (1 per roster x 12 rosters, minus Steven's now-absent Points This Week one), got {live_badges}"

    steven_row = next(r for r in current_rows() if r[1] == "Steven")
    assert "LIVE" not in steven_row[3], (
        f"expected Steven's 'This Week' (Rumbles) cell to never show LIVE -- that column no longer carries a "
        f"LIVE badge for any roster, got {steven_row[3]!r}"
    )
    other_managers_missing_thisweek_live = [
        r[1] for r in current_rows() if "LIVE" in r[3]
    ]
    assert not other_managers_missing_thisweek_live, (
        f"expected NO manager's 'This Week' (Rumbles) cell to show a LIVE badge (removed entirely), but "
        f"these still had one: {other_managers_missing_thisweek_live}"
    )
    assert "LIVE" not in steven_row[4], (
        f"expected Steven's 'Points This Week' cell to have NO LIVE badge -- both his starters' games "
        f"(SEA, fixture-marked is_over=true) are positively confirmed complete, got {steven_row[4]!r}"
    )
    other_managers_missing_points_live = [
        r[1] for r in current_rows() if r[1] != "Steven" and "LIVE" not in r[4]
    ]
    assert not other_managers_missing_points_live, (
        f"expected every OTHER manager to still show LIVE in 'Points This Week' (none of their rosters have "
        f"every player positively confirmed complete), but these were missing it: {other_managers_missing_points_live}"
    )
    print(
        "Confirmed the 'This Week' (Rumbles) column never shows a LIVE badge for any roster, and the "
        "'Points This Week' LIVE pill disappears only once every one of a manager's players is positively "
        "confirmed complete (Steven), while every other manager's 'Points This Week' pill is unaffected."
    )

    # ---- QB Injury Backup Adjustments table --------------------------
    # Roster 6 (Alex)'s started QB ("Kyler Murray", stats/players fixtures
    # above) has a same-team backup ("Carson Wentz") who recorded real
    # action this week -- must show up as a live "Likely" row (his
    # injury_status is "Out" in the fixture, no custom_points override
    # exists yet). The same-team 3rd-stringer who didn't play, and the
    # same-position QB on a DIFFERENT team who did, must both be excluded.
    # The synthetic week-1 "Confirmed" row from rumbles_history.json must
    # also be present, sorted below week 2 (weeks sort descending).
    # textContent, not innerText -- thead th has CSS text-transform:
    # uppercase (a purely visual effect), which innerText would pick up
    # (rendering-aware) and textContent doesn't (raw markup text).
    qb_status_header = page.eval_on_selector("#qb-adj-table thead th:nth-child(7)", "el => el.textContent.trim()")
    assert qb_status_header == "Commissioner Status", f"expected the last column header to read 'Commissioner Status', got: {qb_status_header!r}"

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
    assert override_only_row["commissioner_adjustment"] == "+15.00", f"expected the new Commissioner Adjustment column to show the override delta (+15.00), got: {override_only_row['commissioner_adjustment']}"

    live_row = next((r for r in qb_rows if r["manager"] == "Alex"), None)
    assert live_row is not None, f"expected a live QB-adjustment row for Alex (roster 6), got: {qb_rows}"
    assert live_row["week"] == "2", f"expected the live row to be week 2, got: {live_row['week']}"
    assert live_row["injured_qb"] == "Kyler Murray", f"expected injured QB 'Kyler Murray', got: {live_row['injured_qb']}"
    assert live_row["backups"] == ["Carson Wentz (21.90)"], f"expected only Carson Wentz as backup (21.90 pts) -- 3rd-stringer and other-team QB must be excluded, got: {live_row['backups']}"
    assert live_row["injured_points"] == "7.20", f"expected Kyler Murray's own points to be 7.20, got: {live_row['injured_points']}"
    assert live_row["backup_points"] == "21.90", f"expected backup total 21.90, got: {live_row['backup_points']}"
    assert live_row["confidence"] == "Likely", f"expected 'Likely' confidence (injury_status 'Out', no override yet), got: {live_row['confidence']}"
    assert live_row["commissioner_adjustment"] == "—", f"expected the Commissioner Adjustment column to be blank (an em dash) for a still-'likely' row with no override yet, got: {live_row['commissioner_adjustment']}"

    hist_row = next((r for r in qb_rows if r["manager"] == "Ben"), None)
    assert hist_row is not None, f"expected the historical week-1 row for Ben, got: {qb_rows}"
    assert hist_row["week"] == "1", f"expected the historical row to be week 1, got: {hist_row['week']}"
    assert hist_row["confidence"] == "Confirmed", f"expected 'Confirmed' confidence for the historical override row, got: {hist_row['confidence']}"
    assert hist_row["commissioner_adjustment"] == "+12.34", f"expected the historical row's Commissioner Adjustment to show its own custom_points_delta (+12.34), got: {hist_row['commissioner_adjustment']}"

    # A STALE, never-confirmed "possible" entry from week 1 (Jake, see
    # make_fixtures.py) must be silently hidden -- week 2 is live now, so a
    # newer week has clearly already begun. This is rumbles.html's own
    # client-side backstop on top of build_rumbles.py's carry-forward rule
    # (determine_fresh_week): real production data showed a stale
    # "possible" entry can keep sitting in rumbles_history.json for a
    # while even after that backend fix shipped, since the scheduled job
    # that rewrites the file only runs once a day -- this filter makes the
    # live page correct immediately regardless of when that next run
    # happens, rather than waiting on it.
    assert not any(r["manager"] == "Jake" for r in qb_rows), (
        f"expected the stale, never-confirmed week-1 'possible' row for Jake to be hidden now that week 2 is "
        f"live, got: {qb_rows}"
    )

    assert [r["week"] for r in qb_rows] == ["2", "2", "1"], f"expected rows sorted week descending (both week-2 rows, then week-1), got weeks: {[r['week'] for r in qb_rows]}"
    assert [r["manager"] for r in qb_rows[:2]] == ["Alex", "Ankit"], f"expected the two week-2 rows sorted by manager A-Z, got: {[r['manager'] for r in qb_rows[:2]]}"
    print("Confirmed a stale, never-confirmed 'possible' entry from an older week is hidden once a newer week is live, even though it's still sitting in the history fixture.")

    qb_table_live_badges = page.locator("#qb-adj-table .badge-live").count()
    assert qb_table_live_badges == 0, (
        f"the QB Injury Backup Adjustments table should carry NO LIVE badge at all (the confidence pill's own "
        f"label already conveys status) -- not even for Alex's live-detected 'Likely' row, found {qb_table_live_badges}"
    )
    print("Confirmed the QB Injury Backup Adjustments table shows no LIVE badge anywhere, live-detected rows included.")

    print("\nConfirmed QB Injury Backup Adjustments table: live detection (team-scoped, injury-status-corroborated) + historical merge + correct sort order.")

    # ---- QB Injury Backup Adjustments table: row text coloring -----------
    # Every plain-text cell (Week, Commissioner Status) matches that row's
    # OWN manager matchup color (the same color already used for the
    # Manager cell and the standings table); the Injured QB's name/points
    # are always red (--bad) regardless of manager color, and Commissioner
    # Adjustment is always green -- see the CSS comment above
    # #qb-adj-table td.qb-adj-injured in rumbles.html. Resolved live via
    # getComputedStyle rather than hardcoded hex/rgb literals, since
    # --good/--bad/--replacement (unlike --matchup-4) actually differ
    # between light/dark mode.
    #
    # The Backup QB(s) NAME column, the Backup QB Points TOTAL, and the
    # Commissioner Status pill all instead track confidence, using the
    # SAME color for all three within a row: yellow (--replacement) while
    # still "likely" (Alex -- no commissioner adjustment yet), green
    # (--good) once "confirmed" (Ankit and Ben both have one, even though
    # Ankit's has no identifiable backup NAME -- the total still falls back
    # to the override delta and is still colored confirmed-green).
    good_ref = get_css_var_color(page, "good")
    bad_ref = get_css_var_color(page, "bad")
    replacement_ref = get_css_var_color(page, "replacement")
    row_colors = get_qb_adjustment_row_colors(page)
    confidence_by_manager = {"Alex": "likely", "Ankit": "confirmed", "Ben": "confirmed"}

    for manager in ["Alex", "Ankit", "Ben"]:
        rc = row_colors[manager]
        tier = confidence_by_manager[manager]
        expected_tier_color = replacement_ref if tier == "likely" else good_ref
        assert rc["week_color"] == rc["manager_color"], (
            f"{manager}: expected the Week cell to match their own manager color ({rc['manager_color']}), got {rc['week_color']}"
        )
        assert rc["injured_color"] == bad_ref, f"{manager}: expected the Injured QB name+points cell to be red ({bad_ref}), got {rc['injured_color']}"
        assert rc["commissioner_adj_color"] == good_ref, f"{manager}: expected Commissioner Adjustment to be green ({good_ref}), got {rc['commissioner_adj_color']}"
        assert rc["backup_points_color"] == expected_tier_color, (
            f"{manager}: expected the Backup QB Points total to be "
            f"{'yellow (' + replacement_ref + ', still likely -- no commissioner adjustment yet)' if tier == 'likely' else 'green (' + good_ref + ', commissioner-confirmed)'}"
            f", got {rc['backup_points_color']}"
        )
        assert rc["pill_color"] == expected_tier_color, (
            f"{manager}: expected the Commissioner Status pill to be "
            f"{'yellow (' + replacement_ref + ')' if tier == 'likely' else 'green (' + good_ref + ')'}, got {rc['pill_color']}"
        )
    # Backup QB name: only Alex and Ben actually have an identified backup
    # QB to color (Ankit's override has none -- see the empty `backups`
    # list checked above -- so there's no name text there to assert a
    # color on, even though its points TOTAL still gets checked above). The
    # name now tracks confidence the SAME way the total does (they share
    # one class -- see qb-adj-tier-* in rumbles.html), so Alex's ("likely")
    # is yellow, not the fixed green this used to be.
    for manager in ["Alex", "Ben"]:
        rc = row_colors[manager]
        tier = confidence_by_manager[manager]
        expected_name_color = replacement_ref if tier == "likely" else good_ref
        assert rc["backup_name_color"] == expected_name_color, (
            f"{manager}: expected the Backup QB name to track confidence like the total does "
            f"({'yellow, ' + replacement_ref if tier == 'likely' else 'green, ' + good_ref}), got {rc['backup_name_color']}"
        )
    print(
        "Confirmed QB Injury Backup Adjustments row coloring: Week/Commissioner Status match the manager color, "
        "Injured QB name+points are red and Commissioner Adjustment is green, and the Backup QB name + Backup QB "
        "Points total + Commissioner Status pill all track confidence together (yellow while likely, green once confirmed)."
    )

    # ---- Live QB-injury-backup credit folded into the standings totals,
    # plus the manager-name "*" + tooltip for the still-"likely" case ------
    # This is the actual feature: the log table above is purely
    # informational, but applyQbAdjustmentsToScores (called from loadLive,
    # BEFORE computeRumblesForWeek) now also folds the credit into the live
    # Actual/Projected totals themselves, which is what the
    # expected/expected_points_this_week dicts and the Alex-vs-Joe winner
    # checks above already exercised. This section checks the other half:
    # the "*" UI cue itself, and that it appears for the "likely" case
    # (Alex) but NOT the "confirmed" one (Ankit) or anyone else.
    for manager in ["Aidan", "Ben", "Jake", "Rohaan", "Joe", "Steven", "Ankit", "Christian", "Ryan", "Kaitlyn", "Stephanie"]:
        el = get_qb_adj_asterisk(page, manager)
        assert el is None, f"expected no manager-name '*' for {manager} (no live 'likely' QB adjustment for them), but found one"
    alex_asterisk = get_qb_adj_asterisk(page, "Alex")
    assert alex_asterisk is not None, "expected Alex (roster 6) to have a manager-name marker -- his live QB adjustment is still 'likely', not yet commissioner-confirmed"
    assert alex_asterisk.inner_text() == "QB Inj*", f"expected the marker text to read 'QB Inj*' (not a bare '*'), got: {alex_asterisk.inner_text()!r}"
    print("Confirmed the manager-name 'QB Inj*' marker shows up ONLY for Alex (the one live 'likely' adjustment) -- not for Ankit (already 'confirmed'), nor any other manager.")

    tooltip_text = get_qb_adj_tooltip_text(page, "Alex")
    assert tooltip_text is not None, "expected hovering Alex's '*' to show the #qb-adj-tooltip"
    assert "Kyler Murray was injured in-game and ruled out this week" in tooltip_text, (
        f"expected the tooltip's first line to plainly state the injured QB was injured in-game and ruled out, got: {tooltip_text!r}"
    )
    assert "Carson Wentz" in tooltip_text, f"expected the tooltip to name the replacement/backup QB (Carson Wentz), got: {tooltip_text!r}"
    assert "came in" in tooltip_text and "credited to this roster" in tooltip_text, (
        f"expected a line stating the replacement QB(s) came in and their points will be credited to this roster, got: {tooltip_text!r}"
    )
    assert "+21.90" in tooltip_text, f"expected the tooltip to state the exact points added (+21.90), got: {tooltip_text!r}"
    assert "pending the commissioner" in tooltip_text.lower(), f"expected a final line noting this is pending the commissioner's official adjustment, got: {tooltip_text!r}"
    for banned in ("IR", "PUP", "house rule"):
        assert banned not in tooltip_text, f"expected the tooltip wording to make no mention of {banned!r} (no injury-status jargon, no house-rule language), got: {tooltip_text!r}"
    print(f"Confirmed Alex's '*' tooltip names the injured QB, the replacement QB, and the exact points added, with no IR/PUP/house-rule language: {tooltip_text!r}")

    tooltip_points_color = get_qb_adj_tooltip_points_color(page, "Alex")
    assert tooltip_points_color == replacement_ref, (
        f"expected the tooltip's points-added figure to be yellow ({replacement_ref}), got {tooltip_points_color}"
    )
    print(f"Confirmed the tooltip's points-added figure is colored yellow ({replacement_ref}).")

    # ---- QB-adj tooltip auto-sizes to keep each line on one line ---------
    # Each sentence in buildQbAdjTooltipHtml's output is its own <br>-
    # separated "logical" line -- counting distinct rendered line-box
    # top-coordinates (via a Range over the tooltip's whole contents) and
    # comparing that to the number of <br>-separated segments in its HTML
    # catches the one failure mode that matters here: a logical line
    # wrapping onto two visual lines because max-width was too tight (see
    # .qb-adj-tooltip in rumbles.html). Re-hover manually (rather than
    # reusing get_qb_adj_tooltip_text, which hides it again immediately)
    # so the tooltip is still open to measure.
    alex_el = get_qb_adj_asterisk(page, "Alex")
    alex_el.hover()
    page.wait_for_timeout(150)
    tip_html = page.eval_on_selector("#qb-adj-tooltip", "el => el.innerHTML")
    expected_logical_lines = tip_html.count("<br>") + 1
    rendered_line_count = page.evaluate(
        """() => {
            var tip = document.getElementById('qb-adj-tooltip');
            var range = document.createRange();
            range.selectNodeContents(tip);
            var rects = range.getClientRects();
            var tops = new Set();
            for (var i = 0; i < rects.length; i++) tops.add(Math.round(rects[i].top));
            return tops.size;
        }"""
    )
    assert rendered_line_count == expected_logical_lines, (
        f"expected the tooltip's {expected_logical_lines} logical (<br>-separated) lines to each render as exactly "
        f"one visual line (no mid-sentence wrapping), but measured {rendered_line_count} distinct rendered lines -- "
        f"the tooltip's max-width may be too tight for its content"
    )
    page.mouse.move(0, 0)
    page.wait_for_timeout(150)
    print(f"Confirmed the QB-adj tooltip's {expected_logical_lines} lines each render on their own line (no mid-sentence wrapping).")

    # ---- QB Injury Backup Adjustments table needs no horizontal scroll ---
    # on a real desktop width or a landscape phone -- unlike the wider (10-
    # column) standings table above, which keeps its own scroll-to-see-more
    # behavior by design. Checked at the page's own max content width
    # (980px, see .wrap) and down through a conservative landscape-phone
    # floor (640px) -- if the table's own scroll container ever needs to
    # scroll at any of these, its content is too wide again.
    original_viewport = page.viewport_size
    for width in (1280, 980, 700, 640):
        page.set_viewport_size({"width": width, "height": 800})
        page.wait_for_timeout(50)
        overflow = page.evaluate(
            """() => {
                var table = document.getElementById('qb-adj-table');
                var scroller = table.closest('.table-scroll');
                return { scrollWidth: scroller.scrollWidth, clientWidth: scroller.clientWidth };
            }"""
        )
        assert overflow["scrollWidth"] <= overflow["clientWidth"] + 1, (
            f"expected the QB Injury Backup Adjustments table to fit without horizontal scrolling at {width}px wide, "
            f"but its content ({overflow['scrollWidth']}px) exceeds its container ({overflow['clientWidth']}px)"
        )
    if original_viewport:
        page.set_viewport_size(original_viewport)
    print("Confirmed the QB Injury Backup Adjustments table needs no horizontal scrolling at 1280/980/700/640px wide (desktop through landscape-phone widths).")

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
        "**/v1/league/TESTLEAGUE1/matchups/1": load("matchups_week1.json"),
        "**/stats/nfl/2026/1*": load("stats_week1.json"),
        "**/projections/nfl/2026/1*": load("projections_week1.json"),
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
        "cells => cells.map(c => c.innerText.trim().replace(/\\s*QB Inj\\*$/, \'\').replace(/\\s+[WL]$/, \'\'))",
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

    # ---- Manager-name "*" (live QB-injury adjustment): same tap-to-toggle
    # behavior as the Pts This Week tooltip above -- Alex (roster 6) has a
    # live "likely" adjustment in this same fixture, so his "*" is present.
    qb_asterisk = get_qb_adj_asterisk(page, "Alex")
    assert qb_asterisk is not None, "expected Alex to have a manager-name '*' in this mobile context too"
    qb_tip = page.locator("#qb-adj-tooltip")
    assert not qb_tip.evaluate("el => el.classList.contains('visible')"), "qb-adj tooltip should start out hidden"

    qb_asterisk.hover(force=True)
    page.wait_for_timeout(200)
    assert not qb_tip.evaluate("el => el.classList.contains('visible')"), "a hover-only event must not show the qb-adj tooltip on a touch-primary device"

    qb_asterisk.tap()
    page.wait_for_timeout(200)
    assert qb_tip.evaluate("el => el.classList.contains('visible')"), "tapping the '*' should show the qb-adj tooltip"
    assert "Kyler Murray" in qb_tip.inner_text(), f"expected the injured QB's name in the tooltip, got: {qb_tip.inner_text()!r}"
    print("QB-adj '*': tap #1 correctly showed the tooltip.")

    qb_asterisk.tap()
    page.wait_for_timeout(200)
    assert not qb_tip.evaluate("el => el.classList.contains('visible')"), "tapping the same '*' again should toggle the tooltip closed"
    print("QB-adj '*': tap #2 on the same element correctly toggled it closed.")

    qb_asterisk.tap()
    page.wait_for_timeout(200)
    assert qb_tip.evaluate("el => el.classList.contains('visible')"), "qb-adj tooltip should be showing again after re-tapping"
    page.locator("#subtitle").tap()
    page.wait_for_timeout(200)
    assert not qb_tip.evaluate("el => el.classList.contains('visible')"), "tapping elsewhere on the page should dismiss an open qb-adj tooltip"
    print("QB-adj '*': tapping elsewhere on the page correctly dismissed the open tooltip.")

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


def scenario_mobile_responsive_layout(browser):
    print("\n" + "=" * 70)
    print("SCENARIO 1c: mobile portrait default-visible columns + scroll-to-")
    print("see-more, QB Injury Backup Adjustments one-line layout + full")
    print("table fit (portrait AND landscape)")
    print("=" * 70)
    routes = {
        "**/rumbles_history.json": load("rumbles_history.json"),
        "**/v1/state/nfl": load("state.json"),
        "**/v1/league/TESTLEAGUE1": load("league.json"),
        "**/v1/league/TESTLEAGUE1/matchups/2": load("matchups_week2.json"),
        "**/v1/league/TESTLEAGUE1/matchups/1": load("matchups_week1.json"),
        "**/stats/nfl/2026/1*": load("stats_week1.json"),
        "**/projections/nfl/2026/1*": load("projections_week1.json"),
        "**/stats/nfl/2026/2*": load("stats_week2.json"),
        "**/projections/nfl/2026/2*": load("projections_week2.json"),
        "**/v1/players/nfl": load("players.json"),
        "**/scores/nfl/regular/2026/2": load("scores_week2.json"),
    }

    # ---- Portrait: #, Manager, Rumbles, This Week, Points This Week must
    # all be visible with zero horizontal scrolling -- the whole point of
    # dropping the "This Week" LIVE badge and tightening "Points This
    # Week"'s. Checked at a common, fairly narrow portrait width (390px,
    # same viewport new_mobile_page uses elsewhere in this file).
    console_errors, page_errors = [], []
    page = new_mobile_page(browser, console_errors, page_errors)
    install_routes(page, routes)
    page.goto(PAGE_URL, wait_until="load")
    page.wait_for_timeout(1000)

    layout = page.evaluate("""() => {
        var container = document.querySelector('.table-scroll');
        var cRect = container.getBoundingClientRect();
        var rows = Array.from(document.querySelectorAll('#standings-body tr'));
        var maxRight = 0;
        var overlaps = [];
        rows.forEach(r => {
            var cells = Array.from(r.querySelectorAll('td')).slice(0, 5);
            cells.forEach(c => { maxRight = Math.max(maxRight, c.getBoundingClientRect().right); });
            var pts = r.querySelector('td.thisweek-pts');
            var badge = pts && pts.querySelector('.badge-live');
            if (badge) {
                var range = document.createRange();
                range.selectNodeContents(pts);
                range.setEndBefore(badge);
                var textRect = range.getBoundingClientRect();
                overlaps.push(badge.getBoundingClientRect().left - textRect.right);
            }
        });
        return {
            containerRight: cRect.right,
            scrollLeft: container.scrollLeft,
            maxRightOfFirstFiveCols: maxRight,
            minBadgeGap: overlaps.length ? Math.min.apply(null, overlaps) : null,
        };
    }""")
    assert layout["scrollLeft"] == 0, f"table shouldn't need any initial scroll, got scrollLeft={layout['scrollLeft']}"
    assert layout["maxRightOfFirstFiveCols"] <= layout["containerRight"], (
        f"#, Manager, Rumbles, This Week and Points This Week must all fit within the viewport with no "
        f"horizontal scrolling -- rightmost edge {layout['maxRightOfFirstFiveCols']} exceeds the visible "
        f"container's right edge {layout['containerRight']}"
    )
    assert layout["minBadgeGap"] is not None and layout["minBadgeGap"] > 0, (
        f"the 'Points This Week' LIVE badge must sit closer to the number without actually overlapping it, "
        f"got a minimum gap of {layout['minBadgeGap']}"
    )
    print(
        f"Confirmed #, Manager, Rumbles, This Week and Points This Week are all fully visible with no "
        f"horizontal scroll on a 390px portrait viewport (rightmost edge {layout['maxRightOfFirstFiveCols']:.1f} "
        f"<= {layout['containerRight']:.1f}), and the Points This Week LIVE badge sits close to its number "
        f"(min gap {layout['minBadgeGap']:.1f}px) without overlapping it."
    )

    # "This Week" (Rumbles) must never show a LIVE badge, on any viewport --
    # already covered in depth by scenario_live_blending's desktop-width
    # assertions, but confirmed here too since this is the mobile-specific
    # layout that motivated removing it.
    thisweek_badges = page.locator("#standings-body td.thisweek .badge-live").count()
    assert thisweek_badges == 0, f"'This Week' column must never show a LIVE badge, found {thisweek_badges}"

    # ---- Rumble %, PF, PA, H2H and Vs. Field must stay reachable by
    # scrolling right in portrait (not removed from the table entirely --
    # an earlier version of this page hid them outright at this breakpoint,
    # which was reverted). Confirmed two ways: the scroll-hint is visible
    # (it was deliberately hidden when there was nothing left to scroll
    # to), and the standings table's own scroll container is genuinely
    # wider than its viewport (there's real content past the first five
    # columns to scroll into view), with the Rumble % header still present
    # and actually laid out (not display: none).
    scroll_info = page.evaluate("""() => {
        var container = document.querySelector('.table-scroll');
        var hint = document.querySelector('.scroll-hint');
        var pctHeader = document.querySelector('#standings-table thead th:nth-child(6)');
        return {
            hintVisible: hint ? getComputedStyle(hint).display !== 'none' : false,
            scrollWidth: container.scrollWidth,
            clientWidth: container.clientWidth,
            pctHeaderText: pctHeader ? pctHeader.textContent.trim() : null,
            pctHeaderDisplay: pctHeader ? getComputedStyle(pctHeader).display : null,
        };
    }""")
    assert scroll_info["hintVisible"], "expected the 'Swipe to see more stats →' hint to be visible in portrait now that Rumble %/PF/PA/H2H/Vs. Field are reachable by scrolling again"
    assert scroll_info["scrollWidth"] > scroll_info["clientWidth"], (
        f"expected the standings table to genuinely overflow its container in portrait (scrollWidth "
        f"{scroll_info['scrollWidth']} > clientWidth {scroll_info['clientWidth']}) -- Rumble % onward should "
        f"still be there to scroll to, not removed"
    )
    assert scroll_info["pctHeaderDisplay"] != "none", f"expected the Rumble % header to still be laid out (not display: none) in portrait, got display: {scroll_info['pctHeaderDisplay']!r}"
    assert "RUMBLE" in scroll_info["pctHeaderText"].upper(), f"expected the 6th header to still be Rumble %, got: {scroll_info['pctHeaderText']!r}"
    print(
        "Confirmed Rumble %/PF/PA/H2H/Vs. Field are NOT hidden in portrait -- still reachable by scrolling right, "
        "with the 'Swipe to see more stats →' hint visible again."
    )

    # ---- QB Injury Backup Adjustments table, same portrait viewport: the
    # Injured QB / Backup QB(s) "Name (pts)" text must stay on one line
    # (nowrap), and backups must lay out in a row (not stacked) the same
    # way landscape already does. Unlike landscape/desktop, this table is
    # NOT required to fit without horizontal scrolling in portrait --
    # rumbles.html shrinks it as far as stays legible, but a real player
    # name can still be wider than a ~390px viewport allows even then, so
    # it's left free to scroll the rest of the way (same as the standings
    # table above) rather than chase an unrealistic zero-scroll target at
    # the cost of readability. What IS checked: the table's own
    # .qb-scroll-hint turns on here (it's off at every breakpoint that
    # doesn't need it), and the table never overflows the PAGE itself (it
    # scrolls within its own .table-scroll container, same pattern as the
    # standings table -- it must never force the whole page wider).
    qb_portrait_info = page.evaluate("""() => {
        var table = document.getElementById('qb-adj-table');
        var scroller = table.closest('.table-scroll');
        var hint = document.querySelector('.qb-scroll-hint');
        var bl = document.querySelector('#qb-adj-table .backup-list');
        var span = document.querySelector('#qb-adj-table .backup-list span');
        var injuredCell = document.querySelector('#qb-adj-table td.qb-adj-injured');
        return {
            scrollWidth: scroller.scrollWidth,
            clientWidth: scroller.clientWidth,
            hintVisible: hint ? getComputedStyle(hint).display !== 'none' : false,
            bodyOverflowsViewport: document.body.scrollWidth > window.innerWidth,
            flexDirection: bl ? getComputedStyle(bl).flexDirection : null,
            spanWhiteSpace: span ? getComputedStyle(span).whiteSpace : null,
            injuredWhiteSpace: injuredCell ? getComputedStyle(injuredCell).whiteSpace : null,
        };
    }""")
    assert qb_portrait_info["hintVisible"], "expected the QB Injury Backup Adjustments table's own swipe hint (.qb-scroll-hint) to be visible in portrait"
    assert not qb_portrait_info["bodyOverflowsViewport"], (
        f"the QB Injury Backup Adjustments table must scroll within its own .table-scroll container in portrait, "
        f"not force the whole page wider than the viewport (scrollWidth {qb_portrait_info['scrollWidth']} vs "
        f"clientWidth {qb_portrait_info['clientWidth']} is fine for the table's own scroller; the PAGE itself "
        f"overflowing is not)"
    )
    assert qb_portrait_info["flexDirection"] == "row", (
        f"expected .backup-list to lay out in a row (one line) in portrait too (same as landscape), got "
        f"flex-direction: {qb_portrait_info['flexDirection']!r}"
    )
    assert qb_portrait_info["spanWhiteSpace"] == "nowrap", f"expected a backup QB's 'Name (pts)' text to be nowrap (one line), got white-space: {qb_portrait_info['spanWhiteSpace']!r}"
    assert qb_portrait_info["injuredWhiteSpace"] == "nowrap", f"expected the Injured QB's 'Name (pts)' text to be nowrap (one line), got white-space: {qb_portrait_info['injuredWhiteSpace']!r}"
    print(
        f"Confirmed the QB Injury Backup Adjustments table keeps backups in a row and Injured QB/Backup QB(s) "
        f"text on one line in portrait, with its own swipe hint visible when the (shrunk-as-far-as-legible) "
        f"table content ({qb_portrait_info['scrollWidth']}px) still exceeds the viewport "
        f"({qb_portrait_info['clientWidth']}px), scrolling within its own container rather than the page."
    )

    page.close()
    assert not console_errors, f"console errors found: {console_errors}"
    assert not page_errors, f"page errors found: {page_errors}"

    # ---- Landscape: the QB Injury Backup Adjustments table's backup-list
    # must lay out in a row (not stacked) at this breakpoint, and must not
    # force the page to grow wider than the viewport (no horizontal
    # scrolling introduced).
    console_errors, page_errors = [], []
    context = browser.new_context(viewport={"width": 844, "height": 390}, is_mobile=True, has_touch=True, timezone_id="UTC")
    page = context.new_page()
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    install_routes(page, routes)
    page.goto(PAGE_URL, wait_until="load")
    page.wait_for_timeout(1000)

    landscape_info = page.evaluate("""() => {
        var matches = window.matchMedia('(max-height: 500px) and (orientation: landscape)').matches;
        var bl = document.querySelector('.backup-list');
        var span = document.querySelector('#qb-adj-table .backup-list span');
        var injuredCell = document.querySelector('#qb-adj-table td.qb-adj-injured');
        var table = document.getElementById('qb-adj-table');
        var scroller = table.closest('.table-scroll');
        return {
            mediaMatches: matches,
            flexDirection: bl ? getComputedStyle(bl).flexDirection : null,
            spanWhiteSpace: span ? getComputedStyle(span).whiteSpace : null,
            injuredWhiteSpace: injuredCell ? getComputedStyle(injuredCell).whiteSpace : null,
            bodyOverflowsViewport: document.body.scrollWidth > window.innerWidth,
            qbScrollWidth: scroller.scrollWidth,
            qbClientWidth: scroller.clientWidth,
        };
    }""")
    assert landscape_info["mediaMatches"], "this viewport should match the landscape-mobile media query -- the test setup itself is wrong if not"
    assert landscape_info["flexDirection"] == "row", (
        f"expected .backup-list to lay out in a row (one line) at the landscape-mobile breakpoint, got "
        f"flex-direction: {landscape_info['flexDirection']!r}"
    )
    assert landscape_info["spanWhiteSpace"] == "nowrap", f"expected a backup QB's 'Name (pts)' text to be nowrap (one line) in landscape, got white-space: {landscape_info['spanWhiteSpace']!r}"
    assert landscape_info["injuredWhiteSpace"] == "nowrap", f"expected the Injured QB's 'Name (pts)' text to be nowrap (one line) in landscape, got white-space: {landscape_info['injuredWhiteSpace']!r}"
    assert not landscape_info["bodyOverflowsViewport"], (
        "the QB Injury Backup Adjustments table's one-line backup layout must not introduce horizontal "
        "scrolling on a landscape phone"
    )
    assert landscape_info["qbScrollWidth"] <= landscape_info["qbClientWidth"] + 1, (
        f"expected the QB Injury Backup Adjustments table itself to fit without horizontal scrolling in "
        f"landscape, but its content ({landscape_info['qbScrollWidth']}px) exceeds its container "
        f"({landscape_info['qbClientWidth']}px)"
    )
    print(
        "Confirmed the QB Injury Backup Adjustments table's backup-list lays out in a row (one line) in "
        "landscape mobile orientation, with Injured QB/Backup QB(s) text kept on one line and no horizontal "
        "scrolling introduced."
    )

    page.close()
    context.close()
    assert not console_errors, f"console errors found: {console_errors}"
    assert not page_errors, f"page errors found: {page_errors}"
    print("\nSCENARIO 1c PASSED")


def scenario_howto_tooltip(browser):
    print("\n" + "=" * 70)
    print("SCENARIO 1d: 'How to Use' button, on a real-hover (desktop) device")
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
        "**/v1/league/TESTLEAGUE1/matchups/1": load("matchups_week1.json"),
        "**/stats/nfl/2026/1*": load("stats_week1.json"),
        "**/projections/nfl/2026/1*": load("projections_week1.json"),
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
    print("\nSCENARIO 1d PASSED")


# Mirrors rumbles.html's formatWeekStartLabel exactly (full day name + 12h
# hour, no leading zero, no minutes) so the pregame status text's expected
# value is derived the same way rather than hardcoded a second time.
STATUS_DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def format_week_start_label(ms):
    d = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
    hour12 = d.hour % 12
    if hour12 == 0:
        hour12 = 12
    suffix = "pm" if d.hour >= 12 else "am"
    js_day_index = (d.weekday() + 1) % 7  # Python Monday=0 -> JS Sunday=0
    return f"{STATUS_DAY_NAMES[js_day_index]} {hour12}{suffix}"


def scenario_pregame_week(browser):
    print("\n" + "=" * 70)
    print("SCENARIO 1e: Week 2's matchups are posted but NO game has kicked")
    print("off yet -- pregame status pill + Projected mode must match Actual")
    print("=" * 70)
    console_errors, page_errors = [], []
    page = new_page(browser, console_errors, page_errors)
    routes = {
        "**/rumbles_history.json": load("rumbles_history.json"),  # weeks_completed: [1]
        "**/v1/state/nfl": load("state.json"),  # week 2, regular
        "**/v1/league/TESTLEAGUE1": load("league.json"),
        "**/v1/league/TESTLEAGUE1/matchups/2": load("matchups_week2.json"),  # posted
        "**/v1/league/TESTLEAGUE1/matchups/1": load("matchups_week1.json"),
        "**/stats/nfl/2026/1*": load("stats_week1.json"),
        "**/projections/nfl/2026/1*": load("projections_week1.json"),
        "**/stats/nfl/2026/2*": [],  # nobody's played yet -- no stats rows at all
        "**/projections/nfl/2026/2*": load("projections_week2.json"),  # real pregame projections
        "**/v1/players/nfl": load("players.json"),
        "**/scores/nfl/regular/2026/2": load("scores_week2_pregame.json"),  # every game still pre_game
    }
    install_routes(page, routes)
    page.goto(PAGE_URL, wait_until="load")
    page.wait_for_timeout(1000)

    # ---- Status pill: grey, "Week 2 begins <day> <time>" -- NOT the green
    # "Live" pill, even though matchups are posted for week 2. The expected
    # label uses the EARLIEST game in the pregame fixture (DAL/PHI, a day
    # before the others), proving the page picks the earliest kickoff
    # across the whole week rather than just the first game it happens to
    # see.
    pregame_games = load("scores_week2_pregame.json")
    earliest_start_ms = min(g["start_time"] for g in pregame_games)
    expected_label = format_week_start_label(earliest_start_ms)

    status_text = page.text_content("#status-text")
    print("Status text:", status_text)
    assert status_text == f"Week 2 begins {expected_label}", (
        f"expected the pregame status text 'Week 2 begins {expected_label}', got: {status_text!r}"
    )
    dot_classes = page.eval_on_selector("#status-dot", "el => el.className")
    assert "live" not in dot_classes.split(), f"status dot must NOT be green/'live' pregame, got class={dot_classes!r}"
    print(f"Confirmed the pregame status pill reads 'Week 2 begins {expected_label}' with a grey (non-live) dot.")

    # ---- Actual mode pregame: both "This Week" (Rumbles) and "Points This
    # Week" are repurposed to show last week's real, FINAL numbers instead
    # of the not-yet-started target week's own (0/still-0, a live QB-
    # adjustment commissioner override aside) figures -- "This Week"
    # becomes "Last Week"/last_completed_week_rumbles, "Points This Week"
    # becomes "Pts Last Week"/last_completed_week_points, both headers
    # relabeled to match (render()). This is deliberately UNCONDITIONAL:
    # even Ankit's live "confirmed" QB-adjustment commissioner override --
    # baked directly into matchups_week2.json (custom_points), independent
    # of any stats, and previously the one exception that still showed up
    # in "This Week" pregame -- no longer affects either column here, since
    # both are now sourced purely from last week's history, not the
    # not-yet-started target week at all. (That override still shows up
    # once the week actually kicks off -- see scenario_live_blending's
    # Ankit assertions -- just not before then.)
    header_text_actual = page.text_content("#th-points")
    assert header_text_actual == "Pts Last Week", (
        f"expected the 'Pts This Week' header to read 'Pts Last Week' in Actual mode once the target week hasn't "
        f"kicked off yet, got: {header_text_actual!r}"
    )
    header_text_rumbles = page.text_content("#th-thisweek")
    assert header_text_rumbles == "Last Week", (
        f"expected the 'This Week' header to read 'Last Week' in Actual mode once the target week hasn't kicked "
        f"off yet, got: {header_text_rumbles!r}"
    )
    print("Confirmed the headers read 'Last Week'/'Pts Last Week' in Actual mode pregame.")

    rows_actual = get_table_rows(page)
    assert len(rows_actual) == 12, f"expected 12 rows, got {len(rows_actual)}"
    history = load("rumbles_history.json")
    baseline_by_manager = {s["manager"]: s for s in history["standings"]}
    weekly_wk1 = history["weekly"]["1"]
    roster_id_by_manager = {s["manager"]: s["roster_id"] for s in history["standings"]}
    for r in rows_actual:
        base = baseline_by_manager[r[1]]
        assert r[3] == str(base["last_completed_week_rumbles"]), (
            f"[actual] expected 'Last Week' to show {r[1]}'s real last-week Rumbles total "
            f"({base['last_completed_week_rumbles']}), unconditionally (Ankit's live override included -- it no "
            f"longer applies here at all), got {r[3]!r}"
        )
        # Rohaan's Week-1 QB-adjustment is still "likely" (unconfirmed) --
        # his backup's real 20.00 (Week1 Rohaan Backup QB, asserted in the
        # tooltip below) is folded on TOP of the frozen history baseline,
        # same as the live week's applyQbAdjustmentsToScores already does
        # for an in-progress week's own totals (see loadLastWeekBreakdown's
        # pointsDeltaByRoster). Ben's is already "confirmed", so his
        # baseline already has it baked in server-side (via
        # official_points()'s custom_points) -- no client-side addition on
        # top for him, proving a confirmed adjustment is never double-added.
        last_week_delta = 20.00 if r[1] == "Rohaan" else 0.0
        expected_last_week = f"{base['last_completed_week_points'] + last_week_delta:.2f}"
        assert r[4] == expected_last_week, (
            f"[actual] expected 'Pts Last Week' to show {r[1]}'s real last-week PF ({expected_last_week}), untouched "
            f"by anything in the not-yet-started week (Ankit's live override included), but WITH a still-'likely' "
            f"QB-adjustment's estimated backup points folded in (Rohaan only), got {r[4]!r}"
        )
        assert r[2] == str(base["rumbles"]), f"[actual] {r[1]}: expected cumulative Rumbles {base['rumbles']} unchanged, got {r[2]}"
        assert abs(float(r[6]) - base["pf"]) < 0.01, f"[actual] {r[1]}: expected PF {base['pf']} unchanged pregame, got {r[6]}"
        assert abs(float(r[7]) - base["pa"]) < 0.01, f"[actual] {r[1]}: expected PA {base['pa']} unchanged pregame, got {r[7]}"
        assert r[8] == f"{base['h2h_w']}-{base['h2h_l']}", f"[actual] {r[1]}: expected H2H {base['h2h_w']}-{base['h2h_l']} unchanged pregame, got {r[8]}"
        # "W"/"L" badge (see get_lastweek_h2h_badge below for a dedicated,
        # element-level check) -- cross-checked here too against
        # rumbles_history.json's own week-1 weekly log, straight off the
        # same manager-cell text get_table_rows already strips it from.
        wk1_entry = weekly_wk1.get(str(roster_id_by_manager[r[1]]))
        expected_badge_text = ""
        if wk1_entry and wk1_entry["h2h_win"] is True:
            expected_badge_text = " W"
        elif wk1_entry and wk1_entry["h2h_win"] is False:
            expected_badge_text = " L"
        # (get_table_rows already strips the badge back out of r[1] itself
        # -- this just documents that every manager here has SOME
        # resolvable week-1 result, i.e. expected_badge_text is never ""
        # for this fixture's fully-paired 12-team week.)
        assert expected_badge_text, f"expected a resolvable week-1 H2H result for {r[1]}, got: {wk1_entry}"
    print("Confirmed Actual mode pregame: 'Last Week' shows each roster's real last-completed-week Rumbles total unconditionally (Ankit's override no longer an exception); 'Pts Last Week' shows each roster's real last-completed-week PF plus any still-'likely' QB-adjustment estimate; and every cumulative column matches the Week-1-only history baseline exactly.")

    live_badges_actual = page.locator(".badge-live").count()
    assert live_badges_actual == 0, f"no LIVE badges expected anywhere pregame (Actual mode), found {live_badges_actual}"

    # ---- "Pts Last Week" hover tooltip: shows week 1's real, final
    # per-player breakdown (see loadLastWeekBreakdown in rumbles.html) --
    # the same underlying tooltip mechanism as the live "Points This Week"
    # one, just fed last week's data instead of this week's. Aidan gets a
    # single ordinary player row (proving this isn't just about the
    # QB-injury cases below); Ben and Rohaan each also get a QB-injury
    # replacement row, colored by their OWN confidence tier -- Ben's is
    # "confirmed" (green), Rohaan's is still "likely" (yellow) -- so both
    # colors are exercised in the same scenario. get_thisweek_pts_tooltip's
    # "proj" key holds whichever the tooltip's third column actually shows
    # -- for the LIVE tooltip that's a real Proj number, but here (see
    # thisWeekTooltip's isLastWeek branch) it's the Actual-minus-real-
    # pregame-projection DIFFERENCE instead (signed, "+"/"-" prefix), fed
    # by projections_week1.json (make_fixtures.py) -- see that fixture's
    # own comment for exactly which numbers were chosen and why (it
    # deliberately makes BOTH replacement rows' raw diff sign disagree with
    # their own tier color, to prove the tier color wins -- see the
    # dedicated color assertions further below).
    aidan_last_week_rows = get_thisweek_pts_tooltip(page, "Aidan")
    assert aidan_last_week_rows == [
        {"time": "", "name": "Week1 Aidan Starter", "actual": "16.00", "proj": "+4.00", "live": False},
    ], f"expected Aidan's 'Pts Last Week' tooltip to show his one real week-1 player row (16.00 actual vs 12.00 pregame proj = +4.00), got {aidan_last_week_rows}"
    print("Confirmed Aidan's 'Pts Last Week' tooltip shows a plain, real week-1 player row with its actual-vs-pregame-proj Diff:", aidan_last_week_rows)

    ben_last_week_rows = get_thisweek_pts_tooltip(page, "Ben")
    assert ben_last_week_rows == [
        {"time": "", "name": "Test Injured QB", "actual": "10.00", "proj": "-4.00", "live": False},
        {"time": "", "name": "Test Backup QB", "actual": "15.00", "proj": "-5.00", "live": False},
    ], f"expected Ben's 'Pts Last Week' tooltip to show his own injured starter's real points plus a replacement row for the confirmed backup, each with its own Diff, got {ben_last_week_rows}"
    print("Confirmed Ben's 'Pts Last Week' tooltip shows his injured starter plus a 'confirmed' replacement row, with Diff values:", ben_last_week_rows)

    rohaan_last_week_rows = get_thisweek_pts_tooltip(page, "Rohaan")
    assert rohaan_last_week_rows == [
        {"time": "", "name": "Week1 Rohaan Injured QB", "actual": "7.00", "proj": "-2.00", "live": False},
        {"time": "", "name": "Week1 Rohaan Backup QB", "actual": "20.00", "proj": "+10.00", "live": False},
    ], f"expected Rohaan's 'Pts Last Week' tooltip to show his own injured starter's real points plus a replacement row for the still-likely backup, each with its own Diff, got {rohaan_last_week_rows}"
    print("Confirmed Rohaan's 'Pts Last Week' tooltip shows his injured starter plus a 'likely' replacement row, with Diff values:", rohaan_last_week_rows)

    # Diff header + coloring: header says "Diff" (not "Proj") for this
    # tooltip; a PLAIN row's Diff cell is colored by its own sign (green
    # positive, red negative -- Aidan's +4.00 green, both injured-QB rows'
    # negative diffs red), while EACH replacement row keeps its own tier
    # color regardless of what its diff's sign would otherwise call for
    # (Ben's confirmed backup: -5.00 but still GREEN; Rohaan's likely
    # backup: +10.00 but still YELLOW) -- proving the tier color always
    # wins over the diff's own sign, per renderPtsTooltipContent.
    diff_header = page.evaluate("() => { var th = document.querySelectorAll('#pts-tooltip thead th'); return th[th.length - 1].textContent.trim(); }")
    assert diff_header == "Diff", f"expected the 'Pts Last Week' tooltip's second column header to read 'Diff', got: {diff_header!r}"
    print("Confirmed the 'Pts Last Week' tooltip's column header reads 'Diff' (not 'Proj').")

    good_ref_diff = get_css_var_color(page, "good")
    bad_ref_diff = get_css_var_color(page, "bad")
    replacement_ref_diff = get_css_var_color(page, "replacement")

    def get_last_week_diff_color(manager, player_name):
        idx = page.eval_on_selector_all(
            "#standings-body tr td.manager",
            "cells => cells.map(c => c.innerText.trim().replace(/\\s*QB Inj\\*$/, '').replace(/\\s+[WL]$/, ''))",
        ).index(manager)
        cell = page.locator("#standings-body tr").nth(idx).locator("td.thisweek-pts")
        cell.hover()
        page.wait_for_timeout(150)
        color = page.evaluate(
            """(name) => {
                var trs = document.querySelectorAll('#pts-tooltip tbody tr');
                for (var tr of trs) {
                    var tds = tr.querySelectorAll('td');
                    if (tds[0].innerText.trim() === name) {
                        return getComputedStyle(tds[tds.length - 1]).color;
                    }
                }
                return null;
            }""",
            player_name,
        )
        page.mouse.move(0, 0)
        page.wait_for_timeout(150)
        return color

    assert get_last_week_diff_color("Aidan", "Week1 Aidan Starter") == good_ref_diff, "expected Aidan's plain +4.00 Diff cell to be green"
    assert get_last_week_diff_color("Ben", "Test Injured QB") == bad_ref_diff, "expected Ben's plain -4.00 Diff cell (his own injured starter, not the replacement) to be red"
    assert get_last_week_diff_color("Ben", "Test Backup QB") == good_ref_diff, "expected Ben's CONFIRMED replacement row's Diff cell to stay green (tier color), even though its raw diff (-5.00) would otherwise be red"
    assert get_last_week_diff_color("Rohaan", "Week1 Rohaan Injured QB") == bad_ref_diff, "expected Rohaan's plain -2.00 Diff cell (his own injured starter, not the replacement) to be red"
    assert get_last_week_diff_color("Rohaan", "Week1 Rohaan Backup QB") == replacement_ref_diff, "expected Rohaan's LIKELY replacement row's Diff cell to stay yellow (tier color), even though its raw diff (+10.00) would otherwise be green"
    print("Confirmed Diff-cell coloring: plain rows follow their own sign (green/red), while both replacement rows keep their own tier color regardless of their diff's sign.")

    # Ben's replacement row (Test Backup QB, "confirmed") must be GREEN
    # (--good) -- the whole point of this round's fix: a confirmed
    # replacement no longer shows the same yellow as an unconfirmed one.
    # Rohaan's (Week1 Rohaan Backup QB, still "likely") must stay the usual
    # yellow (--replacement), unchanged from before. Neither row's own
    # injured-starter row gets any special color (just an ordinary player
    # row) -- only the appended replacement row itself is tier-colored.
    good_ref_lastweek = get_css_var_color(page, "good")
    replacement_ref_lastweek = get_css_var_color(page, "replacement")
    ben_colors = get_tooltip_row_computed_colors(page, "Ben")
    ben_backup_colors = next(r for r in ben_colors if r["name"] == "Test Backup QB")
    assert ben_backup_colors["name_color"] == good_ref_lastweek, f"expected Ben's confirmed replacement row Name to be green ({good_ref_lastweek}), got {ben_backup_colors['name_color']}"
    assert ben_backup_colors["actual_color"] == good_ref_lastweek, f"expected Ben's confirmed replacement row Actual to be green ({good_ref_lastweek}), got {ben_backup_colors['actual_color']}"
    assert ben_backup_colors["proj_color"] == good_ref_lastweek, f"expected Ben's confirmed replacement row Proj to be green ({good_ref_lastweek}), got {ben_backup_colors['proj_color']}"

    rohaan_colors = get_tooltip_row_computed_colors(page, "Rohaan")
    rohaan_backup_colors = next(r for r in rohaan_colors if r["name"] == "Week1 Rohaan Backup QB")
    assert rohaan_backup_colors["name_color"] == replacement_ref_lastweek, f"expected Rohaan's still-likely replacement row Name to be yellow ({replacement_ref_lastweek}), got {rohaan_backup_colors['name_color']}"
    assert rohaan_backup_colors["actual_color"] == replacement_ref_lastweek, f"expected Rohaan's still-likely replacement row Actual to be yellow ({replacement_ref_lastweek}), got {rohaan_backup_colors['actual_color']}"
    assert rohaan_backup_colors["proj_color"] == replacement_ref_lastweek, f"expected Rohaan's still-likely replacement row Proj to be yellow ({replacement_ref_lastweek}), got {rohaan_backup_colors['proj_color']}"
    print(f"Confirmed 'Pts Last Week' replacement-row coloring: Ben's confirmed backup is green ({good_ref_lastweek}), Rohaan's still-likely backup is yellow ({replacement_ref_lastweek}).")

    # ---- Yellow "*" on "Pts Last Week" itself for a still-"likely"
    # QB-injury adjustment (buildCombinedStandings' last_week_qb_adjustment)
    # -- Rohaan's is still "likely" so he gets one; Ben's is already
    # "confirmed" so he must NOT (see render()'s ptsAsteriskHtml). Reuses
    # the exact same qb-adj-asterisk mechanism as the manager-name "QB
    # Inj*" marker (get_qb_adj_asterisk's own pattern), just scoped to the
    # "Pts Last Week" cell and keyed "lw-<roster_id>" instead.
    def get_lastweek_pts_asterisk(page, manager):
        idx = page.eval_on_selector_all(
            "#standings-body tr td.manager",
            "cells => cells.map(c => c.innerText.trim().replace(/\\s*QB Inj\\*$/, '').replace(/\\s+[WL]$/, ''))",
        ).index(manager)
        row = page.locator("#standings-body tr").nth(idx)
        el = row.locator("td.thisweek-pts span.pts-lastweek-yellow")
        return el if el.count() else None

    rohaan_lw_asterisk = get_lastweek_pts_asterisk(page, "Rohaan")
    assert rohaan_lw_asterisk is not None, "expected a yellow '*' on Rohaan's 'Pts Last Week' cell (still-'likely' QB adjustment)"
    assert rohaan_lw_asterisk.inner_text() == "*", f"expected the marker text to read a bare '*', got: {rohaan_lw_asterisk.inner_text()!r}"
    ben_lw_asterisk = get_lastweek_pts_asterisk(page, "Ben")
    assert ben_lw_asterisk is None, "expected NO '*' on Ben's 'Pts Last Week' cell (his QB adjustment is already 'confirmed')"
    print("Confirmed the yellow 'Pts Last Week' '*' shows up ONLY for Rohaan (still-'likely') -- not for Ben (already 'confirmed').")

    # Its color must be yellow (--replacement, matching the tier convention
    # everywhere else), not the manager-name marker's usual blue.
    rohaan_lw_asterisk_color = rohaan_lw_asterisk.evaluate("el => getComputedStyle(el).color")
    assert rohaan_lw_asterisk_color == replacement_ref_lastweek, (
        f"expected the 'Pts Last Week' '*' to be yellow ({replacement_ref_lastweek}), got {rohaan_lw_asterisk_color!r}"
    )
    print(f"Confirmed the 'Pts Last Week' '*' is colored yellow ({replacement_ref_lastweek}), not the manager-name marker's usual blue.")

    # Hovering it shows the SAME #qb-adj-tooltip element (reused wholesale,
    # see render()'s comment), but with "last week"-tensed wording and a
    # "Pts Last Week" points-added line instead of "Actual & Projected".
    rohaan_lw_asterisk.hover()
    page.wait_for_timeout(150)
    lw_tip_text = page.locator("#qb-adj-tooltip").inner_text()
    assert lw_tip_text == (
        "QB injury adjustment (likely)\n"
        "Week1 Rohaan Injured QB was injured in-game and ruled out last week.\n"
        "Week1 Rohaan Backup QB came in; their points were credited to this roster.\n"
        "+20.00 pts added to Pts Last Week.\n"
        "Pending the commissioner’s official adjustment."
    ), f"unexpected 'Pts Last Week' '*' tooltip content: {lw_tip_text!r}"
    page.mouse.move(0, 0)
    page.wait_for_timeout(150)
    print("Confirmed the 'Pts Last Week' '*' tooltip uses last-week-tensed wording ('ruled out last week', 'were credited') and names 'Pts Last Week' in its points-added line, reusing #qb-adj-tooltip.")

    # ---- "W"/"L" badge for last week's real H2H result, next to the
    # manager name -- pregame Actual mode only (buildCombinedStandings'
    # last_week_h2h_win). Kaitlyn (roster 11, the odd/winning half of the
    # 11-vs-12 pair) beat Stephanie in week 1 (see make_fixtures.py's H2H
    # pairing); Ben (roster 2, the even/losing half of the 1-vs-2 pair)
    # lost to Aidan.
    def get_lastweek_h2h_badge(page, manager):
        idx = page.eval_on_selector_all(
            "#standings-body tr td.manager",
            "cells => cells.map(c => c.innerText.trim().replace(/\\s*QB Inj\\*$/, '').replace(/\\s+[WL]$/, ''))",
        ).index(manager)
        row = page.locator("#standings-body tr").nth(idx)
        el = row.locator("span.lastweek-h2h-badge")
        return el if el.count() else None

    good_ref_badge = get_css_var_color(page, "good")
    bad_ref_badge = get_css_var_color(page, "bad")
    kaitlyn_badge = get_lastweek_h2h_badge(page, "Kaitlyn")
    assert kaitlyn_badge is not None and kaitlyn_badge.inner_text() == "W", f"expected Kaitlyn's manager-name badge to read 'W', got {kaitlyn_badge.inner_text() if kaitlyn_badge else None!r}"
    assert kaitlyn_badge.evaluate("el => getComputedStyle(el).color") == good_ref_badge, "expected Kaitlyn's 'W' badge to be green"
    ben_badge = get_lastweek_h2h_badge(page, "Ben")
    assert ben_badge is not None and ben_badge.inner_text() == "L", f"expected Ben's manager-name badge to read 'L', got {ben_badge.inner_text() if ben_badge else None!r}"
    assert ben_badge.evaluate("el => getComputedStyle(el).color") == bad_ref_badge, "expected Ben's 'L' badge to be red"
    print("Confirmed the manager-name 'W'/'L' badge shows last week's real H2H result, colored green/red (Kaitlyn 'W', Ben 'L').")

    # ---- "Pts Last Week" point-folding: a still-"likely" (unconfirmed)
    # QB-injury replacement's estimated points get added into the
    # displayed 'Pts Last Week' total on top of the frozen history
    # baseline (same in spirit as applyQbAdjustmentsToScores already does
    # for the LIVE week), but a "confirmed" one does NOT -- its official
    # custom_points override is already baked into last_completed_week_points
    # server-side (build_rumbles.py's official_points()), so adding it
    # again here would double-count it. Cross-checked directly against
    # the tooltip's own rows fetched above rather than a hardcoded literal,
    # so this fails loudly if the fixture's numbers ever change.
    rohaan_row = next(r for r in rows_actual if r[1] == "Rohaan")
    rohaan_base = baseline_by_manager["Rohaan"]
    rohaan_backup_pts = float(next(r for r in rohaan_last_week_rows if r["name"] == "Week1 Rohaan Backup QB")["actual"])
    expected_rohaan_pts = round(rohaan_base["last_completed_week_points"] + rohaan_backup_pts, 2)
    assert float(rohaan_row[4]) == expected_rohaan_pts, (
        f"expected Rohaan's still-'likely' backup ({rohaan_backup_pts}) to be folded into his displayed 'Pts Last "
        f"Week' ({rohaan_base['last_completed_week_points']} + {rohaan_backup_pts} = {expected_rohaan_pts}), got {rohaan_row[4]!r}"
    )
    print(f"Confirmed Rohaan's still-'likely' backup's {rohaan_backup_pts} real points ARE folded into his displayed 'Pts Last Week' total ({expected_rohaan_pts}).")

    ben_row = next(r for r in rows_actual if r[1] == "Ben")
    ben_base = baseline_by_manager["Ben"]
    assert float(ben_row[4]) == ben_base["last_completed_week_points"], (
        f"expected Ben's already-'confirmed' backup NOT to be added a second time on top of his displayed 'Pts Last "
        f"Week' ({ben_base['last_completed_week_points']}, already official via custom_points server-side), got {ben_row[4]!r}"
    )
    print(f"Confirmed Ben's already-'confirmed' backup's points are NOT double-added to his displayed 'Pts Last Week' total (stays at the official {ben_base['last_completed_week_points']}).")

    # ---- Projected mode: the "Last Week"/"Pts Last Week" relabels AND the
    # value swaps behind them are Actual-mode-only (per the reported spec,
    # mirrored for both columns -- Projected mode's whole point pregame is
    # showing a live/pregame PROJECTED total, never a frozen historical
    # one) -- so Projected mode's own "This Week" (Rumbles) pregame value
    # is UNCHANGED from before this round of fixes: still sourced from the
    # Actual bucket's not-yet-started-week numbers (0 for everyone except
    # Ankit's override-driven 20), which is why it no longer matches Actual
    # mode's own "This Week" column now that Actual's shows last week's
    # real Rumbles instead (Ankit: 7 there, 20 here) -- these two columns
    # are deliberately NOT required to match anymore. Every cumulative
    # column (PF/PA/H2H/Vs. Field) still must NOT be folded pregame either
    # way, and Points This Week still shows the real pregame PROJECTED
    # total (not 0, and different from Actual's), with its tooltip intact.
    page.click("#mode-custom")
    page.wait_for_timeout(200)

    header_text_custom = page.text_content("#th-points")
    # Projected mode's default sort is by this_week_points itself, so the
    # header can carry a trailing sort arrow (" ▼") -- strip it before
    # comparing, same as the label text alone.
    header_label_custom = header_text_custom.replace(" ▲", "").replace(" ▼", "")
    assert header_label_custom == "Pts This Week", (
        f"expected the header to read 'Pts This Week' again in Projected mode (the relabel is Actual-mode-only), "
        f"got: {header_text_custom!r}"
    )
    header_text_thisweek_custom = page.text_content("#th-thisweek")
    assert header_text_thisweek_custom == "This Week", (
        f"expected the 'This Week' header to read 'This Week' again in Projected mode (the relabel is "
        f"Actual-mode-only, same as 'Pts This Week'), got: {header_text_thisweek_custom!r}"
    )
    print("Confirmed both headers revert to 'This Week'/'Pts This Week' in Projected mode pregame.")

    rows_custom = get_table_rows(page)
    assert len(rows_custom) == 12, f"expected 12 rows, got {len(rows_custom)}"

    nonzero_points = 0
    for r in rows_custom:
        if r[1] == "Ankit":
            assert r[3] == "20", f"[custom] expected Ankit's This Week to still be 20 pregame (commissioner override beats every other still-0 roster, unchanged from before this round of fixes), got {r[3]!r}"
        else:
            assert r[3] == "0", f"[custom] expected This Week (Rumbles) to still be 0 pregame for {r[1]} (unchanged from before this round of fixes), got {r[3]!r}"
        if float(r[4]) > 0:
            nonzero_points += 1
        base = baseline_by_manager[r[1]]
        assert r[2] == str(base["rumbles"]), f"[custom] {r[1]}: expected cumulative Rumbles {base['rumbles']} NOT folded pregame, got {r[2]}"
        assert abs(float(r[6]) - base["pf"]) < 0.01, f"[custom] {r[1]}: expected PF {base['pf']} NOT folded with a hypothetical projection pregame, got {r[6]}"
        assert abs(float(r[7]) - base["pa"]) < 0.01, f"[custom] {r[1]}: expected PA {base['pa']} NOT folded pregame, got {r[7]}"
        assert r[8] == f"{base['h2h_w']}-{base['h2h_l']}", f"[custom] {r[1]}: expected H2H {base['h2h_w']}-{base['h2h_l']} NOT folded pregame, got {r[8]}"

    assert nonzero_points >= 10, (
        f"expected most/all managers to show a real nonzero pregame PROJECTED 'Points This Week' total in "
        f"Projected mode (the one thing that's allowed to differ from Actual pregame), only {nonzero_points}/12 were nonzero"
    )
    print(f"Confirmed Projected mode pregame: This Week/Rumbles keeps its OLD (pre-'Last Week'-relabel) Actual-bucket value (including Ankit's override), every cumulative column stays unfolded, and Points This Week shows a real nonzero projected total for {nonzero_points}/12 managers.")

    live_badges_custom = page.locator(".badge-live").count()
    assert live_badges_custom == 0, f"no LIVE badges expected anywhere pregame (Projected mode either), found {live_badges_custom}"

    # A "Points This Week" tooltip should still work pregame (showing
    # projected players), confirming the pregame/live distinction didn't
    # accidentally break the existing tooltip feature.
    idx = page.eval_on_selector_all(
        "#standings-body tr td.manager",
        "cells => cells.map(c => c.innerText.trim().replace(/\\s*QB Inj\\*$/, '').replace(/\\s+[WL]$/, ''))",
    ).index("Aidan")
    cell = page.locator("#standings-body tr").nth(idx).locator("td.thisweek-pts")
    cell.hover()
    page.wait_for_timeout(200)
    tip = page.locator("#pts-tooltip")
    assert tip.evaluate("el => el.classList.contains('visible')"), "Points This Week tooltip should still work pregame"
    assert tip.inner_text().strip(), "pregame tooltip should still have real content (projected players)"
    print("Confirmed the Points This Week tooltip still works pregame, showing projected player content.")

    # ---- QB Injury Backup Adjustments table: Jake's stale, never-
    # confirmed week-1 "possible" row must STILL be showing here (unlike
    # scenario_live_blending, where it's correctly hidden once week 2 has
    # genuinely kicked off) -- week 2's matchups are posted in this
    # fixture but no game has actually started, so per the client-side
    # backstop filter (see renderQbAdjustments), a newer week hasn't
    # "begun" yet and Jake's row is still current. This is also the one
    # real, currently-visible "possible" row across this test suite (the
    # live scenario's own would-be-visible case is always a "likely" or
    # "confirmed" tier), so it's the right place to confirm the Injured QB
    # cell reads BLUE (--accent-2) rather than the usual red (--bad) for
    # this tier -- see the CSS comment on #qb-adj-table td.qb-adj-injured
    # in rumbles.html for why: red is reserved for a corroborated/
    # confirmed injury, and "possible" is specifically the tier where
    # nothing corroborates it yet.
    qb_rows_pregame = get_qb_adjustment_rows(page)
    jake_row = next((r for r in qb_rows_pregame if r["manager"] == "Jake"), None)
    assert jake_row is not None, f"expected Jake's stale week-1 'possible' row to still be showing pregame (week 2 hasn't kicked off yet), got rows: {qb_rows_pregame}"
    assert jake_row["confidence"] == "Possible", f"expected Jake's row to still read 'Possible', got: {jake_row['confidence']!r}"

    accent2_ref = get_css_var_color(page, "accent-2")
    bad_ref_pregame = get_css_var_color(page, "bad")
    row_colors_pregame = get_qb_adjustment_row_colors(page)
    jake_injured_color = row_colors_pregame["Jake"]["injured_color"]
    assert jake_injured_color == accent2_ref, (
        f"expected Jake's 'possible'-tier Injured QB name+points cell to be blue ({accent2_ref}, matching the "
        f"Backup QB(s)/Backup QB Points cells for this tier) rather than the usual red ({bad_ref_pregame}), "
        f"got {jake_injured_color}"
    )
    print(f"Confirmed Jake's stale 'Possible' row is still visible pregame, with its Injured QB cell colored blue ({accent2_ref}) instead of red.")

    page.close()
    assert not console_errors, f"console errors found: {console_errors}"
    assert not page_errors, f"page errors found: {page_errors}"
    print("\nSCENARIO 1e PASSED")


def scenario_pointer_ahead_of_history(browser):
    print("\n" + "=" * 70)
    print("SCENARIO 1f: Sleeper's state.week pointer has advanced AHEAD of")
    print("rumbles_history.json (Week 2 ended, but the daily rewrite hasn't")
    print("run again yet) -- the reported production bug")
    print("=" * 70)
    console_errors, page_errors = [], []
    page = new_page(browser, console_errors, page_errors)
    # Identical to scenario_live_blending's own routes (real, in-progress
    # Week 2 data) EXCEPT for state.json -- state_ahead.json reports
    # Sleeper's own pointer at week 3, while rumbles_history.json (below)
    # still only has weeks_completed: [1]. The OLD targetWeek formula
    # (Math.max(reportedWeek, maxCompleted + 1) = Math.max(3, 2) = 3) would
    # have pointed this page at Week 3 -- which has no matchups posted at
    # all anywhere in these fixtures -- producing a blank/"not live" page
    # instead of Week 2's real, live data. The fix (targetWeek = maxCompleted
    # + 1, full stop, never blended with Sleeper's own pointer) must still
    # land on Week 2 here.
    routes = {
        "**/rumbles_history.json": load("rumbles_history.json"),  # weeks_completed: [1]
        "**/v1/state/nfl": load("state_ahead.json"),  # Sleeper says week 3
        "**/v1/league/TESTLEAGUE1": load("league.json"),
        "**/v1/league/TESTLEAGUE1/matchups/2": load("matchups_week2.json"),
        "**/v1/league/TESTLEAGUE1/matchups/1": load("matchups_week1.json"),
        "**/stats/nfl/2026/1*": load("stats_week1.json"),
        "**/projections/nfl/2026/1*": load("projections_week1.json"),
        "**/stats/nfl/2026/2*": load("stats_week2.json"),
        "**/projections/nfl/2026/2*": load("projections_week2.json"),
        "**/v1/players/nfl": load("players.json"),
        "**/scores/nfl/regular/2026/2": load("scores_week2.json"),
        # Deliberately no route for matchups/3, stats/nfl/2026/3*, etc. --
        # if targetWeek ever regressed back to picking week 3, the page
        # would either hang on those unmocked requests or fall through to
        # block_other and abort them, either way failing the assertions
        # below rather than silently showing week 3 as if it were normal.
    }
    install_routes(page, routes)
    page.goto(PAGE_URL, wait_until="load")
    page.wait_for_timeout(1000)

    status_text = page.text_content("#status-text")
    print("Status text:", status_text)
    assert "Live" in status_text and "Week 2" in status_text, (
        f"expected the page to still target/live-track Week 2 (the real, just-ended week) even though Sleeper's "
        f"own pointer already says Week 3 -- got status: {status_text!r}"
    )
    print("Confirmed the page targets Week 2, not a blank Week 3, despite Sleeper's pointer having advanced ahead of rumbles_history.json.")

    rows = get_table_rows(page)
    assert len(rows) == 12, f"expected the 12 real standings rows (Week 2's live data), not a blank table, got {len(rows)}"

    hist_standings = load("rumbles_history.json")["standings"]
    baseline_rumbles = {s["manager"]: s["rumbles"] for s in hist_standings}
    by_manager = {r[1]: r for r in rows}
    for manager, base_rumbles in baseline_rumbles.items():
        assert by_manager[manager][2] == str(base_rumbles), (
            f"expected {manager}'s cumulative Rumbles ({base_rumbles}, from Week 1 history) to still be intact, "
            f"got {by_manager[manager][2]!r}"
        )
    # Points This Week must be genuinely live (nonzero for at least the
    # rosters with real week-2 actual stats), never the frozen 0 a blank
    # Week 3 would produce for everyone.
    nonzero_points = sum(1 for r in rows if float(r[4].split("\n")[0]) > 0)
    assert nonzero_points >= 4, f"expected several rosters to show real, nonzero live Week 2 points, only {nonzero_points}/12 did"
    print("Confirmed Week 1's cumulative Rumbles are intact and Week 2's live Points This Week are real/nonzero -- this is genuinely Week 2's data, not a blank Week 3.")

    # The QB Injury Backup Adjustments table must show Week 2's freshly-
    # detected live entries (Alex/Kyler Murray, still 'likely'; Ankit,
    # already 'confirmed' via commissioner override) -- if targetWeek had
    # regressed to Week 3, neither of these would ever be detected at all.
    qb_rows = get_qb_adjustment_rows(page)
    qb_by_manager = {r["manager"]: r for r in qb_rows}
    assert "Alex" in qb_by_manager and qb_by_manager["Alex"]["week"] == "2" and qb_by_manager["Alex"]["confidence"] == "Likely", (
        f"expected a live, 'likely' Week 2 QB-adjustment entry for Alex, got: {qb_by_manager.get('Alex')}"
    )
    assert "Ankit" in qb_by_manager and qb_by_manager["Ankit"]["week"] == "2" and qb_by_manager["Ankit"]["confidence"] == "Confirmed", (
        f"expected a live, 'confirmed' Week 2 QB-adjustment entry for Ankit, got: {qb_by_manager.get('Ankit')}"
    )
    print("Confirmed the QB Injury Backup Adjustments table shows Week 2's freshly-detected live entries (Alex 'likely', Ankit 'confirmed').")

    page.close()
    assert not console_errors, f"console errors found: {console_errors}"
    assert not page_errors, f"page errors found: {page_errors}"
    print("\nSCENARIO 1f PASSED")


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
        "**/v1/league/TESTLEAGUE1/matchups/1": load("matchups_week1.json"),
        "**/stats/nfl/2026/1*": load("stats_week1.json"),
        "**/projections/nfl/2026/1*": [],
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

    # ---- "Pts Last Week" Diff column, unavailable case: this scenario's
    # own "**/projections/nfl/2026/1*": [] route (see routes above) is a
    # real, successful, genuinely-empty response -- unlike scenario 1e's
    # real projections_week1.json fixture -- so loadLastWeekBreakdown's
    # projectionsAvailable comes out false and the tooltip's second column
    # must be omitted ENTIRELY (no "Proj" showing stale actual==proj
    # numbers, no "Diff" computed from nothing) rather than showing
    # anything misleading. Aidan's tooltip (a single plain player row, same
    # underlying data as scenario 1e) is the one exercised there for
    # comparison -- here it must render with just Name + Actual, no second
    # numeric column or header cell at all.
    aidan_idx = page.eval_on_selector_all(
        "#standings-body tr td.manager",
        "cells => cells.map(c => c.innerText.trim().replace(/\\s*QB Inj\\*$/, '').replace(/\\s+[WL]$/, ''))",
    ).index("Aidan")
    aidan_cell = page.locator("#standings-body tr").nth(aidan_idx).locator("td.thisweek-pts")
    aidan_cell.hover()
    page.wait_for_timeout(150)
    header_cell_count = page.eval_on_selector_all("#pts-tooltip thead th", "ths => ths.length")
    assert header_cell_count == 2, f"expected exactly TWO header cells (the blank name header + 'Actual' -- Actual mode has no time column, and the Diff header must be omitted here) when projections are unavailable, got {header_cell_count}"
    body_cell_counts = page.eval_on_selector_all("#pts-tooltip tbody tr", "trs => trs.map(tr => tr.querySelectorAll('td').length)")
    assert body_cell_counts and all(c == 2 for c in body_cell_counts), f"expected every row to have exactly 2 cells (Name + Actual, no Diff) when projections are unavailable, got {body_cell_counts}"
    page.mouse.move(0, 0)
    page.wait_for_timeout(150)
    print("Confirmed the 'Pts Last Week' tooltip omits its Diff column entirely (header and every row) when last week's real pregame projections aren't available.")

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
        "**/v1/state/nfl": load("state_lagging.json"),
    }
    install_routes(page, routes)
    # Explicitly mock a 404 for rumbles_history.json rather than leaving it
    # unmocked and relying on "no such file exists at the served project
    # root" -- that used to be true incidentally, but this repo ships a
    # real rumbles_history.json committed at the project root (see
    # build_rumbles.py/the daily workflow), so the very directory this
    # suite serves from (the repo root, per README's `python -m http.server
    # 8123` instructions) now legitimately has one sitting right there. A
    # real 404 route keeps this scenario deterministic regardless of
    # whether that file happens to exist on disk.
    page.route("**/rumbles_history.json", lambda route: route.fulfill(status=404, body="Not Found"))
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
        scenario_mobile_responsive_layout(browser)
        scenario_howto_tooltip(browser)
        scenario_pregame_week(browser)
        scenario_pointer_ahead_of_history(browser)
        scenario_cumulative_only(browser)
        scenario_history_load_failure(browser)
        browser.close()

    print("\nALL SCENARIOS PASSED")


if __name__ == "__main__":
    main()
