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
  extract(/function findStartedQbs\([^)]*\) \{[\s\S]*?\n  \}/, "findStartedQbs"),
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
    "\nthis.buildTeamQbIndex = buildTeamQbIndex;" +
    "\nthis.findBackupQbs = findBackupQbs;",
  sandbox
);
const { detectQbAdjustmentsForWeek, applyQbAdjustmentsToScores, buildTeamQbIndex, findBackupQbs } = sandbox;

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

// ---- SUPER_FLEX / multi-started-QB regression: this league's real
// roster_positions has TWO SUPER_FLEX slots and no dedicated QB slot, so a
// manager can start two QBs at once. This is the exact real bug report:
// Alex started BOTH Carson Wentz (MIN) and Caleb Williams (CHI) the same
// week; Williams got hurt and Tyson Bagent (also CHI) came in to relieve
// him, but findStartedQb (singular, old code) only ever checked the FIRST
// QB found in `starters` (Wentz, since he came first), so Bagent's case
// was silently never even considered. findStartedQbs (plural) now checks
// every started QB independently. -----------------------------------------
const SUPERFLEX_PLAYERS_META = {
  WENTZ: { position: "QB", team: "MIN", full_name: "Carson Wentz", injury_status: null },
  WILLIAMS: { position: "QB", team: "CHI", full_name: "Caleb Williams", injury_status: null },
  BAGENT: { position: "QB", team: "CHI", full_name: "Tyson Bagent", injury_status: null },
  MIN_OTHER: { position: "QB", team: "MIN", full_name: "MIN 3rd String", injury_status: null }, // didn't play
};
const SUPERFLEX_STATS = {
  WENTZ: { pass_att: 30, pass_yd: 260, pass_td: 2, pass_int: 1 }, // 10.4+8-2 = 16.4, played the whole game, no backup
  WILLIAMS: { pass_att: 15, pass_yd: 100, pass_td: 0, pass_int: 0 }, // 4.0, left early
  BAGENT: { pass_att: 9, pass_yd: 54, pass_td: 0, pass_int: 0 }, // 2.16 -- close to the real 2.31 (real stat line has a couple more scored categories not modeled in this simplified fixture)
};
// Wentz listed FIRST in starters (this is exactly what made the old
// singular findStartedQb miss Williams entirely).
const SUPERFLEX_MATCHUPS = [
  { roster_id: 1, matchup_id: 1, starters: ["WENTZ", "WILLIAMS"], points: 20.4 },
  { roster_id: 2, matchup_id: 1, starters: ["WR2"], points: 90.0 },
];
const SUPERFLEX_MANAGERS = { 1: "Alex", 2: "Ben" };

(function () {
  var teamQbIndex = buildTeamQbIndex(SUPERFLEX_PLAYERS_META);
  var entries = detectQbAdjustmentsForWeek(2, SUPERFLEX_MATCHUPS, SUPERFLEX_PLAYERS_META, teamQbIndex, SUPERFLEX_STATS, SCORING, SUPERFLEX_MANAGERS, true);
  ok("exactly one entry logged -- Wentz (no backup) produces none, only Williams/Bagent does", entries.length === 1);
  var e = entries[0];
  check("the logged entry is about Caleb Williams, not Carson Wentz", e.injured_qb.name, "Caleb Williams");
  check("Tyson Bagent is correctly identified as the backup", e.backup_qbs.map(function (b) { return b.name; }), ["Tyson Bagent"]);
  check("confidence is 'possible' (uncorroborated injury_status)", e.confidence, "possible");
})();

// ---- Same roster, but now BOTH started QBs independently qualify (Wentz
// also picks up a real same-team backup this week) -- both must be logged
// as SEPARATE entries, and each one's backup list must exclude the OTHER
// started QB, not just itself. ---------------------------------------------
(function () {
  var meta = Object.assign({}, SUPERFLEX_PLAYERS_META, {
    MIN_OTHER: Object.assign({}, SUPERFLEX_PLAYERS_META.MIN_OTHER), // still MIN, QB
  });
  var stats = Object.assign({}, SUPERFLEX_STATS, {
    MIN_OTHER: { pass_att: 5, pass_yd: 40, pass_td: 0, pass_int: 0 }, // 1.6 -- now "played" too
  });
  var teamQbIndex = buildTeamQbIndex(meta);
  var entries = detectQbAdjustmentsForWeek(2, SUPERFLEX_MATCHUPS, meta, teamQbIndex, stats, SCORING, SUPERFLEX_MANAGERS, true);
  ok("two separate entries logged for the same roster/week (one per started QB)", entries.length === 2);
  var wentzEntry = entries.find(function (e) { return e.injured_qb.name === "Carson Wentz"; });
  var williamsEntry = entries.find(function (e) { return e.injured_qb.name === "Caleb Williams"; });
  ok("Wentz's own entry exists", !!wentzEntry);
  ok("Williams's own entry exists", !!williamsEntry);
  check("Wentz's backup is the MIN 3rd-stringer, not Williams (a different team anyway) or Bagent", wentzEntry.backup_qbs.map(function (b) { return b.name; }), ["MIN 3rd String"]);
  check("Williams's backup is still just Bagent", williamsEntry.backup_qbs.map(function (b) { return b.name; }), ["Tyson Bagent"]);
  // Both entries' deltas must still land on the SAME roster's totals
  // (applyQbAdjustmentsToScores accumulates via +=, not assignment).
  var scoresActual = { 1: 100.0, 2: 90.0 };
  var scoresCustom = { 1: 110.0, 2: 95.0 };
  applyQbAdjustmentsToScores(entries, scoresActual, scoresCustom);
  ok("both entries are 'possible' (delta 0), so totals are untouched by either", scoresActual[1] === 100.0 && scoresCustom[1] === 110.0);
})();

// ---- A manager deliberately starting TWO QBs from the SAME NFL team
// (both legally started, neither one relieving an injury) must NOT have
// one miscounted as the other's "backup". -----------------------------------
(function () {
  var meta = {
    A: { position: "QB", team: "CHI", full_name: "Started QB A", injury_status: null },
    B: { position: "QB", team: "CHI", full_name: "Started QB B", injury_status: null },
  };
  var stats = {
    A: { pass_att: 20, pass_yd: 150, pass_td: 1, pass_int: 0 },
    B: { pass_att: 18, pass_yd: 140, pass_td: 1, pass_int: 0 },
  };
  var matchups = [{ roster_id: 1, matchup_id: 1, starters: ["A", "B"], points: 20.0 }];
  var teamQbIndex = buildTeamQbIndex(meta);
  var entries = detectQbAdjustmentsForWeek(2, matchups, meta, teamQbIndex, stats, SCORING, { 1: "Alex" }, true);
  ok("neither deliberately-started same-team QB is logged as the other's 'backup'", entries.length === 0);
})();

// ---- A backup QB who played (isPlayed) but scored EXACTLY 0.00 fantasy
// points isn't a meaningful "backup credit" -- excluded from the list.
// A negative total (a pick, a lost fumble) is still a real outing and
// stays listed; only an exact 0.00 is filtered. Mirrors
// test_build_rumbles.py's test_find_backup_qbs_excludes_exactly_zero_point_backups
// / test_find_backup_qbs_keeps_negative_point_backups. -----------------------
(function () {
  var meta = playersMeta(null);
  var zeroStats = Object.assign({}, STATS, {
    QB_BACKUP: { pass_att: 2, pass_yd: 0, pass_td: 0, pass_int: 0 }, // dot-products to 0.00
  });
  var backups = findBackupQbs("QB_STARTER", meta.QB_STARTER, meta, buildTeamQbIndex(meta), zeroStats, SCORING);
  ok("a backup QB who played but scored exactly 0.00 points is excluded from the backup list", backups.length === 0);
})();

(function () {
  var meta = playersMeta(null);
  var negativeStats = Object.assign({}, STATS, {
    QB_BACKUP: { pass_att: 5, pass_yd: 10, pass_td: 0, pass_int: 1 }, // 10*.04 - 2 = -1.6
  });
  var backups = findBackupQbs("QB_STARTER", meta.QB_STARTER, meta, buildTeamQbIndex(meta), negativeStats, SCORING);
  ok("a backup QB with negative (but nonzero) points is still listed", backups.length === 1 && Math.abs(backups[0].points - -1.6) < 1e-9);
})();

// ---- End-to-end: when the ONLY candidate backup scored exactly 0.00, no
// adjustment entry should be logged at all (same as "no backup found"). ----
(function () {
  var meta = playersMeta(null);
  var zeroStats = Object.assign({}, STATS, {
    QB_BACKUP: { pass_att: 1, pass_yd: 0, pass_td: 0, pass_int: 0 },
  });
  var entries = detectQbAdjustmentsForWeek(2, MATCHUPS, meta, buildTeamQbIndex(meta), zeroStats, SCORING, MANAGERS, true);
  ok("no adjustment entry when the only candidate backup QB scored exactly 0.00 points", entries.length === 0);
})();

console.log(failures ? "\n" + failures + " FAILURE(S)" : "\nALL QB-adjustment detection/scoring UNIT TESTS PASSED");
process.exit(failures ? 1 : 0);
