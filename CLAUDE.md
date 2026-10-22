# CLAUDE.md: Greystar Telemetry

You are working on Glenn's Plex fleet monitoring dashboard on the Linux server
that hosts it. Read this whole file before changing anything.

## What this is

A single Docker service (FastAPI + static page) that:

- polls every Plex Media Server **directly** (no Tautulli) for live sessions,
- reads CPU and network per server from **Netdata Cloud** (v3 data API) or from
  each Netdata agent,
- records one history row per server per minute in SQLite,
- records every viewer's playback history (`plays` table) from consecutive
  polls, and can import each server's own watch history,
- signs people in with Plex (viewers, plus one Plex admin) or with local
  admin logins, and limits what viewers see,
- shows each person their Stripe subscription (`app/billing.py`): a read-only
  restricted key saved in Settings, Billing; customers, subscriptions,
  products and the last 400 days of charges are copied every 15 minutes,
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
- Stripe: only ever use a **restricted, read-only** key (Read on Customers,
  Subscriptions, Products, Charges). Never add code that writes to Stripe or
  shows one person's billing to another viewer. Matching: a manual link in
  `billing_links` (Stripe email -> Plex id, permanent, set in Settings,
  Billing) wins; otherwise the Stripe email equals the Plex email
  (case-insensitive). PayPal is not built yet (Glenn will configure it later).
- Only the Plex account in `ADMIN_PLEX_EMAIL` (cluster@mail.com) is a Plex
  administrator. Never add another way for a Plex account to become admin.
  Extra admins are local logins (Settings, Access, or `app.tools.admin`).
- Never log or print Plex account tokens of viewers. They are not stored; only
  the linked server-owner accounts' tokens are saved (encrypted) in
  `plex_accounts`.
- Glenn's servers each belong to a **different Plex account**. Do not assume
  one account owns everything: link one account per server (Settings, Plex
  servers), and map Plex's local account `1` to the owner of *that* server
  (`servers.owner_plex_id`, `Poller.owner_for`).

## First-time setup checklist

1. `cp .env.example .env`, generate `SECRET_KEY` (see README), keep
   `ADMIN_PLEX_EMAIL=cluster@mail.com` and `COOKIE_SECURE=true`.
   `chmod 600 .env`. If upgrading from the Basic auth version, remove
   `BASIC_AUTH_USER` and `BASIC_AUTH_PASSWORD` from `.env` (no longer used).
2. `mkdir -p data && sudo chown 10001 data`, then `docker compose up -d --build`.
3. Check `curl -s http://127.0.0.1:8791/healthz`.
4. Add the reverse proxy for **streams.greystarnetworks.com** (DNS already
   points at this server, 45.80.152.150) from `deploy/`: check whether nginx
   or Caddy serves Glenn's other sites, add only this site's file, and with
   nginx get the certificate with `certbot --nginx -d streams.greystarnetworks.com --redirect`.
5. Glenn signs in with Plex (cluster@mail.com), then in Settings, Plex servers
   links the Plex account of each server ("Link another Plex account"; sign out
   at app.plex.tv between accounts or use a private window), uses "Find
   servers" and "Add server" for each, sets up Netdata, then in Settings, Access
   runs Sync Plex users and Import Plex watch history.
   Then verify the mappings (next section).

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
    `parse_json2_by_node` matches either (via `summary.nodes` and
    `view.dimensions.ids`) against each server's Netdata node.
  - `scope.nodes` in a v3 data query accepts **node UUIDs only**; a `*` or a
    hostname there makes Netdata Cloud answer HTTP 400 (Glenn hit this).
    `NetdataClient.build_body` only sets it when every linked node is a UUID.
    Settings, Netdata, Test connection lists the CPU and network metric names
    the room really has (`/contexts`), to pick the Windows ones.
  - The Plex servers are **Windows Server**. Netdata's context names for CPU
    and network on Windows may differ from `system.cpu` / `system.net` and the
    `sent` dimension. If so, set the right ones in Settings, Netdata,
    Advanced, rather than hard-coding them.
  - CPU should be a 0 to 100 percentage of the whole machine (sum of non-idle
    dimensions). Network out should be Mbps.
- Glenn uses Netdata Cloud (agents stream to Cloud). No servers transcode on a
  GPU. All servers have Plex Pass.
- Plex bug Glenn has seen: a session (often a throttled transcode) reports
  10,000 Mbps. `normalize_session` counts anything above `BOGUS_MBPS` (1000)
  as `ESTIMATED_MBPS` (15) and flags `bitrate_estimated`; the card shows
  "estimated". Totals, server load and bandwidth history use the 15.
- Users and history (untested against real plex.tv):
  - `User.id` in `/status/sessions` should be the viewer's plex.tv account id
    (each server's owner shows as `1`, mapped to that server's owner account
    in `track_plays`). Check that
    `/api/users` shows one person per viewer, not duplicates.
  - `PlexAccount.shared_users` parses `plex.tv/api/users` XML. Confirm emails
    come through so search by email works.
  - `import_history` maps `accountID` from `/status/sessions/history/all`
    through `/accounts`. Spot-check one person's imported plays.

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

Before loading datasets the shim calls `GET /api/me` (401 sends the browser to
`/login.html`) and skips datasets the person may not see, returning empty rows.

| Area | Endpoints | Who |
|---|---|---|
| Sign in | `POST /api/auth/plex/start`, `GET /api/auth/plex/check/{pin}`, `POST /api/auth/local`, `POST /api/auth/logout` | anyone |
| Me | `GET /api/me`, `GET /api/me/history?before=` (`history` section), `GET /api/me/subscription` (always, own only) | signed in |
| Users | `GET /api/users?q=&server=` (username, name or email; returns each person's `servers` and the monitored server names), `GET /api/users/{id}`, `GET /api/users/{id}/history` | admin |
| Settings | everything under `/api/settings`, including `billing` (key, `sync`, `unmatched`, `links`), `plex/link`, `plex/accounts/{id}` (unlink), `plex/discover?account=`, `access`, `users/{id}`, `admins`, `users/sync`, `history/import` | admin |

Sessions are random tokens in an `HttpOnly`, `SameSite=Lax` cookie
(`gst_session`, 30 days); only their SHA-256 is stored. A Plex sign-in PIN can
only be finished by the browser that started it. All non-GET API calls need the
header `X-Requested-With: greystar` (CSRF guard). Tokens are write-only: the API
reports `has_token`, never the value.

Sections a viewer can be given: `streams`, `servers`, `bandwidth`, `viewing`,
`history` (`app/auth.py`, `SECTIONS`). The page adds `gs-no-<section>` classes
to hide what a viewer cannot see; the API enforces it independently.

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
- Settings screen: Display (stream card size slider), Plex servers, Netdata,
  Alerts, Access. Viewers only get Display, and only if they can see streams.
- Logo (from Glenn's logo pack, in `web/assets/`): glow mark on a UI Navy
  circle at the top of the nav (and the phone top bar); a small "Telemetry"
  label above the title (Glenn removed the logo there); loading splash on UI
  Navy; "Nothing playing right now" with a faint mark watermark; favicon and
  home-screen icons via `manifest.webmanifest`.
- Users: admin-only Users card with a find-a-person dropdown (`user-picker.js`):
  search by username, name or email, grouped by the Plex server each person is
  on (`users.servers`, from plex.tv shares by machine id plus servers they
  stream from), with a server filter. Built for hundreds of people: nothing is
  listed until it opens. Picking someone opens the history drawer
  (`people.js`); viewer names on stream cards open it too. Viewers get a My
  history section. Settings, Access uses the same picker (inline) to edit one
  person at a time, plus a short "Custom access or turned off" list.
- Subscription card ("Your subscription", `#sub-card`, `people.js`): plan,
  price, status badge, renewal or end date, last payment. Shown to every
  signed-in Plex user (hidden for an admin with no subscription, and when
  Stripe is not connected). Admin sees it in the history drawer; the people
  picker flags Past due, Unpaid, Canceled.
- Streams starting and stopping use "Lime pulse" (Glenn's pick from six options;
  `trackStreams`, `withEnded`, `pulseMarks` in `index.html`, styles in
  `people.css`): a new stream fades in with a lime ring that pulses twice and a
  "New" tag for 6 s; an ended one stays 1.7 s dimmed with an "Ended" stamp in its
  old place, then fades out. Cards, carousel and phone rows (not the table).
  Changes are announced in a polite live region. Reduced motion: no movement.
  It only plays for streams that start or stop while the page is open (nothing on
  first load). The ring and tag are timed from when the stream appeared, so page
  redraws do not cut them short. Preview: open the page with `?demo=pulse` to
  treat every current stream as new.
- The Servers online summary card was removed at Glenn's request. Transcoding,
  Buffering and Paused are a row of pills (`.sd-pills`, Glenn's pick "C. Pill
  row"): count, label, detail tag and a black arrow. A pill filters the stream
  list and shows pressed while its filter is on; pressing it again clears the
  filter. On phones the pills are three equal tiles (count over label);
  Buffering turns amber with a warning shape when anything is buffering.
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
- Viewer avatars from Plex user thumbnails (currently initials; the thumb URL
  is stored as `users.thumb`).
