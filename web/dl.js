/* Download progress and Unwatched cleanup, as members see them (requests.js and recent.js use these).
   - Progress comes from GET /api/requests/progress (Radarr/Sonarr queue and import state in plain words; never release
     names, indexers or the download client). Movies: a big percent, the numbers, and six steps. Shows: every episode
     of the requested seasons.
   - The clock comes from GET /api/requests/clocks (or `clock` on a row): days left before a title nobody started is
     removed. Never who asked.
   - agreeBox: the "removed if not watched within 14 days" tick box every movie or show request needs.
   Status is always a word plus a shape. */
(function () {
  'use strict';
  var el = window.gsUI.el;

  function fmtBytes(b) {
    b = Number(b) || 0;
    if (b >= 1e12) return (b / 1e12).toFixed(1) + ' TB';
    if (b >= 1e9) return (b / 1e9).toFixed(1) + ' GB';
    if (b >= 1e6) return Math.round(b / 1e6) + ' MB';
    return b ? Math.round(b / 1e3) + ' KB' : '0 GB';
  }
  function fmtMin(m) {
    if (m == null) return null;
    if (m < 1) return 'under a minute left';
    if (m < 60) return 'about ' + m + ' min left';
    var h = Math.floor(m / 60), r = m % 60;
    if (h >= 24) return 'about ' + Math.round(h / 24) + (Math.round(h / 24) === 1 ? ' day' : ' days') + ' left';
    return 'about ' + h + ' h' + (r ? ' ' + r + ' min' : '') + ' left';
  }
  function fmtSpeed(bps) { return bps ? (bps >= 1e6 ? (bps / 1e6).toFixed(1) + ' MB/s' : Math.round(bps / 1e3) + ' KB/s') : null; }
  function day(t, withYear) {
    if (!t) return '';
    var d = new Date(typeof t === 'number' ? (t > 1e11 ? t : t * 1000) : t);
    return isNaN(d) ? '' : d.toLocaleDateString(undefined, withYear ? { month: 'short', day: 'numeric', year: 'numeric' } : { weekday: 'short', month: 'short', day: 'numeric' });
  }
  // A release day as Radarr gives it ("2026-11-24"): read as that calendar day here, never shifted by time zone.
  function cal(s) { var p = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || ''); return p ? new Date(+p[1], +p[2] - 1, +p[3]) : null; }
  function today0() { var n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate()); }
  function daysFrom(s) { var d = cal(s); return d ? Math.round((d - today0()) / 864e5) : null; }   // 0 today, 1 tomorrow, -2 two days ago
  function calText(s, how) {
    var d = cal(s);
    if (!d) return '';
    var other = d.getFullYear() !== new Date().getFullYear();   // another year: always say which
    var o = how === 'long' ? { weekday: 'long', month: 'short', day: 'numeric' } : how === 'year' ? { month: 'short', day: 'numeric', year: 'numeric' } : { month: 'short', day: 'numeric' };
    if (other) o.year = 'numeric';
    return d.toLocaleDateString(undefined, o);
  }
  // "Coming tomorrow", "Coming Nov 24", "Coming soon": one short word for a title that is not out yet (badges, buttons).
  function comingWord(x) {
    var home = x && x.dates && x.dates.home, n = daysFrom(home);
    if (!home || n == null || n < 0) return 'Coming soon';
    return n === 0 ? 'Out today' : n === 1 ? 'Coming tomorrow' : 'Coming ' + calText(home);
  }
  // When a step happened or started: the time if today, else the day.
  function when(t) {
    if (!t) return '';
    var d = new Date(typeof t === 'number' ? (t > 1e11 ? t : t * 1000) : t);
    if (isNaN(d)) return '';
    return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
      : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  function clock(t) {
    if (!t) return '';
    var d = new Date(typeof t === 'number' ? (t > 1e11 ? t : t * 1000) : t);
    return isNaN(d) ? '' : d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }
  function shape(s) { return el('span', { class: 'dl-shape dl-shape--' + (s || 'square'), 'aria-hidden': 'true' }); }
  function pill(word, tone, s) { return el('span', { class: 'dl-pill dl-pill--' + tone }, shape(s), word); }

  // stage -> [tone, shape]
  var LOOK = {
    downloading: ['volt', 'triangle'], queued: ['grey', 'square'], paused: ['amber', 'square'], retry: ['amber', 'ring'],
    importing: ['sky', 'triangle'], problem: ['danger', 'square'], imported: ['lime', 'circle'], onplex: ['lime', 'circle'],
    searching: ['grey', 'ring'], release: ['grey', 'ring']
  };
  function stagePill(x) { var l = LOOK[x.stage] || LOOK.searching; return pill(x.word || 'Looking for a copy', l[0], l[1]); }

  // ---------- the clock ----------
  var CLOCK_LOOK = { waiting: ['grey', 'square'], running: ['amber', 'ring'], due: ['amber', 'ring'], last: ['danger', 'triangle'],
    watched: ['lime', 'circle'], kept: ['lime', 'circle'], deleted: ['grey', 'square'] };
  function clockPill(c) {
    if (!c) return null;
    var l = CLOCK_LOOK[c.state] || CLOCK_LOOK.waiting;
    return pill(c.word, l[0], l[1]);
  }
  function segs(c) {
    var total = c.days || 14, left = Math.min(total, c.daysLeft == null ? total : c.daysLeft);
    var used = Math.max(0, total - left);
    var row = el('span', { class: 'dl-segs', 'aria-hidden': 'true' });
    for (var i = 0; i < total; i++) row.appendChild(el('i', { class: i < used ? 'is-used' : '' }));
    return row;
  }
  // The countdown under a title (and in a My requests row).
  function clockNote(c, kind, compact) {
    if (!c) return null;
    var days = c.days || 14;
    var it = kind === 'tv' ? 'an episode' : 'it';
    if (c.state === 'waiting') {
      return el('p', { class: 'dl-cnote' }, shape('square'), 'When it arrives on Plex, someone has ' + days + ' days to start watching ' + it + ', or it is removed to save space.');
    }
    if (c.state === 'deleted') {
      return el('p', { class: 'dl-cnote' }, shape('square'), 'Removed ' + day(c.deletedAt) + ' because nobody watched it within ' + days + ' days. You can request it again.');
    }
    if (c.state === 'watched' || c.state === 'kept') return null;
    var by = c.deleteAt ? day(c.deleteAt) : '';
    var text = c.state === 'last'
      ? 'Last day: it is removed tonight at ' + clock(c.deleteAt) + ' unless someone starts ' + it + '.'
      : 'Nobody has started ' + it + ' yet. If no one does by ' + by + ', it is removed from Plex to save space. Watching any of it keeps it for good.';
    if (compact) return el('span', { class: 'dl-cnote dl-cnote--row' }, segs(c), el('span', { text: text }));
    var big = c.state === 'last' ? 'Last day' : (c.daysLeft === 1 ? '1 day' : c.daysLeft + ' days');
    return el('div', { class: 'dl-count', role: 'note' },
      el('div', { class: 'dl-count__head' }, el('b', { class: 'dl-count__big', text: big }), el('span', { text: c.state === 'last' ? 'to start watching' : 'left for someone to start watching' })),
      segs(c),
      el('p', { class: 'dl-count__text', text: (c.availableAt ? 'It arrived on ' + day(c.availableAt) + '. ' : '') + text }));
  }

  // ---------- the tick box ----------
  function agreeBox(days, kind, title, onChange) {
    var id = 'dl-agree-' + Math.random().toString(36).slice(2, 8);
    var box = el('input', { type: 'checkbox', id: id, class: 'dl-agree__box' });
    var wrap = el('div', { class: 'dl-agree' },
      el('div', { class: 'dl-agree__head' }, el('span', { class: 'dl-agree__icon', 'aria-hidden': 'true' }, window.sdIcon ? window.sdIcon('clock', 18) : ''),
        el('div', null, el('b', { text: 'Watch ' + (kind === 'tv' ? 'it' : 'it') + ' within ' + days + ' days, or it is removed' }),
          el('p', { text: kind === 'tv'
            ? 'To save space, requested seasons that nobody starts within ' + days + ' days of arriving on Plex are removed. The days start when they arrive, not today. Anyone watching keeps them.'
            : 'To save space, a requested movie that nobody starts watching within ' + days + ' days of arriving on Plex is removed. The days start when it arrives, not today. Anyone watching it keeps it.' }))),
      el('label', { class: 'dl-agree__label', for: id }, box,
        el('span', { text: 'I understand ' + (title || 'it') + ' will be removed if it is not watched within ' + days + ' days' })));
    box.addEventListener('change', function () { wrap.classList.toggle('is-on', box.checked); if (onChange) onChange(box.checked); });
    return { el: wrap, checked: function () { return box.checked; } };
  }

  // ---------- a movie's download (in the title's hero) ----------
  function facts(x) {
    var out = [];
    if (x.size) out.push(el('b', { text: fmtBytes(x.done || 0) + ' of ' + fmtBytes(x.size) }));
    var sp = fmtSpeed(x.speed); if (sp) out.push(el('span', { text: sp }));
    if (x.quality) out.push(el('span', { text: x.quality }));
    return out;
  }
  function steps(list) {
    return el('ol', { class: 'dl-steps' }, (list || []).map(function (s) {
      return el('li', { class: 'dl-step is-' + s.state, 'aria-current': s.state === 'now' ? 'step' : null },
        el('i', { class: 'dl-step__bar', 'aria-hidden': 'true' }),
        el('span', { class: 'dl-step__name' }, shape(s.state === 'done' ? 'circle' : s.state === 'now' ? 'triangle' : 'ring'), s.label),
        el('span', { class: 'dl-step__at', text: s.state === 'done' ? (when(s.at) || 'Done') : s.state === 'now' ? (s.at ? 'since ' + when(s.at) : '') : '' }));
    }));
  }
  function problemBox(x, opts) {
    var b = null;
    if (opts && opts.onNotify) {
      b = el('button', { type: 'button', class: 'rq-btn rq-btn--sm', disabled: opts.notified ? true : null }, opts.notified ? 'Admin notified' : 'Notify admin');
      b.addEventListener('click', function () { b.disabled = true; opts.onNotify().then(function () { b.replaceChildren('Admin notified'); }, function () { b.disabled = false; }); });
    }
    return el('div', { class: 'dl-problem', role: 'status' }, el('b', { text: 'It could not be added to Plex on its own.' }),
      el('span', { text: (x.problem || '') + ' The admin will sort it out.' }), b);
  }
  // Not out yet: a simple card with the dates instead of the steps (Glenn's pick, Nov 23 2026). opts.sameAs: the 4K copy
  // whose dates match the standard copy's, said in one line.
  function releaseCard(x, opts) {
    var d = x.dates || {}, home = d.home, n = daysFrom(home);
    var box = el('div', { class: 'dl-movie dl-rel', role: 'group', 'aria-label': 'Release' });
    var right = home ? 'At home ' + calText(home, 'year') : d.cinema && daysFrom(d.cinema) > 0 ? 'In cinemas ' + calText(d.cinema, 'year') : '';
    box.appendChild(el('div', { class: 'dl-movie__head' }, pill('Not out yet', 'grey', 'ring'), el('span', { class: 'dl-movie__eta', text: right })));
    if (opts && opts.sameAs) {
      box.appendChild(el('p', { class: 'dl-rel__same', text: 'Same dates as the standard copy. We will look for a 4K copy on release day too.' }));
      return box;
    }
    var tile, lead, more;
    if (home && n != null && n >= 0) {
      var c = cal(home);
      tile = el('div', { class: 'dl-cal', 'aria-hidden': 'true' }, el('small', { text: c.toLocaleDateString(undefined, { month: 'short' }).toUpperCase() }), el('b', { text: String(c.getDate()) }));
      lead = n === 0 ? 'Out to watch at home today.' : n === 1 ? 'Coming to watch at home tomorrow, ' + calText(home, 'long') + '.' : 'Coming to watch at home ' + calText(home, 'long') + '.';
      more = 'We\u2019ll start looking for a copy automatically on release day. You\u2019ll get an alert when it\u2019s ready to watch.';
    } else {
      tile = el('div', { class: 'dl-cal dl-cal--tba', 'aria-hidden': 'true' }, el('small', { text: 'TBA' }), el('b', { text: '?' }));
      var cin = d.cinema ? daysFrom(d.cinema) : null;
      lead = home ? 'Waiting to be released.' : d.cinema ? 'No at-home date announced yet.' : 'No release date yet.';
      more = d.cinema && cin != null
        ? (cin > 0 ? 'It opens in cinemas ' + calText(d.cinema, 'long') + '. ' : 'It has been in cinemas since ' + calText(d.cinema) + '. ')
          + 'Films usually reach streaming 1 to 3 months later. We\u2019ll start looking automatically as soon as it\u2019s out, and you\u2019ll get an alert when it\u2019s ready.'
        : 'We\u2019ll look for it as soon as it\u2019s out, and you\u2019ll get an alert when it\u2019s ready.';
    }
    box.appendChild(el('div', { class: 'dl-rel__body' }, tile, el('p', null, el('b', { text: lead }), el('br'), document.createTextNode(more))));
    var cells = [['In cinemas', d.cinema], ['Digital (stream / buy)', d.digital], ['Blu-ray / DVD', d.physical]];
    if (d.cinema || d.digital || d.physical) {
      var next = [d.digital, d.physical].filter(function (z) { return z && daysFrom(z) >= 0; }).sort()[0];
      box.appendChild(el('div', { class: 'dl-dates' }, cells.map(function (c) {
        var k = daysFrom(c[1]);
        var tag = !c[1] ? null : c[0] === 'In cinemas' ? (k <= 0 ? 'In cinemas now' : null) : k === 0 ? 'Today' : k === 1 ? 'Tomorrow' : k < 0 ? 'Out' : null;
        return el('div', { class: 'dl-date' + (c[1] && c[1] === next ? ' is-next' : '') },
          el('span', { text: c[0] }), c[1] ? el('b', { text: calText(c[1], 'year') }) : el('b', { class: 'is-none', text: 'Not announced' }),
          tag ? el('em', { text: tag }) : null);
      })));
    }
    return box;
  }
  // Released and approved, nothing grabbed yet: what to expect, by how long it has been out.
  function searchNote(x) {
    var n = x.outDay ? daysFrom(x.outDay) : null;
    var last = x.lastSearch ? ' Last checked ' + when(x.lastSearch) + '.' : '';
    if (n === 0) return 'It came out today. Copies usually show up within a day or two; we\u2019ll grab one as soon as it does.' + last;
    if (n != null && n >= -3) return 'It came out ' + calText(x.outDay) + '. Copies usually show up within a day or two; we\u2019ll grab one as soon as it does.' + last;
    return 'We\u2019re still looking for a good copy. This can take a while for new or hard-to-find titles.' + last;
  }
  function movieBlock(x, opts) {
    if (!x || x.error) return null;
    if (x.onPlex) return null;
    if (x.stage === 'release') return releaseCard(x, opts);
    var box = el('div', { class: 'dl-movie', role: 'group', 'aria-label': 'Download' });
    if (['downloading', 'queued', 'paused', 'retry', 'importing', 'problem'].indexOf(x.stage) >= 0) {
      var pct = x.stage === 'importing' || x.stage === 'problem' ? 100 : (x.pct || 0);
      box.append(
        el('div', { class: 'dl-movie__head' },
          x.stage === 'downloading' ? el('div', { class: 'dl-movie__pct' }, el('b', { text: pct + '%' }), el('span', { text: 'downloaded' })) : stagePill(x),
          el('span', { class: 'dl-movie__eta', text: x.stage === 'importing' ? 'Adding it to Plex' : fmtMin(x.minutes) || '' })),
        el('div', { class: 'dl-bar dl-bar--' + x.stage, role: 'progressbar', 'aria-valuenow': String(pct), 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-label': x.word },
          el('i', { style: 'width:' + pct + '%' })),
        el('div', { class: 'dl-movie__facts' }, facts(x)));
    } else {
      var outN = x.outDay ? daysFrom(x.outDay) : null;
      box.append(el('div', { class: 'dl-movie__head' }, x.stage === 'searching' ? pill('Searching', 'sky', 'square') : stagePill(x),
        el('span', { class: 'dl-movie__eta', text: x.stage === 'searching' ? (outN === 0 ? 'Out today \u00b7 ' + calText(x.outDay) : x.outDay ? 'Out since ' + calText(x.outDay, 'year') : '')
          : x.stage === 'imported' ? 'On Plex in a moment' : '' })));
    }
    box.appendChild(steps(x.steps));
    if (x.stage === 'problem') box.appendChild(problemBox(x, opts));
    box.appendChild(el('p', { class: 'dl-movie__note', text: x.stage === 'searching' ? searchNote(x)
      : 'Updated ' + clock(Date.now()) + '. Checks again every 15 seconds while this page is open.' }));
    return box;
  }

  // ---------- a show's episodes ----------
  // keep: an object kept across refreshes, so the picked season survives the 15-second update.
  function showPanel(x, keep) {
    keep = keep || {};
    var sh = x && x.show;
    if (!sh || !sh.seasons || !sh.seasons.length) return null;
    var c = sh.counts || {};
    var done = c.onplex || 0;
    // Episode stages, their words and tints come from the server (app/request_state.py, `episodes`).
    var parts = (window.gsRqWords && window.gsRqWords().episodes) || [];
    var bar = el('span', { class: 'dl-split', 'aria-hidden': 'true' }, parts.map(function (p) {
      return c[p[0]] ? el('i', { class: 'dl-split--' + p[2], style: 'flex:' + c[p[0]] + ' 1 0' }) : null;
    }));
    var legend = el('ul', { class: 'dl-legend' }, parts.filter(function (p) { return c[p[0]]; }).map(function (p) {
      return el('li', null, el('i', { class: 'dl-split--' + p[2], 'aria-hidden': 'true' }), p[1] + ' ' + c[p[0]]);
    }));
    var cur = sh.seasons.some(function (z) { return z.number === keep.season; }) ? keep.season : sh.seasons[0].number;
    var tabs = el('div', { class: 'rq-chips', role: 'tablist', 'aria-label': 'Season' });
    var list = el('ol', { class: 'dl-eps' });
    function paint() {
      tabs.replaceChildren();
      sh.seasons.forEach(function (s) {
        var have = s.episodes.filter(function (e) { return e.stage === 'onplex' || e.stage === 'imported'; }).length;
        tabs.appendChild(el('button', { type: 'button', class: 'rq-chip', role: 'tab', 'aria-selected': s.number === cur ? 'true' : 'false', 'aria-pressed': s.number === cur ? 'true' : 'false',
          onclick: function () { cur = keep.season = s.number; paint(); } }, 'Season ' + s.number, el('em', { text: ' ' + have + ' of ' + s.episodes.length })));
      });
      var s = sh.seasons.filter(function (z) { return z.number === cur; })[0];
      list.replaceChildren.apply(list, s.episodes.map(function (e) {
        var sub = e.stage === 'downloading' ? [e.size ? fmtBytes(e.done) + ' of ' + fmtBytes(e.size) : null, fmtMin(e.minutes)].filter(Boolean).join(' · ')
          : e.stage === 'release' ? (e.release ? 'Airs ' + day(e.release, true) : 'Not aired yet')
          : e.stage === 'problem' ? e.problem : e.stage === 'importing' ? 'Downloaded, adding to Plex' : e.stage === 'onplex' ? 'Ready to watch' : e.stage === 'queued' ? 'Waiting its turn' : '';
        return el('li', { class: 'dl-ep is-' + e.stage },
          el('span', { class: 'dl-ep__n', text: String(e.n) }),
          el('span', { class: 'dl-ep__main' }, el('b', { text: e.title }),
            e.stage === 'downloading' ? el('span', { class: 'dl-bar dl-bar--thin', 'aria-hidden': 'true' }, el('i', { style: 'width:' + (e.pct || 0) + '%' })) : null,
            sub ? el('span', { class: 'dl-ep__sub', text: sub }) : null),
          stagePill(e));
      }));
    }
    paint();
    return el('section', { class: 'rq-panel dl-show', 'aria-label': 'Episodes on the way' },
      el('div', { class: 'dl-show__head' },
        el('h2', { class: 'rq-h2', text: done + ' of ' + sh.total + ' episodes on Plex' }),
        el('span', { class: 'rq-muted', text: sh.minutes != null && done < sh.total ? 'The rest in ' + (fmtMin(sh.minutes) || '').replace(' left', '') : '' })),
      bar, legend, tabs, list);
  }

  // ---------- a compact line for My requests ----------
  function rowLine(x, opts) {
    if (!x || x.error) return null;
    if (x.kind === 'tv') {
      var sh = x.show;
      if (!sh || !sh.total) return null;
      var c = sh.counts || {};
      if (c.onplex === sh.total) return null;
      var word = (c.onplex || 0) + ' of ' + sh.total + ' episodes on Plex';
      var busy = (c.downloading || 0) + (c.importing || 0);
      return el('span', { class: 'dl-row' },
        el('span', { class: 'dl-row__top' }, pill(word, busy ? 'sky' : 'grey', busy ? 'triangle' : 'ring'),
          el('span', { class: 'dl-row__right', text: busy ? (c.downloading || 0) + ' downloading' + (sh.minutes != null ? ', ' + fmtMin(sh.minutes) : '') : c.release ? c.release + ' not out yet' : '' })),
        el('span', { class: 'dl-split dl-split--thin', 'aria-hidden': 'true' }, [['onplex', 'lime'], ['importing', 'sky'], ['downloading', 'volt'], ['queued', 'grey']].map(function (p) {
          return c[p[0]] ? el('i', { class: 'dl-split--' + p[1], style: 'flex:' + c[p[0]] + ' 1 0' }) : null;
        }), el('i', { class: 'dl-split--empty', style: 'flex:' + ((c.searching || 0) + (c.release || 0) + (c.problem || 0)) + ' 1 0' })));
    }
    if (x.onPlex) return null;
    var pct = x.stage === 'importing' || x.stage === 'problem' ? 100 : x.pct || 0;
    var right = x.stage === 'downloading' ? fmtMin(x.minutes) : x.stage === 'importing' ? 'adding to Plex' : x.stage === 'release' ? (x.dates && x.dates.home ? 'out ' + calText(x.dates.home, 'year') : x.release ? 'out ' + day(x.release, true) : '')
      : x.stage === 'searching' && x.lastSearch ? 'looked ' + clock(x.lastSearch) : '';
    var line = el('span', { class: 'dl-row' },
      el('span', { class: 'dl-row__top' }, stagePill(x), right ? el('span', { class: 'dl-row__right', text: right }) : null),
      ['downloading', 'importing', 'problem', 'paused', 'queued'].indexOf(x.stage) >= 0 ? el('span', { class: 'dl-bar dl-bar--thin dl-bar--' + x.stage, 'aria-hidden': 'true' }, el('i', { style: 'width:' + pct + '%' })) : null,
      x.stage === 'downloading' && x.size ? el('span', { class: 'dl-row__facts', text: [fmtBytes(x.done) + ' of ' + fmtBytes(x.size), fmtSpeed(x.speed), x.quality].filter(Boolean).join(' · ') }) : null);
    if (x.stage === 'problem') line.appendChild(problemBox(x, opts));
    return line;
  }

  window.gsDl = { comingWord: comingWord, pill: pill, shape: shape, clockPill: clockPill, clockNote: clockNote, agreeBox: agreeBox, movieBlock: movieBlock,
    showPanel: showPanel, rowLine: rowLine, fmtBytes: fmtBytes, fmtMin: fmtMin, day: day, clock: clock };
})();
