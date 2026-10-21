# CLAUDE.md: Greystar Telemetry

You are working on Glenn's Plex fleet monitoring dashboard on the Linux server
that hosts it. Read this whole file before changing anything.

## What this is

A single Docker service (FastAPI + static page) that:

- polls every Plex Media Server **directly** (no Tautulli) for live sessions,
- reads CPU and network per server from **Netdata Cloud** (v3 data API) or from
  each Netdata agent,
- records one history row per server per minute in SQLite,
- serves the dashboard page and its API on `127.0.0.1:8791`.

Plex servers and the Netdata connection are managed from the dashboard's
Settings screen and stored in SQLite. Glenn does **not** want to edit config
files to add servers. Keep it that way.

## Safety rules for this server

- This server also hosts Glenn's other websites. Work only inside this
  repository's directory and the web server config file for this site. Ask
  before touching anything else (other vhosts, system packages, firewall,
  Docker networks of other apps).
- Never print, cat, echo or log the contents of `.env`, tokens or the
  `SECRET_KEY`. Refer to variables by name. Never commit `.env` or `data/`.
- The container must stay bound to `127.0.0.1:8791`. HTTPS comes from the
  existing web server (nginx or Caddy; check which is running before editing).
- Run commands that need root by asking Glenn, or with sudo only after he
  approves.
- Do not change `SECRET_KEY` once set: it decrypts the saved tokens.

## First-time setup checklist

1. `cp .env.example .env`, generate `SECRET_KEY` (see README), set
   `BASIC_AUTH_USER` and ask Glenn to type `BASIC_AUTH_PASSWORD` himself.
   `chmod 600 .env`.
2. `mkdir -p data && sudo chown 10001 data`, then `docker compose up -d --build`.
3. Check `curl -s http://127.0.0.1:8791/healthz`.
4. Add the reverse proxy for the chosen subdomain from `deploy/` (ask Glenn for
   the subdomain; get a certificate with certbot if nginx).
5. Glenn adds Plex servers and Netdata in Settings. Then verify the mappings
   (next section).

## Verify against the real servers (do this, the code was written without access to them)

- `docker compose exec telemetry python -m app.tools.probe plex` prints raw
  `/status/sessions` and the normalized rows. Compare: title, episode, user,
  player, state, LAN/WAN, bandwidth, transcode decisions, source vs stream
  codecs, progress. Fix `normalize_session` in `backend/app/plex.py` if a
  field is wrong, and add a test case in `backend/tests/` using the real (but
  anonymized) JSON shape.
- `docker compose exec telemetry python -m app.tools.probe netdata` prints the
  raw Netdata Cloud response. Things to confirm:
  - The `labels` in a `group_by: node` response are node names or node IDs;
    `parse_json2_by_node` matches either against each server's Netdata node.
  - The Plex servers are **Windows Server**. Netdata's context names for CPU
    and network on Windows may differ from `system.cpu` / `system.net` and the
    `sent` dimension. If so, set the right ones in Settings, Netdata,
    Advanced, rather than hard-coding them.
  - CPU should be a 0 to 100 percentage of the whole machine (sum of non-idle
    dimensions). Network out should be Mbps.
- Glenn uses Netdata Cloud (agents stream to Cloud). No servers transcode on a
  GPU. All servers have Plex Pass.

## How the page gets data

`web/index.html` was designed inside a hosted dashboard runtime and calls a
small `dash` API. `web/dash-shim.js` implements that API against the backend:

| Page dataset | Endpoint | Rows |
|---|---|---|
| `streams` | `GET /api/streams` | one per live session (see `normalize_session`) |
| `servers` | `GET /api/servers` | one per enabled server: status, cpu_pct, cpu_known, net_out_mbps, max_streams, hot_for_min |
| `bandwidth` | `GET /api/bandwidth` | `{minute, mbps}` for the last hour |
| `viewing_hours` | `GET /api/viewing?tz=` | `{day, hour, streams}` peak concurrency, last 7 days |

Each response is `{"rows": [...], "sample": bool}`. The page derives
`kpis`, `server_load` and `viewing_summary` with `dash.calc` in the browser.
`dash.params()` holds UI state (`look`, `cardsize`, filters); `look` and
`cardsize` persist in localStorage.

Settings endpoints are under `/api/settings`. Writes require the login and the
header `X-Requested-With: greystar` (CSRF guard). Tokens are write-only: the
API reports `has_token`, never the value.

## Design

Follow `docs/design-system/README.md` and `tokens.json` (Greystar Networks
design system). The dashboard adds its own patterns:

- Light theme: page `#e4e4e4`, cards `#f1f1f1`, black pills, lime `#b8fc64` accent.
- Cards have a 45-degree flat corner cut at the top right holding a round arrow
  button (`.sd-notch`, `--notch-path`). The Servers card has no cut and a
  subtle highlight border instead.
- Stream cards show the poster (from `/api/poster/...`) dimmed under the text,
  and flip to show playback details.
- Mobile (760px and below): bottom tab bar Home / Servers / Streams / Alerts,
  compact stream rows with a bottom sheet, optional poster carousel.
- Settings screen: Display (stream card size slider), Plex servers, Netdata, Alerts.
- Status is always a word plus a shape, never color alone. No emoji.

## Running tests

```bash
cd backend && pip install -r requirements-dev.txt && python -m pytest -q
```

## Not built yet (ask Glenn before starting)

- "End stream" button: `PlexClient.terminate` exists (Plex Pass); needs a UI
  and should be gated by an `ENABLE_ACTIONS=true` env flag.
- Rebalance only suggests moves; Plex cannot move a live session between
  servers, so a real action would end the stream with a message.
- Viewer avatars from Plex user thumbnails (currently initials).
