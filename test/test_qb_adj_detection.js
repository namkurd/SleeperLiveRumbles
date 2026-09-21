// Standalone unit test for rumbles.html's live QB-injury-backup-points
// confidence-tier detection (detectQbAdjustmentsForWeek) and how it feeds
// the live standings totals (applyQbAdjustmentsToScores), run directly
// with `node test/test_qb_adj_detection.js` -- no browser, no fixtures, no
// server needed.
//
// Why this exists as its own thing rather than extending run_test.py's
// Playwright fixture: that fixture's roster 6 (Alex/Kyler-Murray/Carson-
// Wentz) is already precisely tied to a large number of existing, passing
// assertions (live totals, tooltip rows, coloring, kickoff columns...), so
// adding a whole new "possible"-tier scenario roster into that same
// matchups/stats/players graph risks disturbing all of it for what is,
// underneath, pure function logic with no DOM/live-fetch involvement.
// Instead (same pattern as test_qb_adj_tooltip.js), this file regex-
// extracts the real functions straight out of rumbles.html's source and
// evals them in isolation, so what's tested is the actual shipped code.
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
  extract(/var KEY_ALIASES = \{\};/, "KEY_ALIASES"),
  extract(/var TIER_SUM_KEYS = \{[^}]*\};/, "TIER_SUM_KEYS"),
  extract(/function round2\([^)]*\) \{[\s\S]*?\n  \}/, "round2"),
  extract(/function dotProduct\([^)]*\) \{[\s\S]*?\n  \}/, "dotProduct"),
  extract(/function qbScore\([^)]*\) \{[\s\S]*?\n  \}/, "qbScore"),
  extract(/function isPlayed\([^)]*\) \{[\s\S]*?\n  \}/, "isPlayed"),
  extract(/function playerName\([^)]*\) \{[\s\S]*?\n  \}/, "playerName"),
  extract(/function buildTeamQbIndex\([^)]*\) \{[\s\S]*?\n  \}/, "buildTeamQbIndex"),
  extract(/function findStartedQb\([^)]*\) \{[\s\S]*?\n  \}/, "findStartedQb"),
  extract(/function isOutStatus\([^)]*\) \{[\s\S]*?\n  \}/, "isOutStatus"),
  extract(/function findBackupQbs\([^)]*\) \{[\s\S]*?\n  \}/, "findBackupQbs"),
  extract(/function detectQbAdjustmentsForWeek\([^)]*\) \{[\s\S]*?\n  \}/, "detectQbAdjustmentsForWeek"),
  extract(/function applyQbAdjustmentsToScores\([^)]*\) \{[\s\S]*?\n  \}/, "applyQbAdjustmentsToScores"),
].join("\n");

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(
  source + "\nthis.detectQbAdjustmentsForWeek = detectQbAdjustmentsForWeek;" +
    "\nthis.applyQbAdjustmentsToScores = applyQbAdjustmentsToScores;" +
    "\nthis.buildTeamQbIndex = buildTeamQbIndex;",
  sandbox
);
const { detectQbAdjustmentsForWeek, applyQbAdjustmentsToScores, buildTeamQbIndex } = sandbox;

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
function ok(label, cond) {
  if (cond) {
    console.log("PASS: " + label);
  } else {
    failures++;
    console.log("FAIL: " + label);
  }
}

// ---- Shared fixture, mirrors test_build_rumbles.py's QB_* fixture so the
// two suites (JS live-detection, Python historical-detection) exercise the
// exact same real-world numbers: Kyler Murray (started) / Carson Wentz
// (backup, outscores him) / JJ McCarthy (3rd-string, didn't play) / an
// unrelated same-position QB on a different team (must be excluded). -----
const SCORING = { pass_yd: 0.04, pass_td: 4, pass_int: -2, rush_yd: 0.1, rush_td: 6 };

function playersMeta(starterStatus) {
  return {
    QB_STARTER: { position: "QB", team: "MIN", full_name: "Kyler Murray", injury_status: starterStatus },
    QB_BACKUP: { position: "QB", team: "MIN", full_name: "Carson Wentz", injury_status: null },
    QB_THIRD: { position: "QB", team: "MIN", full_name: "JJ McCarthy", injury_status: null },
    QB_OTHER_TEAM: { position: "QB", team: "KC", full_name: "Other Team's QB", injury_status: null },
  };
}

const STATS = {
  QB_STARTER: { pass_att: 10, pass_yd: 80, pass_td: 1, pass_int: 0 }, // 7.2
  QB_BACKUP: { pass_att: 25, pass_yd: 210, pass_td: 2, pass_int: 1, rush_yd: 15, rush_td: 1 }, // 21.9
  QB_OTHER_TEAM: { pass_att: 20, pass_yd: 150, pass_td: 1, pass_int: 0 },
  // QB_THIRD deliberately has no stats entry -- did not play.
};

const MATCHUPS = [
  { roster_id: 1, matchup_id: 1, starters: ["QB_STARTER", "WR1"], points: 100.0 },
  { roster_id: 2, matchup_id: 1, starters: ["WR2"], points: 90.0 },
];

const MANAGERS = { 1: "Alex", 2: "Ben" };

function detect(starterStatus, isFresh) {
  var meta = playersMeta(starterStatus);
  return detectQbAdjustmentsForWeek(2, MATCHUPS, meta, buildTeamQbIndex(meta), STATS, SCORING, MANAGERS, isFresh);
}

// ---- "possible" fires when fresh, a backup recorded action, but nothing
// corroborates the starter being out (Ben's own Caleb Williams/Tyler
// Bagent example: null injury_status, i.e. it was never marked). --------
(function () {
  var entries = detect(null, true);
  ok("exactly one entry logged for the null-status case", entries.length === 1);
  var e = entries[0];
  check("possible tier: confidence", e.confidence, "possible");
  check("possible tier: backup_qbs", e.backup_qbs.map(function (b) { return b.name; }), ["Carson Wentz"]);
  ok("possible tier: backup_points_total still recorded (21.9) for awareness", Math.abs(e.backup_points_total - 21.9) < 1e-9);
  check("possible tier: custom_points_delta is null (only a commissioner sets this)", e.custom_points_delta, null);
})();

// ---- Also fires for a real-but-non-out status ("Questionable") -- not
// just a missing one. -----------------------------------------------------
(function () {
  var entries = detect("Questionable", true);
  check("possible tier fires for 'Questionable' too", entries[0].confidence, "possible");
})();

// ---- "likely" still fires (unaffected regression check) when the status
// DOES corroborate out/IR/PUP. --------------------------------------------
(function () {
  var entries = detect("Out", true);
  check("'Out' still yields 'likely', not 'possible'", entries[0].confidence, "likely");
})();

// ---- Not fresh -- and not corroborated -- falls back to "possible" too
// (freshness only gates whether "likely" can be claimed; the weaker
// "possible" signal doesn't depend on a fresh injury_status snapshot at
// all, since it isn't relying on injury_status being accurate). ----------
(function () {
  var entries = detect(null, false);
  check("not fresh + not corroborated still yields 'possible' (freshness only gates 'likely')", entries[0].confidence, "possible");
})();

// ---- Not fresh, but WOULD have corroborated "Out" -- without freshness,
// this can't be trusted as "likely", so it must fall back to "possible",
// never silently disappear or silently stay "likely". --------------------
(function () {
  var entries = detect("Out", false);
  check("not fresh + would-be-corroborated 'Out' downgrades to 'possible', not 'likely'", entries[0].confidence, "possible");
})();

// ---- applyQbAdjustmentsToScores: a "possible" entry must NEVER move the
// live Actual/Projected totals, and must NOT populate the per-roster
// asterisk/tooltip map (byRoster) -- that's what keeps the manager-name
// "*" marker and the "Points This Week" replacement row from ever
// appearing for a merely-"possible" case, per Ben's "do NOT apply the
// points from this player... only the commissioner will do that". -------
(function () {
  var entries = detect(null, true); // -> confidence "possible"
  var scoresActual = { 1: 100.0, 2: 90.0 };
  var scoresCustom = { 1: 110.0, 2: 95.0 };
  var byRoster = applyQbAdjustmentsToScores(entries, scoresActual, scoresCustom);
  check("possible tier: Actual total left completely unchanged", scoresActual, { 1: 100.0, 2: 90.0 });
  check("possible tier: Custom/Projected total left completely unchanged", scoresCustom, { 1: 110.0, 2: 95.0 });
  ok("possible tier: no byRoster entry at all (no asterisk, no tooltip)", byRoster[1] === undefined);
})();

// ---- Regression: "likely" still DOES apply its points to both totals and
// DOES populate byRoster (this is the existing, already-shipped behavior
// -- confirming the new three-way branch in applyQbAdjustmentsToScores
// didn't change it). -------------------------------------------------------
(function () {
  var entries = detect("Out", true); // -> confidence "likely", backup_points_total 21.9
  var scoresActual = { 1: 100.0, 2: 90.0 };
  var scoresCustom = { 1: 110.0, 2: 95.0 };
  var byRoster = applyQbAdjustmentsToScores(entries, scoresActual, scoresCustom);
  ok("likely tier: Actual total increased by 21.9", Math.abs(scoresActual[1] - 121.9) < 1e-9);
  ok("likely tier: Custom total increased by 21.9", Math.abs(scoresCustom[1] - 131.9) < 1e-9);
  ok("likely tier: byRoster entry exists (asterisk + tooltip still show)", byRoster[1] !== undefined);
  check("likely tier: byRoster confidence", byRoster[1].confidence, "likely");
})();

// ---- A commissioner override (custom_points set) ALWAYS wins as
// "confirmed", regardless of what the possible/likely detection would
// otherwise have said -- and confirmed's own delta IS applied. ------------
(function () {
  var matchupsWithOverride = [Object.assign({}, MATCHUPS[0], { custom_points: 129.1 }), MATCHUPS[1]];
  var meta = playersMeta(null); // uncorroborated -- would be "possible" without the override
  var entries = detectQbAdjustmentsForWeek(2, matchupsWithOverride, meta, buildTeamQbIndex(meta), STATS, SCORING, MANAGERS, true);
  check("a set custom_points always wins as 'confirmed'", entries[0].confidence, "confirmed");
  var scoresActual = { 1: 100.0, 2: 90.0 };
  var scoresCustom = { 1: 110.0, 2: 95.0 };
  var byRoster = applyQbAdjustmentsToScores(entries, scoresActual, scoresCustom);
  ok("confirmed tier: Actual total increased by the official delta (29.1)", Math.abs(scoresActual[1] - 129.1) < 1e-9);
  ok("confirmed tier: byRoster entry exists", byRoster[1] !== undefined);
})();

console.log(failures ? "\n" + failures + " FAILURE(S)" : "\nALL QB-adjustment detection/scoring UNIT TESTS PASSED");
process.exit(failures ? 1 : 0);
