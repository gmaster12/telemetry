/* Greystar Telemetry: signed-in user, roles, Users search, playback history and
   the loading splash. Works with dash-shim.js (window.dash, window.gsMe). */
(function () {
  'use strict';

  var HEADERS = { 'Content-Type': 'application/json', 'X-Requested-With': 'greystar' };
  var root = document.getElementById('streams-app');
  var me = null;

  // ---------- helpers ----------
  function el(tag, props) {
    var e = document.createElement(tag);
    props = props || {};
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v == null || v === false) return;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), v);
      else if (k === 'value') e.value = v;
      else e.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) {
      var kid = arguments[i];
      if (kid == null || kid === false) continue;
      if (Array.isArray(kid)) kid.forEach(function (x) { if (x) e.appendChild(typeof x === 'string' ? document.createTextNode(x) : x); });
      else e.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    }
    return e;
  }
  function icon(name, size) {
    return window.sdIcon ? window.sdIcon(name, size) : document.createTextNode('');
  }
  function api(method, path, body) {
    return fetch(path, { method: method, headers: HEADERS, credentials: 'same-origin', cache: 'no-store', body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        if (r.status === 401) { window.location.replace('/login.html'); throw new Error('Signed out'); }
        return r.json().catch(function () { return null; }).then(function (j) {
          if (!r.ok) throw new Error((j && j.detail) || ('HTTP ' + r.status));
          return j;
        });
      });
  }
  function avatar(name, size) {
    var s = String(name || '?');
    var hash = Array.from(s).reduce(function (h, c) { return h + c.charCodeAt(0); }, 0) % 4;
    var letter = (s.replace(/[^A-Za-z0-9]/g, '')[0] || '?').toUpperCase();
    return el('span', { class: 'sd-avatar sd-avatar--' + hash + (size ? ' sd-avatar--' + size : ''), 'aria-hidden': 'true', text: letter });
  }
  var DAY = 86400000;
  function dayLabel(ts) {
    var d = new Date(ts * 1000);
    var today = new Date(); today.setHours(0, 0, 0, 0);
    var that = new Date(d); that.setHours(0, 0, 0, 0);
    var diff = Math.round((today - that) / DAY);
    if (diff === 0) return 'Today';
    if (diff === 1) return 'Yesterday';
    if (diff < 7) return d.toLocaleDateString(undefined, { weekday: 'long' });
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: d.getFullYear() === today.getFullYear() ? undefined : 'numeric' });
  }
  function timeLabel(ts) {
    return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }
  function ago(ts) {
    if (!ts) return 'Never';
    var s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 90) return 'Just now';
    if (s < 3600) return Math.round(s / 60) + ' min ago';
    if (s < 86400) return Math.round(s / 3600) + ' h ago';
    if (s < 86400 * 30) return Math.round(s / 86400) + ' d ago';
    return new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }
  var SECTION_LABEL = { streams: 'Live streams', mystreams: 'My streams', requests: 'Requests', servers: 'Servers', bandwidth: 'Bandwidth', viewing: 'When people watch', history: 'My history' };

  // ---------- loading splash ----------
  var splash = document.getElementById('splash');
  function hideSplash() {
    if (!splash || splash.hidden) return;
    splash.classList.add('is-done');
    document.dispatchEvent(new CustomEvent('gs:ready'));  // index.html plays the page-load build
    setTimeout(function () { splash.hidden = true; }, 350);
  }
  setTimeout(hideSplash, 8000);

  // ---------- roles ----------
  var ALL = ['streams', 'servers', 'bandwidth', 'viewing', 'history'];
  function applyRole() {
    root.classList.toggle('gs-admin', me.role === 'admin');
    root.classList.toggle('gs-viewer', me.role !== 'admin');
    ALL.forEach(function (s) { root.classList.toggle('gs-no-' + s, !window.dash.can(s)); });
    var dashSections = ['streams', 'servers', 'bandwidth', 'viewing'].filter(window.dash.can);
    root.classList.toggle('gs-only-history', dashSections.length === 0);
    var own = !!(window.dash.ownStreams && window.dash.ownStreams());
    root.classList.toggle('gs-own-streams', own);
    var t = document.getElementById('title');
    if (t) t.textContent = own ? 'Your streams' : 'Active streams';
    var gear = document.getElementById('gear');
    if (gear) gear.hidden = !(me.role === 'admin' || window.dash.can('streams'));
  }

  // ---------- user pill and menu ----------
  var menuOpen = false;
  function renderUser() {
    var box = document.getElementById('user');
    if (!box || !me) return;
    box.replaceChildren();
    var a = avatar(me.name, null);
    a.appendChild(el('span', { class: 'sd-avatar__on' }));
    var btn = el('button', { type: 'button', class: 'sd-user__btn', 'aria-haspopup': 'menu', 'aria-expanded': menuOpen ? 'true' : 'false', 'aria-controls': 'user-menu',
      'aria-label': me.name + ', account menu', onclick: function () { menuOpen = !menuOpen; renderUser(); if (menuOpen) { var f = document.querySelector('#user-menu [role="menuitem"]'); if (f) f.focus(); } } },
      a, el('span', { class: 'sd-user__text' },
        el('span', { class: 'sd-user__name', text: me.name }), el('br'),
        el('span', { class: 'sd-user__org', text: me.role === 'admin' ? (me.kind === 'local' ? 'Administrator' : 'Administrator · Plex') : 'Greystar Networks' })));
    box.appendChild(btn);
    if (!menuOpen) return;
    var items = [];
    if (me.kind === 'plex' && (me.role === 'admin' || window.dash.can('history'))) {
      items.push(el('button', { type: 'button', role: 'menuitem', class: 'gp-menu__item', onclick: function () { closeMenu(); openHistory('me', me.name); } }, icon('history', 18), 'My playback history'));
    }
    if (me.role === 'admin') {
      items.push(el('button', { type: 'button', role: 'menuitem', class: 'gp-menu__item', onclick: function () { closeMenu(); focusUsers(); } }, icon('users', 18), 'Users'));
      items.push(el('button', { type: 'button', role: 'menuitem', class: 'gp-menu__item', onclick: function () { closeMenu(); openSettingsTab('access'); } }, icon('sliders', 18), 'Access and logins'));
    }
    items.push(el('button', { type: 'button', role: 'menuitem', class: 'gp-menu__item', onclick: signOut }, icon('logout', 18), 'Sign out'));
    var menu = el('div', { class: 'gp-menu', id: 'user-menu', role: 'menu', 'aria-label': 'Account' },
      el('div', { class: 'gp-menu__who' }, el('b', { text: me.name }), me.email ? el('span', { text: me.email }) : null), items);
    menu.addEventListener('keydown', function (e) {
      var list = Array.prototype.slice.call(menu.querySelectorAll('[role="menuitem"]'));
      var i = list.indexOf(document.activeElement);
      if (e.key === 'Escape') { closeMenu(); btn.focus(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); list[(i + 1) % list.length].focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); list[(i - 1 + list.length) % list.length].focus(); }
    });
    box.appendChild(menu);
  }
  function closeMenu() { if (menuOpen) { menuOpen = false; renderUser(); } }
  document.addEventListener('click', function (e) {
    var box = document.getElementById('user');
    if (menuOpen && box && !box.contains(e.target)) closeMenu();
  });
  function signOut() {
    api('POST', '/api/auth/logout').catch(function () {}).then(function () { window.location.replace('/login.html'); });
  }
  function openSettingsTab(t) {
    if (window.stOpenTab) window.stOpenTab(t);
    if (window.sdOpenSettings) window.sdOpenSettings(true);
    var dock = document.getElementById('settings-dock');
    if (dock) dock.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // ---------- history list (drawer and the viewer's own section) ----------
  function playItem(r) {
    var thumb = el('span', { class: 'gp-play__thumb', 'aria-hidden': 'true' });
    if (r.poster) thumb.style.backgroundImage = 'url("' + r.poster + '")';
    else thumb.appendChild(icon('play', 18));
    var meta = [r.server, r.player || r.device, timeLabel(r.started_at)];
    if (r.source === 'live') meta.push(r.live ? 'watching now' : (r.watched_min ? plural(r.watched_min, 'min', 'min') : 'under a minute'));
    if (r.method) meta.push(r.method);
    var tags = el('span', { class: 'gp-play__tags' });
    if (r.live) tags.appendChild(el('span', { class: 'gp-tag gp-tag--live' }, el('i', { 'aria-hidden': 'true' }), 'Live'));
    if (r.source === 'plex') tags.appendChild(el('span', { class: 'gp-tag', text: 'From Plex history' }));
    if (r.source === 'live' && r.progress_pct != null && !r.live) tags.appendChild(el('span', { class: 'gp-tag', text: Math.round(r.progress_pct) + '% watched' }));
    return el('li', { class: 'gp-play' + (r.live ? ' is-live' : '') }, thumb,
      el('span', { class: 'gp-play__main' },
        el('b', { class: 'gp-play__title', text: r.title || 'Unknown title' }),
        r.subtitle ? el('span', { class: 'gp-play__sub', text: r.subtitle }) : null,
        el('span', { class: 'gp-play__meta', text: meta.filter(Boolean).join(' · ') })),
      tags.childNodes.length ? tags : null);
  }
  function historyList(rows, into) {
    var lastDay = into.dataset.lastDay || '';
    var list = into.querySelector('ol:last-of-type');
    rows.forEach(function (r) {
      var d = dayLabel(r.started_at);
      if (d !== lastDay) {
        into.appendChild(el('h4', { class: 'gp-day', text: d }));
        list = el('ol', { class: 'gp-plays' });
        into.appendChild(list);
        lastDay = d;
      }
      list.appendChild(playItem(r));
    });
    into.dataset.lastDay = lastDay;
  }
  function statsBlock(stats) {
    if (!stats) return null;
    var tile = function (v, l) { return el('div', { class: 'gp-stat' }, el('b', { text: v }), el('span', { text: l })); };
    var block = el('div', { class: 'gp-stats' },
      tile(String(stats.plays_30d), 'Plays, 30 days'), tile(String(stats.hours_30d), 'Hours, 30 days'), tile(String(stats.plays_total), 'All plays'));
    var extra = el('div', { class: 'gp-extra' });
    if (stats.top_titles && stats.top_titles.length) {
      extra.appendChild(el('div', { class: 'gp-top' }, el('span', { class: 'gp-label', text: 'Most watched, 30 days' }),
        el('ol', null, stats.top_titles.map(function (t) { return el('li', null, el('span', { text: t.title }), el('small', { text: plural(t.plays, 'play', 'plays') })); }))));
    }
    if (stats.servers && stats.servers.length) {
      extra.appendChild(el('p', { class: 'gp-servers' }, el('span', { class: 'gp-label', text: 'Watches on' }), ' ' + stats.servers.join(', ')));
    }
    return el('div', null, block, extra.childNodes.length ? extra : null);
  }
  function emptyHistory(isSelf) {
    return el('div', { class: 'gp-empty' }, el('b', { text: 'No playback history yet' }),
      el('span', { text: isSelf ? 'Anything you play on a Greystar server shows up here.' : 'Plays are recorded from now on. Import Plex watch history in Settings, Access, to fill in the past.' }));
  }

  // A history view with paging: fetchPage(before) returns {rows}
  function pagedHistory(container, fetchPage, isSelf) {
    var box = el('div', { class: 'gp-history' });
    var more = el('button', { type: 'button', class: 'st-btn gp-more', text: 'Show older plays', hidden: true });
    var status = el('p', { class: 'st-note', role: 'status' });
    var oldest = null;
    var total = 0;
    function page() {
      more.disabled = true; more.textContent = 'Loading…';
      return fetchPage(oldest).then(function (rows) {
        total += rows.length;
        if (!total) { box.replaceChildren(emptyHistory(isSelf)); more.hidden = true; return; }
        historyList(rows, box);
        if (rows.length) oldest = rows[rows.length - 1].started_at;
        more.hidden = rows.length < 50;
        status.textContent = '';
      }).catch(function (e) {
        status.textContent = 'Could not load history: ' + e.message;
      }).finally(function () { more.disabled = false; more.textContent = 'Show older plays'; });
    }
    more.addEventListener('click', page);
    container.append(box, status, more);
    return page();
  }

  // ---------- subscription (Stripe) ----------
  var ZERO_DECIMAL = ['bif', 'clp', 'djf', 'gnf', 'jpy', 'kmf', 'krw', 'mga', 'pyg', 'rwf', 'ugx', 'vnd', 'vuv', 'xaf', 'xof', 'xpf'];
  function money(amount, currency) {
    var cur = (currency || 'usd').toUpperCase();
    var v = ZERO_DECIMAL.indexOf((currency || '').toLowerCase()) >= 0 ? amount : amount / 100;
    try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur }).format(v); } catch (e) { return v.toFixed(2) + ' ' + cur; }
  }
  function day(ts) { return ts ? new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : null; }
  var SUB_STATUS = {
    active: ['Active', 'ok'], trialing: ['Trial', 'ok'], past_due: ['Past due', 'warn'], unpaid: ['Unpaid', 'warn'],
    incomplete: ['Payment incomplete', 'warn'], paused: ['Paused', 'off'], canceled: ['Canceled', 'off'], incomplete_expired: ['Expired', 'off']
  };
  function subBadge(status) {
    var st = SUB_STATUS[status] || [status, 'off'];
    return el('span', { class: 'gp-subbadge gp-subbadge--' + st[1] }, el('i', { 'aria-hidden': 'true' }), st[0]);
  }
  function period(sub) {
    var every = sub.interval_count > 1 ? 'every ' + sub.interval_count + ' ' + sub.interval + 's' : sub.interval ? 'a ' + sub.interval : '';
    return money(sub.amount, sub.currency) + (every ? ' ' + every : '');
  }
  function whenLine(sub) {
    if (sub.status === 'canceled') return 'Ended ' + (day(sub.ended_at || sub.canceled_at) || '');
    if (sub.cancel_at_period_end || sub.cancel_at) return 'Ends ' + day(sub.cancel_at || sub.current_period_end) + ', will not renew';
    if (sub.status === 'trialing' && sub.trial_end) return 'Trial ends ' + day(sub.trial_end);
    if (sub.status === 'past_due' || sub.status === 'unpaid') return 'Payment is overdue' + (sub.current_period_end ? ' since ' + day(sub.current_period_end) : '');
    if (sub.current_period_end) return 'Renews ' + day(sub.current_period_end);
    return null;
  }
  function paymentLine(p) {
    if (!p) return null;
    var what = { succeeded: 'Paid', failed: 'Failed', pending: 'Pending', refunded: 'Refunded' }[p.status] || p.status;
    return el('p', { class: 'gp-sub__pay' + (p.status === 'failed' ? ' is-failed' : '') },
      el('span', { class: 'gp-label', text: 'Last payment' }), ' ' + money(p.amount, p.currency) + ' on ' + day(p.created) + ' · ', el('b', { text: what }));
  }
  // summary: from /api/me/subscription or /api/users/{id}. who: 'self' or 'admin'.
  function subscriptionCard(summary, who) {
    var self = who === 'self';
    var card = el('div', { class: 'gp-sub' + (self ? '' : ' gp-sub--light') });
    card.appendChild(el('p', { class: 'gp-eyebrow gp-sub__eyebrow', text: self ? 'Your subscription' : 'Subscription' }));
    var subs = (summary && summary.subscriptions) || [];
    if (!subs.length) {
      card.append(el('b', { class: 'gp-sub__plan', text: 'No subscription found' }),
        el('p', { class: 'gp-sub__note', text: self
          ? 'We could not find a subscription for ' + ((summary && summary.emails && summary.emails[0]) || 'your Plex email') + '. If you pay with a different email address, ask the administrator to link it.'
          : 'No Stripe customer matched ' + ((summary && summary.emails && summary.emails.join(', ')) || 'this person (no email on file)') + '. Link their Stripe email in Settings, Billing.' }));
      var pay0 = paymentLine(summary && summary.last_payment);
      if (pay0) card.appendChild(pay0);
      return card;
    }
    var main = subs[0];
    card.append(
      el('div', { class: 'gp-sub__top' }, el('b', { class: 'gp-sub__plan', text: main.plan }), subBadge(main.status)),
      el('p', { class: 'gp-sub__price', text: period(main) }));
    if (whenLine(main)) card.appendChild(el('p', { class: 'gp-sub__when', text: whenLine(main) }));
    subs.slice(1).forEach(function (sub) {
      card.appendChild(el('div', { class: 'gp-sub__more' }, el('span', { text: sub.plan + ' · ' + period(sub) }), subBadge(sub.status)));
    });
    var pay = paymentLine(summary.last_payment);
    if (pay) card.appendChild(pay);
    if (!self) {
      card.appendChild(el('p', { class: 'gp-sub__note', text: (summary.linked ? 'Linked by hand to ' : 'Matched by email ') + (summary.emails || []).join(', ') }));
    }
    return card;
  }
  var mySub = null;
  function loadMySubscription() {
    if (!me || me.kind !== 'plex') return;
    api('GET', '/api/me/subscription').then(function (r) { mySub = r; renderMySub(); }).catch(function () {});
  }
  function renderMySub() {
    var box = document.getElementById('sub-card');
    if (!box) return;
    box.replaceChildren();
    // Admin without a subscription, or Stripe not connected: nothing to show.
    var show = mySub && mySub.configured && !(me.role === 'admin' && mySub.status === 'none');
    box.hidden = !show;
    if (show) box.appendChild(subscriptionCard(mySub, 'self'));
  }

  // ---------- history drawer ----------
  var drawer = document.getElementById('hist');
  var lastFocus = null;
  function closeHistory() {
    if (!drawer || drawer.hidden) return;
    drawer.hidden = true;
    document.documentElement.classList.remove('gp-lock');
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  function openHistory(id, name) {
    if (!drawer) return;
    lastFocus = document.activeElement;
    closeMenu();
    var panel = drawer.querySelector('.gp-drawer__panel');
    panel.replaceChildren();
    var close = el('button', { type: 'button', class: 'gp-drawer__close', 'aria-label': 'Close history', onclick: closeHistory }, icon('close', 18));
    var head = el('div', { class: 'gp-drawer__head' }, avatar(name, 'lg'),
      el('div', { class: 'gp-drawer__who' }, el('p', { class: 'gp-eyebrow', text: 'Playback history' }), el('h2', { id: 'hist-title', text: name || 'Viewer' })));
    var body = el('div', { class: 'gp-drawer__body' });
    panel.append(close, head, body);
    drawer.hidden = false;
    document.documentElement.classList.add('gp-lock');
    close.focus();
    var isSelf = id === 'me';
    var info = isSelf ? api('GET', '/api/me/history?limit=50') : api('GET', '/api/users/' + encodeURIComponent(id));
    info.then(function (r) {
      var u = r.user || {};
      var sub = [];
      if (u.username && u.username !== u.name) sub.push('@' + u.username);
      if (u.email && me.role === 'admin') sub.push(u.email);
      var who = head.querySelector('.gp-drawer__who');
      if (u.name) who.querySelector('h2').textContent = u.name;
      if (sub.length) who.appendChild(el('p', { class: 'gp-drawer__sub', text: sub.join(' · ') }));
      var tags = el('div', { class: 'gp-tags' });
      if (u.admin) tags.appendChild(el('span', { class: 'gp-tag gp-tag--admin', text: 'Administrator' }));
      if (u.owner) tags.appendChild(el('span', { class: 'gp-tag', text: 'Server owner' }));
      if (u.shared && !u.owner) tags.appendChild(el('span', { class: 'gp-tag', text: 'Shared user' }));
      if (u.disabled) tags.appendChild(el('span', { class: 'gp-tag gp-tag--off', text: 'Access off' }));
      if (tags.childNodes.length) who.appendChild(tags);
      var s = statsBlock(r.stats);
      if (s) body.appendChild(s);
      if (!isSelf && me.role === 'admin' && r.billing && (r.billing.status !== 'none' || r.billing.emails.length)) {
        body.appendChild(subscriptionCard(r.billing, 'admin'));
      }
      if (!isSelf && me.role === 'admin' && !u.admin) {
        var access = (u.sections || []).map(function (k) { return SECTION_LABEL[k] || k; });
        body.appendChild(el('div', { class: 'gp-access' },
          el('span', null, el('span', { class: 'gp-label', text: 'Can see' }), ' ' + (u.disabled ? 'Nothing, access is off' : access.length ? access.join(', ') : 'Nothing yet')),
          el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Change access', onclick: function () {
            closeHistory(); if (window.stFocusUser) window.stFocusUser(u.id); openSettingsTab('access');
          } })));
      }
      var first = isSelf ? r.rows : null;
      return pagedHistory(body, function (before) {
        if (first && before == null) { var f = first; first = null; return Promise.resolve(f); }
        var q = '?limit=50' + (before ? '&before=' + before : '');
        return api('GET', (isSelf ? '/api/me/history' : '/api/users/' + encodeURIComponent(id) + '/history') + q).then(function (x) { return x.rows; });
      }, isSelf);
    }).catch(function (e) {
      body.appendChild(el('p', { class: 'st-msg st-msg--error', role: 'alert' }, el('b', { text: 'Error: ' }), e.message));
    });
  }
  if (drawer) {
    drawer.addEventListener('click', function (e) { if (e.target === drawer) closeHistory(); });
    drawer.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { closeHistory(); return; }
      if (e.key !== 'Tab') return;
      var f = Array.prototype.slice.call(drawer.querySelectorAll('button, [href], input, [tabindex]:not([tabindex="-1"])')).filter(function (x) { return !x.disabled && x.offsetParent; });
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    });
  }

  // ---------- people section: Users (admin) or My history (viewer) ----------
  function focusUsers() {
    var card = document.getElementById('people-card');
    if (window.sdGoTab) window.sdGoTab('people');
    if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (picker) setTimeout(function () { picker.focus(); }, 300);
  }
  function renderPeople() {
    var card = document.getElementById('people-card');
    if (!card || !me) return;
    card.replaceChildren();
    if (me.role === 'admin') return renderUsers(card);
    if (!window.dash.can('history')) { card.hidden = true; return; }
    card.hidden = false;
    card.append(el('div', { class: 'sd-sec-head' }, el('h2', { class: 'sd-sec-title', id: 'people-title', text: 'My history' })));
    if (me.kind !== 'plex') return;
    api('GET', '/api/me/history?limit=50').then(function (r) {
      var s = statsBlock(r.stats);
      var wrap = el('div', { class: 'gp-mine' });
      if (s) wrap.appendChild(s);
      card.appendChild(wrap);
      var first = r.rows;
      pagedHistory(wrap, function (before) {
        if (first && before == null) { var f = first; first = null; return Promise.resolve(f); }
        return api('GET', '/api/me/history?limit=50' + (before ? '&before=' + before : '')).then(function (x) { return x.rows; });
      }, true);
    }).catch(function (e) {
      card.appendChild(el('p', { class: 'st-msg st-msg--error', role: 'alert' }, el('b', { text: 'Error: ' }), e.message));
    });
  }
  let picker = null;
  let usersLoadedAt = 0;
  function renderUsers(card) {
    card.hidden = false;
    if (!picker) {
      picker = window.gsUserPicker({ idPrefix: 'people', icon: icon, onPick: function (p) { openHistory(p.key, p.name); } });
      // Opening the search with a list older than a minute fetches a fresh one.
      picker.el.addEventListener('focusin', function () { if (Date.now() - usersLoadedAt > 60000) loadUsers(); });
    }
    var sub = el('span', { class: 'gp-finder__sub', id: 'people-sub' });
    card.replaceChildren(el('div', { class: 'gp-finder', 'aria-labelledby': 'people-title' },
      el('div', { class: 'gp-finder__head' }, el('h2', { class: 'sd-sec-title', id: 'people-title', text: 'Users' }), sub),
      picker.el,
      el('p', { class: 'sd-hint', text: 'Pick someone to open their playback history. The list groups people by the Plex server they are on.' })));
    loadUsers();
  }
  function loadUsers() {
    usersLoadedAt = Date.now();
    api('GET', '/api/users?limit=5000').then(function (r) {
      picker.setData({
        servers: r.servers || [],
        people: (r.rows || []).map(function (u) {
          return { key: u.id, name: u.name || u.username || 'Plex user', username: u.username, email: u.email, servers: u.servers || [],
            plays_30d: u.plays_30d, admin: u.admin, disabled: u.disabled, owner: u.owner, billing: u.billing_status };
        })
      });
      var sub = document.getElementById('people-sub');
      if (sub) sub.textContent = plural(picker.count(), 'person', 'people') + ' on ' + plural(picker.serverCount(), 'server', 'servers');
    }).catch(function (e) {
      var sub = document.getElementById('people-sub');
      if (sub) sub.textContent = 'Could not load people: ' + e.message;
    });
  }
  // Pick up new people now and then (sync runs every 6 hours, new viewers appear as they stream).
  setInterval(function () { if (picker && Date.now() - usersLoadedAt > 5 * 60000 && !document.hidden) loadUsers(); }, 60000);

  // ---------- public API for the page ----------
  window.gsPeople = {
    canOpen: function (r) { return !!(me && me.role === 'admin' && r && r.user_id); },
    openHistory: openHistory,
    nameButton: function (r, cls, tag) {
      if (!window.gsPeople.canOpen(r)) return null;
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'gp-namebtn ' + (cls || '');
      b.textContent = r.user;
      b.title = 'See ' + r.user + '’s playback history';
      b.setAttribute('aria-label', r.user + ', see playback history');
      b.addEventListener('click', function (e) { e.stopPropagation(); openHistory(r.user_id, r.user); });
      b.addEventListener('keydown', function (e) { e.stopPropagation(); });
      return b;
    }
  };

  function start(detail) {
    me = detail;
    applyRole();
    renderUser();
    renderPeople();
    loadMySubscription();
    setInterval(function () { if (!document.hidden) loadMySubscription(); }, 5 * 60000);
  }
  if (window.gsMe) start(window.gsMe);
  else document.addEventListener('gs:me', function (e) { start(e.detail); }, { once: true });
  if (window.dash) window.dash.onData(function () { if (window.dash.loaded && window.dash.loaded()) hideSplash(); });
})();
