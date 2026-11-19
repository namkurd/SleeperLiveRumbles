#!/usr/bin/env python3
"""Cleans the saved highlight clips: takes out press conferences and other talk, and takes out videos that have been taken down.

build_wp_clips.py finds a clip once and keeps it, so a video that is later deleted or made private would stay on as a dead
button, and an interview that slipped past the title matcher would stay on as a "highlight". This pass runs over every week of
every season (wp_clips/week_N.json, and seasons/*/wp_clips/):

  1. interviews: a clip whose title reads like a press conference, post-game interview, locker-room chat or breakdown, or whose channel
     is mostly talk (clip_filters.py) is dropped.
  2. still uploaded: every video id is checked against YouTube (the Data API when YOUTUBE_API_KEY is set, 1 unit per 50 videos; the
     keyless oEmbed endpoint otherwise). A video that is gone, private or rejected is dropped. A video that merely refuses embedding
     is kept (the page links out to YouTube for it). A check that fails (network trouble) changes nothing.
  Each play keeps its next clip (an alternate, or STACKED's exact moment) when its main one is dropped; a play left with none has no
  play button. Answers are cached in wp_clips/live.json (alive videos are re-checked after 7 days), so a run costs almost nothing.

    python3 audit_wp_clips.py            # checks and cleans
    python3 audit_wp_clips.py --dry      # prints what it would drop, changes nothing
    python3 audit_wp_clips.py --titles   # only the interview/talk filter (no YouTube lookups)
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from clip_filters import not_a_clip

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://www.googleapis.com/youtube/v3/videos"
RECHECK_SECONDS = 7 * 86400
BAD_UPLOAD = {"deleted", "failed", "rejected"}


def clip_dicts(c: dict) -> list[dict]:
    return [c] + list(c.get("alt") or []) + ([c["x"]] if c.get("x") else [])


def check_api(ids: list[str], key: str) -> dict[str, bool | None]:
    """id -> True (up) / False (gone, private or rejected) / None (no answer). One unit per 50 ids."""
    out: dict[str, bool | None] = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            r = requests.get(API, params={"part": "status", "id": ",".join(chunk), "key": key}, timeout=30)
            r.raise_for_status()
            got = {v["id"]: v.get("status") or {} for v in r.json().get("items", [])}
        except Exception as e:  # noqa: BLE001
            print(f"[warn] YouTube API: {e}", file=sys.stderr)
            continue
        for vid in chunk:
            st = got.get(vid)
            out[vid] = bool(st) and st.get("privacyStatus") in ("public", "unlisted") and st.get("uploadStatus") not in BAD_UPLOAD
    return out


def check_oembed(ids: list[str]) -> dict[str, bool | None]:
    """Keyless: oEmbed answers 200 for a public video, 401 when only embedding is off (still up), 404 when it is gone or private."""
    def one(vid: str) -> tuple[str, bool | None]:
        try:
            r = requests.get("https://www.youtube.com/oembed", params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"}, timeout=20)
        except Exception:  # noqa: BLE001
            return vid, None
        if r.status_code in (200, 401, 403):
            return vid, True
        if r.status_code in (404, 400):
            return vid, False
        return vid, None
    with ThreadPoolExecutor(8) as ex:
        return dict(ex.map(one, ids))


def drop(c: dict, bad) -> dict | None:
    """The play's clip without the clips `bad(clip)` condemns: the next one steps up; None when nothing is left."""
    cands = [x for x in [c] + list(c.get("alt") or []) if not bad(x)]
    stk = c.get("x") if c.get("x") and not bad(c["x"]) else None
    if not cands:
        return dict(stk) if stk else None
    new = {k: v for k, v in cands[0].items() if k not in ("alt", "x")}
    alts = [{k: v for k, v in x.items() if k not in ("alt", "x")} for x in cands[1:]]
    if alts:
        new["alt"] = alts
    if stk:
        new["x"] = stk
    return new


def main() -> None:
    dry = "--dry" in sys.argv
    files = sorted(glob.glob(os.path.join(HERE, "wp_clips", "week_*.json")) + glob.glob(os.path.join(HERE, "seasons", "*", "wp_clips", "week_*.json")))
    docs = {f: json.load(open(f)) for f in files}
    ids = sorted({x["id"] for d in docs.values() for c in d.get("clips", {}).values() for x in clip_dicts(c) if x.get("id")})
    live_path = os.path.join(HERE, "wp_clips", "live.json")
    try:
        live = json.load(open(live_path))
    except Exception:  # noqa: BLE001
        live = {}
    now = time.time()
    need = [i for i in ids if i not in live or (live[i][1] and now - live[i][0] > RECHECK_SECONDS)]
    key = os.environ.get("YOUTUBE_API_KEY")
    if "--titles" in sys.argv:
        need = []
    if need:
        res = check_api(need, key) if key else check_oembed(need)
        for vid, ok in res.items():
            if ok is not None:
                live[vid] = [now, int(ok)]
        print(f"[info] checked {len(need)} videos ({'API' if key else 'oEmbed'}); {sum(1 for v in need if live.get(v, [0, 1])[1] == 0)} gone")
    gone = {i for i, v in live.items() if v[1] == 0}
    changed = interviews = removed = 0
    for f, d in docs.items():
        clips = d.get("clips", {})
        new_clips = {}
        for k, c in clips.items():
            def bad_title(x):     # STACKED's exact-moment video ("x") is a film upload, never an interview
                return x is not c.get("x") and not_a_clip(x.get("t") or "", x.get("c"))
            n0 = len(clip_dicts(c))
            c2 = drop(c, lambda x: bad_title(x))
            if c2 is not None:
                c3 = drop(c2, lambda x: x.get("id") in gone)
            else:
                c3 = None
            interviews += n0 - len(clip_dicts(c2)) if c2 else n0
            if c3 is not None:
                removed += len(clip_dicts(c2)) - len(clip_dicts(c3))
                new_clips[k] = c3
            else:
                removed += len(clip_dicts(c2)) if c2 else 0
        if new_clips != clips:
            changed += 1
            print(f"[info] {os.path.relpath(f, HERE)}: {len(clips)} -> {len(new_clips)} plays with a clip")
            if not dry:
                d["clips"] = new_clips
                json.dump(d, open(f, "w"), separators=(",", ":"))
    print(f"[info] dropped {interviews} interview/talk clips and {removed} taken-down clips in {changed} weeks" + (" (dry run)" if dry else ""))
    if not dry:
        json.dump(live, open(live_path, "w"), separators=(",", ":"))


if __name__ == "__main__":
    main()
