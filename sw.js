// Rumble Central: minimal service worker. It exists so phones offer "Add to Home Screen" /
// "Install app". It deliberately caches nothing, so the page and live scores are always fresh.
self.addEventListener("fetch", function () {});
