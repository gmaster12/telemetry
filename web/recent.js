/* Recently requested (Glenn's pick B, the activity feed): what everyone asked for in the last 7 days, newest first,
   grouped by day, never who asked. One renderer for the viewer home's three layouts (home.js: a door in Doors, a
   section in the Requests hub, a panel in My month) and the full list on #/requests/recent (requests.js).
   Data: GET /api/requests/recent (requests section): {days, total, counts, status, items, more, sources}; each item
   {kind, title, sub, poster, created, status, shape, times, open}. Status is a word plus a shape, never colour alone. */
(function () {
  'use strict';
  var el = window.gsUI.el;
  var KINDS = ['movie', 'show', 'ebook', 'audiobook', 'comic'];
  var WORD = { movie: 'Movie', show: 'TV show', ebook: 'Ebook', audiobook: 'Audiobook', comic: 'Comic' };
  var PLURAL = { movie: 'Movies', show: 'Shows', ebook: 'Ebooks', audiobook: 'Audio', comic: 'Comics' };
  var LONG_PLURAL = { movie: 'Movies', show: 'TV shows', ebook: 'Ebooks', audiobook: 'Audiobooks', comic: 'Comics' };
  var images = 'https://image.tmdb.org/t/p/';
  var cache = null;

  function load(fresh) {
    if (!fresh && cache && cache.until > Date.now()) return cache.p;
    var p = fetch('/api/requests/recent', { credentials: 'same-origin', headers: { Accept: 'application/json' } }).then(function (r) {
      if (!r.ok) { var e = new Error('HTTP ' + r.status); e.status = r.status; throw e; }
      return r.json();
    });
    cache = { p: p, until: Date.now() + 60000 };
    p.catch(function () { cache = null; });
    return p;
  }
  function setImages(base) { if (base) images = base; }

  function dayKey(d) { return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate(); }
  function dayLabel(d) {
    var now = new Date();
    var y = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    if (dayKey(d) === dayKey(now)) return 'Today';
    if (dayKey(d) === dayKey(y)) return 'Yesterday';
    return d.toLocaleDateString(undefined, { weekday: 'long' });
  }
  function dayDate(d) { return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }); }
  function timeOf(d) { return isNaN(d) ? '' : d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }); }

  // Items (already newest first) into days in this browser's time zone. maxDays / maxItems cap what is drawn.
  function groups(items, maxDays, maxItems) {
    var out = [], byKey = {}, n = 0;
    for (var i = 0; i < items.length; i++) {
      if (maxItems && n >= maxItems) break;
      var d = new Date(items[i].created);
      var k = isNaN(d) ? 'earlier' : dayKey(d);
      if (!byKey[k]) {
        if (maxDays && out.length >= maxDays) break;
        byKey[k] = { label: isNaN(d) ? 'Earlier' : dayLabel(d), date: isNaN(d) ? '' : dayDate(d), items: [] };
        out.push(byKey[k]);
      }
      byKey[k].items.push(items[i]);
      n++;
    }
    return out;
  }

  function shape(s) { return el('span', { class: 'rr-shape rr-shape--' + (s || 'square'), 'aria-hidden': 'true' }); }
  function status(x) { return el('span', { class: 'rr-status rr-status--' + (x.shape || 'square') }, shape(x.shape), x.status || 'Searching'); }

  function tile(x) {
    var t = el('span', { class: 'rr-tile rr-tile--' + x.kind, 'aria-hidden': 'true' });
    // TMDB posters for movies and shows; comic, ebook and audiobook covers come through this site
    // (/api/requests/recent/cover/...). The letter stays until the picture has loaded, and if there is none.
    // Not loading="lazy": a lazy image that is not on the page yet never loads, which left every tile a letter.
    var src = !x.poster ? null : /^(https?:|\/api\/)/.test(x.poster) ? x.poster : images + 'w185' + x.poster;
    if (src) {
      var im = new Image();
      im.alt = '';
      im.decoding = 'async';
      im.onload = function () { t.classList.add('has-img'); t.replaceChildren(im); };
      im.src = src;
    }
    t.textContent = (x.title || '?').charAt(0).toUpperCase();
    return t;
  }

  function row(x, opts) {
    opts = opts || {};
    var d = new Date(x.created);
    var meta = [WORD[x.kind] || ''];
    if (opts.sub !== false && x.sub) meta.push(x.sub);
    if (opts.time === 'inline') meta.push(timeOf(d));
    if (x.times > 1) meta.push('asked for ' + x.times + ' times');
    // Unwatched cleanup: on Plex and nobody has started it yet ("9 days left to watch").
    if (x.clock && ['running', 'due', 'last'].indexOf(x.clock.state) >= 0) meta.push(x.clock.word.toLowerCase());
    var title = x.open
      ? el('a', { class: 'rr-title', href: x.open, text: x.title })
      : el('span', { class: 'rr-title', text: x.title });
    return el('li', { class: 'rr-row' },
      tile(x),
      el('span', { class: 'rr-main' }, title, el('span', { class: 'rr-meta', text: meta.join(' · ') })),
      el('span', { class: 'rr-side' }, status(x), opts.time === 'inline' ? null : el('span', { class: 'rr-time', text: timeOf(d) })));
  }

  // The grouped list. opts: days (max day groups), max (max rows), time ('inline' puts it in the meta line),
  // sub (false hides author / year), dates (true adds the date beside the day name).
  function list(items, opts) {
    opts = opts || {};
    var wrap = el('div', { class: 'rr-days' });
    groups(items, opts.days, opts.max).forEach(function (g) {
      wrap.appendChild(el('section', { class: 'rr-day' },
        el('h3', { class: 'rr-dayname' }, g.label, opts.dates && g.date && g.label !== g.date ? el('span', { class: 'rr-daydate', text: g.date }) : null),
        el('ul', { class: 'rr-list' }, g.items.map(function (x) { return row(x, opts); }))));
    });
    return wrap;
  }

  // Count chips per kind (only kinds with something), as plain labels or as filter buttons.
  function counts(data, opts) {
    opts = opts || {};
    var c = data.counts || {};
    var keys = KINDS.filter(function (k) { return c[k]; });
    if (!opts.pick) {
      return el('div', { class: 'rr-counts' }, keys.map(function (k) {
        return el('span', { class: 'rr-count rr-count--' + k, text: PLURAL[k] + ' ' + c[k] });
      }));
    }
    return el('div', { class: 'rr-counts rr-counts--pick', role: 'group', 'aria-label': 'Filter by type' }, ['all'].concat(keys).map(function (k) {
      var on = (opts.active || 'all') === k;
      return el('button', { type: 'button', class: 'rr-pick' + (k === 'all' ? '' : ' rr-pick--' + k), 'aria-pressed': on ? 'true' : 'false',
        onclick: function () { opts.pick(k); } },
        k === 'all' ? 'All' : LONG_PLURAL[k], el('span', { class: 'rr-pick__n', text: String(k === 'all' ? data.total : c[k]) }));
    }));
  }

  // One stacked bar of the week's mix, with a legend (My month).
  function mix(data) {
    var c = data.counts || {};
    var keys = KINDS.filter(function (k) { return c[k]; });
    var words = keys.map(function (k) { return LONG_PLURAL[k] + ' ' + c[k]; }).join(', ');
    return el('div', { class: 'rr-mix' },
      el('div', { class: 'rr-mix__bar', role: 'img', 'aria-label': words || 'Nothing yet' }, keys.map(function (k) {
        return el('i', { class: 'rr-mix__seg rr-tile--' + k, style: 'flex:' + c[k] + ' 1 0' });
      })),
      el('ul', { class: 'rr-mix__legend', 'aria-hidden': 'true' }, keys.map(function (k) {
        return el('li', null, el('i', { class: 'rr-mix__key rr-tile--' + k }), LONG_PLURAL[k] + ' ' + c[k]);
      })));
  }

  // A line when one side could not load, so an empty-looking list is explained.
  function notes(data) {
    var s = data.sources || {}, out = [];
    if (s.seerr === 'down') out.push('Movies and shows could not load right now.');
    if (s.books === 'down') out.push('Comics, ebooks and audiobooks could not load right now.');
    return out.length ? el('p', { class: 'rr-note', role: 'status', text: out.join(' ') }) : null;
  }

  function filter(items, kind) { return kind && kind !== 'all' ? items.filter(function (x) { return x.kind === kind; }) : items; }

  window.gsRecent = { load: load, setImages: setImages, list: list, counts: counts, mix: mix, notes: notes, filter: filter, row: row, groups: groups };
})();
