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

This league's `roster_positions` has two SUPER_FLEX slots and no dedicated
single QB slot (see "Columns"/the tooltip slot-order section below), so a
manager can -- and regularly does -- start TWO QBs in the same week, not
just one. Every started QB is checked independently for its own backup
situation, so a roster can log more than one adjustment entry in the same
week if more than one of its started QBs has a real same-team backup (each
check also excludes this roster's OTHER started QB from counting as "a
backup" for the other, since starting two QBs from the same NFL team on
purpose is the manager's own lineup choice, not injury relief). This
matters in practice, not just in theory: a manager started both Carson
Wentz (MIN) and Caleb Williams (CHI) the same week, Williams got hurt and
Tyson Bagent (also CHI) came in to relieve him, and it went completely
undetected at first -- the original version of this logic only ever
checked the FIRST started QB found on a roster, which happened to be
Wentz, so Williams (and therefore Bagent) was never even considered.
Fixed by checking every started QB, not just the first one; see
`findStartedQbs`/`find_started_qbs` in `rumbles.html`/`build_rumbles.py`
and the SUPER_FLEX regression tests in `test/test_qb_adj_detection.js` and
`test/test_build_rumbles.py`.

This log shows an entry under one of three tiers:

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
- **Possible** -- no override, and nothing corroborates the started QB
  actually being out (their `injury_status` is missing, or reads something
  short of "Out"/"IR"/"PUP" -- "Questionable", healthy, whatever), but a
  same-team backup QB still recorded real action and points. Added because
  of a real case that slipped through the cracks: Week ?, Caleb Williams
  (Bears) got hurt in-game and Tyler Bagent came in and scored, but
  Sleeper's `injury_status` was never updated to reflect Williams being
  ruled out, so the "Likely" tier's corroboration check never fired and the
  case went completely unlogged. Most of the time a same-team backup
  scoring without a corroborated starter injury is a false alarm -- a
  banged-up starter resting a series, a blowout benching, a spot start --
  and the starter is back the following week with nothing further to see.
  But sometimes, like the Caleb Williams case, it's a real injury that
  Sleeper's status feed simply never caught in time. Rather than silently
  dropping either possibility, "Possible" logs it either way, purely for
  awareness -- see below for why it's never allowed to affect any actual
  point total on its own.

Rows in this log are sorted by confidence tier first -- **Likely** above
**Possible** above **Confirmed** -- then by week (newest first) and manager
name within a tier, rather than by week alone. The idea is to surface
whatever still needs a human's attention (an uncorroborated "Possible", or
a "Likely" still waiting on the commissioner to key in an official
override) above the rows that are already fully resolved ("Confirmed"),
which would otherwise just as easily be buried under a pile of older,
settled entries. See `CONFIDENCE_SORT_RANK` in `rumbles.html`.

A same-team backup QB playing with *no* signal at all -- nobody else on
that NFL team's roster even recorded action -- is, naturally, still not
logged; "Possible" only fires once a real backup QB has actually been
identified. Neither is a backup who DID record action but ended up with
EXACTLY 0.00 fantasy points -- a kneel-down, a single incomplete pass,
whatever it was, left no measurable statistical footprint, so listing them
as "the backup" would be misleading (real production cases: Justin Fields
0.00, Joe Flacco 0.00). A NEGATIVE total is different and stays listed --
a pick or a lost fumble is still a real, if bad, outing, unlike a flat
0.00. This is enforced once, inside `findBackupQbs`/`find_backup_qbs`
itself, so it applies everywhere a backup is identified -- the override
branch's candidate-picking heuristic included, not just the plain
"Likely"/"Possible" detection path.

**The unconfirmed-entry lifecycle** -- this is the intended behavior end to
end, so it's worth stating plainly: a "Confirmed" entry (the commissioner
has keyed in an official adjustment) stays in the log for the rest of the
season, full stop. An UNCONFIRMED entry -- "Likely" or "Possible" alike --
is visible from the moment it's first detected through the start of the
FOLLOWING week, and is dropped for good if the commissioner never confirms
it by then. So at any given moment you should never see an unconfirmed
entry from more than one week back, but you SHOULD see every unconfirmed
entry from the week that just started, carried over from whenever it was
first detected.

Two real production bugs in this lifecycle were reported and fixed together
(both were symptoms of the same underlying issue -- see "Sleeper's
`state.week` pointer can also run AHEAD of `rumbles_history.json`" below):
"Likely" entries from a week that had just ended were vanishing instead of
carrying forward into the next week, and stale "Possible" entries from an
already-superseded week were reappearing instead of staying dropped.

On the server (`build_rumbles.py`), a NEW distinction drives the carry-
forward: `determine_fresh_week`'s existing "fresh" flag is a strict
one-shot -- true only on the single run immediately after a week first
appears in `completed_weeks` -- which is the right gate for detecting a
brand-new case (checking `injury_status` etc.) but the WRONG gate for how
long an already-detected "Likely" case should keep being carried forward
across subsequent daily runs. A second flag, `is_most_recent_completed`
(`week == max(completed_weeks)`), stays true for the entire multi-day span
a newer week is live-but-not-yet-completed, and only flips false once an
even-newer week ALSO completes and supersedes it -- that's the real
one-week grace window. A previously-captured "Likely" entry is now carried
forward across daily runs for exactly as long as `is_most_recent_completed`
holds, and stops being carried the moment it no longer does (see
`compute_qb_adjustments_for_week`'s `is_most_recent_completed`/
`carried_by_roster` handling). "Possible" entries were never carried
forward server-side at all (client-side-only, below) -- that part is
unchanged.

That server-side half only takes effect once `build_rumbles.py` actually
runs again and rewrites `rumbles_history.json` -- and the scheduled
workflow that does that only runs once a day (see Setup below), so there's
a real window, potentially most of a day, where the file on disk can still
hold a stale unconfirmed entry from an old week even after the backend fix
has shipped. Rather than make visitors wait on that, `rumbles.html` also
filters its OWN copy of `state.history.qb_adjustments` at render time: an
unconfirmed entry -- "likely" or "possible" alike -- from an older week is
hidden the moment a genuinely newer week is known to the page (either live
right now, or itself already fully completed), computed fresh on every
render straight from the live/history data already loaded, with no
dependency on `build_rumbles.py` having run again. This client-side filter
now covers "likely" as well as "possible" -- it used to be "possible"-only,
which is exactly how a stale week-1 "possible" entry could slip back into
view once the page's own notion of "the current week" got confused (see
below). "Confirmed" rows are never touched by this filter. See the comment
right above `histAdjustments`' filter in `renderQbAdjustments`.

**Sleeper's `state.week` pointer can also run AHEAD of
`rumbles_history.json`, not just lag behind it.** `rumbles.html` picks
which week to live-track as `maxCompleted + 1` -- one past whatever's
already finalized in `rumbles_history.json` -- and used to blend that
against Sleeper's own `state.week` pointer via
`Math.max(reportedWeek, maxCompleted + 1)`, on the assumption that
`reportedWeek` could only ever LAG behind (Sleeper hasn't rolled its own
pointer over yet). In the reported case, Sleeper's pointer had instead
already advanced past the just-ended week -- it flips on its own daily
cadence, independent of whether this page's own once-a-day cron has
actually finalized that week yet -- so `Math.max` picked the higher, WRONG
value: the page jumped straight to a genuinely empty, not-yet-posted week,
completely skipping the week that had just ended but wasn't in
`rumbles_history.json` yet. That's also exactly how both QB-adjustment
symptoms above showed up together: skipping straight past the just-ended
week meant its freshly-detected "Likely" cases were never even looked for,
while the stale week-old "Possible" entry the client-side filter should
have been hiding (because a newer week was live) suddenly looked current
again, since the page thought there was no newer live week at all. The
fix: `targetWeek` is now always `maxCompleted + 1`, full stop -- Sleeper's
own pointer is never blended in or allowed to override it. See
`loadLive`'s `targetWeek` comment in `rumbles.html`.

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
The 0.00-point-backup exclusion and the "first run only" freshness window
are each covered on both sides too: `test/test_qb_adj_detection.js` and
`test/test_build_rumbles.py` both directly test that a backup who played
but scored exactly 0.00 is excluded (while a negative-point backup is
kept), and `test/test_build_rumbles.py` covers `determine_fresh_week`
across a simulated multi-run sequence (a week's first run as completed is
fresh; a later run, with a newer week now current but not yet itself
completed, is not; freshness moves on once that newer week itself
finishes), plus an end-to-end check that an unconfirmed "possible" entry
is genuinely dropped, not just theoretically excluded, once a new week has
started. `test/test_build_rumbles.py` also directly covers the
`is_most_recent_completed` carry-forward window: a previously-captured
"likely" entry IS carried forward while its week is still the most-
recently-completed one, and STOPS being carried forward the moment an even
newer week has also completed and superseded it (the actual reported bug --
"likely" used to be carried forward forever, with no cutoff at all). The
client-side backstop filter has its own dedicated coverage in
`test/run_test.py`'s live scenario too: a stale, never-confirmed "possible"
week-1 entry is deliberately planted straight into the
`rumbles_history.json` fixture (alongside the existing "confirmed"
historical row, which must keep showing) precisely so the fixture can
prove `rumbles.html` hides it once week 2 is live, independent of whatever
`build_rumbles.py` itself would have done with the same data. And
`test/run_test.py`'s dedicated `scenario_pointer_ahead_of_history` (1f)
directly reproduces the real bug report end to end: Sleeper's `state.week`
pointer advanced to week 3 while `rumbles_history.json` still only had week
1 finalized, and the page must still target/show week 2's real live data --
cumulative Rumbles intact, real nonzero live points, and both of week 2's
freshly-detected QB-adjustment entries (Alex "likely", Ankit "confirmed")
showing -- rather than a blank week 3.

#### The credit now also lands in the live standings themselves

Originally, everything above only ever fed a separate, purely
informational log table -- `rosterScore`/`playerPoints` (the functions
behind every live Actual/Projected total on the page) never looked at
`m.points`/`m.custom_points` at all, so the house rule never actually
moved a manager's live PF, Points This Week, Rumbles, H2H, PA, or Vs.
Field until the week finalized and `build_rumbles.py`'s
`official_points()` picked up `custom_points` for good, days later. Per a
follow-up request, the live totals now reflect it in real time too -- but
only for two of the three tiers above:

- **Possible**: never adds anything to either total, ever. This tier
  exists purely so the log table can flag "something happened here worth a
  look" -- since a "Possible" case has no corroboration at all (that's
  exactly what makes it "Possible" and not "Likely"), most of them are a
  routine in-game substitution rather than a real injury, so crediting
  points automatically would be wrong more often than it'd be right. Only
  a commissioner who looks at a "Possible" case and decides it's real (by
  keying in a matching `custom_points` override, the same override that
  produces a "Confirmed" entry for any tier) ever moves its points onto a
  manager's total. No `QB Inj*` marker or tooltip either -- there's
  nothing provisional showing up in the live totals to explain.
- **Likely**: the same-team backup QB's own already-scored points
  (`backup_points_total`) are added onto BOTH the roster's live Actual
  total and its Custom/Projected total, before Rumbles/H2H/PA/Vs. Field
  are computed from them -- so a manager's live standing can actually
  flip on this, not just their log entry (see `test/run_test.py`'s Alex-
  vs-Joe matchup, where the 21.90-point credit is what puts Alex ahead of
  Joe in Projected mode, not just Actual). It's added flat, not projected
  or dampened -- it's real points a real player already scored, so it
  counts in full toward both totals immediately, the same way any other
  player's actual points do. The manager's name in the standings table
  gets a small blue `QB Inj*` marker (hover on desktop, tap on mobile)
  with a tooltip that spells the whole thing out in short, plain-language
  lines: which QB got hurt ("Kyler Murray was injured in-game and ruled
  out this week"), who's covering for them and that their points will be
  credited to this roster, the exact number of points that represents
  (colored yellow -- the same yellow used everywhere else on the page for
  "this page's own estimate, not yet official" -- see below), and a final
  line noting it's pending the commissioner's official adjustment.
  Deliberately no "IR"/"PUP" injury-status jargon and no "house rule"
  language anywhere in it, so it reads clearly to anyone regardless of
  whether they know this league's rule by that name. The same backup
  QB(s) also show up as their own row(s) in that manager's "Points This
  Week" tooltip -- see below.

  If the backup who came in is later ALSO ruled out mid-game (Sleeper's
  live injury_status reads "Out"/"IR"/"PUP" for them too, same signal used
  for the original starter), the tooltip explains the whole chain rather
  than quietly showing an incomplete picture: one more "was also injured
  in-game and ruled out this week" line for that backup, then either who's
  covering next (if a further same-team QB recorded action) or a plain
  closing line saying nobody else is available -- worded differently
  depending on why: "No other QB was available to step in" when the NFL
  team's roster genuinely only lists that many QBs at all (2, per the
  example that prompted this), versus "No other QB has recorded action to
  step in" when more are listed but nobody else has actually played yet.
  This can't reconstruct a true play-by-play order (Sleeper has no
  historical record of who entered a game when), so it reasons from
  which backup(s) currently carry that live "Out"/"IR"/"PUP" status
  themselves (prior relief QBs who also went down) versus which one
  doesn't (presumably whoever's in now) -- see `buildQbAdjTooltipHtml` in
  `rumbles.html`.
- **Confirmed**: once the commissioner sets `custom_points`, the "likely"
  credit (`backup_points_total`) is removed and replaced with the OFFICIAL
  point delta the override represents (`custom_points - points` -- the
  same number the log table's new "Commissioner Adjustment" column shows,
  see below). Per Ben's own framing, this is meant to be an invisible
  accounting swap, not a visible change: since the official delta is
  normally very close to the "likely" estimate it's replacing (the
  override is meant to reflect the same real-world credit), a manager's
  total doesn't visibly jump or fall the instant the commissioner keys it
  in -- it just quietly switches from this page's best-effort guess to the
  *official*, commissioner-set number. The `QB Inj*` marker/tooltip
  disappear once confirmed; the credit itself just changes source,
  seamlessly.

The log table has a **Commissioner Adjustment** column for this exact
comparison: it shows the official `custom_points_delta` once an override
exists (blank -- an em dash -- for a still-"likely" row with none yet),
right next to "Backup QB Points" so the two are easy to eyeball against
each other -- did the commissioner's manual number land close to what this
page detected on its own?

The table is deliberately condensed to fit without horizontal scrolling
on both a real desktop width and a landscape-oriented phone (checked down
to 640px wide) -- unlike the wider, 10-column standings table above,
which keeps its own scroll-to-see-more behavior by design. The Injured
QB's own points are folded into its name cell ("Kyler Murray (7.20)"),
the same "Name (points)" format the Backup QB(s) column already used, so
the table needs one fewer column; its headers are also allowed to wrap
onto two lines instead of forcing extra column width just to keep a long
word like "COMMISSIONER ADJUSTMENT" on one line.

Row text in this log table is colored to make the two "sides" of each
adjustment easy to tell apart at a glance: the Injured QB's name and
points are red (`--bad`) for a "likely"/"confirmed" row, but blue
(`--accent-2` -- the same blue used everywhere else on the page for
"flagged for awareness only, not corroborated") for a "possible" row
specifically, since red is meant to signal an actual, corroborated
injury and a "possible" row's whole point is that nothing corroborates
one yet -- reusing red there read as more certain than the tier itself
claims to be. The Commissioner Adjustment column is always green, and
every other plain-text cell (Week, Commissioner Status) matches that
row's own manager color (the same color used for the Manager cell and
reused from the standings table above) -- regardless of scoring mode or
which week the row is about.

The **Backup QB(s)** name, the **Backup QB Points** total, and the
**Commissioner Status** pill (the last column: "Possible", "Likely", or
"Confirmed") all track confidence state together, in the same color,
rather than the backup name being a fixed color independent of the rest
of the row: blue (`--accent-2` -- the same blue already used for the `QB
Inj*` marker itself) for a still-uncorroborated "possible" row, this
page's own yellow (`--replacement` -- the same yellow used for the
manager-marker tooltip's points figure and the replacement row in the
"Points This Week" tooltip below) once it's "likely" and there's no
commissioner adjustment yet to confirm it, then green (matching
Commissioner Adjustment) once "confirmed" -- blue meaning "flagged for
awareness only, not counted anywhere yet", yellow meaning "counted
provisionally", green meaning "counted, and official." This table
deliberately carries no LIVE badge at all (unlike the standings table's
"This Week"/"Points This Week" cells, described below) -- the confidence
pill's own label already says everything a badge would.

This only ever touches the in-progress week's own figures ("Points This
Week" in both modes, and every season-cumulative column in Projected
mode) -- Actual mode's season-cumulative PF/Rumbles/H2H/PA/Vs. Field stay
frozen to whatever's already final in `rumbles_history.json`, exactly as
before, since Actual mode never folds the in-progress week into those at
all (see "Columns" below).

Covered end-to-end by `test/run_test.py`'s live scenario, which reuses its
existing Alex/Kyler-Murray/Carson-Wentz ("likely") and Ankit/Ben
("confirmed") fixtures: hand-verified PF/Points-This-Week totals with the
credit folded in, the `QB Inj*` marker appearing only for Alex (never
Ankit or anyone else) with the correct tooltip content -- including that
the injured-QB line reads "was injured in-game and ruled out this week," a
line naming the backup(s) as having "came in" and crediting this roster,
the points-added figure colored yellow, a closing line noting it's pending
the commissioner's official adjustment, and a regression check that the
wording makes no mention of "IR", "PUP", or "house rule" -- the
"Commissioner Status" header text (checked via `textContent`, not
`innerText`, since the header's CSS `text-transform: uppercase` would
otherwise make a case-sensitive `innerText` comparison fail even though
the underlying markup is correct), the Commissioner Adjustment column's
values (including the blank/em-dash case for Alex's still-"likely" row),
the row text-coloring scheme (red for the Injured QB, green for
Commissioner Adjustment, resolved live via `getComputedStyle` against the
actual `--good`/`--bad` CSS variables rather than a hardcoded hex, since
those differ between light and dark mode), the Backup QB name, Backup QB
Points total, and Commissioner Status pill all tracking confidence
together (yellow for Alex's still-"likely" row -- name AND total both --
green for Ankit's and Ben's "confirmed" ones, against `--replacement`),
Carson Wentz's replacement row showing up in the
"Points This Week" tooltip in BOTH Actual and Projected mode (same
slot-appended position, same actual/proj values as computed for any other
player) colored in that same distinct `--replacement` yellow rather than
the usual live-green -- even while his own game is genuinely still in
progress -- and, on the touch-primary mobile context, the same
tap-to-show/tap-to-toggle/tap-elsewhere-dismisses behavior already
established for the "Pts This Week" tooltip. The same live scenario also
measures, directly: that the log table's `.table-scroll` container never
needs to scroll horizontally at 1280/980/700/640px wide (a real desktop
width down through a conservative landscape-phone floor), and that the
manager-marker tooltip's own `<br>`-separated lines each render as
exactly one visual line at its default width (via a `Range` over the
tooltip's contents, comparing distinct rendered line-box positions against
the number of logical lines in its HTML) -- catching a regression to
mid-sentence wrapping either way, not just eyeballing a screenshot.

The chain scenario -- a replacement QB who's ALSO ruled out mid-game, both
with and without a further backup left to credit -- is unit-tested
separately in `test/test_qb_adj_tooltip.js` (`node
test/test_qb_adj_tooltip.js`, no browser or fixtures needed): it
regex-extracts `buildQbAdjTooltipHtml` and its small helpers straight out
of `rumbles.html`'s real source (the same technique
`test/test_blended_projection.js` already used for the live-blending math)
and exercises it directly against hand-built `backup_qbs`/`injury_status`/
`team_qb_count` inputs, covering: a single healthy backup (no chain, and a
regression guard shared with the Playwright scenario above), multiple
healthy backups (pluralized wording), no identifiable backup at all (the
existing fallback wording), a backup who's also hurt with a further backup
correctly named as having "came in" (including the pluralized case), a
2-QB-deep team where that backup is now the last one and nobody else is
available, a deeper team where nobody else has recorded action yet
(distinct wording from the "nobody else available" case), and that names
get HTML-escaped the same as everywhere else on the page.

The "possible" tier's detection and scoring logic (`detectQbAdjustments
ForWeek` picking "possible" over "likely" when nothing corroborates the
starter being out, and `applyQbAdjustmentsToScores` treating "possible" as
a strict no-op on both totals and on the marker/tooltip map) is unit-tested
the same way, in `test/test_qb_adj_detection.js` (`node
test/test_qb_adj_detection.js`): a null `injury_status` and a real
non-out one ("Questionable") both correctly yield "possible" rather than
"likely" or nothing at all; "Out"/"IR"/"PUP" still correctly yields
"likely" (regression guard); a would-be-"likely" case falls back to
"possible" rather than silently disappearing when the freshness check
itself fails; a "possible" entry leaves both the Actual and Projected
totals completely untouched and never populates the manager-marker map
(while an otherwise-identical "likely" entry still does, confirming the
new three-way branch didn't regress the existing behavior); and a
commissioner override still always wins as "confirmed" regardless of what
the stats-only heuristic would have said. `test/test_build_rumbles.py`
covers the equivalent server-side logic in `compute_qb_adjustments_for_
week` (the historical/nightly-build detector), including that an
unconfirmed "possible" entry is deliberately NOT carried forward at all
once its week is no longer the freshest one being checked -- "likely" IS
carried forward, but only for the one-week grace window described above
(`is_most_recent_completed`), never indefinitely -- so neither an
unconfirmed "possible" nor an unconfirmed "likely" that nobody ever
confirmed can accumulate permanently in history, matching their shared
purpose as a live-awareness signal rather than a permanent record.

The SUPER_FLEX/multi-started-QB fix (every started QB checked
independently, not just the first one found) has its own dedicated
regression tests in both `test/test_qb_adj_detection.js` and
`test/test_build_rumbles.py`, built around the exact real scenario that
caught the bug (Carson Wentz + Caleb Williams both started at once, Wentz
listed first): a roster where only the SECOND started QB has a real
backup correctly produces exactly one entry about THAT QB (not the first
one, and not zero entries); a roster where BOTH started QBs independently
qualify produces two separate entries in the same week, each with its own
correctly-scoped backup (never crossing over to the other QB's team); and
a roster that deliberately starts two QBs from the SAME NFL team (not an
injury situation at all) correctly logs nothing, proving the other started
QB is excluded from counting as "a backup" for its counterpart.

### Columns

Rank (`#`), Manager, Rumbles, This Week (Rumbles earned so far this week),
Points This Week (the raw score for this week, to 2 decimal places),
Rumble % (Rumbles earned / max possible so far), PF, PA, H2H, and Vs.
Field. On a narrow screen the table scrolls horizontally (Rank and
Manager stay pinned) rather than squeezing or clipping any column -- the
first five columns (#, Manager, Rumbles, This Week, Points This Week) are
specifically sized to all fit on a portrait phone with no scrolling
needed at all; only Rumble % onward requires scrolling right. See "Mobile
layout" below.

Every column, including `#`, is **sortable** -- click a header to sort by
it (numbers/records default to biggest-first, Manager defaults to A-Z;
click `#` to restore/reverse natural standings order; click again on any
column to flip direction; an arrow on the header shows the active sort
and direction). What never changes, no matter which column the table is
currently sorted by, is the actual VALUE in each team's own `#` cell --
that's always their fixed season standing (by cumulative Rumbles, then
PF), assigned once before any sort is applied.

**The default sort (before you've clicked any header yourself) depends on
which mode is selected.** Actual mode defaults to natural standings order
(same as clicking `#`) -- the season-cumulative ranking is the point of
that tab. Projected mode defaults to **Points This Week, highest first**
instead -- Projected is the tab for watching a live week unfold, and
who's actually having the best week right now is more useful there than
season rank. Switching modes only re-applies that mode's default sort
until you manually sort by clicking a header yourself; the instant you do,
your choice sticks and is preserved across mode switches from then on (a
sort chosen in one mode isn't reset when you flip to the other and back).
See `DEFAULT_SORT_BY_MODE` / `state.userSorted` in `rumbles.html`.

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

The 6 matchup colors (and the "live game" green reused from one of them,
and the per-timeslot kickoff-time coloring described below) were all
chosen to also be clearly distinguishable from the page's own fixed
colors -- the default blue used for "This Week"/"Points This Week", the
orange used for "Rumbles" figures, and the red used for negative/error
states -- not just from each other. In particular, the color that used to
sit in matchup slot 1 was a cyan that read as barely more than a lighter
shade of the default blue at a glance; it's now a true purple instead.

Whichever manager is currently AHEAD in this week's live H2H matchup --
under whichever scoring mode (Actual or Projected) is currently
selected -- gets their own "This Week" (Rumbles earned so far) AND
"Points This Week" values colored to match their manager-name color, in
BOTH modes -- an at-a-glance "who's winning this matchup right now"
signal alongside the name coloring above. This is purely about the two
live, per-week figures; it's independent of whether Actual mode's
season-cumulative totals (PF/PA/H2H/Vs. Field, all frozen until the week
is finalized) reflect this week's outcome yet or not. The trailing
manager in that matchup just keeps the default color for both cells.

A team's "Points This Week" cell shows a per-starter breakdown, rendered as
a small table (blank header cells over the kickoff-time/name columns in
Actual mode, "Actual"/"Proj" labels over the two score columns, then one
row per player -- Projected mode's header row is described separately
below) during a live week -- hover it on desktop, or tap it on mobile (a native
`title`-attribute tooltip never appears on tap at all, so this is a custom
element instead -- see `#pts-tooltip`/`.pts-tooltip` in `rumbles.html` --
driven by real hover where a device has one, and tap-to-toggle (tap again,
or tap elsewhere, to dismiss) where it doesn't; which one a given device
gets is decided once via `matchMedia("(hover: hover) and (pointer: fine)")`).
The tooltip's box always shrinks/grows to fit whatever it's showing (never
wider than it needs to be), and no player's name is ever ellipsis-truncated
to make it fit. Every row always shows both a player's actual AND projected
score, regardless of mode -- what differs by mode is which starters even
qualify to be shown, matching what the Actual/Projected toggle already
means everywhere else on the page:

- **Actual** -- only starters who've completed or are currently in a live
  game; anyone who hasn't started yet is left off entirely (a flat "0.00"
  row for them would just be noise in a view that's explicitly about
  banked, real production). Just a 3-column table here (name, Actual,
  Proj) -- no kickoff-time column at all (see Projected, next).
- **Projected** -- only starters whose game is still developing: not yet
  started, or currently in progress. A starter whose real game has already
  gone FINAL is deliberately left out here (even though Actual mode still
  shows them): once a game's over, their score is fully locked in and
  already baked into the roster total, so re-showing them in a tooltip
  about what's still live or still to come is just clutter. Telling "still
  in progress" apart from "finished" needs more than the stats/projections
  payloads alone can say (a player who's already played looks identical in
  both cases from those alone), so this additionally fetches Sleeper's own
  live-scoreboard feed (`/scores/nfl/{season_type}/{season}/{week}`, a
  small ~16-game payload, not the multi-MB player-stats one) and matches
  each starter's game status (and kickoff time) by their NFL team. If that
  fetch fails, or a player's team isn't in it for some reason (a bye week,
  say), they're treated as NOT complete and kept in the tooltip rather than
  risking hiding someone whose game might still be going. This mode's table
  gains a 4th column, a compact "Mon 1pm"-style kickoff-time label (day +
  local hour, no minutes -- e.g. "1pm", never "1:00pm") -- right-aligned and
  in a smaller/dimmer font, sitting immediately to the left of (and tight
  against) the player's name, so an upcoming or in-progress player's game
  window is visible at a glance without competing with the name for
  attention. Each DISTINCT displayed kickoff label (e.g. "Sun 1pm", "Sun
  4pm", "Thu 8pm") gets its own color, reusing the same matchup-pair color
  set (assigned chronologically -- the earliest kickoff gets the first
  color, wrapping around if there are more than 6 distinct times that
  week) -- so it's easy to see at a glance when a manager's players'
  games cluster into the same window versus being spread across the
  slate. This coloring is independent of the win/name/live colors used
  elsewhere on the page -- the same 6-color set is just reused again here
  for a different purpose (see `buildTimeSlotColors` in `rumbles.html`).

  Projected mode's header row also carries one more thing, specifically
  because that mode's whole table is otherwise all-projected numbers with
  no real anchor point: a real-points reference line reading e.g. "29.10:
  Actual Pts" -- the manager's own real Actual-mode total for the week
  right now, fixed and independent of whatever the Projected total shown
  next to the cell says. It takes over the header row's kickoff-time and
  name cells (both otherwise blank), flush against the tooltip's left
  edge so it sits directly above the kickoff-time column's own values
  rather than starting one column in, and is kept short (no manager name
  -- the tooltip is already anchored to one manager's row) so it shares
  that row neatly with the "Actual"/"Proj" labels instead of forcing the
  tooltip wider. Actual mode's own tooltip never shows this line -- its
  headline "Points This Week" figure right next to the cell already IS
  the actual total, so restating it inside the tooltip would be
  redundant (see `actualPointsLabel` in `thisWeekTooltip`/
  `renderPtsTooltipContent`, `rumbles.html`).

Every row, in either mode, is ordered by the player's roster SLOT (Sleeper
Superflex, Superflex, RB, RB, WR/TE flex, WR/TE flex, FLEX, TE, K, DEF), not
by whatever order Sleeper happens to return the starters in -- see
`TOOLTIP_SLOT_ORDER`/`tooltipSlotRank` in `rumbles.html`. A player currently
in a live (in-progress) game gets highlighted with green text on their name,
Actual score, AND Proj score -- reusing `--matchup-4`, one of the existing
matchup-pair colors, rather than introducing a new one just for this. Only
the kickoff-time cell is excluded (it keeps its own per-timeslot color from
above), so the green reads as a clean "this whole row is still live and
updating" signal, including the still-moving projected number, without
competing with the timeslot coloring. (Name+Actual-only used to be the
rule, with Proj deliberately left uncolored on the theory that it wasn't
"real" the way Actual was -- that was changed because an in-progress
player's Proj cell genuinely is live too, recomputing every 30-second poll
right along with Actual, and leaving it the default color made it read as
static/settled when it wasn't.)

When a manager has a "likely" or "confirmed" QB-injury-backup adjustment in
effect (see above) WITH an identified backup, that backup QB also gets
appended as their own row at the END of that manager's "Points This Week"
tooltip -- after their own (already slot-sorted) starters, since a backup
QB isn't really filling one of this roster's own slots, just extra context
on who actually covered for the injured starter. Each row is computed
exactly the same way as any other player's -- same actual/proj math
(including the pace-dampened live blend while their game is still in
progress), same Actual/Projected filtering rules (left out of Actual mode
until they've actually played; left out of Projected mode once their game
goes final) -- so it slots into the tooltip identically to a real starter
in every respect except one: its Name, Actual, and Proj cells are colored
by the adjustment's OWN confidence tier instead of the usual colors,
including instead of the green "live" color a genuinely-in-progress game
would otherwise get (the tier color always wins, so this row reads as "the
injury-backup credit," not as an ordinary live starter, even while its own
game is still going) -- a distinct yellow/gold (`--replacement`) while
still "likely" (not yet commissioner-confirmed), or GREEN (`--good`,
matching the QB Injury Backup Adjustments table's own green for that tier)
once "confirmed". This shows up in both modes, since a backup QB's real
stats are as "actual" as anyone else's, and the exact same tier-based
coloring is reused by the "Pts Last Week" tooltip's own replacement rows
(see the Live scoring section above).

If nobody on a team's roster qualifies for the current mode -- nobody's
played yet in Actual mode, or every starter's game is already final in
Projected mode -- the tooltip is simply absent rather than showing an
empty table.

The standings table always shows the season's cumulative numbers -- it's
never blank. Outside of a live window (off game days, or the gap between
one week ending and the next one's games starting) it's just
`rumbles_history.json` as-is, no LIVE badge. Once a week goes live, "Points
This Week" refreshes every 30 seconds with a LIVE badge no matter which
mode is selected. Whether the in-progress week's numbers get folded into
the season totals -- Rumbles, H2H W-L, PF, PA, and Vs. Field W-L, all five
-- depends on the mode toggle, described next.

**Only "Points This Week" ever shows a LIVE badge -- "This Week" (the
Rumbles-earned column) never does.** Earlier versions of this page put a
LIVE badge on both columns; it was removed from "This Week" entirely,
since a manager's Rumbles-this-week figure moving live is already implied
by "Points This Week" showing LIVE right beside it, and the freed-up
column width is what lets a portrait phone show #, Manager, Rumbles, This
Week and Points This Week all at once with no horizontal scrolling (see
"Mobile layout" below).

**The "Points This Week" LIVE badge is per-manager, not per-week.** Once a
manager's own players are ALL positively confirmed to have finished their
games, that manager's own score for the week is locked in and won't change
again, so their "Points This Week" LIVE badge drops off individually --
even while the week is still live for everyone else. "Positively
confirmed" means Sleeper's live-scoreboard feed actually marked that
player's NFL game `is_over` -- a player on a bye, or one whose team/game
status the feed couldn't resolve (`game_status: "unknown"`), is
deliberately treated as NOT yet finished (fails open, same precedent as
the "Pts This Week" tooltip's own Projected-mode filter), never assumed
done just because nothing says otherwise. This includes any
QB-injury-backup replacement row appended to a roster's breakdown -- a
lingering in-progress or unresolved backup keeps that manager's badge on
too, exactly like a real starter would.

Because that badge can be present for some rosters and absent for others
in the very same "Points This Week" column, the badge is pulled out of the
number's own text flow and pinned to a fixed, reserved strip at the right
edge of the cell (see `td.thisweek-pts`'s `position: relative` + reserved
`padding-right`, and the `.badge-live` override right after it in
rumbles.html) rather than just being appended inline after the digits --
otherwise the number itself would visibly shift left or right depending on
whether that particular row's badge happened to be showing, instead of
lining up column-wide regardless. That reserved strip is intentionally
tight (the badge sits right up against the number, just clear of actually
touching it) rather than the wider gap earlier versions used. "This Week"
carries none of this -- it's a plain centered cell now, since it never
shows a badge.

### Mobile layout

**Portrait.** The standings table is intentionally wide (10 columns) and
scrolls horizontally on narrow screens, with Rank + Manager pinned via
`position: sticky` so you always know which row you're looking at while
scrolling through the rest. On a portrait phone, the five columns anyone
actually needs at a glance -- #, Manager, Rumbles, This Week, and Points
This Week -- are sized to all fit with NO scrolling needed by default;
Rumble %, PF, PA, H2H, and Vs. Field stay reachable, exactly like on a
landscape phone or desktop, by swiping/scrolling the table horizontally
(the `.scroll-hint` -- "Swipe to see more stats →" -- stays visible in
portrait for this reason, not just in landscape). An earlier version of
this page removed those five columns from portrait layout entirely
(`display: none`) rather than leaving them scrollable; that was reverted
-- hiding them outright meant there was no way to check Rumble %/PF/PA/
H2H/Vs. Field at all on a portrait phone, not even by choice, so the
scroll-to-reveal pattern (identical in spirit to how the QB Injury table
itself falls back to scrolling, described below) was restored instead,
while keeping the same five-column default view. Those five default
columns get explicit widths (plus a tighter header font that's allowed to
wrap onto two lines, e.g. "This Week" -> "This" / "Week", instead of
forcing its column wide enough to fit on one line unwrapped) so their
combined width comfortably fits inside a ~390px-wide viewport before any
scrolling; Manager gets an ellipsis fallback for the rare case a name plus
the live " QB Inj*" suffix still doesn't fit. One easy-to-miss detail: the
Manager column's sticky offset (`left: ...`) is hardcoded to match the
Rank column's width, so shrinking Rank's width without also shrinking
Manager's sticky offset to match would silently make Manager overlap the
column beside it -- both are updated together in that media query.

**QB Injury Backup Adjustments table -- one-line names, both orientations.**
The "Injured QB" and "Backup QB(s)" cells (each in "Name (points)" format)
are kept on a single line -- never wrapping mid-name/mid-number -- in BOTH
portrait and landscape phone orientations, and the "Backup QB(s)" column's
normal one-backup-per-line stacking (`.backup-list { flex-direction:
column }`) switches to a row layout (comma-separated) in both orientations
too, rather than landscape only as in an earlier version. In landscape
(`@media (max-height: 500px) and (orientation: landscape)`, the same
breakpoint used elsewhere on the page for landscape-specific compacting)
this comfortably fits with zero horizontal scrolling introduced, since
`#qb-adj-table` stays `width: 100%` there regardless. Portrait
(`@media (max-width: 640px)`) is tighter: even after shrinking the table's
font/padding as far as stays legible (down to a 0.6rem base, 0.54rem
headers, with header text additionally allowed to break mid-word --
`word-break: break-word` -- so a long word like "COMMISSIONER" doesn't
alone force extra column width) and letting long headers wrap onto two
lines, the two nowrap "Name (points)" cells still don't consistently fit a
~356px-wide portrait content area, so this table falls back to its own
horizontal scroll specifically in portrait (`.table-scroll`'s inherent
`overflow-x: auto`, same mechanism the standings table above uses) rather
than accepting illegibly small text just to force a zero-scroll fit. A
dedicated `.qb-scroll-hint` ("Swipe to see the full row →", shown only in
portrait) flags this, mirroring the standings table's own `.scroll-hint`.
Only the table's own container scrolls -- the page itself never gains
horizontal scroll from this. Both the row-layout backups and the nowrap
rule are scoped to `#qb-adj-table` (rather than the bare `.backup-list`/
plain cell selectors, which stay wrap-able at desktop widths) specifically
so the id-prefixed override reliably wins the cascade regardless of
stylesheet order, without affecting the table's own already-established
640-1280px zero-scroll desktop/tablet behavior.

### Status indicator: pregame vs. live

The status dot/text at the top of the page (next to "Refresh now") used to
turn green and read "Live -- Week N in progress" the moment Sleeper posted
a week's matchups -- which is often days before that week's first real
game, since Sleeper routinely posts matchups well ahead of kickoff. That
conflated two genuinely different things: "matchups exist to be scored"
and "a game has actually started." The page now tracks both separately
(`isLive` for the former, a new `hasKickedOff` for the latter -- computed
in `computeWeekKickoffInfo()` from Sleeper's live-scoreboard feed: true if
any game in the week has left `pre_game` status, or, as a fallback, once
the clock has simply passed the earliest known kickoff time) and the
status dot/text now reflect real kickoff, not just matchups being posted:

- **Once the week has genuinely kicked off,** the dot turns green and the
  text reads **"Live: Week N"** (shortened from the old "Live -- Week N in
  progress").
- **Before kickoff** (matchups posted, or not yet, but the first game
  hasn't started), the dot stays grey and the text instead reads
  **"Week N begins &lt;Day&gt; &lt;hour&gt;&lt;am/pm&gt;"** -- e.g. "Week 3
  begins Thursday 8pm" -- computed from the earliest `start_time` across
  that week's games (`formatWeekStartLabel()`). This also covers the
  in-between window after one week ends and before the next week's
  matchups have even posted yet, once Sleeper's own kickoff data for the
  upcoming week is available.
- Offseason and preseason keep their own separate, unchanged messages
  ("Offseason" / "Preseason -- Rumbles start Week 1").

This same `hasKickedOff` signal is also what the QB Injury Backup
Adjustments table's stale-"possible"-entry backstop (see above) now keys
off of, tightened from the coarser "matchups posted" signal it used
before -- so a "Possible" entry from the prior week correctly keeps
showing right up until the new week has actually kicked off, not just
until its matchups appear.

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
a genuine bug in how one `scoring_settings` key gets matched against
Sleeper's real per-player payloads, plus a real fix for a second one
(see `KEY_ALIASES` / `TIER_SUM_KEYS` in `rumbles.html`):

- `fgmiss` is a single flat weight in `scoring_settings`, but Sleeper's
  real ACTUAL payloads only expose missed field goals pre-split by
  distance tier (`fgmiss_30_39`, `fgmiss_40_49`, ...) -- there's never a
  bare `fgmiss` field to match. Fixed by summing every `fgmiss_*` tier
  present and applying the flat weight to that sum. This one's confirmed
  correct and still in place.
- `kr_yd` (kick-return yardage) was a real mistake, now reversed. An
  earlier version of this page aliased it to a real, already-played
  DEFENSE's `def_kr_yd` actual-stats field, on the theory that Sleeper's
  payload just used a different field name for the same category. A
  direct side-by-side check against Sleeper's own displayed matchup page
  (multiple real, live team defenses) found this was flat-out wrong: it
  was inflating every defense's actual score by its opponent's
  return-yardage total that game (0.04/yd, often 3-5+ points) --
  Tampa Bay's real defense showed 8.39 on Sleeper's own site at a moment
  this page, with the alias applied, computed 12.55 for; removing just
  the kick-return credit landed right on 8.27. Green Bay confirmed it
  again the same day (6.41 real vs. 9.97 with the alias vs. 6.25
  without). Sleeper's own scoring engine simply doesn't apply `kr_yd` to
  the team DEF slot at all -- it's an individual return-specialist
  category only (a real return specialist's own actual-stats payload
  already carries a literal `kr_yd` field with no alias needed; the team
  DEFENSE entries' `def_kr_yd` is an unrelated, not-separately-scored
  team stat). The alias has been removed -- `KEY_ALIASES` is now empty,
  kept as a mechanism for a future confirmed case rather than deleted
  outright.

**`TIER_SUM_KEYS` is deliberately gated to real, already-played stat
lines only -- never applied to a pregame projection.** A still-100%-
pregame roster's `fgmiss_*` fields (if a provider's projection payload
ever carried any) are never summed into a phantom `fgmiss` penalty --
that gating is determined per-player (not per-mode), so even in
Projected mode, a player who's already played gets the tier-sum
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

**One category is deliberately left at 0 for a still-pregame player, by
design, not by omission:** this league scores first downs through
POSITION-KEYED `bonus_fd_qb` / `bonus_fd_rb` / `bonus_fd_wr` /
`bonus_fd_te` fields (weighted 0.2/0.5/0.5/0.5), not the generic
`pass_fd` / `rush_fd` / `rec_fd` fields (all weighted 0 in this league --
they're just the raw building blocks the bonus is computed from). A real
ACTUAL (post-game) stat line already carries the correct
`bonus_fd_<position>` field precomputed by Sleeper, so this scores
correctly with no extra work. A real PREGAME projection, however, never
carries any `bonus_fd_*` field at all, only the raw `pass_fd` /
`rush_fd` / `rec_fd` counts, so a plain key-by-key match against
`scoring_settings` naturally scores that category as 0 for a still-
pregame starter.

A gameday report once suggested this was a bug (Projected running well
below Sleeper's own displayed total for a heavily-pregame roster), and an
earlier version of this page tried "fixing" it by deriving
`bonus_fd_<position>` from a still-pregame player's raw
`pass_fd`/`rush_fd`/`rec_fd` counts, the same way a real actual stat line
arrives at its own precomputed value. That derivation was reverted after
a follow-up, side-by-side live comparison against Sleeper's own matchup
page (multiple still-pregame starters, checked individually, mid-Sunday
Week 2): Sleeper's own displayed per-player number matched this page's
plain, undecorated formula exactly, to the penny, for every one of them
-- and the derived-bonus version overshot Sleeper's real number by
2-5+ points per player. So Sleeper's own frontend does not appear to
estimate this bonus for a still-pregame player either, and this page
now matches that behavior deliberately rather than guessing at a number
Sleeper itself doesn't show. (Whatever produced the originally-reported
gap on a live, partially-in-progress roster remains only partly
understood -- Sleeper's displayed number for a player who's *already*
started playing can drift from a pure "actual stats so far" total for
reasons outside this page's control, most likely an updated rest-of-game
estimate blended into their live total rather than the frozen pregame
projection.)

**A player whose game is currently in progress is scored, in Projected
mode, as a PACE-DAMPENED BLEND of their actual-so-far stat line and
their pregame projection** -- not either one alone. The exact formula
(see `blendedProjection()` in `rumbles.html`):

```
pace = actualPointsSoFar / pregameProjectionPoints        (0 if no projection, or 0 actual)
FLOOR, K = (0.90, 1.75) if remainingGameClockFraction > 0.75   -- Q1, see below
         = (0.15, 1.40) if remainingGameClockFraction <= 0.25  -- Q4/OT, see below
         = (0.40, 1.10) otherwise                               -- Q2-Q3 (the original fit)
dampening = FLOOR + (1 - FLOOR) * exp(-K * pace)
points = actualPointsSoFar + remainingGameClockFraction * pregameProjectionPoints * dampening
```

Overtime is a special case even beyond that: `remainingGameClockFraction`
is forced to exactly 0 the instant a game reaches OT (`quarter_num >= 5`),
which collapses the formula above straight to `actualPointsSoFar` --
Sleeper gives no extra projected credit at all once a game is in OT (see
below).

`remainingGameClockFraction` runs from 1 at kickoff down to 0 at the
final whistle (computed from Sleeper's live-scoreboard `quarter_num` /
`time_remaining` fields, treating each quarter as 15 game-clock minutes
-- see `remainingFraction()` in `rumbles.html`). So at kickoff this is
100% pregame projection; at the final whistle it's 100% actual stats.
Once a game is confirmed COMPLETE, only the real stat line counts.

This formula went through three iterations, each one replacing a real,
validated shortcoming of the last:

1. Swap straight from pregame projection to actual-so-far the instant a
   player's game merely *started*. A gameday report showed this dragged
   a roster with several starters mid-game well below where its total
   should sit -- one concrete example, a still-in-progress QB, went from
   a 21-point pregame projection to under 1 real point the instant his
   game kicked off, even though the vast majority of his likely
   production was still ahead of him.
2. Keep the frozen pregame projection for the entire time a game is in
   progress, only swapping to actual once it's confirmed complete. This
   fixed problem 1, but had the opposite, equally real problem: it never
   credited any actual production while a game was live, so a player
   already *outproducing* their projection got under-reported until
   their game ended.
3. A flat clock-weighted blend with no dampening (`points =
   actualPointsSoFar + remainingGameClockFraction * pregameProjectionPoints`,
   i.e. the formula above with `dampening` fixed at 1). This fixed
   problem 2 -- credits actual production immediately -- but a follow-up
   gameday report with more real examples showed it now systematically
   OVERSHOT Sleeper's own live-displayed number, and by MORE the further
   ahead of a flat pace a player was already running. Comparing two
   players in the exact same real game at the exact same clock reading
   made this unmistakable: a receiver already at 78% of his full pregame
   projection early in the 3rd quarter needed his blend pulled down hard,
   while his teammate at only 11% of projection barely needed any
   adjustment at all -- the clock alone wasn't driving the gap, how far
   ahead of pace each player was already running was. That's where the
   `dampening` term above comes from: `0.40 + 0.60*exp(-1.10*pace)` keeps
   ~100% credit for a player with zero production so far, and saturates
   down to ~40% credit for a player already running well ahead of pace
   (their overall total is barely affected by this -- their actual-so-far
   is untouched and is usually the bigger share of their eventual total
   anyway -- it only dampens what's assumed about the game still ahead).

This final formula was fit (least total absolute error) and then
validated against Sleeper's own live-displayed "projected" number,
screenshotted directly off Sleeper's real matchup pages across four
separate live gameday reports, for 30 different real players (a mix of
QB/RB/WR/TE/K), each cross-checked against this page's own live-fetched
actual stats, pregame projection, and the scores feed's
`quarter_num`/`time_remaining` fields at the matching moment:

| Player | Actual so far | Pregame proj | Sleeper's live number | Flat blend (iteration 3) | This formula |
|---|---|---|---|---|---|
| Tucker Kraft (TE) | 0.00 | 12.24 | 7.19 | 7.19 | 7.19 |
| Ka'imi Fairbairn (K) | 0.00 | 9.06 | 4.76 | 4.77 | 4.77 |
| DK Metcalf (WR) | 1.20 | 14.32 | 8.49 | 8.79 | 8.38 |
| Garrett Wilson (WR) | 3.10 | 15.66 | 11.21 | 11.98 | 10.90 |
| Colston Loveland (TE) | 1.30 | 12.30 | 6.56 | 6.88 | 6.50 |
| Quinshon Judkins (RB) | 3.70 | 12.60 | 9.50 | 10.42 | 9.27 |
| Travis Etienne (RB) | 4.40 | 11.98 | 8.45 | 9.53 | 8.47 |
| Aaron Jones (RB) | 5.70 | 14.22 | 10.74 | 12.15 | 10.72 |
| Tee Higgins (WR) | 10.70 | 14.26 | 15.64 | 18.30 | 15.63 |
| D'Andre Swift (RB) | 10.00 | 12.78 | 13.32 | 15.80 | 13.71 |
| Chase McLaughlin (K) | 10.40 | 8.41 | 13.19 | 14.76 | 12.72 |
| Dalton Schultz (TE) | 12.30 | 11.09 | 15.38 | 17.84 | 15.39 |
| DeVonta Smith (WR) | 19.10 | 15.22 | 23.98 | 26.80 | 23.17 |
| Bijan Robinson (RB) | 9.10 | 22.82 | 17.54 | 19.81 | 17.44 |
| Bucky Irving (RB) | 8.90 | 14.06 | 11.38 | 13.29 | 11.92 |
| Christian Watson (WR) | 5.80 | 15.22 | 10.40 | 11.78 | 10.51 |
| Tetairoa McMillan (WR) | 12.80 | 15.04 | 16.67 | 19.86 | 17.17 |
| Juwan Johnson (TE) | 6.00 | 10.32 | 7.71 | 8.93 | 8.07 |
| D'Andre Swift (RB), wk 2 rd 2 | 9.30 | 12.78 | 10.49 | 12.06 | 11.15 |
| Aaron Jones (RB), wk 2 rd 2 | 9.00 | 14.22 | 10.55 | 12.07 | 11.15 |
| Dalton Schultz (TE), wk 2 rd 2 | 12.30 | 11.10 | 13.14 | 15.19 | 13.97 |
| Ja'Marr Chase (WR), wk 2 rd 2 | 25.50 | 17.84 | 27.23 | 30.15 | 27.94 |
| Bryce Young (QB) | 25.10 | 14.66 | 27.32 | 28.46 | 27.24 |
| Drake Maye (QB) | 7.47 | 17.12 | 10.04 | 11.36 | 10.47 |
| Derrick Henry (RB) | 19.20 | 15.28 | 19.61 | 21.44 | 20.43 |
| Woody Marks (RB) | 8.40 | 9.11 | 9.15 | 10.77 | 9.86 |
| Mark Andrews (TE) | 10.00 | 10.85 | 10.34 | 11.59 | 10.98 |
| Colston Loveland (TE), wk 2 rd 2 | 1.30 | 12.30 | 3.74 | 3.96 | 3.78 |
| Jayden Reed (WR) | 1.40 | 12.26 | 4.35 | 4.62 | 4.40 |
| Tyler Loop (K) | 5.90 | 8.01 | 6.38 | 7.16 | 6.74 |

Average miss: 1.42 points for the flat blend (iteration 3), 0.33 points
for the pace-dampened version above -- about 4.3x tighter, and several
players landing within a few hundredths of a point. This isn't
believed to be a coincidence of overfitting: adding 5 new players from a
third gameday report barely moved the fit at all from the one
originally tuned on just the first 13 -- and leave-one-out
cross-validation (refitting the two constants with each player held out
in turn) kept both in a similar range each time, with every held-out
player's prediction still landing within about half a point of what the
full fit predicted. That said, **this is a heuristic fit to real
examples, not a disclosed Sleeper formula** -- nothing about Sleeper's
actual live blend is exposed by the public stats/projections/scores
endpoints this page reads (a live usage/opportunity signal -- snaps,
targets, red-zone role -- almost certainly factors into Sleeper's real
number, and none of that is available here), so this page validates
against real examples rather than guessing at an unverified extra
factor -- exactly the discipline the `bonus_fd_<position>` episode
above was a lesson in.

A fourth gameday report (12 more players, appended to the table above --
the average miss ticked up from ~0.23 to ~0.33 with this batch folded in)
was specifically checked for two things the fit might be missing:
whether the miss tracks how much game clock is left
(`remainingGameClockFraction`) independent of pace, and whether any one
position's miss is consistently worse than the others. Taken by itself,
this batch's errors do correlate fairly strongly with a small
`remainingGameClockFraction` (most of these 12 players were deep in the
3rd or 4th quarter) -- but refitting the two constants (and even trying
a third, extra exponent on `remainingGameClockFraction` to bend that
relationship) against all 30 points together always lands back within a
few hundredths of the current 0.40/1.10, because the original batch has
its own cluster of similarly-sized misses running the other direction
(DeVonta Smith, Garrett Wilson, Chase McLaughlin -- all *undershoot*
Sleeper's real number, and all also happen to have more game clock left
than this new batch). That's a real tension in the data, not something a
two-constant formula can resolve away, so nothing was changed here --
tuning the constants to this one batch would just trade today's misses
for tomorrow's. Position-by-position, the picture is similarly
unsettled: RB is the one group that shows a repeatable, modest
(roughly 15-20%) improvement from its own separately-fit constants, but
even RB only has 10 validated points across all four reports (4 of them
from this latest batch), and every other position has far fewer --
nowhere near enough to responsibly ship a per-position split without
real overfitting risk. Both are worth revisiting once more real examples
accumulate, especially more RB data and more players checked earlier in
their games (larger `remainingGameClockFraction`) to see whether that
clock-correlation in this batch holds up or was this batch's own noise.

**That clock-correlation held up on a follow-up check, and turned out to
be fixable after all -- just not the way a single continuous formula
could do it.** The same four players (Schultz, Jones, Chase, Young) were
re-checked later in the exact same games, with `remainingGameClockFraction`
down to 0.05-0.17 -- and this page was still running 0.3-0.8 points
ahead of Sleeper's real number for every one of them, the same direction
and a similar size as before, even though the absolute remaining-credit
term itself had shrunk a lot by then. An extra exponent on
`remainingGameClockFraction` (so the remaining-credit term shrinks
faster than linearly as the clock runs out, independent of pace) was
tried again across the full validated set and rejected again, same as
before -- it only ever traded accuracy from one part of the game for
another, because it was trying to smoothly interpolate between ranges
that don't share one shape.

The fix came from asking a more specific question: does the miss track
which QUARTER a player is in, not just `remainingGameClockFraction` as
one continuous number? Bucketing every validated point (34 total) by
quarter instead answered it cleanly: Q1-Q3 points had a small, mixed-sign
average miss (well under half a point either way), while all 11 real
Q4/overtime points overshot, every single one, by 0.3-0.8 points each.
That's not a gradient -- it's a step. So Q4/OT now gets its own,
separately-fit constants (`PACE_DAMPENING_FLOOR_Q4` = 0.15,
`PACE_DAMPENING_K_Q4` = 1.40, both more aggressive than the Q1-Q3
values), switched on whenever `remainingGameClockFraction` is 0.25 or
below -- a threshold that, by `remainingFraction()`'s own math, can only
ever be reached in Q4 or OT (Q3 never produces anything below 0.25; OT's
entire range sits inside it), so no extra quarter-number plumbing was
needed to detect it. This dropped the average miss on the 11 validated
Q4/OT points from 0.53 to 0.13 -- about 4x tighter -- while leaving the
Q1-Q3 fit exactly as it was. Leave-one-out cross-validation on the
Q4/OT set held up too: refitting with each point held out kept both
constants in a similar neighborhood each time, and every held-out
prediction landed within about a third of a point of the full fit. This
tracks with how football actually plays late in games -- run-out-the-
clock playcalling once a game is decided, reduced roles, backups
checking in -- game-flow effects tied specifically to how much game is
left, not to how hot a player is running, and ones that apparently kick
in sharply at the Q4 line rather than building up gradually through the
whole game. One visible side effect: because the switch is a hard
threshold rather than a smooth blend, a player's live projection can
take a small, real step right at the Q3/Q4 boundary rather than drifting
continuously -- an accepted trade-off for a fix that's this well-
supported by the data, and arguably a more honest reflection of a real
discontinuity in the underlying football than a smoothed-over curve
would be.

**The same idea applies at the other end of the game too: Q1 needs its
own, much LIGHTER dampening, not the Q1-3 default.** A live report caught
Jaxon Smith-Njigba scoring an 82-yard touchdown on an early target, 11:08
still left in the 1st quarter (`remainingGameClockFraction` 0.9356,
actual 15.20 off a 20.17 pregame projection) -- and this page, still on
the Q1-3 constants, undershot Sleeper's own live "projected" number by
5.7 points (27.69 vs. 33.41). Solving backward for what dampening value
Sleeper's real number implies at that exact pace shows it was barely
dampening at all (~0.97) -- within a third of a point of the fully
UNDAMPENED flat blend (iteration 3 above, the version that had to be
replaced BECAUSE of later-game overshoot). That's the mirror image of
the Q4 finding: the miss isn't really about pace on its own, it's about
how much of the game has actually happened yet to have produced that
pace. A player at 75% of their full-game projection after 3-4 real
minutes of football is an extremely small sample with essentially a
whole game still ahead for a normal share of production to arrive on top
-- there's little reason to discount the rest of their pregame
projection nearly as hard as this page discounts an equally-hot player
deep in the 3rd quarter, who's already had most of a game to prove that
pace out. Q1 now gets its own, much gentler constants, switched on
whenever `remainingGameClockFraction` is above 0.75, a threshold that,
symmetrically to the Q4 one, can only ever be reached in Q1 (Q2 tops out
at exactly 0.75).

**A follow-up report a few minutes later, same gameday, added three more
real points -- including a second read on Smith-Njigba himself -- and
that second read is what actually shaped the final constants.** Jeremiyah
Love (low pace, 0.70 off a 12.83 pregame projection) and Stefon Diggs
(zero actual yet) both came in essentially exact regardless of the exact
constants chosen -- at pace 0, the dampening term is a no-op by
construction (`floor + (1-floor)*e^0` always equals 1, whatever `floor`
and `k` are), so these two are useful sanity checks but don't help pick
between candidate constants. The important addition was re-checking
Smith-Njigba himself a few minutes further into the same quarter
(`remainingGameClockFraction` down to 0.8447, pace essentially unchanged
at ~0.7535 since he hadn't scored again): at virtually the SAME pace as
the first read, Sleeper's own number had moved from implying about 0.96
dampening down to about 0.88. That's not noise -- it's direct proof that
dampening keeps sliding down through Q1 purely as time passes, even
holding pace fixed, something a flat pace-only formula can't represent
at all (by definition, one `(floor, k)` pair gives exactly one dampening
value for a given pace, never two). Unlike Q4, which looked like a clean
step change once bucketed by quarter, Q1 looks like a genuine continuous
drift that this page's per-quarter-bucket design can't fully capture.
`PACE_DAMPENING_FLOOR_Q1` / `PACE_DAMPENING_K_Q1` were refit (least
squared error) across all four real points -- landing at 0.90 / 1.75,
with Love and Diggs matching almost exactly and the fit deliberately
splitting the difference on Smith-Njigba's two reads (about +0.7 on the
early one, -0.7 on the later one) rather than nailing one and ignoring
the other. That's still only 4 points from 2 real games, nowhere near
the 11-point, cross-validated Q4/OT sample, so this remains a
lower-confidence, better-informed-than-before estimate rather than a
settled fit -- worth replacing with an actually continuous,
time-since-kickoff-aware model once enough independent Q1 examples
accumulate to fit one responsibly, the same way Q4's step-function
insight only became clear once enough points existed to bucket by
quarter in the first place.

**A same-day follow-up brought 4 more real points, this time from
mid-Q2 -- a real signal, but not (yet) enough to act on.** Jeremiyah
Love (6.40 actual off 12.83 pregame, `remainingGameClockFraction`
0.6211), Jaxon Smith-Njigba (15.20 off 20.17, same 0.6211 -- same
broadcast window as Love) and Brock Purdy (13.60 off 18.50, 0.5900) were
all still on the plain Q2-3 baseline (0.40 / 1.10, untouched by the Q1
work above), and all three undershot Sleeper's real live number, by
0.35-0.66 points each -- noticeably more consistent, and a bit larger on
average, than the roughly-symmetric small miss the original 30-point fit
found across Q2-3 as a whole. That's the same DIRECTION as the Q1
finding, which raises a real question: does the "less game has happened
yet, dampen less" effect actually fade out smoothly across Q1 into Q2,
rather than snapping cleanly to the Q2-3 baseline right at the 0.75
threshold? (The fourth point, Stefon Diggs, was zero actual -- the same
pace=0 no-op case as before, an exact match regardless of any constants
and not informative either way.)

This page is deliberately NOT retuning anything off this batch alone.
Three informative points, all pulled from the same few minutes of the
same Sunday's early game window, is a correlated sample, not an
independent one -- exactly the kind of thin, same-day batch this project
has specifically avoided overreacting to before (see the two earlier
"real signal, not (yet) fixable" rounds above the Q4 discovery, both of
which turned out to need a broader, less-correlated sample before a
genuine fix became clear rather than a coincidence). The already-shipped
Q2-3 baseline was fit and leave-one-out cross-validated against roughly
ten times as many points spanning a full slate of games, so a handful of
same-morning results shouldn't move it. If a future, independent gameday
report keeps showing the same early-Q2 undershoot, that would be the
signal to actually extend the lighter-dampening treatment past the Q1
boundary (or, more likely by then, replace the hard Q1/Q2/Q4 buckets
with one continuous, time-since-kickoff-aware curve instead of three
separate flat ones) -- logged here so that evidence doesn't have to be
rediscovered from scratch next time.

**Overtime is now a hard cutoff to actual-so-far, with zero blended
credit for the extra period, for every position -- not just the
DEF-only cap below.** An earlier version of this page modeled OT as one
more 10-minute period tacked onto the 60-minute regulation clock and
blended a (small) share of the pregame projection back in for it, the
same shape as any other in-progress quarter. That was a reasonable-
sounding guess that had never actually been checked against a real OT
game until one showed up live (Garrett Wilson's) -- and Sleeper's own
displayed "projected" number for every player in that game sat exactly
on their actual-so-far the moment OT started, no extra credit at all.
The guess-vs-verify lesson here is the same one `KEY_ALIASES`'s `kr_yd`
entry taught elsewhere on this page: a plausible model of how Sleeper
"must" work is still a guess until it's checked against something real.
`remainingFraction()` now returns 0 the instant `quarter_num` reaches 5,
which routes an OT game through `blendedProjection`'s existing
`remainingFraction <= 0` early return -- the exact same path a
confirmed-complete game takes, no separate OT branch needed anywhere
else. A team already in OT is functionally decided for fantasy purposes
anyway (a sudden-death score ends the game immediately), so there's very
little practical upside being given up by this.

**Team defenses are a deliberate exception to the blend above: once a
DEF's game has started, its Projected-mode score is pinned exactly to
its actual-so-far, with zero blended credit for the rest of the game**
(see `effectiveRemainingFraction()` in `rumbles.html`, which zeroes out
`remainingGameClockFraction` for an in-progress DEF before it ever
reaches `blendedProjection()` above -- a still-pregame DEF, and every
non-DEF position, are untouched by this). This was a direct gameday
observation: defenses were never seen projected above their own
actual-so-far on Sleeper's real site, however early in the game or far
below their pregame projection they were running -- one concrete real
example, a defense already at 13.09 actual points mid-game, showed
Sleeper's own live "projected" number also sitting at exactly 13.09, not
a penny more. That tracks with how DEF scoring actually works: it's
lumpy and swingy (a single defensive/special-teams touchdown, a big
sack/turnover game, or a blowout garbage-time collapse can each be worth
a double-digit point swing on their own), so a smooth clock-weighted
blend of a DEF's pregame projection tends to keep crediting expected
future production that either never shows up or arrives all at once in
a way no gradual blend captures well -- pinning to actual-so-far avoids
guessing at that shape entirely.

**A defense's own "Actual" number can look like it updates noticeably
slower than a skill player's, and that's real -- but it isn't this page
falling behind.** Every position is pulled from the exact same single
stats-array fetch on the exact same 30-second poll (see `POLL_MS` and
the `Promise.all(...)` in the live-fetch function in `rumbles.html`) --
there's no separate, slower code path for DEF anywhere in this page. A
direct check against Sleeper's real stats feed, comparing its
per-player `last_modified` timestamp for several currently-in-progress
games, showed DEF entries updating on the same cadence as the freshest
skill-position players in those same games (tens of seconds to a couple
of minutes) -- not a fixed multi-minute lag baked into the feed itself.
What's actually going on is this league's own DEF scoring
(`scoring_settings`): the two biggest categories, `pts_allow_<bracket>`
and `yds_allow_<bracket>`, are flat bonuses (4.62 and 5.75 points) for
which single bracket the defense currently sits in, backed by only tiny
continuous terms (-0.23/point allowed, -0.02/yard allowed) -- so a
defense's fantasy total is dominated by discrete events (a bracket
boundary getting crossed, a sack, a turnover, a touchdown) rather than
the smooth, every-play accrual a receiver's yardage gets. The underlying
data is just as fresh; there are just fewer moments where a defense's
own point total actually changes, which reads as "slower to update"
even though it isn't. There's no client-side fix available here -- a
faster poll interval wouldn't change how often Sleeper's own feed
produces a new DEF number.

A player whose live game status can't be resolved at all (no team
metadata, or the live-status feed came back empty) falls back to the
plain has-actual-stats check used everywhere else on this page.

**A real bug found this way (live, Sep 20 2026): a weather-delayed game
was silently treated as still pregame, double-counting every player on
both teams who'd already played.** Sleeper's live-scoreboard feed marks a
finished game with `metadata.is_over: true` and a live one with
`metadata.is_in_progress: true` (see `normalizeGameStatus()` in
`rumbles.html`) -- but a real TB @ CLE game that got paused for weather
had BOTH of those false while genuinely sitting mid-4th-quarter
(`quarter_num: 4`, `time_remaining: "2:33"`), because Sleeper's own
top-level `status` field for it was `"suspended"`, a value the status
-string fallback didn't recognize either, so it fell all the way through
to the `"pre_game"` default. That default feeds
`remainingGameClockFraction = 1` (the full game still ahead) into the
blend above, so every TB/CLE starter who already had real, banked
production got a FULL, undampened pregame projection stacked on top of
it -- and, separately, skipped the DEF cap entirely, since that only
kicks in once a game is recognized as having started. Checked against
real rosters live: one manager's Projected total was inflated by +11.38
points from this one game alone (their kicker read 21.47 instead of
~17.67; their DEF, which should have been pinned to its 5.42 actual, read
13.00 instead), and several other managers across the league were off by
6-10 points the same way, just from owning a TB or CLE player. Recomputing
with the fix landed within a couple hundredths of a point of Sleeper's own
displayed live projections for every affected team. The fix: a game whose
`quarter_num` parses to 1 or higher has demonstrably kicked off (a truly
pregame entry's `quarter_num` is `""`, never a number) even when neither
boolean is set and the status string is something unanticipated --
falling back to `"in_progress"` in that case routes through the normal
clock-derived blend instead of silently double-counting, whatever
Sleeper's feed happens to call a paused game next time (a weather hold, a
lightning delay, or "suspended" again). There's deliberately no separate
"suspended" status of its own -- a paused game is still, functionally, a
game in progress, and once it resumes or goes final the normal `is_over`/
`is_in_progress` checks take back over immediately.

**The "Pts This Week" hover tooltip's "Proj" column now shows this same
number**, not a fixed pregame projection: a not-yet-started player still
shows their plain pregame projection (the formula above reduces to
exactly that when nothing's happened yet), an in-progress player shows
the pace-dampened live estimate, and a player whose game has gone final
shows their real final score (there's no more game left to project, so
the formula collapses straight to their actual). That last part is a
deliberate trade-off: this column used to always show the fixed pregame
number even after a player's game ended, specifically so a bust could be
compared against what was expected of them at a glance -- that
comparison is no longer available directly in this column post-game,
since it now converges to their actual final total instead. A team
defense's "Proj" column reflects its own exception too: once its game
has started, this column shows the same actual-so-far number as the
"Actual" column, per the DEF cap described above.

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
- **Projected** -- a player uses their pregame projection while their game
  hasn't started, their real actual-so-far stat line once it's confirmed
  COMPLETE, and a pace-dampened blend of both while it's in progress (see
  the in-progress-scoring section above for the formula and how closely it
  tracks -- but doesn't exactly reproduce -- Sleeper's own live blended
  number). Once the week has genuinely kicked off (see "Pregame vs. live"
  below), this mode DOES fold the in-progress week's numbers -- Rumbles,
  H2H W-L, PF, PA, and Vs. Field W-L -- on top of the cumulative totals, so
  you can see where the season stands if the week ended right now.

**Before kickoff, Projected deliberately does NOT fold anything in yet --
even though Sleeper has already posted the week's matchups and real
projections exist to show.** Matchups routinely post days ahead of the
first game, and a projection is just a guess about a game that hasn't
happened; treating it as provisionally "real" and folding it into Rumbles/
PF/PA/H2H/Vs. Field that early would mean the standings could swing on
projections alone, for a week nobody has actually played a single snap of
yet. So pregame, Projected mode's season-cumulative columns match Actual
mode's exactly -- frozen to `rumbles_history.json`, nothing added -- and so
does "This Week" (Rumbles earned so far): it's sourced from the same
actual-bucket numbers Actual mode uses, regardless of which tab is
selected, so a team correctly shows 0 Rumbles this week in both tabs alike
before kickoff (a live commissioner override, keyed in before the game
started, is the one thing that can still make this nonzero for a specific
roster even pregame -- see "Commissioner score overrides" above; that's
real, already-official points, not a live/projected distinction). The one
thing that's still genuinely different pregame is **"Points This Week"**:
Projected mode keeps showing each team's real projected point total (and
the tooltip breakdown of which players are projected for what) the whole
time, exactly as it does once the week is live -- that number was never
folded into anything, so there's no premature-standings risk in showing
it early, and it's useful information regardless of kickoff. Once the week
actually kicks off, Projected mode switches over to folding the
live/blended numbers into the season totals as described above. See
`foldCurrentWeekIntoTotals` / `rumblesSourceInfo` in `rumbles.html`.

**"Points This Week" becomes "Pts Last Week" in Actual mode specifically,
before kickoff.** Actual mode only ever uses real stats, so once the target
week hasn't kicked off yet -- whether matchups aren't posted at all, or
they're posted but every real player is still sitting at a genuine
pregame 0.00 -- a flat 0.00 "Points This Week" figure is true but useless,
telling you nothing you didn't already know. Rather than show that, the
column header itself relabels to "Pts Last Week" and the value becomes
each roster's real, final PF from the last COMPLETED week
(`last_completed_week_points`, already present in `rumbles_history.json`) --
a genuinely useful "what did this team actually score last time" figure
instead of a placeholder zero. This is deliberately a plain historical
number: it's untouched by anything happening in the not-yet-started week,
including a commissioner's live QB-adjustment override on a still-pregame
roster (that override still shows up right where it always has -- the
"This Week" (Rumbles) column -- and once the week actually kicks off,
"Points This Week" itself reverts to showing it too). The relabel is
Actual-mode-only: Projected mode's whole purpose pregame is showing a real
projected total for the week ahead (see above), never a frozen historical
one, so its header and value are untouched by any of this. See
`showLastWeekPoints`/`this_week_points_is_last_week` in
`buildCombinedStandings`, and the header-label swap right before
`updateHeaderSortIndicators()` in `render()`, both in `rumbles.html`.
Covered end-to-end by `test/run_test.py`'s pregame scenario (1e): the
header reads "Pts Last Week" with the real last-week PF for every roster
(Ankit's live override included) in Actual mode, then reverts to "Pts This
Week" the moment Projected mode is selected.

**Hovering "Pts Last Week" shows last week's real, final per-player
breakdown -- the same tooltip as a live week's "Points This Week", just
fed last week's data.** Rather than leave "Pts Last Week" as a bare number
with nothing to hover, it gets the exact same tooltip treatment as a
genuinely live week: one row per starter with their real actual points
(the tooltip's "Proj" column also just mirrors "Actual" here -- there's
nothing left to project for a week that's already over, same convention a
live tooltip already uses once a player's own game is confirmed complete).
Any QB-injury-backup replacement for that week is appended too, exactly
like the live tooltip does, and colored by its OWN confidence tier: still
yellow (`--replacement`) while "likely" (not yet commissioner-confirmed),
or GREEN (`--good`, matching the QB Injury Backup Adjustments table's own
green for that tier) once "confirmed". This tier-based coloring is shared
code (`renderPtsTooltipContent`'s `.pts-tooltip-replacement`/
`.pts-tooltip-replacement-confirmed` CSS classes) with the live "Points
This Week" tooltip's own replacement row, which used to always render
yellow regardless of confidence -- a latent inconsistency (a confirmed
override with an identified backup would have shown yellow, not green,
during a live week) fixed alongside this feature, not just for last week's
tooltip.

The underlying data comes from `loadLastWeekBreakdown` in `rumbles.html`:
a small, independent, best-effort fetch (last week's matchups + actual
stats only -- no projections, no live-scoreboard feed, since none of that
applies to an already-finished week) kicked off in parallel with the main
live fetch and cached by week (a past week's real stats never change, so
this only actually fetches once per newly-completed week, not on every 30s
poll). Any identified backup QB for that week is read straight out of
`rumbles_history.json`'s own `qb_adjustments` (already computed once,
server-side, at generation time) rather than re-detected client-side --
only "likely" and "confirmed" entries ever produce a row this way
("possible" never carries a real points delta, so there'd be nothing
genuine to credit). Covered end-to-end by `test/run_test.py`'s pregame
scenario (1e): Aidan's tooltip shows one plain, real week-1 player row
(not just the QB-injury cases); Ben's shows his injured starter's real
points plus a GREEN "confirmed" replacement row; Rohaan's shows his
injured starter plus a YELLOW "likely" replacement row -- both colors
hand-verified against the real `--good`/`--replacement` CSS custom
properties, not just a class-name check.

**A still-"likely" QB-injury replacement's estimated points are folded
into the displayed "Pts Last Week" NUMBER too, not just shown in its
tooltip -- mirroring what a live week's "Points This Week" already does,
and dropping back out on its own once the commissioner confirms it.**
`last_completed_week_points` (in `rumbles_history.json`) is a frozen,
server-computed figure from the week it was generated for: it reflects
whatever `official_points()` considered official at that time (the
commissioner's own `custom_points` override once one's been set, real
`points` otherwise), so it never includes a "likely" entry's estimated
backup points -- those are still just an unconfirmed best guess, not
Sleeper's own scored total. That's the same gap `applyQbAdjustmentsToScores`
already closes for the LIVE week's own "Points This Week"/"This Week"
(folding a "likely" entry's estimate straight into the score before
Rumbles are ever computed for it), so this closes it for "Pts Last Week"
the same way: `loadLastWeekBreakdown` sums each roster's still-"likely"
replacement row(s) (reading their own `actual` points straight off the
same breakdown rows the tooltip renders, so the delta can never drift
from what's shown on hover) into a `pointsDeltaByRoster` map, and
`buildCombinedStandings` adds that on top of `last_completed_week_points`
whenever "Pts Last Week" is being shown. A "confirmed" entry is
deliberately excluded from this sum -- its official delta is already
part of `last_completed_week_points` itself (via `official_points()`
preferring `custom_points`), so adding it again would double-count it.
In practice this means: the moment the commissioner keys in their
official adjustment, the entry's tier flips from "likely" to "confirmed"
in `rumbles_history.json`, it stops contributing to `pointsDeltaByRoster`
on its own (no separate "was this previously likely" transition check
needed), and `last_completed_week_points` itself has already picked up
the real number in its place -- so the displayed total doesn't move
(assuming the commissioner's official points matched the estimate) while
the tooltip's replacement row simultaneously turns from yellow to green.
Covered end-to-end by `test/run_test.py`'s pregame scenario (1e): Rohaan's
still-"likely" backup (20.00 real points) is confirmed folded into his
displayed "Pts Last Week" total on top of the history baseline, cross-
checked directly against the same number shown in his own tooltip; Ben's
already-"confirmed" backup is confirmed NOT added a second time, staying
at the official `last_completed_week_points` value alone.

**A yellow asterisk on "Pts Last Week" itself flags when a still-"likely"
estimate is folded into that number, and disappears the moment it's
confirmed.** The previous paragraph's folded-in estimate was, until now,
invisible in the number itself -- you'd only discover it by hovering the
tooltip. Now, whenever a roster's displayed "Pts Last Week" total includes
a still-"likely" (uncommissioner-confirmed) QB-adjustment estimate, a small
yellow `*` appears right after the number, using the exact same asterisk
element/tooltip mechanism as the manager-name `QB Inj*` marker (down to
reusing `#qb-adj-tooltip`, `showQbAdjTooltip`/`hideQbAdjTooltip`, and the
same hover-on-desktop/tap-on-mobile behavior) rather than a second,
parallel tooltip system -- it's keyed into `state.qbAdjTooltipData` under a
`"lw-" + roster_id` lookup key (instead of the live marker's plain
`roster_id`) purely so the two can never collide for the same roster, and
given its own `.pts-lastweek-yellow` CSS class so it renders in this page's
"likely" yellow (`--replacement`) rather than the manager-marker's usual
blue. Its own tooltip reuses `buildQbAdjTooltipHtml` with `{ isLastWeek:
true }` -- the same past-tense, "Pts Last Week"-labeled wording already
described in "Hovering 'Pts Last Week'..." above. The moment the
commissioner confirms the adjustment, the entry's tier flips to
"confirmed" in `rumbles_history.json`, `pointsDeltaByRoster` no longer
counts it (see above), and this asterisk simply stops rendering --
identical lifecycle to the folded-in number itself, no separate cleanup
needed. Covered end-to-end by `test/run_test.py`'s pregame scenario (1e):
the asterisk shows up only for Rohaan (still-"likely"), never for Ben
(already-"confirmed"), is colored the correct yellow, and its own tooltip
uses the last-week-tensed wording.

**"This Week" (the Rumbles column) also relabels to "Last Week" pregame,
in Actual mode, showing each roster's real last-completed-week Rumbles
total -- the same pregame relabel pattern as "Pts This Week"/"Pts Last
Week" above, applied to its neighboring column.** Before kickoff, Actual
mode's "This Week" used to just show 0 Rumbles for everyone (or a live
commissioner override's already-official value for one roster, same as
described in the paragraph above this section) -- accurate, but no more
useful than the old flat-0.00 "Points This Week" was. It now shows
`last_completed_week_rumbles` instead, unconditionally (no override
special-case needed here, since a roster's own last-week Rumbles total is
already whatever it finished with). Like the Pts relabel, this is
Actual-mode-only: Projected mode's pregame "This Week" is deliberately
untouched, still showing the old (pre-relabel) value, including a live
override where one exists -- mirroring exactly how "Pts This Week"/"Pts
Last Week" already draws that same Actual-only line. See
`COLUMNS.this_week.label` in `render()`, and the `thisWeekRumbles`
branch in `buildCombinedStandings`, in `rumbles.html`. Covered end-to-end
by `test/run_test.py`'s pregame scenario (1e): the header reads "Last
Week" and every roster's value matches `last_completed_week_rumbles`
exactly in Actual mode, reverting to "This Week" with the old
Actual-bucket values (Ankit's override included) the instant Projected
mode is selected.

**A green " W" or red " L" appears right after a manager's name, in the
same Manager column, showing last week's real head-to-head result --
Actual mode, pregame only.** Sourced from `rumbles_history.json`'s own
`history.weekly[<last completed week>][<roster_id>].h2h_win` (a field
that's been sitting in the history file, unused, since it was first
written by `build_rumbles.py`'s `score_week()`) -- `true` renders a green
`W`, `false` a red `L`, and a bye/malformed pairing (`h2h_win` is neither
boolean, i.e. `None` server-side) renders nothing at all rather than a
misleading badge either way. This sits right alongside the existing live
`QB Inj*` marker in the same name cell -- both can show at once -- and,
like every other pregame-only feature on this page, only appears in
Actual mode before the target week has kicked off; Projected mode's
manager names, and any manager name once the week is actually live, are
untouched. See `last_week_h2h_win` in `buildCombinedStandings` and the
`.lastweek-h2h-badge`/`.lastweek-h2h-win`/`.lastweek-h2h-loss` CSS classes
in `rumbles.html`. Covered end-to-end by `test/run_test.py`'s pregame
scenario (1e): Kaitlyn (who won her week-1 matchup) shows a green "W",
Ben (who lost his) shows a red "L", both colors hand-verified against the
real `--good`/`--bad` CSS custom properties.

**The "Pts Last Week" tooltip's "Proj" column becomes a "Diff" column --
actual minus last week's real pregame projection, colored green when the
actual beat the projection and red when it fell short -- or the column is
omitted entirely if last week's real pregame projections can't be found
at all.** The old "Proj" column in this particular tooltip was always
identical to "Actual" (there's nothing left to project for a finished
week), which made it pure redundancy. `loadLastWeekBreakdown` now also
fetches Sleeper's real `/projections/nfl/{season}/{week}` payload for the
last completed week -- confirmed, directly against the live Sleeper API,
that this endpoint still serves the genuine, untouched PRE-GAME
projection for an already-finished week, not just future ones -- and
feeds it through the exact same `computePlayerBreakdownEntry` path
already used for the live tooltip's own Proj column, with no changes to
that function itself (`loadLastWeekBreakdown` always passes an empty
`teamGameSchedule`, so that function's own live-blending branch never
engages here -- `entry.proj` lands on the raw pregame number, unblended,
exactly what a real "last week's projection" should mean). Each tooltip
row's `diff` (`actual - proj`, to 2 decimals) is then colored green
(`--good`) when positive/actual-beat-the-projection or red (`--bad`) when
negative -- **except** a QB-injury-replacement row (the yellow "likely" or
green "confirmed" rows described above), which always keeps its OWN tier
color regardless of what its diff's sign would otherwise call for -- a
replacement row's whole point is signaling confidence tier, not whether
the backup outperformed a projection nobody made for them stepping in.
If the projections fetch comes back empty (Sleeper genuinely has nothing
for that week -- confirmed by a real, tested fallback, not just a network
failure), the column is dropped entirely -- no header, no cells, rather
than showing a misleading "Diff" of nothing-vs-actual. This only ever
applies to the "Pts Last Week" tooltip specifically; the live week's
"Points This Week" tooltip keeps its ordinary "Proj" column exactly as
before. See `showDiffColumn`/`r.diff` in `thisWeekTooltip`, and
`projCellHtml`/`showProjColumn` in `renderPtsTooltipContent`, in
`rumbles.html`. Covered end-to-end by `test/run_test.py`: the pregame
scenario (1e) confirms the header reads "Diff" (not "Proj"), that plain
rows are colored by their own diff sign while both replacement rows keep
their tier color even where it disagrees with the diff's sign (a
deliberately adversarial fixture -- Ben's confirmed backup has a negative
diff but stays green, Rohaan's likely backup has a positive diff but
stays yellow), and a separate no-projections-available scenario (2)
confirms the column -- header and every row -- is omitted entirely rather
than showing something misleading.

Completed weeks (from `rumbles_history.json`) aren't affected by the
toggle -- it only changes how the live, in-progress week is scored.

(An earlier version of this page also had a third "Generic PPR" / "Sleeper
Projection" mode using Sleeper's precomputed `pts_ppr` field, and a third
"Our Custom Scoring" label for what's now just "Projected". The PPR mode
was dropped: this league isn't PPR-scored, so that number could never
match what Sleeper's own site shows, no matter how precisely it was
displayed -- it was just a different scoring system. What's now called
"Projected" is the one that actually reproduces Sleeper's own number.)

### "How to Use" button

A button next to "Refresh now" that shows a short explainer of what the
Actual and Projected tabs each mean, in plain language, for anyone opening
the page without the context above. Hover it on desktop; on a touch
device (or with a click on desktop) it toggles open and stays open until
you tap/click elsewhere. Same static content either way -- it's not
per-team data, just a standing explainer.

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
   `main` / root. This repo includes an empty `.nojekyll` file at the root
   -- make sure it's actually committed (it has no extension and no
   visible content, so it's easy to accidentally leave out of a manual
   file copy). Without it, GitHub Pages runs every push through a full
   Jekyll build (installing Ruby gems, generating the site) instead of
   just copying the files as-is, which is both much slower and gets
   slower still as the repo picks up more files over time -- `.nojekyll`
   tells Pages to skip that entirely and serve the files directly. Nothing
   here uses Jekyll templating, so this is always safe. If deploys ever
   start taking several minutes instead of well under one, check that this
   file is still present and committed at the repo root (`git ls-files
   .nojekyll` from a checkout, or look for it directly on github.com).
4. **Trigger the workflow once manually** (Actions tab -> "Update Rumbles
   standings" -> Run workflow) so `rumbles_history.json` gets created for
   the first time. It also only otherwise runs once a day (see the
   `cron` schedule in `.github/workflows/update.yml`) -- after pushing a
   change to `build_rumbles.py` itself (as opposed to `rumbles.html`,
   which takes effect immediately on the next page load with no workflow
   run needed), trigger it manually the same way if you want
   `rumbles_history.json` to pick up that change right away rather than
   waiting for the next scheduled run.
5. **Confirm the page loads** at
   `https://<you>.github.io/<repo>/rumbles.html`.
6. **Embed it** in the Google Site: Insert -> Embed -> By URL, pointing at
   that URL, on its own page.

## Testing without live Sleeper access

`test/make_fixtures.py` builds mock Sleeper API responses, and
`test/run_test.py` runs a headless-browser end-to-end test of the real
`rumbles.html` against those mocks, across eight scenarios:

1. **A week genuinely live** -- hand-verified PF checks across both
   scoring modes (Actual / Projected), including a fully-pregame
   roster to confirm Actual correctly shows the frozen history PF while
   Projected still shows a real, nonzero folded number, a check that
   every manager gets two genuinely distinct totals (proving the modes
   never bleed into each other), a regression check for the `fgmiss`
   key-matching fix AND for the reversed `kr_yd` alias -- an
   already-played fixture DEFENSE-shaped stat line still carries a
   `def_kr_yd` field, now asserted to be correctly ignored rather than
   double-counted (a "poison-pill" fixture also plants the same fields
   on a still-pregame projection to prove neither ever fires there), a
   check that every column -- `#` included -- sorts correctly in both
   directions while each team's own `#` value never changes, a check that
   the standings table carries exactly 11 (not the naive 12 = 1 x 12) LIVE
   badges, all of them in the "Points This Week" column -- "This Week"
   (Rumbles) never shows one, for any roster -- and that the one missing
   badge belongs to Steven specifically (whose two starters both have a
   fixture-marked-`is_over` NFL game, so EVERY player on his roster is
   positively confirmed complete) while every other manager's "Points
   This Week" badge still shows LIVE, a check
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
   current-week matchup, even for a log row about an older week). Also
   hand-verifies the "Points This Week" hover tooltip's table content in
   both modes for a hand-crafted roster (Actual: only the played starter,
   by real name now that it has metadata, with both its actual AND
   projected columns checked, and the unplayed one filtered out entirely;
   Projected: the same played starter is EXCLUDED because his NFL team is
   marked "complete" in the mock live-scoreboard fixture -- reproducing the
   exact reported scenario -- while the still-pregame starter is kept),
   confirms a fully-pregame roster gets no tooltip at all in Actual mode
   rather than an empty table, and confirms a starter whose team is
   "in_progress" (not "complete") is still kept in the Projected tooltip,
   rendered with a green "live" row and a "Mon 1pm"-style kickoff-time
   label in its own separate column (both read back from `league.json`'s
   deliberately-reversed `roster_positions` and `scores_week2.json`'s
   `start_time`, which also proves the slot-order re-sort actually ran --
   the roster-mate in the other slot must display FIRST despite being
   `starters[1]`). Also confirms (via real `getComputedStyle` colors, not
   just text content) that a live row's green highlight lands on the Name,
   Actual, AND Proj cells, that two starters whose games
   share the same displayed kickoff label (a second fixture game, "Sun
   1pm", added specifically for this) get the SAME per-timeslot color
   while a different label ("Mon 8pm") gets a DIFFERENT one, and that the
   "This Week" and "Points This Week" cells of whichever manager is ahead
   in this week's live H2H matchup are colored to match their own
   matchup-name color, checked in BOTH modes -- including that the two
   modes can disagree on who's actually ahead (this fixture's H2H pair
   has different leaders in Actual vs. Projected), proving the coloring
   is genuinely re-evaluated per mode rather than fixed to one of them;
   their opponent's cells stay uncolored in both cases.
2. **Same live week, in a touch-primary (mobile) context** -- confirms the
   page's own hover-capability check (`matchMedia("(hover: hover) and
   (pointer: fine)")`) correctly reports `false` for a mobile-emulated
   context, that a plain hover event does NOT show the tooltip there
   (proving it's really on the tap-only code path, not silently falling
   back to hover), and that tapping the cell shows it, tapping the same
   cell again toggles it closed, and tapping anywhere else on the page
   dismisses an open one. Also confirms the "How to Use" button's tooltip
   behaves the same way on a touch device (tap-to-toggle, tap-elsewhere
   dismisses, a plain hover does nothing).
3. **Mobile responsive layout** -- two checks, at two different
   viewports. Portrait (390px wide): confirms the table starts at
   `scrollLeft: 0` and that #, Manager, Rumbles, This Week, and Points
   This Week's combined right edge fits inside the visible container with
   no horizontal scrolling needed to see any of them, confirms the
   "Points This Week" LIVE badge sits strictly to the right of the
   number's own text with a positive (non-overlapping) gap, and confirms
   "This Week" shows zero LIVE badges anywhere in the table. Landscape
   (844x390, matching the page's own landscape-mobile breakpoint):
   confirms the QB Injury Backup Adjustments table's `.backup-list`
   actually computes to `flex-direction: row` at this breakpoint (not the
   default stacked `column`), and that the page's overall width still
   doesn't exceed the viewport (no horizontal scrolling introduced by the
   row layout).
4. **The "How to Use" button, on a real-hover (desktop) context** --
   confirms hovering shows the explainer tooltip (and moving the mouse
   away hides it again), that its content actually explains both the
   Actual and Projected tabs with no em dashes and no mention of the QB
   Injury Backup Adjustments table, and that clicking it also works on a
   hover-capable device: shows it, a second click toggles it closed, and
   clicking elsewhere on the page dismisses an open one. This also
   regression-tests a real race this button has to avoid: on a device with
   real hover, a click is always preceded by a real mouseenter (the cursor
   has to arrive before the click fires), so a naive show/hide toggle on
   click would immediately re-close what hover had just opened -- click
   there needs to PIN the tooltip open instead (see `howtoPinned` in
   `rumbles.html`).
5. **Week 2's matchups are posted but no game has kicked off yet
   (pregame)** -- confirms the grey (non-live) status pill reads "Week 2
   begins &lt;day&gt; &lt;time&gt;" using the EARLIEST kickoff across the
   whole week (not just the first fixture game), that both headers relabel
   in Actual mode ("Points This Week" -> "Pts Last Week", "This Week" ->
   "Last Week"), that "Last Week" shows every roster's real
   last-completed-week Rumbles total unconditionally (Ankit's override no
   longer a special case now that the whole column is last-week-sourced),
   that "Pts Last Week" is every roster's real last-completed-week PF --
   EXCEPT that Rohaan's still-"likely" backup's real 20.00 points ARE
   folded on top of his history baseline (cross-checked directly against
   the same number shown in his own tooltip) with a yellow `*` appearing
   right after his number, while Ben's already-"confirmed" backup is
   confirmed NOT double-added and shows no asterisk -- while every
   cumulative column stays exactly as `rumbles_history.json` already has
   them, that a green "W" (Kaitlyn) or red "L" (Ben) badge appears right
   after the manager name showing last week's real H2H result, that
   hovering "Pts Last Week" shows week 1's real per-player breakdown with
   a "Diff" column (not "Proj") -- a plain player row with its own
   actual-vs-pregame-projection diff colored by sign (Aidan), an injured
   starter plus a GREEN "confirmed" replacement row that keeps its tier
   color despite a negative diff (Ben), and an injured starter plus a
   YELLOW "likely" replacement row that keeps its tier color despite a
   positive diff (Rohaan), all colors hand-verified against the real
   `--good`/`--bad`/`--replacement` CSS custom properties -- that the
   asterisk's own tooltip uses last-week-tensed wording naming "Pts Last
   Week" -- that both headers revert to "This Week"/"Pts This Week" the
   moment Projected mode is selected, that Projected mode's "This
   Week"/Rumbles keeps its OLD pre-relabel value (Ankit's override
   included, every cumulative column staying unfolded) while "Points This
   Week" shows a real nonzero projected total instead, that the "Points
   This Week" tooltip still works pregame, and that a stale week-1
   "possible" QB-adjustment entry (Jake) is still correctly showing (week
   2 hasn't kicked off yet, so it isn't stale YET) with its Injured QB
   cell colored blue rather than the usual red. A companion check in
   scenario 2 (Week 1 final, Week 2 not yet posted, no projections fixture
   supplied) confirms the "Pts Last Week" tooltip's Diff column -- header
   and every row -- is omitted entirely rather than showing something
   misleading, when last week's real pregame projections aren't
   available.
6. **Sleeper's `state.week` pointer has advanced AHEAD of
   `rumbles_history.json`** -- the exact reported production bug: Week 2
   has genuinely ended and Sleeper's own pointer already says week 3, but
   `rumbles_history.json` (only rewritten once a day) still only has week
   1 finalized. Reuses the same real, in-progress week-2 fixtures as
   scenario 1 (deliberately with NO week-3 routes mocked at all, so a
   regression back to targeting week 3 fails loudly rather than silently
   showing stale/blank data), and confirms the page still targets and
   live-tracks week 2: the status pill reads "Live: Week 2" (not blank),
   week 1's cumulative Rumbles are intact, several rosters show real
   nonzero live "Points This Week" figures, and the QB Injury Backup
   Adjustments table shows week 2's freshly-detected live entries (Alex
   "likely", Ankit "confirmed") -- none of which would exist at all if
   the page had wrongly jumped to the empty week 3.
7. **Cumulative-only** -- the mirror-image case: Week 1 is final in
   `rumbles_history.json`, but Sleeper's own `state.week` pointer hasn't
   rolled over yet (LAGGING, not ahead) and Week 2's matchups aren't
   posted. The page must show Week 1's cumulative standings, never a
   blank table.
8. **`rumbles_history.json` fails to load** -- explicitly mocked as a 404
   (rather than relying on the file's absence at the served project root,
   which is no longer guaranteed now that a real `rumbles_history.json`
   ships committed at the repo root) -- the page must show a clear,
   diagnosable message instead of a silent blank table.

`test/test_build_rumbles.py` is a separate, plain-Python unit test (no
browser, no network) covering `build_rumbles.py`'s scoring math directly
-- in particular the `custom_points` commissioner-override handling: that
it's preferred over the plain `points` field when set, and that it
correctly flows through to PF, the opponent's PA, H2H result, and the
vs.-the-field outscored/outscored-by counts. It also covers the QB
injury-backup detector: the dot-product QB scoring, team-scoping (same
scenario as above -- excludes a non-playing same-team QB and a playing
different-team QB), all three confidence tiers -- including that
"possible" fires whenever fresh but uncorroborated (a `None` status and a
real non-out one like "Questionable" are both checked) and, unlike
"likely", is deliberately NOT carried forward once its week goes stale --
and, the exact bug this log design fixes, that a commissioner override
always produces a log entry even when no backup QB can be independently
identified from the stats.

`test/test_blended_projection.js` (`node test/test_blended_projection.js`,
no server/browser needed), `test/test_qb_adj_tooltip.js` (`node
test/test_qb_adj_tooltip.js`, likewise), and `test/test_qb_adj_detection.js`
(`node test/test_qb_adj_detection.js`, likewise) are all standalone unit
tests that regex-extract specific pure functions straight out of
`rumbles.html` and exercise them in isolation -- the live-blending
pace-dampening math for the first, the QB-injury manager-marker tooltip's
wording (including the "a replacement QB is also injured" chain scenarios)
for the second, and `detectQbAdjustmentsForWeek`/`applyQbAdjustmentsToScores`
(the client-side confidence-tier detector and how it feeds the live
totals -- including the "possible" tier never moving either total or
populating the marker/tooltip map) for the third. All three exist
specifically to cover logic that would otherwise need a lot of fixture
plumbing to reach through the full Playwright scenario for what's really
pure string/number-crunching with no DOM or live-fetch involved.

Useful if you ever touch the scoring or live-detection logic and want to
check it without waiting for a live NFL window:

```bash
pip install playwright
python -m playwright install chromium
python test/make_fixtures.py
python -m http.server 8123 &   # serve the repo root
python test/run_test.py
python test/test_build_rumbles.py   # no server/browser needed for this one
node test/test_blended_projection.js   # ditto
node test/test_qb_adj_tooltip.js       # ditto
node test/test_qb_adj_detection.js     # ditto
```

## Source of truth

For now this is a supplemental gameday view -- keep updating the "2026
TRUE STANDINGS" Google Sheet by hand. Eventually this page is meant to
replace that manual process, but that's a future step.
