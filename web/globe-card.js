/* Live streams globe card (admins; #globe-card in index.html, next to When people watch). Asks GET /api/globe every
   10 s while the card is on screen and the tab is visible, and hands each new stream start to the globe (web/globe.js).
   The reply only has a random id, the time, a city label and the city's centre: never a name or an IP (app/globe.py).
   - Page load: the first call has no cursor and gets no events, so streams already playing do not ping.
   - Coming back to a hidden tab, or to the card after 25 s away: what started meanwhile only updates the count.
   - At most 8 pings per batch (after a server restart there could be dozens); the count still has them all. */
(function () {
  'use strict';
  var card = document.getElementById('globe-card');
  if (!card) return;
  var canvas = card.querySelector('canvas');
  var num = document.getElementById('globe-n');
  var note = document.getElementById('globe-note');
  var latest = document.getElementById('globe-latest');
  var root = document.getElementById('streams-app');
  var EVERY_MS = 10000, MAX_PINGS = 8;
  var globe = null, cursor = null, timer = 0, busy = false, missed = false, lastOk = 0;

  function shown() {
    if (!(window.dash && window.dash.isAdmin && window.dash.isAdmin())) return false;
    if (window.gsCards && window.gsCards.off('globe')) return false;
    return card.offsetParent !== null;  // hidden on this phone tab or page
  }
  function mount() {
    if (globe) return;
    globe = window.gsGlobe.create(canvas, {
      avoid: [card.querySelector('.sd-card__head'), card.querySelector('.sd-globe__cap'), card.querySelector('.sd-notch')].filter(Boolean),
      clear: [card.querySelector('.sd-card-title'), card.querySelector('.sd-card-note'), card.querySelector('.sd-globe__note')].filter(Boolean)
    });
  }
  function paint(j) {
    if (num) num.textContent = String(j.last_hour);
    if (note) {
      var g = j.geo || {};
      note.hidden = !!(g.ok || j.sample);
      note.textContent = g.ok ? '' : 'City lookups are not ready yet, so new streams are counted but not shown.';
    }
  }
  function poll() {
    clearTimeout(timer);
    if (document.hidden || !shown()) { timer = setTimeout(poll, EVERY_MS); return; }
    mount();
    if (busy) return;
    busy = true;
    var url = '/api/globe' + (cursor == null ? '' : '?after=' + cursor);
    window.gsUI.api('GET', url).then(function (j) {
      var first = cursor == null;
      // Away for a while (another phone tab, the card scrolled off): what started meanwhile is old news.
      if (lastOk && Date.now() - lastOk > 25000) missed = true;
      lastOk = Date.now();
      cursor = j.cursor;
      paint(j);
      if (first || missed) { missed = false; return; }
      var evs = (j.events || []).slice(-MAX_PINGS);
      evs.forEach(function (e) { globe.ping(e); });
      if (evs.length && latest) latest.textContent = 'Latest new stream: ' + evs[evs.length - 1].label + '.';
    }).catch(function () { /* try again next round */ }).then(function () {
      busy = false;
      timer = setTimeout(poll, EVERY_MS);
    });
  }
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) missed = true; else poll();
  });
  // Light, Dark or Match device changed: redraw in the new ink.
  new MutationObserver(function () { if (globe) globe.retheme(); }).observe(root, { attributes: true, attributeFilter: ['data-look', 'data-mtab', 'class'] });
  try { window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () { if (globe) globe.retheme(); }); } catch (e) { /* old browser */ }

  window.gsGlobeCard = {
    /** Called on every dashboard redraw: start when the card first shows. */
    refresh: function () { if (shown() && cursor == null && !busy) poll(); else if (globe && shown()) globe.retheme(); }
  };
  poll();
})();
