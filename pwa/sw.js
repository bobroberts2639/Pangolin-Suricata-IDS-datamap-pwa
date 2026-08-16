/* Service worker for Suricata Alert Datamap.
   Cache version is derived from a hash of the shell + data, so publishing new data
   busts the cache automatically and old caches are deleted on activate. */
const VERSION = "999e472f29b6";
const CACHE = "suricata-datamap-" + VERSION;
const ASSETS = [
  "./",
  "index.html",
  "manifest.webmanifest",
  "data/datamap.json",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/icon-180.png",
  "icons/icon-512-maskable.png"
];

self.addEventListener("install", (e) => {
  // Full precache: the whole map is available offline once installed.
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    await c.addAll(ASSETS);
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k.startsWith("suricata-datamap-") && k !== CACHE)
                          .map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // never touch the rule-lookup links

  // Navigations fall back to the cached shell so deep links work offline.
  if (req.mode === "navigate") {
    e.respondWith((async () => {
      try { return await fetch(req); }
      catch (err) {
        const c = await caches.open(CACHE);
        return (await c.match("index.html")) || Response.error();
      }
    })());
    return;
  }

  e.respondWith((async () => {
    const c = await caches.open(CACHE);
    const hit = await c.match(req, { ignoreSearch: true });
    if (hit) return hit;
    try {
      const res = await fetch(req);
      if (res.ok && res.type === "basic") c.put(req, res.clone());
      return res;
    } catch (err) {
      return hit || Response.error();
    }
  })());
});
