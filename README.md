# Greystar Telemetry

Live monitoring for the Greystar Networks Plex fleet: active streams with poster
art, transcode details, server capacity from Netdata, bandwidth over the last
hour and a week-by-hour viewing heatmap.

- Talks to every Plex server directly. No Tautulli.
- Plex servers are added, edited and removed from the dashboard's **Settings**
  screen, including **Sign in with Plex**, which finds your servers and their
  access tokens for you.
- Server CPU and network come from **Netdata Cloud** (or each Netdata agent).
- Tokens are stored encrypted in SQLite and never sent to the browser.
- Shows sample data until the first Plex server is added.
- Keeps every viewer's **playback history** (and can import each server's own
  Plex watch history). Search people by username or email and open anyone's
  history from their name on a live stream card.
- **Sign in with Plex** for everyone you share a server with. Viewers see only
  the sections you allow. The Plex account in `ADMIN_PLEX_EMAIL` is the only
  Plex administrator; you can also add username and password admin logins
  that are not Plex accounts.

## Install on the Linux server (Docker)

```bash
git clone https://github.com/gmaster12/telemetry.git /opt/telemetry
cd /opt/telemetry
cp .env.example .env
```

Edit `.env`:

1. `SECRET_KEY`: generate once and keep it. Losing it means re-entering tokens.
   ```bash
   docker run --rm python:3.12-slim sh -c "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
   ```
2. `ADMIN_PLEX_EMAIL`: the Plex account (by email) that administers the
   dashboard. Already set to `cluster@mail.com` in the example.
3. `COOKIE_SECURE=true` as long as the site is served over HTTPS.

Lock the file down and start it:

```bash
chmod 600 .env
mkdir -p data && sudo chown 10001 data   # the container runs as uid 10001
docker compose up -d --build
curl -s http://127.0.0.1:8791/healthz     # {"ok":true}
```

The app listens on `127.0.0.1:8791` only. Put your web server in front for
HTTPS: see `deploy/nginx-telemetry.conf` or `deploy/Caddyfile.telemetry`.

## First run

Open the dashboard and choose **Sign in with Plex** with the admin Plex account.
Then open **Settings** (the sliders button in the top bar):

1. **Plex servers**: **Sign in with Plex**, approve in the Plex tab that opens,
   then **Find my servers** and **Add server** for each one. Pick a local or
   private address (for example a ZeroTier IP) when it is offered. You can also
   add a server by address and token.
2. **Netdata**: choose **Netdata Cloud**, enter the Space ID, Room ID and an API
   token (Netdata Cloud: your profile, API Tokens, scope `all`), **Save**, then
   **Test connection**. Back in **Plex servers**, **Edit** each server and pick
   its Netdata node.
3. **Alerts**: set the CPU level that counts as "hot" (default 85%).
4. **Access**:
   - **Sync Plex users** loads everyone you share servers with (names and
     emails). It also runs by itself every 6 hours.
   - **Import Plex watch history** copies each server's own history so people
     have a past from day one. Safe to run again.
   - **Default access for Plex users** picks the sections viewers see. Each
     person can get custom access or be turned off.
   - **Admin logins** adds username and password logins with full admin rights.

## Sign-in and roles

| Who | How they sign in | What they see |
|---|---|---|
| The Plex account in `ADMIN_PLEX_EMAIL` | Sign in with Plex | Everything, plus Settings |
| Admin logins | Administrator sign-in (username and password) | Everything, plus Settings |
| Anyone you share a Plex server with, or who has streamed | Sign in with Plex | Only the sections allowed in Access (default: their own history) |
| Any other Plex account | Refused | Nothing |

Sections: Live streams, Servers, Bandwidth, When people watch, My history.
Viewers never see email addresses of other people or any settings.

If you are ever locked out, create an admin login from the server:

```bash
docker compose exec -it telemetry python -m app.tools.admin create ops      # prompts for a password
docker compose exec -it telemetry python -m app.tools.admin password ops    # reset it
docker compose exec telemetry python -m app.tools.admin list
```

The bandwidth chart fills in within an hour and the viewing heatmap within a
week, because Plex only reports what is playing now; the app records one
sample per server per minute.

## Update

```bash
cd /opt/telemetry && git pull && docker compose up -d --build
```

## Troubleshooting

```bash
docker compose logs -f telemetry
docker compose exec telemetry python -m app.tools.probe plex      # raw Plex sessions and how they map
docker compose exec telemetry python -m app.tools.probe netdata   # raw Netdata Cloud responses
curl -s http://127.0.0.1:8791/healthz
```

## Layout

| Path | What |
|---|---|
| `backend/app/main.py` | FastAPI app: data API, Settings API, login, static files |
| `backend/app/plex.py` | Plex client, Sign in with Plex, session mapping |
| `backend/app/netdata.py` | Netdata Cloud and agent clients |
| `backend/app/poller.py` | Background polling and per-minute history |
| `backend/app/store.py` | SQLite: servers, settings, minute samples, users, plays, admin logins, sessions |
| `backend/app/auth.py` | Sign-in sessions, roles, sections, password hashing |
| `backend/app/tools/admin.py` | Command line for admin logins |
| `web/index.html` | The dashboard page |
| `web/settings.js` | The Settings screen |
| `web/dash-shim.js` | Loads the signed-in user and the data the page may show |
| `web/people.js` | User menu, Users search, playback history, loading splash |
| `web/login.html`, `web/login.js` | Sign-in page |
| `web/assets/` | Logo, mark, favicon and app icons from the Greystar logo pack |
| `docs/design-system/` | Greystar Networks design system: brand book and tokens |
