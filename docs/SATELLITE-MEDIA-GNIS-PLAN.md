# Plan: satellite data, media/video, and GNIS place names

> 2026-07-22: GNIS partially built (`pipeline/geocode.py` + `/gnis` endpoints); FIRMS still key-gated.

Three additions that share one theme — giving the ledger **eyes and a map**.
Today the system reads text. These let it see fire from orbit, show pictures and
video of it, and know where a place name actually is.

Ordered by value per unit of work. Nothing here is committed yet.

---

## Part 1 — GNIS: the missing geo signal (highest value, no keys, do first)

### Why this matters more than it sounds

Attribution scores facts against incidents on name (0.5), geo (0.35), time
(0.15). Measured on the live ledger, **almost every media fact scores geo =
0.0** — the extractor gets "near Loyalton", "Arrowhead Mine Road", "the Marble
Mountain Wilderness", but never coordinates. A third of the scoring model is
dead weight, which is why the adjudication queue fills with items whose only
signal is a weak name match.

USGS **GNIS** (Geographic Names Information System) is the authoritative
gazetteer of US place names: every populated place, ridge, creek, canyon, and
wilderness area with coordinates. It is public domain, needs no key, and CA is
a small download.

Two distinct wins:

1. **Geocode location text** → facts get lat/lon → the geo signal starts
   working → fewer adjudication items, better corroboration clustering.
2. **Resolve fire names.** California fires are named after nearby features —
   Biscar, Elephant, Pit, Coast, Thorn, Dove. GNIS can turn "Dove Fire" into
   "there is a Dove Creek in Butte County at 39.7/-121.6", which gives a brand
   new fire an approximate location *before any official source lists it*.
   That is exactly what the WATCH board needs and currently lacks.

### Approach

- Fetch the California GNIS file once (National Map staged products,
  pipe-delimited). Load into `gnis_features(feature_id, name, class, county,
  lat, lon, elevation)` via a migration + one-shot loader command. ~90k CA rows,
  a few MB — trivial for SQLite, and it ships offline (PSPS-safe, no API call
  at query time).
- `pipeline/geocode.py`: `resolve_place(name, county=None, classes=(...))`
  returning ranked candidates. Prefer populated places and prominent natural
  features; require a county match when we have one; return nothing rather than
  guess between equally-good matches (a wrong pin is worse than none).
- Wire into: (a) the extract stage — when a fact has `location_text` but no
  coords, attach `lat/lon` with `geo_method='geocoded_text'` (the schema already
  has this enum value); (b) the watch board — resolve the fire name to seed a
  candidate's location; (c) attribution — which then starts using its geo term.
- **Confidence discipline:** geocoded coordinates are weaker evidence than EXIF
  or an official perimeter. Cap the geo contribution for `geocoded_text` so a
  place-name match can never alone push a fact over the auto-attribute
  threshold.

### Verify

Replay the current adjudication backlog: how many items gain coordinates, and
does auto-attribution rise without new false positives? Spot-check that "Dove
Fire" lands in Butte County and that ambiguous names (there are many "Mill
Creek"s) return no answer rather than a wrong one.

---

## Part 2 — Satellite

### 2a. NASA FIRMS — do this the moment the key exists

Already built ([`sources/firms.py`](backend/src/toast/sources/firms.py)),
CA-bboxed, seeded but disabled pending `FIRMS_MAP_KEY`. Hotspots are frequently
the **earliest** signal of a new start — before any newsroom writes anything.

Work remaining beyond the key:
- **Hotspot clustering.** Raw VIIRS pixels are noise individually. Cluster by
  space+time (DBSCAN-ish over a small radius / few hours), and treat a cluster
  that persists across two satellite passes as a watch-board signal — persistence
  is what separates a fire from a flare, a solar farm, or a factory roof.
- **Known-false-positive suppression.** Industrial sites, flares, and solar
  installations light up repeatedly at the same coordinates. Track a
  `hotspot_ignore` list learned from clusters that never become fires.
- Feed clusters into the existing WATCH board as a new signal type, and into
  incident intel as "N hotspots in the last 12h" per fire.

### 2b. NOAA GOES — smoke and growth, no key

Geostationary, ~5–10 minute refresh. Two uses:
- **Map layer:** GOES-West imagery as a background tile/overlay for the OPS map
  (currently an offline vector outline).
- **Smoke plume presence:** a coarse "smoke over this county" signal.
GOES data is on the NOAA open-data cloud program (no key, no quota). Cost is
bandwidth and some raster handling, so this is a Phase-later item.

### 2c. NASA GIBS / Worldview — easiest map imagery

WMTS/WMS tiles designed for exactly this: drop MODIS/VIIRS true-color,
thermal-anomaly, and aerosol layers straight onto a map as a tile URL. **This is
the cheapest visual upgrade available** — no processing, no storage, just tile
URLs — and pairs naturally with the planned MapLibre/PMTiles map. Caveat: needs
network, so PSPS degraded mode must fall back to the offline outline.

### 2d. Copernicus Sentinel — perimeters and smoke-penetrating radar

Free registration. Sentinel-2 optical at ~10 m is good enough to see burn scars
and rough perimeters; **Sentinel-1 radar sees through smoke and cloud**, which
optical cannot. Revisit is days, not minutes, so this is a *retrospective and
perimeter* tool, not a detection tool. Best used on-demand per incident rather
than polled.

### 2e. USGS Landsat — historical burn scars

STAC search over the free archive. Right tool for retrospectives and
burn-severity comparison on frozen incidents; wrong tool for live tracking
(16-day revisit). Lowest priority, highest archival value.

**Recommended satellite order:** FIRMS (detection) → GIBS tiles (visual) →
hotspot clustering → GOES (smoke) → Sentinel on-demand → Landsat retrospective.

---

## Part 3 — Media: images and video

The `media` table already exists in the schema (raw_ref, exif, vlm_analysis,
lat/lon, geo_method, **credit**) and is unused. The pipeline plan already
anticipated EXIF → VLM placement → reverse-image checking.

### Images — licensing is the hard part, not the code

**Do not scrape news photos.** They are copyrighted, and the mission's legal
hygiene rule (preserve credit, respect TOS) is explicit. Genuinely free sources:

| Source | Terms | Value |
|---|---|---|
| **InciWeb incident photos** | public domain (federal) | official fire photos, already a T2 source we poll |
| **CAL FIRE / USFS / NPS releases** | public domain | operational imagery, aerial shots |
| **NASA/NOAA satellite imagery** | public domain | plume and scar imagery |
| **Wikimedia Commons** | CC-BY / CC-BY-SA | historical fires; attribution mandatory |
| **Operator-submitted / own photos** | ours | Paradise-area ground truth |

Everything else: link out, never rehost. Store `credit` and a license field on
every media row, and have the site renderer refuse to display media lacking one.

### Video — YouTube

- **YouTube RSS per channel** (`/feeds/videos.xml?channel_id=…`) needs **no API
  key or quota** — the cheapest path, and it fits the existing RSS adapter
  almost unchanged. Curate a channel list (CAL FIRE, county OES, local news,
  known fire-watchers).
- **YouTube Data API** search needs a key with a 10k-unit/day quota (a search
  costs 100 units → ~100 searches/day). Worth adding only if per-fire *search*
  is needed beyond channel following.
- **Never download video.** Store the link, title, channel, publish time, and
  thumbnail; embed by URL. That sidesteps both TOS and disk.

### Intake color

The operator asked for a new intake color for media. Proposed, consistent with
the existing kind chips: **images = `C.blue`**, **video = `C.violet`**, each with
a small ⧉/▶ glyph, and a MEDIA filter chip in the intake toolbar. Media rows
click through to the source exactly like fact rows do now.

### Trust posture for media

Images are the easiest vector for misinformation — recycled photos from old
fires circulate every season. The plan's defenses stay mandatory: EXIF
extraction, reverse-image check against our own archive, VLM description, and a
**visible provenance line** on anything published. A photo whose origin cannot
be established is a T5 signal, never illustration for a report.

---

## Suggested sequence

1. **GNIS** — no key, unblocks attribution and gives the watch board location.
2. **FIRMS** (operator adds key) + hotspot clustering — earliest detection.
3. **YouTube channel RSS** — no key, immediate media coverage.
4. **GIBS tiles** — biggest visual upgrade for the least work.
5. Public-domain image ingest (InciWeb first) with credit/license enforcement.
6. GOES smoke, Sentinel on-demand, Landsat retrospective.

Sources: [USGS GNIS downloads](https://www.usgs.gov/us-board-on-geographic-names/download-gnis-data),
[NASA FIRMS API](https://firms.modaps.eosdis.nasa.gov/api/),
[NASA GIBS](https://nasa-gibs.github.io/gibs-api-docs/),
[Copernicus Data Space](https://dataspace.copernicus.eu/),
[NOAA GOES open data](https://www.noaa.gov/nodd).
