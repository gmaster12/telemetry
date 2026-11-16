/* Greystar Telemetry: notifications and the Welcome back sheet (Glenn's pick A "Welcome sheet", Nov 14 2026).

   A bell in the top bar (`#bell`, before the Settings gear) with a count of what this person has not seen. When they
   come back to the site and there is something new (the server decides: `welcome` in GET /api/notifications), the
   Welcome back sheet opens by itself over the page, which is dimmed and slightly blurred behind it (Glenn's ask).
   The bell opens the same sheet any time.

   The sheet: "Welcome back, <name>", how many things happened since their last visit, then Needs action (billing
   problems, and for admins the Needs you list and servers down: these stay pinned until they are sorted out, not just
   until seen), New, and Earlier (already seen). Each row has its own button (Open, Track it, See subscription, ...);
   using it marks that one seen. Footer: "Remind me next time" (closes, leaves them unseen; the sheet waits for a new
   visit or new news) and "Mark all as seen". On phones it is the shared bottom sheet (gsUI.sheet).

   While the person is on the site the list is checked every 60 s (only while the tab is visible); something new
   shows a small pop-up for a few seconds (tap it to open the sheet) and bumps the count.

   Hrefs: "#/..." are page addresses; "settings:<view>" opens that Settings section (admins).

   window.gsNotify = { open(), refresh() } */
(function () {
  'use strict';
  var U = window.gsUI;
  var el = U.el;
  var POLL_MS = 60000;
  var SESSION_KEY = 'gs-welcome-done';
  var state = { items: [], unseen: 0, pinned: 0, since: null, welcome: false };
  var known = null;          // ids already shown to this tab (null until the first load)
  var open = false, auto = false, detach = null, opener = null, timer = null, started = false;
  var bell = document.getElementById('bell');
  if (!bell) {   // a page without the button in its markup (the dashboard preview): put it before the gear
    var gear = document.getElementById('gear');
    if (gear) { bell = el('button', { type: 'button', class: 'sd-gear sd-bell', id: 'bell', 'aria-label': 'Notifications', 'aria-haspopup': 'dialog', 'aria-expanded': 'false', hidden: true }); gear.parentNode.insertBefore(bell, gear); }
  }
  var root = null, panel = null;

  var svgNS = 'http://www.w3.org/2000/svg';
  var PATHS = {
    bell: ['M6 9a6 6 0 1 1 12 0c0 6 2 7 2 7H4s2-1 2-7', 'M10 20a2 2 0 0 0 4 0'],
    check: ['m5 12 5 5 9-10'],
    down: ['M12 4v12', 'M6 11l6 6 6-6', 'M5 20h14'],
    card: ['M3 6h18v12H3z', 'M3 10h18', 'M7 15h4'],
    alert: ['M12 9v4', 'M12 17h.01', 'M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z'],
    server: ['M4 4h16v6H4z', 'M4 14h16v6H4z', 'M8 7h.01', 'M8 17h.01'],
    disk: ['M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z', 'M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6', 'M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3'],
    broom: ['M5 7h14', 'M10 7V5h4v2', 'M7 7l1 13h8l1-13'],
    inbox: ['M3 13h5l2 3h4l2-3h5', 'M5 5h14l2 8v6H3v-6z'],
    close: ['M6 6l12 12', 'M18 6 6 18'],
    film: ['M3 4h18v16H3z', 'M7 4v16', 'M17 4v16', 'M3 9h4', 'M3 15h4', 'M17 9h4', 'M17 15h4'],
    tv: ['M3 7h18v12H3z', 'M8 3l4 4 4-4'],
    book: ['M4 5a2 2 0 0 1 2-2h13v15H6a2 2 0 0 0-2 2z', 'M4 20a2 2 0 0 0 2 2h13v-4'],
    headphones: ['M4 15v-3a8 8 0 0 1 16 0v3', 'M4 15h3v6H5a1 1 0 0 1-1-1z', 'M20 15h-3v6h2a1 1 0 0 0 1-1z']
  };
  function icon(name, size) {
    var s = document.createElementNS(svgNS, 'svg');
    s.setAttribute('width', size || 18); s.setAttribute('height', size || 18); s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '2');
    s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
    (PATHS[name] || PATHS.bell).forEach(function (d) { var p = document.createElementNS(svgNS, 'path'); p.setAttribute('d', d); s.appendChild(p); });
    return s;
  }
  // What each kind looks like: icon and tint class (the tints are the dashboard's own pastel tokens).
  var KIND = { billing: ['card', 'ember'], needs: ['alert', 'danger'], server: ['server', 'amber'], cleanup: ['broom', 'sand'], backup: ['disk', 'danger'],
    requests: ['inbox', 'lilac'], ready: ['check', 'volt'], started: ['down', 'sky'], problem: ['alert', 'danger'],
    issue: ['alert', 'amber'] };
  var MEDIA = { movie: ['film', 'rose', 'Movie'], tv: ['tv', 'lilac', 'Show'], comic: ['book', 'sand', 'Comic'],
    ebook: ['book', 'peri', 'Ebook'], audiobook: ['headphones', 'sky', 'Audio'] };

  // window.gsNotifyMe: the dashboard preview's stand-in for /api/me (notify-sample.js there).
  function who() { return (window.dash && window.dash.me && window.dash.me()) || (window.gsNotifyMe && window.gsNotifyMe()) || null; }
  function can() { return !!who(); }
  function firstName() {
    var me = who();
    var n = (me && me.name) || '';
    return n.split(/[\s@]/)[0] || '';
  }
  function mark(ids) {
    return U.api('POST', '/api/notifications/seen', ids ? { ids: ids } : {}).catch(function () { /* next check fixes it */ });
  }

  // ---------- the bell ----------
  function paintBell() {
    if (!bell) return;
    if (!bell.firstChild) bell.appendChild(icon('bell', 22));
    var badge = bell.querySelector('.nt-badge');
    var n = state.unseen;
    if (n) {
      if (!badge) { badge = el('span', { class: 'nt-badge', 'aria-hidden': 'true' }); bell.appendChild(badge); }
      badge.textContent = n > 99 ? '99+' : String(n);
      badge.classList.toggle('is-urgent', state.items.some(function (x) { return x.pinned && !x.seen; }));
    } else if (badge) badge.remove();
    bell.setAttribute('aria-label', n ? 'Notifications, ' + n + ' new' : 'Notifications');
    bell.hidden = false;
  }

  // ---------- loading ----------
  function load(first) {
    if (!can()) return Promise.resolve();
    return U.api('GET', '/api/notifications').then(function (body) {
      var before = known;
      state = body || state;
      known = {};
      state.items.forEach(function (x) { known[x.id] = 1; });
      paintBell();
      if (open) paint();
      if (first) {
        var done = false;
        try { done = !!sessionStorage.getItem(SESSION_KEY); } catch (e) { /* storage blocked */ }
        if (state.welcome && !done) { show(true); return; }
      }
      if (before && !open) {
        var fresh = state.items.filter(function (x) { return !before[x.id] && !x.seen && x.id !== 'new-requests'; });
        if (fresh.length) popup(fresh);
      }
    }).catch(function () { /* the next check tries again */ });
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      if (document.hidden) { schedule(); return; }
      load(false).then(schedule);
    }, POLL_MS);
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden && started) { load(false); schedule(); } });

  // ---------- a small pop-up for news that arrives while the person is here ----------
  var pop = null;
  function popup(fresh) {
    if (pop) pop.remove();
    var x = fresh[0];
    var k = KIND[x.kind] || KIND.ready;
    pop = el('button', { type: 'button', class: 'nt-pop' + (x.pinned ? ' is-urgent' : ''), onclick: function () { if (pop) pop.remove(); pop = null; show(false); } },
      el('span', { class: 'nt-pop__icon nt-tint--' + k[1] }, icon(k[0], 18)),
      el('span', { class: 'nt-pop__text' }, el('b', { text: x.title }),
        el('span', { text: fresh.length > 1 ? 'And ' + (fresh.length - 1) + ' more. Tap to see them.' : 'Tap to see it.' })));
    var live = el('span', { class: 'nt-sr', role: 'status' }, 'New notification: ' + x.title);
    (document.getElementById('streams-app') || document.body).append(pop, live);
    requestAnimationFrame(function () { if (pop) pop.classList.add('is-in'); });
    var me = pop;
    setTimeout(function () { live.remove(); if (pop === me) { me.classList.remove('is-in'); setTimeout(function () { me.remove(); if (pop === me) pop = null; }, 300); } }, 6000);
  }

  // ---------- the sheet ----------
  function build() {
    root = el('div', { class: 'nt-scrim', hidden: true, onclick: function (e) { if (e.target === root) hide(false); } });
    panel = el('section', { class: 'nt-sheet', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'nt-title', tabindex: '-1' });
    root.appendChild(panel);
    (document.getElementById('streams-app') || document.body).appendChild(root);
    root.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.stopPropagation(); hide(false); return; }
      if (e.key !== 'Tab') return;
      var f = panel.querySelectorAll('button, a[href]');
      if (!f.length) return;
      var a = f[0], z = f[f.length - 1];
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
    });
  }

  function since(ts) {
    if (!ts) return null;
    var d = new Date(ts * 1000), now = new Date();
    var days = Math.round((new Date(now.toDateString()) - new Date(d.toDateString())) / 86400000);
    var t = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    if (days === 0) return 'today ' + t;
    if (days === 1) return 'yesterday ' + t;
    if (days < 7) return d.toLocaleDateString(undefined, { weekday: 'long' }) + ' ' + t;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  function shortWhen(ts) {
    var d = new Date(ts * 1000), now = new Date();
    var days = Math.round((new Date(now.toDateString()) - new Date(d.toDateString())) / 86400000);
    var t = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    if (days === 0) return t;
    if (days === 1) return 'Yesterday';
    if (days < 7) return d.toLocaleDateString(undefined, { weekday: 'short' }) + ' ' + t;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function art(x) {
    var m = x.media && MEDIA[x.media];
    if (m) {
      var tile = el('span', { class: 'nt-art nt-art--poster nt-tint--' + m[1], 'aria-hidden': 'true' }, icon(m[0], 16),
        el('span', { class: 'nt-art__word', text: m[2] }));
      if (x.poster) {
        var im = new Image();
        im.alt = ''; im.className = 'nt-art__img';
        im.onload = function () { tile.appendChild(im); tile.classList.add('has-img'); };
        im.src = x.poster;
      }
      return tile;
    }
    var k = KIND[x.kind] || KIND.ready;
    return el('span', { class: 'nt-art nt-tint--' + k[1], 'aria-hidden': 'true' }, icon(k[0], 20));
  }

  function go(x) {
    if (typeof x.id === 'number' && !x.seen) { x.seen = true; mark([x.id]); }
    else if (x.id === 'new-requests') mark(null);
    hide(true);
    var h = x.href || '';
    if (h.indexOf('settings:') === 0) {
      if (window.stOpenTab) window.stOpenTab(h.slice(9));
      return;
    }
    if (h.charAt(0) === '#') U.go(h);
  }

  function row(x) {
    var btn = x.action ? el('button', { type: 'button', class: 'nt-btn' + (x.pinned ? ' nt-btn--accent' : ''), text: x.action, onclick: function () { go(x); } }) : null;
    return el('li', { class: 'nt-row' + (x.pinned ? ' is-pinned' : '') + (x.seen ? ' is-seen' : '') + (x.resolved ? ' is-resolved' : '') },
      art(x),
      el('span', { class: 'nt-row__text' },
        el('b', { class: 'nt-row__title', text: x.title }),
        x.body ? el('span', { class: 'nt-row__body', text: x.body }) : null),
      el('span', { class: 'nt-row__when', text: shortWhen(x.at) }),
      btn);
  }

  function section(name, items, note) {
    if (!items.length) return null;
    return el('div', { class: 'nt-sec' },
      el('h3', { class: 'nt-sec__name' }, name, note ? el('span', { class: 'nt-sec__note', text: note }) : null),
      el('ul', { class: 'nt-list' }, items.map(row)));
  }

  function paint() {
    var items = state.items || [];
    var pinned = items.filter(function (x) { return x.pinned; });
    var fresh = items.filter(function (x) { return !x.pinned && !x.seen; });
    var older = items.filter(function (x) { return !x.pinned && x.seen; }).slice(0, 6);
    var count = pinned.length + fresh.length;
    var name = firstName();
    var s = since(state.since);
    var close = el('button', { type: 'button', class: 'nt-x', 'aria-label': 'Close', onclick: function () { hide(false); } }, icon('close', 18));
    var head = el('header', { class: 'nt-head' },
      el('div', { class: 'nt-head__text' },
        el('span', { class: 'nt-eyebrow', text: auto ? 'While you were away' : 'Notifications' }),
        el('h2', { class: 'nt-title', id: 'nt-title', text: auto ? 'Welcome back' + (name ? ', ' + name : '')
          : fresh.length || pinned.some(function (x) { return !x.seen; }) ? 'What is new' : pinned.length ? 'Still to sort out' : 'You are all caught up' }),
        el('span', { class: 'nt-lead', text: !count ? 'Nothing new since your last visit.'
          : !fresh.length && !pinned.some(function (x) { return !x.seen; }) ? count + (count === 1 ? ' thing still needs a look.' : ' things still need a look.')
          : count + (count === 1 ? ' thing to catch up on' : ' things to catch up on') + (s && auto ? ' since ' + s : '') + '.' })),
      close);
    var body = el('div', { class: 'nt-body' },
      section('Needs action', pinned, 'Stays here until it is sorted out'),
      section('New', fresh),
      section('Earlier', older),
      items.length ? null : el('p', { class: 'nt-empty', text: 'When something you asked for starts downloading or is ready, it shows up here.' }));
    var foot = el('footer', { class: 'nt-foot' },
      fresh.length || pinned.some(function (x) { return !x.seen; })
        ? [el('button', { type: 'button', class: 'nt-btn nt-btn--lg', text: 'Remind me next time', onclick: function () { hide(false); } }),
           el('button', { type: 'button', class: 'nt-btn nt-btn--lg nt-btn--accent', text: 'Mark all as seen', onclick: seeAll })]
        : el('button', { type: 'button', class: 'nt-btn nt-btn--lg', text: 'Close', onclick: function () { hide(false); } }));
    panel.replaceChildren(el('div', { class: 'gs-sheet__grab', 'aria-hidden': 'true' }), head, body, foot);
  }

  function seeAll() {
    state.items.forEach(function (x) { x.seen = true; });
    state.unseen = 0;
    paintBell();
    mark(null).then(function () { load(false); });
    hide(false);
    U.toast('All caught up.');
  }

  function show(byItself) {
    if (!root) build();
    auto = !!byItself;
    if (auto) {
      try { sessionStorage.setItem(SESSION_KEY, '1'); } catch (e) { /* storage blocked */ }
      U.api('POST', '/api/notifications/shown').catch(function () {});
    }
    if (open) { paint(); return; }
    opener = byItself ? null : document.activeElement;
    open = true;
    paint();
    root.hidden = false;
    document.documentElement.classList.add('nt-open');
    if (bell) bell.setAttribute('aria-expanded', 'true');
    detach = U.sheet(panel, { onClose: function () { hide(false); }, scrim: root });
    requestAnimationFrame(function () { root.classList.add('is-in'); panel.focus({ preventScroll: true }); });
  }

  function hide(navigating) {
    if (!open) return;
    open = false;
    root.classList.remove('is-in');
    document.documentElement.classList.remove('nt-open');
    if (bell) bell.setAttribute('aria-expanded', 'false');
    if (detach) { detach(); detach = null; }
    setTimeout(function () { if (!open) root.hidden = true; }, 220);
    if (!navigating) (opener && opener.focus ? opener : bell || document.body).focus({ preventScroll: true });
  }

  if (bell) bell.addEventListener('click', function () { if (open) hide(false); else { show(false); load(false); } });

  // ---------- start once the page knows who is signed in ----------
  function start() {
    if (started || !can()) return;
    started = true;
    paintBell();
    load(true).then(schedule);
  }
  document.addEventListener('gs:ready', start);
  var tries = 0;
  // a fallback if gs:ready came before this file: start 3 s after the page knows who is signed in
  (function wait() { if (started) return; tries = can() ? tries + 1 : 0; if (tries > 6) { start(); return; } setTimeout(wait, 500); })();

  window.gsNotify = { open: function () { show(false); load(false); }, refresh: function () { return load(false); } };
})();
