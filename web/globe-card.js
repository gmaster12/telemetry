/* Live streams globe card (admins; #globe-card in index.html, next to When people watch). Asks GET /api/globe every
   10 s while the card is on screen and the tab is visible, and hands each new stream start to the globe (web/globe.js).
   The reply only has a random id, the time, a city label and the city's centre: never a name or an IP (app/globe.py).
   - Page load: the first call has no cursor and gets no events, so streams already playing do not ping.
   - Coming back to a hidden tab, or to the card after 25 s away: what started meanwhile only updates the count.
   - At most 8 pings per batch (after a server restart there could be dozens); the count still has them all.
   Streams playing now (`active` in the reply): a small pulsing dot per city, coloured by stream type (blue Direct play,
   orange Direct stream, green Transcode) or by server (the Type / Server switch, saved per browser in `gs-globe-by`;
   three colours times circle, square and diamond, in Settings order so a server keeps its colour). The legend lists
   each with a count; hovering or tapping a dot shows "Toronto, CA · 2 streams · Transcode". The colours are the
   validated three-colour chart palette (all pairs pass for colour-blind eyes on both card colours); lime stays for
   new streams. */
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
  var legend = document.getElementById('globe-legend');
  var nowEl = document.getElementById('globe-now');
  var tip = document.getElementById('globe-tip');
  var byBox = card.querySelector('.sd-globe__by');
  var HUES = { light: ['42,120,214', '235,104,52', '27,175,122'], dark: ['57,135,229', '217,89,38', '25,158,112'] };
  var TYPES = [{ key: 'direct', name: 'Direct play' }, { key: 'stream', name: 'Direct stream' }, { key: 'transcode', name: 'Transcode' }];
  var SHAPES = ['circle', 'square', 'diamond'];
  var active = [], servers = [], by = 'type';
  try { by = localStorage.getItem('gs-globe-by') === 'server' ? 'server' : 'type'; } catch (e) { /* storage off */ }

  function lightCard() {
    var m = /(\d+)\D+(\d+)\D+(\d+)/.exec(getComputedStyle(canvas).color || '');
    return !m || (m[1] * 0.299 + m[2] * 0.587 + m[3] * 0.114) < 128;  // dark ink: a light card
  }
  // Categories in a fixed order, so colours follow the thing, not its count.
  function cats() {
    if (by === 'type') return TYPES.map(function (t, i) { return { key: t.key, name: t.name, hue: i, shape: 'circle' }; });
    var names = servers.slice();
    active.forEach(function (d) { if (d.server && names.indexOf(d.server) < 0) names.push(d.server); });
    return names.map(function (n, i) { return { key: n, name: n, hue: i % 3, shape: SHAPES[Math.floor(i / 3) % 3] }; });
  }
  function look(cat, light) {
    var list = cats(), c = list.filter(function (x) { return x.key === cat; })[0] || list[0] || { hue: 0, shape: 'circle' };
    return { rgb: HUES[light ? 'light' : 'dark'][c.hue], shape: c.shape };
  }
  function paintActive() {
    if (globe) globe.setActive(active.map(function (d) { return { id: d.id, label: d.label, lat: d.lat, lon: d.lon, cat: by === 'type' ? d.type : d.server }; }));
    if (nowEl) nowEl.textContent = String(active.length);
    if (!legend) return;
    var n = {}, light = lightCard(), list = cats();
    active.forEach(function (d) { var k = by === 'type' ? d.type : d.server; n[k] = (n[k] || 0) + 1; });
    legend.className = 'sd-globe__legend' + (by === 'server' && list.length > 3 ? ' sd-globe__legend--grid' : '');
    legend.setAttribute('aria-label', 'Streams playing now by ' + (by === 'type' ? 'stream type' : 'server'));
    legend.replaceChildren();
    list.forEach(function (c) {
      var li = document.createElement('li'), k = n[c.key] || 0, key = document.createElement('i');
      var name = document.createElement('span'), b = document.createElement('b');
      if (!k) li.className = 'is-zero';
      key.className = 'sd-globe__key' + (c.shape !== 'circle' ? ' sd-globe__key--' + c.shape : '');
      key.style.setProperty('--c', 'rgb(' + HUES[light ? 'light' : 'dark'][c.hue] + ')');
      key.setAttribute('aria-hidden', 'true');
      name.textContent = c.name; b.textContent = String(k);
      li.append(key, name, b);
      legend.appendChild(li);
    });
    if (byBox) Array.prototype.forEach.call(byBox.children, function (x) { x.setAttribute('aria-pressed', String(x.dataset.by === by)); });
  }
  if (byBox) byBox.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-by]');
    if (!b || b.dataset.by === by) return;
    by = b.dataset.by;
    try { localStorage.setItem('gs-globe-by', by); } catch (err) { /* storage off */ }
    paintActive();
    if (globe) globe.retheme();
  });
  function showTip(e) {
    if (!globe || !tip) return;
    var r = canvas.getBoundingClientRect(), d = globe.hit(e.clientX - r.left, e.clientY - r.top);
    if (!d) { tip.hidden = true; return; }
    var c = cats().filter(function (x) { return x.key === d.cat; })[0];
    var b = document.createElement('b'), s = document.createElement('span');
    b.textContent = d.label;
    s.textContent = d.n + (d.n === 1 ? ' stream' : ' streams') + ' \u00b7 ' + (c ? c.name : d.cat);
    tip.replaceChildren(b, s);
    tip.style.left = d.x + 'px'; tip.style.top = (d.y - 4) + 'px';
    tip.hidden = false;
  }
  canvas.addEventListener('pointermove', showTip);
  canvas.addEventListener('pointerdown', showTip);
  canvas.addEventListener('pointerleave', function () { if (tip) tip.hidden = true; });

  function shown() {
    if (!(window.dash && window.dash.isAdmin && window.dash.isAdmin())) return false;
    if (window.gsCards && window.gsCards.off('globe')) return false;
    return card.offsetParent !== null;  // hidden on this phone tab or page
  }
  function mount() {
    if (globe) return;
    globe = window.gsGlobe.create(canvas, {
      colorOf: look,
      avoid: [card.querySelector('.sd-card__head'), card.querySelector('.sd-globe__cap'), card.querySelector('.sd-notch'), document.getElementById('globe-live')].filter(Boolean),
      clear: [card.querySelector('.sd-card-title'), card.querySelector('.sd-card-note'), card.querySelector('.sd-globe__note'), document.getElementById('globe-live')].filter(Boolean)
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
      servers = j.servers || [];
      active = j.active || [];
      if (first || missed) { missed = false; paintActive(); return; }
      var evs = (j.events || []).slice(-MAX_PINGS);
      evs.forEach(function (e) { globe.ping(e); });
      paintActive();  // after the pings, so a new stream's dot waits for its ping to land
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
  new MutationObserver(function () { if (globe) { globe.retheme(); paintActive(); } }).observe(root, { attributes: true, attributeFilter: ['data-look', 'data-mtab', 'class'] });
  try { window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () { if (globe) { globe.retheme(); paintActive(); } }); } catch (e) { /* old browser */ }

  window.gsGlobeCard = {
    /** Called on every dashboard redraw: start when the card first shows. */
    refresh: function () { if (shown() && cursor == null && !busy) poll(); else if (globe && shown()) globe.retheme(); }
  };
  poll();
})();
