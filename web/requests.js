/* Greystar Telemetry: Requests. Seerr's screens inside the dashboard.
   The server talks to Seerr as the signed-in person (their own Seerr permissions, quotas and
   auto-approve), so nobody needs to sign in to Seerr. Pages live under #/requests/... */
(function () {
  'use strict';

  var root = document.getElementById('streams-app');
  var page = document.getElementById('requests-page');
  if (!root || !page) return;
  var HEADERS = { 'Content-Type': 'application/json', 'X-Requested-With': 'greystar' };
  var me = null;          // GET /api/requests/me
  var meAt = 0;
  var home = null;        // Discover rows
  var cache = {};         // url -> { at, data }
  var built = false;
  var body = null;
  var searchInput = null;
  var lastDashHash = '';
  var routeToken = 0;

  // ---------- helpers ----------
  function el(tag, props) {
    var e = document.createElement(tag);
    props = props || {};
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v == null || v === false) return;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k === 'html') throw new Error('no html');
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), v);
      else if (k === 'value') e.value = v;
      else if (k === 'checked') e.checked = !!v;
      else if (k === 'style') e.setAttribute('style', v);
      else e.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) add(e, arguments[i]);
    return e;
  }
  function add(e, kid) {
    if (kid == null || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(function (k) { add(e, k); }); return; }
    e.appendChild(typeof kid === 'string' || typeof kid === 'number' ? document.createTextNode(String(kid)) : kid);
  }
  var icon = function (n, s) { return window.sdIcon ? window.sdIcon(n, s) : document.createTextNode(''); };
  function badge(tone, text) {
    if (window.sdBadge) return window.sdBadge(tone, text);
    return el('span', { class: 'gs-badge gs-badge--' + tone, text: text });
  }
  function api(method, path, data) {
    return fetch(path, { method: method, headers: HEADERS, credentials: 'same-origin', body: data ? JSON.stringify(data) : undefined })
      .then(function (r) {
        if (r.status === 401) { window.location.replace('/login.html'); throw new Error('Signed out.'); }
        return r.json().catch(function () { return null; }).then(function (j) {
          if (!r.ok) {
            var d = j && j.detail;
            if (Array.isArray(d)) d = d.map(function (x) { return x.msg; }).join('; ');
            var err = new Error(d || ('HTTP ' + r.status));
            err.status = r.status;
            throw err;
          }
          return j;
        });
      });
  }
  function get(path, ttl) {
    var c = cache[path];
    if (c && Date.now() - c.at < (ttl || 60000)) return Promise.resolve(c.data);
    return api('GET', path).then(function (d) { cache[path] = { at: Date.now(), data: d }; return d; });
  }
  function forget() { cache = {}; meAt = 0; }
  function img(path, size) {
    if (!path) return null;
    if (/^(https?:|data:)/.test(path)) return path;
    return ((me && me.images) || 'https://image.tmdb.org/t/p/') + size + path;
  }
  function qs(o) {
    return Object.keys(o).filter(function (k) { return o[k] != null && o[k] !== ''; })
      .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&');
  }
  function fmtDate(iso, opts) {
    if (!iso) return '';
    var d = new Date(iso.length === 10 ? iso + 'T12:00:00' : iso);
    if (isNaN(d)) return '';
    return d.toLocaleDateString(undefined, opts || { month: 'short', day: 'numeric', year: 'numeric' });
  }
  function plural(n, a, b) { return n + ' ' + (n === 1 ? a : b); }
  var KIND_NAME = { movie: 'Movie', tv: 'Series' };

  // What a title's status looks like: always a word plus a shape.
  function mediaBadge(status, is4k) {
    var x = {
      available: ['success', 'Available'], partial: ['info', 'Partly available'], processing: ['info', 'Processing'],
      pending: ['warning', 'Requested'], blocklisted: ['danger', 'Blocked'], deleted: ['neutral', 'Removed']
    }[status];
    return x ? badge(x[0], (is4k ? '4K ' : '') + x[1]) : null;
  }
  function requestBadge(status) {
    var x = { pending: ['warning', 'Pending approval'], approved: ['info', 'Approved'], declined: ['danger', 'Declined'],
      failed: ['danger', 'Failed'], completed: ['success', 'Available'] }[status];
    return x ? badge(x[0], x[1]) : badge('neutral', status);
  }

  function toast(text, tone) {
    var t = el('div', { class: 'rq-toast' + (tone === 'error' ? ' is-error' : ''), role: tone === 'error' ? 'alert' : 'status' }, text);
    root.appendChild(t);
    requestAnimationFrame(function () { t.classList.add('is-in'); });
    setTimeout(function () { t.classList.remove('is-in'); setTimeout(function () { t.remove(); }, 300); }, 4200);
  }

  // ---------- routing ----------
  function parse() {
    var h = location.hash.replace(/^#\/?/, '');
    if (h.indexOf('requests') !== 0) return null;
    var q = {};
    var parts = h.split('?');
    (parts[1] || '').split('&').forEach(function (kv) {
      if (!kv) return;
      var i = kv.indexOf('=');
      q[decodeURIComponent(i < 0 ? kv : kv.slice(0, i))] = decodeURIComponent(i < 0 ? '' : kv.slice(i + 1).replace(/\+/g, ' '));
    });
    return { path: parts[0].split('/').slice(1).filter(Boolean), q: q };
  }
  function go(path, q, replace) {
    var h = '#/requests' + (path ? '/' + path : '') + (q && qs(q) ? '?' + qs(q) : '');
    if (replace) { history.replaceState(null, '', h); route(); } else location.hash = h;
  }
  function link(path, q) { return '#/requests' + (path ? '/' + path : '') + (q && qs(q) ? '?' + qs(q) : ''); }

  function show(on) {
    var was = root.dataset.page === 'requests';
    if (on) root.dataset.page = 'requests'; else delete root.dataset.page;
    page.hidden = !on;
    if (was !== on && window.sdNavChanged) window.sdNavChanged();
  }

  window.gsRequests = {
    open: function () {
      if (!parse()) lastDashHash = location.hash;
      go('', null);
    },
    close: function () {
      history.replaceState(null, '', location.pathname + location.search + (lastDashHash && lastDashHash.indexOf('#/requests') !== 0 ? lastDashHash : ''));
      show(false);
    }
  };

  window.addEventListener('hashchange', route);

  function loadMe(force) {
    if (me && !force && Date.now() - meAt < 60000) return Promise.resolve(me);
    return api('GET', '/api/requests/me').then(function (m) { me = m; meAt = Date.now(); return m; });
  }

  function route() {
    var r = parse();
    if (!r) { if (root.dataset.page === 'requests') show(false); return; }
    if (window.dash && window.dash.can && window.dash.me && window.dash.me() && !window.dash.can('requests')) { show(false); return; }
    build();
    show(true);
    var token = ++routeToken;
    var sect = r.path[0] || 'discover';
    setTabs(sect);
    if (searchInput && sect !== 'search' && document.activeElement !== searchInput) searchInput.value = '';
    body.replaceChildren(el('div', { class: 'rq-loading', role: 'status' }, el('span', { class: 'rq-spin', 'aria-hidden': 'true' }), 'Loading'));
    window.scrollTo({ top: 0 });
    loadMe().then(function () {
      if (token !== routeToken) return;
      if (!me.connected) return notConnected();
      updateTabs();
      var id = r.path[1];
      var view = {
        discover: discover, movies: function () { return browseKind('movies', r); }, tv: function () { return browseKind('tv', r); },
        search: function () { return search(r.q.q || ''); }, movie: function () { return detail('movie', id); },
        series: function () { return detail('tv', id); }, person: function () { return person(id); },
        collection: function () { return collection(id); }, browse: function () { return browse(r.path[1], r.path[2], r.q.title); },
        mine: function () { return mine(r.q); }, watchlist: watchlist, issues: function () { return issues(r.q); },
        manage: function () { return manage(r.q); }
      }[sect] || discover;
      return view();
    }).catch(function (e) {
      if (token !== routeToken) return;
      body.replaceChildren(errorBox(e, route));
    });
  }

  function errorBox(e, retry) {
    return el('div', { class: 'rq-empty rq-empty--error', role: 'alert' },
      el('b', { text: e.status === 403 ? 'Not allowed' : 'Could not load this' }),
      el('span', { text: e.message || 'Something went wrong.' }),
      retry ? el('button', { type: 'button', class: 'rq-btn', text: 'Try again', onclick: function () { forget(); retry(); } }) : null);
  }

  function notConnected() {
    body.replaceChildren(el('div', { class: 'rq-empty' },
      el('b', { text: 'Requests are not set up yet' }),
      el('span', { text: me && me.admin ? 'Connect your Seerr in Settings, Requests. People can then find movies and series and request them here.'
        : 'Ask the admin to connect requests. You can still use Seerr directly until then.' }),
      me && me.admin ? el('button', { type: 'button', class: 'rq-btn rq-btn--accent', text: 'Open Settings, Requests', onclick: function () {
        if (window.stOpenTab) window.stOpenTab('requests');
        if (window.sdOpenSettings) window.sdOpenSettings(true);
      } }) : null));
  }

  // ---------- page frame ----------
  var TABS = [
    { id: 'discover', label: 'Discover', path: '' },
    { id: 'movies', label: 'Movies', path: 'movies' },
    { id: 'tv', label: 'Series', path: 'tv' },
    { id: 'mine', label: 'My requests', path: 'mine' },
    { id: 'watchlist', label: 'Watchlist', path: 'watchlist' },
    { id: 'issues', label: 'Issues', path: 'issues', ok: function () { return me && me.can && me.can.viewIssues; } },
    { id: 'manage', label: 'Approve', path: 'manage', ok: function () { return me && me.can && me.can.manage; } }
  ];
  var tabsBox = null;
  var titleEl = null;
  function build() {
    if (built) return;
    built = true;
    titleEl = el('h1', { class: 'rq-title', tabindex: '-1', text: 'Discover' });
    searchInput = el('input', { type: 'search', class: 'rq-search__input', id: 'rq-q', placeholder: 'Search movies, series and people', autocomplete: 'off', 'aria-label': 'Search movies, series and people' });
    var timer = null;
    searchInput.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var v = searchInput.value.trim();
        var r = parse();
        var onSearch = r && r.path[0] === 'search';
        if (!v) { if (onSearch) go('', null, true); return; }
        go('search', { q: v }, onSearch);
      }, 320);
    });
    searchInput.addEventListener('keydown', function (e) { if (e.key === 'Escape') { searchInput.value = ''; searchInput.dispatchEvent(new Event('input')); } });
    tabsBox = el('nav', { class: 'rq-tabs', 'aria-label': 'Requests sections' });
    body = el('div', { class: 'rq-body' });
    page.replaceChildren(
      el('header', { class: 'rq-top' },
        el('div', { class: 'rq-titles' }, el('span', { class: 'rq-eyebrow', text: 'Requests' }), titleEl),
        el('label', { class: 'rq-search' }, icon('search', 18), searchInput)),
      tabsBox, body);
  }
  function setTitle(t) {
    titleEl.textContent = t;
    document.title = t + ' · Requests · Greystar Telemetry';
  }
  function setTabs(active) {
    tabsBox._active = active;
    updateTabs();
  }
  function updateTabs() {
    var active = tabsBox._active;
    tabsBox.replaceChildren();
    TABS.filter(function (t) { return !t.ok || t.ok(); }).forEach(function (t) {
      var a = el('a', { class: 'rq-tab', href: link(t.path), 'aria-current': active === t.id ? 'page' : null }, t.label);
      if (t.id === 'manage' && me && me.pending) a.appendChild(el('span', { class: 'rq-tab__count', text: String(me.pending) }));
      tabsBox.appendChild(a);
    });
  }

  // ---------- cards and rows ----------
  function posterCard(it) {
    if (it.kind === 'person') {
      return el('a', { class: 'rq-card rq-card--person', href: link('person/' + it.id) },
        el('span', { class: 'rq-card__art rq-card__art--round' }, it.photo ? el('img', { src: img(it.photo, 'w185'), alt: '', loading: 'lazy' }) : el('span', { class: 'rq-card__none', text: (it.title || '?')[0] })),
        el('span', { class: 'rq-card__title', text: it.title }),
        el('span', { class: 'rq-card__meta', text: (it.knownFor || []).join(', ') || 'Person' }));
    }
    var b = mediaBadge(it.status);
    var art = el('span', { class: 'rq-card__art' },
      it.poster ? el('img', { src: img(it.poster, 'w300'), alt: '', loading: 'lazy' }) : el('span', { class: 'rq-card__none', text: it.title }),
      b ? el('span', { class: 'rq-card__badge' }, b) : null,
      el('span', { class: 'rq-card__kind', text: KIND_NAME[it.kind] || '' }));
    return el('a', { class: 'rq-card', href: link((it.kind === 'tv' ? 'series/' : 'movie/') + it.id), title: it.title },
      art, el('span', { class: 'rq-card__title', text: it.title }), el('span', { class: 'rq-card__meta', text: it.year || '' }));
  }
  function requestCard(q) {
    var a = el('a', { class: 'rq-rcard', href: link((q.kind === 'tv' ? 'series/' : 'movie/') + q.tmdbId) });
    if (q.backdrop) a.style.setProperty('--bd', 'url("' + img(q.backdrop, 'w780') + '")');
    a.append(
      q.poster ? el('img', { class: 'rq-rcard__poster', src: img(q.poster, 'w185'), alt: '', loading: 'lazy' }) : el('span', { class: 'rq-rcard__poster' }),
      el('span', { class: 'rq-rcard__text' },
        el('span', { class: 'rq-rcard__year', text: [q.year, KIND_NAME[q.kind]].filter(Boolean).join(' · ') }),
        el('b', { class: 'rq-rcard__title', text: q.title }),
        el('span', { class: 'rq-rcard__by', text: (q.mine ? 'You' : (q.by && q.by.name) || 'Someone') + (q.is4k ? ' · 4K' : '') + (q.seasons && q.seasons.length ? ' · ' + seasonText(q.seasons) : '') }),
        el('span', { class: 'rq-rcard__status' }, requestBadge(q.status))));
    return a;
  }
  function seasonText(list) {
    if (!list || !list.length) return '';
    return (list.length === 1 ? 'Season ' : 'Seasons ') + list.join(', ');
  }
  function strip(items, render) {
    var s = el('div', { class: 'rq-strip' });
    items.forEach(function (i) { s.appendChild(render(i)); });
    return s;
  }
  function rowFrame(title, seeAll) {
    var head = el('div', { class: 'rq-row__head' }, el('h2', { class: 'rq-row__title', text: title }));
    var sec = el('section', { class: 'rq-row' }, head);
    if (seeAll) head.appendChild(el('a', { class: 'rq-row__all', href: seeAll }, 'See all', icon('right', 16)));
    var scroller = el('div', { class: 'rq-row__body' }, el('div', { class: 'rq-strip rq-strip--loading' }, [1, 2, 3, 4, 5, 6].map(function () { return el('span', { class: 'rq-ghost' }); })));
    sec.appendChild(scroller);
    var nav = el('div', { class: 'rq-row__nav' },
      el('button', { type: 'button', class: 'rq-iconbtn', 'aria-label': 'Scroll back', onclick: function () { var st = scroller.querySelector('.rq-strip'); if (st) st.scrollBy({ left: -st.clientWidth * 0.8, behavior: 'smooth' }); } }, icon('left', 18)),
      el('button', { type: 'button', class: 'rq-iconbtn', 'aria-label': 'Scroll on', onclick: function () { var st = scroller.querySelector('.rq-strip'); if (st) st.scrollBy({ left: st.clientWidth * 0.8, behavior: 'smooth' }); } }, icon('right', 18)));
    head.appendChild(nav);
    return { sec: sec, body: scroller };
  }

  var ROW_LINK = { 4: ['browse/trending', 'Trending'], 5: ['movies'], 7: ['browse/upcoming_movies', 'Upcoming movies'], 9: ['tv'],
    11: ['browse/upcoming_tv', 'Upcoming series'], 13: 'movie_keyword', 14: 'movie_genre', 15: 'tv_keyword', 16: 'tv_genre', 17: 'search',
    18: 'studio', 19: 'network', 20: 'movie_streaming', 21: 'tv_streaming' };

  function rowLink(row) {
    var l = ROW_LINK[row.type];
    if (!l) return row.type === 2 ? link('mine') : row.type === 3 ? link('watchlist') : null;
    if (Array.isArray(l)) return link(l[0], l[1] ? { title: l[1] } : null);
    if (l === 'search') return link('search', { q: row.data });
    return link('browse/' + l + '/' + encodeURIComponent(row.data || ''), { title: row.title });
  }

  function fillRow(row, frame) {
    if (row.type === 8 || row.type === 12) {
      var list = row.type === 8 ? home.studios : home.networks;
      frame.body.replaceChildren(strip(list, function (s) {
        return el('a', { class: 'rq-logo', href: link('browse/' + (row.type === 8 ? 'studio' : 'network') + '/' + s.id, { title: s.name }), title: s.name },
          el('img', { src: img(s.logo, 'w780_filter(duotone,ffffff,bababa)'), alt: s.name, loading: 'lazy' }));
      }));
      return Promise.resolve();
    }
    return get('/api/requests/row?' + qs({ type: row.type, data: row.data })).then(function (d) {
      if (d.genres) {
        frame.body.replaceChildren(strip(d.genres, function (g) {
          var a = el('a', { class: 'rq-genre', href: link('browse/' + d.kind + '/' + g.id, { title: g.name }) }, el('span', { text: g.name }));
          if (g.backdrops && g.backdrops[0]) a.style.setProperty('--bd', 'url("' + img(g.backdrops[0], 'w780') + '")');
          return a;
        }));
        return;
      }
      var items = d.items || [];
      if (!items.length) {
        frame.body.replaceChildren(el('p', { class: 'rq-row__none', text: row.type === 2 ? 'No requests yet. Find something below and request it.'
          : row.type === 3 ? 'Nothing on your watchlist yet. Use Watchlist on any title.' : 'Nothing here right now.' }));
        return;
      }
      frame.body.replaceChildren(strip(items, d.requests ? requestCard : posterCard));
    }).catch(function (e) {
      if (e.status === 403) { frame.sec.remove(); return; }  // Seerr does not let this person see this row
      frame.body.replaceChildren(el('p', { class: 'rq-row__none', text: 'Could not load: ' + e.message }));
    });
  }

  function lazyRows(rows) {
    if (!('IntersectionObserver' in window)) { rows.forEach(function (r) { fillRow(r.row, r.frame); }); return; }
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        io.unobserve(e.target);
        var r = rows.find(function (x) { return x.frame.sec === e.target; });
        if (r) fillRow(r.row, r.frame);
      });
    }, { rootMargin: '400px 0px' });
    rows.forEach(function (r) { io.observe(r.frame.sec); });
  }

  // ---------- Discover ----------
  function discover() {
    setTitle('Discover');
    return get('/api/requests/home', 600000).then(function (h) {
      home = h;
      var box = el('div', { class: 'rq-rows' });
      var rows = h.rows.map(function (row) {
        var frame = rowFrame(row.title, rowLink(row));
        if (row.type === 2 || row.type === 1) frame.sec.classList.add('rq-row--first');
        box.appendChild(frame.sec);
        return { row: row, frame: frame };
      });
      body.replaceChildren(quotaNote(), box);
      lazyRows(rows);
    });
  }

  function quotaNote() {
    if (!me || !me.quota) return null;
    var bits = [];
    ['movie', 'tv'].forEach(function (k) {
      var q = me.quota[k];
      if (q && q.limit) bits.push(q.remaining + ' of ' + q.limit + ' ' + (k === 'movie' ? 'movie' : 'series') + ' requests left' + (q.days === 7 ? ' this week' : ' in ' + q.days + ' days'));
    });
    if (!bits.length) return null;
    return el('p', { class: 'rq-quota' }, icon('clock', 16), bits.join(' · '));
  }

  // ---------- grids: browse, search, movies, series ----------
  function grid(fetchPage, opts) {
    opts = opts || {};
    var box = el('div', { class: 'rq-grid' });
    var more = el('button', { type: 'button', class: 'rq-btn rq-more', text: 'Load more' });
    var wrap = el('div', { class: 'rq-gridwrap' }, box, more);
    var n = 1;
    function load() {
      more.disabled = true; more.textContent = 'Loading';
      return fetchPage(n).then(function (d) {
        (d.items || []).forEach(function (i) { box.appendChild(opts.render ? opts.render(i) : posterCard(i)); });
        if (!box.children.length) wrap.replaceChildren(el('div', { class: 'rq-empty' }, el('b', { text: opts.empty || 'Nothing found' }), opts.emptyHint ? el('span', { text: opts.emptyHint }) : null));
        more.hidden = !d.more; more.disabled = false; more.textContent = 'Load more';
        n += 1;
      });
    }
    more.addEventListener('click', function () { load().catch(function (e) { toast(e.message, 'error'); more.disabled = false; more.textContent = 'Load more'; }); });
    return { el: wrap, load: load };
  }

  function browseKind(kind, r) {
    setTitle(kind === 'movies' ? 'Movies' : 'Series');
    var g = grid(function (n) { return get('/api/requests/browse?' + qs({ kind: kind, page: n })); });
    var chips = el('div', { class: 'rq-chips', 'aria-label': 'Genres' });
    get('/api/requests/row?type=' + (kind === 'movies' ? 6 : 10), 3600000).then(function (d) {
      (d.genres || []).forEach(function (x) { chips.appendChild(el('a', { class: 'rq-chip', href: link('browse/' + d.kind + '/' + x.id, { title: x.name }), text: x.name })); });
    }).catch(function () {});
    body.replaceChildren(chips, el('h2', { class: 'rq-h2', text: kind === 'movies' ? 'Popular movies' : 'Popular series' }), g.el);
    return g.load();
  }

  function browse(kind, data, title) {
    setTitle(title || { trending: 'Trending', upcoming_movies: 'Upcoming movies', upcoming_tv: 'Upcoming series' }[kind] || 'Browse');
    var g = grid(function (n) { return get('/api/requests/browse?' + qs({ kind: kind, data: data, page: n })); });
    body.replaceChildren(g.el);
    return g.load();
  }

  function search(q) {
    setTitle(q ? 'Results for "' + q + '"' : 'Search');
    if (searchInput && document.activeElement !== searchInput) searchInput.value = q;
    if (!q) { body.replaceChildren(el('div', { class: 'rq-empty' }, el('b', { text: 'Search for a movie, series or person' }))); return Promise.resolve(); }
    var g = grid(function (n) { return get('/api/requests/search?' + qs({ q: q, page: n })); }, { empty: 'Nothing matches "' + q + '"', emptyHint: 'Check the spelling, or try the original title.' });
    body.replaceChildren(g.el);
    return g.load();
  }

  // ---------- a movie or series ----------
  function detail(kind, id) {
    return get('/api/requests/' + kind + '/' + encodeURIComponent(id), 30000).then(function (d) {
      setTitle(d.title);
      var hero = el('section', { class: 'sd-card rq-hero' });
      if (d.backdrop) hero.style.setProperty('--bd', 'url("' + img(d.backdrop, 'w1280') + '")');
      var meta = [d.year, d.certification, kind === 'movie' ? (d.runtime ? Math.floor(d.runtime / 60) + 'h ' + (d.runtime % 60) + 'm' : null)
        : (d.seasons ? plural(d.seasons.filter(function (s) { return s.number > 0; }).length, 'season', 'seasons') : null),
        (d.genres || []).map(function (g) { return g.name; }).join(', ')].filter(Boolean).join(' · ');
      var badges = el('div', { class: 'rq-hero__badges' }, mediaBadge(d.media.status), me.settings[kind === 'movie' ? 'movie4k' : 'series4k'] ? mediaBadge(d.media.status4k, true) : null);
      hero.append(
        el('span', { class: 'sd-notch', 'aria-hidden': 'true' }),
        el('button', { type: 'button', class: 'sd-go', 'aria-label': 'Back', onclick: function () { history.length > 1 ? history.back() : go('', null); } }, icon('left', 20)),
        d.poster ? el('img', { class: 'rq-hero__poster', src: img(d.poster, 'w500'), alt: '' }) : el('span', { class: 'rq-hero__poster' }),
        el('div', { class: 'rq-hero__info' },
          el('span', { class: 'rq-eyebrow rq-eyebrow--light', text: KIND_NAME[kind] }),
          el('h2', { class: 'rq-hero__title' }, d.title, d.year ? el('span', { class: 'rq-hero__year', text: ' (' + d.year + ')' }) : null),
          el('p', { class: 'rq-hero__meta', text: meta }),
          badges,
          actions(kind, d),
          d.tagline ? el('p', { class: 'rq-hero__tag', text: d.tagline }) : null,
          d.overview ? el('p', { class: 'rq-hero__overview', text: d.overview }) : null));
      var out = [hero];
      var side = facts(kind, d);
      var mineReqs = (d.requests || []).filter(function (q) { return me.can.viewRequests || (me.user && q.by && q.by.id === me.user.id); });
      if (mineReqs.length) {
        out.push(el('section', { class: 'rq-panel' }, el('h2', { class: 'rq-h2', text: me.can.viewRequests ? 'Requests for this title' : 'Your request' }),
          el('ul', { class: 'rq-reqlist' }, mineReqs.map(function (q) {
            return el('li', null, requestBadge(q.status), el('span', { text: (q.by && me.user && q.by.id === me.user.id ? 'You' : (q.by && q.by.name) || 'Someone') + (q.is4k ? ' · 4K' : '') + (q.seasons.length ? ' · ' + seasonText(q.seasons) : '') }),
              el('span', { class: 'rq-muted', text: fmtDate(q.created) }));
          }))));
      }
      if (kind === 'tv') out.push(seasonsPanel(d));
      out.push(side);
      if (d.collection) {
        var c = el('a', { class: 'rq-collection', href: link('collection/' + d.collection.id) },
          el('span', { class: 'rq-eyebrow', text: 'Part of a collection' }), el('b', { text: d.collection.name }), el('span', { class: 'rq-collection__go' }, 'See the collection', icon('right', 16)));
        if (d.collection.poster) c.style.setProperty('--bd', 'url("' + img(d.collection.poster, 'w500') + '")');
        out.push(c);
      }
      if ((d.cast || []).length) {
        var cast = rowFrame('Cast');
        cast.body.replaceChildren(strip(d.cast, function (p) {
          return el('a', { class: 'rq-card rq-card--person', href: link('person/' + p.id) },
            el('span', { class: 'rq-card__art rq-card__art--round' }, p.photo ? el('img', { src: img(p.photo, 'w185'), alt: '', loading: 'lazy' }) : el('span', { class: 'rq-card__none', text: p.name[0] })),
            el('span', { class: 'rq-card__title', text: p.name }), el('span', { class: 'rq-card__meta', text: p.role || '' }));
        }));
        out.push(cast.sec);
      }
      ['recommendations', 'similar'].forEach(function (what) {
        var f = rowFrame(what === 'recommendations' ? 'Recommended' : 'Similar');
        out.push(f.sec);
        get('/api/requests/' + kind + '/' + d.id + '/more?what=' + what).then(function (x) {
          if (!(x.items || []).length) { f.sec.remove(); return; }
          f.body.replaceChildren(strip(x.items, posterCard));
        }).catch(function () { f.sec.remove(); });
      });
      body.replaceChildren.apply(body, out);
      titleEl.focus({ preventScroll: true });
    });
  }

  function facts(kind, d) {
    var rows = [];
    var add2 = function (k, v) { if (v) rows.push(el('dt', { text: k }), el('dd', null, v)); };
    add2('Status', d.status);
    if (kind === 'movie') add2('Release date', fmtDate(d.released));
    else { add2('First aired', fmtDate(d.firstAir)); add2('Last aired', fmtDate(d.lastAir)); add2('Next episode', fmtDate(d.next)); }
    if (d.vote) add2('TMDB rating', (Math.round(d.vote * 10) / 10) + ' / 10');
    (d.crew || []).forEach(function (c) { add2(c.job, el('a', { href: link('person/' + c.id), text: c.name })); });
    if (kind === 'movie' && (d.studios || []).length) add2('Studio', d.studios.map(function (s) { return el('a', { href: link('browse/studio/' + s.id, { title: s.name }), text: s.name }); }).reduce(function (a, b, i) { if (i) a.push(', '); a.push(b); return a; }, []));
    if (kind === 'tv' && (d.networks || []).length) add2('Network', d.networks.map(function (s) { return el('a', { href: link('browse/network/' + s.id, { title: s.name }), text: s.name }); }).reduce(function (a, b, i) { if (i) a.push(', '); a.push(b); return a; }, []));
    return el('section', { class: 'rq-panel' }, el('h2', { class: 'rq-h2', text: 'About' }), el('dl', { class: 'rq-facts' }, rows));
  }

  // The buttons under a title: request (standard and 4K), play, trailer, watchlist, report a problem.
  function actions(kind, d) {
    var box = el('div', { class: 'rq-actions' });
    var canKind = kind === 'movie' ? me.can.movie : me.can.tv;
    var can4k = kind === 'movie' ? me.can.movie4k : me.can.tv4k;
    var open = function (st) { return st === 'unknown' || st === 'deleted' || (kind === 'tv' && st === 'partial'); };
    var tvLeft = function (four) {
      return (d.seasons || []).some(function (s) {
        return (s.number > 0 || me.settings.specials) && !(four ? s.requested4k : s.requested) && ['available', 'processing', 'pending'].indexOf(four ? s.status4k : s.status) < 0;
      });
    };
    var mayStd = open(d.media.status) && (kind === 'movie' || tvLeft(false));
    var may4k = me.settings[kind === 'movie' ? 'movie4k' : 'series4k'] && can4k && open(d.media.status4k) && (kind === 'movie' || tvLeft(true));
    if (canKind && mayStd) box.appendChild(el('button', { type: 'button', class: 'rq-btn rq-btn--accent', onclick: function () { requestDialog(kind, d, false); } }, icon('plus', 18), d.media.status === 'partial' ? 'Request more' : 'Request'));
    else if (['pending', 'processing'].indexOf(d.media.status) >= 0) box.appendChild(el('span', { class: 'rq-btn rq-btn--ghost', 'aria-disabled': 'true' }, icon('clock', 18), d.media.status === 'pending' ? 'Requested' : 'Downloading'));
    if (may4k) box.appendChild(el('button', { type: 'button', class: 'rq-btn rq-btn--ink', onclick: function () { requestDialog(kind, d, true); } }, icon('plus', 18), 'Request in 4K'));
    if (d.media.plexUrl) box.appendChild(el('a', { class: 'rq-btn', href: d.media.plexUrl, target: '_blank', rel: 'noopener' }, icon('play', 18), 'Play on Plex'));
    if (d.trailer) box.appendChild(el('a', { class: 'rq-btn', href: d.trailer, target: '_blank', rel: 'noopener' }, icon('external', 18), 'Trailer'));
    var watched = d.watchlisted;
    var wbtn = el('button', { type: 'button', class: 'rq-btn', 'aria-pressed': watched ? 'true' : 'false' });
    var paintW = function () { wbtn.replaceChildren(icon(watched ? 'check' : 'bookmark', 18), watched ? 'On watchlist' : 'Watchlist'); wbtn.setAttribute('aria-pressed', watched ? 'true' : 'false'); };
    paintW();
    wbtn.addEventListener('click', function () {
      wbtn.disabled = true;
      (watched ? api('DELETE', '/api/requests/watchlist/' + d.id) : api('POST', '/api/requests/watchlist', { tmdbId: d.id, mediaType: kind, title: d.title }))
        .then(function () { watched = !watched; forget(); paintW(); toast(watched ? 'Added to your watchlist.' : 'Removed from your watchlist.'); })
        .catch(function (e) { toast(e.message, 'error'); }).then(function () { wbtn.disabled = false; });
    });
    box.appendChild(wbtn);
    var onPlex = ['available', 'partial'].indexOf(d.media.status) >= 0 || ['available', 'partial'].indexOf(d.media.status4k) >= 0;
    if (me.can.issues && d.media.mediaId && onPlex) {
      box.appendChild(el('button', { type: 'button', class: 'rq-btn rq-btn--quiet', onclick: function () { issueDialog(kind, d); } }, icon('flag', 18), 'Report an issue'));
    }
    if (!canKind && open(d.media.status)) box.appendChild(el('p', { class: 'rq-note', text: 'Your account cannot request ' + (kind === 'movie' ? 'movies' : 'series') + '. Ask the admin if you need to.' }));
    return box;
  }

  function seasonsPanel(d) {
    var list = el('ul', { class: 'rq-seasons' });
    (d.seasons || []).filter(function (s) { return s.number > 0 || me.settings.specials; }).forEach(function (s) {
      var eps = el('div', { class: 'rq-episodes', hidden: true });
      var btn = el('button', { type: 'button', class: 'rq-season__btn', 'aria-expanded': 'false' },
        el('span', { class: 'rq-season__name' }, el('b', { text: s.name || ('Season ' + s.number) }), el('span', { class: 'rq-muted', text: [plural(s.episodes || 0, 'episode', 'episodes'), s.airDate ? s.airDate.slice(0, 4) : null].filter(Boolean).join(' · ') })),
        el('span', { class: 'rq-season__st' },
          mediaBadge(s.status) || (s.requested ? requestBadge(s.requested) : badge('neutral', 'Not requested')),
          me.settings.series4k && (s.status4k !== 'unknown' || s.requested4k) ? (mediaBadge(s.status4k, true) || badge('warning', '4K requested')) : null),
        icon('down', 18));
      btn.addEventListener('click', function () {
        var open = btn.getAttribute('aria-expanded') !== 'true';
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        eps.hidden = !open;
        if (open && !eps._loaded) {
          eps._loaded = true;
          eps.replaceChildren(el('p', { class: 'rq-muted', text: 'Loading episodes' }));
          get('/api/requests/tv/' + d.id + '/season/' + s.number, 600000).then(function (x) {
            eps.replaceChildren(el('ol', { class: 'rq-eplist' }, (x.episodes || []).map(function (e) {
              return el('li', null, el('span', { class: 'rq-ep__n', text: String(e.number) }),
                el('span', null, el('b', { text: e.name }), el('span', { class: 'rq-muted', text: fmtDate(e.airDate) }), e.overview ? el('span', { class: 'rq-ep__ov', text: e.overview }) : null));
            })));
          }).catch(function (e) { eps.replaceChildren(el('p', { class: 'rq-muted', text: e.message })); });
        }
      });
      list.appendChild(el('li', { class: 'rq-season' }, btn, eps));
    });
    return el('section', { class: 'rq-panel' }, el('h2', { class: 'rq-h2', text: 'Seasons' }), list);
  }

  // ---------- dialogs ----------
  function dialog(title, content, foot) {
    var prev = document.activeElement;
    var box = el('div', { class: 'rq-dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      el('div', { class: 'rq-dialog__head' }, el('h2', { text: title }), el('button', { type: 'button', class: 'rq-iconbtn', 'aria-label': 'Close', onclick: function () { close(); } }, icon('close', 18))),
      el('div', { class: 'rq-dialog__body' }, content), foot ? el('div', { class: 'rq-dialog__foot' }, foot) : null);
    var wrap = el('div', { class: 'rq-modal gs-app-modal' }, box);
    wrap.addEventListener('click', function (e) { if (e.target === wrap) close(); });
    wrap.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
    function close() { wrap.remove(); document.body.classList.remove('rq-noscroll'); if (prev && prev.focus) prev.focus(); }
    root.appendChild(wrap);
    document.body.classList.add('rq-noscroll');
    var f = box.querySelector('input, select, textarea, .rq-dialog__foot button');
    if (f) f.focus();
    return { close: close, box: box };
  }

  function requestDialog(kind, d, is4k) {
    var content = el('div', { class: 'rq-req' });
    var msg = el('p', { class: 'rq-req__msg', role: 'status' });
    var picks = [];
    var q = me.quota && me.quota[kind === 'movie' ? 'movie' : 'tv'];
    if (q && q.limit) {
      content.appendChild(el('p', { class: 'rq-quota' }, icon('clock', 16),
        q.remaining + ' of ' + q.limit + ' ' + (kind === 'movie' ? 'movie' : 'series') + ' requests left' + (q.days === 7 ? ' this week' : ' in ' + q.days + ' days') + (kind === 'tv' ? '. Each season counts as one.' : '.')));
    }
    if (kind === 'tv') {
      var rows = (d.seasons || []).filter(function (s) { return s.number > 0 || me.settings.specials; });
      var allBox = el('input', { type: 'checkbox', id: 'rq-all' });
      var tbody = el('tbody');
      rows.forEach(function (s) {
        var st = is4k ? s.status4k : s.status;
        var asked = is4k ? s.requested4k : s.requested;
        var taken = ['available', 'processing', 'pending'].indexOf(st) >= 0 || !!asked;
        var cb = el('input', { type: 'checkbox', id: 'rq-s' + s.number, value: String(s.number), disabled: taken, checked: false });
        if (!taken) picks.push(cb);
        tbody.appendChild(el('tr', { class: taken ? 'is-taken' : null },
          el('td', null, cb), el('td', null, el('label', { for: 'rq-s' + s.number, text: s.name || ('Season ' + s.number) })),
          el('td', { class: 'rq-num', text: String(s.episodes || 0) }),
          el('td', null, mediaBadge(st, is4k) || (asked ? requestBadge(asked) : badge('neutral', 'Not requested')))));
      });
      var sync = function () { allBox.checked = picks.length > 0 && picks.every(function (c) { return c.checked; }); count(); };
      allBox.addEventListener('change', function () { picks.forEach(function (c) { c.checked = allBox.checked; }); count(); });
      picks.forEach(function (c) { c.addEventListener('change', sync); });
      content.appendChild(el('table', { class: 'rq-seasontable' },
        el('thead', null, el('tr', null, el('th', null, allBox), el('th', null, el('label', { for: 'rq-all', text: 'All seasons' })), el('th', { class: 'rq-num', text: 'Episodes' }), el('th', { text: 'Status' }))),
        tbody));
    }
    var adv = null;
    if (me.can.advanced) {
      adv = el('div', { class: 'rq-adv' }, el('p', { class: 'rq-muted', text: 'Loading download options' }));
      content.appendChild(el('details', { class: 'rq-advbox' }, el('summary', { text: 'Download options' }), adv));
      api('GET', '/api/requests/services/' + kind + '?is4k=' + (is4k ? 'true' : 'false')).then(function (x) {
        if (!x.servers.length) { adv.replaceChildren(el('p', { class: 'rq-muted', text: 'No ' + (is4k ? '4K ' : '') + 'servers set up in Seerr.' })); return; }
        var server = el('select', { class: 'st-select', id: 'rq-server' });
        var profile = el('select', { class: 'st-select', id: 'rq-profile' });
        var folder = el('select', { class: 'st-select', id: 'rq-folder' });
        var lang = el('select', { class: 'st-select', id: 'rq-lang' });
        x.servers.forEach(function (s) { server.appendChild(el('option', { value: String(s.id), text: s.name + (s.default ? ' (default)' : ''), selected: s.default })); });
        var fill = function () {
          var s = x.servers.find(function (z) { return String(z.id) === server.value; }) || x.servers[0];
          profile.replaceChildren.apply(profile, s.profiles.map(function (p) { return el('option', { value: String(p.id), text: p.name, selected: p.id === s.defaultProfile }); }));
          folder.replaceChildren.apply(folder, s.folders.map(function (f) { return el('option', { value: f.path, text: f.path, selected: f.path === s.defaultFolder }); }));
          lang.replaceChildren.apply(lang, s.languages.map(function (l) { return el('option', { value: String(l.id), text: l.name, selected: l.id === s.defaultLanguage }); }));
          lang.parentElement && (lang.parentElement.hidden = !s.languages.length);
        };
        server.addEventListener('change', fill);
        var f = function (label, input) { return el('div', { class: 'st-field' }, el('label', { for: input.id, text: label }), input); };
        adv.replaceChildren(el('div', { class: 'st-form' }, f('Server', server), f('Quality profile', profile), f('Root folder', folder), kind === 'tv' ? f('Language profile', lang) : null));
        fill();
        adv._value = function () {
          var o = { serverId: Number(server.value), profileId: Number(profile.value), rootFolder: folder.value };
          if (kind === 'tv' && lang.value) o.languageProfileId = Number(lang.value);
          return o;
        };
      }).catch(function (e) { adv.replaceChildren(el('p', { class: 'rq-muted', text: e.message })); });
    }
    var auto = is4k ? false : (kind === 'movie' ? me.can.autoMovie : me.can.autoTv);
    content.appendChild(el('p', { class: 'rq-note', text: auto ? 'This is approved straight away and starts downloading.' : 'Requests wait for approval, then download. You can follow it under My requests.' }));
    content.appendChild(msg);
    var send = el('button', { type: 'button', class: 'rq-btn rq-btn--accent' }, icon('plus', 18), 'Request');
    var cancel = el('button', { type: 'button', class: 'rq-btn', text: 'Cancel' });
    function count() {
      if (kind !== 'tv') return;
      var n = picks.filter(function (c) { return c.checked; }).length;
      send.disabled = !n;
      send.replaceChildren(icon('plus', 18), n ? 'Request ' + plural(n, 'season', 'seasons') : 'Pick seasons');
    }
    count();
    if (q && q.restricted) { send.disabled = true; msg.textContent = 'You have used all your requests for now. They free up as older ones age out.'; }
    var dlg = dialog((is4k ? 'Request in 4K: ' : 'Request ') + d.title, content, [cancel, send]);
    cancel.addEventListener('click', dlg.close);
    send.addEventListener('click', function () {
      var bodyData = { mediaType: kind, mediaId: d.id, is4k: is4k };
      if (kind === 'tv') { bodyData.seasons = picks.filter(function (c) { return c.checked; }).map(function (c) { return Number(c.value); }); if (d.tvdbId) bodyData.tvdbId = d.tvdbId; }
      if (adv && adv._value && adv.closest('details').open) Object.assign(bodyData, adv._value());
      send.disabled = true; send.textContent = 'Sending';
      api('POST', '/api/requests/request', bodyData).then(function (r) {
        dlg.close();
        forget();
        toast(d.title + ': ' + (r.status === 'approved' ? 'approved and on its way.' : 'requested. It is waiting for approval.'));
        route();
      }).catch(function (e) {
        msg.textContent = e.message;
        msg.classList.add('is-error');
        send.disabled = false; count(); if (kind !== 'tv') send.replaceChildren(icon('plus', 18), 'Request');
      });
    });
  }

  function issueDialog(kind, d) {
    var types = [[1, 'Video'], [2, 'Audio'], [3, 'Subtitles'], [4, 'Other']];
    var pick = el('div', { class: 'rq-chips', role: 'radiogroup', 'aria-label': 'What is wrong' });
    types.forEach(function (t, i) {
      pick.appendChild(el('label', { class: 'rq-chip rq-chip--radio' }, el('input', { type: 'radio', name: 'rq-itype', value: String(t[0]), checked: i === 0 }), t[1]));
    });
    var season = null, episode = null;
    if (kind === 'tv') {
      season = el('select', { class: 'st-select', id: 'rq-iseason' }, el('option', { value: '', text: 'All seasons' }),
        (d.seasons || []).filter(function (s) { return s.number > 0 && ['available', 'partial'].indexOf(s.status) >= 0; }).map(function (s) { return el('option', { value: String(s.number), text: s.name || 'Season ' + s.number }); }));
      episode = el('input', { class: 'st-input', id: 'rq-iep', type: 'number', min: 1, placeholder: 'All episodes' });
    }
    var text = el('textarea', { class: 'st-input rq-textarea', id: 'rq-itext', rows: 4, maxlength: 2000, placeholder: 'What did you notice? For example: no sound after 20 minutes.' });
    var msg = el('p', { class: 'rq-req__msg', role: 'status' });
    var f = function (label, input) { return el('div', { class: 'st-field' }, el('label', { for: input.id, text: label }), input); };
    var content = el('div', { class: 'rq-req' }, el('p', { class: 'rq-note', text: 'Say what is wrong with this title on Plex. You will see replies under Issues.' }),
      pick, kind === 'tv' ? el('div', { class: 'st-form' }, f('Season', season), f('Episode', episode)) : null, f('Details', text), msg);
    var send = el('button', { type: 'button', class: 'rq-btn rq-btn--accent', text: 'Send report' });
    var cancel = el('button', { type: 'button', class: 'rq-btn', text: 'Cancel' });
    var dlg = dialog('Report an issue: ' + d.title, content, [cancel, send]);
    cancel.addEventListener('click', dlg.close);
    send.addEventListener('click', function () {
      if (!text.value.trim()) { msg.textContent = 'Add a few words about the problem.'; msg.classList.add('is-error'); text.focus(); return; }
      var t = dlg.box.querySelector('input[name="rq-itype"]:checked');
      var b = { issueType: Number(t ? t.value : 4), message: text.value.trim(), mediaId: d.media.mediaId };
      if (season && season.value) b.problemSeason = Number(season.value);
      if (episode && episode.value) b.problemEpisode = Number(episode.value);
      send.disabled = true;
      api('POST', '/api/requests/issue', b).then(function () { dlg.close(); forget(); toast('Thanks. Your report was sent.'); })
        .catch(function (e) { msg.textContent = e.message; msg.classList.add('is-error'); send.disabled = false; });
    });
  }

  // ---------- people and collections ----------
  function person(id) {
    return get('/api/requests/person/' + encodeURIComponent(id), 600000).then(function (p) {
      setTitle(p.name);
      var bio = el('p', { class: 'rq-bio is-clamped', text: p.bio || 'No biography yet.' });
      var more = (p.bio || '').length > 420 ? el('button', { type: 'button', class: 'rq-link', text: 'Read more', onclick: function () { bio.classList.toggle('is-clamped'); more.textContent = bio.classList.contains('is-clamped') ? 'Read more' : 'Show less'; } }) : null;
      var head = el('section', { class: 'sd-card rq-personhead' },
        p.photo ? el('img', { class: 'rq-personhead__photo', src: img(p.photo, 'w300'), alt: '' }) : el('span', { class: 'rq-personhead__photo' }),
        el('div', null, el('span', { class: 'rq-eyebrow', text: p.known || 'Person' }),
          el('p', { class: 'rq-muted', text: [p.born ? 'Born ' + fmtDate(p.born) : null, p.place, p.died ? 'Died ' + fmtDate(p.died) : null].filter(Boolean).join(' · ') }),
          bio, more));
      var g = el('div', { class: 'rq-grid' });
      (p.items || []).forEach(function (i) { g.appendChild(posterCard(i)); });
      body.replaceChildren(head, el('h2', { class: 'rq-h2', text: 'Movies and series' }), g);
    });
  }

  function collection(id) {
    return get('/api/requests/collection/' + encodeURIComponent(id), 30000).then(function (c) {
      setTitle(c.name);
      var missing = c.items.filter(function (i) { return i.status === 'unknown' || i.status === 'deleted'; });
      var hero = el('section', { class: 'sd-card rq-hero rq-hero--short' });
      if (c.backdrop) hero.style.setProperty('--bd', 'url("' + img(c.backdrop, 'w1280') + '")');
      var reqAll = me.can.movie && missing.length ? el('button', { type: 'button', class: 'rq-btn rq-btn--accent' }, icon('plus', 18), 'Request ' + plural(missing.length, 'missing movie', 'missing movies')) : null;
      if (reqAll) reqAll.addEventListener('click', function () {
        reqAll.disabled = true;
        var done = 0, failed = 0;
        missing.reduce(function (p, m) {
          return p.then(function () { return api('POST', '/api/requests/request', { mediaType: 'movie', mediaId: m.id }).then(function () { done++; }, function () { failed++; }); });
        }, Promise.resolve()).then(function () {
          forget();
          toast('Requested ' + plural(done, 'movie', 'movies') + (failed ? '. ' + failed + ' could not be requested.' : '.'), failed ? 'error' : null);
          route();
        });
      });
      hero.append(el('span', { class: 'sd-notch', 'aria-hidden': 'true' }),
        el('button', { type: 'button', class: 'sd-go', 'aria-label': 'Back', onclick: function () { history.back(); } }, icon('left', 20)),
        c.poster ? el('img', { class: 'rq-hero__poster', src: img(c.poster, 'w500'), alt: '' }) : el('span', { class: 'rq-hero__poster' }),
        el('div', { class: 'rq-hero__info' }, el('span', { class: 'rq-eyebrow rq-eyebrow--light', text: 'Collection' }), el('h2', { class: 'rq-hero__title', text: c.name }),
          el('p', { class: 'rq-hero__meta', text: plural(c.items.length, 'movie', 'movies') + (missing.length ? ' · ' + missing.length + ' not on Plex yet' : ' · all on Plex or requested') }),
          el('div', { class: 'rq-actions' }, reqAll), c.overview ? el('p', { class: 'rq-hero__overview', text: c.overview }) : null));
      var g = el('div', { class: 'rq-grid' });
      c.items.forEach(function (i) { g.appendChild(posterCard(i)); });
      body.replaceChildren(hero, g);
    });
  }

  // ---------- my requests, approvals, watchlist, issues ----------
  var FILTERS = [['all', 'All'], ['pending', 'Pending'], ['approved', 'Approved'], ['processing', 'Processing'], ['available', 'Available'], ['failed', 'Failed']];
  function filterChips(path, current, extra) {
    return el('div', { class: 'rq-chips', role: 'group', 'aria-label': 'Show' }, FILTERS.map(function (f) {
      return el('a', { class: 'rq-chip', href: link(path, Object.assign({}, extra || {}, { filter: f[0] === 'all' ? null : f[0] })), 'aria-current': (current || 'all') === f[0] ? 'true' : null, text: f[1] });
    }));
  }
  function requestRow(q, onChange) {
    var actions = el('div', { class: 'rq-rrow__actions' });
    if (q.canDecide) {
      actions.append(
        el('button', { type: 'button', class: 'rq-btn rq-btn--accent rq-btn--sm', onclick: function (e) { decide(q, 'approve', e.currentTarget, onChange); } }, icon('check', 16), 'Approve'),
        el('button', { type: 'button', class: 'rq-btn rq-btn--sm', onclick: function (e) { decide(q, 'decline', e.currentTarget, onChange); } }, icon('close', 16), 'Decline'));
    }
    if (q.canCancel && q.status === 'pending') {
      var cancel = el('button', { type: 'button', class: 'rq-btn rq-btn--quiet rq-btn--sm', text: q.mine ? 'Cancel request' : 'Delete' });
      cancel.addEventListener('click', function () {
        if (cancel.dataset.sure !== '1') { cancel.dataset.sure = '1'; cancel.textContent = 'Tap again to cancel'; return; }
        cancel.disabled = true;
        api('DELETE', '/api/requests/request/' + q.id).then(function () { forget(); toast('Request cancelled.'); onChange(); })
          .catch(function (e) { toast(e.message, 'error'); cancel.disabled = false; });
      });
      actions.appendChild(cancel);
    }
    return el('li', { class: 'rq-rrow' },
      el('a', { class: 'rq-rrow__poster', href: link((q.kind === 'tv' ? 'series/' : 'movie/') + q.tmdbId), 'aria-hidden': 'true', tabindex: '-1' },
        q.poster ? el('img', { src: img(q.poster, 'w185'), alt: '', loading: 'lazy' }) : null),
      el('div', { class: 'rq-rrow__main' },
        el('a', { class: 'rq-rrow__title', href: link((q.kind === 'tv' ? 'series/' : 'movie/') + q.tmdbId) }, q.title, q.year ? el('span', { class: 'rq-muted', text: ' (' + q.year + ')' }) : null),
        el('span', { class: 'rq-rrow__meta', text: [KIND_NAME[q.kind], q.is4k ? '4K' : null, seasonText(q.seasons), (q.mine ? 'You' : (q.by && q.by.name)) + ', ' + fmtDate(q.created)].filter(Boolean).join(' · ') }),
        q.modifiedBy && q.status !== 'pending' ? el('span', { class: 'rq-muted', text: (q.status === 'declined' ? 'Declined by ' : 'Approved by ') + q.modifiedBy }) : null),
      el('div', { class: 'rq-rrow__status' }, requestBadge(q.status), q.status === 'approved' && q.media.status === 'processing' ? badge('info', 'Downloading') : null),
      actions);
  }
  function decide(q, what, btn, onChange) {
    btn.disabled = true;
    api('POST', '/api/requests/request/' + q.id + '/' + what).then(function () { forget(); toast(q.title + (what === 'approve' ? ' approved.' : ' declined.')); onChange(); })
      .catch(function (e) { toast(e.message, 'error'); btn.disabled = false; });
  }
  function requestList(url, emptyText, emptyHint) {
    var list = el('ul', { class: 'rq-rlist' });
    var more = el('button', { type: 'button', class: 'rq-btn rq-more', text: 'Load more' });
    var n = 1;
    var reload = function () { route(); };
    function load() {
      more.disabled = true;
      return api('GET', url + '&page=' + n).then(function (d) {
        (d.items || []).forEach(function (q) { list.appendChild(requestRow(q, reload)); });
        if (!list.children.length) list.replaceWith(el('div', { class: 'rq-empty' }, el('b', { text: emptyText }), emptyHint ? el('span', { text: emptyHint }) : null));
        more.hidden = !d.more; more.disabled = false; n++;
      });
    }
    more.addEventListener('click', function () { load().catch(function (e) { toast(e.message, 'error'); }); });
    return { el: el('div', null, list, more), load: load };
  }
  function mine(q) {
    setTitle('My requests');
    var everyone = q.everyone === '1' && me.can.viewRequests;
    var l = requestList('/api/requests/mine?' + qs({ filter: q.filter || 'all', everyone: everyone ? 'true' : null }),
      everyone ? 'No requests match' : 'You have not requested anything yet', everyone ? null : 'Find a movie or series and choose Request.');
    var top = el('div', { class: 'rq-listtop' }, filterChips('mine', q.filter, { everyone: everyone ? '1' : null }),
      me.can.viewRequests ? el('a', { class: 'rq-chip', href: link('mine', { filter: q.filter, everyone: everyone ? null : '1' }), 'aria-pressed': everyone ? 'true' : 'false', text: everyone ? 'Everyone' : 'Only mine' }) : null);
    body.replaceChildren(quotaNote(), top, l.el);
    return l.load();
  }
  function manage(q) {
    setTitle('Approve requests');
    if (!me.can.manage) { body.replaceChildren(el('div', { class: 'rq-empty' }, el('b', { text: 'Only people who manage requests in Seerr can approve.' }))); return Promise.resolve(); }
    var f = q.filter || 'pending';
    var l = requestList('/api/requests/mine?' + qs({ filter: f, everyone: 'true' }), f === 'pending' ? 'Nothing waiting for approval' : 'No requests match');
    body.replaceChildren(filterChips('manage', f), l.el);
    return l.load();
  }
  function watchlist() {
    setTitle('Watchlist');
    var g = grid(function (n) { return get('/api/requests/watchlist?page=' + n, 30000); },
      { empty: 'Your watchlist is empty', emptyHint: 'Choose Watchlist on any movie or series to keep it here. Your Plex watchlist shows here too once you have signed in to Seerr with Plex.' });
    body.replaceChildren(g.el);
    return g.load();
  }
  function issues(q) {
    setTitle('Issues');
    var f = q.filter || 'open';
    return api('GET', '/api/requests/issues?' + qs({ filter: f })).then(function (d) {
      var chips = el('div', { class: 'rq-listtop' },
        el('div', { class: 'rq-chips' }, [['open', 'Open'], ['resolved', 'Resolved'], ['all', 'All']].map(function (x) {
          return el('a', { class: 'rq-chip', href: link('issues', { filter: x[0] }), 'aria-current': f === x[0] ? 'true' : null, text: x[1] });
        })),
        me.can.issues ? el('button', { type: 'button', class: 'rq-btn rq-btn--accent', onclick: pickForIssue }, icon('flag', 18), 'Report an issue') : null);
      if (!d.items.length) {
        body.replaceChildren(chips, el('div', { class: 'rq-empty' }, el('b', { text: f === 'open' ? 'No open issues' : 'No issues' }),
          el('span', { text: 'If something on Plex will not play right, choose Report an issue above, or open the title and use its Report an issue button.' })));
        return;
      }
      var list = el('ul', { class: 'rq-rlist' });
      d.items.forEach(function (i) {
        var reply = el('textarea', { class: 'st-input rq-textarea', rows: 2, maxlength: 2000, placeholder: 'Add a comment', 'aria-label': 'Add a comment' });
        var send = el('button', { type: 'button', class: 'rq-btn rq-btn--sm', text: 'Comment' });
        send.addEventListener('click', function () {
          if (!reply.value.trim()) return;
          send.disabled = true;
          api('POST', '/api/requests/issue/' + i.id + '/comment', { message: reply.value.trim() }).then(function () { toast('Comment added.'); route(); })
            .catch(function (e) { toast(e.message, 'error'); send.disabled = false; });
        });
        var resolve = me.can.manageIssues ? el('button', { type: 'button', class: 'rq-btn rq-btn--sm', text: i.status === 'open' ? 'Mark resolved' : 'Reopen', onclick: function (e) {
          e.currentTarget.disabled = true;
          api('POST', '/api/requests/issue/' + i.id + '/' + (i.status === 'open' ? 'resolved' : 'open')).then(function () { route(); }).catch(function (x) { toast(x.message, 'error'); });
        } }) : null;
        list.appendChild(el('li', { class: 'rq-rrow rq-rrow--issue' },
          el('a', { class: 'rq-rrow__poster', href: link((i.kind === 'tv' ? 'series/' : 'movie/') + i.tmdbId), 'aria-hidden': 'true', tabindex: '-1' }, i.poster ? el('img', { src: img(i.poster, 'w185'), alt: '', loading: 'lazy' }) : null),
          el('div', { class: 'rq-rrow__main' },
            el('a', { class: 'rq-rrow__title', href: link((i.kind === 'tv' ? 'series/' : 'movie/') + i.tmdbId), text: i.title }),
            el('span', { class: 'rq-rrow__meta', text: [i.type + ' problem', i.season ? 'Season ' + i.season + (i.episode ? ', episode ' + i.episode : '') : null, (i.by || 'Someone') + ', ' + fmtDate(i.created)].filter(Boolean).join(' · ') }),
            el('ul', { class: 'rq-comments' }, i.comments.map(function (c) { return el('li', null, el('b', { text: (c.by || 'Someone') + ': ' }), c.message); })),
            el('div', { class: 'rq-reply' }, reply, send, resolve)),
          el('div', { class: 'rq-rrow__status' }, i.status === 'open' ? badge('warning', 'Open') : badge('success', 'Resolved'))));
      });
      body.replaceChildren(chips, list);
    });
  }

  // Report an issue from the Issues page: find the title first (only what is on Plex can have an issue).
  function pickForIssue() {
    var input = el('input', { type: 'search', class: 'st-input', id: 'rq-ipick', placeholder: 'Search for the movie or series', autocomplete: 'off' });
    var list = el('ul', { class: 'rq-picklist', role: 'listbox', 'aria-label': 'Titles on Plex' });
    var hint = el('p', { class: 'rq-muted', text: 'Only titles that are on Plex can have an issue.' });
    var content = el('div', { class: 'rq-req' }, el('div', { class: 'st-field' }, el('label', { for: 'rq-ipick', text: 'Which title?' }), input), hint, list);
    var dlg = dialog('Report an issue', content, null);
    var timer = null, n = 0;
    function show(items) {
      list.replaceChildren();
      items.forEach(function (it) {
        list.appendChild(el('li', null, el('button', { type: 'button', class: 'rq-pick', onclick: function () {
          dlg.close();
          get('/api/requests/' + (it.kind === 'tv' ? 'tv' : 'movie') + '/' + it.id, 30000).then(function (d) {
            if (!d.media.mediaId) { toast('That title is not on Plex yet.', 'error'); return; }
            issueDialog(it.kind, d);
          }).catch(function (e) { toast(e.message, 'error'); });
        } }, it.poster ? el('img', { src: img(it.poster, 'w92'), alt: '' }) : el('span', { class: 'rq-pick__none' }),
          el('span', null, el('b', { text: it.title }), el('span', { class: 'rq-muted', text: [it.year, KIND_NAME[it.kind]].filter(Boolean).join(' · ') })),
          mediaBadge(it.status))));
      });
      if (!items.length && input.value.trim()) list.appendChild(el('li', { class: 'rq-muted', text: 'Nothing on Plex matches that.' }));
    }
    function load() {
      var q = input.value.trim();
      var mine = ++n;
      if (!q) {
        get('/api/requests/row?type=1', 60000).then(function (d) { if (mine === n) { hint.textContent = 'Recently added, or search above.'; show((d.items || []).filter(onPlex)); } }).catch(function () {});
        return;
      }
      get('/api/requests/search?' + qs({ q: q })).then(function (d) {
        if (mine === n) { hint.textContent = 'Only titles that are on Plex can have an issue.'; show((d.items || []).filter(onPlex)); }
      }).catch(function (e) { if (mine === n) list.replaceChildren(el('li', { class: 'rq-muted', text: e.message })); });
    }
    function onPlex(i) { return (i.kind === 'movie' || i.kind === 'tv') && (['available', 'partial'].indexOf(i.status) >= 0 || ['available', 'partial'].indexOf(i.status4k) >= 0); }
    input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(load, 300); });
    load();
  }

  // ---------- start ----------
  document.addEventListener('gs:me', function () { route(); });
  if (parse()) route();
})();
