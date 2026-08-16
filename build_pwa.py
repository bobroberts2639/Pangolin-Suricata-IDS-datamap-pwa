#!/usr/bin/env python3
"""Build a hostable Progressive Web App from the rendered Suricata datamap.

Takes the single-file HTML (payload inlined) and splits it into an app shell plus a
fetched data file, then adds the PWA scaffolding:

    pwa/
      index.html                 app shell: the viewer, data loaded by fetch()
      data/datamap.json          the point payload (~8.5 MB, gzipped by the host)
      manifest.webmanifest       installability metadata
      sw.js                      service worker: precache shell + data, offline-capable
      icons/*.png                app icons, rendered from the real layout coordinates
      .nojekyll                  stop GitHub Pages running the files through Jekyll
      README.md                  hosting + deploy notes

Why split the data out: the single file precaches fine, but every shell tweak would
re-download all 8.5 MB. Split, the service worker versions them independently.
The original single-file HTML is left untouched -- it stays the portable, file://
artifact; the PWA is additive and must be served over HTTP(S).

All paths are relative so the app works from a GitHub Pages project subpath
(https://user.github.io/repo/) as well as from a domain root.
"""
import hashlib
import json
import os
import re
import shutil
import sys

BASE = os.environ.get("SURICATA_DATAMAP_DIR",
                      "/sessions/happy-clever-brown/mnt/Suricata - Datamap")
DATE = "2026-08-12"
SRC_HTML = os.path.join(BASE, f"suricata_alert_datamap_{DATE}.html")
PREVIEW = os.path.join(BASE, f"suricata_alert_datamap_{DATE}_preview.png")
LAYOUT = os.path.join(BASE, "layout_coords.npy")
OUT = os.path.join(BASE, "pwa")

APP_NAME = "Suricata Alert Datamap"
APP_SHORT = "Suricata Map"
APP_DESC = ("Interactive map of 59,579 deduplicated Suricata IDS/IPS alerts from four "
            "packet captures, with hierarchical cluster labels and per-alert rule lookups.")
BG = "#10101a"
FG = "#e8e8f0"
PALETTE = ["#4cc9f0", "#b388ff", "#ffd166", "#ff2e63"]


def log(m):
    print(f"  {m}")


# --------------------------------------------------------------- 1. split payload out
def split_payload():
    html = open(SRC_HTML, encoding="utf-8").read()
    m = re.search(r"const DATA = (\{.*?\});\n", html, re.S)
    if not m:
        sys.exit("could not find the inlined payload in " + SRC_HTML)
    payload = json.loads(m.group(1))

    # the shell is everything except the payload assignment, wrapped so it can await
    # the fetch. Declarations inside the IIFE keep the same relative scope they had
    # at top level, so no other line of the viewer needs to change.
    start = html.index("<script>") + len("<script>")
    end = html.index("</script>", start)
    script = html[start:end]
    script = script.replace(m.group(0), "", 1)
    script = script.replace(
        'if (!DATA) { document.body.innerHTML = '
        '"<p style=\'padding:2em\'>No data payload embedded.</p>"; }\n', "", 1)
    return html[:start], script, html[end:], payload


BOOT = """
"use strict";
(async function boot() {
  const status = document.getElementById("boot");
  let DATA;
  try {
    const r = await fetch("data/datamap.json", { cache: "force-cache" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    DATA = await r.json();
  } catch (err) {
    status.innerHTML = "<b>Could not load the map data.</b><br><br>" +
      "This app must be served over HTTP(S) \\u2014 opening index.html straight from " +
      "disk blocks the fetch.<br>Run <code>python3 -m http.server</code> in this " +
      "folder, or use the hosted URL.<br><br><small>" + String(err) + "</small>";
    status.className = "err";
    return;
  }
  status.remove();
%%SCRIPT%%
})();
"""


def build_shell(head, script, tail):
    boot = BOOT.replace("%%SCRIPT%%", script.replace('"use strict";', "", 1))
    pwa_head = f"""
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="{BG}">
<meta name="description" content="{APP_DESC}">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="{APP_SHORT}">
<link rel="apple-touch-icon" href="icons/icon-180.png">
<link rel="icon" type="image/png" sizes="192x192" href="icons/icon-192.png">
<style>
  #boot {{ position:absolute; inset:0; display:flex; flex-direction:column;
    align-items:center; justify-content:center; text-align:center; padding:2em;
    font: 14px/1.6 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    color:{FG}; background:{BG}; z-index:50; }}
  #boot.err {{ max-width:none; }}
  #boot code {{ background:#8883; padding:1px 5px; border-radius:4px; }}
  #boot .spin {{ width:26px; height:26px; margin-bottom:14px; border-radius:50%;
    border:3px solid #8884; border-top-color:{PALETTE[0]}; animation:sp 0.9s linear infinite; }}
  @keyframes sp {{ to {{ transform:rotate(360deg); }} }}
</style>
"""
    head = head.replace("</head>", pwa_head + "</head>", 1)
    head = head.replace("<script>", "", 1)          # reopened below, after the boot div
    body_marker = '<div id="footer">'
    boot_div = ('<div id="boot"><div class="spin"></div>'
                '<div>Loading 59,579 alerts…</div></div>\n')
    head = head.replace(body_marker, boot_div + body_marker, 1)
    reg = """
<script>
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(e => console.warn("SW:", e));
  });
}
</script>
"""
    return head + "<script>" + boot + tail.replace("</body>", reg + "</body>", 1)


# --------------------------------------------------------------- 2. icons
def build_icons(icon_dir):
    from PIL import Image, ImageDraw
    import numpy as np
    os.makedirs(icon_dir, exist_ok=True)
    coords = np.load(LAYOUT)

    # Colour by the real capture, same palette as the map's legend, so the icon is a
    # genuine thumbnail of the data rather than decoration.
    order = ["wrccdc-2017", "wrccdc-2018", "first-org-2015", "honeypot-2018"]
    colour_of = dict(zip(order, PALETTE))
    src = None
    corpus = os.path.join(BASE, "suricata_alerts_corpus.csv")
    if os.path.exists(corpus):
        import csv as _csv
        with open(corpus, newline="", encoding="utf-8") as fh:
            r = _csv.DictReader(fh)
            src = [row["source"] for row in r]
        if len(src) != len(coords):
            src = None

    rng = np.random.default_rng(42)
    idx = rng.choice(len(coords), size=min(4000, len(coords)), replace=False)

    # Frame on the DENSE CORE, not the full extent. The layout is a diagonal band with a
    # far-off Dropbox island; framing the whole thing leaves the art in one corner and
    # unreadable at 192 px. The 4-96 percentile window crops to the mass and fills the tile.
    x0, x1 = np.percentile(coords[:, 0], [4, 96])
    y0, y1 = np.percentile(coords[:, 1], [4, 96])
    span = max(x1 - x0, y1 - y0)                    # square window keeps the aspect honest
    cx0, cy0 = (x0 + x1) / 2, (y0 + y1) / 2
    x0, x1 = cx0 - span / 2, cx0 + span / 2
    y0, y1 = cy0 - span / 2, cy0 + span / 2

    keep = ((coords[idx, 0] >= x0) & (coords[idx, 0] <= x1) &
            (coords[idx, 1] >= y0) & (coords[idx, 1] <= y1))
    sel = idx[keep]
    pts = coords[sel]
    cols = ([colour_of.get(src[i], PALETTE[0]) for i in sel] if src
            else [PALETTE[i % len(PALETTE)] for i in sel])
    log(f"icon: {len(pts):,} points, coloured by {'source' if src else 'index'}")

    def render(size, pad_frac, out):
        S = size * 4                                   # supersample, then downscale
        img = Image.new("RGBA", (S, S), BG)
        d = ImageDraw.Draw(img)
        pad = S * pad_frac
        w, h = S - 2 * pad, S - 2 * pad
        sc = min(w / max(x1 - x0, 1e-9), h / max(y1 - y0, 1e-9))
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        r = max(3, int(S / 95))          # fat enough to read at 192 px in a launcher
        for (px, py), c in zip(pts, cols):
            sx = S / 2 + (px - cx) * sc
            sy = S / 2 - (py - cy) * sc
            d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=c)
        img = img.resize((size, size), Image.LANCZOS)
        img.convert("RGB").save(out, "PNG", optimize=True)
        return out

    made = []
    made.append(render(192, 0.10, os.path.join(icon_dir, "icon-192.png")))
    made.append(render(512, 0.10, os.path.join(icon_dir, "icon-512.png")))
    made.append(render(180, 0.10, os.path.join(icon_dir, "icon-180.png")))
    # maskable icons get cropped to a circle by the launcher: keep art inside ~80%
    made.append(render(512, 0.22, os.path.join(icon_dir, "icon-512-maskable.png")))
    return made


# --------------------------------------------------------------- 3. manifest + sw
MANIFEST = {
    "name": APP_NAME,
    "short_name": APP_SHORT,
    "description": APP_DESC,
    "start_url": "./",
    "scope": "./",
    "display": "standalone",
    "orientation": "any",
    "background_color": BG,
    "theme_color": BG,
    "categories": ["security", "utilities", "productivity"],
    "icons": [
        {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png",
         "purpose": "any"},
        {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any"},
        {"src": "icons/icon-512-maskable.png", "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ],
}

SW = """/* Service worker for %(name)s.
   Cache version is derived from a hash of the shell + data, so publishing new data
   busts the cache automatically and old caches are deleted on activate. */
const VERSION = "%(version)s";
const CACHE = "suricata-datamap-" + VERSION;
const ASSETS = %(assets)s;

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
"""


def main():
    # Overwrite in place rather than rmtree: some mounted/synced folders permit writes
    # but refuse unlink, and a half-deleted bundle is worse than a stale one.
    os.makedirs(os.path.join(OUT, "data"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "icons"), exist_ok=True)

    print("building PWA bundle")
    head, script, tail, payload = split_payload()

    data_path = os.path.join(OUT, "data", "datamap.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    log(f"data/datamap.json        {os.path.getsize(data_path)/1e6:7.2f} MB "
        f"({len(payload['points']):,} points)")

    shell = build_shell(head, script, tail)
    idx_path = os.path.join(OUT, "index.html")
    open(idx_path, "w", encoding="utf-8").write(shell)
    log(f"index.html               {os.path.getsize(idx_path)/1e3:7.1f} KB (shell only)")

    icons = build_icons(os.path.join(OUT, "icons"))
    for i in icons:
        log(f"{os.path.relpath(i, OUT):24s} {os.path.getsize(i)/1e3:7.1f} KB")

    open(os.path.join(OUT, "manifest.webmanifest"), "w", encoding="utf-8").write(
        json.dumps(MANIFEST, indent=2))
    log("manifest.webmanifest")

    if os.path.exists(PREVIEW):
        shutil.copy(PREVIEW, os.path.join(OUT, "preview.png"))
        log("preview.png")

    assets = ["./", "index.html", "manifest.webmanifest", "data/datamap.json",
              "icons/icon-192.png", "icons/icon-512.png", "icons/icon-180.png",
              "icons/icon-512-maskable.png"]
    h = hashlib.sha256()
    h.update(open(idx_path, "rb").read())
    h.update(open(data_path, "rb").read())
    version = h.hexdigest()[:12]
    open(os.path.join(OUT, "sw.js"), "w", encoding="utf-8").write(
        SW % {"name": APP_NAME, "version": version,
              "assets": json.dumps(assets, indent=2)})
    log(f"sw.js                    cache version {version}")

    open(os.path.join(OUT, ".nojekyll"), "w").write("")
    log(".nojekyll")

    total = sum(os.path.getsize(os.path.join(r, f))
                for r, _, fs in os.walk(OUT) for f in fs)
    print(f"\n  bundle total: {total/1e6:.2f} MB in {OUT}")
    return version


if __name__ == "__main__":
    main()
