#!/usr/bin/env python3
"""The NFL game situation behind each play on the win-probability timeline: quarter, game clock and score.

wp_timeline/week_N.json places every fantasy event on a wall-clock axis (seconds from the week's first kickoff) but says nothing
about the game itself. This writes wp_sit/week_N.json from the same nflverse play-by-play, on the same axis (it reuses the
timeline's own GameClock and t0), so a play at second S can be shown as "Q2 4:12 · SEA 14 – NYG 10":

  { "BUF@NYJ": { "away": "BUF", "home": "NYJ", "p": [[sec, qtr, "m:ss", away_score, home_score (before the play)], ...] } }

Covers this season (wp_sit/) and every archived season (seasons/YYYY/wp_sit/). Only weeks that have a timeline and no file yet are built (a finished week never changes).  python3 build_wp_scores.py [--force]
"""
import json, os, sys

import build_wp_timeline as T

OUT = "wp_sit"


def clock_text(row: dict) -> str:
    t = (row.get("time") or "").strip()
    if t:
        m, _, s = t.partition(":")
        try:
            return "%d:%02d" % (int(m), int(s or 0))
        except ValueError:
            pass
    try:
        g = float(row["quarter_seconds_remaining"])
        return "%d:%02d" % (int(g // 60), int(g % 60))
    except (KeyError, ValueError):
        return ""


def build_week(week: int, rows: list, t0: int) -> dict:
    by_game: dict = {}
    for r in rows:
        if int(float(r["week"])) == week and (r.get("season_type") or "REG") == "REG":
            by_game.setdefault(r["game_id"], []).append(r)
    out = {}
    for gid, grows in by_game.items():
        grows.sort(key=lambda r: float(r.get("play_id") or 0))
        away, home = T.team_of(grows[0].get("away_team") or ""), T.team_of(grows[0].get("home_team") or "")
        clk = T.GameClock(grows)
        if not clk.anchors:
            continue
        plays, last = [], None
        for i, r in enumerate(grows):
            el = T.elapsed_of(r)
            ms = clk.ms(el) if el is not None else None
            if ms is None:
                continue
            try:   # the score going into the play (nflverse's running total at the snap), so a touchdown is shown before its points
                a, h = int(float(r["total_away_score"])), int(float(r["total_home_score"]))
            except (KeyError, ValueError):
                continue
            try:
                q = int(float(r["qtr"]))
            except (KeyError, ValueError):
                continue
            s = int(round((ms - t0) / 1000))
            row = [s, q, clock_text(r), a, h]
            if plays and plays[-1][0] == s:
                plays[-1] = row
            else:
                plays.append(row)
        out["%s@%s" % (away, home)] = {"away": away, "home": home, "p": plays}
    return out


def jobs():
    """(timeline dir, output dir) for this season and every archived season under seasons/."""
    out = [(T.OUT_DIR, OUT)]
    base = os.path.join(T.HERE, "seasons")
    if os.path.isdir(base):
        for y in sorted(os.listdir(base)):
            if os.path.isdir(os.path.join(base, y, "wp_timeline")):
                out.append((os.path.join(base, y, "wp_timeline"), os.path.join(base, y, "wp_sit")))
    return out


def main() -> None:
    force = "--force" in sys.argv
    for tl_dir, out_dir in jobs():
        try:
            idx = json.load(open(os.path.join(tl_dir, "index.json")))
            weeks = [int(w) for w in idx.get("weeks", [])]
            season = int(json.load(open(os.path.join(tl_dir, "week_%d.json" % weeks[0])))["season"])
        except Exception:  # noqa: BLE001
            continue
        todo = [w for w in weeks if force or not os.path.exists(os.path.join(out_dir, "week_%d.json" % w))]
        if not todo:
            continue
        rows = T.load_pbp_rows(season)
        if rows is None:
            print("[warn] no play-by-play for %d yet" % season); continue
        os.makedirs(out_dir, exist_ok=True)
        for w in todo:
            doc = json.load(open(os.path.join(tl_dir, "week_%d.json" % w)))
            data = build_week(w, rows, int(doc["t0"]))
            if not data:
                continue
            path = os.path.join(out_dir, "week_%d.json" % w)
            json.dump(data, open(path, "w"), separators=(",", ":"))
            print("%d week %d: %d games, %d KB" % (season, w, len(data), os.path.getsize(path) // 1024))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - an optional extra
        print("[warn] game situation build failed: %s" % e, file=sys.stderr)
