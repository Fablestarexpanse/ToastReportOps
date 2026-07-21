# API Keys — what to register, what each unlocks

Every key is free-tier unless marked. Add each to `.env` (never commit it),
then restart the stack (`docker compose --profile desktop up -d`). Sorted by
value-per-minute-of-signup.

## Do these first (minutes each)

| Key | Register at | `.env` variable | Unlocks |
|---|---|---|---|
| **NASA FIRMS map key** | https://firms.modaps.eosdis.nasa.gov/api/ (instant email key) | `FIRMS_MAP_KEY` | Satellite hotspot detection (VIIRS) — often the earliest automated signal of a new start; feeds the watch board. After adding: `docker compose exec worker python -m toast.seed` re-run enables the source, or flip it ON in SOURCES. |
| **Bluesky app password** | bsky.app → Settings → Privacy & Security → App Passwords | `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` | Easiest social auto-posting (atproto API, no review process). Publisher adapter posts approved SOCIAL drafts. |

> **Web hosting is not on this list.** The newsroom does not push to any
> website — you publish to your own site by hand (NEWSROOM → ⧉ COPY, or
> `/drafts/<id>/export`). Nothing to register until you decide to wire a host
> in.

## Social publishing (more setup)

| Key | Register at | `.env` variable | Notes |
|---|---|---|---|
| **X / Twitter** | developer.x.com → Free tier | `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` | Free tier allows ~500 posts/mo (writes only) — fine for incident updates. App must be created under your account; generate user-context tokens. |
| **Facebook Page** | developers.facebook.com → create app → Pages API | `FB_PAGE_ID`, `FB_PAGE_TOKEN` | Requires **app review** for `pages_manage_posts` — weeks of lead time. Start the application early; biggest local (Butte County) audience. |

## Maps & environment layers (for the OPS map upgrades)

| Key | Register at | `.env` variable | Unlocks |
|---|---|---|---|
| **Telegram** | @BotFather (free, 2 min) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `TELEGRAM_OPS_CHAT_ID` | Publish channel + phone alerts + approve-from-chat. Make the bot admin of your channel; get the ops chat id by messaging the bot and reading /getUpdates. |
| **AirNow** | docs.airnowapi.org (free) | `AIRNOW_API_KEY` | Smoke / AQI layer per incident area. |
| **MapTiler or Protomaps** (optional) | maptiler.com free tier / protomaps (self-host PMTiles, no key) | `MAPTILER_KEY` (if used) | Real basemap tiles for the OPS map. Recommendation: **Protomaps PMTiles self-hosted** — no key, works offline during PSPS (plan-aligned); MapTiler only if you want hosted raster tiles. |
| **OpenWeatherMap** (optional) | openweathermap.org (free) | `OWM_API_KEY` | Simple wind/temp overlays. Note RAWS/HRRR (planned WEATHER tab) are keyless public feeds — this is only a convenience layer. |

## Air attack / aircraft (decision, not just a key)

| Option | Cost | Notes |
|---|---|---|
| **airplanes.live / adsb.fi API** | free, fair-use | Community ADS-B aggregators; verify current fair-use terms; fine for tanker/air-attack orbit detection at our poll rates. |
| **ADSBExchange** | paid (RapidAPI) | Formerly free; skip unless the free aggregators prove insufficient. |
| **Own receiver** | ~$30 hardware once | RTL-SDR dongle + antenna feeding your own data — unlimited, offline-capable, and feeding aggregators usually earns free API access. Pairs with the future scanner-audio SDR decision. |

## YouTube channels (decision, not a key)

CAL FIRE units, county OES, and local TV post footage and press conferences on
YouTube, and every channel has a public, keyless, quota-free feed:
`https://www.youtube.com/feeds/videos.xml?channel_id=UC…`

**But** `youtube.com/robots.txt` says `Disallow: /feeds/videos.xml` for every
crawler. The feed is public and unauthenticated, and nothing technical stops us
— but that line is an explicit "no automated fetching", and this project honors
robots.txt by default. So the `youtube` adapter **refuses to poll** and raises
`RobotsDisallowed` unless you set `robots_override = 1` on that source.

Your call, not the software's. Ways to go:

| Option | Cost | Notes |
|---|---|---|
| **Leave it off** | — | Default. YouTube video simply isn't an ingest source. |
| **Set `robots_override` per channel** | free | Only for official channels (CAL FIRE units, county OES) at a slow schedule. Public-safety-information use, one request per poll — but it is still against a stated directive, and it is you making that choice. |
| **YouTube Data API v3** | free, quota'd | 10,000 units/day, keyed, no robots question: the API is the sanctioned path. Costs a Google Cloud project + key; `search.list` is 100 units a call, so budget it. |

Finding a channel id: open the channel, View Source, search `channelId`. Then
add the source with adapter `youtube`, config `{"channel_id": "UC…"}` (add
`"keywords": []` for a channel that is nothing but fire).

Whatever you choose, we only ever read the uploader's own title and description
— the video page is never fetched and nothing is transcribed.

## Already handled / no key needed

- CAL FIRE incidents, NWS CAP alerts, InciWeb, RSS/news/social feeds, SearXNG,
  Ollama, Local Deep Research (local service account in `.env`).
- **Backblaze B2** (backups — from the audit, highest priority of all):
  backblaze.com → B2 → create bucket + app key → `B2_KEY_ID`,
  `B2_APPLICATION_KEY`, `B2_BUCKET`, then uncomment the s3 replica in
  `infra/litestream.yml`. 10 GB free — the offsite copy of everything.
