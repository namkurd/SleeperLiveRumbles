// Standalone unit test for rumbles.html's computeH2hStreak -- the
// manager-name "W"/"2W"/"L"/"3L" streak-badge logic -- run directly with
// `node test/test_h2h_streak.js`, no browser, no fixtures, no server
// needed.
//
// Why this exists as its own thing rather than living inside run_test.py's
// Playwright fixture: the Playwright pregame fixture (scenario 1e) only
// ever has ONE completed week of history (weeks_completed: [1]), which is
// enough to exercise the single-week "W"/"L" case (a 1-week streak) but
// not a real multi-week streak (2W, 3L, a streak broken by a bye
// partway back, etc) -- building that out in the shared fixture would mean
// adding a second fully-completed week to EVERY scenario that fixture
// backs (live week, mobile layouts, the pointer-ahead scenario, ...), just
// to test a few lines of pure backward-scan arithmetic with no DOM
// involvement at all. Instead, this file regex-extracts computeH2hStreak
// straight out of rumbles.html's real source and evals it in isolation
// (same technique test_qb_adj_tooltip.js and test_blended_projection.js
// already use), so what's tested is the actual shipped code. The
// single-week case is still covered end-to-end by run_test.py's pregame
// scenario (Kaitlyn's "W" / Ben's "L") -- this file covers the streak-
// length branches that scenario can't reach with only one week of history.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const RUMBLES_PATH = path.join(__dirname, "..", "rumbles.html");
const html = fs.readFileSync(RUMBLES_PATH, "utf8");

function extract(pattern, label) {
  const m = html.match(pattern);
  if (!m) throw new Error("Couldn't find " + label + " in rumbles.html -- has it moved or been renamed?");
  return m[0];
}

const source = extract(/function computeH2hStreak\([^)]*\) \{[\s\S]*?\n  \}/, "computeH2hStreak");

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(source + "\nthis.computeH2hStreak = computeH2hStreak;", sandbox);
const { computeH2hStreak } = sandbox;

let failures = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) {
    console.log("PASS: " + label);
  } else {
    failures++;
    console.log("FAIL: " + label);
    console.log("  expected: " + e);
    console.log("  got:      " + a);
  }
}

// Small helper: builds a `history.weekly` log for roster 1 across however
// many weeks are given, one h2h_win value per week (true/false/null --
// null simulates a bye/unresolvable week), 1-indexed by array position.
function weeklyFor(results) {
  const weekly = {};
  results.forEach(function (win, i) {
    const week = i + 1;
    weekly[String(week)] = {
      "1": win === null ? { points: 0, opponent_roster_id: null, opponent_points: null, h2h_win: null, teams_outscored: 0, teams_outscored_by: 0, rumbles: 0 } : { points: 100, opponent_roster_id: 2, opponent_points: 90, h2h_win: win, teams_outscored: 5, teams_outscored_by: 5, rumbles: win ? 9 : 0 },
    };
  });
  return { weekly: weekly };
}

// ---- a single win, nothing before it -- the plain "W" case (unchanged
// from before this was a streak at all) ----------------------------------
check(
  "single win, week 1 only -- length 1",
  computeH2hStreak(weeklyFor([true]), 1, 1),
  { result: true, length: 1 }
);

// ---- a single loss, same idea ------------------------------------------
check(
  "single loss, week 1 only -- length 1",
  computeH2hStreak(weeklyFor([false]), 1, 1),
  { result: false, length: 1 }
);

// ---- two wins in a row -- "2W" ------------------------------------------
check(
  "two wins in a row -- length 2",
  computeH2hStreak(weeklyFor([true, true]), 2, 1),
  { result: true, length: 2 }
);

// ---- three losses in a row -- "3L" ---------------------------------------
check(
  "three losses in a row -- length 3",
  computeH2hStreak(weeklyFor([false, false, false]), 3, 1),
  { result: false, length: 3 }
);

// ---- the streak breaks when the result flips: a win right after a loss
// only counts the win itself, not the loss before it ----------------------
check(
  "result flips from loss to win -- streak resets to just the win",
  computeH2hStreak(weeklyFor([false, false, true]), 3, 1),
  { result: true, length: 1 }
);
check(
  "result flips from win to loss -- streak resets to just the loss",
  computeH2hStreak(weeklyFor([true, true, true, false]), 4, 1),
  { result: false, length: 1 }
);

// ---- a bye (h2h_win: null) at the MOST RECENT week -- nothing to show at
// all, regardless of what came before it ----------------------------------
check(
  "bye in the most recent week -- no streak to show, even with wins before it",
  computeH2hStreak(weeklyFor([true, true, null]), 3, 1),
  { result: null, length: 0 }
);

// ---- a bye PARTWAY back stops the streak from extending past it, rather
// than being skipped over -- only the wins AFTER the bye count -----------
check(
  "bye partway back stops the streak there -- only weeks after it count",
  computeH2hStreak(weeklyFor([true, null, true, true]), 4, 1),
  { result: true, length: 2 }
);

// ---- missing history.weekly entirely, or no entry for the given week at
// all, degrades gracefully to no streak rather than throwing -------------
check(
  "no history.weekly at all -- degrades to no streak, doesn't throw",
  computeH2hStreak({}, 3, 1),
  { result: null, length: 0 }
);
check(
  "uptoWeek is null (no last-completed week known yet) -- no streak",
  computeH2hStreak(weeklyFor([true, true]), null, 1),
  { result: null, length: 0 }
);
check(
  "a roster with no entry at all in the given week's log -- treated like a bye",
  computeH2hStreak({ weekly: { "1": {} } }, 1, 1),
  { result: null, length: 0 }
);

// ---- a long streak (double-digit length) still reports the real number,
// not just capped at some small max -- the badge text itself ("10W") is
// rumbles.html's own concern, this function just counts honestly ---------
check(
  "a 10-week win streak reports length 10, not capped",
  computeH2hStreak(weeklyFor(new Array(10).fill(true)), 10, 1),
  { result: true, length: 10 }
);

console.log(failures ? "\n" + failures + " FAILURE(S)" : "\nALL computeH2hStreak UNIT TESTS PASSED");
process.exit(failures ? 1 : 0);
