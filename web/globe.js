/* Live streams globe (Glenn's pick 7, "Swing to the city", Nov 13 2026): a dotted globe in the dashboard's ink that
   turns once every 75 s. When a stream starts, the globe swings the short way round to face its city, a line flies out
   from the Dallas servers, and a lime dot lands there with the city's name.
   Canvas only, no libraries. window.gsGlobe.create(canvas, {avoid: [elements]}) -> {ping, retheme, destroy}.
   Needs window.GLOBE_DATA.dots (web/globe-data.js). The card around it is web/globe-card.js.

   Keeping up when many streams start (Glenn's question):
   - a swing is never cut short; new cities wait their turn (smooth ease in and out, then a 1.6 s hold);
   - three or more waiting: one swing to the busiest area (the city with the most others within about 2,500 km, and
     those around it), and every waiting city pings; ones on the far side ping dimmed;
   - a city that has waited over 12 s pings where it is, without a swing;
   - two pings within about 400 km of each other share one dot with a count ("Dallas, TX ×3").
   Reduced motion: no turning and no swings; it rests over the Americas and the pings and lines still show. */
(function () {
  'use strict';
  var RAD = Math.PI / 180;
  var PERIOD = 75;            // seconds per turn
  var TILT = 22 * RAD;        // resting tilt: North America and Europe in view
  var RM_LON = -88 * RAD;     // where it rests for reduced motion
  var MERGE_DEG = 3.5, MERGE_S = 2.6, STAGGER_S = 0.32, HOLD_S = 1.6, STALE_S = 12, CLUSTER_DEG = 22;
  var LIFE = 4.2;
  var SERVER = { label: 'Dallas DC', lat: 32.8 * RAD, lon: -96.8 * RAD };
  // Land dots, read when the first globe is made (the data file may arrive after this script).
  var DOTS = null;
  function loadDots() {
    var src = (window.GLOBE_DATA && window.GLOBE_DATA.dots) || [];
    DOTS = new Float32Array(src.length);
    for (var i = 0; i < src.length; i++) DOTS[i] = src[i] / 10 * RAD;
  }

  var reduce = false;
  try {
    var mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    reduce = mq.matches;
    var onMq = function () { reduce = mq.matches; globes.forEach(function (g) { g.reducedChanged(); }); wake(); };
    if (mq.addEventListener) mq.addEventListener('change', onMq); else if (mq.addListener) mq.addListener(onMq);
  } catch (e) { /* no matchMedia */ }

  // ---------- projection ----------
  var px = 0, py = 0, pz = 0;
  function proj(v, lon, lat, alt) {
    var cl = Math.cos(lat), d = lon - v.lam;
    var x = cl * Math.sin(d), y0 = Math.sin(lat), z0 = cl * Math.cos(d), k = v.R * (alt || 1);
    px = v.cx + k * x; py = v.cy - k * (y0 * v.cp - z0 * v.sp); pz = y0 * v.sp + z0 * v.cp;
  }
  function circle(ctx, x, y, r) { ctx.beginPath(); ctx.arc(x, y, Math.max(0, r), 0, Math.PI * 2); }
  function rgba(rgb, a) { return 'rgba(' + rgb + ',' + Math.max(0, Math.min(1, a)).toFixed(3) + ')'; }
  function clamp(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
  function ease(x) { x = clamp(x); return 1 - Math.pow(1 - x, 3); }
  function inOut(x) { x = clamp(x); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function wrap(a) { while (a > Math.PI) a -= Math.PI * 2; while (a < -Math.PI) a += Math.PI * 2; return a; }
  function angDeg(a, b) {
    var s = Math.sin(a.lat) * Math.sin(b.lat) + Math.cos(a.lat) * Math.cos(b.lat) * Math.cos(a.lon - b.lon);
    return Math.acos(Math.max(-1, Math.min(1, s))) / RAD;
  }
  function rgbOf(c) {
    c = String(c || '').trim();
    var m = /^#?([0-9a-f]{6})/i.exec(c);
    if (m) { var n = parseInt(m[1], 16); return ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255); }
    m = /rgba?\(([^)]+)\)/.exec(c);
    return m ? m[1].split(',').slice(0, 3).join(',') : '0,0,0';
  }
  function arcPoints(a, b, n) {
    function vec(p) { return [Math.cos(p.lat) * Math.cos(p.lon), Math.cos(p.lat) * Math.sin(p.lon), Math.sin(p.lat)]; }
    var A = vec(a), B = vec(b);
    var om = Math.acos(Math.max(-1, Math.min(1, A[0] * B[0] + A[1] * B[1] + A[2] * B[2]))), so = Math.sin(om);
    var pts = [], lift = Math.min(0.2, 0.05 + om * 0.11);
    for (var i = 0; i <= n; i++) {
      var s = i / n, ka = so < 1e-6 ? 1 - s : Math.sin((1 - s) * om) / so, kb = so < 1e-6 ? s : Math.sin(s * om) / so;
      var x = ka * A[0] + kb * B[0], y = ka * A[1] + kb * B[1], z = ka * A[2] + kb * B[2];
      pts.push(Math.atan2(y, x), Math.atan2(z, Math.sqrt(x * x + y * y)), 1 + lift * Math.sin(Math.PI * s));
    }
    return pts;
  }

  // Labels try right, left, above and below the dot; a label with nowhere to go is left out (the dot still shows).
  function placeLabels(ctx, items, w, h, avoid) {
    ctx.font = '600 11px Urbanist, "Avenir Next", "Segoe UI", system-ui, sans-serif';
    var taken = avoid.slice(), out = [];
    items.forEach(function (it) { taken.push({ x: it.x - 6, y: it.y - 6, w: 12, h: 12 }); });
    items.forEach(function (it) {
      var tw = ctx.measureText(it.text).width, th = 14, gap = it.gap || 10;
      var spots = [[it.x + gap, it.y - th / 2], [it.x - gap - tw, it.y - th / 2], [it.x - tw / 2, it.y - gap - th], [it.x - tw / 2, it.y + gap]];
      for (var s = 0; s < spots.length; s++) {
        var r = { x: spots[s][0] - 3, y: spots[s][1] - 2, w: tw + 6, h: th + 4 };
        if (r.x < 2 || r.y < 2 || r.x + r.w > w - 2 || r.y + r.h > h - 2) continue;
        var hit = taken.some(function (q) { return r.x < q.x + q.w && r.x + r.w > q.x && r.y < q.y + q.h && r.y + r.h > q.y; });
        if (hit) continue;
        taken.push(r); out.push({ it: it, x: spots[s][0], y: spots[s][1] + th / 2 }); break;
      }
    });
    return out;
  }
  function labelText(p) { return p.n > 1 ? p.label + '  ×' + p.n : p.label; }

  // ---------- drawing ----------
  function draw(g, ctx, v, t, th) {
    var INK = th.ink, VOLT = th.volt, light = th.light;
    circle(ctx, v.cx, v.cy, v.R); ctx.lineWidth = 1; ctx.strokeStyle = rgba(INK, 0.12); ctx.stroke();
    var buckets = [new Path2D(), new Path2D(), new Path2D(), new Path2D()];
    for (var i = 0; i < DOTS.length; i += 2) {
      proj(v, DOTS[i], DOTS[i + 1]);
      if (pz <= 0.02) continue;
      var b = pz > 0.75 ? 3 : pz > 0.5 ? 2 : pz > 0.25 ? 1 : 0, r = 0.55 + 0.95 * pz;
      buckets[b].moveTo(px + r, py); buckets[b].arc(px, py, r, 0, Math.PI * 2);
    }
    [0.22, 0.4, 0.6, 0.82].forEach(function (a, k) { ctx.fillStyle = rgba(INK, a); ctx.fill(buckets[k]); });

    proj(v, SERVER.lon, SERVER.lat);
    var srv = pz > 0 ? { x: px, y: py } : null;
    if (srv) {
      ctx.fillStyle = rgba(INK, 1); ctx.fillRect(srv.x - 4, srv.y - 4, 8, 8);
      ctx.fillStyle = rgba(VOLT, 1); ctx.fillRect(srv.x - 1.5, srv.y - 1.5, 3, 3);
    }
    var labels = [];
    g.eachPing(t, function (p, age) {
      var fade = age > LIFE - 1.4 ? (LIFE - age) / 1.4 : 1, a = p.arc;
      if (a) {
        var n = a.length / 3, upto = Math.max(1, Math.round((n - 1) * ease(age / 0.9)));
        var path = new Path2D(), hidden = new Path2D(), pen = false, penH = false, hx = 0, hy = 0;
        for (var i = 0; i <= upto; i++) {
          proj(v, a[i * 3], a[i * 3 + 1], a[i * 3 + 2]);
          var dx = px - v.cx, dy = py - v.cy, vis = pz > 0 || dx * dx + dy * dy > v.R * v.R;
          if (vis) { if (!pen) path.moveTo(px, py); else path.lineTo(px, py); pen = true; penH = false; }
          else { if (!penH) hidden.moveTo(px, py); else hidden.lineTo(px, py); penH = true; pen = false; }
          hx = px; hy = py;
        }
        var af = age > 1.5 ? clamp(1 - (age - 1.5) / 1.7) : 1;
        if (af > 0) {
          ctx.lineCap = 'round';
          if (light) { ctx.lineWidth = 4.5; ctx.strokeStyle = rgba(VOLT, 0.85 * af); ctx.stroke(path); }
          ctx.lineWidth = 1.6; ctx.strokeStyle = rgba(light ? INK : VOLT, 0.95 * af); ctx.stroke(path);
          ctx.setLineDash([2, 4]); ctx.lineWidth = 1; ctx.strokeStyle = rgba(INK, 0.22 * af); ctx.stroke(hidden); ctx.setLineDash([]);
          if (age < 0.9) { circle(ctx, hx, hy, 3.2); ctx.fillStyle = rgba(light ? INK : VOLT, 1); ctx.fill(); }
        }
      }
      var land = a ? 0.9 : 0;
      if (age < land) return;
      proj(v, p.lon, p.lat);
      var front = pz > 0, f = front ? 1 : 0.3, la = age - land, halo = clamp(la / 1.4);
      if (halo < 1) { circle(ctx, px, py, 4 + halo * 16); ctx.fillStyle = rgba(VOLT, (1 - halo) * 0.55 * f); ctx.fill(); }
      circle(ctx, px, py, 1.5 + ease(la / 0.4) * 3.8); ctx.fillStyle = rgba(VOLT, fade * f); ctx.fill();
      ctx.lineWidth = 1.5; ctx.strokeStyle = rgba(INK, 0.9 * fade * f); ctx.stroke();
      if (front && pz > 0.15 && age < LIFE - 0.6) labels.push({ x: px, y: py, text: labelText(p), a: fade, gap: 11 });
    });
    labels = labels.slice(-3);
    if (srv && !labels.some(function (l) { var dx = l.x - srv.x, dy = l.y - srv.y; return dx * dx + dy * dy < 900; })) {
      labels.push({ x: srv.x, y: srv.y, text: SERVER.label, a: 0.85, server: true, gap: 10 });
    }
    var avoid = v.avoid.concat(srv ? [{ x: srv.x - 6, y: srv.y - 6, w: 12, h: 12 }] : []);
    ctx.textBaseline = 'middle'; ctx.lineJoin = 'round';
    placeLabels(ctx, labels, v.w, v.h, avoid).forEach(function (l) {
      ctx.lineWidth = 4; ctx.strokeStyle = rgba(th.card, 0.9 * l.it.a); ctx.strokeText(l.it.text, l.x, l.y);
      ctx.fillStyle = rgba(l.it.server ? th.muted : INK, l.it.a); ctx.fillText(l.it.text, l.x, l.y);
    });
  }

  // ---------- one loop for every globe on the page ----------
  var globes = [], raf = 0, last = 0;
  function frame(now) {
    raf = 0;
    var t = now / 1000, dt = last ? Math.min(0.1, t - last) : 0, any = false;
    last = t;
    globes.forEach(function (g) { if (g.tick(t, dt)) any = true; });
    if (any && !document.hidden) raf = requestAnimationFrame(frame); else last = 0;
  }
  function wake() { if (!raf && !document.hidden) raf = requestAnimationFrame(frame); }
  document.addEventListener('visibilitychange', wake);

  function create(canvas, opts) {
    opts = opts || {};
    if (!DOTS || !DOTS.length) loadDots();
    var ctx = canvas.getContext('2d');
    var w = 1, h = 1, dpr = 1, onScreen = true, dirty = true, cxf = 0.5, avoid = [], clear = [], th = null;
    var lam = reduce ? RM_LON : (opts.startLon != null ? opts.startLon : -70) * RAD, tilt = TILT;
    var pings = [], queue = [], spin = null, holdUntil = 0, lastStart = 0;

    function readTheme() {
      var cs = getComputedStyle(canvas);
      th = { ink: rgbOf(cs.getPropertyValue('--ink') || '#0b0b0d'), muted: rgbOf(cs.getPropertyValue('--ink-muted') || '#58585c'),
        volt: rgbOf(cs.getPropertyValue('--volt') || '#b8fc64'), card: rgbOf(cs.getPropertyValue('--surface-card') || '#f1f1f1') };
      var c = th.ink.split(',').map(Number);
      th.light = (c[0] * 0.299 + c[1] * 0.587 + c[2] * 0.114) < 128;  // dark ink: a light card
      cxf = parseFloat(cs.getPropertyValue('--gl-cx')) || 0.5;
    }
    function measureAvoid() {
      var c = canvas.getBoundingClientRect();
      avoid = (opts.avoid || []).map(function (el) {
        var r = el.getBoundingClientRect();
        return { x: r.left - c.left, y: r.top - c.top, w: r.width, h: r.height };
      }).filter(function (r) { return r.w > 0 && r.h > 0; });
      // The globe itself keeps clear of the words in these (the card's title and note), measured by their text.
      clear = (opts.clear || []).map(function (el) {
        var rg = document.createRange(); rg.selectNodeContents(el);
        var r = rg.getBoundingClientRect();
        return { x: r.left - c.left, y: r.top - c.top, w: r.width, h: r.height };
      }).filter(function (r) { return r.w > 0 && r.h > 0; });
    }
    function radiusFor(cx, cy, R) {
      for (; R > 40; R -= 2) {
        var hit = clear.some(function (q) {
          var nx = Math.max(q.x, Math.min(cx, q.x + q.w)), ny = Math.max(q.y, Math.min(cy, q.y + q.h));
          return (nx - cx) * (nx - cx) + (ny - cy) * (ny - cy) < (R + 8) * (R + 8);
        });
        if (!hit) break;
      }
      return R;
    }
    function resize() {
      var r = canvas.getBoundingClientRect();
      dpr = Math.min(2, window.devicePixelRatio || 1);
      w = Math.max(1, r.width); h = Math.max(1, r.height);
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      readTheme(); measureAvoid(); dirty = true; wake();
    }
    var ro = window.ResizeObserver ? new ResizeObserver(resize) : null;
    if (ro) ro.observe(canvas); else window.addEventListener('resize', resize);
    var io = window.IntersectionObserver ? new IntersectionObserver(function (es) { onScreen = es[0].isIntersecting; if (onScreen) { dirty = true; wake(); } }) : null;
    if (io) io.observe(canvas);

    function release(p, at) { p.startAt = at; }
    // Swing to the next waiting city (or the busiest area of a group), hold, then drift on from there.
    function focusStep(t, dt) {
      if (spin) {
        var k = (t - spin.t0) / spin.dur, e = inOut(k);
        lam = spin.lam0 + spin.dl * e; tilt = spin.tilt0 + (spin.tilt1 - spin.tilt0) * e;
        if (k >= 1) { spin = null; holdUntil = t + HOLD_S; }
        return;
      }
      if (queue.length) {
        var stale = queue.filter(function (p) { return t - p.born > STALE_S; });
        if (stale.length) {
          queue = queue.filter(function (p) { return stale.indexOf(p) < 0; });
          stale.forEach(function (p, i) { release(p, t + i * STAGGER_S); });
        }
      }
      if (queue.length && t >= holdUntil) {
        var batch = queue.length > 2 ? queue.splice(0, queue.length) : [queue.shift()];
        var aim = batch;
        if (batch.length > 2) {
          var best = batch[0], bestN = -1;
          batch.forEach(function (p) {
            var n = batch.filter(function (q) { return angDeg(p, q) < CLUSTER_DEG; }).length;
            if (n > bestN) { best = p; bestN = n; }
          });
          aim = batch.filter(function (q) { return angDeg(best, q) < CLUSTER_DEG; });
        }
        var x = 0, y = 0, z = 0;
        aim.forEach(function (p) { x += Math.cos(p.lat) * Math.cos(p.lon); y += Math.cos(p.lat) * Math.sin(p.lon); z += Math.sin(p.lat); });
        var lon = Math.atan2(y, x), lat = Math.atan2(z, Math.sqrt(x * x + y * y));
        var dl = wrap(lon - lam), t1 = Math.max(-25 * RAD, Math.min(50 * RAD, lat));
        var dur = Math.max(Math.abs(dl), Math.abs(t1 - tilt)) < 8 * RAD ? 0.35 : 0.5 + 0.8 * Math.abs(dl) / Math.PI;
        spin = { t0: t, dur: dur, lam0: lam, dl: dl, tilt0: tilt, tilt1: t1 };
        batch.forEach(function (p, i) { release(p, t + dur + i * STAGGER_S); });
        return;
      }
      lam -= dt * (Math.PI * 2 / PERIOD);
      if (t > holdUntil + 3) tilt += (TILT - tilt) * Math.min(1, dt * 0.6);
    }

    var g = {
      eachPing: function (t, fn) {
        pings.forEach(function (p) { var age = t - p.startAt; if (age >= 0 && age < LIFE) fn(p, age); });
      },
      tick: function (t, dt) {
        var alive = false;
        for (var i = pings.length - 1; i >= 0; i--) { if (t - pings[i].startAt >= LIFE) pings.splice(i, 1); else alive = true; }
        if (!reduce) focusStep(t, dt);
        if (!onScreen) return !reduce || alive;
        if (reduce && !alive && !dirty) return false;
        dirty = false;
        var cx = w * cxf;
        var v = { w: w, h: h, cx: cx, cy: h / 2, R: radiusFor(cx, h / 2, Math.max(10, Math.min(h / 2, cx, w - cx) - 26)), lam: lam,
          sp: Math.sin(tilt), cp: Math.cos(tilt), avoid: avoid };
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);
        draw(g, ctx, v, t, th);
        return !reduce || alive;
      },
      reducedChanged: function () {
        if (reduce) {
          var now = performance.now() / 1000;
          lam = RM_LON; tilt = TILT; spin = null;
          queue.forEach(function (p, i) { release(p, now + i * STAGGER_S); });
          queue = [];
        }
        dirty = true;
      },
      /** A stream started at {label, lat, lon} (degrees). */
      ping: function (e) {
        var now = performance.now() / 1000;
        var p = { lat: e.lat * RAD, lon: e.lon * RAD, label: String(e.label || ''), n: 1, born: now };
        for (var i = 0; i < pings.length; i++) {
          var q = pings[i];
          if (now - q.born < MERGE_S && angDeg(q, p) < MERGE_DEG) { q.n += 1; q.startAt = Math.max(now, q.startAt); wake(); return; }
        }
        p.arc = angDeg(SERVER, p) > 1.5 ? arcPoints(SERVER, p, 40) : null;
        if (reduce) { p.startAt = Math.max(now, lastStart + STAGGER_S); lastStart = p.startAt; }
        else { p.startAt = Infinity; queue.push(p); }
        pings.push(p);
        if (pings.length > 24) { var gone = pings.shift(); queue = queue.filter(function (q) { return q !== gone; }); }
        wake();
      },
      retheme: function () { readTheme(); measureAvoid(); dirty = true; wake(); },
      destroy: function () {
        if (ro) ro.disconnect(); if (io) io.disconnect();
        var k = globes.indexOf(g); if (k >= 0) globes.splice(k, 1);
      }
    };
    globes.push(g);
    resize();
    return g;
  }

  window.gsGlobe = { create: create };
})();
