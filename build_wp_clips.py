#!/usr/bin/env python3
"""Finds a short official highlight for each of a week's biggest plays, so the expanded win-probability chart can play it.

Source: the NFL's own YouTube channel (its uploads playlist, which costs almost no API quota to read, unlike search).
Each upload's title is matched against the plays in wp_timeline/week_N.json: the player's name, a team nickname, the
kind of play (TD, interception, sack, field goal...) and the yardage. Only a confident match is kept. Nothing is
downloaded or re-hosted; the page embeds YouTube's own player, trimmed to the first 15 seconds with start/end.

  wp_clips/week_N.json   { "v": 1, "week": N, "clips": { "<sleeper id>:<seconds>": {"id", "s", "e", "t"} }, "checked": iso }

The key is the play's own entry in the timeline's `ev` list (sleeper id and seconds), which is what the page's
"biggest swings" rows carry. Needs YOUTUBE_API_KEY (a YouTube Data API v3 key); without it this does nothing.
Covers the current season (recent weeks, every night) and every archived season under seasons/ (once; --force redoes them).
Safe to run nightly: unmatched plays are retried (clips appear a few hours after games), matched ones are kept.
"""
from __future__ import annotations

import hashlib
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
MAX_PAGES = 300                              # per channel; 50 uploads a page, one quota unit each: reaches back through several seasons
# Channels searched, by handle (resolved to ids once, 1 quota unit each, and remembered in wp_clips/channels.json). The NFL's own
# channel is first; the team channels post many focused single-player clips. A handle that does not resolve is skipped, and more
# can be added in clip_channels.json: {"handles": ["@Name", ...]}.
HANDLES = ["@NFL", "@NFLonFOX", "@NFLNetwork", "@ESPN",
           "@azcardinals", "@AtlantaFalcons", "@baltimoreravens", "@buffalobills", "@panthers", "@chicagobears", "@bengals", "@Browns",
           "@dallascowboys", "@DenverBroncos", "@Lions", "@packers", "@HoustonTexans", "@Colts", "@Jaguars", "@KansasCityChiefs",
           "@Raiders", "@chargers", "@LosAngelesRams", "@MiamiDolphins", "@vikings", "@patriots", "@neworleanssaints", "@Giants",
           "@NewYorkJets", "@philadelphiaeagles", "@steelers", "@49ers", "@Seahawks", "@TBBuccaneers", "@tennesseetitans", "@commanders"]
CLIP_SECONDS = 15
MAX_DURATION = 120                           # a highlight of one play, not a recap
MIN_POINTS = 2.5
MAX_PLAYS = 150
MIN_SCORE = 7
MATCHER = 2                                  # bump when the matching rules change: earlier matches are then thrown away and redone
SKIP = re.compile(r"scoring adjustment|^latest$|^$|points?,? .*allowed|-and-out|-down stop|extra point|^\d+-pt|tackle for loss", re.I)

NICK = {
    "ARI": ["cardinals"], "ATL": ["falcons"], "BAL": ["ravens"], "BUF": ["bills"], "CAR": ["panthers"], "CHI": ["bears"],
    "CIN": ["bengals"], "CLE": ["browns"], "DAL": ["cowboys"], "DEN": ["broncos"], "DET": ["lions"], "GB": ["packers"],
    "HOU": ["texans"], "IND": ["colts"], "JAX": ["jaguars", "jags"], "JAC": ["jaguars", "jags"], "KC": ["chiefs"],
    "LV": ["raiders"], "OAK": ["raiders"], "SD": ["chargers"], "STL": ["rams"], "LA": ["rams"], "LAC": ["chargers"], "LAR": ["rams"], "MIA": ["dolphins"], "MIN": ["vikings"], "NE": ["patriots"],
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
    if "interception" in l or "pick" in l:
        out += ["interception", "pick", "int", "picks", "intercepts"]
    elif "td" in l:
        out += ["touchdown", "td"]
    if "sack" in l:
        out += ["sack", "sacks", "sacked"]
    if "fumble" in l:
        out += ["fumble", "strip", "forced", "recovery"]
    if "field goal" in l:
        out += ["field goal", "fg"]
    return out


# A title about a whole game, a player's day or a reaction is not about this play.
NOT_A_PLAY = re.compile(r"\b(game|games|highlights|best plays|recap|every|all|weighs|reacts|reaction|interview|press conference|postgame|"
                        r"pregame|talks|says|speaks|celly|celebration|film|breakdown|season|career|top|moments|plays|night|day)\b|\d-(td|touchdown)\b|\d touchdowns", re.I)


def yards_in(label: str) -> int | None:
    m = re.search(r"(\d+) yd", label)
    return int(m.group(1)) if m else None


def score_title(title: str, player: str | None, team: str | None, opp: str | None, label: str, is_def: bool, channel: str = "") -> int:
    if NOT_A_PLAY.search(norm(title).replace("-", " ")) or NOT_A_PLAY.search(title.lower()):
        return 0
    t = norm(title)
    words = t.split()
    ch = norm(channel)
    nicks = NICK.get(team or "", [])
    opps = NICK.get(opp or "", [])
    team_in_title = any(n in t for n in nicks)
    own_channel = any(n in ch for n in nicks)
    s = 0
    if player:
        parts = norm(player).replace(" jr", "").replace(" sr", "").replace(" iii", "").replace(" ii", "").split()
        if len(parts) < 2 or parts[-1] not in words:
            return 0   # a play is about a player: no name, no match
        first = parts[0] in words
        # a last name alone is any number of players: the first name, or his team (in the title or as the channel), must back it up
        if not (first or team_in_title or own_channel or any(n in t for n in opps)):
            return 0
        s += 5 if first else 4
    else:
        if not (team_in_title or own_channel):
            return 0       # a D/ST has no name to find, so its team has to be in the title or the channel
        s += 3             # (what a player's name would have been worth)
    if team_in_title or own_channel:
        s += 2
    if any(n in t for n in opps):
        s += 1
    kinds = play_kind(label)
    specific = False
    if kinds and any(re.search(rf"\b{re.escape(k)}\b", t) for k in kinds):
        s += 2
        specific = True
    y = yards_in(label)
    if y is not None:
        said = [int(x) for x in re.findall(r"\b(\d+) (?:yard|yd)", t)]
        if said and y not in said:
            return 0   # the title gives a different distance: another play
        if said:
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
            s = score_title(u["title"], p["player"], p["team"], p["opp"], p["label"], p["def"], u.get("ch", ""))
            if s > best_s:
                best, best_s = u, s
        if best and best_s >= MIN_SCORE:
            out[p["key"]] = {"id": best["id"], "s": 0, "e": min(CLIP_SECONDS, best["dur"]) if best["dur"] > 0 else CLIP_SECONDS, "t": best["title"][:90], "c": best.get("ch", "")[:40]}
    return out


def resolve_channels(key: str, cache_path: str, extra_path: str) -> list[dict]:
    """[{handle, playlist}] for every handle that resolves; remembered so later runs spend nothing on it."""
    try:
        known = json.load(open(cache_path))
    except Exception:  # noqa: BLE001
        known = {}
    handles = list(HANDLES)
    try:
        extra = json.load(open(extra_path))
        handles += [h for h in extra.get("handles", []) if h not in handles]
        # a video from a channel worth searching: its channel is found from the video id
        handles += ["video:" + v for v in extra.get("videos", []) if "video:" + v not in handles]
    except Exception:  # noqa: BLE001
        pass
    out = []
    for h in handles:
        if h not in known:
            try:
                if h.startswith("video:"):
                    r = requests.get(f"{API}/videos", params={"part": "snippet", "id": h[6:], "key": key}, timeout=30)
                    items = r.json().get("items") if r.ok else None
                    known[h] = ("UU" + items[0]["snippet"]["channelId"][2:]) if items else None
                    if items:
                        print(f"[info] {h} is from the channel {items[0]['snippet'].get('channelTitle')}")
                else:
                    r = requests.get(f"{API}/channels", params={"part": "id", "forHandle": h, "key": key}, timeout=30)
                    items = r.json().get("items") if r.ok else None
                    known[h] = ("UU" + items[0]["id"][2:]) if items else None
            except Exception:  # noqa: BLE001
                continue
        if known.get(h):
            out.append({"handle": h, "playlist": known[h]})
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as fh:
        json.dump(known, fh, separators=(",", ":"), sort_keys=True)
    return out


def fetch_uploads(key: str, since_ms: int, windows: list[tuple[float, float]] | None = None, playlists: list[str] | None = None) -> list[dict]:
    """Uploads back to since_ms from each playlist (newest first, 50 a page at one quota unit each). `windows` limits the
    per-video details lookup (also one unit per 50) to uploads inside some week's span."""
    items, seen = [], set()
    for pl in playlists or [NFL_UPLOADS]:
        token = None
        for _ in range(MAX_PAGES):
            r = requests.get(f"{API}/playlistItems", params={"part": "snippet", "playlistId": pl, "maxResults": 50, "key": key, **({"pageToken": token} if token else {})}, timeout=30)
            if not r.ok:
                print(f"[warn] playlist {pl}: HTTP {r.status_code}", file=sys.stderr)
                break
            j = r.json()
            stop = False
            for it in j.get("items", []):
                sn = it.get("snippet") or {}
                if not sn.get("publishedAt"):
                    continue
                pub = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")).timestamp() * 1000
                if pub < since_ms:
                    stop = True
                    continue
                vid = (sn.get("resourceId") or {}).get("videoId")
                if vid and vid not in seen and (windows is None or any(a <= pub <= b for a, b in windows)):
                    seen.add(vid)
                    items.append({"id": vid, "title": sn.get("title") or "", "pub_ms": pub, "ch": sn.get("channelTitle") or ""})
            token = j.get("nextPageToken")
            if stop or not token:
                break
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


def season_jobs(root: str = HERE) -> list[dict]:
    """Every season folder with timelines: the current season (the repo root, rechecked nightly for recent weeks) and each
    archived one under seasons/ (searched once; the channel's old uploads no longer change)."""
    jobs = [{"dir": root, "recent": True}]
    sdir = os.path.join(root, "seasons")
    if os.path.isdir(sdir):
        for y in sorted(os.listdir(sdir), reverse=True):
            if os.path.isdir(os.path.join(sdir, y, "wp_timeline")):
                jobs.append({"dir": os.path.join(sdir, y), "recent": False})
    return jobs


def run(root: str = HERE, force: bool = False) -> None:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("[info] no YOUTUBE_API_KEY; skipping clips")
        return
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    chans = resolve_channels(key, os.path.join(root, "wp_clips", "channels.json"), os.path.join(root, "clip_channels.json"))
    sig = hashlib.md5(",".join(sorted(c["playlist"] for c in chans)).encode()).hexdigest()[:8]
    work = []   # (dir, week, doc, names)
    for job in season_jobs(root):
        d = job["dir"]
        tl_dir, out_dir = os.path.join(d, "wp_timeline"), os.path.join(d, "wp_clips")
        if not os.path.isdir(tl_dir):
            continue
        try:
            names = json.load(open(os.path.join(d, "epa_weekly.json"))).get("names") or {}
        except Exception:  # noqa: BLE001
            names = {}
        for f in sorted(os.listdir(tl_dir)):
            if not (f.startswith("week_") and f.endswith(".json")):
                continue
            w = int(f[5:-5])
            doc = json.load(open(os.path.join(tl_dir, f)))
            if not doc.get("ev"):
                continue
            try:   # an archived week is searched once per set of channels: adding a channel searches it again
                old = json.load(open(os.path.join(out_dir, f"week_{w}.json")))
                done_before = old.get("chs") == sig and old.get("mv") == MATCHER
            except Exception:  # noqa: BLE001
                done_before = False
            # the current season: weeks of the last three weeks, every night. An archived season: once.
            if (job["recent"] and now_ms - doc["t0"] < 21 * 86400 * 1000) or (not job["recent"] and (force or not done_before)) or (job["recent"] and force):
                work.append((d, w, doc, names))
    if not work:
        print("[info] no week needs clips")
        return
    windows = [(doc["t0"], doc["t0"] + 8 * 86400 * 1000) for _, _, doc, _ in work]
    print(f"[info] channels: {', '.join(c['handle'] for c in chans) or 'none resolved (using the NFL channel)'}")
    uploads = fetch_uploads(key, min(a for a, _ in windows), windows, [c["playlist"] for c in chans] or None)
    print(f"[info] {len(uploads)} uploads in the windows of {len(work)} week(s)")
    touched = set()
    for d, w, doc, names in work:
        out_dir = os.path.join(d, "wp_clips")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"week_{w}.json")
        try:
            old = json.load(open(path))
            have = (old.get("clips") or {}) if old.get("mv") == MATCHER else {}
        except Exception:  # noqa: BLE001
            have = {}
        found = match_plays([p for p in candidate_plays(doc, names) if p["key"] not in have], uploads, doc["t0"])
        have.update(found)
        with open(path, "w") as fh:
            json.dump({"v": 1, "week": w, "clips": have, "chs": sig, "mv": MATCHER, "checked": datetime.now(timezone.utc).isoformat(timespec="seconds")}, fh, separators=(",", ":"))
        touched.add(out_dir)
        print(f"[info] {os.path.relpath(d, root) if d != root else 'current'} week {w}: {len(have)} clip(s) ({len(found)} new)")
    for out_dir in touched:
        idx = sorted(int(f[5:-5]) for f in os.listdir(out_dir) if f.startswith("week_") and f.endswith(".json"))
        with open(os.path.join(out_dir, "index.json"), "w") as fh:
            json.dump({"weeks": idx}, fh, separators=(",", ":"))


if __name__ == "__main__":
    try:
        run(force="--force" in sys.argv)
    except Exception as e:  # noqa: BLE001 - clips are an extra; never fail the build
        print(f"[warn] clips skipped: {e}", file=sys.stderr)
