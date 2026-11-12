/* Greystar Telemetry: Unwatched cleanup (admins only, #/cleanup; Glenn's pick B, the board).
   Requested movies and shows that nobody starts within N days (14) of arriving on Plex are removed with their files
   through Radarr and Sonarr, then cleared in Seerr so they can be requested again. Four columns: Clock running,
   Due in 3 days, Kept (watched or kept by an admin), Deleted (last 30 days). Every card can be kept, given more days,
   removed now or tracked again. Mode: Review only (nothing is removed; the board says what would have gone),
   Auto delete, or Off. Data: GET /api/settings/cleanup (cleanup.py, board()). */
(function () {
  'use strict';

  var root = document.getElementById('streams-app');
  var page = document.getElementById('cleanup-page');
  if (!root || !page) return;
  var el = window.gsUI.el;
  var icon = function (n, s) { return window.sdIcon ? window.sdIcon(n, s) : document.createTextNode(''); };
  var api = function (m, p, d) { return window.gsUI.api(m, p, d); };
  var toast = function (t, tone) { return window.gsUI.toast(t, tone); };
  var D = function () { return window.gsDl; };
  var isMobile = function () { return window.matchMedia('(max-width: 760px), (max-height: 500px) and (pointer: coarse)').matches; };

  var board = null;
  var col = null;           // the column shown on a phone
  var built = false, titleEl = null, headEl = null, bannerEl = null, colsEl = null, pickEl = null, rulesEl = null;
  var loading = false;
  var open = {};            // card id -> 'keep' | 'delete' (an inline form left open across redraws)

  var COLS = [
    { id: 'running', name: 'Clock running', shape: 'ring', empty: 'Nothing is on the clock. New requests show here once they are made.' },
    { id: 'due', name: 'Due in 3 days', shape: 'triangle', empty: 'Nothing runs out in the next 3 days.' },
    { id: 'kept', name: 'Kept', shape: 'circle', empty: 'Titles someone watched, or you kept, show here.' },
    { id: 'deleted', name: 'Deleted', shape: 'square', empty: 'Nothing removed in the last 30 days.' }
  ];

  function bytes(b) { return D() ? D().fmtBytes(b) : String(b); }
  function day(t) { return t ? new Date(t * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ''; }
  function at(t) {
    if (!t) return '';
    var d = new Date(t * 1000);
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }) + ', ' + d.toLocaleTimeString(undefined, d.getMinutes() ? { hour: 'numeric', minute: '2-digit' } : { hour: 'numeric' });
  }
  function hourName(h) { var d = new Date(); d.setHours(h, 0, 0, 0); return d.toLocaleTimeString(undefined, { hour: 'numeric' }); }
  function shape(s) { return el('span', { class: 'dl-shape dl-shape--' + s, 'aria-hidden': 'true' }); }
  function poster(p) {
    if (!p) return null;
    return /^(https?:|\/api\/)/.test(p) ? p : 'https://image.tmdb.org/t/p/w185' + p;
  }

  // ---------- page ----------
  function show(on) {
    var was = root.dataset.page === 'cleanup';
    if (on) root.dataset.page = 'cleanup';
    else if (was) delete root.dataset.page;
    page.hidden = !on;
    if (was !== on && window.sdNavChanged) window.sdNavChanged();
  }
  function here() { return /^#\/?cleanup(\/|\?|$)/.test(location.hash); }
  function route() {
    if (!here()) { if (root.dataset.page === 'cleanup' || !page.hidden) show(false); return; }
    var me = window.dash && window.dash.me && window.dash.me();
    if (me && !(window.dash.isAdmin && window.dash.isAdmin())) { show(false); window.gsUI.replace('#/home'); window.dispatchEvent(new HashChangeEvent('hashchange')); return; }
    build();
    show(true);
    document.title = 'Unwatched cleanup · Greystar Telemetry';
    var y = window.gsUI.scroll.enter();
    if (y != null) window.gsUI.scroll.restore(y); else window.scrollTo({ top: 0 });
    load(true);
  }
  window.addEventListener('hashchange', route);
  document.addEventListener('gs:ready', route);
  if (document.readyState !== 'loading') setTimeout(route, 0); else document.addEventListener('DOMContentLoaded', route);
  window.gsCleanup = { reload: function () { return load(false); } };

  function build() {
    if (built) return;
    built = true;
    titleEl = el('h1', { class: 'rq-title', id: 'cu-title', tabindex: '-1', text: 'Unwatched cleanup' });
    headEl = el('div', { class: 'cu-mode', role: 'group', 'aria-label': 'Mode' });
    bannerEl = el('div', { class: 'cu-banners' });
    rulesEl = el('div', { class: 'cu-rules' });
    pickEl = el('div', { class: 'rq-chips cu-pick', role: 'tablist', 'aria-label': 'Column' });
    colsEl = el('div', { class: 'cu-cols' });
    var refresh = el('button', { type: 'button', class: 'rq-btn rq-btn--sm cu-refresh', onclick: function () { load(true); } }, icon('refresh', 16), 'Check now');
    page.replaceChildren(
      el('header', { class: 'cu-top' },
        el('div', { class: 'rq-titles' }, el('span', { class: 'rq-eyebrow', text: 'Admin' }), titleEl,
          el('p', { class: 'cu-lead', id: 'cu-lead' })),
        el('div', { class: 'cu-topright' }, headEl, refresh)),
      bannerEl, rulesEl, pickEl, colsEl);
    colsEl.replaceChildren(el('div', { class: 'rq-loading', role: 'status' }, el('span', { class: 'rq-spin', 'aria-hidden': 'true' }), 'Checking Plex, Seerr, Radarr and Sonarr'));
  }

  function load(fresh) {
    if (loading) return Promise.resolve();
    loading = true;
    page.classList.add('is-busy');
    return api('GET', '/api/settings/cleanup?fresh=' + (fresh ? 'true' : 'false')).then(function (b) {
      board = b;
      paint();
    }).catch(function (e) {
      if (!board) colsEl.replaceChildren(el('div', { class: 'rq-empty' }, el('b', { text: 'The board could not load' }), el('span', { text: e.message })));
      else toast(e.message, 'error');
    }).then(function () { loading = false; page.classList.remove('is-busy'); });
  }

  // ---------- header: mode, banners, rules ----------
  var MODES = [['review', 'Review only'], ['auto', 'Auto delete'], ['off', 'Off']];
  function setMode(m) {
    if (m === board.settings.mode) return;
    if (m === 'auto' && !window.confirm('Turn on Auto delete? Titles nobody started are removed with their files every night at ' + hourName(board.settings.hour) + '.')) return;
    api('PUT', '/api/settings/cleanup', { mode: m }).then(function (s) {
      board.settings = s;
      toast(m === 'auto' ? 'Auto delete is on.' : m === 'review' ? 'Review only: nothing is removed.' : 'Unwatched cleanup is off. Requests no longer need the tick box.');
      load(false);
    }).catch(function (e) { toast(e.message, 'error'); });
  }
  function paintHead() {
    var s = board.settings;
    headEl.replaceChildren.apply(headEl, MODES.map(function (m) {
      return el('button', { type: 'button', class: 'cu-mode__btn', 'aria-pressed': s.mode === m[0] ? 'true' : 'false', onclick: function () { setMode(m[0]); } },
        shape(m[0] === 'auto' ? 'circle' : m[0] === 'review' ? 'ring' : 'square'), m[1]);
    }));
    document.getElementById('cu-lead').textContent = 'Requested movies and shows that nobody starts within ' + s.days + ' days of arriving on Plex are removed with their files, at '
      + hourName(s.hour) + ' (' + (s.tz || 'Central') + '). One real play by anyone keeps a title for good.';
    var t = board.totals;
    var out = [];
    if (s.mode === 'review') {
      out.push(el('div', { class: 'cu-banner cu-banner--review', role: 'status' }, shape('ring'),
        el('div', null, el('b', { text: 'Review only: nothing is removed.' }),
          el('span', { text: t.wouldHave ? ' ' + t.wouldHave + (t.wouldHave === 1 ? ' title' : ' titles') + ' would have been removed so far (' + bytes(t.wouldHaveBytes) + '). They are marked on the board.'
            : ' When a title runs out, the board marks it "Would be removed" instead.' })),
        el('button', { type: 'button', class: 'rq-btn rq-btn--sm rq-btn--ink', onclick: function () { setMode('auto'); }, text: 'Turn on Auto delete' })));
    } else if (s.mode === 'off') {
      out.push(el('div', { class: 'cu-banner', role: 'status' }, shape('square'),
        el('div', null, el('b', { text: 'Off.' }), el('span', { text: ' Nothing is tracked or removed, and requests do not need the tick box. The board keeps what it had.' }))));
    }
    if (!(s.radarr && s.sonarr) && s.mode !== 'off') {
      out.push(el('div', { class: 'cu-banner cu-banner--warn', role: 'status' }, shape('triangle'),
        el('div', null, el('b', { text: 'Connect ' + (!s.radarr && !s.sonarr ? 'Radarr and Sonarr' : !s.radarr ? 'Radarr' : 'Sonarr') + '.' }),
          el('span', { text: ' Titles are removed through them (with their files, so they are not downloaded again). Until then nothing can be removed.' })),
        el('button', { type: 'button', class: 'rq-btn rq-btn--sm', text: 'Open Settings', onclick: function () {
          if (window.stOpenTab) window.stOpenTab('arr');
          if (window.sdOpenSettings) window.sdOpenSettings(true);
        } })));
    }
    if (s.error) out.push(el('div', { class: 'cu-banner cu-banner--danger', role: 'alert' }, shape('square'), el('div', null, el('b', { text: 'The last check failed. ' }), el('span', { text: s.error }))));
    bannerEl.replaceChildren.apply(bannerEl, out);
    paintRules();
  }
  function paintRules() {
    var s = board.settings;
    var days = el('select', { class: 'st-select', id: 'cu-days', 'aria-label': 'Days to start watching' },
      [7, 10, 14, 21, 30, 45, 60].concat([7, 10, 14, 21, 30, 45, 60].indexOf(s.days) < 0 ? [s.days] : []).sort(function (a, b) { return a - b; })
        .map(function (n) { return el('option', { value: String(n), text: n + ' days', selected: n === s.days }); }));
    var hour = el('select', { class: 'st-select', id: 'cu-hour', 'aria-label': 'Nightly run' },
      Array.from({ length: 24 }, function (_, h) { return el('option', { value: String(h), text: hourName(h), selected: h === s.hour }); }));
    var save = function (body) {
      api('PUT', '/api/settings/cleanup', body).then(function (x) { board.settings = x; toast('Saved.'); load(false); }).catch(function (e) { toast(e.message, 'error'); });
    };
    days.addEventListener('change', function () { save({ days: Number(days.value) }); });
    hour.addEventListener('change', function () { save({ hour: Number(hour.value) }); });
    var t = board.totals;
    rulesEl.replaceChildren(
      el('label', { class: 'cu-rule', for: 'cu-days' }, el('span', { text: 'Time to start watching' }), days),
      el('label', { class: 'cu-rule', for: 'cu-hour' }, el('span', { text: 'Nightly run' }), hour),
      el('div', { class: 'cu-stat' }, el('b', { text: bytes(t.freedBytes) }), el('span', { text: 'freed in 30 days' })),
      el('div', { class: 'cu-stat' }, el('b', { text: bytes(t.dueBytes) }), el('span', { text: 'due in 3 days' })),
      el('div', { class: 'cu-stat' }, el('b', { text: s.lastRun ? new Date(s.lastRun + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : 'Not yet' }), el('span', { text: 'last nightly run' })));
  }

  // ---------- the board ----------
  function paint() {
    paintHead();
    var cols = board.columns;
    if (!col || !cols[col]) col = cols.due.length ? 'due' : 'running';
    pickEl.replaceChildren.apply(pickEl, COLS.map(function (c) {
      return el('button', { type: 'button', class: 'rq-chip', role: 'tab', 'aria-selected': col === c.id ? 'true' : 'false', 'aria-pressed': col === c.id ? 'true' : 'false',
        onclick: function () { col = c.id; paint(); } }, shape(c.shape), c.name, el('em', { text: ' ' + cols[c.id].length }));
    }));
    colsEl.replaceChildren.apply(colsEl, COLS.map(function (c) {
      var list = cols[c.id];
      var sub = c.id === 'due' && list.length ? bytes(board.totals.dueBytes) + ' to free'
        : c.id === 'deleted' && list.length ? bytes(board.totals.freedBytes) + ' freed'
        : c.id === 'running' ? list.filter(function (x) { return x.state === 'waiting'; }).length + ' not on Plex yet' : '';
      return el('section', { class: 'cu-col cu-col--' + c.id, 'data-col': c.id, 'aria-label': c.name, hidden: isMobile() && col !== c.id ? true : null },
        el('header', { class: 'cu-col__head' },
          el('h2', { class: 'cu-col__name' }, shape(c.shape), c.name, el('span', { class: 'cu-col__n', text: String(list.length) })),
          sub ? el('span', { class: 'cu-col__sub', text: sub }) : null),
        list.length ? el('ul', { class: 'cu-list' }, list.map(card)) : el('p', { class: 'cu-empty', text: c.empty }));
    }));
  }
  window.addEventListener('resize', function () { if (board && here()) Array.prototype.forEach.call(colsEl.querySelectorAll('.cu-col'), function (s) { s.hidden = isMobile() && s.getAttribute('data-col') !== col; }); });

  function act(path, body, done) {
    return api('POST', '/api/settings/cleanup/' + path, body || {}).then(function (r) { if (done) toast(done); return load(false).then(function () { return r; }); });
  }

  function card(x) {
    var segs = null, line = null;
    var days = board.settings.days;
    if (x.state === 'running' || x.state === 'due') {
      var left = Math.max(0, Math.min(days, x.daysLeft == null ? days : x.daysLeft));
      segs = el('span', { class: 'dl-segs cu-segs', 'aria-hidden': 'true' });
      for (var i = 0; i < days; i++) segs.appendChild(el('i', { class: i < days - left ? 'is-used' : '' }));
      var last = x.deleteAt && x.deleteAt * 1000 - Date.now() <= 864e5;
      line = el('p', { class: 'cu-when' }, el('b', { text: last ? 'Last day' : (x.daysLeft === 1 ? '1 day left' : x.daysLeft + ' days left') }),
        el('span', { text: (board.settings.mode === 'auto' ? ' · removed ' : ' · runs out ') + at(x.deleteAt) }));
    } else if (x.state === 'waiting') {
      line = el('p', { class: 'cu-when' }, el('span', { text: 'Not on Plex yet. The clock starts when it arrives.' }));
    } else if (x.state === 'watched') {
      line = el('p', { class: 'cu-when' }, el('b', { text: 'Watched ' + day(x.watchedAt) }), el('span', { text: x.plays ? ' · ' + x.plays + (x.plays === 1 ? ' play' : ' plays') : '' }));
    } else if (x.state === 'kept') {
      line = el('p', { class: 'cu-when' }, el('b', { text: 'Kept' + (x.keptBy ? ' by ' + x.keptBy : '') }), el('span', { text: (x.keptAt ? ' · ' + day(x.keptAt) : '') + (x.keptNote ? ': ' + x.keptNote : '') }));
    } else if (x.state === 'deleted') {
      line = el('p', { class: 'cu-when' }, el('b', { text: 'Removed ' + day(x.deletedAt) }), el('span', { text: (x.deletedBy ? ' by ' + x.deletedBy : '') + (x.freed ? ' · freed ' + bytes(x.freed) : '') }));
    }
    var kindLine = [x.kind === 'tv' ? 'Series' : 'Movie', x.is4k ? '4K' : null, x.kind === 'tv' && (x.seasons || []).length ? (x.seasons.length === 1 ? 'Season ' : 'Seasons ') + x.seasons.join(', ') : null,
      x.size ? bytes(x.size) : null].filter(Boolean).join(' · ');
    var p = poster(x.poster);
    var art = p ? el('img', { src: p, alt: '', loading: 'lazy', decoding: 'async' }) : el('span', { class: 'cu-card__letter', text: (x.title || '?').charAt(0).toUpperCase() });
    var tags = [];
    if (x.wouldDeleteAt && (x.state === 'running' || x.state === 'due')) tags.push(el('span', { class: 'dl-pill dl-pill--amber' }, shape('triangle'), 'Would be removed ' + day(x.wouldDeleteAt)));
    if (x.error) tags.push(el('p', { class: 'cu-error', role: 'alert' }, shape('square'), x.error));
    return el('li', { class: 'cu-card cu-card--' + x.state, 'data-id': String(x.id) },
      el('a', { class: 'cu-card__art', href: x.open, tabindex: '-1', 'aria-hidden': 'true' }, art),
      el('div', { class: 'cu-card__main' },
        el('a', { class: 'cu-card__title', href: x.open }, x.title || 'Unknown title', x.year ? el('span', { class: 'cu-card__year', text: ' ' + x.year }) : null),
        el('span', { class: 'cu-card__meta', text: kindLine }),
        el('span', { class: 'cu-card__meta', text: 'Requested by ' + (x.requestedBy || 'someone') + ' · ' + day(x.requestedAt) }),
        segs, line, tags.length ? el('div', { class: 'cu-card__tags' }, tags) : null,
        actions(x)));
  }

  function actions(x) {
    var box = el('div', { class: 'cu-actions' });
    var title = x.title || 'this title';
    if (open[x.id] === 'delete') {
      var yes = el('button', { type: 'button', class: 'rq-btn rq-btn--sm cu-btn--danger' }, icon('trash', 16), 'Remove');
      var no = el('button', { type: 'button', class: 'rq-btn rq-btn--sm', text: 'Cancel', onclick: function () { delete open[x.id]; paint(); } });
      yes.addEventListener('click', function () {
        yes.disabled = no.disabled = true; yes.replaceChildren('Removing');
        act(x.id + '/delete', null).then(function (r) {
          delete open[x.id];
          toast(title + ' was removed' + (r && r.freed ? ', ' + bytes(r.freed) + ' freed.' : '.'));
          paint();
        }).catch(function (e) { delete open[x.id]; toast(e.message, 'error'); load(false); });
      });
      box.append(el('p', { class: 'cu-confirm', text: 'Remove ' + title + (x.kind === 'tv' && (x.seasons || []).length ? ' (the requested seasons)' : '') + ' and its files from Plex now?' }), yes, no);
      setTimeout(function () { no.focus(); }, 0);
      return box;
    }
    if (open[x.id] === 'keep') {
      var note = el('input', { type: 'text', class: 'st-input cu-note', maxlength: '200', placeholder: 'Why (optional)', 'aria-label': 'Why you are keeping it (optional)' });
      var save = el('button', { type: 'button', class: 'rq-btn rq-btn--sm rq-btn--accent' }, icon('check', 16), 'Keep it');
      var cancel = el('button', { type: 'button', class: 'rq-btn rq-btn--sm', text: 'Cancel', onclick: function () { delete open[x.id]; paint(); } });
      var go = function () {
        save.disabled = true;
        act(x.id + '/keep', { note: note.value.trim() || null }, title + ' is kept. It is no longer on the clock.').then(function () { delete open[x.id]; paint(); })
          .catch(function (e) { save.disabled = false; toast(e.message, 'error'); });
      };
      save.addEventListener('click', go);
      note.addEventListener('keydown', function (e) { if (e.key === 'Enter') go(); if (e.key === 'Escape') { delete open[x.id]; paint(); } });
      box.append(note, save, cancel);
      setTimeout(function () { note.focus(); }, 0);
      return box;
    }
    var b = function (label, ic, fn, cls) { return el('button', { type: 'button', class: 'rq-btn rq-btn--sm' + (cls ? ' ' + cls : ''), onclick: fn }, ic ? icon(ic, 16) : null, label); };
    if (['running', 'due', 'waiting'].indexOf(x.state) >= 0) {
      box.appendChild(b('Keep', 'check', function () { open[x.id] = 'keep'; paint(); }));
      if (x.state !== 'waiting') box.appendChild(b('+7 days', 'clock', function (e) {
        e.currentTarget.disabled = true;
        act(x.id + '/extend', { days: 7 }, title + ' has 7 more days.').catch(function (er) { toast(er.message, 'error'); load(false); });
      }));
      box.appendChild(b('Delete now', 'trash', function () { open[x.id] = 'delete'; paint(); }, 'cu-btn--quietdanger'));
    } else if (x.state === 'kept' || x.state === 'watched') {
      box.appendChild(b('Track again', 'refresh', function (e) {
        e.currentTarget.disabled = true;
        act(x.id + '/track', null, title + ' is back on the clock with ' + board.settings.days + ' days.').catch(function (er) { toast(er.message, 'error'); load(false); });
      }));
      box.appendChild(b('Delete now', 'trash', function () { open[x.id] = 'delete'; paint(); }, 'cu-btn--quietdanger'));
    }
    return box.childNodes.length ? box : null;
  }
})();
