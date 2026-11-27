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
  Plex watch history). Find anyone by username or email from a dropdown
  grouped by the Plex server they are on, or open their history from their
  name on a live stream card.
- **Sign in with Plex** for everyone you share a server with. Viewers see only
  the sections you allow. The Plex accounts in `ADMIN_PLEX_EMAIL` are the only
  Plex administrators; you can also add username and password admin logins
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
   dashboard. Already set to `cluster@mail.com` in the example. List more
   than one, separated by commas, to make several Plex administrators.
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

1. **Plex servers**: each of your servers can belong to its own Plex account.
   For each one, **Link another Plex account** (the first time it says **Sign
   in with Plex**), approve with that server's Plex account, then **Find
   servers** and **Add server**. Plex signs in with whatever account the
   browser is already using, so sign out at app.plex.tv between accounts, or
   use a private window. Pick a local or
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

5. **Billing**: connect Stripe with a **restricted** key (Stripe: Developers,
   API keys, Create restricted key; Read access to Customers, Subscriptions,
   Products and Charges). Customers match Plex users by email. For anyone who
   pays with a different email, use **Link to a Plex user** under "Not matched
   to a Plex user"; those links are permanent. Everyone then sees a "Your
   subscription" card when they sign in. PayPal comes later.

   **Paid by hand (a free month):** open a person from **Users**, and on their
   Subscription card set the date under **Paid by hand**. It defaults to their
   next payment date. Until that date nothing treats them as behind: they get
   no "your payment did not go through" notice, they are not on the admin's
   failed-payments list, and the access-hold watcher skips them, so their
   Movies and TV Shows are never paused for an unpaid bill (anything already
   paused is handed back on its next run, with no payment email to them).
   **Remove** on the same card puts them back under the normal checks at once.

6. **Sign-up** (People and billing): lets a new person join without you. On
   `/signup.html` they sign in with Plex (the Plex window also makes a free
   account), pay for one Stripe subscription in a payment form shown on the
   page, are invited to one server by the Plex account that owns it, and the
   invite is accepted for them. The last screen explains the Plex apps and how
   to sign in here, with a short how-to-request video
   (`web/assets/how-to-request.mp4`). While it shows, the subscription is
   matched to their Plex account in Billing (a permanent link), Billing is
   refreshed from Stripe and their Requests account is made; a progress bar
   follows that and the button to open the dashboard unlocks when it is done
   (or after two minutes: signing in never depends on it). It is **off** until you turn it on. To set it up:
   - **Where new people go**: the server, the libraries they get, and the
     number of people to stop at (Plex itself stops at 100 per server; invites
     nobody has accepted yet count too).
   - **Payment**: a second Stripe **restricted** key just for sign-up (Write on
     Checkout Sessions, Read on Subscriptions, Prices and Products), the
     publishable key (`pk_...`), and the price. The Billing key stays read-only.
   - **Test the setup** opens a checkout, reads it back and closes it again
     (nobody is charged), and reads the server's libraries.
   - **Dry run with Stripe test keys** (`rk_test_`, `pk_test_`): nothing is
     charged, so sign-up stays closed to the public and the sign-in page shows
     no Sign up button. Only the Plex emails listed under **Test accounts** can
     sign up, from `/signup.html?test`, with Stripe's test cards. A test
     account that finishes really is added to the server.

   Nobody is invited until Stripe itself says the checkout is complete, paid
   and its subscription active. The checkout is locked to the person's Plex
   email, so Billing matches them without a manual link. Someone already on a
   server, or already paying, is sent to sign in instead of paying again. If a
   person pays and closes the tab, the app finishes the invite within a minute.
   If someone has paid and cannot be added after three tries, admins get a
   **Paid sign-up needs you** notification and the app keeps trying every
   hour; **Try again now** is on their row under Sign-ups. One payment sends at
   most two invites by itself. Admins are also told when Stripe cannot be asked
   about payments, and when a finished checkout is still unconfirmed after an
   hour. Removing a row
   there changes nothing in Stripe or on Plex. Canceling a subscription does
   not remove anyone from Plex. See `backend/app/signup.py`.

## Sign-in and roles

| Who | How they sign in | What they see |
|---|---|---|
| The Plex accounts in `ADMIN_PLEX_EMAIL` | Sign in with Plex | Everything, plus Settings |
| Admin logins | Administrator sign-in (username and password) | Everything, plus Settings |
| Anyone you share a Plex server with, or who has streamed | Sign in with Plex | Only the sections allowed in Access (default: their own history) |
| Users on the Greystar Emby server (linked Emby Connect account, or a local Emby password) | Sign in with Emby | The same as a Plex viewer (default sections); never an admin |
| Any other Plex account | Refused | Nothing |

Emby sign-in needs `EMBY_URL` (the Emby server, e.g. its ZeroTier address
`http://172.25.244.168:8096`) and `EMBY_API_KEY` (Emby Dashboard, Advanced,
API Keys) in `.env`, then `docker compose up -d --force-recreate`. Without
them the button is hidden. A disabled Emby user, or an Emby Connect account not
linked to a user on that server, is refused. See `backend/app/emby.py`.

With Emby set up, Emby watch history is kept like Plex's (`backend/app/emby_plays.py`):
Emby's sessions are read every poll, and on first start Emby's activity log is
imported once (Settings' history import runs it again; nothing is added twice).
Emby plays show as server "Emby" on My history and People, with Emby covers.

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

**Shows with no TheTVDB id.** New streaming shows often have no TheTVDB id on
TMDB yet, so Seerr cannot send them to Sonarr. A request for one is added to
Sonarr directly, found by its IMDb id (exactly one match, first-air year within
a year), with the profile and folder of Seerr's default Sonarr server. The
request log tags it "Added to Sonarr". If that is not possible (no IMDb id, no
single match, 4K, or the person's series requests need approval), the show goes
on Settings, Request log, **Needs you** as "Add by hand".

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
| `web/people.js` | User menu, Users card, playback history, loading splash |
| `web/user-picker.js` | Find-a-person dropdown grouped by Plex server |
| `web/login.html`, `web/login.js` | Sign-in page |
| `web/assets/` | Logo, mark, favicon and app icons from the Greystar logo pack |
| `docs/design-system/` | Greystar Networks design system: brand book and tokens |
