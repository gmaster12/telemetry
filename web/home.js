/* Viewer home (Glenn's pick): people who sign in with Plex and are not the administrator land on a calm home in
   the layout they choose in Settings, Display (saved per browser as `vhome`):
     doors  Four big doors (default): Watching now, Request something, My history, My subscription.
     hub    Requests hub: a big search, your requests by stage, trending.
     month  My month: your own viewing for the week, month or year.
   Each door or card opens the sections the page already has (streams, history, subscription) with a Home button
   to come back. The admin page is unchanged. Server status stays at the end of the lime bar. */
(function () {
  'use strict';
  var root = document.querySelector('.gs-app');
  var box = document.getElementById('vhome');
  var back = document.getElementById('vhome-back');
  if (!root || !box || !window.dash) return;

  var LAYOUTS = ['doors', 'hub', 'month'];
  var VIEWS = ['home', 'streams', 'history', 'subscription'];
  var DAY = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  var cache = {};
  var period = 'month';

  function el(tag, attrs) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === 'text') n.textContent = v;
      else if (k === 'class') n.className = v;
      else if (k.slice(0, 2) === 'on') n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) add(n, arguments[i]);
    return n;
  }
  function add(n, c) {
    if (c == null || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { add(n, x); }); return; }
    n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  function icon(name, size) { return window.sdIcon ? window.sdIcon(name, size || 18) : el('span'); }
  function me() { return window.dash.me ? window.dash.me() : null; }
  function can(s) { return window.dash.can ? window.dash.can(s) : false; }
  function get(url, ttl) {
    var hit = cache[url];
    if (hit && hit.until > Date.now()) return hit.p;
    var p = fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
    cache[url] = { p: p, until: Date.now() + (ttl || 60000) };
    p.catch(function () { delete cache[url]; });
    return p;
  }
  function rqLink(path, q) {
    var qs = q ? Object.keys(q).filter(function (k) { return q[k] != null && q[k] !== ''; }).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(q[k]); }).join('&') : '';
    return '#/requests' + (path ? '/' + path : '') + (qs ? '?' + qs : '');
  }
  var images = 'https://image.tmdb.org/t/p/';
  function tmdb(path, size) { return !path ? null : /^(https?:|data:|\/api\/)/.test(path) ? path : images + size + path; }
  function tz() { return -new Date().getTimezoneOffset(); }
  function ago(sec) {
    if (sec < 3600) return Math.max(1, Math.round(sec / 60)) + ' min ago';
    if (sec < 86400) return Math.round(sec / 3600) + ' h ago';
    var d = Math.round(sec / 86400);
    return d === 1 ? 'yesterday' : d + ' days ago';
  }
  function hourText(h) { return (h % 12 || 12) + (h < 12 ? ' AM' : ' PM'); }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }
  function shortDate(iso) { return iso ? new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ''; }

  // ---------- which layout, which view ----------
  function active() { var m = me(); return !!m && m.role !== 'admin' && m.kind === 'plex'; }
  function layout() {
    var v = window.dash.params().vhome;
    if (LAYOUTS.indexOf(v) < 0) v = 'doors';
    if (v === 'hub' && !can('requests')) v = 'doors';
    if (v === 'month' && !can('history')) v = 'doors';
    return v;
  }
  function view() { return VIEWS.indexOf(root.dataset.vview) >= 0 ? root.dataset.vview : 'home'; }
  function show(v) {
    if (VIEWS.indexOf(v) < 0) v = 'home';
    if (root.dataset.page === 'requests' && window.gsRequests) window.gsRequests.close();
    root.dataset.vview = v;
    if (window.sdSetMtab) window.sdSetMtab({ home: 'home', streams: 'streams', history: 'people', subscription: 'home' }[v]);
    if (v === 'home') render();
    if (window.sdNavChanged) window.sdNavChanged();
    root.scrollIntoView({ block: 'start' });
  }
  function apply() {
    var on = active();
    root.classList.toggle('gs-vh', on);
    if (!on) { delete root.dataset.vview; delete root.dataset.vlayout; return; }
    root.dataset.vlayout = layout();
    if (!root.dataset.vview) root.dataset.vview = 'home';
  }

  // ---------- shared bits ----------
  function ownStreams() {
    var d = window.dash.data('streams');
    return d && d.status === 'ok' ? d.data.slice().sort(function (a, b) { return a.elapsed_min - b.elapsed_min; }) : [];
  }
  function goBtn(label, fn, dark) {
    return el('button', { type: 'button', class: 'vh-go' + (dark ? ' vh-go--dark' : ''), 'aria-label': label, onclick: function (e) { e.stopPropagation(); fn(); } }, icon('arrow', 18));
  }
  function door(cls, label, open) {
    var d = el('section', { class: 'vh-door ' + cls, 'aria-label': label });
    if (open) {
      d.addEventListener('click', function (e) { if (!e.target.closest('input, button, a, form')) open(); });
      d.appendChild(el('span', { class: 'vh-door__cut', 'aria-hidden': 'true' }));
      d.appendChild(goBtn('Open ' + label, open, cls.indexOf('vh-door--req') >= 0));
    }
    return d;
  }
  function searchForm(cls, placeholder) {
    var input = el('input', { type: 'search', class: 'vh-search__input', name: 'q', placeholder: placeholder, 'aria-label': 'Search movies and series', autocomplete: 'off' });
    return el('form', { class: 'vh-search ' + (cls || ''), role: 'search', onsubmit: function (e) {
      e.preventDefault();
      var q = input.value.trim();
      window.location.hash = q ? rqLink('search', { q: q }) : rqLink('');
    } }, icon('search', 20), input, el('button', { type: 'submit', class: 'vh-search__go', text: 'Search' }));
  }
  function nowItem(r) {
    var art = window.sdArtFor ? window.sdArtFor(r) : null;
    var pct = Math.max(0, Math.min(100, Number(r.progress_pct) || 0));
    var wrap = el('div', { class: 'vh-now' },
      art ? el('div', { class: 'vh-now__art', style: 'background-image:url("' + art + '")', 'aria-hidden': 'true' }) : null,
      el('div', { class: 'vh-now__fade', 'aria-hidden': 'true' }),
      el('div', { class: 'vh-now__in' },
        el('span', { class: 'vh-eyebrow', text: 'Watching now' }),
        el('b', { class: 'vh-now__title', text: r.title || 'Something' }),
        r.subtitle ? el('span', { class: 'vh-muted', text: r.subtitle }) : null,
        el('span', { class: 'vh-tags' }, [r.player || r.device, r.resolution, r.server].filter(Boolean).map(function (t) { return el('span', { class: 'vh-tag', text: t }); })),
        el('span', { class: 'vh-prog', 'aria-hidden': 'true' }, el('i', { style: 'width:' + pct + '%' })),
        el('span', { class: 'vh-muted vh-small', text: pct + '% · ' + Math.round(r.elapsed_min || 0) + ' min in' + (r.state && r.state !== 'Playing' ? ' · ' + r.state : '') })));
    return wrap;
  }

  // ---------- Four big doors ----------
  function doors() {
    var grid = el('div', { class: 'vh-doors' });
    // Watching now
    var nowDoor = door('vh-door--now', 'Watching now', can('streams') ? function () { show('streams'); } : null);
    nowBody = el('div', { class: 'vh-door__body' });
    nowDoor.appendChild(nowBody);
    grid.appendChild(nowDoor);
    fillNow();
    // Request something
    if (can('requests')) {
      var req = door('vh-door--req', 'Request something', function () { window.location.hash = rqLink(''); });
      var status = el('p', { class: 'vh-door__foot', text: 'Ask for anything that is not on Plex yet.' });
      req.append(el('div', { class: 'vh-door__body' },
        el('span', { class: 'vh-eyebrow', text: 'Request something' }),
        el('b', { class: 'vh-door__big', text: 'Missing a movie or show? Ask for it here.' }),
        searchForm('vh-search--door', 'Search movies and series'), status));
      grid.appendChild(req);
      get('/api/requests/attention').then(function (a) {
        if (a.failed && a.failed.length) {
          status.replaceChildren(el('a', { class: 'vh-alert', href: rqLink('mine') }, el('span', { class: 'vh-sq', 'aria-hidden': 'true' }), plural(a.failed.length, 'request failed', 'requests failed') + '. Try again or remove it.'));
        } else if (a.waiting && a.waiting.length) {
          status.textContent = plural(a.waiting.length, 'request is', 'requests are') + ' waiting for a download.';
        }
        return get('/api/requests/mine?filter=available');
      }).then(function (d) {
        var weekAgo = Date.now() - 14 * 864e5;
        var fresh = ((d && d.items) || []).filter(function (x) { return !x.created || Date.parse(x.created) > weekAgo; });
        if (fresh.length && !status.querySelector('.vh-alert')) status.textContent = plural(fresh.length, 'of your requests is', 'of your requests are') + ' ready to watch.';
      }).catch(function () {});
    }
    // My history
    if (can('history')) {
      var hist = door('vh-door--hist', 'My history', function () { show('history'); });
      var hb = el('div', { class: 'vh-door__body' }, el('span', { class: 'vh-eyebrow', text: 'My history' }), el('b', { class: 'vh-door__big', text: '…' }));
      hist.appendChild(hb);
      grid.appendChild(hist);
      get('/api/me/recap?period=month&tz=' + tz()).then(function (r) {
        hb.replaceChildren();
        add(hb, [el('span', { class: 'vh-eyebrow', text: 'My history' }),
          el('b', { class: 'vh-door__big', text: r.plays ? plural(r.plays, 'play', 'plays') + ' this month' : 'Nothing watched this month yet' }),
          r.last ? el('span', { class: 'vh-muted', text: 'Last: ' + r.last.title + (r.last.subtitle ? ' ' + r.last.subtitle.replace(/ · /g, ' ') : '') + ', ' + ago(Date.now() / 1000 - r.last.started_at) }) : null,
          el('span', { class: 'vh-minis', 'aria-hidden': 'true' }, (r.top || []).slice(0, 5).map(function (t) {
            var m = el('span', { class: 'vh-mini' });
            if (t.poster) { var im = new Image(); im.onload = function () { m.style.backgroundImage = 'url("' + t.poster + '")'; }; im.src = t.poster; }
            return m;
          }))]);
      }).catch(function () {});
    }
    // My subscription
    var sub = window.gsMySub ? window.gsMySub() : null;
    if (sub && sub.configured) {
      var sd = door('vh-door--sub', 'My subscription', function () { show('subscription'); });
      var main = (sub.subscriptions || [])[0];
      var bits = window.gsSubBits || {};
      sd.appendChild(el('div', { class: 'vh-door__body' },
        el('span', { class: 'vh-eyebrow', text: 'My subscription' }),
        el('b', { class: 'vh-door__big', text: main ? main.plan : 'No subscription found' }),
        main && bits.badge ? bits.badge(main.status) : null,
        el('div', { class: 'vh-facts' },
          main && bits.when && bits.when(main) ? el('span', { text: bits.when(main) }) : null,
          main && bits.period ? el('span', { text: bits.period(main) }) : null,
          !main ? el('span', { class: 'vh-muted', text: 'Paying with another email? Ask the administrator to link it.' }) : null)));
      grid.appendChild(sd);
    }
    grid.dataset.count = String(grid.children.length);
    return [el('h1', { class: 'vh-hello', text: 'Hi ' + ((me() && me().name) || 'there') }), grid];
  }
  var nowBody = null;
  function fillNow() {
    var body = nowBody;
    if (!body) return;
    var list = ownStreams();
    if (list.length) {
      body.replaceChildren(nowItem(list[0]));
      if (list.length > 1) body.appendChild(el('span', { class: 'vh-more', text: '+ ' + plural(list.length - 1, 'more stream', 'more streams') }));
      return;
    }
    body.replaceChildren(el('div', { class: 'vh-now vh-now--idle' }, el('div', { class: 'vh-now__in' },
      el('span', { class: 'vh-eyebrow', text: 'Watching now' }),
      el('b', { class: 'vh-now__title', text: 'Nothing playing' }),
      el('span', { class: 'vh-muted', text: 'Start something in Plex and it shows up here within 10 seconds.' }))));
  }

  // ---------- Requests hub ----------
  var STAGES = [
    { id: 'ready', label: 'Ready to watch', shape: 'circle', filter: 'available', test: function (x) { return x.media && x.media.status === 'available'; } },
    { id: 'way', label: 'On its way', shape: 'triangle', filter: 'processing', test: function (x) { return x.status === 'approved' && !(x.media && x.media.status === 'available'); } },
    { id: 'wait', label: 'Waiting for approval', shape: 'square', filter: 'pending', test: function (x) { return x.status === 'pending'; } },
    { id: 'failed', label: 'Failed', shape: 'square', filter: 'failed', test: function (x) { return x.status === 'failed'; } }
  ];
  function shape(s) {
    if (s === 'circle') return el('span', { class: 'vh-shape vh-shape--c', 'aria-hidden': 'true' });
    if (s === 'triangle') return el('span', { class: 'vh-shape vh-shape--t', 'aria-hidden': 'true' });
    return el('span', { class: 'vh-shape vh-shape--s', 'aria-hidden': 'true' });
  }
  function poster(x, cls) {
    var href = rqLink((x.kind === 'tv' ? 'series/' : 'movie/') + (x.tmdbId || x.id));
    var a = el('a', { class: cls || 'vh-poster', href: href, 'aria-label': x.title + (x.year ? ' (' + x.year + ')' : '') });
    var src = tmdb(x.poster, 'w185');
    if (src) a.appendChild(el('img', { src: src, alt: '', loading: 'lazy' }));
    if (x.status === 'available') a.appendChild(el('span', { class: 'vh-onplex' }, shape('circle'), 'On Plex'));
    return a;
  }
  function hub() {
    var stages = el('div', { class: 'vh-stages' });
    var trend = el('div', { class: 'vh-shelf' });
    var out = [
      el('div', { class: 'vh-hubhead' },
        el('h1', { class: 'vh-hubtitle', text: 'What do you want to watch?' }),
        searchForm('vh-search--hero', 'Search movies, series and people'),
        el('nav', { class: 'vh-chips', 'aria-label': 'Browse' },
          [['Trending', rqLink('')], ['Movies', rqLink('movies')], ['Series', rqLink('tv')], ['Watchlist', rqLink('watchlist')], ['My requests', rqLink('mine')]].map(function (c) {
            return el('a', { class: 'vh-chip', href: c[1], text: c[0] });
          }))),
      el('div', { class: 'vh-sechead' }, el('h2', { class: 'vh-h2', text: 'Your requests' }), el('a', { class: 'vh-link', href: rqLink('mine'), text: 'See all' })),
      stages,
      el('div', { class: 'vh-sechead' }, el('h2', { class: 'vh-h2', text: 'Trending this week' }), el('a', { class: 'vh-link', href: rqLink(''), text: 'More' })),
      trend
    ];
    var ready = get('/api/requests/me', 600000).then(function (m) { if (m && m.images) images = m.images; }).catch(function () {});
    ready.then(function () {
      return get('/api/requests/mine?filter=all');
    }).then(function (d) {
      var items = (d && d.items) || [];
      var shown = STAGES.filter(function (s) { return s.id !== 'failed' || items.some(s.test); });
      stages.replaceChildren.apply(stages, shown.map(function (s) {
        var list = items.filter(s.test);
        return el('a', { class: 'vh-stage vh-stage--' + s.id, href: rqLink('mine', { filter: s.filter }) },
          el('span', { class: 'vh-stage__head' }, shape(s.shape), el('b', { text: s.label }), el('span', { class: 'vh-stage__n', text: String(list.length) })),
          el('span', { class: 'vh-stage__list' }, list.slice(0, 2).map(function (x) {
            return el('span', { class: 'vh-stage__item' }, tmdb(x.poster, 'w92') ? el('img', { src: tmdb(x.poster, 'w92'), alt: '', loading: 'lazy' }) : el('span', { class: 'vh-stage__ph' }), el('span', { text: x.title }));
          }), list.length ? null : el('span', { class: 'vh-muted vh-small', text: s.id === 'ready' ? 'Nothing new yet.' : 'None right now.' })));
      }));
      if (!items.length) stages.replaceChildren(el('p', { class: 'vh-muted', text: 'You have not requested anything yet. Search above, or pick something trending.' }));
    }).catch(function () { stages.replaceChildren(el('p', { class: 'vh-muted', text: 'Requests are not answering right now.' })); });
    ready.then(function () { return get('/api/requests/row?type=4', 3600000); }).then(function (d) {
      trend.replaceChildren.apply(trend, ((d && d.items) || []).slice(0, 12).map(function (x) {
        return el('div', { class: 'vh-shelf__item' }, poster(x), el('span', { class: 'vh-shelf__title', text: x.title }));
      }));
    }).catch(function () {});
    return out;
  }

  // ---------- My month ----------
  function month() {
    var body = el('div', { class: 'vh-month' }, el('p', { class: 'vh-muted', text: 'Loading your viewing…' }));
    var seg = el('div', { class: 'vh-seg', role: 'group', 'aria-label': 'Period' }, [['week', 'This week'], ['month', 'This month'], ['year', 'This year']].map(function (p) {
      return el('button', { type: 'button', class: 'vh-seg__b', 'aria-pressed': p[0] === period ? 'true' : 'false', text: p[1], onclick: function () { period = p[0]; render(true); } });
    }));
    var head = el('div', { class: 'vh-monthhead' }, el('div', null, el('span', { class: 'vh-eyebrow', text: 'Your viewing on Greystar' }), el('h1', { class: 'vh-hubtitle vh-monthtitle', id: 'vh-mtitle', text: '…' })), seg);
    get('/api/me/recap?period=' + period + '&tz=' + tz()).then(function (r) {
      document.getElementById('vh-mtitle').textContent = r.period === 'month' ? r.label + ', at a glance' : r.label;
      if (!r.plays) {
        body.replaceChildren(el('div', { class: 'vh-empty' }, el('b', { text: 'Nothing watched ' + (r.period === 'week' ? 'in the last 7 days' : r.period === 'year' ? 'this year' : 'this month') + ' yet' }),
          el('span', { class: 'vh-muted', text: 'Your hours, streaks and favorites show up here once you watch something.' })));
        return;
      }
      var diff = Math.round((r.hours - r.prev_hours) * 10) / 10;
      var prevName = r.period === 'week' ? 'the 7 days before' : r.period === 'year' ? 'this time last year' : 'this time last month';
      var maxH = Math.max.apply(null, r.by_hour.concat([0.0001]));
      var topMax = Math.max.apply(null, (r.top || []).map(function (t) { return t.hours || t.plays; }).concat([0.0001]));
      body.replaceChildren(
        el('div', { class: 'vh-tiles' },
          el('div', { class: 'vh-tile vh-tile--ink' }, el('span', { class: 'vh-eyebrow', text: 'Hours watched' }), el('b', { class: 'vh-tile__big', text: String(r.hours) }),
            el('span', { class: 'vh-small', text: diff === 0 ? 'Same as ' + prevName : Math.abs(diff) + ' ' + (diff > 0 ? 'more' : 'fewer') + ' than ' + prevName })),
          el('div', { class: 'vh-tile' }, el('span', { class: 'vh-eyebrow', text: 'Plays' }), el('b', { class: 'vh-tile__big', text: String(r.plays) }),
            el('span', { class: 'vh-muted vh-small', text: [r.episodes ? plural(r.episodes, 'episode', 'episodes') : null, r.movies ? plural(r.movies, 'movie', 'movies') : null].filter(Boolean).join(', ') || ' ' })),
          el('div', { class: 'vh-tile vh-tile--lime' }, el('span', { class: 'vh-eyebrow', text: 'Longest streak' }), el('b', { class: 'vh-tile__big', text: plural(r.streak.days, 'day', 'days') }),
            el('span', { class: 'vh-small', text: r.streak.days > 1 ? shortDate(r.streak.from) + ' to ' + shortDate(r.streak.to) : shortDate(r.streak.from) })),
          el('div', { class: 'vh-tile' }, el('span', { class: 'vh-eyebrow', text: 'Your prime time' }),
            el('b', { class: 'vh-tile__big vh-tile__big--sm', text: r.prime ? DAY[r.prime.weekday] + ' ' + hourText(r.prime.hour) : 'Not yet' }),
            el('span', { class: 'vh-muted vh-small', text: 'When you watch the most' }))),
        el('div', { class: 'vh-mgrid' },
          el('section', { class: 'vh-panel', 'aria-label': 'Most watched' },
            el('div', { class: 'vh-sechead' }, el('h2', { class: 'vh-h2', text: 'Most watched' }), can('history') ? el('button', { type: 'button', class: 'vh-link', text: 'Full history', onclick: function () { show('history'); } }) : null),
            el('ol', { class: 'vh-top' }, (r.top || []).map(function (t, i) {
              var thumb = el('span', { class: 'vh-top__p', 'aria-hidden': 'true' });
              if (t.poster) { var im = new Image(); im.onload = function () { thumb.style.backgroundImage = 'url("' + t.poster + '")'; }; im.src = t.poster; }
              return el('li', { class: 'vh-top__row' }, el('b', { class: 'vh-top__n', text: String(i + 1) }), thumb,
                el('span', { class: 'vh-top__t' }, el('b', { text: t.title }), el('span', { class: 'vh-muted vh-small', text: (t.kind === 'Episode' ? plural(t.plays, 'episode', 'episodes') : t.kind === 'Movie' ? 'Movie' : plural(t.plays, 'play', 'plays')) + (t.hours ? ' · ' + t.hours + ' h' : '') })),
                el('span', { class: 'vh-top__bar', 'aria-hidden': 'true' }, el('i', { style: 'width:' + Math.round(100 * (t.hours || t.plays) / topMax) + '%' })));
            }))),
          el('section', { class: 'vh-panel', 'aria-label': 'When you watch' },
            el('h2', { class: 'vh-h2', text: 'When you watch' }),
            el('div', { class: 'vh-hours', role: 'img', 'aria-label': 'Most of your watching starts around ' + (r.prime ? hourText(r.prime.hour) : 'no particular time') },
              r.by_hour.map(function (h, i) { var lvl = h <= 0 ? 0 : Math.min(4, Math.ceil(4 * h / maxH)); return el('span', { class: 'vh-hour vh-hour--' + lvl, title: hourText(i) + ': ' + h + ' h' }); })),
            el('div', { class: 'vh-hours__axis', 'aria-hidden': 'true' }, el('span', { text: '12 AM' }), el('span', { text: '6 AM' }), el('span', { text: '12 PM' }), el('span', { text: '6 PM' })),
            el('p', { class: 'vh-muted vh-small', text: 'Darker means more watching at that hour.' }))));
    }).catch(function () { body.replaceChildren(el('p', { class: 'vh-muted', text: 'Your viewing could not be loaded right now.' })); });
    return [head, body];
  }

  // ---------- Sweep in: cards fade in one at a time (the border sweep plays on hover, in home.css) ----------
  var CARDS = '.vh-door, .vh-stage, .vh-tile, .vh-panel';
  var ready = false, watch = null;
  function finishWait() { ready = true; root.classList.remove('vh-wait'); }
  document.addEventListener('gs:ready', finishWait, { once: true });
  setTimeout(finishWait, 5000);  // never leave the cards hidden if the splash event is missed
  function tidy(e) {
    var n = e.currentTarget;
    if (e.target !== n || e.animationName !== 'vh-fade') return;
    n.classList.remove('vh-in', 'vh-in-plain');
    n.style.removeProperty('--vh-d');
    n.removeEventListener('animationend', tidy);
  }
  function sweepIn() {
    if (watch) { watch.disconnect(); watch = null; }
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    var start = Date.now(), n = 0;
    function mark(node) {
      if (node.dataset.vhSwept) return;
      node.dataset.vhSwept = '1';
      var card = node.matches(CARDS);
      // cards that arrive later (the hub and My month fill in after a fetch) keep their place in the order
      var d = card ? Math.max(0, 120 + Math.min(n++, 8) * 170 - (ready ? Date.now() - start : 0)) : 0;
      node.style.setProperty('--vh-d', d + 'ms');
      node.classList.add(card ? 'vh-in' : 'vh-in-plain');
      node.addEventListener('animationend', tidy);
    }
    function scan(rootNode) {
      if (rootNode.nodeType !== 1) return;
      if (rootNode.matches('.vh-hello, ' + CARDS)) mark(rootNode);
      rootNode.querySelectorAll('.vh-hello, ' + CARDS).forEach(mark);
    }
    scan(box);
    if (!ready) { root.classList.add('vh-wait'); start = 0; document.addEventListener('gs:ready', function () { start = Date.now(); }, { once: true }); }
    if (window.MutationObserver) {
      var w = watch = new MutationObserver(function (list) { list.forEach(function (m) { m.addedNodes.forEach(scan); }); });
      w.observe(box, { childList: true, subtree: true });
      setTimeout(function () { w.disconnect(); if (watch === w) watch = null; }, 4000);
    }
  }

  // ---------- draw ----------
  function render(still) {
    apply();
    if (!active()) { box.replaceChildren(); return; }
    var lay = layout();
    box.dataset.layout = lay;
    box.replaceChildren.apply(box, [].concat(lay === 'hub' ? hub() : lay === 'month' ? month() : doors()));
    if (!still) sweepIn();
  }
  if (back) {
    back.appendChild(icon('left', 18));
    back.appendChild(document.createTextNode('Home'));
    back.addEventListener('click', function () { show('home'); });
  }
  window.gsHome = {
    active: active,
    view: view,
    layout: layout,
    show: show,
    refresh: function () { if (active() && view() === 'home') render(); else apply(); },
    layouts: LAYOUTS
  };
  var started = false;
  window.dash.onData(function () {
    if (!active()) { apply(); return; }
    if (!started) { started = true; render(); if (window.sdNavChanged) window.sdNavChanged(); return; }
    apply();
    if (view() === 'home' && layout() === 'doors') fillNow();
  });
})();
