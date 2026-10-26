// Unit test for the win-probability engine in rumbles.html (the block between WP-ENGINE-START and WP-ENGINE-END):
// the game-clock lookup, the probability model, the squeezed time axis and the biggest-swing finder.
// Run with `node test/test_wp_engine.js`; it extracts the real shipped code, no browser or server needed.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const html = fs.readFileSync(path.join(__dirname, "..", "rumbles.html"), "utf8");

function extract(pattern, label) {
  const m = html.match(pattern);
  if (!m) throw new Error("Couldn't find " + label + " in rumbles.html -- has it moved or been renamed?");
  return m[0];
}
const source = [
  "function num(v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; }",
  extract(/var PACE_DAMPENING_FLOOR = [^;]+;/, "PACE_DAMPENING_FLOOR"),
  extract(/var PACE_DAMPENING_K = [^;]+;/, "PACE_DAMPENING_K"),
  extract(/var PACE_DAMPENING_Q4_THRESHOLD = [^;]+;/, "PACE_DAMPENING_Q4_THRESHOLD"),
  extract(/var PACE_DAMPENING_FLOOR_Q4 = [^;]+;/, "PACE_DAMPENING_FLOOR_Q4"),
  extract(/var PACE_DAMPENING_K_Q4 = [^;]+;/, "PACE_DAMPENING_K_Q4"),
  extract(/var PACE_DAMPENING_Q1_THRESHOLD = [^;]+;/, "PACE_DAMPENING_Q1_THRESHOLD"),
  extract(/var PACE_DAMPENING_FLOOR_Q1 = [^;]+;/, "PACE_DAMPENING_FLOOR_Q1"),
  extract(/var PACE_DAMPENING_K_Q1 = [^;]+;/, "PACE_DAMPENING_K_Q1"),
  extract(/function blendedProjection\([^)]*\) \{[\s\S]*?\n  \}/, "blendedProjection"),
  extract(/var WIN_SD_C = [^;]+;/, "WIN_SD_C"),
  extract(/function rbNormCdf\([^)]*\) \{[\s\S]*?\n  \}/, "rbNormCdf"),
  extract(/\/\/ WP-ENGINE-START[\s\S]*?\/\/ WP-ENGINE-END/, "the win-probability engine"),
].join("\n");
const sb = {};
vm.createContext(sb);
vm.runInContext(source + "\nthis.E = { wpRem, wpClockLabel, wpBuild, wpMarks, wpNotches, wpWindows, wpScrub, wpDot, wpDone };", sb);
const { wpRem, wpClockLabel, wpBuild, wpMarks, wpNotches, wpWindows, wpScrub, wpDot, wpDone } = sb.E;

let failures = 0;
function check(name, ok, detail) { if (ok) console.log("  ok  " + name); else { failures++; console.log("  FAIL " + name + (detail ? " -- " + detail : "")); } }

// Two games: A (Thursday-ish, first) and B (three days later). Mine: QB "a1" (game A) and WR "b1" (game B); theirs: RB "a2" (A), DEF "BBB" (B).
const DAY = 86400;
const doc = {
  v: 1, t0: 1000000, g: {
    "AAA@AA2": { ko: 0, end: 11000, clk: [[0, 1000], [3000, 750], [6000, 500], [9000, 250], [11000, 0]] },
    "BBB@BB2": { ko: 3 * DAY, end: 3 * DAY + 11000, clk: [[3 * DAY, 1000], [3 * DAY + 5500, 500], [3 * DAY + 11000, 0]] },
  },
  tg: { AAA: "AAA@AA2", AA2: "AAA@AA2", BBB: "BBB@BB2", BB2: "BBB@BB2" },
  tm: { a1: "AAA", b1: "BBB", a2: "AA2", BBB: "BBB" },
  pre: { a1: 20, b1: 12, a2: 15, BBB: 8 },
  ev: [
    [1000, "a1", 4.0, "50 yd TD pass"],
    [1001, "a2", 6.0, "20 yd TD run"],          // lands with the one above: one swing
    [6500, "a1", 12.0, "75 yd TD pass"],
    [3 * DAY + 2000, "b1", 3.0, "30 yd catch"],
    [3 * DAY + 11000, "BBB", 5.0, "7 points, 280 yards allowed"],
  ],
};
const mine = [{ sid: "a1", pos: "QB" }, { sid: "b1", pos: "WR" }], theirs = [{ sid: "a2", pos: "RB" }, { sid: "BBB", pos: "DEF" }];

console.log("game clock");
check("before kickoff the whole game is left", wpRem(doc, "a1", -10) === 1);
check("steps with the clock samples", wpRem(doc, "a1", 3500) === 0.75 && wpRem(doc, "a1", 9500) === 0.25);
check("nothing left once the game ends", wpRem(doc, "a1", 11000) === 0 && wpRem(doc, "b1", 3 * DAY + 20000) === 0);
check("a player with no known game waits for the week to end", wpRem(doc, "zz", 5) === 1 && wpRem(doc, "zz", 3 * DAY + 11000) === 0);
check("clock label", wpClockLabel(1) === "Pregame" && wpClockLabel(0) === "Final" && wpClockLabel(0.5) === "Q3 15:00", wpClockLabel(0.5));
check("mid-game label", wpClockLabel(0.4) === "Q3 9:00" && wpClockLabel(0.1) === "Q4 6:00", wpClockLabel(0.4) + " / " + wpClockLabel(0.1));
check("a finished doc is done", wpDone(doc));

console.log("model");
const m = wpBuild(doc, mine, theirs, {});
check("a model is built", !!m && m.T.length > 50);
const p0 = m.at(-1).p;
check("pregame favours the side with more projection (32 vs 23)", p0 > 0.6 && p0 < 0.95, String(p0));
const fin = m.at(3 * DAY + 11000);
check("ends exactly on the real result (mine 4+12+3=19, theirs 6+5=11)", fin.p === 1 && Math.abs(fin.my - 19) < 1e-9 && Math.abs(fin.th - 11) < 1e-9, JSON.stringify(fin));
check("every point is a probability", m.P.every((p) => p >= 0 && p <= 1));
check("times ascend", m.T.every((t, i) => !i || t >= m.T[i - 1]));
check("a long idle stretch is squeezed (3 days become about the length of the games)", m.L < 2 * 11500 + 20 * 60, String(m.L));
check("x runs 0..1 and is monotonic", m.x(m.start) === 0 && m.x(m.end) === 1 && m.x(5000) < m.x(3 * DAY + 10));
check("tOf inverts x inside a block", Math.abs(m.tOf(m.x(7000)) - 7000) <= 1);
const early = m.at(1001), before = m.at(999);
check("a touchdown for each side nets a small move", Math.abs(early.p - before.p) < 0.2, early.p + " vs " + before.p);
const big = m.at(6500).p - m.at(6499).p;
check("a 12-point play for me is a big positive swing", big > 0.05, String(big));

console.log("a week still in progress");
const live = JSON.parse(JSON.stringify(doc));
live.g["BBB@BB2"].end = null; live.g["BBB@BB2"].clk = [[3 * DAY, 1000]]; live.ev.pop();
const lm = wpBuild(live, mine, theirs, { now: 3 * DAY + 100 });
check("stops at now", lm.end === 3 * DAY + 100 && lm.T[lm.T.length - 1] === lm.end);
check("the axis still includes the unfinished game", lm.blocks.length === 2 && lm.blocks[1].b - lm.blocks[1].a >= 11000);
check("not done", !wpDone(live));

console.log("biggest swings");
const names = { a1: "Quinn", a2: "Rex", b1: "Bo", BBB: "Bears" };
const marks = wpMarks(doc, m, names, {});
check("finds the big plays, ranked", marks.length >= 1 && marks[0].rank === 1 && Math.abs(marks[0].d) >= Math.abs(marks[marks.length - 1].d), JSON.stringify(marks.map((x) => [x.label, x.d.toFixed(2)])));
check("the 75-yard TD is the biggest", marks[0].label === "75 yd TD pass" && marks[0].who === "Quinn" && marks[0].side === 1, JSON.stringify(marks[0]));
check("plays within 150s of each other are one swing", !marks.some((x) => x.t0 === 1000 && x.n === 1), JSON.stringify(marks.filter((x) => x.t0 === 1000)));
check("at most five", wpMarks(doc, m, names, { min: 0, max: 5 }).length <= 5);
const notches = wpNotches(m);
check("one notch per scoring play (a TD pass and its catch are one)", notches.length === 4 && notches.every((n, i) => !i || n.t >= notches[i - 1].t), JSON.stringify(notches.map((n) => [n.t, n.ev.length])));
check("a notch lists its plays, biggest first, and whose they were", notches[0].t === 1000 && notches[0].ev.length === 2 && notches[0].ev[0].sid === "a2" && notches[0].side === -1, JSON.stringify(notches[0]));
const small = Object.assign({}, doc, { ev: doc.ev.concat([[2000, "a1", 0.4, "2 yd catch"], [2300, "a2", -0.3, "short loss"]]) });
check("a small play that barely moves the odds gets no notch", wpNotches(wpBuild(small, mine, theirs, {})).length === 4, String(wpNotches(wpBuild(small, mine, theirs, {})).length));
check("more than 2 points is always a notch", wpNotches(wpBuild(Object.assign({}, doc, { ev: doc.ev.concat([[2500, "a1", 2.5, "x"]]) }), mine, theirs, {})).length === 5);
check("end-of-game corrections are not notches", wpNotches(wpBuild(Object.assign({}, doc, { ev: doc.ev.concat([[11000, "a1", 1.5, "scoring adjustment"]]) }), mine, theirs, {})).length === 4);
check("a notch has its place on the axis", notches.every((n) => n.x >= 0 && n.x <= 1) && notches[0].x < notches[3].x);
const on = wpScrub(m, notches, notches[2].x + 0.002, 300, 6), off = wpScrub(m, notches, 0.5 * (notches[1].x + notches[2].x), 300, 6);
check("scrubbing near a notch snaps to its play", on.notch && on.notch.t === notches[2].t && on.t === notches[2].t, JSON.stringify(on.notch && on.notch.t));
check("anywhere else is just a moment (no plays)", off.notch === null && off.t > notches[1].t && off.t < notches[2].t, JSON.stringify(off));
check("the end of the line is the furthest you can scrub", wpScrub(m, notches, 1, 300, 6).t <= m.end);
const wins = wpWindows(doc);
check("kickoff windows: one per cluster of games", wins.length === 2 && wins[0].t === 0 && wins[1].t === 3 * DAY, JSON.stringify(wins));
check("games kicking off within 90 minutes share a window", wpWindows({ g: { a: { ko: 0 }, b: { ko: 4000 }, c: { ko: 13000 } } }).length === 2);

console.log("the marker on the bar");
check("47 to 53 is a coin flip", [47, 50, 53].every((w) => wpDot(w).coin) && !wpDot(46).coin && !wpDot(54).coin);
check("whoever is ahead leads: you above 50, they below", wpDot(60).lead === 1 && wpDot(40).lead === -1 && wpDot(50).lead === 1);
check("it slides toward the leader's end as their chance grows (yours on the left)", wpDot(60).pos < wpDot(54).pos && wpDot(80).pos < wpDot(60).pos && wpDot(40).pos > wpDot(46).pos && wpDot(20).pos > wpDot(40).pos);
check("dead centre at 50, and never off the ends of the bar", wpDot(50).pos === 50 && wpDot(100).pos === 8 && wpDot(0).pos === 92 && wpDot(99).pos >= 8);
check("it is where the win chance says: 70% for you is 30% along from your end", wpDot(70).pos === 30 && wpDot(25).pos === 75);

if (failures) { console.log("\n" + failures + " FAILED"); process.exit(1); }
console.log("\nALL win-probability engine UNIT TESTS PASSED");
