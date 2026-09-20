// Standalone unit test for rumbles.html's in-progress-player blending
// logic (blendedProjection / effectiveRemainingFraction), run directly
// with `node test/test_blended_projection.js` -- no browser, no fixtures,
// no server needed.
//
// Why this exists as its own thing rather than living inside run_test.py's
// Playwright fixture: rumbles.html's whole <script> is one top-level IIFE
// (see its "(function () { ... })();" wrapper), so blendedProjection and
// effectiveRemainingFraction are private to that closure and can't be
// called from outside it. Exercising them through the full fixture would
// mean threading a real in-progress DEF starter through
// make_fixtures.py's roster_players/starters/roster_positions plumbing,
// which several OTHER already-passing assertions (hand-verified PF
// numbers, tooltip slot ordering) depend on the exact current shape of --
// a real risk of an unrelated regression for a check that doesn't need a
// browser at all. Instead, this file regex-extracts just those two pure,
// self-contained functions (plus the two constants they use) straight out
// of rumbles.html's real source and evals them in isolation, so what's
// tested is the actual shipped code, not a hand-copied reimplementation
// that could quietly drift from it.
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

const source = [
  extract(/var PACE_DAMPENING_FLOOR = [^;]+;/, "PACE_DAMPENING_FLOOR"),
  extract(/var PACE_DAMPENING_K = [^;]+;/, "PACE_DAMPENING_K"),
  extract(/var PACE_DAMPENING_Q4_THRESHOLD = [^;]+;/, "PACE_DAMPENING_Q4_THRESHOLD"),
  extract(/var PACE_DAMPENING_FLOOR_Q4 = [^;]+;/, "PACE_DAMPENING_FLOOR_Q4"),
  extract(/var PACE_DAMPENING_K_Q4 = [^;]+;/, "PACE_DAMPENING_K_Q4"),
  extract(/function blendedProjection\([^)]*\) \{[\s\S]*?\n  \}/, "blendedProjection"),
  extract(/function effectiveRemainingFraction\([^)]*\) \{[\s\S]*?\n  \}/, "effectiveRemainingFraction"),
  extract(/function normalizeGameStatus\([^)]*\) \{[\s\S]*?\n  \}/, "normalizeGameStatus"),
].join("\n");

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(
  source +
    "\nthis.blendedProjection = blendedProjection; this.effectiveRemainingFraction = effectiveRemainingFraction; this.normalizeGameStatus = normalizeGameStatus;",
  sandbox
);
const { blendedProjection, effectiveRemainingFraction, normalizeGameStatus } = sandbox;

let failures = 0;
function assertClose(actual, expected, tolerance, label) {
  const diff = Math.abs(actual - expected);
  if (diff > tolerance) {
    failures++;
    console.error(`FAIL: ${label} -- expected ${expected} (+/-${tolerance}), got ${actual} (diff ${diff.toFixed(4)})`);
  } else {
    console.log(`PASS: ${label} (got ${actual.toFixed(4)}, expected ${expected} +/-${tolerance})`);
  }
}
function assertEqual(actual, expected, label) {
  if (actual !== expected) {
    failures++;
    console.error(`FAIL: ${label} -- expected ${expected}, got ${actual}`);
  } else {
    console.log(`PASS: ${label} (${actual})`);
  }
}

// ---- blendedProjection: boundary behavior -------------------------------
assertEqual(blendedProjection(2.5, 17.0, 0), 2.5, "remainingFraction=0 (game over) reduces to actualPts alone");
assertEqual(blendedProjection(0, 12.5, 1), 12.5, "remainingFraction=1, zero actual (pregame) reduces to pregameProjPts alone");
assertEqual(blendedProjection(5, 0, 0.5), 5, "pregameProjPts=0 never divides by zero -- pace treated as 0, blend is just actual");

// ---- blendedProjection: real validated data points (see README's table,
// fit against 30 real Week 2 players' live-displayed Sleeper numbers,
// across four separate gameday reports) ----
// Tucker Kraft: zero actual production -- dampening barely matters, blend
// should land almost exactly on Sleeper's real shown number (7.19).
assertClose(blendedProjection(0.00, 12.24, 0.5872), 7.19, 0.05, "Kraft (0 actual) matches Sleeper's real live number closely");
// DeVonta Smith: already well ahead of pace (19.10 actual vs 15.22 pregame
// projection, over 100% before halftime) -- dampening should pull this
// well below the undampened flat-blend guess of 26.80, landing near
// Sleeper's real 23.98.
assertClose(blendedProjection(19.10, 15.22, 0.5056), 23.98, 1.0, "Smith (already over pregame proj) lands within ~1pt of Sleeper's real live number");
// A fourth gameday report (see README) added 12 more players, mostly deep
// in the 3rd/4th quarter (small remainingFraction). Jayden Reed: small
// remainingFraction, but very low pace (barely any production yet) --
// dampening barely matters here either, same as Kraft above, so this one
// matched almost exactly even before the Q4-specific fix below (Reed's
// remainingFraction, 0.2631, is actually still Q3 -- see the boundary
// tests further down).
assertClose(blendedProjection(1.40, 12.262, 0.2631), 4.35, 0.1, "Reed (low pace, late game) matches Sleeper's real live number closely");
// Derrick Henry: remainingFraction 0.1464 -- genuinely Q4. A fifth
// gameday report re-checked several of this batch's players even later
// in the same games (remainingFraction down to 0.05-0.17) and found this
// formula was STILL overshooting every one of them -- bucketing all the
// validated points by quarter (not just by remainingFraction as one
// continuous scale) showed the miss wasn't a smooth drift, it was
// specific to Q4/OT: every real Q4/OT point overshot, while Q1-Q3 stayed
// small and mixed-sign. Q4/OT now gets its own, more aggressive
// constants (see PACE_DAMPENING_FLOOR_Q4/K_Q4's comment in rumbles.html)
// -- these next few assertions are real Q4 data points, now held to a
// tight tolerance instead of the ~1pt one they needed before that fix.
assertClose(blendedProjection(19.20, 15.28, 0.1464), 19.61, 0.3, "Henry (Q4, over pace) lands within ~0.3pt of Sleeper's real live number with the Q4-specific constants");
assertClose(blendedProjection(12.30, 11.095, 0.1672), 12.64, 0.3, "Schultz, re-checked later in the same game (Q4 now) lands within ~0.3pt");
assertClose(blendedProjection(27.00, 17.838, 0.1672), 27.76, 0.05, "Chase, re-checked later in the same game (Q4 now) matches almost exactly");
assertClose(blendedProjection(25.58, 14.6588, 0.1389), 26.07, 0.05, "Young, re-checked later in the same game (Q4 now) matches almost exactly");

// ---- blendedProjection: the Q4/OT constant switch itself ----------------
// remainingFraction()'s own math means Q3 never produces anything below
// 0.25 and Q4 never produces anything above it -- so the switch is exact,
// not approximate, and these pin that boundary down directly rather than
// only exercising it indirectly through the real data points above.
(function () {
  var justAboveQ4 = blendedProjection(10, 20, 0.2501); // still Q3 -- FLOOR/K
  var justAtQ4 = blendedProjection(10, 20, 0.25); // Q4 -- FLOOR_Q4/K_Q4
  if (justAboveQ4 === justAtQ4) {
    failures++;
    console.error("FAIL: Q3/Q4 boundary -- expected a visible jump in the blended value right at remainingFraction=0.25, got none (Q4-specific constants aren't being applied)");
  } else {
    console.log(`PASS: Q3/Q4 boundary produces a real jump (0.2501 -> ${justAboveQ4.toFixed(4)}, 0.2500 -> ${justAtQ4.toFixed(4)})`);
  }
})();

// ---- effectiveRemainingFraction: the DEF cap ----------------------------
assertEqual(effectiveRemainingFraction("DEF", 0.65), 0, "an in-progress DEF gets its remainingFraction zeroed out (no blended future credit)");
assertEqual(effectiveRemainingFraction("DEF", 1), 1, "a still-pregame DEF (remainingFraction=1) is untouched -- normal pregame projection");
assertEqual(effectiveRemainingFraction("DEF", 0), 0, "a complete DEF's remainingFraction (already 0) is untouched");
assertEqual(effectiveRemainingFraction("WR", 0.65), 0.65, "a non-DEF position's remainingFraction is never touched by the DEF cap");
assertEqual(effectiveRemainingFraction(null, 0.65), 0.65, "no resolvable position (null) is treated like any non-DEF position");

// ---- the two combined: an in-progress DEF's blended total is pinned to
// its actual-so-far, exactly matching the real example that motivated this
// rule (a defense already at 13.09 actual mid-game, Sleeper's own live
// "projected" number also showing 13.09 -- not a penny more).
const defActual = 13.09, defPregameProj = 9.5, defRemFrac = effectiveRemainingFraction("DEF", 0.52);
assertEqual(blendedProjection(defActual, defPregameProj, defRemFrac), defActual, "an in-progress DEF's blend equals its actual-so-far exactly, regardless of its pregame projection or game clock");

// ---- normalizeGameStatus: the real cases Sleeper's live feed sends ------
assertEqual(normalizeGameStatus({ status: "complete", metadata: { is_over: true, is_in_progress: false, quarter_num: 4 } }), "complete", "is_over=true is authoritative, regardless of the top-level status string");
assertEqual(normalizeGameStatus({ status: "in_game", metadata: { is_over: false, is_in_progress: true, quarter_num: 2 } }), "in_progress", "is_in_progress=true is authoritative");
assertEqual(normalizeGameStatus({ status: "pre_game", metadata: { is_over: false, is_in_progress: false, quarter_num: "" } }), "pre_game", "a genuine pregame entry (no quarter_num yet) is still pre_game");
assertEqual(normalizeGameStatus({ status: "post_game", metadata: {} }), "complete", "top-level status string fallback still catches \"post_game\" when metadata omits the booleans");
// The real bug (see normalizeGameStatus's comment in rumbles.html): a
// weather-delayed TB @ CLE game on 2026-09-20 sat with BOTH booleans
// false while genuinely mid-4th-quarter (quarter_num 4, 2:33 left), and
// Sleeper's own top-level "status" for it was "suspended" -- a string
// that matched neither the "complete" nor "in_progress" checks and used
// to fall through to "pre_game", double-counting every player on either
// team who'd already banked real production. This is that exact payload.
assertEqual(
  normalizeGameStatus({ status: "suspended", metadata: { is_over: false, is_in_progress: false, quarter_num: 4, time_remaining: "2:33" } }),
  "in_progress",
  "a paused-but-not-final game (real \"suspended\" TB@CLE payload) resolves to in_progress, not pre_game"
);
// Same idea for any other not-yet-recognized status string, as long as
// quarter_num shows the game has actually kicked off -- the fix isn't
// specific to the literal word "suspended".
assertEqual(
  normalizeGameStatus({ status: "delayed", metadata: { is_over: false, is_in_progress: false, quarter_num: 1, time_remaining: "10:15" } }),
  "in_progress",
  "any unrecognized status string with a real quarter_num is treated as in_progress, not just \"suspended\""
);

if (failures > 0) {
  console.error(`\n${failures} FAILURE(S)`);
  process.exit(1);
} else {
  console.log("\nALL blendedProjection/effectiveRemainingFraction UNIT TESTS PASSED");
}
