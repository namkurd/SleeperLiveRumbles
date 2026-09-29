// Standalone unit test for small tooltip details in rumbles.html, run with
// `node test/test_tooltip_details.js`:
//   - posBadgeHtml: the color-coded position tag in the points tooltips
//   - buildQbAdjCapturedTooltipHtml: the "when was the status change to Out
//     logged" tooltip on a Likely row's Injured QB name
//   - buildQbAdjTooltipHtml: the manager "QB Inj*" tooltip's logged-time line
// Same approach as the other JS tests: regex-extract the real shipped
// functions and eval them in an isolated VM context.
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
  extract(/function escapeHtml\([^)]*\) \{[\s\S]*?\n  \}/, "escapeHtml"),
  extract(/function isOutStatus\([^)]*\) \{[\s\S]*?\n  \}/, "isOutStatus"),
  extract(/function joinNamesForSentence\([^)]*\) \{[\s\S]*?\n  \}/, "joinNamesForSentence"),
  extract(/function formatCapturedAt\([^)]*\) \{[\s\S]*?\n  \}/, "formatCapturedAt"),
  extract(/function posBadgeHtml\([^)]*\) \{[\s\S]*?\n  \}/, "posBadgeHtml"),
  extract(/function buildQbAdjCapturedTooltipHtml\([^)]*\) \{[\s\S]*?\n  \}/, "buildQbAdjCapturedTooltipHtml"),
  extract(/function buildQbAdjTooltipHtml\([^)]*\) \{[\s\S]*?\n  \}/, "buildQbAdjTooltipHtml"),
].join("\n");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(source + "\nthis.posBadgeHtml = posBadgeHtml; this.cap = buildQbAdjCapturedTooltipHtml; this.adj = buildQbAdjTooltipHtml; this.fmt = formatCapturedAt;", sandbox);

let failures = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) console.log("PASS: " + label);
  else { failures++; console.log("FAIL: " + label + "\n  expected: " + e + "\n  got:      " + a); }
}
function ok(label, cond) {
  if (cond) console.log("PASS: " + label); else { failures++; console.log("FAIL: " + label); }
}

// ---- Position tags --------------------------------------------------------
["QB", "RB", "WR", "TE", "K", "DEF"].forEach(function (pos) {
  check(pos + " gets its own color class", sandbox.posBadgeHtml(pos), '<span class="pos-badge pos-' + pos + '">' + pos + "</span>");
});
check("lowercase position is normalized", sandbox.posBadgeHtml("wr"), '<span class="pos-badge pos-WR">WR</span>');
check("an unknown position still shows, in the neutral style", sandbox.posBadgeHtml("LB"), '<span class="pos-badge">LB</span>');
check("no position renders nothing", sandbox.posBadgeHtml(null), "");
const css = html;
[["QB", "#dc2626"], ["RB", "#16a34a"], ["WR", "#2563eb"], ["TE", "#eab308"], ["K", "#9333ea"], ["DEF", "#8b4513"]].forEach(function (pair) {
  ok(pair[0] + " tag is " + pair[1], new RegExp("\\.pos-" + pair[0] + " \\{ background: " + pair[1]).test(css));
});

// ---- "When was it logged" tooltip on a Likely row -------------------------
const ts = "2026-10-04T18:42:00Z";
const logged = sandbox.cap({ injured_qb: { name: "Baker Mayfield" }, captured_at: ts });
ok("logged row names the QB and says his status changed to Out during the game", logged.indexOf("Baker Mayfield's status changed to Out during the game.") !== -1);
ok("logged row shows the logged time", logged.indexOf("Logged " + sandbox.fmt(ts) + ".") !== -1);
const pending = sandbox.cap({ injured_qb: { name: "Baker Mayfield" }, captured_at: null });
ok("not-yet-logged row says the time will be logged shortly", pending.indexOf("The time will be logged shortly.") !== -1);
ok("not-yet-logged row never shows a time", pending.indexOf("Logged ") === -1);

// ---- Manager "QB Inj*" tooltip ----------------------------------------------
const base = { delta: 12.5, confidence: "likely", injured_qb: { name: "Baker Mayfield" }, backup_qbs: [{ name: "Kyle Trask", injury_status: null }] };
const withTime = sandbox.adj(Object.assign({}, base, { captured_at: ts }));
ok("QB Inj tooltip includes the logged time when captured", withTime.indexOf("Status change logged " + sandbox.fmt(ts) + ".") !== -1);
const noTime = sandbox.adj(base);
ok("QB Inj tooltip has no logged-time line without a capture", noTime.indexOf("Status change logged") === -1);

if (failures) { console.log("\n" + failures + " FAILURE(S)"); process.exit(1); }
console.log("\nALL tooltip detail UNIT TESTS PASSED");
