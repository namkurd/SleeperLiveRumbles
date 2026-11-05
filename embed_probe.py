"""Does a YouTube video really play inside another site?

YouTube's API says "embeddable" for videos whose player then shows error 150 ("the owner does not allow embedded playback"), so
the only reliable test is to load the video in the real embedded player. This opens one headless Chromium page with YouTube's
IFrame API and asks it for many videos at once, reading each player's ready / error event.

Results are cached by the caller (id -> 1 plays, 0 blocked); a video that gave no answer in time stays unknown (None) and is
not cached. If the browser cannot start (no Playwright, no network) every answer is None and the caller carries on as before.
"""
from __future__ import annotations

import os
import sys
import time

PAGE = """<!doctype html><div id=box></div><script>
window.res = {};
function mk(v) {
  var d = document.createElement('div'); d.id = 'p' + v; document.getElementById('box').appendChild(d);
  new YT.Player(d.id, {width: 160, height: 90, videoId: v, events: {
    onReady: function () { window.res[v] = 'ready'; },
    onError: function (e) { window.res[v] = 'err' + e.data; }}});
}
window.onYouTubeIframeAPIReady = function () { window.go = function (l) { l.forEach(mk); }; };
</script><script src="https://www.youtube.com/iframe_api"></script>"""

BLOCKED = {"err101", "err150", "err100", "err153"}   # embedding refused, or the video is gone
BATCH = 30
WAIT_SECONDS = 14


class EmbedProbe:
    def __init__(self, store: dict, seconds: float = 15 * 60):
        self.store = store              # id -> 1 / 0, shared with (and saved by) the caller's cache
        self.deadline = time.time() + seconds
        self.pw = self.browser = self.page = None
        self.dead = False
        self.asked = self.answered = 0

    # --- browser -------------------------------------------------------------------------------------------
    def _launch(self) -> bool:
        """A browser and a fresh page with the player API loaded (an old page is dropped: many iframes make a page slow and then crash it)."""
        if self.dead:
            return False
        try:
            if self.browser is None:
                from playwright.sync_api import sync_playwright
                self.pw = sync_playwright().start()
                kw = {"args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--mute-audio"]}
                exe = os.environ.get("EMBED_CHROME")
                if exe:
                    kw["executable_path"] = exe
                proxy = os.environ.get("HTTPS_PROXY")
                if proxy and os.environ.get("EMBED_USE_PROXY"):
                    kw["proxy"] = {"server": proxy}
                self.browser = self.pw.chromium.launch(**kw)
            if self.page is not None:
                try:
                    self.page.close()
                except Exception:  # noqa: BLE001
                    pass
                self.page = None
            self.page = self.browser.new_page()
            self.page.route("http://embed.test/**", lambda r: r.fulfill(body=PAGE, content_type="text/html"))
            self.page.goto("http://embed.test/p.html", wait_until="load")
            self.page.wait_for_function("() => !!window.go", timeout=30000)
            return True
        except Exception as e:  # noqa: BLE001 - no browser: carry on without the check
            print(f"[warn] embed check browser failed ({type(e).__name__}: {str(e)[:120]})", file=sys.stderr)
            self.close()
            return False

    def close(self) -> None:
        for o, m in ((self.page, "close"), (self.browser, "close"), (self.pw, "stop")):
            try:
                if o:
                    getattr(o, m)()
            except Exception:  # noqa: BLE001
                pass
        self.page = self.browser = self.pw = None

    # --- the check -----------------------------------------------------------------------------------------
    def known(self, vid: str):
        v = self.store.get(vid)
        return None if v is None else bool(v)

    def check(self, ids: list[str]) -> dict:
        """{id: True (plays) | False (blocked) | None (not known)} for every id."""
        todo = [i for i in dict.fromkeys(ids) if i not in self.store]
        fails = 0
        b0 = 0
        while b0 < len(todo):
            if time.time() > self.deadline or self.dead:
                break
            batch = todo[b0:b0 + BATCH]
            try:
                if not self._launch():       # a fresh page for every batch
                    fails += 1
                    if fails >= 3:
                        self.dead = True
                    continue
                self.page.evaluate("l => window.go(l)", batch)
                t_end = time.time() + WAIT_SECONDS
                while time.time() < t_end:
                    self.page.wait_for_timeout(500)
                    if self.page.evaluate("n => Object.keys(window.res).length >= n", len(batch)):
                        break
                res = self.page.evaluate("() => window.res")
            except Exception as e:  # noqa: BLE001 - a crashed page: start again with a new browser, three failures in a row give up
                fails += 1
                print(f"[warn] embed check hiccup ({type(e).__name__}: {str(e)[:100]})", file=sys.stderr)
                self.close()
                if fails >= 3:
                    self.dead = True
                continue
            fails = 0
            b0 += BATCH
            self.asked += len(batch)
            for v, r in res.items():
                if r == "ready":
                    self.store[v] = 1
                elif r in BLOCKED:
                    self.store[v] = 0
                self.answered += 1
        return {i: self.known(i) for i in ids}
