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
  Settings, Billing, "Not matched" leaves out customers whose every subscription is canceled or expired
  (`ended_customers`; Glenn does not need them); customers with no subscription yet still show. The count
  (`counts()["unmatched"]`, also on the home card and "needs a look") matches, and a note says how many were left out.
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

- Security headers (`main.py`, `CSP`): scripts only from this site, so no inline
  `<script>` and no `eval` (the page's code lives in `web/app.js`); inline styles
  are allowed; images from this site, `data:`/`blob:` and image.tmdb.org. Add any
  new outside host to `CSP` deliberately. No CORS headers are sent, on purpose.
- Sign-in limits use the LAST `X-Forwarded-For` entry (`_client_ip`): that is the
  one the site's proxy adds. Never use the first entry; the client writes it.
  Local admin sign-in is also limited per username, and password changes per login.
- Every `/api/settings/*` route is admin-only in the middleware; `/api/users*` and
  `/api/status` call `admin()`. `tests/test_security.py` checks viewers get 403.

## First-time setup checklist

1. `cp .env.example .env`, generate `SECRET_KEY` (see README), keep
   `ADMIN_PLEX_EMAIL=cluster@mail.com` and `COOKIE_SECURE=true`.
   `chmod 600 .env`. If upgrading from the Basic auth version, remove
   `BASIC_AUTH_USER` and `BASIC_AUTH_PASSWORD` from `.env` (no longer used).
2. `mkdir -p data && sudo chown 10001 data`, then `docker compose up -d --build`.
3. Check `curl -s http://127.0.0.1:8791/healthz`.
4. Add the reverse proxy for **streaming.greystarnetworks.com** (DNS already
   points at this server, 45.80.152.150) from `deploy/`: check whether nginx
   or Caddy serves Glenn's other sites, add only this site's file, and with
   nginx get the certificate with `certbot --nginx -d streaming.greystarnetworks.com --redirect`.
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
- My requests (own, All) shows failed requests in a red box (Try again, Remove, "Try both again") and
  approved ones not downloading yet in an amber "waiting for a download" box with the reason
  (`GET /api/requests/attention`, `waiting_reason`): not out yet (digital or physical release date, or
  season air date, from TMDB through Seerr), next episode airs on a date, or out but no copy found
  ("Searching"). Seerr only lets owners delete pending requests and only managers retry, so for a
  person's OWN FAILED request the dashboard retries (`POST /request/{id}/retry`) or removes
  (`DELETE /request/{id}?failed=true`) with its own key after `own_failed` checks owner and status.
  Radarr and Sonarr are now connected for download progress (see Unwatched cleanup and download progress below).
- Request log (Settings, Request log, admins only; `app/request_log.py`, `openLog`, `loadLog`, `paintLog` in `settings.js`): every
  request made through the site is saved in `request_log` when it is made (`make_request` calls `log_request`: who
  (Plex id, name, email, or an admin login), title, year, poster, seasons, 4K, time); canceling or removing a failed one
  marks it `canceled` / `removed`. The state now comes from Seerr's full request list (`all_requests`, as Seerr's
  admin, cached 60 s): Waiting for approval, Approved (waiting for a download), Downloading, Partly available,
  Available, Declined, Failed, No longer in Seerr, or Unknown when Seerr is not answering (the log still shows).
  Grouped by local day, with search (person, username, email, title), Movies/Series, status chips with counts,
  Show more (50 a page) and Download CSV (`/api/settings/requests/log.csv?tz=`, in the admin's time zone).
  "Import earlier requests" (`import_earlier`) brings in requests by people linked to Seerr made since they were
  linked, marked Imported (Seerr does not record where a request came from). A Settings home card shows counts.
- Ratings and box office (Glenn's pick C, "Scores on every poster"): `GET /api/requests/scores?ids=movie:1,tv:2`
  (up to 60, `requests` section) returns per title Rotten Tomatoes critics/audience (`/movie/{id}/ratingscombined`,
  falling back to `/movie/{id}/ratings`; `/tv/{id}/ratings`), IMDb (from ratingscombined), TMDB user score and, for
  movies, budget, worldwide gross, profit (gross minus budget, before marketing), multiple and verdict (hit 1.5x and
  up, even 0.8x to 1.5x, loss below). `Requests.scores` saves them in `title_scores` (a day; 6 h when not out yet or
  Seerr failed) and fetches new ones 4 at a time as Seerr's admin. The page (`wantScores` batches cards, `paintChips`)
  puts chips at the bottom of every poster card (`.rq-card__chips`): critics % with a circle (Fresh) or square
  (Rotten), a ring with the TMDB % when there is no Rotten Tomatoes score (50+ votes), and "5.2x" (hit), a triangle
  "1.0x" (even) or a square "Loss". The kind (Movie/Series) moved to the line under the title. Grids (Movies, Series,
  browse, search) get Sort: Popular, Highest rated, Biggest hit (`sortBar`, reorders the cards on screen; titles
  without the number go last). A title's About list gets Rotten Tomatoes, IMDb, TMDB users, Budget, Worldwide gross
  and Profit rows (`facts`). Series have no money numbers.
- My requests is one list of everything a person asked for (Glenn's unified requests, Nov 2026): `GET
  /api/requests/everything?cursor=&order=` (`everything` in `requests_api.py`, `unify_seerr`) merges their Seerr
  requests with their comic, ebook and audiobook requests from the media site (`Media.member_requests`, a signed
  server-to-server call to `GET /api/telemetry/requests?sub=<plex id>`, header `X-Greystar-Telemetry`
  `v1.<ts>.<nonce>.<HMAC-SHA256(media_secret, "v1.<ts>.<nonce>.<METHOD> <path?query>")>`, cached 20 s; Plex sign-ins
  with the `books` section only). Newest activity first (`order=asc` oldest), 30 a page; `cursor` is "<Seerr rows
  used>.<book items used>" so the two sides page together without gaps. Shared words: work (In progress), release
  (Waiting for release, Airing), follow (Following: an ongoing comic series, moves up when a new issue arrives), ready,
  problem (Needs attention); declined and cancelled are left out. One side not answering: `sources` says `down` and the
  page shows the rest with a note. The page (`everything` in `requests.js`) groups by day in the browser's time zone,
  filters by kind and status (on what is loaded; it loads more on its own when a filter shows little), and keeps the
  old Seerr-only list for request viewers behind "Everyone (movies and shows)" (`mine?everyone=1`). Book covers are
  not shown (this site's CSP allows only TMDB images): a coloured letter tile stands in.
- Season requests follow Seerr's own rule (`server/entity/MediaRequest.ts`): a season can be requested only when it
  is not on Plex at all (`unknown`, or `deleted`) and no request holds it; a FAILED request still holds its seasons
  (only declined and completed ones free them). Seerr answers HTTP 202 "No seasons available to request" and makes
  nothing when none of the picked seasons qualify, so `make_request` treats any reply without a request id as a 409
  ("Seerr did not make a request ...") instead of "requested" (that showed success while nothing reached My
  requests). The picker (`seasonOpen` in `requests.js`) offers only qualifying seasons, and explains partly-on-Plex
  seasons (use Report issue) and failed ones (Try again in My requests).
- Request rules (Glenn's answers, Nov 2026; `app/request_help.py`, tables `request_flags`, `request_watch`,
  `search_misses`; the media site has the same rules for books in `server/request_help.ts`). Glenn approves everything
  (turn on Seerr's default Auto-Approve and Auto-Approve 4K permissions in Seerr itself; the dashboard does not change
  Seerr's permissions). A person's failed request gets ONE Try again (`POST /request/{id}/retry`, then 409); after that
  Notify admin (`POST /request/{id}/notify`), then "Admin notified" (`helpBtn` in `requests.js`; `with_help` adds
  `canRetry`, `canNotify`, `notified` to My requests and the red attention box). Request managers are not limited.
  Admins see them in Settings, Request log, "Needs you" (Mark handled, `/api/settings/requests/help`), a "Needs you"
  tag on the log row, and a line in the Settings home's "needs a look" (`requests.needs_you` in the summary).
- Notify me too: on a title someone else requested and that is not on Plex yet, `followBox` shows "Requested by
  another member" and Notify me too (`POST/DELETE /api/requests/watch/{request id}`). Followed requests show in My
  requests (`seerr-watch:<id>`, `watching`, "Stop notifying me"). Who made someone else's request is never sent to
  people who are not request managers (`requests_on` sends `by: null` and `mine`); `tests/test_request_help.py`
  checks this. My requests' cursor is now "<Seerr>.<books>.<followed>".
- Recently requested (Glenn's pick B, the activity feed, Nov 2026): `GET /api/requests/recent?days=7` (`recent` in
  `requests_api.py`, `requests` section, 1 to 14 days) is what EVERYONE asked for, newest first, never who asked (no
  names, ids, avatars; `tests/test_recent_requests.py` checks this). Movies and shows come from Seerr's full list
  (`all_requests`, as its admin, cached 60 s; declined left out, 200 newest), titles and posters through `title_info`;
  comics, ebooks and audiobooks from the media site's signed `GET /api/telemetry/recent?days=` (`Media.recent_requests`,
  cached 60 s, only ref, kind, title, sub, created, state, label kept; Plex sign-ins with `books` only). Book rows get
  covers through this site: `poster` is `/api/requests/recent/cover/<kind>/<id>` (from the media site's `ref`), which
  fetches the media site's signed `GET /api/telemetry/cover/<kind>/<id>` (a 184 px WebP: library cover, else the cover
  saved with the request, else its Open Library / Google Books lookup) via `Media.cover` (kept in memory: a day, an hour
  for none, 400 at most) and serves it from here, so the CSP stays images-from-this-site-and-TMDB. Tiles load the picture
  with a detached `new Image()` and swap it in on load; never `loading="lazy"` there (a lazy image not on the page never
  loads, which left every tile a letter). The same kind and title
  shows once with `times`. `public_status` turns the shared words into four plain ones, each with a shape: Available or
  Following (circle), Downloading (triangle), Not out yet (ring), Searching (square; a failed or waiting request reads
  Searching here: what went wrong is the requester's business). Reply: `{days, total, counts, status, items (80), more,
  sources}`. `web/recent.js` (`gsRecent`) and `web/recent.css` draw it everywhere: kind tiles in the pastel tints (TMDB
  poster when there is one), rows grouped by day in the browser's time zone, count chips (filters on the full page), the
  mix bar. Full list: `#/requests/recent`, the "Recently requested" tab after My requests (`recentPage` in `requests.js`).
- Book rows in My requests act through `POST /api/requests/books/{comic|ebook|audiobook}/{id}/{retry|notify|cancel|stop|watch|unwatch}`,
  sent on to the media site signed (`Media.call`), then `forget_member` so the list refreshes.
- A partly available series' search card gets "Request more seasons" (`.rq-card__more`): it opens the series with
  `?more=1`, which opens the season picker once (the button there reads "Request more seasons").
- Movie and show searches with no results are counted by words only (`svc.help.miss`), never who searched:
  Settings, Request log, "Searches that found nothing" (7 or 30 days, `/api/settings/requests/misses`).
- A red floating "Report issue" button (`.rq-report`, Glenn's pick, option 2) on every Requests page opens
  the title picker; the list tab is "Reported issues".
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

## Unwatched cleanup and download progress (Glenn's pick B, Nov 2026)

- Radarr & Sonarr (`app/arr.py`, Settings, Radarr & Sonarr, `renderArr` in `settings.js`): slots `radarr`, `sonarr`,
  `radarr4k`, `sonarr4k` (address plus API key, keys saved encrypted, write-only; 4K falls back to the standard one).
  "Copy from Seerr" reads Seerr's own `/settings/radarr|sonarr` (`Arr.from_seerr`). Routes under `/api/settings/arr`.
  v3 API with `X-Api-Key`. Members never see release names, indexers or the download client: `progress_word` turns a
  queue record into a stage (queued, downloading, paused, importing, problem, retry) and plain words (`PROBLEMS`).
- Download progress for members: `GET /api/requests/progress?ids=movie:603,tv:1396,movie:603:4k` (`requests` router).
  Movies: percent, size, speed (from earlier samples), time left, quality ("1080p · WEB"), six steps (`steps_for`:
  Requested, Approved, Copy found, Downloading, Importing, On Plex) and a problem text with Notify admin
  (`POST /api/requests/request/{id}/notify-import`, puts it on the admins' Needs you list). Shows: every episode of the
  requested seasons with counts. `/api/requests/me` says `progress` (Radarr or Sonarr connected). `web/dl.js` (`gsDl`)
  and `web/dl.css` draw it: the title hero (`heroLive` in `requests.js`, checks every 15 s while on screen; a show's
  episodes in a panel under the hero, the picked season kept) and My requests rows (`progress` in `everything`, every
  20 s; the line replaces Seerr's waiting reason, `.has-progress`).
- Play on Plex is gone from Requests (Glenn's ask): `media_state` no longer sends Plex links. Do not add Play buttons back.
- Unwatched cleanup (`app/cleanup.py`, table `cleanup_items`): only movie and show requests made with the tick box are
  tracked (`agreeRemoval` in `RequestIn`; `make_request` answers 422 without it while cleanup is on, then
  `cleanup.track`). Books, comics and audiobooks are never tracked. The clock starts when the title arrives on Plex (Seerr
  `mediaAddedAt`, or now; for shows, when the requested seasons arrive), `days` (setting `cleanup_days`, 14) plus extra
  days. One real play by anyone (120 s or more, or an imported Plex play) of the title or the requested seasons keeps it
  for good (`plays_of`: TMDB id from Plex Guids (`includeGuids=1`), rating keys, or title and year; errs toward keeping).
  `plays` gained `parent_key`, `season`, `tmdb_id`.
- Nightly run (`Cleanup.loop`, every 10 min: `refresh`, then `nightly` once a day at `cleanup_hour` (3) in `cleanup_tz`
  (America/Chicago; `tzdata` is in requirements)). Modes (`cleanup_mode`): `review` (default: nothing removed, the board
  marks "Would be removed"), `auto`, `off` (no tick box, nothing tracked). Never removes a title someone is streaming.
  Removal goes through Radarr (movie with files, no import exclusion) or Sonarr (only the requested seasons: unmonitor and
  delete their files; the whole series only when nothing else is kept), then Seerr `DELETE /media/{id}` so it can be
  requested again. A failure saves `error` on the card and keeps it.
- Admin board: `#/cleanup` (`web/cleanup.js`, `web/cleanup.css`; `PLACES` entry `cleanup`, admins only, under More on
  phones). Columns Clock running (includes not on Plex yet), Due in 3 days, Kept (watched, or kept by an admin), Deleted
  (30 days). Mode switch, days and hour, Keep (with a note), +7 days, Delete now (confirm), Track again. Routes under
  `/api/settings/cleanup` (admin-only like all settings). The Settings home's "needs a look" counts due titles.
- What members see (`clock_for`, never who asked): `clock` on My requests and Recently requested rows,
  `GET /api/requests/clocks?ids=` for title pages: a pill ("9 days left to watch", "Last day to watch", "Watched, it
  stays", "Kept", "Removed, not watched") and a countdown note with segments. The request dialog shows the tick box
  ("I understand ... will be removed if it is not watched within 14 days"); Request stays disabled until it is ticked.
- Tests: `tests/test_cleanup.py` with `tests/mock_arr.py` (a stand-in Radarr/Sonarr).

## Download progress in the green bar, and the fading tab bar (Glenn's pick A, Nov 12 2026)

- `GET /api/requests/downloads` (`downloads` in `requests_api.py`, `requests` section; `?fresh=1` drops this person's
  caches, at most every 5 s) lists what THIS person asked for that is downloading now or finished in the last 30 minutes
  (`READY_SECONDS`): their own Seerr requests (approved, media processing/partial) with Radarr/Sonarr progress
  (`movie_progress`; for shows the average of the requested episodes in the queue), and their own comic, ebook and
  audiobook requests from the media site's member feed (`work` items labelled Downloading or Adding; `ready` items with
  the feed's `doneAt`). Never someone else's request (followed ones are left out), never release names. Up to 6, still
  downloading first. Cached 15 s per person. `tests/test_downloads.py`.
- `web/downloads.js` (`gsDownloads`) and `web/downloads.css`: while there is something, the lime Live now bar (`#newest`,
  `renderNewest` in `app.js` calls `gsDownloads.paint`) fills from the left with a darker band (`--dl-pct`), says
  "2 downloading" / "Four is ready" with the titles, and a dark chip "46% · 4 min left". Stream start/stop messages still
  take it for their 6 s first. A server that is down keeps its red segment at the end; "all up" steps aside. Tapping it
  opens "Your downloads" (a dropdown under the bar on desktops, the shared bottom sheet on phones) with each download and
  Open My requests. Opening a ready one hides it from the bar for this browser tab (sessionStorage `gs-dl-seen`).
  Checked every 20 s while something downloads, every 60 s otherwise, only while the tab is visible; the Books frame's
  `requests-changed` message checks at once. On phones the "Live now" words step aside while it shows.
- Phones: the tab bar fades and slides down while the person scrolls and comes back 450 ms after they stop (`web/tabfade.js`,
  `html.gs-scrolling`). Any scroller counts (capturing listener) except sheets and dropdowns; the Books frame sends
  `scrolling` (at most every 150 ms) since its scrolling is invisible here. It stays while a sheet holds the page, while a
  tab has keyboard focus, and for 0.8 s after a page change.

## Live streams globe and Dashboard Cards (Glenn's pick 7 "Swing to the city", Nov 13 2026)

- Admin card `#globe-card` beside When people watch (same size, corner cut and header; `.sd-insights` is two equal columns
  from 1,000 px). Server capacity moved under the pair as a thin full-width block (`#capacity-card` spans the row; title,
  one line of rings, warning and Rebalance; rings wrap to a second line under 1,180 px). Phones: the globe is on the Home
  tab under When people watch; capacity stays on Servers. Styles: `web/globe.css`. Viewers never get the globe.
- Look: the dotted globe in the dashboard ink (`web/globe-data.js`, land dots from Natural Earth 110m), turning once every
  75 s. On a stream start it swings the short way round to face the city (0.35 to 1.3 s, ease in and out, then a 1.6 s
  hold), a line flies out from the Dallas servers (`SERVER` in `web/globe.js`, the square marked "Dallas DC") and a lime
  dot lands with the city's name. Glenn's worry was it getting jerky: a swing is never cut short; three or more waiting
  get one swing to the busiest area (the city with most others within 22 degrees) and all ping (far side dimmed); a city
  waiting over 12 s pings in place; pings within 3.5 degrees share one dot with "x3"; at most 8 pings a batch
  (`web/globe-card.js`); nothing pings on page load or for what started while the tab was hidden. Reduced motion: no
  turning or swings, it rests over the Americas, pings and lines still show. Labels keep off each other, the title,
  the caption and the corner cut, and are left out when there is no room; the globe shrinks to keep clear of the title.
- Data: `GET /api/globe?after=<cursor>` (admin; `app/globe.py`). The poller calls `Globe.started` only for a session it
  had not seen (not after a restart, not for the next episode in the same session). The viewer's address (`_ip` from
  `Player.remotePublicAddress` or `address`, set in `normalize_session`) is looked up and dropped: `poll_plex` pops it
  before rows are kept or sent. Events are only `{id (random), at, label ("Dallas, TX", "Toronto, CA"), lat, lon}`, lat
  and lon being the city's centre rounded to one decimal; never a name, Plex id or IP (`tests/test_globe.py`). LAN and
  unknown addresses count but do not ping. `last_hour` counts live plays started in the last hour (`starts_since`).
- Streams playing now (Glenn's round three, Nov 13 2026): every stream playing is a small dot at its city with a soft
  ring pulsing on its own beat (none for reduced motion). `Globe.playing(rows, online)` runs every poll before the IPs
  are dropped and keeps one dot per session (a server that did not answer keeps its dots); a new stream's dot reuses
  its start event's id, so the card shows it only once the lime ping lands. `/api/globe` adds `active` ({id, label,
  lat, lon, type: direct|stream|transcode, server}) and `servers` (enabled names in Settings order). Colours: the
  validated three-colour chart palette (blue Direct play, orange Direct stream, green Transcode; light and dark steps in
  `HUES`, `globe-card.js`); the card's Type / Server switch (saved per browser, `gs-globe-by`) colours by server instead,
  the three colours times circle, square and diamond in Settings order. Lime stays for new streams only. Same city and
  colour share a bigger dot; places within about 130 km are one ("Dallas, TX area") and different colours there sit side
  by side. Legend with counts under the header ("Playing now 17"); hover or tap a dot: "Toronto, CA · 2 streams ·
  Transcode". Never a name, Plex id or IP in `active` either (`tests/test_globe.py`).
- Country outline: when a ping lands, the country it is in gets a white outline with a glow (a dark edge on the light
  card), fading with the ping. Found in the browser from the city's rounded centre (`web/globe-countries.js`, Natural
  Earth 110m outer rings, delta-encoded tenths of a degree, about 19 KB gzipped; a coastal city just outside takes the
  nearest country within 1 degree). Nothing new comes from the server.
- GeoIP: DB-IP "IP to City Lite" (CC BY 4.0; the card links "IP Geolocation by DB-IP", keep it), downloaded to
  `data/geo/dbip-city-lite.mmdb` from `download.db-ip.com/free/dbip-city-lite-YYYY-MM.mmdb.gz` (this month, else last
  month), checked every 6 hours, refreshed after 32 days (`Globe.loop`, `GeoDB.refresh`). Read with `maxminddb`. If it
  cannot download, the card says city lookups are not ready and only counts. Sample mode makes up starts (`SampleGlobe`).
- Settings, Dashboard Cards (`cards` view in `settings.js`, `renderCards`; scope tag "Your account"): a switch per admin
  card (`DASH_CARDS` in `main.py`: summary pills, Bandwidth, Servers, When people watch, Live streams globe, Server
  capacity, Live streams, Users, Alerts). Saved per admin account in settings `cards_hidden:<kind>:<subject>` (a Plex
  admin and each local admin login have their own); `GET/PUT /api/settings/cards`, and `/api/me` carries
  `cards_hidden` so the page is right on first paint. Nothing saved = every card shows. `web/cards.js` (`gsCards`) sets
  `gs-hide-<key>` on `#streams-app`; a hidden card's menu place and phone tab go when nothing is left on that page
  (`off()` in `PLACES`), Users also leaves the account menu, and When people watch or the globe alone takes the whole row
  (`gs-insights-single`). Alerts hides the Alerts list and the alert strip under the title.

## Books & comics (the Greystar media site in a frame)

- Glenn's media site (https://media.greystarnetworks.com, a Node app on his Windows server, repo
  gmaster12/media) shows inside the dashboard on `#/books` (`web/books.js`, `web/books.css`,
  `app/media.py`). Glenn's picks: shown inside the page so people never seem to leave; new people are
  approved on the media site straight away; everyone can request comics, ebooks and audiobooks, but
  listening needs the media site's Audiobooks add-on (its "Add Audiobooks to my subscription" button is a
  placeholder for a Stripe sign-up, not built; this dashboard's Stripe key stays read-only).
- Sign-in: `GET /api/books/link?next=/path` (`books` section, Plex sign-ins only; local admin logins get
  409) returns the media site's `/api/sso/telemetry?token=...`. The pass is `v1.<payload>.<sig>` (base64url,
  HMAC-SHA256 with the shared secret `media_secret`, saved encrypted), payload `{v, sub: plex id, email,
  username, title, thumb, iat, exp (60 s), nonce, next}`. The media site checks it, uses each nonce once,
  finds the person by numeric Plex id then Plex email (or makes an approved account) and opens `next`.
  The secret is made on the media site (Administration, Settings, Telemetry control panel) and pasted in
  Settings, Books & comics (`/api/settings/books`, write-only). `tests/test_books.py` checks the pass.
- CSP: `frame-src` names the media site's origin only while Books & comics is on (`csp()` in `main.py`);
  otherwise `frame-src 'none'` as before. The media site lists this site in its `frame-ancestors`.
- The frame is made once and only hidden afterwards, so an audiobook keeps playing around the dashboard.
  Messages from the frame (checked against the media origin and the frame's window): `ready`, `immersive`
  (a reader or the full audiobook player opened: `data-immersive` hides the menu and the frame fills the
  screen; kept as state in `books.js`, so leaving and coming back to `#/books` puts it back), `signed-out` (get a
  fresh pass, at most every 15 s), `audiobook` (below).
- Audiobooks (Glenn's audiobook review, Nov 2026). The media site's player has one of each thing (in the
  frame and on the site itself): the mini player has the book (opens the full player), rewind, play or pause, forward and Stop
  (square icon); the full player has Minimize ("keeps playing") and an outlined Stop pill at the top, and its
  speeds and sleep times open from their buttons. On every other Telemetry page a "Now listening" bar (`.np`,
  `renderNp` in `books.js`, styles at the end of `books.css`) shows the book with Playing or Paused (word plus
  circle or square), the time left and a thin progress line, a Play/Pause button and Stop; the book opens
  `#/books` and the full player. Desktop: bottom left of the content (Report issue is bottom right); phones:
  above the tab bar. The frame sends `{type: 'audiobook', state: {title, author, chapter, playing, positionMs,
  durationMs} | null}` on changes and every 5 s of listening (`cleanNp` checks it; text only, never as HTML);
  we send `{type: 'audiobook', action: 'toggle' | 'stop' | 'open'}`. Stop from the keyboard puts focus on the
  page (`#content`).
- The media site in a frame draws its own search and tabs in this dashboard's look under the dashboard's top
  bar (which stays on `#/books`, phones and desktop). It also sends `scrolling` (the phone tab bar fades) and
  `requests-changed` (the green bar's download progress checks now).
- `books` is an access section, on for everyone by default (`add_books_section`, once); the nav button and
  phone tab show only when it is on in Settings and the person has the section.
- Changing the frame's page without reloading it (so an audiobook keeps playing): opening `#/books/<path>` while
  the frame exists sends `{source: 'greystar-telemetry', type: 'navigate', path}` to the media origin once the
  frame said `ready` (before that the path waits); the media site replaces its current entry, so Back needs no
  extra press. A plain `#/books` keeps the frame where it is. `gsBooks.usable()` = on, and a Plex sign-in.
- The media site now talks to exactly this origin: its sign-in hand-off adds `parent=<telemetryOrigin>` (the
  media site's own setting), and it posts only to that origin and reads only messages from the parent window at
  that origin. The frame's search box can send `open-requests` (`q`): we open `#/requests/search?q=`.

## Shared search box (Glenn's unified search, Nov 2026)

One box with a selector everywhere (`web/usearch.js`, `web/usearch.css`; the media site's twin is
`src/components/UnifiedSearch.tsx`, same kinds, words and routes; keep them in step). Kinds: Movies & shows
(`requests` section) goes to `#/requests/search?q=`; Comics, Ebooks, Audiobooks and All books need `books`, Books &
comics on and a Plex sign-in, and open the frame at the media site's results page `/find?type=<kind>&q=` (no words:
its `/library|/ebooks|/audiobooks|/search` page). Glenn's pick C (Nov 12 2026): there is no "In our library / Request
new" switch any more. The results page lists what the library has first (Play or Open), then, on the same screen,
the request page's own results for those words with their real buttons (MAM versions and Download, Shelfmark Choose a
release, comic requests); Glenn did not want a second page to reach them. The media site's own box, inside the frame, also drops
suggestions as you type (library, then catalog, then "See all results"); this box cannot (the media site's data is
only reachable inside the frame), so Enter goes to the results page. One kind left: the selector is a plain label. The last kind is remembered per
browser (`gs-usearch-kind`); the Requests page always starts on Movies & shows and searches as you type for it
(book kinds wait for Search/Enter, so ComicVine and the book catalog are not hit per keystroke; picking a kind
cancels a pending movie search). Used on the Requests page header (`searchBox` in `requests.js`, its input keeps
id `rq-q`) and the viewer home's Request something door and Requests hub (`searchForm` in `home.js`). The lime
pulse moved to `.us--page` / `.us--hero`.

No visible scrollbars anywhere (Glenn's ask): `system.css` ends with a rule that stops painting them on every
element; scrolling still works. Do not add visible scrollbars back.

## Servers by id (history and covers)

Plays belong to a server by `plays.server_id`, not its name (Glenn's servers changed IP and were re-added
under new names and ids in Nov 2026, which broke covers). `plays.server` keeps the name it had when watched;
history shows the server's current name (`_history_rows`, plus `watched_on`). `server_aliases (old_name,
server_id)` maps old names (seeded once: Orion 14, Beta 13, Atlas 9, Red 12, Galaxy 11, only for ids that
exist) and `machine:<id>` keys to server ids; `Store.link_plays` fills a missing `server_id` at start and
after adding a server. `art_for(server_id, rating_key)` finds covers by id; `api_poster` asks Plex for the
item's current cover when the saved path fails, and the page keeps the play icon until a cover loads.
Removing a server asks where its history goes (`DELETE /api/settings/servers/{id}?move_to=`); without a
target, re-adding the same Plex machine relinks it. For an IP change, edit the address in place.

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
choice), 360p H.264 (`CLIP_HEIGHT`, was 240p; about 250 KB), saved as `data/clips/<serverId>_<ratingKey>_360p.mp4`
(the height is in the name, so a new height makes new clips and `prune` removes the old ones)
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
| Settings | everything under `/api/settings`, including `summary` (the Settings home), `arr` (Radarr & Sonarr), `cleanup` (the board), `requests/log` (+ `.csv`, `/import`), `billing` (key, `sync`, `unmatched`, `links`), `plex/link`, `plex/accounts/{id}` (unlink), `plex/discover?account=`, `access`, `users/{id}`, `admins`, `users/sync`, `history/import` | admin |

Sessions are random tokens in an `HttpOnly`, `SameSite=Lax` cookie
(`gst_session`, 30 days); only their SHA-256 is stored. A Plex sign-in PIN can
only be finished by the browser that started it. All non-GET API calls need the
header `X-Requested-With: greystar` (CSRF guard). Tokens are write-only: the API
reports `has_token`, never the value.

Sections a viewer can be given: `streams`, `mystreams`, `servers`, `bandwidth`, `viewing`,
`history`, `requests` (`app/auth.py`, `SECTIONS`). Defaults: `mystreams`, `history`, `requests`
(each added once to saved defaults and custom lists by `add_section_once`; turning it off sticks).
`mystreams` ("My streams") shows the streams card with only that person's own streams: `/api/streams`
filters by `user_id`, and a server's local account `1` counts when that person owns the server
(`own_streams` in `main.py`). `streams` (everyone's) wins when both are on. The page treats `mystreams`
as `streams` (`dash.can`, `dash.ownStreams()`), titles it "Your streams" and hides the server count line. The page adds `gs-no-<section>` classes
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
- Phones (see "Phone layout and addresses" below): a fixed bottom tab bar, compact stream rows
  with a bottom sheet, optional poster carousel.
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
  the right), `tiles` (compact 2:3 poster tiles), and Glenn's picks 3, 4 and 9 of the Nov 13 2026
  card redesigns: `sheet` "Info sheet" (raised card, poster set in with the avatar and state, server
  eyebrow, title, name and progress, a row of device/quality/method facts between thin rules, bitrate
  and a Details pill), `tray` "Full bleed" (poster fills the card under a shade, title, name, time and a
  big bitrate on it, a raised tray at the bottom with quality/method/LAN-WAN, progress and a Playback
  details pill), `glance` "Night glance" (always dark: lifted poster with the title, a black panel with
  "Watching," and the name (state word plus shape), minutes, an orange progress line, "On server, device,
  quality" and method/Mbps/location facts). The Details pills are look-only (`ctaEl`, `aria-hidden`): the
  card flips on click and keyboards use `.sd-flipbtn`. `sheetFront`, `trayFront`, `glanceFront` in
  `app.js`, styles after Tiles in `index.html` (`.sd-is__*`, `.sd-tr__*`, `.sd-gl__*`, shared `.sd-facts`,
  `.sd-rate`, `.sd-cta`). `streamFront` builds Classic,
  `styledFront` the others; `.gs-app[data-cstyle]` sets the grid. In the new
  styles the flip side (`.sd-back--fit`) keeps the front's size and scrolls.
  Rows and the table are the same in every style. Viewers only get Display (everyone gets the gear: Appearance and Home
  layout; the stream card choices only show for people who can see streams, `gs-no-streams` in `ui.css`).
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
  saved per browser): up to 6 cards with a ready clip (6 to 12, Settings, Display, "Cards with moving clips",
  `clipcount`, saved per browser), picked at random and kept
  while they last, play it muted on a loop under the card's shade and text
  (`pickClips`, `attachClip`, `.sd-clip`, `.has-clip`). Each card keeps its
  `<video>` across redraws, so the loop never restarts on the 10-second refresh.
  A clip plays only while that stream is Playing, on screen and in a visible tab.
  Cards and carousel only (not rows or the table). Off for reduced motion and for
  browsers asking to save data. Placement per style: Classic over the poster with
  the same shade, Poster beside and Poster on top in the poster area, Banner on
  the right with a fade, Tiles full tile with the bottom shade, Info sheet and Night glance in the poster, Full bleed
  over the whole card under the same shade.
- The Requests search pill pulses a slow lime ring (`rq-pulse-pill`, 2.8 s, end of `requests.css`; Glenn's ask):
  not while typing, not for reduced motion. The Requests nav button and phone tab show a small lime dot instead
  (`ui.css`; the ring read as a second selected item).
- Your servers (viewers, Plex sign-ins): `GET /api/me/servers` lists only the servers shared with them
  (`users.servers`, old names through `server_aliases`), each Online or Offline and since when, nothing
  else. `Poller._mark` saves the time each server went up or down in setting `server_since` (a server
  counts as down after 3 failed polls in a row, so a blip keeps its up time). Shown at the END OF THE LIME
  Live now bar (Glenn's pick B, `.sd-srvseg`, `paintSeg` in `people.js`, placed by `renderNewest` via
  `gsServerSeg`): all up is a quiet "Your servers ● ATLAS ● ORION-3 All up"; any down turns it red with
  "ORION-3 is down for 2 h 14 min, since 8:45 AM". On phones the bar takes its own full-width row and
  says "ATLAS up" / "ORION-3 down 2 h 14 min". The old Your servers card is gone (`gsServersCard` returns null).
- The account menu opens from the circle at the top right (`renderUser`): My playback history, My subscription
  (viewers), Sign out.
- Viewer home (`web/home.js`, `web/home.css`; Glenn's pick): Plex sign-ins that are not the admin land on a
  home in the layout chosen in Settings, Display, "Home layout" (`vhome`, saved per browser, kept in `KEEP`):
  `doors` Four big doors (DEFAULT: Watching now, Request something with a search box, My history, My
  subscription), `hub` Requests hub (big search, your requests as Ready to watch / On its way / Waiting for
  approval / Failed, trending row), `month` My month (`GET /api/me/recap?period=week|month|year&tz=`,
  `recap_for` in `main.py`: hours vs the same stretch before, plays, longest streak, prime time, most watched,
  hours of the day). Every layout shows Recently requested when the person has `requests` (see Requests; `recentDoor`,
  `recentSection`, `recentPanel` in `home.js`): Doors gets a "Recently requested" door after Request something (full
  width when there are more than three doors, the last two days, days side by side on desktop), the hub a section
  between Your requests and Trending this week (type chips that filter, three days), My month a "Requested on Greystar"
  panel at the end (total, mix bar, two days; always the last 7 days, it does not follow the period switch). Each links
  to `#/requests/recent`. A layout needing a section the person lacks falls back to doors. Doors and cards open
  the page's own sections via `gsHome.show('streams'|'history'|'subscription')` with a "Home" button
  (`#vhome-back`); `.gs-vh[data-vview]` in `home.css` decides what each view shows. The Home nav button and
  phone tab go back to it. Admins never get it. Cards "Sweep in" (Glenn's pick C, `sweepIn` in `home.js`, end of
  `home.css`): when the home opens the cards (doors, hub stages, month tiles and panels) fade in one at a time, 170 ms
  apart, waiting for the splash (`gs:ready`); cards that load later keep their turn (a MutationObserver for 4 s).
  Hover (pointer devices): the card grows a little (`--vh-grow`) and brightens while a lime border sweeps once around
  it (`::before`, `vh-sweep`) and stays; leaving only fades it (Glenn: no animation on the cards you leave, no line on
  the entrance). A tap presses it in. Reduced motion: none of it. Changing the My month period does not replay it (`render(true)`).
- Page changes "Cascade in" (Glenn's pick 6 of 8, Nov 12 2026; `web/cascade.js`, `gsCascade.run`): when the page on
  screen changes (`key()`: `data-page`, the phone tab `data-mtab`, the viewer home's `data-vview`, a Requests section and
  address), the title and then each visible card float up from 26 px lower at 96% size, 520 ms each, 60 ms apart
  (`cubic-bezier(.16,1,.3,1)`). It picks blocks on its own (visible children of `#content`, stepping into a block taller
  than 60% of the screen that holds several parts), skips the top bar, tab bar, sheets and anything already animating
  (the viewer home's Sweep in, the page-load build), and keeps going for 700 ms for cards that arrive after a fetch. Web
  Animations on transform and opacity only, `fill: backwards`; nothing for Reduce Motion. Desktop dashboard jumps to a
  card on the same page do not count. The media site does the same inside its own pages (`src/lib/cascade.ts`).
- Status is always a word plus a shape, never color alone. No emoji.
- Light theme (`web/light.css`, Glenn's reference picture): deeper shadows on
  cards, pills, the top bar and stream cards, and light Servers and Server
  capacity cards (the dark rack photo is cut to its shape). Notched cards get
  their shadow from `filter: drop-shadow` on a background that leaves the cut
  corner see-through, so the shadow follows the cut; stream card faces use a
  mask (cut corner plus the round button) and `.sd-flip` casts the shadow.
  Applies for Light, and for Match device when the device is light.
- Stop a stream: admins see Stop stream on the back of a card, with a message
  the viewer sees (`stopControl` in `app.js`, `POST /api/streams/{id}/stop`,
  `PlexClient.terminate` with the session id; Plex Pass). On by default; set
  `ENABLE_ACTIONS=false` in `.env` to turn it off. `/api/me` reports `actions`.

## Phone layout and addresses (Glenn's phone audit, Nov 2026)

- Every page has an address: `#/home`, `#/servers`, `#/streams`, `#/alerts`, `#/users` (admin) or `#/history`
  (viewer), `#/subscription`, `#/bandwidth`, beside `#/requests/...` and `#/books/...`. The router is in `app.js`
  (`ROUTE_OF`, `CARD_OF`, `hashFor`, `applyRoute` on `hashchange`); old anchors (`#streams-card` and the like, in
  `LEGACY`) are replaced with the new address. Back and Forward move between pages; reload stays put.
- `web/ui.js` (`gsUI`, loaded before `app.js`) holds what every page shares: `el`/`elc`/`add`, `api`, `ago`, `when`,
  `date`, `duration`, `toast` (one toast style, above the tab bar on phones), `lock` (counted scroll lock,
  `html.gs-locked`), `go`/`replace` (navigation), `scroll` (each history entry, keyed by `history.state.gsk`, keeps
  its scroll position in memory and sessionStorage `gs-scroll`; Back restores it, a new page starts at the top,
  tapping the tab you are on goes to the top), `sheet` and `strip`. Use these rather than writing new copies.
- The phone rule is one media query everywhere: `(max-width: 760px), (max-height: 500px) and (pointer: coarse)`
  (a phone on its side counts as a phone). The desktop side is its complement in `index.html`. `web/ui.css` holds
  the shared phone layout; its selectors start with `html ` because `index.html`'s own `<style>` comes later.
- Tab bar: fixed to the bottom of the screen on every page (safe areas respected, `--tabbar-space` padding), at most
  five tabs (`PLACES` in `app.js`): viewers Home, Streams, Requests, Books, History; admins Home, Streams, Requests,
  Books, More (a sheet with Servers, Alerts, Users; the alert count shows on More). On a phone on its side the bar is
  slimmer with labels beside the icons. The Requests tab shows a small lime dot instead of the pulse ring.
- One bottom sheet for every overlay on phones (`gsUI.sheet`: grab bar, swipe down or tap the dimmed page to close,
  scroll lock): stream details, Settings, the history drawer, the account menu, the search kind menu, request
  dialogs, More. On desktop the same panels keep their own look.
- One page header on phones (`#phead`, `renderPhead`, `window.sdPhead`): a small "Telemetry" (or section) eyebrow and
  a 32 px title under the top bar; `has-phead` hides the old titles inside cards. Requests title pages skip it
  (the poster block names the title). Light and Dark live in Settings, Display, Appearance (not the top bar).
- Requests on phones: the search box is one line (kind chip plus field, Enter searches) away from Discover and
  search results (`searchBox.setCompact`); the floating Report issue button is hidden (title pages and Reported
  issues have their own). Sideways strips fade where more is hidden (`gsUI.strip`), and the current tab scrolls
  into view.
- Books & comics on phones keeps the top bar; the frame fills the space above the tab bar. The media site in a
  frame hides its own Books & comics title (on every screen now; its tabs scroll and fade the same way on phones).
- Verify phone changes at 390 x 844, 360 x 740 and 844 x 390 (touch), light and dark, viewer and admin.

## Desktop layout (Glenn's desktop audit, Nov 2026)

- The desktop rules are the `desktops and tablets` block in `web/ui.css` (the complement of the phone rule); what is
  the same on both sits above it. Keep phone and desktop in step: one look, one set of names.
- Frame: the top bar (`.sd-top`) is sticky like the side menu, on the page colour; cards have `scroll-margin-top` so
  a jump lands under it. While an overlay holds the page still (`html.gs-locked`) only the window stops scrolling on
  desktop: an `overflow: hidden` body becomes its own scroller and the sticky bar and Settings would stick to the
  top of the page. The content stops at 1,600 px and is centred; the menu stays at the left edge.
- Below 1,200 px the account is its picture only; Live now names three, two (under 1,100) or one (under 900) newest
  streams. Light, Dark and Match device are in Settings, Display, Appearance on every screen (no top bar buttons).
- The menu follows the page (`spy` in `app.js`): scrolling by hand to another card highlights it and replaces the
  address once scrolling stops (no new Back step); a click or Back holds it for 1.2 s while the smooth scroll runs.
- Every address names the browser tab: `setDocTitle` in `applyRoute` ("Servers · Greystar Telemetry"), Requests
  (`setTitle`, "Movies · Requests · ...") and Books & comics (`show` in `books.js`).
- One page header on desktop too: eyebrow plus one 48 px title. Admin dashboard: the Telemetry lockup and
  `#title`; a viewer's pages: `#phead` (`renderPhead` runs for `vh()` on desktop); Requests: `rq-eyebrow` and
  `rq-title`; Books & comics: the media site's page titles (its "Books & comics" bar title is gone in a frame on
  every screen). No Home pill (`#vhome-back`): the menu's Home does that.
- Menu and tab names are the same list (`PLACES`): Home, Bandwidth, Servers, Streams, Users or History, Requests,
  Books. Browser tab titles use the page names (My history, Books & comics).
- Books & comics keeps the top bar on desktop; the frame is the window less 168 px, so the page itself never scrolls.
- Keyboard: "Skip to content" (`.gs-skip`, first Tab stop, `ui.js`) focuses the current part's title (the card the
  address names on the desktop dashboard), or the Books frame. `.sd-content` is `role="main"`. A stream card is a
  group (`role="group"`), not a button: its own "Show playback details" button (`.sd-flipbtn`, shown on keyboard
  focus) flips it and moves focus to Flip back; a mouse can still click anywhere. The hidden side of a card is
  `inert` (`faceState`). Closing the account menu, or the history opened from it, returns focus to the account
  button (`closeMenu(true)`, `fromMenu`).
- Load: `viewing_hours` and `bandwidth_daily` (last 7 days) are fetched every 5 minutes, not every 10 seconds
  (`SLOW_MS` in `dash-shim.js`). Each loaded dataset records `at`; Live now's "Updated" time falls back to the
  streams load for people who are not given bandwidth.
- Report issue floats on Requests lists only; title pages and Reported issues have their own.
- Verify desktop changes at 1024 x 768, 1280 x 800, 1440 x 900 and 2560 x 1440, light and dark, viewer and admin.

## Running tests

```bash
cd backend && pip install -r requirements-dev.txt && python -m pytest -q
```

## Not built yet (ask Glenn before starting)

- Rebalance only suggests moves; Plex cannot move a live session between
  servers, so a real action would end the stream with a message.
- Viewer avatars from Plex user thumbnails (currently initials; the thumb URL
  is stored as `users.thumb`).
