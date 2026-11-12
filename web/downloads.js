/* Greystar Telemetry: download progress in the green Live now bar (Glenn's pick A, Nov 12 2026).

   When something this person asked for is downloading (a movie or show through Radarr/Sonarr, or a comic, ebook or
   audiobook on the media site), the lime bar at the top of every page turns into a progress bar: a darker band fills
   it as the downloads go, it says "2 downloading" (or "Four is ready" for half an hour after one finishes) with the
   titles under it, and a dark chip says "46% · 4 min left". Tapping it opens a list of each download (a bottom sheet
   on phones) with a link to My requests. Stream start/stop messages still take the bar for their few seconds first.

   Data: GET /api/requests/downloads (own requests only, plain words, cached 15 s on the server). Checked every 20 s
   while something is downloading and every 60 s otherwise, only while the tab is visible, and again right away when
   the Books frame says a request was made (gsDownloads.refresh(true)). app.js's renderNewest calls paint(box, seg).

   window.gsDownloads = { active(), paint(box, seg), refresh(fresh) } */
(function () {
  'use strict';
  var U = window.gsUI;
  var el = U.el;
  var FAST_MS = 20000, SLOW_MS = 60000;
  var DISMISS_KEY = 'gs-dl-seen';
  var items = [], more = 0, timer = null, loading = false, started = false;
  var tray = null, scrim = null, detach = null, opener = null;
  var announced = {};
  var live = el('div', { class: 'sd-dl-sr', role: 'status', 'aria-live': 'polite' });
  document.body.appendChild(live);

  var svgNS = 'http://www.w3.org/2000/svg';
  function icon(d, size) {
    var s = document.createElementNS(svgNS, 'svg');
    s.setAttribute('width', size || 18); s.setAttribute('height', size || 18); s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '2');
    s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
    var p = document.createElementNS(svgNS, 'path'); p.setAttribute('d', d); s.appendChild(p);
    return s;
  }
  var DOWN = 'M12 4v12M6 11l6 6 6-6M5 20h14';
  var CHECK = 'M5 12.5l4.5 4.5L19 7';

  function can() { return !!(window.dash && window.dash.can && window.dash.can('requests')); }

  // A ready item stops showing once the person has opened it from the bar (this browser tab only).
  function seen() { try { return JSON.parse(window.sessionStorage.getItem(DISMISS_KEY) || '[]'); } catch (e) { return []; } }
  function markSeen(key) {
    try { var s = seen(); if (s.indexOf(key) < 0) { s.push(key); window.sessionStorage.setItem(DISMISS_KEY, JSON.stringify(s.slice(-50))); } }
    catch (e) { /* storage blocked */ }
  }
  function shown() {
    var s = seen();
    return items.filter(function (x) { return !(x.ready && s.indexOf(x.key) >= 0); });
  }

  function active() { return shown().length > 0; }

  var KIND = { movie: 'Movie', tv: 'Series', comic: 'Comic', ebook: 'Ebook', audiobook: 'Audiobook' };
  var TINT = { movie: '#fcccd4', tv: '#f0e4fd', comic: '#fcecc8', ebook: '#bac4ed', audiobook: '#d4f0f5' };
  function short(t) { return String(t || '').split(/:\s/)[0]; }
  function left(m) {
    if (m == null) return '';
    if (m < 1) return 'under a minute';
    if (m < 60) return Math.round(m) + ' min left';
    var h = Math.floor(m / 60), r = Math.round(m % 60);
    return h + ' h' + (r ? ' ' + r + ' min' : '') + ' left';
  }
  function letters(t) { return String(t || '?').replace(/^(the|a|an)\s+/i, '').slice(0, 2).toUpperCase(); }

  function summary() {
    var list = shown();
    var going = list.filter(function (x) { return !x.ready; });
    var ready = list.filter(function (x) { return x.ready; });
    var known = going.filter(function (x) { return typeof x.pct === 'number'; });
    var pct = going.length ? (known.length ? Math.round(known.reduce(function (a, x) { return a + x.pct; }, 0) / known.length) : null) : 100;
    var mins = going.reduce(function (m, x) { return x.minutes != null && x.minutes > m ? x.minutes : m; }, -1);
    var head, lead, chip;
    if (ready.length) {
      head = short(ready[0].title) + ' is ready';
      lead = going.length ? going.length + ' more downloading' : (ready.length > 1 ? ready.length + ' ready to open' : 'Tap to open');
    } else {
      head = going.length === 1 ? 'Downloading' : going.length + ' downloading';
      lead = going.map(function (x) { return x.title; }).join(', ');
    }
    if (going.length) chip = (pct == null ? going[0].word : pct + '%') + (mins >= 0 ? ' · ' + left(mins) : '');
    else chip = 'Open';
    return { list: list, going: going, ready: ready, pct: pct == null ? 0 : pct, head: head, lead: lead, chip: chip };
  }

  /** Called by app.js's renderNewest. Fills the bar and returns true when there is something to show. */
  function paint(box, seg) {
    if (!active()) return false;
    var s = summary();
    box.classList.add('is-dl');
    box.style.setProperty('--dl-pct', s.pct + '%');
    var btn = el('button', { type: 'button', class: 'sd-dlbar', 'aria-haspopup': 'dialog', 'aria-expanded': tray ? 'true' : 'false',
      'aria-label': s.head + '. ' + (s.going.length ? s.chip + '. ' : '') + 'Show your downloads',
      onclick: function () { if (tray) close(true); else openTray(btn); } },
      el('span', { class: 'sd-dlbar__icon' }, icon(s.ready.length ? CHECK : DOWN)),
      el('span', { class: 'sd-dlbar__text' }, el('b', { text: s.head }), el('span', { text: s.lead })),
      el('span', { class: 'sd-dlbar__chip', text: s.chip }));
    box.appendChild(btn);
    // A server that is down still says so at the end of the bar; "all up" makes room for the downloads.
    if (seg && seg.classList.contains('is-down')) box.appendChild(seg);
    return true;
  }

  function row(x) {
    var status = x.ready ? 'Ready' : (typeof x.pct === 'number' ? x.pct + '%' : x.word) + (x.minutes != null ? ' · ' + left(x.minutes) : '');
    var bar = el('span', { class: 'sd-dlrow__bar', 'aria-hidden': 'true' }, el('span', { style: 'width:' + (x.ready ? 100 : (x.pct || 0)) + '%' }));
    return el('a', { class: 'sd-dlrow' + (x.ready ? ' is-ready' : ''), href: x.open || '#/requests/mine',
      onclick: function () { if (x.ready) markSeen(x.key); close(false); setTimeout(redraw, 0); } },
      el('span', { class: 'sd-dlrow__tile', style: 'background:' + (TINT[x.kind] || '#d4f0f5'), 'aria-hidden': 'true', text: letters(x.title) }),
      el('span', { class: 'sd-dlrow__text' },
        el('b', { text: x.title }),
        el('span', { text: (KIND[x.kind] || 'Request') + ' · ' + (x.ready ? 'Ready to open' : (x.word || '')) }),
        bar),
      el('span', { class: 'sd-dlrow__pct' + (x.ready ? ' is-ready' : ''), text: x.ready ? 'Open' : (typeof x.pct === 'number' ? x.pct + '%' : '') }),
      el('span', { class: 'sd-dl-sr', text: ' ' + status }));
  }

  function fillTray() {
    if (!tray) return;
    var s = summary();
    tray.replaceChildren(
      el('div', { class: 'sd-dltray__head' }, el('h2', { id: 'dl-tray-title', text: 'Your downloads' }),
        el('button', { type: 'button', class: 'sd-dltray__close', 'aria-label': 'Close', onclick: function () { close(true); } }, icon('M6 6l12 12M18 6 6 18', 16))),
      el('div', { class: 'sd-dltray__list' }, s.list.map(row)),
      more ? el('p', { class: 'sd-dltray__more', text: more + ' more in My requests' }) : null,
      el('a', { class: 'sd-dltray__all', href: '#/requests/mine', onclick: function () { close(false); } }, 'Open My requests'));
  }

  function place() {
    if (!tray || U.isPhone() || !opener) return;
    var r = (opener.closest('.sd-pill-lime') || opener).getBoundingClientRect();
    tray.style.top = Math.round(r.bottom + 8) + 'px';
    tray.style.left = Math.round(Math.max(12, r.left)) + 'px';
    tray.style.width = Math.round(Math.max(360, Math.min(r.width, 480))) + 'px';
  }

  function outside(e) {
    if (!tray) return;
    if (tray.contains(e.target) || (opener && opener.contains(e.target))) return;
    if (scrim && e.target === scrim) return;
    close(false);
  }
  function onKey(e) { if (e.key === 'Escape' && tray) { e.stopPropagation(); close(true); } }

  function openTray(btn) {
    opener = btn;
    tray = el('div', { class: 'sd-dltray', role: 'dialog', 'aria-labelledby': 'dl-tray-title' });
    fillTray();
    var host = document.getElementById('streams-app') || document.body;
    if (U.isPhone()) {
      scrim = el('div', { class: 'us-scrim' });
      host.append(scrim, tray);
      detach = U.sheet(tray, { onClose: function () { close(true); }, scrim: scrim });
    } else {
      host.appendChild(tray);
      place();
      detach = U.sheet(tray, { desktop: 'menu' });
      window.addEventListener('resize', place);
      window.addEventListener('scroll', place, true);
    }
    document.addEventListener('mousedown', outside, true);
    document.addEventListener('keydown', onKey, true);
    btn.setAttribute('aria-expanded', 'true');
    var first = tray.querySelector('.sd-dlrow, .sd-dltray__all');
    if (first) first.focus();
  }

  function close(focusBack) {
    if (!tray) return;
    document.removeEventListener('mousedown', outside, true);
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('resize', place);
    window.removeEventListener('scroll', place, true);
    if (detach) { detach(); detach = null; }
    tray.remove(); tray = null;
    if (scrim) { scrim.remove(); scrim = null; }
    var btn = document.querySelector('.sd-dlbar');
    if (btn) { btn.setAttribute('aria-expanded', 'false'); if (focusBack) btn.focus(); }
  }

  function redraw() { if (window.sdRedrawNewest) window.sdRedrawNewest(); }

  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

  function refresh(fresh) {
    if (!can() || loading) return;
    if (document.hidden && !fresh) return;
    loading = true;
    U.api('GET', '/api/requests/downloads' + (fresh ? '?fresh=1' : '')).then(function (body) {
      var next = (body && Array.isArray(body.items)) ? body.items : [];
      // Say it once when something becomes ready while the page is open.
      next.forEach(function (x) {
        var was = items.filter(function (y) { return y.key === x.key; })[0];
        if (x.ready && was && !was.ready && !announced[x.key]) { announced[x.key] = true; live.textContent = x.title + ' is ready.'; }
      });
      var changed = !same(next, items) || (body && body.more) !== more;
      items = next; more = (body && body.more) || 0;
      if (changed) { redraw(); fillTray(); }
    }).catch(function () { /* keep what we had */ }).then(function () { loading = false; schedule(); });
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(function () { refresh(false); }, items.some(function (x) { return !x.ready; }) ? FAST_MS : SLOW_MS);
  }

  function start() {
    if (started || !can()) return;
    started = true;
    refresh(false);
  }
  document.addEventListener('gs:me', start);
  document.addEventListener('gs:ready', start);
  document.addEventListener('visibilitychange', function () { if (!document.hidden && started) refresh(false); });
  window.addEventListener('hashchange', function () { close(false); });
  if (window.dash && window.dash.can) setTimeout(start, 0);

  window.gsDownloads = { active: active, paint: paint, refresh: refresh };
})();
