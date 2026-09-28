// Standalone unit test for rumbles.html's week-by-week standings rebuild
// (computeStandingsAsOf), which backs the Actual tab's week picker and the
// "#" column's movement arrows. Run with `node test/test_standings_history.js`.
// Same approach as test_h2h_streak.js: regex-extract the real shipped
// functions from rumbles.html and eval them in an isolated VM context.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const html = fs.readFileSync(path.join(__dirname, "..", "rumbles.html"), "utf8");
function extract(pattern, label) {
  const m = html.match(pattern);
  if (!m) throw new Error("Couldn't find " + label + " in rumbles.html");
  return m[0];
}
const source = [
  extract(/function num\([^)]*\) \{[^\n]*\}/, "num"),
  extract(/function round2\([^)]*\) \{[^\n]*\}/, "round2"),
  extract(/function computeStandingsAsOf\([^)]*\) \{[\s\S]*?\n  \}/, "computeStandingsAsOf"),
].join("\n");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(source + "\nthis.computeStandingsAsOf = computeStandingsAsOf;", sandbox);
const { computeStandingsAsOf } = sandbox;

let failures = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) console.log("PASS: " + label);
  else { failures++; console.log("FAIL: " + label + "\n  expected: " + e + "\n  got:      " + a); }
}

function entry(points, opp, oppPts, win, outscored, outscoredBy, rumbles) {
  return { points, opponent_roster_id: opp, opponent_points: oppPts, h2h_win: win, teams_outscored: outscored, teams_outscored_by: outscoredBy, rumbles };
}

// Three teams, two weeks. Week 1: A beats B, C bye-ish (no h2h). Week 2: C
// beats A, B idle. Built so the leader after Week 1 (A) drops after Week 2.
const HISTORY = {
  weeks_completed: [1, 2],
  standings: [{ roster_id: 1, manager: "A" }, { roster_id: 2, manager: "B" }, { roster_id: 3, manager: "C" }],
  weekly: {
    "1": { "1": entry(120, 2, 100, true, 2, 0, 11), "2": entry(100, 1, 120, false, 1, 1, 1), "3": entry(90, null, null, null, 0, 2, 0) },
    "2": { "1": entry(80, 3, 150, false, 0, 2, 0), "2": entry(110, null, null, null, 1, 1, 1), "3": entry(150, 1, 80, true, 2, 0, 11) },
  },
};

(function () {
  const w1 = computeStandingsAsOf(HISTORY, 1);
  check("after week 1: ranks A, B, C", [w1.rankByRoster[1], w1.rankByRoster[2], w1.rankByRoster[3]], [1, 2, 3]);
  check("after week 1: A totals", [w1.byRoster[1].rumbles, w1.byRoster[1].pf, w1.byRoster[1].h2h_w, w1.byRoster[1].h2h_l], [11, 120, 1, 0]);
  check("after week 1: weekEntry is week 1's own row", w1.byRoster[2].weekEntry.points, 100);
  check("after week 1: a null h2h_win counts as neither W nor L", [w1.byRoster[3].h2h_w, w1.byRoster[3].h2h_l], [0, 0]);

  const w2 = computeStandingsAsOf(HISTORY, 2);
  // A 11 Rumbles/200 PF, C 11/240, B 2/210 -> C first on the PF tiebreak.
  check("after week 2: C passes A on the PF tiebreak", [w2.rankByRoster[3], w2.rankByRoster[1], w2.rankByRoster[2]], [1, 2, 3]);
  check("after week 2: cumulative PA", w2.byRoster[1].pa, 250);
  check("after week 2: vs field record", [w2.byRoster[3].vs_field_w, w2.byRoster[3].vs_field_l], [2, 2]);
  check("rank change A (1 -> 2) is -1", w1.rankByRoster[1] - w2.rankByRoster[1], -1);
  check("rank change C (3 -> 1) is +2", w1.rankByRoster[3] - w2.rankByRoster[3], 2);

  check("before any completed week returns null", computeStandingsAsOf(HISTORY, 0), null);
  check("missing history returns null", computeStandingsAsOf(null, 1), null);
})();

// Cross-check against the real, committed rumbles_history.json: summing
// every completed week must reproduce the server's own standings.
(function () {
  const real = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "rumbles_history.json"), "utf8"));
  const last = Math.max.apply(null, real.weeks_completed);
  const asOf = computeStandingsAsOf(real, last);
  let mismatches = 0;
  real.standings.forEach(function (st) {
    const t = asOf.byRoster[st.roster_id];
    const same = t.rumbles === st.rumbles && Math.abs(t.pf - st.pf) < 0.01 && Math.abs(t.pa - st.pa) < 0.01 &&
      t.h2h_w === st.h2h_w && t.h2h_l === st.h2h_l && t.vs_field_w === st.vs_field_w && t.vs_field_l === st.vs_field_l &&
      asOf.rankByRoster[st.roster_id] === st.rank;
    if (!same) { mismatches++; console.log("  mismatch for " + st.manager, t, st); }
  });
  check("real rumbles_history.json: rebuilt standings match the server's for every team", mismatches, 0);
})();

if (failures) { console.log("\n" + failures + " FAILURE(S)"); process.exit(1); }
console.log("\nALL computeStandingsAsOf UNIT TESTS PASSED");
