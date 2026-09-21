// Standalone unit test for rumbles.html's QB-injury manager-marker tooltip
// wording (buildQbAdjTooltipHtml), run directly with
// `node test/test_qb_adj_tooltip.js` -- no browser, no fixtures, no
// server needed.
//
// Why this exists as its own thing rather than living inside run_test.py's
// Playwright fixture: the "a replacement QB is ALSO injured mid-game"
// chain scenario needs its own NFL team + backup(s) with their own
// injury_status, and threading that through make_fixtures.py's
// roster_players/matchups/stats/players plumbing without disturbing
// roster 6's existing (heavily asserted-against) Alex/Kyler-Murray/
// Carson-Wentz fixture, or any other roster's already-passing checks,
// is a lot of fixture surface for what's really pure string-building
// logic with no DOM/live-fetch involvement at all. Instead, this file
// regex-extracts buildQbAdjTooltipHtml (and the small helpers it calls --
// isOutStatus, joinNamesForSentence, escapeHtml) straight out of
// rumbles.html's real source and evals them in isolation, so what's
// tested is the actual shipped code, not a hand-copied reimplementation
// that could quietly drift from it. The single-backup, no-chain case is
// still covered end-to-end by run_test.py's Playwright scenario (Alex's
// tooltip) -- this file covers the chain branches that scenario doesn't
// exercise.
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
  extract(/function escapeHtml\([^)]*\) \{[\s\S]*?\n  \}/, "escapeHtml"),
  extract(/function isOutStatus\([^)]*\) \{[\s\S]*?\n  \}/, "isOutStatus"),
  extract(/function joinNamesForSentence\([^)]*\) \{[\s\S]*?\n  \}/, "joinNamesForSentence"),
  extract(/function buildQbAdjTooltipHtml\([^)]*\) \{[\s\S]*?\n  \}/, "buildQbAdjTooltipHtml"),
].join("\n");

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(source + "\nthis.buildQbAdjTooltipHtml = buildQbAdjTooltipHtml;", sandbox);
const { buildQbAdjTooltipHtml } = sandbox;

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

// Pulls just the plain-text lines (the <br>-joined body, stripped of the
// leading "<strong>...</strong>" heading and the yellow-points/pending
// lines every case shares) so each check below only has to state what's
// actually distinctive about it.
function bodyLines(html) {
  const withoutHeading = html.replace(/^<strong>[^<]*<\/strong><br>/, "");
  return withoutHeading.split("<br>");
}

// ---- existing single-backup, no-chain case (matches run_test.py's Alex
// scenario -- included here too so a regression in the shared helpers
// shows up in both places, not just the slower Playwright run) ----------
check(
  "single healthy backup -- no chain",
  bodyLines(buildQbAdjTooltipHtml({
    injured_qb: { name: "Kyler Murray" },
    backup_qbs: [{ name: "Carson Wentz", points: 21.9, injury_status: null }],
    delta: 21.9,
    team_qb_count: 3,
  })),
  [
    "Kyler Murray was injured in-game and ruled out this week.",
    "Carson Wentz came in; their points will be credited to this roster.",
    '<span class="qb-adj-tooltip-yellow">+21.90 pts</span> added to Actual &amp; Projected.',
    "Pending the commissioner&rsquo;s official adjustment.",
  ]
);

// ---- multiple healthy backups (pluralized "are") -----------------------
check(
  "multiple healthy backups -- pluralized, no chain",
  bodyLines(buildQbAdjTooltipHtml({
    injured_qb: { name: "Starter One" },
    backup_qbs: [
      { name: "Backup A", points: 10, injury_status: null },
      { name: "Backup B", points: 5, injury_status: "Questionable" },
    ],
    delta: 15,
    team_qb_count: 3,
  })),
  [
    "Starter One was injured in-game and ruled out this week.",
    "Backup A, Backup B came in; their points will be credited to this roster.",
    '<span class="qb-adj-tooltip-yellow">+15.00 pts</span> added to Actual &amp; Projected.',
    "Pending the commissioner&rsquo;s official adjustment.",
  ]
);

// ---- no backup identified at all (fallback wording) ---------------------
check(
  "no identifiable backup -- fallback wording",
  bodyLines(buildQbAdjTooltipHtml({
    injured_qb: { name: "Starter One" },
    backup_qbs: [],
    delta: 12,
    team_qb_count: null,
  })),
  [
    "Starter One was injured in-game and ruled out this week.",
    "A backup QB came in; their points will be credited to this roster.",
    '<span class="qb-adj-tooltip-yellow">+12.00 pts</span> added to Actual &amp; Projected.',
    "Pending the commissioner&rsquo;s official adjustment.",
  ]
);

// ---- CHAIN: the replacement is ALSO injured, and a further backup steps
// in -- exactly Ben's described scenario, just with someone left to name.
check(
  "chain: backup also injured, a further backup steps in",
  bodyLines(buildQbAdjTooltipHtml({
    injured_qb: { name: "Starter One" },
    backup_qbs: [
      { name: "Backup One", points: 8, injury_status: "Out" },
      { name: "Backup Two", points: 14, injury_status: null },
    ],
    delta: 22,
    team_qb_count: 3,
  })),
  [
    "Starter One was injured in-game and ruled out this week.",
    "Backup One was also injured in-game and ruled out this week.",
    "Backup Two came in; their points will be credited to this roster.",
    '<span class="qb-adj-tooltip-yellow">+22.00 pts</span> added to Actual &amp; Projected.',
    "Pending the commissioner&rsquo;s official adjustment.",
  ]
);

// ---- CHAIN: the replacement is ALSO injured, MULTIPLE further backups
// step in (pluralized).
check(
  "chain: backup also injured, multiple further backups (pluralized)",
  bodyLines(buildQbAdjTooltipHtml({
    injured_qb: { name: "Starter One" },
    backup_qbs: [
      { name: "Backup One", points: 8, injury_status: "IR" },
      { name: "Backup Two", points: 14, injury_status: null },
      { name: "Backup Three", points: 3, injury_status: null },
    ],
    delta: 25,
    team_qb_count: 4,
  })),
  [
    "Starter One was injured in-game and ruled out this week.",
    "Backup One was also injured in-game and ruled out this week.",
    "Backup Two, Backup Three came in; their points will be credited to this roster.",
    '<span class="qb-adj-tooltip-yellow">+25.00 pts</span> added to Actual &amp; Projected.',
    "Pending the commissioner&rsquo;s official adjustment.",
  ]
);

// ---- CHAIN, exhausted: only 2 QBs on the team's depth chart at all
// (the starter plus this one backup), and that backup is ALSO hurt now --
// per Ben's own example, nobody else is even eligible.
check(
  "chain exhausted: only 2 QBs on the depth chart -- nobody left",
  bodyLines(buildQbAdjTooltipHtml({
    injured_qb: { name: "Starter One" },
    backup_qbs: [
      { name: "Backup One", points: 9, injury_status: "Out" },
    ],
    delta: 9,
    team_qb_count: 2,
  })),
  [
    "Starter One was injured in-game and ruled out this week.",
    "Backup One was also injured in-game and ruled out this week.",
    "No other QB was available to step in.",
    '<span class="qb-adj-tooltip-yellow">+9.00 pts</span> added to Actual &amp; Projected.',
    "Pending the commissioner&rsquo;s official adjustment.",
  ]
);

// ---- CHAIN, exhausted but the depth chart has MORE names listed --
// they just haven't recorded any action, so there's still nothing more
// to credit, but the wording says so accurately (not "unavailable",
// since as far as this page can tell they could still come in -- they
// just haven't yet).
check(
  "chain exhausted: more QBs listed but none have recorded action",
  bodyLines(buildQbAdjTooltipHtml({
    injured_qb: { name: "Starter One" },
    backup_qbs: [
      { name: "Backup One", points: 9, injury_status: "Out" },
    ],
    delta: 9,
    team_qb_count: 4,
  })),
  [
    "Starter One was injured in-game and ruled out this week.",
    "Backup One was also injured in-game and ruled out this week.",
    "No other QB has recorded action to step in.",
    '<span class="qb-adj-tooltip-yellow">+9.00 pts</span> added to Actual &amp; Projected.',
    "Pending the commissioner&rsquo;s official adjustment.",
  ]
);

// ---- Names get HTML-escaped same as everywhere else on the page --------
check(
  "names are HTML-escaped",
  bodyLines(buildQbAdjTooltipHtml({
    injured_qb: { name: "A & B" },
    backup_qbs: [{ name: 'C <D>', points: 5, injury_status: null }],
    delta: 5,
    team_qb_count: 3,
  }))[0],
  "A &amp; B was injured in-game and ruled out this week."
);

console.log(failures ? "\n" + failures + " FAILURE(S)" : "\nALL buildQbAdjTooltipHtml UNIT TESTS PASSED");
process.exit(failures ? 1 : 0);
