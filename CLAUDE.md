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
- lets people request movies and series through Glenn's Seerr
  (https://perseus.greystarnetworks.com) from a Requests page (`app/seerr.py`,
  `app/requests_api.py`, `web/requests.js`), without signing in to Seerr,
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

## Requests (Seerr)

- Connected in Settings, Requests: Seerr address and API key (saved encrypted as
  `seerr_key`; write-only like every token). Calls go server to server with
  `X-Api-Key` plus `X-API-User: <their Seerr id>` (Seerr's own middleware, verified in
  its source), so Seerr applies that person's permissions, quotas, auto-approve and
  notifications. Never send `userId` or `ignoreQuota`; advanced options
  (server, profile, folder) only when Seerr gives the person REQUEST_ADVANCED.
- People are linked once (`seerr_links`): found by email or Plex username, else
  imported from Plex (`/user/import-from-plex`), else made by Plex email (`POST /user`,
  random password) because Seerr's Plex check only sees servers its own Plex account
  shares and Glenn's servers have different owners. Local admin logins act as
  Seerr user 1. Settings, Requests lists links with Relink.
- Nothing from Seerr reaches the browser raw: routes shape each reply and drop
  other people's emails and tokens (`tests/test_requests.py` checks this).
- `requests` is an access section, on for everyone by default (a one-time
  migration added it to saved defaults and custom lists, `add_requests_section`);
  turn it off per person or in the defaults like the rest.
- Page: `#/requests/...` (discover, movies, tv, search, movie/ID, series/ID,
  person/ID, collection/ID, browse/KIND/DATA, mine, watchlist, issues, manage).
  Discover follows Seerr's own slider settings. TV requests pick seasons; 4K when
  Seerr has it on and the person may; report a problem; watchlist; approve and
  decline for people Seerr lets manage requests. Posters come from TMDB
  (`SEERR_IMAGE_BASE` overrides for tests). `tests/mock_seerr.py` is a stand-in
  Seerr for tests and local browser checks.

## Polling (backend/app/poller.py)

`Poller.run` polls Plex every `poll_seconds` and nothing else, so live streams
show within seconds of a restart. Netdata runs in `_telemetry_loop` (CPU and
network queries in parallel). The slow account syncs run in `_upkeep_loop`
(`upkeep`: plex.tv users every 6 hours, Stripe every 15 minutes); their last
run times are saved in settings (`users_synced_at`, `billing_synced_at`) so a
restart does not redo them. Settings changes call `poller.kick()` to sync now.
Source metadata for transcodes is fetched in parallel and cached per title.

Moving clips (`app/clips.py`): the first time a show or movie plays, the poller
queues it; `Clips.worker` (one at a time) asks that title's Plex server for its
file (`/library/metadata/{ratingKey}`, `Part.key` under `/library/parts/`) and
runs ffmpeg in this container to cut 8 silent seconds starting 10% in (Glenn's
choice), 240p H.264, about 100 KB, saved as `data/clips/<serverId>_<ratingKey>.mp4`
and served by `GET /api/clip/{server}/{ratingKey}` (byte ranges; Safari needs
them). The Plex token goes to ffmpeg as a request header, never in a URL. ffmpeg
seeks with byte-range requests, so a clip reads about its own size from Plex, not
the whole file. Failed titles wait 6 hours; clips unplayed for 14 days are
deleted and the folder stays under 500 MB. Stream rows carry `clip` (URL or
null). `/api/status` reports `clips` (available, count, queued, last error).

## How the page gets data

`web/index.html` was designed inside a hosted dashboard runtime and calls a
small `dash` API. `web/dash-shim.js` implements that API against the backend:

| Page dataset | Endpoint | Rows |
|---|---|---|
| `streams` | `GET /api/streams` | one per live session (see `normalize_session`) |
| `servers` | `GET /api/servers` | one per enabled server: status, cpu_pct, cpu_known, net_out_mbps, max_streams, hot_for_min |
| `bandwidth` | `GET /api/bandwidth` | `{minute, mbps}` for the last hour |
| `viewing_hours` | `GET /api/viewing?tz=` | `{day, hour, streams}` peak concurrency, last 7 days |
| `bandwidth_daily` | `GET /api/bandwidth/daily?tz=` | `{day, today, peak_mbps, peak_at}` peak total bandwidth per local day, last 7 days (cached 60 s) |

Each response is `{"rows": [...], "sample": bool}`. The page derives
`kpis`, `server_load` and `viewing_summary` with `dash.calc` in the browser.
`dash.params()` holds UI state (`look`, `cardsize`, `artlight`, `cardstyle`,
filters); all but the filters persist in localStorage.

Before loading datasets the shim calls `GET /api/me` (401 sends the browser to
`/login.html`) and skips datasets the person may not see, returning empty rows.

| Area | Endpoints | Who |
|---|---|---|
| Sign in | `POST /api/auth/plex/start`, `GET /api/auth/plex/check/{pin}`, `POST /api/auth/local`, `POST /api/auth/logout` | anyone |
| Me | `GET /api/me`, `GET /api/me/history?before=` (`history` section), `GET /api/me/subscription` (always, own only) | signed in |
| Users | `GET /api/users?q=&server=` (username, name or email; returns each person's `servers` and the monitored server names), `GET /api/users/{id}`, `GET /api/users/{id}/history` | admin |
| Settings | everything under `/api/settings`, including `summary` (the Settings home), `billing` (key, `sync`, `unmatched`, `links`), `plex/link`, `plex/accounts/{id}` (unlink), `plex/discover?account=`, `access`, `users/{id}`, `admins`, `users/sync`, `history/import` | admin |

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
- Settings screen (Glenn's pick "Mix": a home of cards plus Display with a live
  preview). Admins open the gear onto a home (`#st-home`, `renderHome` in
  `settings.js`, data from `GET /api/settings/summary`): a tall Display card with a
  small live preview of a stream card in the current look and chips for the
  choices, then Plex servers, Netdata, a black "needs a look" card (`problems`:
  servers not answering, missing Netdata nodes, failed syncs, unmatched Stripe
  customers; each line opens the section that fixes it), Alerts, Access, Billing.
  Every card says what it controls, shows a status (word plus shape) and a few
  numbers, and has a scope tag: "This browser only" (Display) or "Everyone".
  Opening a card shows that section alone (`setTab`, `data-view` on `#settings`)
  with a back arrow, a "Settings" crumb, the title, the scope tag and a one-line
  lead (`VIEWS`). Home and Display are 1120px wide, the other sections 760px,
  over a dimmed page (`#set-scrim`; click or Escape closes). Viewers go straight
  to Display with no home. Display: style picked by picture (`.st-thumb`), card
  size, cover art brightness (`artlight` 0 to 100, default 30, drives
  `--art-shade` on the Classic overlay; only for Classic), Moving clips switch,
  Reset to default in the header (resets all four), and the live preview
  (`renderPreviews` in `index.html`): a real card drawn with `streamFront` /
  `streamBack` from a stream playing now (or a sample), Card / Back switch,
  follows the size slider. The preview has its own `<video>` (`pvMode`,
  `pvVideos`) so it never takes a clip from a real card. Simple controls save as
  you change them (Display, Alerts threshold on release, default access as you
  tick); forms with addresses, tokens or passwords keep their button.
- Stream card style (`cardstyle`, saved per browser): `classic` (default, the
  original dimmed-poster card), `side` (light card, poster beside the details),
  `top` (full poster on top, details below), `banner` (wide, poster bright at
  the right), `tiles` (compact 2:3 poster tiles). `streamFront` builds Classic,
  `styledFront` the others; `.gs-app[data-cstyle]` sets the grid. In the new
  styles the flip side (`.sd-back--fit`) keeps the front's size and scrolls.
  Rows and the table are the same in every style. Viewers only get Display, and only if they can see streams.
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
- Streams starting and stopping use "Clock sweep" with the Live now bar
  (Glenn's pick; it replaced "Drop in, fall away", which had replaced Lime pulse).
  `trackStreams`, `withEnded`, `pulseMarks`, `glideFrom` in `index.html`, styles
  in `people.css` (`.sd-sweep`, a conic-gradient ring animated through the
  registered `--sw-a` angle): a new stream's card fades in while a lime border
  sweeps once around it like a clock hand (760 ms), holds, then fades (`.is-new`);
  an ended card stays in its old place while a grey border unwinds the other way
  and the poster goes grey (640 ms), then it fades out (`.is-ending`) and the cards
  around it glide to their new places (FLIP, 420 ms, only right after a change).
  The lime Live now bar shows "<user> started <title> on <server>" or "<user>
  stopped <title> after N min" (several at once: "2 streams started, 1 stream
  stopped") for 6 s, with a circle for started and a square for stopped, then
  goes back to the newest streams. Cards, carousel and phone rows (not the
  table). Changes are also announced in a polite live region. Reduced motion: no
  sweep, ended cards just dim. It only plays for streams that start or stop while
  the page is open (nothing on first load). A sweep in progress continues by time
  across redraws (negative `--sw-d`). Preview: open the page with `?demo=pulse` to
  treat every current stream as new.
- The Servers online summary card was removed at Glenn's request. Transcoding,
  Buffering and Paused are a row of pills (`.sd-pills`, Glenn's pick "C. Pill
  row"): count, label, detail tag and a black arrow. A pill filters the stream
  list and shows pressed while its filter is on; pressing it again clears the
  filter. On phones the pills are three equal tiles (count over label);
  Buffering turns amber with a warning shape when anything is buffering.
- Bandwidth card: the last-hour chart, then "Peak by day, last 7 days"
  (`renderDaily`, `.sd-bwdays`; Glenn's pick "A" of four): one bar per day with
  its peak Mbps and the time it happened, the busiest day in solid ink and named
  in "Highest: ...", today in lime with a dashed outline marked "so far". On
  phones the times are hidden.
- Page-load build, "Build and count" (Glenn's pick of five; `playIntro` in
  `index.html`, started by the `gs:ready` event `people.js` sends as the splash
  fades): navigation and header slide in, the pills drop one by one, the cards
  rise in order, the big numbers and pill counts count up from zero, and the
  bandwidth line draws (`.sd-bwline`, `.gs-intro-draw`). Plays once per page
  load, never on the 10-second refreshes; skipped for reduced motion. The
  classes are removed when each animation ends.
- Moving clips in stream cards (Settings, Display, `clips` on/off, default on,
  saved per browser): up to 6 cards with a ready clip, picked at random and kept
  while they last, play it muted on a loop under the card's shade and text
  (`pickClips`, `attachClip`, `.sd-clip`, `.has-clip`). Each card keeps its
  `<video>` across redraws, so the loop never restarts on the 10-second refresh.
  A clip plays only while that stream is Playing, on screen and in a visible tab.
  Cards and carousel only (not rows or the table). Off for reduced motion and for
  browsers asking to save data. Placement per style: Classic over the poster with
  the same shade, Poster beside and Poster on top in the poster area, Banner on
  the right with a fade, Tiles full tile with the bottom shade.
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
