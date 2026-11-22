/* Greystar Telemetry: Activity log (admins only, #/activity; Glenn's pick, Nov 22 2026: "Sign-in sessions" with the
   daily activity card from "People activity").
   Each card is one visit: a sign-in (or coming back after 30 quiet minutes while still signed in) and everything the
   person did until they signed out or went quiet. Search narrows it to one person. The daily card counts every day
   since the site went live. Data: GET /api/settings/activity, /summary, /people (app/activity.py; admin-only).

   This script also runs for everyone else: it tells the server which page was opened (POST /api/activity/page),
   because the part of the address after # never reaches the server. Nothing is shown to viewers. */
(function () {
  'use strict';

  if (!window.gsUI) return;
  var el = window.gsUI.el;
  var api = function (m, p, d) { return window.gsUI.api(m, p, d); };

  // ---------- which page was opened (everyone) ----------
  var beaconTimer = null, lastPath = '';
  function beacon() {
    clearTimeout(beaconTimer);
    // Wait for the page to settle (redirects, titles set after a fetch) before saying where we are.
    beaconTimer = setTimeout(function () {
      if (document.visibilityState === 'hidden') return;
      var path = (location.hash && location.hash.length > 2 ? location.hash : '#/home').slice(0, 200);
      if (path === lastPath) return;
      lastPath = path;
      api('POST', '/api/activity/page', { path: path, title: (document.title || '').slice(0, 200) }).catch(function () {});
    }, 1500);
  }
  window.addEventListener('hashchange', beacon);
  document.addEventListener('gs:ready', beacon);
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') { lastPath = ''; beacon(); } });
  if (document.readyState !== 'loading') beacon(); else document.addEventListener('DOMContentLoaded', beacon);

  // ---------- the admin page ----------
  var root = document.getElementById('streams-app');
  var page = document.getElementById('activity-page');
  if (!root || !page) return;
  var icon = function (n, s) { return window.sdIcon ? window.sdIcon(n, s) : document.createTextNode(''); };
  var toast = function (t, tone) { return window.gsUI.toast(t, tone); };
  var tz = -new Date().getTimezoneOffset();

  var state = { q: '', who: null, whoName: '', before: null, day: null, visits: [], next: null, summary: null, open: {} };
  var built = false, listEl, moreEl, barsEl, axisEl, coverEl, statsEl, flagsEl, chipEl, sugEl, inputEl, dayTitleEl, dayNoteEl;
  var loadSeq = 0;

  var TYPE = { login: 'Signed in', logout: 'Signed out', page: 'Page', search: 'Search', request: 'Request', admin: 'Admin', denied: 'Denied' };

  function clock(t) { return new Date(t * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }); }
  function dayName(t) { return new Date(t * 1000).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' }); }
  function shortDay(t) { return new Date(t * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); }
  function dayKey(t) { var d = new Date(t * 1000); return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }
  function fromKey(k) { var p = k.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }
  var TINTS = ['rose', 'sand', 'lilac', 'sky'];
  function avatar(name, big) {
    var s = String(name || '?'), h = 0;
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return el('span', { class: 'al-av al-av--' + TINTS[h % 4] + (big ? ' al-av--big' : ''), 'aria-hidden': 'true', text: s.replace(/[^A-Za-z0-9]/g, '').slice(0, 2) || '?' });
  }
  function tag(text, cls) { return el('span', { class: 'al-tag ' + (cls || ''), text: text }); }

  function show(on) {
    var was = root.dataset.page === 'activity';
    if (on) root.dataset.page = 'activity';
    else if (was) delete root.dataset.page;
    page.hidden = !on;
    if (was !== on && window.sdNavChanged) window.sdNavChanged();
  }
  function here() { return /^#\/?activity(\/|\?|$)/.test(location.hash); }
  function route() {
    if (!here()) { if (root.dataset.page === 'activity' || !page.hidden) show(false); return; }
    var me = window.dash && window.dash.me && window.dash.me();
    if (me && !(window.dash.isAdmin && window.dash.isAdmin())) { show(false); window.gsUI.replace('#/home'); window.dispatchEvent(new HashChangeEvent('hashchange')); return; }
    build();
    show(true);
    document.title = 'Activity log · Greystar Telemetry';
    var y = window.gsUI.scroll.enter();
    if (y != null) window.gsUI.scroll.restore(y); else window.scrollTo({ top: 0 });
    reload();
  }
  window.addEventListener('hashchange', route);
  document.addEventListener('gs:ready', route);
  if (document.readyState !== 'loading') setTimeout(route, 0); else document.addEventListener('DOMContentLoaded', route);

  function build() {
    if (built) return;
    built = true;
    inputEl = el('input', { type: 'search', class: 'al-search__input', placeholder: 'Search a person', 'aria-label': 'Search a person', autocomplete: 'off', maxlength: '80' });
    sugEl = el('div', { class: 'al-sug', role: 'listbox', hidden: true });
    chipEl = el('div', { class: 'al-chipwrap' });
    statsEl = el('div', { class: 'al-stats' });
    flagsEl = el('div', { class: 'al-flags' });
    barsEl = el('div', { class: 'al-bars', role: 'group', 'aria-label': 'Activity per day' });
    axisEl = el('div', { class: 'al-axis' });
    coverEl = el('p', { class: 'al-cover' });
    dayTitleEl = el('span', { class: 'al-eyebrow', text: 'Daily activity since the site went live' });
    dayNoteEl = el('span', { class: 'al-daynote' });
    listEl = el('div', { class: 'al-list', 'aria-live': 'polite' });
    moreEl = el('button', { type: 'button', class: 'rq-btn al-more', hidden: true, onclick: function () { load(true); } }, 'Show older');

    var t;
    inputEl.addEventListener('input', function () {
      clearTimeout(t);
      t = setTimeout(function () {
        state.q = inputEl.value.trim();
        state.who = null; state.whoName = '';
        suggest(state.q);
        reload();
      }, 280);
    });
    inputEl.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { sugEl.hidden = true; }
      if (e.key === 'ArrowDown') { var f = sugEl.querySelector('button'); if (f) { e.preventDefault(); f.focus(); } }
    });
    document.addEventListener('click', function (e) { if (!e.target.closest || !e.target.closest('.al-search')) sugEl.hidden = true; });

    page.replaceChildren(
      el('header', { class: 'al-top' },
        el('div', { class: 'rq-titles' }, el('span', { class: 'rq-eyebrow', text: 'Admin' }),
          el('h1', { class: 'rq-title', id: 'al-title', tabindex: '-1', text: 'Activity log' }),
          el('p', { class: 'al-lead', text: 'Every sign-in and what people did while signed in, newest first. Times are in your time zone.' })),
        el('span', { class: 'al-lock' }, icon('users', 15), 'Admins only')),
      el('div', { class: 'al-grid' },
        el('aside', { class: 'al-side' },
          el('div', { class: 'al-search' }, el('span', { class: 'al-search__icon', 'aria-hidden': 'true' }, icon('search', 18)), inputEl, sugEl),
          chipEl, statsEl, flagsEl),
        el('section', { class: 'al-main' },
          el('div', { class: 'al-daily' }, el('div', { class: 'al-daily__head' }, dayTitleEl, dayNoteEl), barsEl, axisEl, coverEl),
          listEl, moreEl)));
    listEl.replaceChildren(el('div', { class: 'rq-loading', role: 'status' }, el('span', { class: 'rq-spin', 'aria-hidden': 'true' }), 'Loading activity'));
  }

  function params() {
    var p = [];
    if (state.who) p.push('who=' + encodeURIComponent(state.who));
    else if (state.q) p.push('q=' + encodeURIComponent(state.q));
    return p;
  }

  function reload() {
    state.before = state.day ? Math.floor(fromKey(state.day).getTime() / 1000) + 86400 : null;
    state.visits = []; state.open = {};
    var seq = ++loadSeq;
    api('GET', '/api/settings/activity/summary?' + params().concat(['tz=' + tz]).join('&')).then(function (s) {
      if (seq !== loadSeq) return;
      state.summary = s;
      paintSide(); paintDaily();
    }).catch(function (e) { toast(e.message, 'error'); });
    load(false, seq);
  }

  function load(more, seq) {
    seq = seq || loadSeq;
    var q = params();
    var before = more ? state.next : state.before;
    if (before) q.push('before=' + before);
    moreEl.disabled = true;
    return api('GET', '/api/settings/activity?' + q.join('&')).then(function (d) {
      if (seq !== loadSeq) return;
      state.visits = more ? state.visits.concat(d.visits) : d.visits;
      state.next = d.next;
      paintList();
    }).catch(function (e) {
      if (!state.visits.length) listEl.replaceChildren(el('div', { class: 'rq-empty' }, el('b', { text: 'The activity log could not load' }), el('span', { text: e.message })));
      else toast(e.message, 'error');
    }).then(function () { moreEl.disabled = false; });
  }

  function suggest(q) {
    if (q.length < 2) { sugEl.hidden = true; return; }
    api('GET', '/api/settings/activity/people?q=' + encodeURIComponent(q)).then(function (d) {
      if (inputEl.value.trim() !== q) return;
      var ppl = d.people || [];
      sugEl.replaceChildren.apply(sugEl, ppl.map(function (p) {
        return el('button', { type: 'button', class: 'al-sug__item', role: 'option', onclick: function () { pick(p); },
          onkeydown: function (e) {
            if (e.key === 'ArrowDown' && e.currentTarget.nextSibling) { e.preventDefault(); e.currentTarget.nextSibling.focus(); }
            if (e.key === 'ArrowUp') { e.preventDefault(); (e.currentTarget.previousSibling || inputEl).focus(); }
          } },
          avatar(p.name), el('span', { class: 'al-sug__name' }, el('b', { text: p.name || p.id }),
            el('span', { text: plural(p.signins || 0, 'sign-in', 'sign-ins') + ' · last ' + shortDay(p.last) })));
      }));
      sugEl.hidden = !ppl.length;
    }).catch(function () { sugEl.hidden = true; });
  }
  function pick(p) {
    state.who = p.kind + ':' + p.id; state.whoName = p.name || p.id; state.q = '';
    inputEl.value = ''; sugEl.hidden = true;
    reload();
  }
  function clearWho() { state.who = null; state.whoName = ''; state.q = ''; inputEl.value = ''; reload(); inputEl.focus(); }

  // ---------- side: who, numbers, worth a look ----------
  function paintSide() {
    var s = state.summary, t = s.totals;
    chipEl.replaceChildren();
    if (state.who || state.q) {
      chipEl.appendChild(el('div', { class: 'al-who' }, state.who ? avatar(state.whoName) : null,
        el('span', null, el('span', { class: 'al-who__label', text: state.who ? 'Only' : 'Names with' }),
          el('b', { text: state.who ? state.whoName : '“' + state.q + '”' })),
        el('button', { type: 'button', class: 'al-who__x', 'aria-label': 'Show everyone', onclick: clearWho }, icon('close', 16))));
    }
    var since = s.coverage && s.coverage.signins ? ' since ' + shortDay(s.coverage.signins) : '';
    statsEl.replaceChildren(
      el('div', { class: 'al-stat' }, el('div', { class: 'al-stat__n' }, String(t.signins), el('small', { text: t.signins === 1 ? ' sign-in' : ' sign-ins' })),
        el('p', { text: (state.who ? '' : plural(t.people, 'person', 'people') + ' · ') + since.trim() })),
      el('div', { class: 'al-stat' }, el('div', { class: 'al-stat__n' }, String(t.actions), el('small', { text: t.actions === 1 ? ' action' : ' actions' })),
        el('p', { text: 'pages, searches, requests and changes' })));
    var items = (s.flags || []).map(function (f) {
      return el('li', null, el('button', { type: 'button', class: 'al-flag', onclick: function () { pick({ kind: f.who.kind, id: f.who.id, name: f.who.name }); } },
        el('b', { text: f.who.name || f.who.id }), ' signed in from a new device (' + f.device + ') · ' + shortDay(f.at)));
    });
    if (s.denied30) items.push(el('li', null, el('b', { text: plural(s.denied30, 'sign-in', 'sign-ins') + ' denied' }), ' in the last 30 days. They show in the list as Sign-in denied.'));
    flagsEl.replaceChildren(el('h2', { class: 'al-flags__title', text: 'Worth a look' }),
      items.length ? el('ul', null, items) : el('p', { class: 'al-flags__none', text: 'Nothing unusual' + (state.who ? ' for this person.' : ' in the last two weeks.') }));
  }

  // ---------- daily activity card ----------
  function paintDaily() {
    var s = state.summary;
    var days = s.days.slice(-92);
    var max = Math.max.apply(null, [1].concat(days.map(function (d) { return d.events; })));
    var gapBefore = s.coverage && s.coverage.signins ? dayKey(s.coverage.signins) : null;
    dayTitleEl.textContent = state.who ? 'Daily activity · ' + state.whoName : 'Daily activity since the site went live';
    barsEl.style.setProperty('--al-n', days.length);
    barsEl.replaceChildren.apply(barsEl, days.map(function (d) {
      var gap = gapBefore && d.day < gapBefore;
      var on = state.day === d.day;
      var when = fromKey(d.day).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
      var label = when + ': ' + (gap ? 'before sign-in records' : plural(d.events, 'event', 'events') + ', ' + plural(d.signins, 'sign-in', 'sign-ins'));
      return el('button', { type: 'button', class: 'al-bar' + (gap ? ' al-bar--gap' : '') + (on ? ' is-on' : '') + (d.events ? '' : ' is-zero'),
        title: label, 'aria-label': label, 'aria-pressed': on ? 'true' : 'false',
        onclick: function () { state.day = on ? null : d.day; reload(); } },
        el('i', { style: 'height:' + (gap ? 100 : Math.max(3, Math.round(d.events / max * 100))) + '%' }));
    }));
    var first = days[0], last = days[days.length - 1];
    var mid = gapBefore && days.some(function (d) { return d.day === gapBefore; }) ? days.findIndex(function (d) { return d.day === gapBefore; }) : -1;
    axisEl.replaceChildren(el('span', { text: first ? fromKey(first.day).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '' }),
      mid > 0 ? el('span', { class: 'al-axis__mid', style: 'left:' + (mid / days.length * 100) + '%', text: shortDay(s.coverage.signins) + ' · sign-in records start' }) : null,
      el('span', { text: last ? 'Today' : '' }));
    dayNoteEl.replaceChildren();
    if (state.day) {
      dayNoteEl.append('From ' + fromKey(state.day).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }) + ' back · ',
        el('button', { type: 'button', class: 'al-link', text: 'Back to newest', onclick: function () { state.day = null; reload(); } }));
    } else dayNoteEl.textContent = 'Tap a day to start the list there';
    var c = s.coverage || {};
    var bits = [];
    if (c.launch) bits.push('Site live ' + shortDay(c.launch) + '.');
    if (c.signins) bits.push('Sign-ins from ' + shortDay(c.signins) + (c.launch && dayKey(c.signins) > dayKey(c.launch) ? ' (earlier ones expired before this log existed).' : '.'));
    if (c.requests) bits.push('Requests from ' + shortDay(c.requests) + '.');
    if (c.pages) bits.push('Pages, searches and changes from ' + shortDay(c.pages) + '.');
    coverEl.textContent = bits.join(' ');
  }

  // ---------- the list of visits ----------
  function paintList() {
    if (!state.visits.length) {
      listEl.replaceChildren(el('div', { class: 'rq-empty' }, el('b', { text: state.who || state.q ? 'No activity for this search' : 'No activity yet' }),
        el('span', { text: state.day ? 'Nothing on or before this day.' : 'Sign-ins and what people do show here as they happen.' })));
      moreEl.hidden = true;
      return;
    }
    var out = [], cur = '';
    state.visits.forEach(function (v) {
      var k = dayKey(v.start);
      if (k !== cur) {
        cur = k;
        var n = state.visits.filter(function (x) { return dayKey(x.start) === k; }).length;
        out.push(el('h2', { class: 'al-day' }, dayName(v.start), el('span', { text: plural(n, 'visit', 'visits') })));
      }
      out.push(card(v));
    });
    listEl.replaceChildren.apply(listEl, out);
    moreEl.hidden = !state.next;
  }

  function endWords(v) {
    if (v.ended === 'denied') return el('span', { class: 'al-end al-end--denied', text: 'Not let in' });
    if (v.ended === 'active') return el('span', { class: 'al-end al-end--live' }, el('i', { 'aria-hidden': 'true' }), 'Still here');
    if (v.ended === 'logout') return el('span', { class: 'al-end', text: 'Signed out ' + clock(v.end) });
    return el('span', { class: 'al-end', text: 'Last seen ' + clock(v.end) });
  }

  function step(e) {
    return el('li', { class: 'al-step al-step--' + e.type },
      el('span', { class: 'al-step__dot', 'aria-hidden': 'true' }),
      el('span', { class: 'al-step__time', text: clock(e.at) }),
      el('span', { class: 'al-step__label', text: e.label }),
      el('span', { class: 'al-step__detail', text: e.detail || '' }),
      el('span', { class: 'al-sr', text: ' (' + (TYPE[e.type] || e.type) + ')' }));
  }

  function card(v) {
    var how = v.how === 'login' ? null : v.how === 'denied' ? 'Sign-in denied' : 'Came back, already signed in';
    var meta = [clock(v.start)];
    if (v.device) meta.push(v.device);
    var head = el('div', { class: 'al-card__head' },
      avatar(v.who.name, true),
      el('div', { class: 'al-card__who' },
        el('div', { class: 'al-card__name' },
          el('button', { type: 'button', class: 'al-name', title: 'Show only ' + v.who.name, onclick: function () { pick({ kind: v.who.kind, id: v.who.id, name: v.who.name }); }, text: v.who.name || v.who.id }),
          v.who.admin ? tag('Admin', 'al-tag--admin') : null,
          v.newDevice ? tag('New device', 'al-tag--new') : null,
          v.recovered ? tag('From the sign-in record', 'al-tag--old') : null),
        el('div', { class: 'al-card__meta' }, meta.join(' · '), v.ip ? el('span', { class: 'al-ip', text: ' · ' + v.ip }) : null,
          how ? el('span', { text: ' · ' + how }) : null)),
      el('div', { class: 'al-card__dur' }, v.how === 'denied' ? null : el('div', { class: 'al-card__mins' }, String(v.minutes), el('small', { text: ' min' })), endWords(v)));
    var evs = v.events;
    var open = !!state.open[v.id];
    var SHOW = 6;
    var shown = open ? evs : evs.slice(0, SHOW);
    var list = el('ol', { class: 'al-steps' + (open ? ' is-open' : '') }, shown.map(step));
    var more = evs.length > SHOW ? el('button', { type: 'button', class: 'al-link al-card__more', 'aria-expanded': open ? 'true' : 'false',
      onclick: function () { state.open[v.id] = !open; paintList(); },
      text: open ? 'Show less' : 'Show all ' + evs.length + ' (+' + (evs.length - SHOW) + ' more)' }) : null;
    return el('article', { class: 'al-card' + (v.how === 'denied' ? ' al-card--denied' : '') }, head, list, more);
  }
})();
