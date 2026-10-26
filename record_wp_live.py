"""Records the in-progress week for the win-probability chart: wp_live/week_N.json.

Sleeper only reports running totals, so a chart of the *current* week can only exist if something watches the
games as they happen. This does, once a minute while any game is live: it notices each starter's points moving,
labels the change from his stat line ("35 yd TD catch"), and keeps the game clocks, so the page can replay the
week and score win probability at every moment. After the week, build_wp_timeline.py replaces this with the
exact play-by-play version (same file format), so a recording only has to be good enough for the days between.

The file has the same shape as wp_timeline/week_N.json (see build_wp_timeline.py) with "src": "live", plus a
private "_s" block (what was last seen) so a successor run picks up where this one stopped.

It runs in GitHub Actions (wp_record.yml), commits to the `wp-data` branch rather than main so a minute-by-minute
log never triggers a Pages deploy or races the nightly commits, and the page reads it from raw.githubusercontent.com.
Gaps (a skipped run) are left as gaps: nothing is interpolated.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

from build_rumbles import API_BASE, STATS_BASE, get_json, discover_current_league_id, array_to_player_map
from capture_live_qb_injuries import SCORES_BASE, normalize_game_status, KICKOFF_LOOKAHEAD_HOURS

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("WP_DATA_DIR", os.path.join(HERE, "wp_live"))
LIVE_INTERVAL_SECONDS = int(os.environ.get("WP_INTERVAL", "60"))
COMMIT_EVERY_SECONDS = 600
SEED_VERSION = 3   # bump when the way earlier games are rebuilt changes: a recording seeded by an older one starts over
MAX_RUN_MINUTES = 330
CLOCK_EVERY_SECONDS = 45
PROJ_URL = "https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular"

STAT_KEYS = {
    "pass_yd", "pass_td", "pass_int", "pass_2pt", "rush_yd", "rush_td", "rush_2pt", "rec", "rec_yd", "rec_td", "rec_2pt",
    "fum_lost", "xpm", "sack", "int", "fum_rec", "safe", "def_td", "def_st_td", "blk_kick", "st_td", "kr_td", "pr_td",
}


def scoring_stats(stats: dict | None) -> dict:
    """The few stat counters that explain a points change."""
    out = {}
    for k, v in (stats or {}).items():
        if isinstance(v, (int, float)) and v and (k in STAT_KEYS or k.startswith("fgm")):
            out[k] = v
    return out


def remaining_permille(meta: dict | None, status: str) -> int:
    """Port of rumbles.html remainingFraction(): how much of the 60 regulation minutes is left, in 1/1000ths."""
    if status == "complete":
        return 0
    if status != "in_progress":
        return 1000
    meta = meta or {}
    try:
        q = int(meta.get("quarter_num"))
    except (TypeError, ValueError):
        return 1000
    if q < 1:
        return 1000
    if q >= 5:
        return 0
    rem_min = 15.0
    t = meta.get("time_remaining")
    if isinstance(t, str) and ":" in t:
        try:
            mm, ss = t.split(":")[:2]
            rem_min = int(mm) + int(ss) / 60
        except ValueError:
            pass
    frac = 1 - ((q - 1) * 15 + (15 - rem_min)) / 60
    return int(round(max(0.0, min(1.0, frac)) * 1000))


def label_from_stats(prev: dict, cur: dict) -> str:
    """Name what just happened from the change in a player's stat line."""
    def d(k: str) -> float:
        return cur.get(k, 0) - prev.get(k, 0)

    if d("def_td") > 0 or d("def_st_td") > 0 or d("st_td") > 0 or d("kr_td") > 0 or d("pr_td") > 0:
        return "defensive / return TD"
    fg = sum(d(k) for k in cur if k.startswith("fgm") and not k.startswith("fgmiss"))
    if fg > 0:
        return "field goal"
    if sum(d(k) for k in cur if k.startswith("fgmiss")) > 0:
        return "missed field goal"
    for yd, td, name in (("rec_yd", "rec_td", "catch"), ("rush_yd", "rush_td", "run"), ("pass_yd", "pass_td", "pass")):
        if d(td) > 0:
            y = int(d(yd))
            return (f"{y} yd " if y > 0 else "") + ("TD " + name)
    for k, text in (("pass_2pt", "2-pt pass"), ("rec_2pt", "2-pt catch"), ("rush_2pt", "2-pt run"), ("pass_int", "interception thrown"),
                    ("fum_lost", "fumble lost"), ("safe", "safety"), ("blk_kick", "blocked kick"), ("int", "interception"),
                    ("fum_rec", "fumble recovery"), ("sack", "sack"), ("xpm", "extra point")):
        if d(k) > 0:
            return text
    for yd, name in (("rec_yd", "catch"), ("rush_yd", "run"), ("pass_yd", "pass")):
        if d(yd) > 0:
            return f"{int(d(yd))} yd {name}"
    return "points change"


def merge_timeline(doc: dict, tl: dict) -> None:
    """Fold a play-by-play timeline (build_wp_timeline.build_week) into a recording, re-based on the recording's own t0.
    Used to fill in the games that were played before the recorder started; what has been seen since stays as it is."""
    shift = int(round((tl["t0"] - doc["t0"]) / 1000))
    for k, g in tl["g"].items():
        doc["g"][k] = {"ko": g["ko"] + shift, "end": g["end"] + shift, "clk": [[t + shift, r] for t, r in g["clk"]]}
    doc["tg"].update(tl["tg"])
    doc["tm"].update(tl["tm"])
    doc["ev"] = sorted(([e[0] + shift, e[1], e[2], e[3]] for e in tl["ev"]), key=lambda e: e[0]) + doc["ev"]
    for sid, p in tl["pre"].items():
        doc["pre"].setdefault(sid, p)
    last = doc["_s"]["last"]
    for e in tl["ev"]:
        last[e[1]] = round(last.get(e[1], 0.0) + e[2], 2)
    doc["_s"]["seeded"] = SEED_VERSION
    doc["q"], doc["approx"], doc["scoring"] = tl["q"], tl["approx"], tl.get("scoring")


def seed_from_plays(doc: dict, snap: dict, ctx: dict) -> bool:
    """A recording that starts mid-week has missed the games already played. nflverse's play-by-play has them (it
    publishes a game the day after), so rebuild those from the real plays, scored with the league's rules and set to
    Sleeper's points, then carry on live from there. Never fatal: without it the chart simply starts later."""
    try:
        import build_wp_timeline as B
        if doc["_s"].get("seeded") == SEED_VERSION or not any(g["status"] != "pre_game" for g in snap["games"]):
            return False
        starts = [g["start_ms"] for g in snap["games"] if g.get("start_ms")]
        if doc["t0"] is None:
            if not starts:
                return False
            doc["t0"] = min(starts)
        rows = B.load_pbp_rows(int(ctx["season"]))
        if not rows:
            return False
        players = ctx["players"]
        pos_of = {sid: ("DEF" if not sid.isdigit() else (players.get(sid) or {}).get("position") or "") for sid in snap["points"]}
        lineup = {"all": [[sid, pts] for sid, pts in snap["points"].items()]}
        proj = B.fetch_projections(int(ctx["season"]), ctx["week"], ctx["scoring"])
        tl = B.build_best(ctx["week"], rows, lineup, B.id_crosswalk(), B.scoring_candidates(ctx["scoring"]), {}, proj, int(ctx["season"]), pos_of, partial=True)
        if not tl:
            doc["_s"]["seeded"] = SEED_VERSION
            return False
        merge_timeline(doc, tl)
        for sid, st in (snap.get("stats") or {}).items():
            doc["_s"]["st"][sid] = scoring_stats(st)
        print(f"[record] filled in {len(tl['g'])} game(s) already played from the play-by-play ({len(tl['ev'])} events)")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[warn] couldn't fill in earlier games from the play-by-play ({e})", file=sys.stderr)
        return False


def empty_doc(season, week: int) -> dict:
    return {"v": 1, "season": int(season), "week": week, "src": "live", "t0": None, "q": 1, "approx": False,
            "g": {}, "tg": {}, "tm": {}, "ev": [], "pre": {}, "preSrc": "sleeper", "_s": {"last": {}, "st": {}}}


def apply_poll(doc: dict, snap: dict, now_ms: int) -> bool:
    """Fold one look at Sleeper into the recording. Returns True when anything was added.

    snap = { "games": [{"key": "AWY@HME", "away", "home", "status", "start_ms", "rem"}],
             "points": {sid: pts}, "stats": {sid: {stat: n}}, "team": {sid: team}, "pre": {sid: projection} }"""
    changed = False
    games = snap.get("games") or []
    if doc["t0"] is None:
        starts = [g["start_ms"] for g in games if g.get("start_ms")]
        if not starts:
            return False
        doc["t0"] = min(starts)
        changed = True
    t0 = doc["t0"]
    sec = int(round((now_ms - t0) / 1000))
    for g in games:
        e = doc["g"].setdefault(g["key"], {"ko": int(round(((g.get("start_ms") or now_ms) - t0) / 1000)), "end": None, "clk": []})
        doc["tg"][g["away"]] = g["key"]
        doc["tg"][g["home"]] = g["key"]
        if g["status"] == "pre_game":
            continue
        clk = e["clk"]
        if not clk:
            clk.append([e["ko"], 1000])
            changed = True
        last_t, last_r = clk[-1]
        if g["rem"] != last_r and (sec - last_t >= CLOCK_EVERY_SECONDS or g["rem"] == 0):
            clk.append([sec, g["rem"]])
            changed = True
        if g["status"] == "complete" and e["end"] is None:
            e["end"] = sec
            if clk[-1][1] != 0:
                clk.append([sec, 0])
            changed = True
    for sid, tm in (snap.get("team") or {}).items():
        if doc["tm"].get(sid) != tm:
            doc["tm"][sid] = tm
            changed = True
    started = {g["key"] for g in games if g["status"] != "pre_game"}
    for sid, proj in (snap.get("pre") or {}).items():
        # Last pregame value wins: stop updating once that player's game has begun.
        if doc["tg"].get(doc["tm"].get(sid, "")) not in started:
            doc["pre"][sid] = round(proj, 2)
    state = doc["_s"]
    for sid, pts in (snap.get("points") or {}).items():
        prev = state["last"].get(sid, 0.0)
        cur_stats = scoring_stats((snap.get("stats") or {}).get(sid))
        if abs(pts - prev) >= 0.005:
            doc["ev"].append([sec, sid, round(pts - prev, 2), label_from_stats(state["st"].get(sid, {}), cur_stats)])
            changed = True
        state["last"][sid] = pts
        state["st"][sid] = cur_stats
    return changed


# ---- Sleeper -----------------------------------------------------------------------------------------------------

def game_key(g: dict) -> tuple[str, str, str] | None:
    meta = g.get("metadata") or {}
    away, home = meta.get("away_team") or g.get("away_team"), meta.get("home_team") or g.get("home_team")
    if not away or not home:
        return None
    return f"{away}@{home}", away, home


def fetch_snapshot(ctx: dict) -> dict | None:
    """One look at Sleeper, or None if a fetch failed (never raises)."""
    try:
        season, week, stype, lid = ctx["season"], ctx["week"], ctx["season_type"], ctx["league_id"]
        scores = get_json(f"{SCORES_BASE}/{stype}/{season}/{week}")
        games = []
        for g in scores or []:
            k = game_key(g or {})
            if not k:
                continue
            status = normalize_game_status(g)
            try:
                start = int(g.get("start_time"))
            except (TypeError, ValueError):
                start = None
            games.append({"key": k[0], "away": k[1], "home": k[2], "status": status, "start_ms": start,
                          "rem": remaining_permille(g.get("metadata"), status)})
        matchups = get_json(f"{API_BASE}/league/{lid}/matchups/{week}") or []
        points, team = {}, {}
        for m in matchups:
            pp = m.get("players_points") or {}
            for sid in m.get("starters") or []:
                if sid and sid != "0":
                    points[sid] = float(pp.get(sid) or 0)
                    meta = ctx["players"].get(sid) or {}
                    team[sid] = sid if len(sid) <= 3 and not sid.isdigit() else (meta.get("team") or "")
        stats = array_to_player_map(get_json(f"{STATS_BASE}/{season}/{week}?season_type={stype}"))
        snap = {"games": games, "points": points, "team": {s: t for s, t in team.items() if t}, "stats": stats}
        if any(g["status"] == "pre_game" for g in games) and time.time() - ctx.get("pre_at", 0) > 1800:
            ctx["pre_at"] = time.time()
            arr = get_json(PROJ_URL.format(season=season, week=week)) or []
            sc = ctx["scoring"]
            snap["pre"] = {str(e["player_id"]): sum(float(v) * float(sc.get(k, 0)) for k, v in (e.get("stats") or {}).items()
                                                  if isinstance(v, (int, float))) for e in arr if e.get("stats") and str(e["player_id"]) in points}
        return snap
    except Exception as e:  # noqa: BLE001 - best effort; a missed minute is just a gap
        print(f"[warn] couldn't read Sleeper this minute ({e})", file=sys.stderr)
        return None


def load_ctx() -> dict | None:
    try:
        state = get_json(f"{API_BASE}/state/nfl")
        stype = state.get("season_type") or "regular"
        week = int(state.get("week") or 0)
        if not week or stype not in ("regular", "post"):
            return None
        season = state["season"]
        lid = discover_current_league_id(season)
        league = get_json(f"{API_BASE}/league/{lid}")
        return {"season": season, "week": week, "season_type": stype, "league_id": lid,
                "scoring": league.get("scoring_settings") or {}, "players": get_json(f"{API_BASE}/players/nfl") or {}}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] couldn't set up ({e})", file=sys.stderr)
        return None


def path_for(week: int) -> str:
    return os.path.join(DATA_DIR, f"week_{week}.json")


def load_doc(season, week: int) -> dict:
    try:
        d = json.load(open(path_for(week)))
        if d.get("season") == int(season) and d.get("src") == "live":
            if d.get("_s", {}).get("seeded") not in (None, SEED_VERSION):
                return empty_doc(season, week)   # seeded by an older version: rebuild it
            return d
    except Exception:  # noqa: BLE001
        pass
    return empty_doc(season, week)


def save_doc(doc: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = path_for(doc["week"]) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    os.replace(tmp, path_for(doc["week"]))


def commit_data(force: bool = False) -> None:
    """Push the recording to the wp-data branch (a git checkout at DATA_DIR's parent). Only in the workflow."""
    if os.environ.get("WP_COMMIT") != "1":
        return
    repo = os.path.dirname(DATA_DIR.rstrip("/"))
    try:
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
        if subprocess.run(["git", "-C", repo, "diff", "--cached", "--quiet"]).returncode == 0:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        subprocess.run(["git", "-C", repo, "commit", "-q", "-m", f"Live win-probability recording ({stamp})"], check=True)
        for _ in range(4):
            if subprocess.run(["git", "-C", repo, "push", "-q", "origin", "HEAD:wp-data"]).returncode == 0:
                return
            subprocess.run(["git", "-C", repo, "pull", "-q", "--rebase", "origin", "wp-data"], check=False)
            time.sleep(5)
        print("[warn] couldn't push the recording yet; the next commit will retry", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] commit failed ({e})", file=sys.stderr)


def start_successor_run() -> None:
    if os.environ.get("CAPTURE_CHAIN") != "1":
        return
    ref = os.environ.get("GITHUB_REF_NAME") or "main"
    try:
        subprocess.run(["gh", "workflow", "run", "wp_record.yml", "--ref", ref], check=True)
        print("[record] started a successor run")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] couldn't start a successor run ({e}); the next scheduled trigger will pick it up", file=sys.stderr)


def seconds_until_next(games: list[dict], now_ms: int) -> int | None:
    """Wait before the next look: a minute while a game is live, until just before kickoff if one is near, else stop."""
    if any(g["status"] == "in_progress" for g in games):
        return LIVE_INTERVAL_SECONDS
    nxt = min((g["start_ms"] for g in games if g["status"] == "pre_game" and g.get("start_ms") and g["start_ms"] > now_ms), default=None)
    if nxt is None:
        return None
    until = (nxt - now_ms) / 1000
    if until > KICKOFF_LOOKAHEAD_HOURS * 3600:
        return None
    return int(max(30, min(until - 60, 15 * 60)))


def main() -> None:
    started = time.monotonic()
    ctx = load_ctx()
    if not ctx:
        print("[record] no live week right now")
        return
    doc = load_doc(ctx["season"], ctx["week"])
    last_commit = time.monotonic()
    while True:
        now_ms = int(time.time() * 1000)
        snap = fetch_snapshot(ctx)
        wait = None
        if snap:
            # First look at a week that is already under way: fill in what was played before we started,
            # so the points already scored are not lumped into one "change" at this moment.
            changed = seed_from_plays(doc, snap, ctx)
            if changed:
                save_doc(doc)
                commit_data()
                last_commit = time.monotonic()
            if apply_poll(doc, snap, now_ms) or changed:
                save_doc(doc)
            wait = seconds_until_next(snap["games"], now_ms)
        else:
            wait = LIVE_INTERVAL_SECONDS
        if time.monotonic() - last_commit >= COMMIT_EVERY_SECONDS:
            commit_data()
            last_commit = time.monotonic()
        if wait is None:
            print("[record] nothing live or coming up soon; done")
            commit_data()
            return
        if (time.monotonic() - started) / 60 + wait / 60 > MAX_RUN_MINUTES:
            print(f"[record] reached the {MAX_RUN_MINUTES}-minute cap for one run")
            commit_data()
            start_successor_run()
            return
        time.sleep(wait)


if __name__ == "__main__":
    main()
