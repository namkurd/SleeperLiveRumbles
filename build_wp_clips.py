#!/usr/bin/env python3
"""Finds a short official highlight for each of a week's biggest plays, so the expanded win-probability chart can play it.

Source: the NFL's own YouTube channel (its uploads playlist, which costs almost no API quota to read, unlike search).
Each upload's title is matched against the plays in wp_timeline/week_N.json: the player's name, a team nickname, the
kind of play (TD, interception, sack, field goal...) and the yardage. Only a confident match is kept. Nothing is
downloaded or re-hosted; the page embeds YouTube's own player, trimmed to the first 15 seconds with start/end.

  wp_clips/week_N.json   { "v": 1, "week": N, "clips": { "<sleeper id>:<seconds>": {"id", "s", "e", "t"} }, "checked": iso }

The key is the play's own entry in the timeline's `ev` list (sleeper id and seconds), which is what the page's
"biggest swings" rows carry. Needs YOUTUBE_API_KEY (a YouTube Data API v3 key); without it this does nothing.
Safe to run nightly: unmatched plays are retried (clips appear a few hours after games), matched ones are kept.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://www.googleapis.com/youtube/v3"
NFL_UPLOADS = "UUDVYQ4Zhbm3S2dlz7P1GBDg"   # the NFL channel's uploads playlist
MAX_PAGES = 30                               # 50 uploads a page; plenty for the week since the games
CLIP_SECONDS = 15
MAX_DURATION = 120                           # a highlight of one play, not a recap
MIN_POINTS = 2.5
MAX_PLAYS = 150
MIN_SCORE = 6
SKIP = re.compile(r"scoring adjustment|^latest$|^$|points?,? .*allowed|-and-out|-down stop|extra point|^\d+-pt|tackle for loss", re.I)

NICK = {
    "ARI": ["cardinals"], "ATL": ["falcons"], "BAL": ["ravens"], "BUF": ["bills"], "CAR": ["panthers"], "CHI": ["bears"],
    "CIN": ["bengals"], "CLE": ["browns"], "DAL": ["cowboys"], "DEN": ["broncos"], "DET": ["lions"], "GB": ["packers"],
    "HOU": ["texans"], "IND": ["colts"], "JAX": ["jaguars", "jags"], "JAC": ["jaguars", "jags"], "KC": ["chiefs"],
    "LV": ["raiders"], "LAC": ["chargers"], "LAR": ["rams"], "MIA": ["dolphins"], "MIN": ["vikings"], "NE": ["patriots"],
    "NO": ["saints"], "NYG": ["giants"], "NYJ": ["jets"], "PHI": ["eagles"], "PIT": ["steelers"], "SF": ["49ers", "niners"],
    "SEA": ["seahawks"], "TB": ["buccaneers", "bucs"], "TEN": ["titans"], "WAS": ["commanders"], "WSH": ["commanders"],
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"['\u2019]s\b", "", s.lower())   # "Kelce's" is Kelce
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.replace("-", " ").replace("'", ""))).strip()


def duration_seconds(iso: str) -> int:
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0) if m else 10 ** 6


def play_kind(label: str) -> list[str]:
    """Words a title is likely to use for this kind of play."""
    l = label.lower()
    out = []
    if "td" in l or "pick-six" in l:
        out += ["touchdown", "td", "score", "scores"]
    if "interception" in l or "pick" in l:
        out += ["interception", "pick", "int"]
    if "sack" in l:
        out += ["sack", "sacks", "sacked"]
    if "fumble" in l:
        out += ["fumble", "strip", "forced", "recovery"]
    if "field goal" in l:
        out += ["field goal", "fg", "kick"]
    return out


def yards_in(label: str) -> int | None:
    m = re.search(r"(\d+) yd", label)
    return int(m.group(1)) if m else None


def score_title(title: str, player: str | None, team: str | None, opp: str | None, label: str, is_def: bool) -> int:
    t = norm(title)
    s = 0
    if player:
        parts = norm(player).replace(" jr", "").replace(" sr", "").replace(" iii", "").replace(" ii", "").split()
        if len(parts) >= 2 and parts[-1] in t.split():
            s += 4
            if parts[0] in t.split():
                s += 1
        else:
            return 0   # a play is about a player: no name, no match
    nicks = NICK.get(team or "", [])
    if is_def and not any(n in t for n in nicks):
        return 0       # a D/ST has no name to find, so its team has to be in the title
    if is_def:
        s += 3         # (what a player's name would have been worth)
    if any(n in t for n in nicks):
        s += 2
    if any(n in t for n in NICK.get(opp or "", [])):
        s += 1
    kinds = play_kind(label)
    specific = False
    if kinds and any(re.search(rf"\b{re.escape(k)}\b", t) for k in kinds):
        s += 2
        specific = True
    y = yards_in(label)
    if y is not None and re.search(rf"\b{y} (yard|yd)", t):
        s += 3
        specific = True
    # the name and team alone could be any of his plays: the title must also say what kind, or how far
    return s if specific else 0


def candidate_plays(doc: dict, names: dict) -> list[dict]:
    plays = []
    tm, tg = doc.get("tm") or {}, doc.get("tg") or {}
    for sec, sid, pts, label in doc.get("ev") or []:
        if abs(pts) < MIN_POINTS or SKIP.search(label or ""):
            continue
        team = tm.get(sid)
        game = tg.get(team) if team else None
        opp = None
        if game and "@" in game:
            a, h = game.split("@")
            opp = h if team == a else a
        nm = (names.get(sid) or [None])[0]
        is_def = not nm and sid in NICK
        if not nm and not is_def:
            continue
        plays.append({"key": f"{sid}:{sec}", "sec": sec, "player": None if is_def else nm, "team": team or (sid if is_def else None),
                      "opp": opp, "label": label, "pts": abs(pts), "def": is_def})
    plays.sort(key=lambda p: -p["pts"])
    return plays[:MAX_PLAYS]


def match_plays(plays: list[dict], uploads: list[dict], t0_ms: int) -> dict:
    out = {}
    for p in plays:
        at = t0_ms + p["sec"] * 1000
        best, best_s = None, 0
        for u in uploads:
            if not u["embeddable"] or u["dur"] > MAX_DURATION or u["pub_ms"] < at or u["pub_ms"] > at + 7 * 86400 * 1000:
                continue
            s = score_title(u["title"], p["player"], p["team"], p["opp"], p["label"], p["def"])
            if s > best_s:
                best, best_s = u, s
        if best and best_s >= MIN_SCORE:
            out[p["key"]] = {"id": best["id"], "s": 0, "e": min(CLIP_SECONDS, best["dur"]) if best["dur"] > 0 else CLIP_SECONDS, "t": best["title"][:90]}
    return out


def fetch_uploads(key: str, since_ms: int) -> list[dict]:
    items, token = [], None
    for _ in range(MAX_PAGES):
        r = requests.get(f"{API}/playlistItems", params={"part": "snippet", "playlistId": NFL_UPLOADS, "maxResults": 50, "key": key, **({"pageToken": token} if token else {})}, timeout=30)
        r.raise_for_status()
        j = r.json()
        stop = False
        for it in j.get("items", []):
            sn = it.get("snippet") or {}
            pub = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")).timestamp() * 1000
            if pub < since_ms:
                stop = True
                continue
            items.append({"id": (sn.get("resourceId") or {}).get("videoId"), "title": sn.get("title") or "", "pub_ms": pub})
        token = j.get("nextPageToken")
        if stop or not token:
            break
    items = [i for i in items if i["id"]]
    for i in range(0, len(items), 50):
        chunk = items[i:i + 50]
        r = requests.get(f"{API}/videos", params={"part": "contentDetails,status", "id": ",".join(c["id"] for c in chunk), "key": key}, timeout=30)
        r.raise_for_status()
        info = {v["id"]: v for v in r.json().get("items", [])}
        for c in chunk:
            v = info.get(c["id"]) or {}
            c["dur"] = duration_seconds((v.get("contentDetails") or {}).get("duration", ""))
            c["embeddable"] = bool((v.get("status") or {}).get("embeddable")) and (v.get("status") or {}).get("privacyStatus") == "public"
    return items


def run(season_dir: str = HERE, epa_path: str | None = None) -> None:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("[info] no YOUTUBE_API_KEY; skipping clips")
        return
    tl_dir = os.path.join(season_dir, "wp_timeline")
    out_dir = os.path.join(season_dir, "wp_clips")
    try:
        names = json.load(open(epa_path or os.path.join(season_dir, "epa_weekly.json"))).get("names") or {}
    except Exception:  # noqa: BLE001
        names = {}
    weeks = sorted(int(f[5:-5]) for f in os.listdir(tl_dir) if f.startswith("week_") and f.endswith(".json")) if os.path.isdir(tl_dir) else []
    os.makedirs(out_dir, exist_ok=True)
    docs = {w: json.load(open(os.path.join(tl_dir, f"week_{w}.json"))) for w in weeks}
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    # only recent weeks are worth looking for: the channel's uploads page back only so far
    todo = [w for w in weeks if now_ms - docs[w]["t0"] < 21 * 86400 * 1000 and docs[w].get("ev")]
    if not todo:
        print("[info] no recent week to find clips for")
        return
    uploads = fetch_uploads(key, min(docs[w]["t0"] for w in todo))
    print(f"[info] {len(uploads)} NFL uploads since week {min(todo)}")
    for w in todo:
        path = os.path.join(out_dir, f"week_{w}.json")
        try:
            have = json.load(open(path)).get("clips") or {}
        except Exception:  # noqa: BLE001
            have = {}
        found = match_plays([p for p in candidate_plays(docs[w], names) if p["key"] not in have], uploads, docs[w]["t0"])
        have.update(found)
        with open(path, "w") as fh:
            json.dump({"v": 1, "week": w, "clips": have, "checked": datetime.now(timezone.utc).isoformat(timespec="seconds")}, fh, separators=(",", ":"))
        print(f"[info] week {w}: {len(have)} clip(s) ({len(found)} new)")
    idx = sorted(int(f[5:-5]) for f in os.listdir(out_dir) if f.startswith("week_") and f.endswith(".json"))
    with open(os.path.join(out_dir, "index.json"), "w") as fh:
        json.dump({"weeks": idx}, fh, separators=(",", ":"))


if __name__ == "__main__":
    try:
        run()
    except Exception as e:  # noqa: BLE001 - clips are an extra; never fail the build
        print(f"[warn] clips skipped: {e}", file=sys.stderr)
