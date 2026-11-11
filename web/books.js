/* Greystar Telemetry: Books & comics. The Greystar media site (comics, ebooks, audiobooks) in a frame.
   The server makes a one-time sign-in pass (GET /api/books/link); the frame opens it and the media site
   signs the person in with their Plex account and shows itself in Telemetry's look. Page: #/books.

   The frame is made once and then only hidden, so an audiobook keeps playing while the person moves
   around the dashboard. The media site sends a few messages (checked against its origin and the frame's window):
     ready          it loaded
     immersive      a reader or the full audiobook player opened or closed: the frame takes the whole window
                    while it is on (kept as state, so coming back to #/books puts it back)
     audiobook      what its audiobook player holds ({ title, author, chapter, playing, positionMs, durationMs },
                    or null when closed): on every other page a "Now listening" bar shows it, with Play/Pause,
                    Stop, and the book itself to open the full player
     signed-out     its session ended: get a fresh pass and reload the frame
     open-requests  its shared search box picked Movies & shows: open #/requests/search?q=...

   Opening #/books/<path> again (the shared search box, usearch.js) does not reload the frame: once it is
   ready we send { source: 'greystar-telemetry', type: 'navigate', path } and the media site goes there,
   replacing its current entry so Back needs no extra press. Before it is ready the path waits and goes
   with the 'ready' message.

   The bar sends { source: 'greystar-telemetry', type: 'audiobook', action: 'toggle' | 'stop' | 'open' }.

   We also send the media site its look, so its own palette matches ours instead of a fixed light look:
     { source: 'greystar-telemetry', type: 'look', look: 'light' | 'dark' }
   sent once the frame says it is ready, and again whenever Telemetry's own look changes (Settings,
   Display, Appearance sets data-look on #streams-app to "light"/"dark"/absent; absent follows the OS
   scheme — see telemetryLook() below). */
(function () {
  'use strict';

  var root = document.getElementById('streams-app');
  var page = document.getElementById('books-page');
  if (!root || !page) return;
  var cfg = null;          // GET /api/books/config: { enabled, origin, plex }
  var frame = null;
  var loading = null;
  var lastReload = 0;
  var ready = false;        // the frame said 'ready' since it was last (re)loaded
  var pending = null;       // a path to send once it is
  var me = window.gsMe || null;
  var immersive = false;    // the frame asked for the whole window (a reader or the full audiobook player)
  var np = null;            // the media site's audiobook player, or null
  var npBar = null;

  function telemetryLook() {
    var explicit = root.dataset.look; // Settings, Display, Appearance: "light", "dark", or absent (Match device)
    if (explicit === 'light' || explicit === 'dark') return explicit;
    return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }

  function sendLook() {
    if (!frame || !frame.contentWindow || !cfg || !cfg.origin) return;
    try { frame.contentWindow.postMessage({ source: 'greystar-telemetry', type: 'look', look: telemetryLook() }, cfg.origin); }
    catch (e) { /* the frame went away */ }
  }
  if (window.matchMedia) {
    var schemeQuery = window.matchMedia('(prefers-color-scheme: dark)');
    var onSchemeChange = function () { sendLook(); };
    if (schemeQuery.addEventListener) schemeQuery.addEventListener('change', onSchemeChange);
    else if (schemeQuery.addListener) schemeQuery.addListener(onSchemeChange); // older Safari
  }
  new MutationObserver(sendLook).observe(root, { attributes: true, attributeFilter: ['data-look'] });

  var el = window.gsUI.el;   // shared helpers (ui.js)

  function api(path) { return window.gsUI.api('GET', path); }

  function canBooks() {
    if (!me) return false;
    return me.role === 'admin' || (me.sections || []).indexOf('books') >= 0;
  }

  function loadConfig() {
    if (!canBooks()) { cfg = null; return; }
    api('/api/books/config').then(function (c) { cfg = c; }).catch(function () { cfg = null; })
      .then(function () {
        if (window.sdNavChanged) window.sdNavChanged();
        document.dispatchEvent(new CustomEvent('gs:books-config'));
        route();
      });
  }
  document.addEventListener('gs:me', function (e) { me = e.detail; loadConfig(); });
  if (me) loadConfig();

  // ---------- page ----------
  function parse() {
    var h = location.hash.replace(/^#\/?/, '');
    if (h !== 'books' && h.indexOf('books/') !== 0 && h.indexOf('books?') !== 0) return null;
    var rest = h.slice(5);
    return { next: rest && rest.charAt(0) === '/' ? rest : '/' };
  }

  function show(on) {
    var was = root.dataset.page === 'books';
    if (on) root.dataset.page = 'books';
    else if (root.dataset.page === 'books') delete root.dataset.page;
    if (on && immersive) root.dataset.immersive = '1'; else delete root.dataset.immersive;
    page.hidden = !on;
    renderNp();
    if (on) document.title = 'Books & comics · Greystar Telemetry';
    if (was !== on && window.sdNavChanged) window.sdNavChanged();
  }

  function note(title, text) {
    return el('div', { class: 'bk-note', role: 'status' }, el('h2', { text: title }), el('p', { text: text }));
  }

  function setLoading(on, text) {
    if (!loading) return;
    loading.hidden = !on;
    if (text) loading.querySelector('span:last-child').textContent = text;
  }

  function ensureFrame(next) {
    if (frame) return;
    page.replaceChildren();
    if (!cfg || !cfg.enabled) { page.appendChild(note('Books & comics is not set up yet', 'An administrator connects the Greystar media site in Settings, Books & comics.')); return; }
    if (!cfg.plex) { page.appendChild(note('Sign in with Plex to use Books & comics', 'Books & comics signs you in to the media site with your Plex account. This login is not a Plex account.')); return; }
    loading = el('div', { class: 'bk-loading', role: 'status' }, el('span', { class: 'rq-spin', 'aria-hidden': 'true' }), el('span', { text: 'Opening Books & comics' }));
    frame = el('iframe', { class: 'bk-frame', title: 'Books & comics', allow: 'autoplay; fullscreen; clipboard-write', referrerpolicy: 'no-referrer' });
    frame.addEventListener('load', function () { setLoading(false); });
    ready = false;
    page.append(loading, frame);
    signIn(next);
  }

  function signIn(next) {
    lastReload = Date.now();
    ready = false;
    pending = null;
    immersive = false;
    np = null; renderNp();   // a new page in the frame: no player yet
    setLoading(true, 'Opening Books & comics');
    api('/api/books/link?next=' + encodeURIComponent(next || '/')).then(function (r) {
      // The look rides along (outside the signed pass, it is only cosmetic) so the media site paints in
      // Telemetry's light/dark look from its very first frame instead of waiting for the message.
      frame.src = r.url + (r.url.indexOf('?') >= 0 ? '&' : '?') + 'look=' + telemetryLook();
    }).catch(function (e) {
      setLoading(false);
      page.replaceChildren(note('Books & comics could not open', e.message));
      frame = null;
    });
  }

  function navigate(path) {
    if (!frame || !frame.contentWindow || !cfg || !cfg.origin) return;
    if (!ready) { pending = path; return; }
    try { frame.contentWindow.postMessage({ source: 'greystar-telemetry', type: 'navigate', path: path }, cfg.origin); }
    catch (e) { /* the frame went away */ }
  }

  function route(force) {
    var r = parse();
    // Leaving: another page may already have set its own data-page, so tidy up whenever the frame was showing.
    if (!r) { if (root.dataset.page === 'books' || !page.hidden || root.dataset.immersive) show(false); return; }
    if (cfg === null && canBooks()) return; // config still loading; route() runs again when it arrives
    if (!canBooks()) { show(false); return; }
    var fresh = !frame;
    ensureFrame(r.next);
    // An existing frame goes to the new page without reloading (a plain #/books keeps where it is).
    if (!fresh && frame && (r.next !== '/' || force === true)) navigate(r.next);
    show(true);
    window.gsUI.scroll.enter();   // the frame keeps its own place; the page itself always starts at the top
    window.scrollTo({ top: 0 });
  }
  window.addEventListener('hashchange', function () { route(); });

  window.addEventListener('message', function (e) {
    if (!cfg || !cfg.origin || e.origin !== cfg.origin || !frame || e.source !== frame.contentWindow) return;
    var d = e.data;
    if (!d || d.source !== 'greystar-media') return;
    if (d.type === 'ready') {
      setLoading(false); sendLook(); ready = true;
      if (pending) { var p = pending; pending = null; navigate(p); }
    }
    else if (d.type === 'open-requests') {
      // The media site's box picked Movies & shows: that lives on our Requests page.
      var q = typeof d.q === 'string' ? d.q.trim().slice(0, 200) : '';
      window.location.hash = '#/requests' + (q ? '/search?q=' + encodeURIComponent(q) : '');
    }
    else if (d.type === 'immersive') {
      immersive = !!d.on;
      if (immersive && root.dataset.page === 'books') root.dataset.immersive = '1'; else delete root.dataset.immersive;
    } else if (d.type === 'audiobook') {
      np = cleanNp(d.state);
      renderNp();
    } else if (d.type === 'signed-out') {
      if (Date.now() - lastReload < 15000) return; // just signed in; do not loop
      signIn('/');
    }
  });

  // ---------- Now listening ----------
  /* The frame is only hidden on other pages and keeps playing, so the player follows the person around: one bar
     with the book (opens the full player on the Books page), Play or Pause, and Stop (closes the player). It
     shows on every page but Books & comics, where the media site's own player is. */
  function cleanNp(s) {
    if (!s || typeof s !== 'object' || typeof s.title !== 'string' || !s.title) return null;
    var text = function (v) { return typeof v === 'string' && v.trim() ? v.trim().slice(0, 200) : null; };
    var ms = function (v) { return typeof v === 'number' && isFinite(v) && v >= 0 ? v : 0; };
    return { title: s.title.trim().slice(0, 200), author: text(s.author), chapter: text(s.chapter), playing: s.playing === true,
      positionMs: ms(s.positionMs), durationMs: ms(s.durationMs) };
  }

  function sendPlayer(action) {
    if (!frame || !frame.contentWindow || !cfg || !cfg.origin) return;
    try { frame.contentWindow.postMessage({ source: 'greystar-telemetry', type: 'audiobook', action: action }, cfg.origin); }
    catch (e) { /* the frame went away */ }
  }

  function npIcon(name, size) { return window.sdIcon ? window.sdIcon(name, size) : el('span'); }

  function buildNp() {
    var open = el('button', { type: 'button', class: 'np__open' },
      el('span', { class: 'np__icon' }, npIcon('headphones', 22)),
      el('span', { class: 'np__text' },
        el('span', { class: 'np__state' }, el('i', { class: 'np__shape', 'aria-hidden': 'true' }), el('span', { class: 'np__word' })),
        el('b', { class: 'np__title' }),
        el('span', { class: 'np__sub' })));
    open.addEventListener('click', function () {
      // The Books page first (the frame shows again), then the full player.
      if (!/^#\/?books(\/|$|\?)/.test(location.hash)) window.gsBooks.open('');
      sendPlayer('open');
    });
    var play = el('button', { type: 'button', class: 'np__btn np__play' });
    play.addEventListener('click', function () { sendPlayer('toggle'); });
    var stop = el('button', { type: 'button', class: 'np__btn np__stop', 'aria-label': 'Stop and close the player', title: 'Stop and close the player' }, npIcon('stop', 18));
    stop.addEventListener('click', function () { sendPlayer('stop'); });
    var bar = el('span', { class: 'np__track', 'aria-hidden': 'true' }, el('i'));
    npBar = el('section', { class: 'np', role: 'region', 'aria-label': 'Now listening' }, open, play, stop, bar);
    root.appendChild(npBar);
  }

  function renderNp() {
    var on = !!np && root.dataset.page !== 'books';
    root.classList.toggle('has-np', on);
    if (!on) {
      if (!npBar || npBar.hidden) return;
      // Stop pressed from the keyboard: focus goes to the page, not lost with the bar.
      var hadFocus = npBar.contains(document.activeElement);
      npBar.hidden = true;
      var main = document.getElementById('content');
      if (hadFocus && main) { if (!main.hasAttribute('tabindex')) main.setAttribute('tabindex', '-1'); main.focus({ preventScroll: true }); }
      return;
    }
    if (!npBar) buildNp();
    npBar.hidden = false;
    npBar.classList.toggle('is-playing', np.playing);
    npBar.querySelector('.np__word').textContent = np.playing ? 'Now listening' : 'Paused';
    npBar.querySelector('.np__title').textContent = np.title;
    var left = np.durationMs > np.positionMs ? window.gsUI.duration((np.durationMs - np.positionMs) / 1000) + ' left' : '';
    npBar.querySelector('.np__sub').textContent = [np.chapter && np.chapter !== np.title ? np.chapter : np.author, left].filter(Boolean).join(' · ');
    npBar.querySelector('.np__open').setAttribute('aria-label', 'Open the player: ' + np.title + (np.playing ? ', playing' : ', paused'));
    var play = npBar.querySelector('.np__play');
    play.replaceChildren(npIcon(np.playing ? 'pause' : 'play', 20));
    play.setAttribute('aria-label', np.playing ? 'Pause' : 'Play');
    play.title = np.playing ? 'Pause' : 'Play';
    npBar.querySelector('.np__track i').style.width = (np.durationMs ? Math.min(100, (np.positionMs / np.durationMs) * 100) : 0).toFixed(1) + '%';
  }

  window.gsBooks = {
    available: function () { return !!(cfg && cfg.enabled); },
    // Turned on, and this person signed in with Plex (the media site needs a Plex account to know who it is).
    usable: function () { return !!(cfg && cfg.enabled && cfg.plex); },
    open: function (path) {
      var target = '#/books' + (path || '');
      // The same address again (a repeated search, or the member moved around inside the frame): send it anyway.
      if (location.hash === target) route(true); else location.hash = target;
    },
    close: function () { show(false); }
  };

  route();
})();
