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
  extract(/function buildLiveCapturesLookup\([^)]*\) \{[\s\S]*?\n  \}/, "buildLiveCapturesLookup"),
].join("\n");

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(
  source + "\nthis.detectQbAdjustmentsForWeek = detectQbAdjustmentsForWeek;" +
    "\nthis.applyQbAdjustmentsToScores = applyQbAdjustmentsToScores;" +
    "\nthis.buildTeamQbIndex = buildTeamQbIndex;" +
    "\nthis.findBackupQbs = findBackupQbs;" +
    "\nthis.buildLiveCapturesLookup = buildLiveCapturesLookup;",
  sandbox
);
const { detectQbAdjustmentsForWeek, applyQbAdjustmentsToScores, buildTeamQbIndex, findBackupQbs, buildLiveCapturesLookup } = sandbox;

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

// Default schedule for these fixtures: MIN's (and KC's) game still live --
// most existing tests below are about isFresh/backup-detection, not the
// game-completion gate, so they should keep behaving exactly as before
// unless a test explicitly passes a "complete" schedule to exercise that
// gate on its own.
const IN_PROGRESS_SCHEDULE = { MIN: { status: "in_progress" }, KC: { status: "in_progress" }, CHI: { status: "in_progress" } };
const COMPLETE_SCHEDULE = { MIN: { status: "complete" }, KC: { status: "complete" }, CHI: { status: "complete" } };

function detect(starterStatus, isFresh, schedule, liveCaptures) {
  var meta = playersMeta(starterStatus);
  return detectQbAdjustmentsForWeek(2, MATCHUPS, meta, buildTeamQbIndex(meta), STATS, SCORING, MANAGERS, isFresh, schedule || IN_PROGRESS_SCHEDULE, liveCaptures);
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
  check("possible tier: captured_at is null (no durable capture log passed in)", e.captured_at, null);
})();

// ---- Also fires for a real-but-non-out status ("Questionable") -- not
// just a missing one. -----------------------------------------------------
(function () {
  var entries = detect("Questionable", true);
  check("possible tier fires for 'Questionable' too", entries[0].confidence, "possible");
})();

// ---- "likely" still fires (unaffected regression check) when the status
// DOES corroborate out/IR/PUP -- game still in progress. -------------------
(function () {
  var entries = detect("Out", true);
  check("'Out' still yields 'likely', not 'possible'", entries[0].confidence, "likely");
})();

// ---- REGRESSION: the real Baker Mayfield case (Week 3 2026) -- a same-
// team backup played, and injury_status now reads "Out" fresh, but the
// started QB's own game has already gone FINAL. Sleeper doesn't flip
// injury_status in real time off what happens on the field; that only
// updates from the team's post-game injury report, often well after the
// final whistle -- there was no "Out" designation anywhere before or
// during this actual game. An "Out" status that only appears once the
// game is already complete must NOT be trusted as live corroboration, so
// this must stay "possible", not jump to "likely". ------------------------
(function () {
  var entries = detect("Out", true, COMPLETE_SCHEDULE);
  check("'Out' observed only after the game went final downgrades to 'possible', not 'likely'", entries[0].confidence, "possible");
})();

// ---- Same case, but pre-game. The rule covers in-game injuries only, so
// an "Out" seen before kickoff is not live corroboration. (In practice a
// starter who has already recorded stats can't be pre_game, but the gate
// itself must still only accept in_progress.) -----------------------------
(function () {
  var entries = detect("Out", true, { MIN: { status: "pre_game" }, KC: { status: "pre_game" } });
  check("'Out' observed pre-game does not yield 'likely' (in-game injuries only)", entries[0].confidence, "possible");
})();

// ---- A pregame inactive: the started QB never took the field (no stats
// of his own) and a same-team backup played instead. That is not an
// in-game injury, so nothing is logged at any tier, live game or not. ------
(function () {
  var meta = playersMeta("Out");
  var stats = Object.assign({}, STATS);
  delete stats.QB_STARTER;
  var inProg = detectQbAdjustmentsForWeek(2, MATCHUPS, meta, buildTeamQbIndex(meta), stats, SCORING, MANAGERS, true, IN_PROGRESS_SCHEDULE, null);
  check("starter who never played (pregame inactive) logs nothing, even with 'Out' and a live game", inProg.length, 0);
  var captured = detectQbAdjustmentsForWeek(2, MATCHUPS, meta, buildTeamQbIndex(meta), stats, SCORING, MANAGERS, true, IN_PROGRESS_SCHEDULE, { "2:1:QB_STARTER": "2026-09-28T20:00:00Z" });
  check("starter who never played logs nothing even if a capture-log entry exists", captured.length, 0);
})();

// ---- No schedule info at all for this team (unknown/missing game status)
// must fail safe to NOT corroborating -- same reasoning as "complete":
// without positive confirmation the game is still live, an Out status
// can't be trusted as caught live. ------------------------------------------
(function () {
  var entries = detect("Out", true, {});
  check("missing schedule info for the team downgrades to 'possible', not 'likely'", entries[0].confidence, "possible");
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
  check("confirmed tier: captured_at is always null (a commissioner override doesn't need a live-capture timestamp)", entries[0].captured_at, null);
})();

// ---- REGRESSION: a durably-captured live case (see LIVE_CAPTURES_URL/
// buildLiveCapturesLookup and capture_live_qb_injuries.py) must be trusted
// as "likely" -- WITH its real captured_at timestamp threaded onto the
// entry -- even when THIS poll's own isFresh/game-status snapshot would
// otherwise downgrade it to "possible" (game already complete, or not the
// one fresh day). This is the actual fix for the real gap Ben found: a
// genuinely-live-caught injury (Out status seen while the game was still
// in progress) had no way to stay "likely" once the game ended on the very
// next 30-second poll, and no timestamp to point to either -- see
// detectQbAdjustmentsForWeek's own comment on liveCaptures. -----------------
(function () {
  var liveCaptures = { "2:1:QB_STARTER": "2026-09-27T20:14:03+00:00" };
  var entries = detect("Out", false, COMPLETE_SCHEDULE, liveCaptures);
  ok("a durable capture must still produce an entry even when not fresh and the game is complete", entries.length === 1);
  check("a durable live-capture log entry wins as 'likely' even on a not-fresh, game-complete poll", entries[0].confidence, "likely");
  check("the durable log's own timestamp is carried through onto the entry", entries[0].captured_at, "2026-09-27T20:14:03+00:00");
})();

// ---- Even when the run IS fresh and the game IS still live (the normal
// "likely" path would fire on its own too), a durable capture's timestamp
// must still be attached -- rather than silently taking the same-day
// injury_status path instead and losing the timestamp. ---------------------
(function () {
  var liveCaptures = { "2:1:QB_STARTER": "2026-09-27T20:14:03+00:00" };
  var entries = detect("Out", true, IN_PROGRESS_SCHEDULE, liveCaptures);
  check("a durable capture's timestamp is attached even on a fresh, still-live run", entries[0].captured_at, "2026-09-27T20:14:03+00:00");
})();

// ---- A capture log that exists but doesn't mention this exact
// week:roster:player combo must behave exactly as if no log were provided
// at all -- falls back cleanly to the same-day injury_status check, with
// no captured_at attached. --------------------------------------------------
(function () {
  var liveCaptures = { "2:99:QB_STARTER": "2026-09-27T20:14:03+00:00", "2:1:SOME_OTHER_PLAYER": "2026-09-27T20:14:03+00:00" };
  var entries = detect("Out", true, IN_PROGRESS_SCHEDULE, liveCaptures);
  check("a capture log with no matching key falls back cleanly to the injury_status check", entries[0].confidence, "likely");
  check("a non-matching capture-log entry is never attached to an unrelated roster/player", entries[0].captured_at, null);
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

// ---- buildLiveCapturesLookup: builds a "{week}:{roster_id}:{player_id}"
// lookup from live_qb_captures.json's raw shape, scoped to one season, with
// the same no-season-in-the-key format as build_rumbles.py's
// load_live_qb_captures (season-scoping happens via the filter, not the
// key itself -- see that Python function's own comment). -------------------
(function () {
  var captureDoc = {
    captures: [
      { season: "2026", week: 3, roster_id: 1, injured_qb: { player_id: "QB_STARTER" }, captured_at: "2026-09-27T20:14:03+00:00" },
      // A different season's Week 3 must NEVER collide with 2026's.
      { season: "2025", week: 3, roster_id: 1, injured_qb: { player_id: "QB_STARTER" }, captured_at: "2025-09-28T20:00:00+00:00" },
      // No captured_at at all -- shouldn't happen in practice, but must
      // never produce a bogus lookup entry if it does.
      { season: "2026", week: 4, roster_id: 2, injured_qb: { player_id: "QB_OTHER" }, captured_at: null },
    ],
  };
  var result = buildLiveCapturesLookup(captureDoc, "2026");
  check("buildLiveCapturesLookup builds a season-scoped, no-season-prefix key", result, { "3:1:QB_STARTER": "2026-09-27T20:14:03+00:00" });
})();

(function () {
  var result = buildLiveCapturesLookup(null, "2026");
  check("buildLiveCapturesLookup returns {} for a missing/empty capture doc", result, {});
})();

console.log(failures ? "\n" + failures + " FAILURE(S)" : "\nALL QB-adjustment detection/scoring UNIT TESTS PASSED");
process.exit(failures ? 1 : 0);
