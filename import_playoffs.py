"""Writes seasons/<year>/playoffs.json from the league's own playoff records (namkurd/sleepersheet data/playoffs_history.json).

Sleeper's brackets never matched this league's custom playoffs, so the page shows the head-to-heads from the records instead:
the first playoff week is the Semifinals and the second the Finals. A three-team final is two rows in the records (the winner over each of
the other two, sharing the winner's score); here it is three head-to-heads: the champion against each of the other two, and those two against
each other. The title game (champion against the runner-up) is marked `champ`.

  python3 import_playoffs.py [path-or-url]     (default: the sleepersheet repo's file on GitHub)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://raw.githubusercontent.com/namkurd/sleepersheet/main/data/playoffs_history.json"
FIRST_SEASON = 2021   # the seasons this page keeps


def load(src: str) -> dict:
    if src.startswith("http"):
        with urllib.request.urlopen(src, timeout=30) as r:
            return json.load(r)
    with open(src) as fh:
        return json.load(fh)


def build(data: dict) -> dict[int, dict]:
    by_season: dict[int, dict[int, list]] = {}
    for g in data.get("games", []):
        if g["season"] >= FIRST_SEASON:
            by_season.setdefault(g["season"], {}).setdefault(g["week"], []).append(g)
    out = {}
    for season, weeks in by_season.items():
        names = [("Semifinals", "Semifinal"), ("Finals", "Final")]
        doc = {"v": 1, "season": season, "weeks": {}}
        for (week, games), (label, short) in zip(sorted(weeks.items()), names):
            rows = [{"w": g["winner"], "l": g["loser"], "ws": g["winner_score"], "ls": g["loser_score"]} for g in games]
            wk = {"label": label, "short": short, "games": rows}
            if short == "Final":
                three = len(rows) == 2 and rows[0]["w"] == rows[1]["w"]
                # the champion's game against the runner-up (the higher-scoring of the other two) is the title game
                rows.sort(key=lambda r: -r["ls"])
                rows[0]["champ"] = True
                if three:
                    wk["three"] = True
                    # the other two teams, head-to-head on the same scores, so every pairing of the three is covered
                    a, b = rows[0], rows[1]
                    rows.append({"w": a["l"], "l": b["l"], "ws": a["ls"], "ls": b["ls"], "other": True})
            doc["weeks"][str(week)] = wk
        out[season] = doc
    return out


def main(argv: list[str]) -> None:
    data = load(argv[1] if len(argv) > 1 else URL)
    for season, doc in sorted(build(data).items()):
        d = os.path.join(HERE, "seasons", str(season))
        if not os.path.isdir(d):
            print(f"[skip] no seasons/{season} folder")
            continue
        with open(os.path.join(d, "playoffs.json"), "w") as fh:
            json.dump(doc, fh, indent=1)
        print(f"[info] seasons/{season}/playoffs.json: " + ", ".join(f"{w['label']} {len(w['games'])}" for w in doc["weeks"].values()))


if __name__ == "__main__":
    main(sys.argv)
