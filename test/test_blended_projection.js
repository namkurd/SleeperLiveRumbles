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
  extract(/function blendedProjection\([^)]*\) \{[\s\S]*?\n  \}/, "blendedProjection"),
  extract(/function effectiveRemainingFraction\([^)]*\) \{[\s\S]*?\n  \}/, "effectiveRemainingFraction"),
].join("\n");

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(source + "\nthis.blendedProjection = blendedProjection; this.effectiveRemainingFraction = effectiveRemainingFraction;", sandbox);
const { blendedProjection, effectiveRemainingFraction } = sandbox;

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
// fit against 18 real Week 2 players' live-displayed Sleeper numbers) ----
// Tucker Kraft: zero actual production -- dampening barely matters, blend
// should land almost exactly on Sleeper's real shown number (7.19).
assertClose(blendedProjection(0.00, 12.24, 0.5872), 7.19, 0.05, "Kraft (0 actual) matches Sleeper's real live number closely");
// DeVonta Smith: already well ahead of pace (19.10 actual vs 15.22 pregame
// projection, over 100% before halftime) -- dampening should pull this
// well below the undampened flat-blend guess of 26.80, landing near
// Sleeper's real 23.98.
assertClose(blendedProjection(19.10, 15.22, 0.5056), 23.98, 1.0, "Smith (already over pregame proj) lands within ~1pt of Sleeper's real live number");

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

if (failures > 0) {
  console.error(`\n${failures} FAILURE(S)`);
  process.exit(1);
} else {
  console.log("\nALL blendedProjection/effectiveRemainingFraction UNIT TESTS PASSED");
}
