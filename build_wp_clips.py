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

import gzip
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://www.googleapis.com/youtube/v3"
NFL_UPLOADS = "UUDVYQ4Zhbm3S2dlz7P1GBDg"   # the NFL channel's uploads playlist
MAX_PAGES = 400                              # per channel; 50 uploads a page, one quota unit each: reaches back through several seasons
# Channels searched, by handle (resolved to ids once, 1 quota unit each, and remembered in wp_clips/channels.json). The NFL's own
# channel is first; the team channels post many focused single-player clips. A handle that does not resolve is skipped, and more
# can be added in clip_channels.json: {"handles": ["@Name", ...]}.
HANDLES = ["@NFL", "@NFLonFOX", "@NFLNetwork", "@ESPN", "@BleacherReport", "@HouseofHighlights", "@FOXSports", "@CBSSports", "@CBSSportsHQ",
           "@NBCSports", "@SportsCenter", "@YahooSports", "@NFLonCBS", "@SNFonNBC", "@NFLThrowback", "@NFLFilms", "@ProFootballFocus",
           "@azcardinals", "@AtlantaFalcons", "@baltimoreravens", "@buffalobills", "@panthers", "@chicagobears", "@bengals", "@Browns",
           "@dallascowboys", "@DenverBroncos", "@Lions", "@packers", "@HoustonTexans", "@Colts", "@Jaguars", "@KansasCityChiefs",
           "@Raiders", "@chargers", "@LosAngelesRams", "@MiamiDolphins", "@vikings", "@patriots", "@neworleanssaints", "@Giants",
           "@NewYorkJets", "@philadelphiaeagles", "@steelers", "@49ers", "@Seahawks", "@TBBuccaneers", "@tennesseetitans", "@commanders"]
CLIP_SECONDS = 15
MAX_DURATION = 120                           # a highlight of one play, not a recap
MIN_POINTS = 2.5
MAX_PLAYS = 150
MIN_SCORE = 7
DAILY_UNITS = 8500                          # of YouTube's 10,000 free units a day, what all runs together may use
BUDGET = 6500                               # YouTube API units a run may spend (the free daily quota is 10,000)
MAX_SEARCHES = 40                            # YouTube searches (100 units each) a run may spend on plays no upload matched
SEARCH_TOP = 12                              # only a week's biggest plays are worth a search
MATCHER = 5                                  # bump when the matching rules change: earlier matches are then thrown away and redone
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


def score_title(title: str, player: str | None, team: str | None, opp: str | None, label: str, is_def: bool, channel: str = "", loose: bool = False, desc: str = "") -> int:
    if NOT_A_PLAY.search(norm(title).replace("-", " ")) or NOT_A_PLAY.search(title.lower()):
        return 0
    t = norm(title + " " + desc)   # a result's description often names the player, team and distance a short title leaves out
    words = t.split()
    ch = norm(channel)
    nicks = NICK.get(team or "", [])
    opps = NICK.get(opp or "", [])
    team_in_title = any(n in t for n in nicks)
    own_channel = any(n in ch for n in nicks)
    s = 0
    need_yards = False
    if player:
        parts = norm(player).replace(" jr", "").replace(" sr", "").replace(" iii", "").replace(" ii", "").split()
        if len(parts) < 2 or parts[-1] not in words:
            return 0   # a play is about a player: no name, no match
        first = parts[0] in words
        # a last name alone is any number of players: the first name, or his team (in the title or as the channel), must back it up
        backed = first or team_in_title or own_channel or any(n in t for n in opps)
        # A short from any channel often names the player by surname alone. For a result of a search made for exactly this play, a
        # surname is enough when the title also gives this play's distance (checked below).
        if not backed and not (loose and yards_in(label) is not None):
            return 0
        need_yards = not backed
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
    if player and need_yards and not (y is not None and re.search(rf"\b{y} (yard|yd)", t)):
        return 0
    # the name and team alone could be any of his plays: the title must also say what kind, or how far
    return s if specific else 0


OTHER_KINDS = {"interception": ("interception", "pick", "picks", "intercepts", "int"), "sack": ("sack", "sacks", "sacked"), "fumble": ("fumble", "strip", "recovery"),
               "touchdown": ("touchdown", "td"), "field goal": ("field goal", "fg")}


def first_result_ok(u: dict, p: dict) -> bool:
    """The top YouTube result for a search made for exactly this play is usually it. Take it when it is a short clip that names the player
    (with his first name, team or opponent, or from his team's channel), gives no other distance and no other kind of play, and the
    player has no second play of this kind that week it could equally be."""
    if not p.get("player") and not p.get("def"):
        return False
    if u.get("rank") != 0 or p.get("sib") or not 0 < u.get("dur", 0) <= 60:
        return False
    title = u["title"]
    if NOT_A_PLAY.search(norm(title).replace("-", " ")) or NOT_A_PLAY.search(title.lower()):
        return False
    t, words, ch = norm(title + " " + u.get("desc", "")), norm(title + " " + u.get("desc", "")).split(), norm(u.get("ch", ""))
    nicks, opps = NICK.get(p["team"] or "", []), NICK.get(p["opp"] or "", [])
    team_in = any(n in t for n in nicks) or any(n in ch for n in nicks)
    if p["player"]:
        parts = norm(p["player"]).replace(" jr", "").replace(" sr", "").replace(" iii", "").replace(" ii", "").split()
        if len(parts) < 2 or parts[-1] not in words or not (parts[0] in words or team_in or any(n in t for n in opps)):
            return False
    elif not team_in:
        return False
    y = yards_in(p["label"])
    said = [int(x) for x in re.findall(r"\b(\d+) (?:yard|yd)", t)]
    if y is not None and said and y not in said:
        return False
    mine = play_kind(p["label"])
    if mine:
        for group, ws in OTHER_KINDS.items():
            if not any(w in mine for w in ws) and any(re.search(rf"\b{re.escape(w)}\b", t) for w in ws):
                return False
    return True


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


# The NFL's own channel often forbids embedding outside YouTube ("blocked it from display on this website") even where the API says
# embeddable, so its uploads are used only when nothing else matches; the page also falls back to the next candidate, then to a link.
NO_EMBED_HINT = ("NFL", "NFL Network")
ALTERNATES = 3


def clip_of(u: dict) -> dict:
    return {"id": u["id"], "s": 0, "e": min(CLIP_SECONDS, u["dur"]) if u["dur"] > 0 else CLIP_SECONDS, "t": u["title"][:90], "c": u.get("ch", "")[:40]}


def match_plays(plays: list[dict], uploads: list[dict], t0_ms: int, loose: bool = False) -> dict:
    out = {}
    for p in plays:
        at = t0_ms + p["sec"] * 1000
        found = []
        for u in uploads:
            if not u["embeddable"] or u["dur"] > MAX_DURATION or u["pub_ms"] < at or u["pub_ms"] > at + 7 * 86400 * 1000:
                continue
            s = score_title(u["title"], p["player"], p["team"], p["opp"], p["label"], p["def"], u.get("ch", ""), loose, u.get("desc", ""))
            if s < MIN_SCORE and loose and first_result_ok(u, p):
                s = MIN_SCORE
            if s >= MIN_SCORE:
                found.append((s - (3 if u.get("ch") in NO_EMBED_HINT else 0), u))
        if found:
            found.sort(key=lambda x: -x[0])
            clip = clip_of(found[0][1])
            alts = [clip_of(u) for _, u in found[1:ALTERNATES + 1] if u["id"] != found[0][1]["id"]]
            if alts:
                clip["alt"] = alts
            out[p["key"]] = clip
    return out


RECAP = re.compile(r"\b(highlights?|recap|full game|game)\b", re.I)
CHAPTER = re.compile(r"^\s*[\u25b6\-\u2022*]?\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s*[-\u2013:|]?\s*(.+?)\s*$")


def chapters_from(description: str) -> list[list]:
    """[[seconds, text], ...] from the timestamp lines of a video description (needs at least three to be a chapter list)."""
    out = []
    for line in (description or "").splitlines():
        m = CHAPTER.match(line)
        if m:
            sec = sum(int(x) * 60 ** i for i, x in enumerate(reversed(m.group(1).split(":"))))
            out.append([sec, m.group(2)[:100]])
    return out if len(out) >= 3 else []


def fetch_chapters(q: "Quota", cache: dict, ids: list[str]) -> None:
    """Duration, embeddability and chapter list of longer highlight videos (one unit per 50)."""
    need = [i for i in ids if i not in cache.setdefault("chap", {})]
    for i in range(0, len(need), 50):
        chunk = need[i:i + 50]
        j = q.get(f"{API}/videos", {"part": "snippet,contentDetails,status", "id": ",".join(chunk), "key": q.key})
        info = {v["id"]: v for v in j.get("items", [])}
        for vid in chunk:
            v = info.get(vid) or {}
            st = v.get("status") or {}
            cache["vid"][vid] = [duration_seconds((v.get("contentDetails") or {}).get("duration", "")), int(bool(st.get("embeddable")) and st.get("privacyStatus") == "public")]
            cache["chap"][vid] = chapters_from((v.get("snippet") or {}).get("description", ""))


def match_chapters(plays: list[dict], recaps: list[dict], cache: dict, t0_ms: float) -> dict:
    """A clip inside a longer highlight video: the chapter whose text names the player and the play."""
    out = {}
    for p in plays:
        at = t0_ms + p["sec"] * 1000
        best, best_s = None, 0
        for u in recaps:
            info = cache["vid"].get(u["id"]) or [0, 0]
            if not info[1] or info[0] > 3600 or u["pub_ms"] < at or u["pub_ms"] > at + 7 * 86400 * 1000:
                continue
            for sec, text in (cache.get("chap", {}).get(u["id"]) or []):
                sc = score_title(text, p["player"], p["team"], p["opp"], p["label"], p["def"], u.get("ch", "") + " " + u["title"])
                if sc > best_s:
                    best, best_s = (u, sec, text), sc
        if best and best_s >= MIN_SCORE:
            u, sec, text = best
            st = max(0, sec - 2)
            out[p["key"]] = {"id": u["id"], "s": st, "e": st + CLIP_SECONDS, "t": text[:90], "c": (u.get("ch") or "")[:40], "chap": 1}
    return out


# STACKED Fantasy publishes, every week, one film video per player ("Tyler Shough: Every Dropback of 2026 Week 2 | Full Film Compilation")
# whose chapters are the plays themselves: "Q3 13.07 25y pass to J.Johnson". The player and the week come from the title; the play within
# the video is found from the quarter, the game clock and the yardage; and the next chapter says where the play ends.
STACKED_TITLE = re.compile(r"^(?P<name>.+?):\s*Every\s+(?P<kind>[A-Za-z ]+?)\s+of\s+(?P<season>\d{4})\s+Week\s+(?P<week>\d+)", re.I)
STACKED_PLAY = re.compile(r"\bQ\s*([1-4]|OT)\s+(\d{1,2})[.:](\d{2})\s+(-?\d+)\s*y?\b\s*(.*)", re.I)
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
STACKED_TOL = 75      # seconds of game clock a chapter may differ from a play whose yardage it also gives
STACKED_TOL_PLAIN = 20


def name_key(name: str) -> str:
    return " ".join(w for w in norm(name).split() if w not in SUFFIXES)


def stacked_chapter(text: str) -> dict | None:
    """{elapsed, yds, what} of a chapter like "Q3 13.07 25y pass to J.Johnson": elapsed is seconds of regulation played."""
    m = STACKED_PLAY.search(text or "")
    if not m:
        return None
    q = 5 if m.group(1).upper() == "OT" else int(m.group(1))
    left = int(m.group(2)) * 60 + int(m.group(3))
    return {"elapsed": 3600 + (600 - left) if q == 5 else (q - 1) * 900 + (900 - left), "yds": abs(int(m.group(4))), "what": norm(m.group(5))}


def game_elapsed(doc: dict, sid: str, sec: float) -> float | None:
    """Seconds of game clock played when the timeline's second `sec` came, from the game's clock samples [[sec, permille left], ...]."""
    team = (doc.get("tm") or {}).get(sid)
    g = ((doc.get("g") or {}).get((doc.get("tg") or {}).get(team) or "")) or {}
    clk = g.get("clk") or []
    if not clk:
        return None
    if sec <= clk[0][0]:
        return 0.0
    if sec >= clk[-1][0]:
        return (1000 - clk[-1][1]) * 3.6
    lo, hi = 0, len(clk) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if clk[mid][0] <= sec:
            lo = mid
        else:
            hi = mid
    (t0, r0), (t1, r1) = clk[lo], clk[hi]
    return (1000 - (r0 + (r1 - r0) * (sec - t0) / (t1 - t0 or 1))) * 3.6


def stacked_videos(uploads: list[dict], season: int, week: int) -> dict[str, list[dict]]:
    """That week's STACKED film videos by player."""
    out: dict[str, list[dict]] = {}
    for u in uploads:
        if not (u.get("ch") or "").upper().startswith("STACKED"):
            continue
        m = STACKED_TITLE.match(u["title"])
        if m and int(m.group("season")) == season and int(m.group("week")) == week:
            out.setdefault(name_key(m.group("name")), []).append(u)
    return out


def match_stacked(plays: list[dict], by_player: dict[str, list[dict]], cache: dict, doc: dict) -> dict:
    """The exact play inside the player's own film video (or, for a quarterback's or runner's, the chapter that names him)."""
    out = {}
    vids = [u for us in by_player.values() for u in us]
    for p in plays:
        if not p["player"] or p["key"].split(":")[0] not in (doc.get("tm") or {}):
            continue
        sid, sec = p["key"].split(":")[0], p["sec"]
        ev = game_elapsed(doc, sid, sec)
        if ev is None:
            continue
        lab = p["label"].lower()
        y, kinds = yards_in(p["label"]), play_kind(p["label"])
        # what a chapter calls this kind of play: a catch is "pass to ..." in the quarterback's film, a carry is "run"
        kinds = kinds + (["pass", "catch"] if "catch" in lab or "pass" in lab else []) + (["run", "rush", "scramble"] if "run" in lab or "rush" in lab else [])
        pk = name_key(p["player"]).split()
        # the quarterback's "Every Dropback" film names the receiver ("pass to J.Smith-Njigba"): initial, then the rest of his name
        mention = (pk[0][0] + " " + " ".join(pk[1:])) if len(pk) >= 2 else None
        own = {u["id"] for u in by_player.get(name_key(p["player"]), [])}
        best = None
        for u in vids:
            info = cache["vid"].get(u["id"]) or [0, 0]
            chaps = (cache.get("chap") or {}).get(u["id"]) or []
            if not info[1] or not chaps:
                continue
            for i, (csec, text) in enumerate(chaps):
                c = stacked_chapter(text)
                if not c:
                    continue
                if u["id"] not in own and not (mention and re.search(rf"\b{re.escape(mention)}\b", c["what"])):
                    continue
                diff = abs(c["elapsed"] - ev)
                if y is not None:
                    if c["yds"] != y or diff > STACKED_TOL:
                        continue
                elif diff > STACKED_TOL_PLAIN or not (kinds and any(re.search(rf"\b{re.escape(k)}\b", c["what"]) for k in kinds)):
                    continue
                score = diff + (0 if u["id"] in own else 8)
                if best is None or score < best[0]:
                    best = (score, u, i, csec, text, chaps, info[0])
        if best:
            _, u, i, csec, text, chaps, dur = best
            st = max(0, csec - 1)
            nxt = chaps[i + 1][0] if i + 1 < len(chaps) else None
            en = nxt - 0.5 if nxt is not None and 4 <= nxt - st <= 30 else st + 14
            if dur:
                en = min(en, dur)
            out[p["key"]] = {"id": u["id"], "s": st, "e": round(en, 1), "t": text[:90], "c": (u.get("ch") or "STACKED Fantasy")[:40], "chap": 1}
    return out


def video_id(ref: str) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([\w-]{11})", ref or "") or re.fullmatch(r"([\w-]{11})", (ref or "").strip())
    return m.group(1) if m else None


def apply_manual(have: dict, plays: list[dict], entries: list[dict], season: int, week: int) -> int:
    """Hand-picked clips (clips_manual.json) win over anything found: {season, week, player, label (or part of it), video, start}."""
    n = 0
    for e in entries:
        if int(e.get("season", 0)) != season or int(e.get("week", 0)) != week:
            continue
        vid = video_id(e.get("video", ""))
        if not vid:
            continue
        want = norm(e.get("player", ""))
        lab = norm(e.get("label", ""))
        for p in plays:
            who = norm(p["player"] or p["team"] or "")
            if want and want in who and (not lab or lab in norm(p["label"])):
                st = int(e.get("start", 0))
                have[p["key"]] = {"id": vid, "s": st, "e": st + CLIP_SECONDS, "t": "picked by hand", "c": "manual", "m": 1}
                n += 1
                break
    return n


def search_clips(q: "Quota", play: dict, t0_ms: float) -> list[dict]:
    """YouTube search for one play (100 units): short, embeddable videos from the week after it, scored like any upload."""
    at = datetime.fromtimestamp((t0_ms + play["sec"] * 1000) / 1000, timezone.utc)
    words = [play["player"] or " ".join(NICK.get(play["team"], [""])[:1]), (play_kind(play["label"]) or [""])[0]]
    y = yards_in(play["label"])
    if y is not None:
        words.append(f"{y} yard")
    words += NICK.get(play["opp"] or "", [])[:1]
    words.append("shorts")   # a YouTube Short of the play is very often among the first results
    j = q.get(f"{API}/search", {"part": "snippet", "type": "video", "q": " ".join(w for w in words if w), "maxResults": 8, "videoEmbeddable": "true",
                                "videoDuration": "short", "regionCode": "US", "key": q.key,
                                "publishedAfter": at.isoformat().replace("+00:00", "Z"), "publishedBefore": (at + timedelta(days=7)).isoformat().replace("+00:00", "Z")})
    out = []
    for rank, it in enumerate(j.get("items", [])):
        sn = it.get("snippet") or {}
        vid = (it.get("id") or {}).get("videoId")
        if vid and sn.get("publishedAt"):
            out.append({"id": vid, "rank": rank, "desc": sn.get("description") or "", "title": sn.get("title") or "", "ch": sn.get("channelTitle") or "", "dur": 0, "embeddable": True,
                        "pub_ms": datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")).timestamp() * 1000})
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


class QuotaOut(Exception):
    """YouTube says today's free quota is spent: stop, keep what was read, carry on tomorrow."""


class Quota:
    def __init__(self, budget: int):
        self.left = budget
        self.key = ""

    def get(self, url: str, params: dict) -> dict:
        if self.left <= 0:
            raise QuotaOut("budget for this run is spent")
        self.left -= 1
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 403 and "quota" in r.text.lower():
            raise QuotaOut("YouTube daily quota is spent")
        if not r.ok:
            raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
        return r.json()


def load_cache(path: str) -> dict:
    try:
        with gzip.open(path, "rt") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {"pl": {}, "vid": {}}


def save_cache(path: str, cache: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt") as fh:
        json.dump(cache, fh, separators=(",", ":"))


def crawl(q: Quota, cache: dict, playlists: list[str], since_ms: float, windows: list[tuple[float, float]]) -> bool:
    """Reads each playlist newest-first into the cache: first whatever is new since last time, then further back toward
    since_ms from where an earlier (maybe interrupted) crawl stopped. Only uploads inside some week's window are kept.
    Returns True when every playlist is read back to since_ms."""
    complete = True
    for pl in playlists:
        c = cache["pl"].setdefault(pl, {"items": {}, "newest": 0, "token": None, "back": False, "ch": ""})

        def page(token):
            """(in-window uploads, next token, newest and oldest publish time on the page)"""
            j = q.get(f"{API}/playlistItems", {"part": "snippet", "playlistId": pl, "maxResults": 50, "key": q.key, **({"pageToken": token} if token else {})})
            kept, pubs = [], []
            for it in j.get("items", []):
                sn = it.get("snippet") or {}
                if not sn.get("publishedAt"):
                    continue
                pub = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")).timestamp() * 1000
                pubs.append(pub)
                c["ch"] = sn.get("channelTitle") or c["ch"]
                vid = (sn.get("resourceId") or {}).get("videoId")
                if vid and pub >= since_ms and any(a <= pub <= b for a, b in windows):
                    c["items"][vid] = [int(pub // 1000), sn.get("title") or ""]
            return j.get("nextPageToken"), (max(pubs) if pubs else 0), (min(pubs) if pubs else 0)

        try:
            if c["newest"]:
                # what is new since the last run: from the top until we meet something already read
                token, seen_top = None, c["newest"]
                for _ in range(MAX_PAGES):
                    token, hi, lo = page(token)
                    c["newest"] = max(c["newest"], hi)
                    if lo <= seen_top or not token or lo < since_ms:
                        break
            if not c["back"]:
                # the rest of the way back (a first crawl starts at the top; an interrupted one resumes where it stopped)
                for _ in range(MAX_PAGES):
                    nxt, hi, lo = page(c["token"])
                    c["newest"] = max(c["newest"], hi)
                    c["token"] = nxt
                    if lo < since_ms or not nxt:
                        c["back"] = True
                        break
        except QuotaOut as e:
            print(f"[warn] {e}; the crawl continues on the next run", file=sys.stderr)
            return False
        except requests.HTTPError as e:
            print(f"[warn] playlist {pl}: {e}", file=sys.stderr)
            continue
        complete = complete and c["back"]
    return complete


def verify(q: Quota, cache: dict, ids: list[str]) -> None:
    """Duration and embeddability for the few uploads that look like a match (one unit per 50)."""
    need = [i for i in ids if i not in cache["vid"]]
    for i in range(0, len(need), 50):
        chunk = need[i:i + 50]
        j = q.get(f"{API}/videos", {"part": "contentDetails,status", "id": ",".join(chunk), "key": q.key})
        info = {v["id"]: v for v in j.get("items", [])}
        for vid in chunk:
            v = info.get(vid) or {}
            st = v.get("status") or {}
            cache["vid"][vid] = [duration_seconds((v.get("contentDetails") or {}).get("duration", "")), int(bool(st.get("embeddable")) and st.get("privacyStatus") == "public")]


def uploads_from_cache(cache: dict, playlists: list[str], windows: list[tuple[float, float]]) -> list[dict]:
    out, seen = [], set()
    for pl in playlists:
        c = cache["pl"].get(pl) or {}
        for vid, (pub_s, title) in (c.get("items") or {}).items():
            pub = pub_s * 1000
            if vid not in seen and any(a <= pub <= b for a, b in windows):
                seen.add(vid)
                out.append({"id": vid, "title": title, "pub_ms": pub, "ch": c.get("ch", ""), "dur": 0, "embeddable": True})
    return out


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
    playlists = [c["playlist"] for c in chans] or [NFL_UPLOADS]
    cache_path = os.path.join(root, "wp_clips", "uploads_cache.json.gz")
    cache = load_cache(cache_path)
    # Several runs a day (Tuesday has eight) share one 10,000-unit daily quota: spend only what the last 24 hours left over.
    now_s = now_ms / 1000
    cache["spent"] = [r for r in cache.get("spent", []) if now_s - r[0] < 86400]
    allowed = min(BUDGET, DAILY_UNITS - sum(r[1] for r in cache["spent"]))
    if allowed < 150:
        print(f"[info] only {allowed} API units left for today; clips wait for the next run")
        return
    q = Quota(allowed)
    q.key = key
    complete = crawl(q, cache, playlists, min(a for a, _ in windows), windows)
    uploads = uploads_from_cache(cache, playlists, windows)
    print(f"[info] {len(uploads)} uploads in the windows of {len(work)} week(s); crawl {'complete' if complete else 'to be continued'}; {allowed - q.left} API units used")
    # 1) by title alone, which uploads could be any play's clip; 2) look up only those; 3) match for real
    cand = set()
    plays_by_week = {}
    for d, w, doc, names in work:
        plays_by_week[(d, w)] = candidate_plays(doc, names)
        for p in plays_by_week[(d, w)]:
            for u in uploads:
                if u["pub_ms"] >= doc["t0"] + p["sec"] * 1000 and u["pub_ms"] <= doc["t0"] + p["sec"] * 1000 + 7 * 86400 * 1000 and score_title(u["title"], p["player"], p["team"], p["opp"], p["label"], p["def"], u["ch"]) >= MIN_SCORE:
                    cand.add(u["id"])
    try:
        verify(q, cache, sorted(cand))
    except QuotaOut as e:
        print(f"[warn] {e}; some clips will be checked on the next run", file=sys.stderr)
        complete = False
    for u in uploads:
        if u["id"] in cache["vid"]:
            u["dur"], u["embeddable"] = cache["vid"][u["id"]][0], bool(cache["vid"][u["id"]][1])
        else:
            u["embeddable"] = False   # not looked up yet (quota): not offered until it has been
    # an archived week is only marked done once every channel has been read all the way back
    wsig = sig if complete else "partial"
    manual = []
    try:
        manual = json.load(open(os.path.join(root, "clips_manual.json")))
    except Exception:  # noqa: BLE001
        pass
    # plays still without a clip get a YouTube search each (100 units), the biggest swings first and only while quota is left
    searches = max(0, min(MAX_SEARCHES, (q.left - 300) // 100))
    by_ch = {}
    touched, results = set(), []
    for d, w, doc, names in work:
        out_dir = os.path.join(d, "wp_clips")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"week_{w}.json")
        try:
            old = json.load(open(path))
            fresh = old.get("mv") == MATCHER
            have = (old.get("clips") or {}) if fresh else {}
            tried = set(old.get("tried") or []) if fresh else set()
        except Exception:  # noqa: BLE001
            have, tried = {}, set()
        plays = plays_by_week[(d, w)]
        # STACKED's film videos give the exact play and where it ends: they come first and win over anything found another way
        by_player = stacked_videos(uploads, int(doc.get("season") or 0), w)
        if by_player:
            try:
                fetch_chapters(q, cache, [u["id"] for us in by_player.values() for u in us])
                st_found = match_stacked(plays, by_player, cache, doc)
                have.update(st_found)
                vids_n = [u["id"] for us in by_player.values() for u in us]
                with_plays = sum(1 for v in vids_n if any(stacked_chapter(t) for _, t in (cache.get("chap", {}).get(v) or [])))
                print(f"[info] STACKED week {w}: {len(vids_n)} film videos, {with_plays} with play chapters, {len(st_found)} play(s) matched")
            except QuotaOut as e:
                print(f"[warn] {e}; STACKED chapters resume on a later run", file=sys.stderr)
        found = match_plays([p for p in plays if p["key"] not in have], uploads, doc["t0"])
        have.update(found)
        results.append((d, w, doc, have, tried, plays, len(found)))
    # chapters of longer highlight videos for the plays no short clip covered
    try:
        for i, (d, w, doc, have, tried, plays, n) in enumerate(results):
            games = [g for g in (doc.get("g") or {})]
            nicks = {n_ for g in games for t_ in g.split("@") for n_ in NICK.get(t_, [])}
            recaps = [u for u in uploads if RECAP.search(u["title"]) and any(n_ in norm(u["title"]) for n_ in nicks) and doc["t0"] <= u["pub_ms"] <= doc["t0"] + 8 * 86400 * 1000]
            if not recaps:
                continue
            fetch_chapters(q, cache, [u["id"] for u in recaps])
            m = match_chapters([p for p in plays[:SEARCH_TOP * 2] if p["key"] not in have], recaps, cache, doc["t0"])
            have.update(m)
            results[i] = (d, w, doc, have, tried, plays, n + len(m))
    except QuotaOut as e:
        print(f"[warn] {e}; chapters resume on a later run", file=sys.stderr)
    # searches go to the biggest plays across all the weeks being worked on
    gaps = sorted(((p["pts"], i, p) for i, (_, _, _, have, tried, plays, _) in enumerate(results) for p in plays[:SEARCH_TOP] if p["key"] not in have and p["key"] not in tried), key=lambda x: -x[0])
    quota_out = False
    for _, i, p in gaps:
        if searches <= 0 or quota_out:
            break
        d, w, doc, have, tried, plays, n = results[i]
        try:
            p["sib"] = any(o is not p and o["player"] == p["player"] and o["team"] == p["team"] and play_kind(o["label"])[:1] == play_kind(p["label"])[:1] for o in plays)
            ups = search_clips(q, p, doc["t0"])
            verify(q, cache, [u["id"] for u in ups])
            for u in ups:
                u["dur"], u["embeddable"] = (cache["vid"].get(u["id"]) or [0, 0])[0], bool((cache["vid"].get(u["id"]) or [0, 0])[1])
            m = match_plays([p], ups, doc["t0"], loose=True)
            have.update(m)
            tried.add(p["key"])
            searches -= 1
            results[i] = (d, w, doc, have, tried, plays, n + len(m))
        except QuotaOut as e:
            print(f"[warn] {e}; searching resumes on a later run", file=sys.stderr)
            quota_out = True
    cache["spent"].append([int(now_s), allowed - q.left])
    save_cache(cache_path, cache)
    for d, w, doc, have, tried, plays, n in results:
        out_dir = os.path.join(d, "wp_clips")
        apply_manual(have, plays, manual, int(doc.get("season") or 0), w)
        for c in have.values():
            by_ch[c.get("c", "?")] = by_ch.get(c.get("c", "?"), 0) + 1
        with open(os.path.join(out_dir, f"week_{w}.json"), "w") as fh:
            json.dump({"v": 1, "week": w, "clips": have, "tried": sorted(tried), "chs": wsig, "mv": MATCHER, "checked": datetime.now(timezone.utc).isoformat(timespec="seconds")}, fh, separators=(",", ":"))
        touched.add(out_dir)
        print(f"[info] {os.path.relpath(d, root) if d != root else 'current'} week {w}: {len(have)} clip(s) ({n} new)")
    print("[info] clips by channel: " + ", ".join(f"{k} {v}" for k, v in sorted(by_ch.items(), key=lambda kv: -kv[1])[:15]))
    for out_dir in touched:
        idx = sorted(int(f[5:-5]) for f in os.listdir(out_dir) if f.startswith("week_") and f.endswith(".json"))
        with open(os.path.join(out_dir, "index.json"), "w") as fh:
            json.dump({"weeks": idx}, fh, separators=(",", ":"))


if __name__ == "__main__":
    try:
        run(force="--force" in sys.argv)
    except Exception as e:  # noqa: BLE001 - clips are an extra; never fail the build
        print(f"[warn] clips skipped: {e}", file=sys.stderr)
