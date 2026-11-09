/* Greystar Telemetry: Books & comics. The Greystar media site (comics, ebooks, audiobooks) in a frame.
   The server makes a one-time sign-in pass (GET /api/books/link); the frame opens it and the media site
   signs the person in with their Plex account and shows itself in Telemetry's look. Page: #/books.

   The frame is made once and then only hidden, so an audiobook keeps playing while the person moves
   around the dashboard. The media site sends a few messages (checked against its origin):
     ready        it loaded
     immersive    a reader opened or closed: hide or show the dashboard menu for a full-screen page
     signed-out   its session ended: get a fresh pass and reload the frame

   We send the media site one message, so its own palette matches ours instead of a fixed light look:
     { source: 'greystar-telemetry', type: 'look', look: 'light' | 'dark' }
   sent once the frame says it is ready, and again whenever Telemetry's own look changes (Settings,
   Display, Appearance sets data-look on #streams-app to "light"/"dark"/absent; absent follows the OS
   scheme — see telemetryLook() below). */
(function () {
  'use strict';

  var root = document.getElementById('streams-app');
  var page = document.getElementById('books-page');
  if (!root || !page) return;
  var HEADERS = { 'X-Requested-With': 'greystar' };
  var cfg = null;          // GET /api/books/config: { enabled, origin, plex }
  var frame = null;
  var loading = null;
  var lastDashHash = '';
  var lastReload = 0;
  var me = window.gsMe || null;

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

  function el(tag, props) {
    var e = document.createElement(tag);
    props = props || {};
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v == null || v === false) return;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) {
      var kid = arguments[i];
      if (kid == null || kid === false) continue;
      e.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    }
    return e;
  }

  function api(path) {
    return fetch(path, { headers: HEADERS, credentials: 'same-origin' }).then(function (r) {
      if (r.status === 401) { window.location.replace('/login.html'); throw new Error('Signed out.'); }
      return r.json().catch(function () { return null; }).then(function (j) {
        if (!r.ok) { var err = new Error((j && j.detail) || ('HTTP ' + r.status)); err.status = r.status; throw err; }
        return j;
      });
    });
  }

  function canBooks() {
    if (!me) return false;
    return me.role === 'admin' || (me.sections || []).indexOf('books') >= 0;
  }

  function loadConfig() {
    if (!canBooks()) { cfg = null; return; }
    api('/api/books/config').then(function (c) { cfg = c; }).catch(function () { cfg = null; })
      .then(function () { if (window.sdNavChanged) window.sdNavChanged(); route(); });
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
    else {
      if (root.dataset.page === 'books') delete root.dataset.page;
      delete root.dataset.immersive;
    }
    page.hidden = !on;
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
    page.append(loading, frame);
    signIn(next);
  }

  function signIn(next) {
    lastReload = Date.now();
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

  function route() {
    var r = parse();
    if (!r) { if (root.dataset.page === 'books') show(false); return; }
    if (cfg === null && canBooks()) return; // config still loading; route() runs again when it arrives
    if (!canBooks()) { show(false); return; }
    ensureFrame(r.next);
    show(true);
    window.scrollTo({ top: 0 });
  }
  window.addEventListener('hashchange', route);

  window.addEventListener('message', function (e) {
    if (!cfg || !cfg.origin || e.origin !== cfg.origin || !frame || e.source !== frame.contentWindow) return;
    var d = e.data;
    if (!d || d.source !== 'greystar-media') return;
    if (d.type === 'ready') { setLoading(false); sendLook(); }
    else if (d.type === 'immersive') {
      if (d.on && root.dataset.page === 'books') root.dataset.immersive = '1'; else delete root.dataset.immersive;
    } else if (d.type === 'signed-out') {
      if (Date.now() - lastReload < 15000) return; // just signed in; do not loop
      signIn('/');
    }
  });

  window.gsBooks = {
    available: function () { return !!(cfg && cfg.enabled); },
    open: function (path) {
      if (!parse()) lastDashHash = location.hash;
      var target = '#/books' + (path || '');
      if (location.hash === target) route(); else location.hash = target;
    },
    close: function () {
      history.replaceState(null, '', location.pathname + location.search + (lastDashHash && lastDashHash.indexOf('#/books') !== 0 ? lastDashHash : ''));
      show(false);
    }
  };

  route();
})();
