# DTF Club — Rumbles Live Standings

A live, gameday-updating standings page for the league's custom "Rumbles"
scoring system, served straight from GitHub Pages. Separate project from
the career head-to-head matrix -- its own repo, its own data, its own
page.

## The Rumbles formula

For each week:

- **+9 Rumbles** for winning your scheduled head-to-head matchup
- **+1 Rumble** for every *other* team in the league you outscore that week

In a 12-team league that's up to **20 Rumbles in a single week** (9 for the
win, 11 for having the top score league-wide). Rumbles accumulate over the
season and reset every year.

This was verified line-by-line against the real "2026 TRUE STANDINGS"
Google Sheet for Week 1 and matched exactly for all 12 managers.

## How it works

`build_rumbles.py` runs on a daily GitHub Actions schedule. For every
fully completed week of the current season, it pulls final scores from
Sleeper, computes each manager's Rumbles/PF/PA/H2H W-L/Vs.-Field W-L, and
commits the cumulative totals to `rumbles_history.json`.

`rumbles.html` is the live page itself. It loads `rumbles_history.json`
for everything already finished, then computes the current, in-progress
week entirely client-side -- polling Sleeper directly every 30 seconds, no
backend involved.

### Commissioner score overrides

`build_rumbles.py` honors Sleeper's `custom_points` field: when the
commissioner manually overrides a roster's score for a week (a house-rule
bonus/penalty, a corrected stat, whatever), that's the OFFICIAL number
Sleeper itself treats as final -- so it's what `build_rumbles.py` uses
for that roster's PF, for their opponent's PA (the opponent's "points
against" reflects the official score too, not the pre-override one), for
who won the H2H matchup, and for the vs.-the-field outscored count that
week -- not just PF in isolation. That last part matters more than it
sounds: an override can change how many OTHER teams a roster outscored
that week (and, in turn, how many teams those other rosters got
outscored by), which feeds directly into Rumbles for everyone involved,
not only the overridden roster. This was confirmed against a real
in-season override (a +19.47 house-rule bonus) and covered by
`test/test_build_rumbles.py`.

This only applies to a week `build_rumbles.py` has already finalized --
a live, in-progress week is scored independently in the browser (see
below) and doesn't read `custom_points` at all, since a mid-week override
on a still-live matchup isn't really a thing Sleeper's own UI supports
either.

### QB injury backup-points adjustment

A custom house rule: if a manager's STARTED quarterback is ruled out
mid-game and a backup QB from the same NFL team comes in and scores, the
manager is credited with the COMBINED points of every QB from that NFL
team who played in that game -- not just their own starter's. This
cascades (a 3rd-string QB coming in after the backup also goes down adds
their points too). `rumbles.html` shows this as a **permanent, running
log** below the main standings -- every time this rule has ever gone into
effect, not just this week -- and it keeps growing live, the same way the
rest of the page does.

Detection works off Sleeper's real data: for each manager's started QB,
look at every OTHER quarterback on that same NFL team (from Sleeper's
player metadata, not just this league's rosters -- confirmed on the real
example below that the backup credited wasn't even on the affected
manager's own roster) and check whether they recorded real action
(`pass_att`/`rush_att`/`gp`) in that week's actual stats. If more than one
team QB played, that's the trigger -- the started QB's own individual
score is "Injured QB Points", and the sum of every OTHER team QB's score
that week is "Backup QB Points".

This log only ever shows an entry under one of two tiers -- deliberately
no vague "might have happened" middle ground:

- **Confirmed** -- the commissioner has already keyed in a matching
  `custom_points` override for that roster/week. The strongest possible
  signal (a human confirmed it), and this **always** produces a log entry
  once an override exists on Sleeper's side -- independent of whether the
  stats-based backup-detection above can actually identify which QB(s)
  account for it. Identifying names/points is best-effort on top of the
  override, never a gate on whether the event gets logged at all -- if no
  backup can be pinned down, the row still shows up with the override
  amount as "Backup QB Points" and no names.
- **Likely** -- no override yet, but a same-team backup QB clearly played
  and scored, AND at the moment this was checked (live, mid-week -- or the
  very first time the nightly `build_rumbles.py` run finalizes that week,
  before the next week's practice reports reset the field) the started
  QB's live `injury_status` read "Out"/"IR"/"PUP". Sleeper has no
  historical "was this player ruled out during this specific past game"
  field -- `injury_status` is a live, current-only snapshot -- so this is
  captured fresh, once, and then permanently carried forward in
  `rumbles_history.json`, never re-derived later from what's by then a
  stale, unrelated snapshot. (`build_rumbles.py` reads its own previous
  output each run specifically to preserve this.)

A same-team backup QB playing with *neither* signal present (no override,
and injury_status wasn't caught as "Out" while it was still fresh) is not
logged at all -- there's no way to tell that apart from an ordinary
blowout benching, and this log is meant to only ever contain confirmed or
well-corroborated cases, not speculation.

Confirmed against the real example that prompted this: league roster_id
6 ("Alex"), Week 1 -- Kyler Murray left hurt, Carson Wentz (a free agent
from Alex's own roster's perspective) came in and scored 19.47 points
under this league's scoring, and the commissioner's `custom_points`
override matches that number exactly. Covered by
`test/test_build_rumbles.py` (the Python/server-side detector -- including
a dedicated regression test for a confirmed override with NO identifiable
backup, proving it still logs rather than silently vanishing) and
`test/run_test.py` (the live, client-side detector in the browser,
including the team-scoping: a same-team QB who didn't play, and a
same-position QB on a *different* team who did, must both be excluded).

### Columns

Rank (`#`), Manager, Rumbles, This Week (Rumbles earned so far this week),
Points This Week (the raw score for this week, to 2 decimal places),
Rumble % (Rumbles earned / max possible so far), PF, PA, H2H, and Vs.
Field. On a narrow screen the table scrolls horizontally (Rank and
Manager stay pinned) rather than squeezing or clipping any column.

Every column, including `#`, is **sortable** -- click a header to sort by
it (numbers/records default to biggest-first, Manager defaults to A-Z;
click `#` to restore/reverse natural standings order; click again on any
column to flip direction; an arrow on the header shows the active sort
and direction). What never changes, no matter which column the table is
currently sorted by, is the actual VALUE in each team's own `#` cell --
that's always their fixed season standing (by cumulative Rumbles, then
PF), assigned once before any sort is applied.

During a live week, each manager's name is also colored whenever they're
one of this week's scheduled H2H matchups -- both sides of a matchup get
the same color text (hover a name to see who they're playing), so you
can tell who's playing whom at a glance even after sorting the table by
something else. No live matchup yet (offseason, or between weeks before
matchups post) just means plain, uncolored names.

These colors are assigned by roster_id pairing, not by table rank --
deliberately, so a manager's color stays exactly the same whether the
Actual or Projected mode is selected (those two modes can produce
different scores and therefore a different rank order, which used to
reshuffle the colors too) and reused as-is by the QB Injury Backup
Adjustments log below, so a given manager's name is the same color
everywhere on the page, not just in the standings table.

The standings table always shows the season's cumulative numbers -- it's
never blank. Outside of a live window (off game days, or the gap between
one week ending and the next one's games starting) it's just
`rumbles_history.json` as-is, no LIVE badge. Once a week goes live, "This
Week" and "Points This Week" refresh every 30 seconds with a LIVE badge no
matter which mode is selected. Whether the in-progress week's numbers get
folded into the season totals -- Rumbles, H2H W-L, PF, PA, and Vs. Field
W-L, all five -- depends on the mode toggle, described next.

### Live scoring: two modes

While a week is in progress, `rumbles.html` fetches every starter's actual
stats and Sleeper's projection for them directly in the browser, on every
30-second poll. Every number is scored by dot-producting a stat line
against the league's real `scoring_settings` -- never Sleeper's generic
`pts_ppr`/`pts_std` fields, which are a *different* scoring system
(standard PPR/standard scoring) that won't match a league with custom
rules like this one's first-down bonuses and tiered defense scoring. This
was verified directly against Sleeper's own displayed matchup projection:
summing a team's starters through this exact formula reproduced Sleeper's
own PRE-GAME number to the penny for every roster tested.

**Once a player has actually played**, the blended total can still be off
by a point or two from what Sleeper's site shows, even hours after the
game with zero live drift happening. A real Week 2 investigation (pulling
that exact roster's real stat/projection payloads and hand-recomputing
the dot product) found the formula itself matches -- it reproduced the
page's own displayed number exactly, key by key -- but it also turned up
two genuine bugs in how a couple of `scoring_settings` keys get matched
against Sleeper's real per-player payloads (both now fixed, see
`KEY_ALIASES` / `TIER_SUM_KEYS` in `rumbles.html`):

- `kr_yd` (kick-return yardage) never had a literal match -- Sleeper's
  real ACTUAL (post-game) stat payloads name that field `def_kr_yd`, not
  `kr_yd`, so every defense's return-yardage credit was being silently
  dropped once they'd played. Fixed via an explicit key alias.
- `fgmiss` is a single flat weight in `scoring_settings`, but Sleeper's
  real ACTUAL payloads only expose missed field goals pre-split by
  distance tier (`fgmiss_30_39`, `fgmiss_40_49`, ...) -- there's never a
  bare `fgmiss` field to match. Fixed by summing every `fgmiss_*` tier
  present and applying the flat weight to that sum.

**Both of those fixes are deliberately gated to real, already-played
stat lines only -- never applied to a pregame projection.** The first
version of this fix applied the `kr_yd` alias everywhere, which
introduced a NEW, worse bug: a still-100%-pregame roster (with a defense
projected to return kicks) started overshooting Sleeper's own number by
~5 points, because a projection's `def_kr_yd` field turned out not to be
a plausible single-week number at all (one real example: `130.97`, when
a real single-game team return total tops out around 60 without a
return TD) -- it's scaled or sourced completely differently than the
same field name in a real post-game box score. So `KEY_ALIASES` and
`TIER_SUM_KEYS` in `rumbles.html` only ever apply when scoring a
player's REAL actual stats, determined per-player (not per-mode) --
even in Projected mode, a player who's already played gets the alias
treatment, while a teammate who hasn't gets none of it, still-frozen
projection and all.

One category is still a **known, unfixed gap**: `fgm_yds_over_30` (bonus
yardage on made field goals past 30) has no reliable source in either
payload -- they only expose a kicker's *total* made-FG yardage, not the
per-kick distance breakdown needed to compute "yards past 30" -- so it's
currently scored as 0. In practice this only shifts a kicker's total by a
small fraction of a point; worth revisiting if kicker scores start
looking consistently light once real games are in.

(Sleeper's own projections for players who *haven't* played yet also get
revised by its providers through the week, independent of any of the
above -- so a snapshot comparison against Sleeper's site for a still-
pregame player will only match exactly at the instant both are captured.)

**A follow-up check confirmed there's nothing further to fix here.**
Re-derived one roster's live total from scratch, independently, and it
matched the page's own number to the penny -- so the formula and the
actual-vs-projection gating are both executing exactly as intended, with
no remaining silent bug. The residual gap against Sleeper's own displayed
number (still up to a couple of points on a roster with many still-
pregame starters) traces to two things outside this page's control: (1)
ordinary pregame-projection drift (documented above), which compounds
with each additional still-pregame starter on a roster, and (2) this
league's first-down bonus categories (`bonus_fd_qb`/`bonus_fd_wr`/
`bonus_fd_rb`/`bonus_fd_te`) are real fields on an ACTUAL post-game stat
line but are never present on a PREGAME projection at all (the projection
provider simply doesn't forecast them) -- every roster's still-pregame
starters are missing that credit equally, so it's not a bug specific to
any one team, but it does mean Sleeper's own displayed "projected" number
may be drawing on some additional internal blending or correction this
page's public API access can't fully replicate. Given that, this is as
close as this approach can get without Sleeper exposing more of its own
internal projection math.

- **Actual** (default on page load) -- ONLY real, actually-banked stats.
  Never touches projections. This is the fully "solidified" view: Rumbles,
  H2H W-L, PF, PA, and Vs. Field W-L are ALL locked to whatever's already
  final in `rumbles_history.json` -- none of them fold in the in-progress
  week's actual-score outcome yet, since it can still flip right up to the
  final whistle (a team's actual PF, for instance, will legitimately sit
  at **0** for anyone who hasn't kicked off, or whose whole roster is
  still pregame -- that's correct, not a bug). "This Week" and "Points
  This Week" keep showing that in-progress figure live regardless, they
  just never get added into the season totals until the week is actually
  over and the workflow finalizes it.
- **Projected** -- matches the live number Sleeper itself shows on its own
  matchup page: once a player's game is underway, their actual performance
  so far replaces their frozen pregame projection; anyone who hasn't
  started yet still uses Sleeper's projection. This mode DOES fold the
  in-progress week's numbers -- Rumbles, H2H W-L, PF, PA, and Vs. Field
  W-L -- on top of the cumulative totals, so you can see where the season
  stands if the week ended right now.

Completed weeks (from `rumbles_history.json`) aren't affected by the
toggle -- it only changes how the live, in-progress week is scored.

(An earlier version of this page also had a third "Generic PPR" / "Sleeper
Projection" mode using Sleeper's precomputed `pts_ppr` field, and a third
"Our Custom Scoring" label for what's now just "Projected". The PPR mode
was dropped: this league isn't PPR-scored, so that number could never
match what Sleeper's own site shows, no matter how precisely it was
displayed -- it was just a different scoring system. What's now called
"Projected" is the one that actually reproduces Sleeper's own number.)

## Files

| File | What it does |
|---|---|
| `build_rumbles.py` | Computes and writes `rumbles_history.json` for every fully completed week of the current season. Standalone -- discovers the league and manager names straight from Sleeper's API each run. |
| `rumbles.html` | The live standings page. Self-contained (no build step, no server) -- just needs to be served as a static file next to `rumbles_history.json`. |
| `rumbles_history.json` | Generated by `build_rumbles.py` -- don't hand-edit it. Doesn't exist until the workflow has run at least once. |
| `.github/workflows/update.yml` | Runs `build_rumbles.py` on a daily schedule and commits `rumbles_history.json` when it changes. |
| `requirements.txt` | Python dependency for `build_rumbles.py` (just `requests`). |
| `test/` | Mocked tests for `rumbles.html` and `build_rumbles.py` (see below) -- optional, not needed to run the site itself. |

## Setup

1. **Create the repo.** Push these files to a new GitHub repo (e.g.
   `dtf-club-rumbles`).
2. **Check the league settings at the top of `build_rumbles.py`:**
   `FALLBACK_LEAGUE_ID`, `FALLBACK_USERNAME`, and `DISPLAY_NAME_OVERRIDES`
   (for anyone whose Sleeper display name isn't what you want shown --
   key it on their REAL Sleeper display_name, exactly, since Sleeper often
   appends digits to a taken username, e.g. "kohagan18" rather than
   "kohagan"; check the league's real `/users` response rather than
   guessing, or the override will silently never match).
3. **Enable GitHub Pages:** repo Settings -> Pages -> Deploy from branch ->
   `main` / root.
4. **Trigger the workflow once manually** (Actions tab -> "Update Rumbles
   standings" -> Run workflow) so `rumbles_history.json` gets created for
   the first time.
5. **Confirm the page loads** at
   `https://<you>.github.io/<repo>/rumbles.html`.
6. **Embed it** in the Google Site: Insert -> Embed -> By URL, pointing at
   that URL, on its own page.

## Testing without live Sleeper access

`test/make_fixtures.py` builds mock Sleeper API responses, and
`test/run_test.py` runs a headless-browser end-to-end test of the real
`rumbles.html` against those mocks, across three scenarios:

1. **A week genuinely live** -- hand-verified PF checks across both
   scoring modes (Actual / Projected), including a fully-pregame
   roster to confirm Actual correctly shows the frozen history PF while
   Projected still shows a real, nonzero folded number, a check that
   every manager gets two genuinely distinct totals (proving the modes
   never bleed into each other), a regression check for the `kr_yd`/
   `fgmiss` key-matching fixes on an already-played stat line (a
   "poison-pill" fixture also plants the same fields on a still-pregame
   projection to prove those fixes correctly do NOT fire there), a check
   that every column -- `#` included -- sorts correctly in both
   directions while each team's own `#` value never changes, a check
   that this week's H2H matchup pairs share a name color (every pair gets
   a distinct one, and the colors stay identical between Actual and
   Projected mode), and a check of the live QB Injury Backup
   Adjustments log -- correctly finds the right backup QB, excludes a
   same-team QB who didn't play and a same-position QB on a different
   team who did, shows the "Likely" confidence tier, confirms a
   commissioner override with NO identifiable backup still logs as
   "Confirmed" (falling back to the override amount) rather than silently
   vanishing, correctly merges with a synthetic historical "Confirmed"
   row from `rumbles_history.json` in the right sort order, and reuses the
   standings table's exact matchup colors for the same managers (by their
   current-week matchup, even for a log row about an older week).
2. **Cumulative-only** -- Week 1 is final in `rumbles_history.json`, but
   Sleeper's own `state.week` pointer hasn't rolled over yet and Week 2's
   matchups aren't posted. The page must show Week 1's cumulative
   standings, never a blank table.
3. **`rumbles_history.json` fails to load** -- the page must show a clear,
   diagnosable message instead of a silent blank table.

`test/test_build_rumbles.py` is a separate, plain-Python unit test (no
browser, no network) covering `build_rumbles.py`'s scoring math directly
-- in particular the `custom_points` commissioner-override handling: that
it's preferred over the plain `points` field when set, and that it
correctly flows through to PF, the opponent's PA, H2H result, and the
vs.-the-field outscored/outscored-by counts. It also covers the QB
injury-backup detector: the dot-product QB scoring, team-scoping (same
scenario as above -- excludes a non-playing same-team QB and a playing
different-team QB), both confidence tiers including the
carry-forward-when-stale behavior, and -- the exact bug this log design
fixes -- that a commissioner override always produces a log entry even
when no backup QB can be independently identified from the stats.

Useful if you ever touch the scoring or live-detection logic and want to
check it without waiting for a live NFL window:

```bash
pip install playwright
python -m playwright install chromium
python test/make_fixtures.py
python -m http.server 8123 &   # serve the repo root
python test/run_test.py
python test/test_build_rumbles.py   # no server/browser needed for this one
```

## Source of truth

For now this is a supplemental gameday view -- keep updating the "2026
TRUE STANDINGS" Google Sheet by hand. Eventually this page is meant to
replace that manual process, but that's a future step.
