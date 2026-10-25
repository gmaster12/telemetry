/* Greystar Telemetry: the dashboard page. Kept in its own file so the Content-Security-Policy can forbid inline scripts. */
(function () {
  const root = document.getElementById('streams-app');
  const n0 = d3.format(',.0f');
  const n1 = d3.format(',.1f');
  const clock = d3.timeFormat('%-I:%M %p');
  const svgNS = 'http://www.w3.org/2000/svg';

  dash.calc('kpis', {
    inputs: ['streams', 'bandwidth', 'servers'],
    title: 'Stream totals',
    description: 'Counts and bandwidth from the stream list, the last hour of bandwidth and the server list. Paused streams use no bandwidth.',
    fn: (streams, bandwidth, servers) => {
      const n = streams.length;
      const count = (key, value) => streams.filter((r) => r[key] === value).length;
      const active = streams.filter((r) => r.state !== 'Paused');
      const mbps = active.reduce((sum, r) => sum + Number(r.bitrate_mbps), 0);
      const peak = bandwidth.reduce((max, r) => Math.max(max, Number(r.mbps)), 0);
      return [{
        streams: n,
        playing: count('state', 'Playing'),
        paused: count('state', 'Paused'),
        buffering: count('state', 'Buffering'),
        transcodes: count('method', 'Transcode'),
        transcode_pct: n ? Math.round((100 * count('method', 'Transcode')) / n) : 0,
        mbps: Math.round(mbps * 10) / 10,
        peak_mbps: Math.round(peak * 10) / 10,
        servers: new Set(streams.map((r) => r.server)).size,
        servers_online: servers.filter((s) => s.status !== 'Offline').length,
        servers_offline: servers.filter((s) => s.status === 'Offline').length,
        servers_total: servers.length
      }];
    }
  });

  dash.calc('viewing_summary', {
    inputs: ['viewing_hours'],
    title: 'Viewing peak and quiet window',
    description: 'Busiest hour of the week, and the quietest 3-hour daytime window across the three quietest weekdays.',
    fn: (viewing_hours) => {
      if (!viewing_hours.length) return [];
      const order = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
      const fmtH = (h) => (h % 12 === 0 ? 12 : h % 12) + (h < 12 ? ' AM' : ' PM');
      const peak = viewing_hours.reduce((a, r) => (r.streams > a.streams ? r : a), viewing_hours[0]);
      const at = (d, h) => (viewing_hours.find((r) => r.day === d && r.hour === h) || { streams: 0 }).streams;
      let best = null;
      for (let h = 6; h <= 15; h++) {
        const byDay = order.slice(0, 5).map((d) => ({ d, v: at(d, h) + at(d, h + 1) + at(d, h + 2) }));
        const run = [0, 1, 2].map((i) => byDay.slice(i, i + 3)).map((ds) => ({ ds, v: ds.reduce((s, x) => s + x.v, 0) }));
        run.forEach((r) => { if (!best || r.v < best.v) best = { h, ds: r.ds.map((x) => x.d), v: r.v }; });
      }
      return [{
        peak_streams: peak.streams,
        peak_label: peak.day + ' ' + fmtH(peak.hour),
        quiet_label: best.ds[0] + '\u2013' + best.ds[2] + ', ' + fmtH(best.h).replace(' ', '\u00a0') + '\u2013' + fmtH(best.h + 3).replace(' ', '\u00a0'),
        quiet_avg: Math.round((best.v / 9) * 10) / 10
      }];
    }
  });

  dash.calc('server_load', {
    inputs: ['streams', 'servers'],
    title: 'Load by server',
    description: 'Streams, transcodes and egress per server, from the stream list and each server’s capacity.',
    fn: (streams, servers) => servers.map((s) => {
      const mine = streams.filter((r) => r.server === s.server);
      const egress = mine.filter((r) => r.state !== 'Paused').reduce((sum, r) => sum + Number(r.bitrate_mbps), 0);
      return {
        server: s.server,
        streams: mine.length,
        transcodes: mine.filter((r) => r.method === 'Transcode').length,
        egress_mbps: s.net_out_mbps != null ? s.net_out_mbps : Math.round(egress * 10) / 10,
        capacity_mbps: s.capacity_mbps,
        util_pct: Math.round((100 * egress) / s.capacity_mbps),
        cpu_pct: s.cpu_pct,
        max_streams: s.max_streams,
        hot_for_min: s.hot_for_min || 0,
        status: s.status,
        status_label: s.status === 'Healthy' ? 'Online' : s.status === 'Degraded' ? 'High load' : 'Offline'
      };
    })
  });

  /* Helpers */
  const ICONS = {
    grid: ['M4 4h6v6H4z', 'M14 4h6v6h-6z', 'M4 14h6v6H4z', 'M14 14h6v6h-6z'],
    activity: ['M3 12h4l3-8 4 16 3-8h4'],
    server: ['M4 4h16v6H4z', 'M4 14h16v6H4z', 'M8 7h.01', 'M8 17h.01'],
    play: ['M8 5v14l11-7z'],
    list: ['M8 6h13', 'M8 12h13', 'M8 18h13', 'M3 6h.01', 'M3 12h.01', 'M3 18h.01'],
    search: ['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z', 'm21 21-4.3-4.3'],
    clock: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z', 'M12 7v5l3 2'],
    arrow: ['M7 17 17 7', 'M8 7h9v9'],
    right: ['M5 12h14', 'm13 6 6 6-6 6'],
    sliders: ['M4 7h10', 'M18 7h2', 'M4 17h2', 'M10 17h10', 'M14 5v4', 'M8 15v4'],
    image: ['M3 5h18v14H3z', 'm3 16 5-5 4 4 3-3 6 6', 'M15.5 9.5h.01'],
    bell: ['M6 9a6 6 0 1 1 12 0c0 6 2 7 2 7H4s2-1 2-7', 'M10 20a2 2 0 0 0 4 0'],
    close: ['M6 6l12 12', 'M18 6 6 18'],
    sun: ['M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z', 'M12 2v2', 'M12 20v2', 'm4.9 4.9 1.4 1.4', 'm17.7 17.7 1.4 1.4', 'M2 12h2', 'M20 12h2', 'm4.9 19.1 1.4-1.4', 'm17.7 6.3 1.4-1.4'],
    moon: ['M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z'],
    auto: ['M3 5h18v11H3z', 'M8 20h8', 'M12 16v4'],
    users: ['M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z', 'M2 21v-1a6 6 0 0 1 6-6h2a6 6 0 0 1 6 6v1', 'M16 3.1a4 4 0 0 1 0 7.8', 'M22 21v-1a6 6 0 0 0-4-5.6'],
    history: ['M3 12a9 9 0 1 0 3-6.7L3 8', 'M3 3v5h5', 'M12 7v5l3 2'],
    logout: ['M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4', 'm16 17 5-5-5-5', 'M21 12H9'],
    down: ['m6 9 6 6 6-6'],
    film: ['M3 4h18v16H3z', 'M7 4v16', 'M17 4v16', 'M3 8h4', 'M3 12h4', 'M3 16h4', 'M17 8h4', 'M17 12h4', 'M17 16h4'],
    plus: ['M12 5v14', 'M5 12h14'],
    check: ['m5 12 5 5 9-10'],
    bookmark: ['M6 3h12v18l-6-4-6 4z'],
    flag: ['M5 21V4', 'M5 4h11l-2 4 2 4H5'],
    left: ['M19 12H5', 'm11 6-6 6 6 6'],
    external: ['M14 4h6v6', 'M20 4 11 13', 'M18 14v6H4V6h6']
  };
  function icon(name, size) {
    const s = document.createElementNS(svgNS, 'svg');
    s.setAttribute('width', size || 20); s.setAttribute('height', size || 20); s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '1.5');
    s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
    ICONS[name].forEach((d) => { const p = document.createElementNS(svgNS, 'path'); p.setAttribute('d', d); s.appendChild(p); });
    return s;
  }
  window.sdIcon = icon;
  window.sdArtFor = (r) => artFor(r);
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function avatar(name, size) {
    const hash = Array.from(String(name)).reduce((h, c) => h + c.charCodeAt(0), 0) % 4;
    const letter = (String(name).replace(/[^A-Za-z0-9]/g, '')[0] || '?').toUpperCase();
    const a = el('span', 'sd-avatar sd-avatar--' + hash + (size ? ' sd-avatar--' + size : ''), letter);
    a.setAttribute('aria-hidden', 'true');
    return a;
  }
  function notchCard(cls, goLabel, onGo) {
    const card = el('article', 'sd-card ' + cls);
    card.appendChild(el('span', 'sd-notch'));
    card.lastChild.setAttribute('aria-hidden', 'true');
    const go = el('button', 'sd-go');
    go.type = 'button'; go.setAttribute('aria-label', goLabel); go.appendChild(icon('arrow'));
    go.addEventListener('click', onGo);
    card.appendChild(go);
    return card;
  }
  const failed = (d) => ['error', 'connect', 'declined', 'missing'].includes(d.status);
  const failLine = () => el('p', 'sd-fail', 'Failed to load');

  function shapeSvg(tone) {
    const defs = { success: ['circle', { cx: 5, cy: 5, r: 4 }], warning: ['path', { d: 'M5 1 9.2 8.5H.8Z' }], danger: ['rect', { x: 1, y: 1, width: 8, height: 8, rx: 1.5 }], info: ['path', { d: 'M5 .6 9.4 5 5 9.4.6 5Z' }] };
    const def = defs[tone];
    if (!def) return null;
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('width', '10'); svg.setAttribute('height', '10'); svg.setAttribute('viewBox', '0 0 10 10');
    svg.setAttribute('fill', 'currentColor'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('class', 'gs-badge__shape');
    const s = document.createElementNS(svgNS, def[0]);
    Object.entries(def[1]).forEach(([k, v]) => s.setAttribute(k, v));
    svg.appendChild(s);
    return svg;
  }
  function badge(tone, text) {
    const span = el('span', 'gs-badge gs-badge--' + tone);
    const s = shapeSvg(tone);
    if (s) span.appendChild(s);
    span.appendChild(document.createTextNode(text));
    return span;
  }
  function led(kind, label) {
    const defs = { online: ['circle', { cx: 5, cy: 5, r: 4 }], warning: ['path', { d: 'M5 1 9.2 8.5H.8Z' }], offline: ['rect', { x: 1, y: 1, width: 8, height: 8, rx: 1.5 }] };
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('class', 'sd-led sd-led--' + kind);
    svg.setAttribute('width', '10'); svg.setAttribute('height', '10'); svg.setAttribute('viewBox', '0 0 10 10');
    svg.setAttribute('fill', 'currentColor'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', label);
    const s = document.createElementNS(svgNS, defs[kind][0]);
    Object.entries(defs[kind][1]).forEach(([k, v]) => s.setAttribute(k, v));
    svg.appendChild(s);
    return svg;
  }
  const STATUS = { Healthy: ['online', 'Healthy'], Degraded: ['warning', 'Degraded'], Offline: ['offline', 'Offline'] };
  const STATE_TONE = { Playing: 'success', Paused: 'neutral', Buffering: 'warning' };
  const METHOD_TONE = { 'Direct play': 'neutral', 'Direct stream': 'info', Transcode: 'accent' };

  /* Static pieces: navigation, icons, arrow buttons */
  const goTo = (id) => {
    const target = id === 'top' ? root : document.getElementById(id);
    if (target) { target.scrollIntoView({ behavior: 'smooth', block: 'start' }); dash.setLink(id === 'top' ? 'summary' : id); }
  };
  const can = (s) => !window.dash.me || !window.dash.me() || window.dash.can(s);
  const isAdmin = () => !!(window.dash.isAdmin && window.dash.isAdmin());
  const NAV = [
    { id: 'summary', label: 'Overview', icon: 'grid', ok: () => can('streams') },
    { id: 'bandwidth-card', label: 'Bandwidth', icon: 'activity', ok: () => can('bandwidth') },
    { id: 'servers-card', label: 'Servers', icon: 'server', ok: () => can('servers') },
    { id: 'streams-card', label: 'Streams', icon: 'play', ok: () => can('streams') },
    { id: 'people-card', label: 'Users', icon: 'users', ok: () => isAdmin() },
    { id: 'people-card', label: 'My history', icon: 'history', ok: () => !isAdmin() && window.dash.me && window.dash.me() && can('history') },
    { id: 'requests', label: 'Requests', icon: 'film', ok: () => can('requests') }
  ];
  let navActive = 'summary';
  function renderNav() {
    const nav = document.getElementById('nav');
    nav.replaceChildren();
    const brand = el('a', 'sd-brand');
    brand.href = '/'; brand.setAttribute('aria-label', 'Greystar Telemetry home');
    const mark = el('img'); mark.src = 'assets/mark-glow.png'; mark.alt = ''; mark.width = 160; mark.height = 126;
    brand.appendChild(mark);
    nav.appendChild(brand);
    NAV.filter((n) => n.ok()).forEach((n) => {
      const b = el('button', 'sd-navbtn');
      b.type = 'button'; b.title = n.label; b.setAttribute('aria-label', n.label);
      const onRequests = root.dataset.page === 'requests';
      b.setAttribute('aria-current', (onRequests ? n.id === 'requests' : navActive === n.id) ? 'true' : 'false');
      b.appendChild(icon(n.icon, 22));
      b.addEventListener('click', () => {
        if (n.id === 'requests') { if (window.gsRequests) window.gsRequests.open(); return; }
        navActive = n.id;
        if (onRequests && window.gsRequests) window.gsRequests.close();
        renderNav(); requestAnimationFrame(() => goTo(n.id));
      });
      nav.appendChild(b);
    });
  }
  const ACTIONS = {
    transcode: () => { dash.setParams({ method: 'Transcode', state: null }); goTo('streams-card'); },
    buffering: () => { dash.setParams({ state: 'Buffering', method: null }); goTo('streams-card'); },
    paused: () => { dash.setParams({ state: 'Paused', method: null }); goTo('streams-card'); },
    servers: () => goTo('servers-card'),
    capacity: () => goTo(isMobile() ? 'servers-card' : 'capacity-card'),
    streams: () => goTo('streams-card'),
    top: () => goTo('top')
  };
  root.querySelectorAll('.sd-go[data-go]').forEach((b) => { b.appendChild(icon('arrow')); b.addEventListener('click', ACTIONS[b.dataset.go]); });
  // Summary pills filter the stream list; pressing the active one again clears its filter.
  const PILL_FILTER = { transcode: (p) => p.method === 'Transcode', buffering: (p) => p.state === 'Buffering', paused: (p) => p.state === 'Paused' };
  root.querySelectorAll('.sd-pill[data-go]').forEach((b) => {
    b.querySelector('.sd-pill__go').appendChild(icon('arrow', 16));
    b.addEventListener('click', () => {
      if (PILL_FILTER[b.dataset.go](dash.params())) dash.setParams({ state: null, method: null });
      else ACTIONS[b.dataset.go]();
    });
  });
  document.getElementById('clock-icon').appendChild(icon('clock', 18));
  document.getElementById('search-icon').appendChild(icon('search', 18));
  document.getElementById('view-cards').appendChild(icon('grid', 20));
  document.getElementById('view-table').appendChild(icon('list', 20));
  document.getElementById('view-rows').appendChild(icon('list', 20));
  document.getElementById('view-carousel').appendChild(icon('image', 20));
  renderNav();

  /* View state */
  let view = 'cards';
  let query = '';
  let sort = { key: 'elapsed_min', dir: 1 };
  const setView = (v) => { view = v; renderStreams(); };
  document.getElementById('view-cards').addEventListener('click', () => setView('cards'));
  document.getElementById('view-table').addEventListener('click', () => setView('table'));
  document.getElementById('view-rows').addEventListener('click', () => setView('rows'));
  document.getElementById('view-carousel').addEventListener('click', () => setView('carousel'));
  const mq = window.matchMedia('(max-width: 760px)');
  const isMobile = () => mq.matches;
  let sheetId = null;
  let mtab = 'home';
  document.getElementById('q').addEventListener('input', (e) => { query = e.target.value.trim().toLowerCase(); renderStreams(); });

  /* Summary figures and top bar */
  function renderKpis() {
    const k = dash.data('kpis');
    const row = k.status === 'ok' && k.data.length ? k.data[0] : null;
    root.querySelectorAll('[data-source="kpis"][data-field]').forEach((e) => {
      const v = row ? row[e.dataset.field] : null;
      e.textContent = v == null ? '—' : e.dataset.fmt === '1' ? n1(v) : n0(v);
    });
    const slot = document.getElementById('buf-badge');
    slot.replaceChildren();
    if (row) slot.appendChild(row.buffering > 0 ? badge('warning', 'Needs attention') : badge('success', 'Clear'));
    const params = dash.params();
    root.querySelectorAll('.sd-pill[data-go]').forEach((b) => b.setAttribute('aria-pressed', PILL_FILTER[b.dataset.go](params) ? 'true' : 'false'));
    root.querySelector('.sd-pill[data-go="buffering"]').classList.toggle('is-warn', !!row && row.buffering > 0);
    const bw = dash.data('bandwidth');
    if (bw.status === 'ok' && bw.data.length) {
      const last = bw.data.reduce((a, r) => (new Date(r.minute) > new Date(a.minute) ? r : a));
      document.getElementById('asof').textContent = 'Updated ' + clock(new Date(last.minute));
    }
  }

  const sortedStreams = () => {
    const d = dash.data('streams');
    return d.status === 'ok' ? d.data.slice().sort((a, b) => a.elapsed_min - b.elapsed_min) : [];
  };

  function renderNewest() {
    const box = document.getElementById('newest');
    box.replaceChildren();
    const d = dash.data('streams');
    if (failed(d)) { box.appendChild(el('span', 'sd-pill-lime__label', 'Failed to load')); return; }
    if (d.status !== 'ok') return;
    trackStreams();
    // Right after a stream starts or stops, the bar says so for a few seconds, then shows the newest streams again.
    const showMsg = liveMsg && Date.now() < liveMsg.until;
    box.classList.toggle('is-msg', !!showMsg);
    if (showMsg) {
      const m = el('div', 'sd-livemsg sd-livemsg--' + liveMsg.kind + (liveMsg.shown ? ' is-old' : ''));
      const shape = el('span', 'sd-livemsg__shape'); shape.setAttribute('aria-hidden', 'true');
      const text = el('span', 'sd-livemsg__text');
      liveMsg.parts.forEach((x) => text.append(typeof x === 'string' ? x : x.cloneNode(true)));
      m.append(shape, text);
      box.appendChild(m);
      if (!liveMsg.shown) { liveMsg.shown = true; box.classList.remove('is-flash'); void box.offsetWidth; box.classList.add('is-flash'); }
      return;
    }
    sortedStreams().slice(0, 3).forEach((r) => {
      const item = el('div', 'sd-newest');
      item.appendChild(avatar(r.user, 'sm'));
      const t = el('div', 'sd-newest__text');
      t.append(el('b', null, r.title), el('span', null, n0(r.elapsed_min) + ' min in'));
      item.appendChild(t);
      box.appendChild(item);
    });
  }

  /* Bandwidth chart */
  // Peak by day, last 7 days (bandwidth card). Today is lime and "so far"; the busiest day is solid ink.
  const dayName = d3.timeFormat('%a');
  const longDay = d3.timeFormat('%A');
  function renderDaily() {
    const list = document.getElementById('bw-days');
    const best = document.getElementById('bw-days-best');
    const d = dash.data('bandwidth_daily');
    list.replaceChildren(); best.textContent = '';
    if (failed(d)) { list.appendChild(failLine()); return; }
    if (d.status !== 'ok' || !d.data.length) return;
    const rows = d.data.map((r) => ({ day: new Date(r.day + 'T12:00:00'), today: !!r.today,
      v: r.peak_mbps == null ? null : Number(r.peak_mbps), at: r.peak_at ? new Date(r.peak_at) : null }));
    const max = d3.max(rows, (r) => r.v) || 0;
    const top = rows.find((r) => r.v != null && r.v === max && max > 0);
    if (top) best.append('Highest: ', el('b', null, (top.today ? 'Today' : dayName(top.day)) + ', ' + n0(top.v) + ' Mbps'), ' at ' + clock(top.at));
    rows.forEach((r) => {
      const li = el('li', 'sd-bwday' + (r === top ? ' is-top' : '') + (r.today ? ' is-today' : '') + (r.v == null ? ' is-empty' : ''));
      li.setAttribute('aria-label', (r.today ? 'Today so far' : longDay(r.day)) + ': ' +
        (r.v == null ? 'no data' : 'peak ' + n1(r.v) + ' Mbps at ' + clock(r.at)) + (r === top ? ', the highest' : ''));
      const v = el('span', 'sd-bwday__v', r.v == null ? '—' : n0(r.v));
      const track = el('span', 'sd-bwday__track');
      const bar = el('span', 'sd-bwday__bar');
      bar.style.height = (r.v == null || !max ? 0 : Math.max(3, (r.v / max) * 100)) + '%';
      if (r.v != null) track.appendChild(bar);
      [v, track].forEach((x) => x.setAttribute('aria-hidden', 'true'));
      const day = el('span', 'sd-bwday__d', r.today ? 'Today' : dayName(r.day));
      const t = el('span', 'sd-bwday__t', r.today ? 'so far' : r.at ? clock(r.at) : 'no data');
      [day, t].forEach((x) => x.setAttribute('aria-hidden', 'true'));
      li.append(v, track, day, t);
      list.appendChild(li);
    });
  }
  function renderChart() {
    const box = root.querySelector('.sd-chart');
    const d = dash.data('bandwidth');
    box.replaceChildren();
    if (failed(d)) { box.appendChild(failLine()); return; }
    if (d.status !== 'ok' || !d.data.length) return;
    const rows = d.data.map((r) => ({ t: new Date(r.minute), v: Number(r.mbps) })).sort((a, b) => a.t - b.t);
    const W = Math.max(box.clientWidth, 280);
    const H = W < 520 ? 170 : 250;
    const m = { t: 28, r: 20, b: 30, l: 44 };
    const x = d3.scaleTime().domain(d3.extent(rows, (r) => r.t)).range([m.l, W - m.r]);
    const y = d3.scaleLinear().domain([0, d3.max(rows, (r) => r.v) * 1.15]).nice().range([H - m.b, m.t]);
    const svg = d3.select(box).append('svg').attr('viewBox', `0 0 ${W} ${H}`).attr('width', '100%').attr('role', 'img').attr('aria-label', 'Total bandwidth in Mbps over the last hour');
    const grad = svg.append('defs').append('linearGradient').attr('id', 'sd-area').attr('x1', 0).attr('x2', 0).attr('y1', 0).attr('y2', 1);
    [[0, 0.2], [0.25, 0.12], [0.5, 0.06], [0.75, 0.02], [1, 0]].forEach(([o, a]) => grad.append('stop').attr('offset', o).attr('stop-opacity', a).attr('style', 'stop-color: var(--ink)'));
    const grid = svg.append('g').attr('class', 'sd-grid sd-axis');
    y.ticks(4).forEach((t) => {
      grid.append('line').attr('x1', m.l).attr('x2', W - m.r).attr('y1', y(t)).attr('y2', y(t));
      grid.append('text').attr('x', m.l - 10).attr('y', y(t)).attr('dy', '0.32em').attr('text-anchor', 'end').text(n0(t));
    });
    const xt = svg.append('g').attr('class', 'sd-axis');
    x.ticks(W < 520 ? 3 : 6).forEach((t, i, all) => {
      xt.append('text').attr('x', x(t)).attr('y', H - 6).attr('text-anchor', i === 0 ? 'start' : i === all.length - 1 ? 'end' : 'middle').text(clock(t));
    });
    svg.append('path').datum(rows).attr('d', d3.area().x((r) => x(r.t)).y0(y(0)).y1((r) => y(r.v))).attr('fill', 'url(#sd-area)');
    svg.append('path').datum(rows).attr('class', 'sd-bwline').attr('pathLength', 1).attr('d', d3.line().x((r) => x(r.t)).y((r) => y(r.v))).attr('fill', 'none').attr('stroke', 'var(--ink)').attr('stroke-width', 2).attr('stroke-linecap', 'round').attr('stroke-linejoin', 'round');
    const last = rows[rows.length - 1];
    svg.append('circle').attr('cx', x(last.t)).attr('cy', y(last.v)).attr('r', 12).attr('fill', 'var(--volt)').attr('fill-opacity', 0.55);
    svg.append('circle').attr('cx', x(last.t)).attr('cy', y(last.v)).attr('r', 5).attr('fill', 'var(--volt)').attr('stroke', 'var(--ink)').attr('stroke-width', 2);
    svg.append('text').attr('x', x(last.t)).attr('y', y(last.v) - 20).attr('text-anchor', 'end').attr('fill', 'var(--ink)').attr('font-size', 13).attr('font-weight', 600).text(n1(last.v) + ' Mbps');
    const guide = svg.append('line').attr('y1', m.t).attr('y2', H - m.b).attr('stroke', 'var(--border-strong)').attr('stroke-dasharray', '3 3').attr('visibility', 'hidden');
    const dot = svg.append('circle').attr('r', 5).attr('fill', 'var(--surface-raised)').attr('stroke', 'var(--ink)').attr('stroke-width', 2).attr('visibility', 'hidden');
    const tip = el('div', 'sd-tip');
    tip.hidden = true;
    box.appendChild(tip);
    const bis = d3.bisector((r) => r.t).center;
    svg.append('rect').attr('x', m.l).attr('y', m.t).attr('width', W - m.l - m.r).attr('height', H - m.t - m.b).attr('fill', 'transparent')
      .on('pointermove', (event) => {
        const r = rows[bis(rows, x.invert(d3.pointer(event)[0]))];
        guide.attr('x1', x(r.t)).attr('x2', x(r.t)).attr('visibility', 'visible');
        dot.attr('cx', x(r.t)).attr('cy', y(r.v)).attr('visibility', 'visible');
        const scale = box.clientWidth / W;
        tip.replaceChildren();
        const strong = el('b', null, n1(r.v) + ' Mbps');
        tip.append(strong, document.createTextNode(clock(r.t)));
        tip.hidden = false;
        tip.style.left = Math.min(Math.max(x(r.t) * scale, 60), box.clientWidth - 60) + 'px';
        tip.style.top = (y(r.v) * scale - 14) + 'px';
      })
      .on('pointerleave', () => { guide.attr('visibility', 'hidden'); dot.attr('visibility', 'hidden'); tip.hidden = true; });
  }

  /* Servers */
  const pickServer = (name) => dash.setParams({ server: dash.params().server === name ? null : name });
  const SRV_KIND = { Online: 'online', 'High load': 'high', Offline: 'offline' };
  function renderServers() {
    const body = document.getElementById('srv-rows');
    const d = dash.data('server_load');
    body.replaceChildren();
    if (failed(d)) { const tr = el('tr'); const td = el('td'); td.colSpan = 5; td.appendChild(failLine()); tr.appendChild(td); body.appendChild(tr); return; }
    if (d.status !== 'ok') return;
    const sel = dash.params().server || null;
    d.data.forEach((r) => {
      const kind = SRV_KIND[r.status_label] || 'online';
      const tr = el('tr', sel === r.server ? 'is-selected' : null);
      tr.dataset.key = r.server;
      const name = el('span', 'sd-srvname');
      const dot = el('i', 'sd-srvdot sd-srvdot--' + kind); dot.setAttribute('aria-hidden', 'true');
      name.append(dot, document.createTextNode(r.server));
      const tdName = el('td'); tdName.appendChild(name);
      const tdStreams = el('td', null, r.streams + ' / ' + (r.max_streams || '\u2014'));
      const tdTp = el('td', r.egress_mbps ? null : 'sd-muted', n1(r.egress_mbps));
      tdTp.appendChild(el('span', 'sd-unit-m', ' Mbps'));
      const tdStatus = el('td'); tdStatus.appendChild(el('span', 'sd-srvpill sd-srvpill--' + kind, r.status_label));
      const go = el('button', 'sd-srvgo'); go.type = 'button';
      go.setAttribute('aria-label', 'Show streams on ' + r.server);
      go.setAttribute('aria-pressed', sel === r.server ? 'true' : 'false');
      go.title = 'CPU ' + n0(r.cpu_pct) + '% \u00b7 ' + n0(r.capacity_mbps) + ' Mbps capacity';
      go.appendChild(icon('right', 18));
      go.addEventListener('click', () => {
        dash.setParams({ server: r.server });
        if (isMobile()) goTab('streams'); else goTo('streams-card');
      });
      const tdGo = el('td'); tdGo.appendChild(go);
      tr.append(tdName, tdStreams, tdTp, tdStatus, tdGo);
      body.appendChild(tr);
    });
  }

  /* Filters */
  function renderChips() {
    const box = root.querySelector('.sd-chips');
    const d = dash.data('server_load');
    box.replaceChildren();
    if (d.status !== 'ok') return;
    const sel = dash.params().server || null;
    const mk = (label, value) => {
      const c = el('button', 'gs-chip' + (sel === value ? ' gs-chip--selected' : ''), label);
      c.type = 'button'; c.setAttribute('aria-pressed', sel === value ? 'true' : 'false');
      c.addEventListener('click', () => dash.setParams({ server: value }));
      return c;
    };
    box.appendChild(mk('All servers', null));
    d.data.forEach((r) => box.appendChild(mk(r.server, r.server)));
  }
  function renderActive() {
    const box = document.getElementById('active-filters');
    box.replaceChildren();
    const p = dash.params();
    const mk = (label, clear) => {
      const c = el('button', 'gs-chip gs-chip--selected', label + ' ×');
      c.type = 'button'; c.setAttribute('aria-label', 'Remove filter ' + label);
      c.addEventListener('click', clear);
      return c;
    };
    if (p.method) box.appendChild(mk(p.method, () => dash.setParams({ method: null })));
    if (p.state) box.appendChild(mk(p.state, () => dash.setParams({ state: null })));
  }
  function visibleRows() {
    const p = dash.params();
    return sortedStreams().filter((r) => (!p.server || r.server === p.server) && (!p.method || r.method === p.method) && (!p.state || r.state === p.state)
      && (!query || [r.id, r.user, r.title, r.server, r.device, r.platform, r.method, r.state].join(' ').toLowerCase().includes(query)));
  }

  /* Streams: cards and table */
  const COLS = [
    { key: 'id', label: 'ID', cls: 'is-mono' }, { key: 'user', label: 'Viewer' }, { key: 'title', label: 'Title' },
    { key: 'server', label: 'Server' }, { key: 'device', label: 'Device' }, { key: 'resolution', label: 'Quality' },
    { key: 'method', label: 'Method' }, { key: 'bitrate_mbps', label: 'Mbps', cls: 'is-num' }, { key: 'progress_pct', label: 'Progress' }, { key: 'state', label: 'State' }
  ];
  /* Cover art: a real poster where one is supplied (window.SD_POSTERS), otherwise a poster-style stand-in drawn per title */
  const artCache = {};
  function artFor(r) {
    if (r.poster) return r.poster;
    const real = window.SD_POSTERS && window.SD_POSTERS[r.title];
    if (real) return real;
    if (artCache[r.title]) return artCache[r.title];
    let h = 0;
    for (const c of r.title) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    const rnd = () => { h = (h * 1664525 + 1013904223) >>> 0; return h / 4294967296; };
    const W = 480, H = 420;
    const cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    const g = cv.getContext('2d');
    const hue = Math.floor(rnd() * 360);
    const warm = (hue + 180 + Math.floor(rnd() * 60) - 30) % 360;
    const title = r.title.replace(/\s*\(audiobook\)$/i, '').toUpperCase();

    if (r.kind === 'Audiobook') {
      g.fillStyle = `hsl(${hue} 38% 22%)`; g.fillRect(0, 0, W, H);
      g.strokeStyle = `hsl(${warm} 55% 62%)`; g.lineWidth = 2;
      g.strokeRect(24, 24, W - 48, H - 48); g.strokeRect(34, 34, W - 68, H - 68);
      g.fillStyle = `hsl(${warm} 60% 68%)`;
      g.textAlign = 'center';
      g.font = '600 15px Urbanist, sans-serif';
      g.fillText('A N   A U D I O B O O K', W / 2, 62);
      let size = 40;
      g.font = `400 ${size}px Georgia, "Times New Roman", serif`;
      while (g.measureText(title).width > W - 210 && size > 20) { size -= 4; g.font = `400 ${size}px Georgia, "Times New Roman", serif`; }
      g.fillText(title, W / 2, 70 + size);
      g.beginPath(); g.moveTo(W / 2 - 50, 92 + size); g.lineTo(W / 2 + 50, 92 + size); g.stroke();
      g.beginPath(); g.arc(W / 2, 92 + size, 4, 0, Math.PI * 2); g.fill();
      for (let i = 0; i < 6; i++) { g.beginPath(); g.arc(W / 2, H * 0.72, 30 + i * 22, Math.PI, Math.PI * 2); g.globalAlpha = 0.5 - i * 0.07; g.stroke(); }
      g.globalAlpha = 1;
    } else {
      const sky = g.createLinearGradient(0, 0, 0, H);
      sky.addColorStop(0, `hsl(${hue} 45% 16%)`);
      sky.addColorStop(0.55, `hsl(${warm} 70% 48%)`);
      sky.addColorStop(1, `hsl(${warm} 80% 62%)`);
      g.fillStyle = sky; g.fillRect(0, 0, W, H);
      const sx = 120 + rnd() * (W - 240), sy = H * 0.62;
      const sun = g.createRadialGradient(sx, sy, 0, sx, sy, 170);
      sun.addColorStop(0, `hsla(${warm + 20} 100% 88% / 0.95)`);
      sun.addColorStop(0.35, `hsla(${warm} 100% 70% / 0.55)`);
      sun.addColorStop(1, `hsla(${warm} 100% 60% / 0)`);
      g.fillStyle = sun; g.fillRect(0, 0, W, H);
      const ground = H * 0.8;
      const dark = `hsl(${hue} 35% 7%)`;
      g.fillStyle = dark;
      if (rnd() > 0.5) {
        for (let x = 0; x < W; x += 18 + rnd() * 26) {
          const bh = 30 + rnd() * 110, bw = 16 + rnd() * 30;
          g.fillRect(x, ground - bh, bw, bh + 2);
        }
      } else {
        g.beginPath(); g.moveTo(0, ground);
        for (let x = 0; x <= W; x += 56) g.lineTo(x, ground - 30 - rnd() * 70);
        g.lineTo(W, ground); g.closePath(); g.fill();
      }
      g.fillRect(0, ground, W, H - ground);
      const figure = (x, fh) => {
        const top = ground - fh, hr = fh * 0.085;
        g.beginPath(); g.arc(x, top + hr, hr, 0, Math.PI * 2); g.fill();
        g.beginPath();
        g.moveTo(x - fh * 0.15, top + hr * 2.7);
        g.quadraticCurveTo(x, top + hr * 1.9, x + fh * 0.15, top + hr * 2.7);
        g.lineTo(x + fh * 0.13, top + fh * 0.55);
        g.lineTo(x + fh * 0.1, ground + 2); g.lineTo(x + fh * 0.025, ground + 2);
        g.lineTo(x, top + fh * 0.63);
        g.lineTo(x - fh * 0.025, ground + 2); g.lineTo(x - fh * 0.1, ground + 2);
        g.lineTo(x - fh * 0.13, top + fh * 0.55);
        g.closePath(); g.fill();
      };
      const count = 1 + Math.floor(rnd() * 3);
      const cx = W * (0.35 + rnd() * 0.3);
      for (let i = 0; i < count; i++) {
        const fh = (count === 1 ? 210 : 190) - Math.abs(i - (count - 1) / 2) * 30;
        figure(cx + (i - (count - 1) / 2) * 62, fh);
      }
      g.textAlign = 'center';
      let size = 46;
      const fit = W - 210;
      g.font = `800 ${size}px Urbanist, Impact, sans-serif`;
      const words = title.split(' ');
      let lines = [title];
      if (g.measureText(title).width > fit && words.length > 1) {
        const mid = Math.ceil(words.length / 2);
        lines = [words.slice(0, mid).join(' '), words.slice(mid).join(' ')];
      }
      const widest = () => Math.max(...lines.map((l) => g.measureText(l).width));
      while (widest() > fit && size > 18) { size -= 2; g.font = `800 ${size}px Urbanist, Impact, sans-serif`; }
      g.shadowColor = 'rgba(0, 0, 0, 0.6)'; g.shadowBlur = 14;
      g.fillStyle = `hsl(${warm + 30} 95% 72%)`;
      lines.forEach((l, i) => g.fillText(l, W / 2, 46 + size * (i + 0.8)));
      g.shadowBlur = 0;
      g.fillStyle = 'rgba(255, 255, 255, 0.7)';
      g.font = '600 10px Urbanist, sans-serif';
      g.fillText(r.kind === 'Episode' ? 'N E W   E P I S O D E S' : 'O N L Y   O N   G R E Y S T A R', W / 2, 46 + size * (lines.length + 0.6));
    }
    const img = g.getImageData(0, 0, W, H);
    for (let i = 0; i < img.data.length; i += 4) { const n = (rnd() - 0.5) * 14; img.data[i] += n; img.data[i + 1] += n; img.data[i + 2] += n; }
    g.putImageData(img, 0, 0);
    return (artCache[r.title] = cv.toDataURL('image/jpeg', 0.75));
  }
  // Moving clips (Settings, Display): up to 6 cards, picked at random, play a short silent loop cut 10% into what
  // is playing (made once per title on the server, app/clips.py). Each card keeps its <video> across redraws,
  // so the loop never restarts on the 10-second refresh. A clip pauses when the stream does, off screen and in a
  // hidden tab. Not for reduced motion or a browser asking to save data.
  const MAX_CLIPS = 6;
  const clipPicks = new Set();
  const clipVideos = new Map();   // stream id -> <video>
  const clipMode = () => (dash.params().clips === 'off' ? 'off' : 'on');
  const clipsOn = () => clipMode() === 'on' && !(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) &&
    !(navigator.connection && navigator.connection.saveData);
  const clipObserver = 'IntersectionObserver' in window
    ? new IntersectionObserver((es) => es.forEach((e) => { e.target._seen = e.isIntersecting; clipPlay(e.target); }), { threshold: 0.2 }) : null;
  function clipPlay(v) {
    if (v._want && v._seen !== false && !document.hidden && v.isConnected) { const p = v.play(); if (p) p.catch(() => {}); } else v.pause();
  }
  document.addEventListener('visibilitychange', () => { clipVideos.forEach(clipPlay); pvVideos.forEach(clipPlay); });
  function pickClips(rows, cardView) {
    const ok = new Set(cardView && clipsOn() ? rows.filter((r) => r.clip && !r._ended).map((r) => r.id) : []);
    clipPicks.forEach((id) => { if (!ok.has(id)) clipPicks.delete(id); });
    const rest = Array.from(ok).filter((id) => !clipPicks.has(id));
    while (clipPicks.size < MAX_CLIPS && rest.length) clipPicks.add(rest.splice(Math.floor(Math.random() * rest.length), 1)[0]);
    clipVideos.forEach((v, id) => {
      if (clipPicks.has(id)) return;
      v.pause(); v.removeAttribute('src'); v.load();
      if (clipObserver) clipObserver.unobserve(v);
      clipVideos.delete(id);
    });
  }
  function clipVideo() {
    const v = document.createElement('video');
    v.className = 'sd-clip';
    v.muted = true; v.loop = true; v.playsInline = true; v.preload = 'auto'; v.disablePictureInPicture = true; v.tabIndex = -1;
    ['muted', 'playsinline'].forEach((a) => v.setAttribute(a, ''));
    v.setAttribute('aria-hidden', 'true');
    v.addEventListener('canplay', () => v.classList.add('is-ready'));
    return v;
  }
  // The Settings preview draws a card too; it gets its own <video> so it never takes one from a real card.
  let pvMode = false;
  const pvVideos = new Map();      // clip url -> <video> used by the Settings preview
  function attachClip(host, r) {
    if (!r.clip || r._ended) return;
    let v;
    if (pvMode) {
      if (!clipsOn()) return;
      v = pvVideos.get(r.clip);
      if (!v) { v = clipVideo(); v.src = r.clip; pvVideos.set(r.clip, v); }
      v._used = true;
    } else {
      if (!clipPicks.has(r.id)) return;
      v = clipVideos.get(r.id);
      if (!v) { v = clipVideo(); clipVideos.set(r.id, v); if (clipObserver) clipObserver.observe(v); }
      if (v.getAttribute('src') !== r.clip) v.src = r.clip;
    }
    v._want = r.state === 'Playing';
    host.classList.add('has-clip');
    const shade = el('span', 'sd-clip__shade'); shade.setAttribute('aria-hidden', 'true');
    host.prepend(v, shade);   // under the shade, and both under the card's text
    requestAnimationFrame(() => clipPlay(v));
  }
  // Stream card style (Settings, Display): classic (the original) or one of four poster layouts.
  const CARD_STYLES = ['classic', 'side', 'top', 'banner', 'tiles'];
  let cardStyle = 'classic';
  const progressEl = (r, withText) => {
    const prog = el('div', 'sd-progress');
    const bar = el('span', 'sd-prog'); bar.setAttribute('aria-hidden', 'true');
    const fill = el('i'); fill.style.width = Math.min(100, r.progress_pct) + '%'; bar.appendChild(fill);
    prog.appendChild(bar);
    const text = el('span', withText ? null : 'sd-sr', n0(r.progress_pct) + '% · ' + n0(r.elapsed_min) + ' min in');
    prog.appendChild(text);
    return prog;
  };
  function whoEl(r) {
    const who = el('div', 'sd-scard__who');
    const nameEl = el('h3', 'sd-scard__name');
    const nb = window.gsPeople && window.gsPeople.nameButton(r);
    if (nb) nameEl.appendChild(nb); else nameEl.textContent = r.user;
    who.append(nameEl, el('p', 'sd-scard__title', r.title));
    return who;
  }
  function tagsEl(r, withMethod) {
    const tags = el('div', 'sd-tags');
    [r.server, r.device, r.resolution].forEach((t) => tags.appendChild(el('span', 'sd-tag', t)));
    if (withMethod) tags.appendChild(el('span', 'sd-tag sd-tag--line', r.method));
    return tags;
  }
  const stateBadge = (r) => badge(STATE_TONE[r.state] || 'neutral', r.state);
  function statusEl(r) {
    const row = el('div', 'sd-scard__status');
    row.append(stateBadge(r), el('span', null, r.method));
    return row;
  }
  function styledFront(r, style) {
    const go = (e) => { e.stopPropagation(); pickServer(r.server); };
    const card = notchCard('sd-scard sd-scard--' + (style === 'tiles' ? 'tile' : style) + ' sd-face', 'Show all streams on ' + r.server, go);
    const art = 'url(' + artFor(r) + ')';
    if (style === 'side') {
      const poster = el('div', 'sd-scard__poster'); poster.setAttribute('aria-hidden', 'true'); poster.style.setProperty('--art', art);
      const body = el('div', 'sd-scard__body');
      body.append(whoEl(r), tagsEl(r), statusEl(r), progressEl(r, true));
      card.append(poster, body);
      attachClip(poster, r);
    } else if (style === 'top') {
      const poster = el('div', 'sd-scard__poster'); poster.style.setProperty('--art', art);
      poster.append(avatar(r.user), stateBadge(r));
      const body = el('div', 'sd-scard__body');
      body.append(whoEl(r), tagsEl(r, true), progressEl(r, true));
      card.append(poster, body);
      attachClip(poster, r);
    } else if (style === 'banner') {
      card.style.setProperty('--art', art);
      const row = el('div', 'sd-scard__whorow');
      row.append(avatar(r.user), whoEl(r));
      card.append(row, tagsEl(r), statusEl(r), progressEl(r, true));
      attachClip(card, r);
    } else {
      card.style.setProperty('--art', art);
      const bottom = el('div');
      bottom.append(whoEl(r), el('p', 'sd-scard__meta', [r.server, r.resolution, r.method].filter(Boolean).join(' · ')), progressEl(r, false));
      card.append(stateBadge(r), bottom);
      attachClip(card, r);
    }
    return card;
  }
  function streamFront(r) {
    if (cardStyle !== 'classic') return styledFront(r, cardStyle);
    const card = notchCard('sd-scard sd-scard--art sd-face', 'Show all streams on ' + r.server, (e) => { e.stopPropagation(); pickServer(r.server); });
    const head = el('div', 'sd-card__head');
    head.appendChild(avatar(r.user, 'lg'));
    const who = el('div', 'sd-scard__who');
    const nameEl = el('h3', 'sd-scard__name');
    const nb = window.gsPeople && window.gsPeople.nameButton(r);
    if (nb) nameEl.appendChild(nb); else nameEl.textContent = r.user;
    who.append(nameEl, el('p', 'sd-scard__title', r.title));
    const foot = el('div', 'sd-scard__foot');
    const left = el('div');
    left.appendChild(el('span', 'sd-micro', 'Playing on'));
    const tags = el('div', 'sd-tags');
    [r.server, r.device, r.resolution].forEach((t) => tags.appendChild(el('span', 'sd-tag', t)));
    left.appendChild(tags);
    const right = el('div');
    right.appendChild(el('span', 'sd-micro', r.method));
    right.appendChild(badge(STATE_TONE[r.state] || 'neutral', r.state));
    foot.append(left, right);
    const prog = el('div', 'sd-progress');
    const bar = el('span', 'sd-prog'); bar.setAttribute('aria-hidden', 'true');
    const fill = el('i'); fill.style.width = Math.min(100, r.progress_pct) + '%'; bar.appendChild(fill);
    prog.append(bar, el('span', null, n0(r.progress_pct) + '% · ' + n0(r.elapsed_min) + ' min in'));
    card.style.setProperty('--art', 'url(' + artFor(r) + ')');
    card.append(head, who, foot, prog);
    attachClip(card, r);
    return card;
  }

  const flipped = new Set();
  function streamBack(r) {
    const fit = cardStyle === 'classic' ? '' : ' sd-back--fit sd-back--' + (cardStyle === 'tiles' ? 'tile' : cardStyle);
    const card = notchCard('sd-scard sd-back sd-face' + fit, 'Flip back', (e) => { e.stopPropagation(); toggleFlip(r.id, e.currentTarget.closest('.sd-flip__inner')); });
    card.style.setProperty('--art', 'url(' + artFor(r) + ')');
    const head = el('div', 'sd-card__head');
    const sub = el('p', 'sd-back__sub');
    const nb = window.gsPeople && window.gsPeople.nameButton(r);
    // Tiles are narrow: leave out the session id there.
    sub.append(nb || document.createTextNode(r.user), document.createTextNode(cardStyle === 'tiles' ? ' \u00b7 ' + r.location : ' \u00b7 ' + r.id + ' \u00b7 ' + r.location));
    head.append(el('p', 'sd-back__eyebrow', 'Playback details'), el('h3', 'sd-back__title', r.title), sub);
    const tags = el('div', 'sd-tags');
    tags.appendChild(badge(METHOD_TONE[r.method] || 'neutral', r.method));
    tags.appendChild(el('span', 'sd-tag', r.quality));
    if (r.throttled) tags.appendChild(el('span', 'sd-tag', 'Throttled'));
    const dl = el('dl', 'sd-dl');
    const row = (k, v, note) => {
      const dd = el('dd', null, v);
      if (note) { dd.appendChild(document.createTextNode(' ')); dd.appendChild(el('small', null, note)); }
      dl.append(el('dt', null, k), dd);
    };
    if (r.video_decision !== 'None') row('Video', r.source_video === r.stream_video ? r.stream_video : r.source_video + ' \u2192 ' + r.stream_video, r.video_decision);
    row('Audio', r.source_audio === r.stream_audio ? r.stream_audio : r.source_audio + ' \u2192 ' + r.stream_audio, r.audio_decision);
    row('Container', r.container);
    if (r.subtitles !== 'None') row('Subtitles', r.subtitles);
    if (r.transcode_reason !== 'None') row('Reason', r.transcode_reason);
    if (r.method === 'Transcode') row('Transcoder', r.hw_accel, r.transcode_speed != null ? n1(r.transcode_speed) + '\u00d7 speed' : null);
    row('Bandwidth', n1(r.bitrate_mbps) + ' Mbps', r.bitrate_estimated ? 'estimated, Plex reported ' + n0(r.bitrate_reported_mbps) + ' Mbps' : null);
    row('Server', r.server);
    row('Player', r.player, r.device);
    card.append(head, tags, dl);
    if (canStop() && !r._ended) card.appendChild(stopControl(r));
    return card;
  }

  /* Stop a stream from the back of its card (admins; Plex Pass; ENABLE_ACTIONS). The viewer sees the message. */
  const STOP_DEFAULT = 'This stream was stopped by the server admin.';
  const stopState = new Map();   // stream id -> { open, reason, busy, error, done }
  const canStop = () => !!(window.dash.me && window.dash.me() && window.dash.me().actions) && !(dash.isSample && dash.isSample());
  function stopControl(r) {
    const st = stopState.get(r.id) || {};
    const box = el('div', 'sd-stop');
    ['click', 'keydown', 'pointerdown'].forEach((t) => box.addEventListener(t, (e) => e.stopPropagation()));
    const paint = () => { stopState.set(r.id, st); box.replaceChildren(); fill(); };
    function fill() {
      if (st.done) { box.append(el('p', 'sd-stop__note', 'Stopping. The card goes when Plex confirms.')); return; }
      if (!st.open) {
        const b = el('button', 'sd-stop__btn');
        b.type = 'button';
        b.append(shapeSvg('danger'), document.createTextNode('Stop stream'));
        b.addEventListener('click', () => { st.open = true; st.error = null; paint(); const i = box.querySelector('input'); if (i) i.focus(); });
        box.append(b);
        return;
      }
      const id = 'stop-' + r.id.replace(/[^A-Za-z0-9_-]/g, '_');
      const label = el('label', 'sd-stop__label', 'Message ' + r.user + ' sees');
      label.htmlFor = id;
      const input = el('input', 'sd-stop__input');
      input.id = id; input.maxLength = 300; input.value = st.reason != null ? st.reason : STOP_DEFAULT;
      input.addEventListener('input', () => { st.reason = input.value; });
      const go = el('button', 'sd-stop__btn sd-stop__btn--go', st.busy ? 'Stopping…' : 'Stop now');
      go.type = 'button'; go.disabled = !!st.busy;
      const keep = el('button', 'sd-stop__keep', 'Keep playing');
      keep.type = 'button';
      keep.addEventListener('click', () => { st.open = false; st.error = null; paint(); });
      const send = () => {
        st.busy = true; st.reason = input.value; paint();
        fetch('/api/streams/' + encodeURIComponent(r.id) + '/stop', { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'greystar' }, body: JSON.stringify({ reason: st.reason }) })
          .then((res) => res.json().catch(() => ({})).then((j) => { if (!res.ok) throw new Error(j.detail || ('HTTP ' + res.status)); }))
          .then(() => { st.busy = false; st.done = true; paint(); setTimeout(() => dash.refresh && dash.refresh(), 1500); })
          .catch((e) => { st.busy = false; st.error = e.message; paint(); });
      };
      go.addEventListener('click', send);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); send(); } if (e.key === 'Escape') { st.open = false; paint(); } });
      box.append(el('p', 'sd-stop__q', 'Stop ' + r.user + '\u2019s stream of ' + r.title + ' on ' + r.server + '?'), label, input,
        st.error ? el('p', 'sd-stop__err', st.error) : document.createTextNode(''),
        el('div', 'sd-stop__row'));
      box.lastChild.append(go, keep);
    }
    fill();
    return box;
  }
  function toggleFlip(id, inner) {
    if (flipped.has(id)) flipped.delete(id); else flipped.add(id);
    if (inner) { inner.classList.toggle('is-flipped', flipped.has(id)); inner.setAttribute('aria-pressed', flipped.has(id) ? 'true' : 'false'); }
  }
  /* Streams starting and stopping, "Clock sweep" with the Live now bar (Glenn's pick): a lime border sweeps once
     around a new card like a clock hand, then fades; an ended card stays in its old place while a grey border
     unwinds the other way and the poster goes grey, then it fades out and the cards around it glide over to
     close the gap. The lime Live now bar says who started or stopped what. */
  const NEW_MS = 1460, END_MS = 920, GLIDE_MS = 420, MSG_MS = 6000;  // match people.css (sweep 760 + hold 300 + fade 400; unwind 640 + fade 280)
  // ids in the last streams load; null until the first load. ?demo=pulse treats the first load as new (preview).
  let seenIds = /[?&]demo=pulse(&|$)/.test(location.search) ? new Set() : null;
  let lastData = null;
  let lastRows = new Map();
  let lastOrder = [];              // ids in the order last drawn, to put an ended card back where it was
  const firstSeen = new Map();     // id -> when it appeared (only after the first load)
  const ended = new Map();         // id -> { row, at }
  let liveMsg = null;              // what the Live now bar says: { kind, parts, until, shown }
  let glideUntil = 0;              // cards glide to their new places until then
  const still = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function liveMessage(events) {
    const starts = events.filter((e) => e.kind === 'start'), stops = events.filter((e) => e.kind === 'stop');
    if (events.length === 1) {
      const { kind, r } = events[0];
      return { kind, parts: kind === 'start'
        ? [el('b', null, r.user), ' started ' + r.title + ' on ' + r.server]
        : [el('b', null, r.user), ' stopped ' + r.title + (r.elapsed_min ? ' after ' + n0(r.elapsed_min) + ' min' : '')] };
    }
    const bits = [];
    if (starts.length) bits.push(starts.length + (starts.length === 1 ? ' stream started' : ' streams started'));
    if (stops.length) bits.push(stops.length + (stops.length === 1 ? ' stream stopped' : ' streams stopped'));
    return { kind: !stops.length ? 'start' : !starts.length ? 'stop' : 'mixed', parts: [el('b', null, bits.join(', '))] };
  }
  function trackStreams() {
    const d = dash.data('streams');
    if (d.status !== 'ok' || d.data === lastData) return;
    lastData = d.data;
    const now = Date.now();
    const ids = new Set(d.data.map((r) => r.id));
    const events = [];
    if (seenIds !== null) {
      d.data.forEach((r) => { if (!seenIds.has(r.id)) { firstSeen.set(r.id, now); ended.delete(r.id); events.push({ kind: 'start', r }); } });
      seenIds.forEach((id) => {
        if (!ids.has(id) && lastRows.has(id)) { const r = lastRows.get(id); ended.set(id, { row: r, at: now }); events.push({ kind: 'stop', r }); }
      });
      if (events.length) {
        document.getElementById('stream-announcer').textContent = events.map((e) => e.kind === 'start'
          ? e.r.user + ' started ' + e.r.title + ' on ' + e.r.server : e.r.user + ' stopped ' + e.r.title).join('. ') + '.';
        liveMsg = Object.assign(liveMessage(events), { until: now + MSG_MS, shown: false });
        glideUntil = now + END_MS + GLIDE_MS + 1000;
        setTimeout(renderStreams, END_MS + 30);    // take the ended cards out; the rest glide over
        setTimeout(renderNewest, MSG_MS + 30);     // back to the newest streams
      }
    }
    seenIds = ids;
    lastRows = new Map(d.data.map((r) => [r.id, r]));
  }
  // Rows to draw: the visible ones plus ones that just ended, back in their old places while they go.
  function withEnded(rows) {
    const now = Date.now();
    const out = rows.slice();
    ended.forEach((e, id) => {
      if (now - e.at > END_MS) { ended.delete(id); return; }
      const at = Math.max(0, Math.min(out.length, lastOrder.indexOf(id) < 0 ? out.length : lastOrder.indexOf(id)));
      out.splice(at, 0, Object.assign({}, e.row, { _ended: e.at }));
    });
    return out;
  }
  // The page redraws often (every poll, resize, flip), so a sweep in progress picks up where it was.
  // node: the card (or phone row); front: the card face the border goes around.
  function pulseMarks(node, r, front) {
    node.dataset.id = r.id;
    const sweep = (cls) => { const s = el('span', 'sd-sweep ' + cls); s.setAttribute('aria-hidden', 'true'); (front || node).appendChild(s); };
    if (r._ended) {
      node.classList.add('is-ending');
      node.inert = true;
      node.style.setProperty('--sw-d', -(Date.now() - r._ended) + 'ms');
      sweep('sd-sweep--out');
      return;
    }
    const t = firstSeen.get(r.id);
    const age = t ? Date.now() - t : Infinity;
    if (age < NEW_MS) {
      node.classList.add('is-new');
      node.style.setProperty('--sw-d', -age + 'ms');
      sweep('sd-sweep--in');
    }
  }
  // Cards that stay slide from where they were to where they are now (only right after a start or stop).
  function streamRects() {
    const out = new Map();
    root.querySelectorAll('#cards .sd-flip[data-id], #carousel .sd-flip[data-id], #rows .sd-row[data-id]').forEach((n) => {
      if (!n.classList.contains('is-ending')) out.set(n.dataset.id, n.getBoundingClientRect());
    });
    return out;
  }
  function glideFrom(before) {
    const z = Number(getComputedStyle(root).getPropertyValue('--card-scale')) || 1;
    streamRects().forEach((b, id) => {
      const a = before.get(id);
      const n = root.querySelector('[data-id="' + CSS.escape(id) + '"]:not(.is-ending)');
      if (!a || !n) return;
      const k = n.classList.contains('sd-row') ? 1 : z;
      const dx = (a.left - b.left) / k, dy = (a.top - b.top) / k;
      if (Math.abs(dx) + Math.abs(dy) < 1) return;
      n.animate([{ transform: 'translate(' + dx + 'px, ' + dy + 'px)' }, { transform: 'none' }], { duration: GLIDE_MS, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)' });
    });
  }
  function streamCard(r) {
    const wrap = el('div', 'sd-flip');
    const inner = el('div', 'sd-flip__inner' + (flipped.has(r.id) ? ' is-flipped' : ''));
    inner.tabIndex = 0;
    inner.setAttribute('role', 'button');
    inner.setAttribute('aria-pressed', flipped.has(r.id) ? 'true' : 'false');
    inner.setAttribute('aria-label', r.user + ', ' + r.title + '. Show playback details');
    inner.addEventListener('click', () => toggleFlip(r.id, inner));
    inner.addEventListener('keydown', (e) => { if (e.target === inner && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggleFlip(r.id, inner); } });
    const front = streamFront(r);
    inner.append(front, streamBack(r));
    wrap.appendChild(inner);
    pulseMarks(wrap, r, front);
    return wrap;
  }
  function idleState() {
    const box = el('div', 'sd-idle');
    [['assets/mark-charcoal.png', 'sd-wm--light'], ['assets/mark-white.png', 'sd-wm--dark']].forEach(([src, cls]) => {
      const img = el('img', cls); img.src = src; img.alt = ''; img.width = 480; img.height = 366; box.appendChild(img);
    });
    const sv = dash.data('servers');
    const list = sv.status === 'ok' ? sv.data : [];
    const on = list.filter((s) => s.status !== 'Offline').length;
    const sub = !list.length ? 'New streams show up here within 10 seconds.'
      : on === list.length ? (list.length === 1 ? 'Your server is online and idle.' : 'All ' + list.length + ' servers are online and idle.') + ' New streams show up here within 10 seconds.'
      : on + ' of ' + list.length + ' servers are online. New streams show up here within 10 seconds.';
    box.append(el('b', null, 'Nothing playing right now'), el('span', null, sub));
    return box;
  }
  function renderStreams() {
    const before = Date.now() < glideUntil && !still() ? streamRects() : null;
    drawStreams();
    if (before) glideFrom(before);
  }
  function drawStreams() {
    // Do not redraw under someone typing the stop message.
    if (document.activeElement && document.activeElement.closest && document.activeElement.closest('.sd-stop')) return;
    const d = dash.data('streams');
    const p = dash.params();
    if (isMobile() && view !== 'rows' && view !== 'carousel') view = 'rows';
    if (!isMobile() && view !== 'cards' && view !== 'table') view = 'cards';
    const cards = document.getElementById('cards');
    const wrap = document.getElementById('table-wrap');
    const list = document.getElementById('rows');
    const car = document.getElementById('carousel');
    const dots = document.getElementById('dots');
    ['rows', 'carousel'].forEach((v) => document.getElementById('view-' + v).setAttribute('aria-pressed', view === v ? 'true' : 'false'));
    list.hidden = view !== 'rows';
    car.hidden = view !== 'carousel';
    dots.hidden = view !== 'carousel';
    const keepLeft = car.scrollLeft;
    list.replaceChildren(); car.replaceChildren(); dots.replaceChildren();
    document.getElementById('str-title').textContent = p.server ? 'Streams on ' + p.server : 'Live streams';
    document.getElementById('view-cards').setAttribute('aria-pressed', view === 'cards' ? 'true' : 'false');
    document.getElementById('view-table').setAttribute('aria-pressed', view === 'table' ? 'true' : 'false');
    cards.hidden = view !== 'cards';
    const hint = document.getElementById('flip-hint');
    hint.hidden = view !== 'cards' && view !== 'carousel';
    hint.textContent = view === 'carousel' ? 'Swipe for more. Tap a card to flip it.' : 'Select a card to flip it and see playback details.';
    wrap.hidden = view !== 'table';
    cards.replaceChildren();
    const head = document.getElementById('head-row');
    const body = document.getElementById('body-rows');
    head.replaceChildren(); body.replaceChildren();
    renderActive();
    if (failed(d)) { (view === 'rows' ? list : view === 'carousel' ? car : cards).appendChild(el('p', 'sd-empty', 'Failed to load')); renderSheet(); return; }
    if (d.status !== 'ok') return;
    trackStreams();
    const live = visibleRows();
    const rows = view === 'table' ? live : withEnded(live);
    lastOrder = rows.map((r) => r.id);
    pickClips(rows, view === 'cards' || view === 'carousel');
    const none = 'No streams match' + (p.server ? ' on ' + p.server : '') + ' right now.';
    if (!d.data.length && view !== 'table' && !rows.length) {
      const box = view === 'rows' ? list : view === 'carousel' ? car : cards;
      box.appendChild(idleState());
      hint.hidden = true;
      renderSheet();
      return;
    }
    renderSheet();
    if (view === 'rows') {
      if (!rows.length) list.appendChild(el('p', 'sd-empty', none));
      rows.forEach((r) => list.appendChild(streamRow(r)));
      return;
    }
    if (view === 'carousel') {
      if (!rows.length) car.appendChild(el('p', 'sd-empty', none));
      rows.forEach((r) => car.appendChild(streamCard(r)));
      live.forEach((_, i) => dots.appendChild(el('i', i === 0 ? 'on' : null)));
      car.scrollLeft = keepLeft;
      car.onscroll = () => {
        const first = car.firstElementChild;
        if (!first) return;
        const step = first.getBoundingClientRect().width + 12;
        const i = Math.round(car.scrollLeft / step);
        Array.from(dots.children).forEach((dot, j) => dot.classList.toggle('on', j === i));
      };
      return;
    }
    if (view === 'cards') {
      if (!rows.length) cards.appendChild(el('p', 'sd-empty', none));
      rows.forEach((r) => cards.appendChild(streamCard(r)));
      return;
    }
    COLS.forEach((c) => {
      const th = el('th'); th.scope = 'col'; th.dataset.field = c.key;
      if (c.cls === 'is-num') th.className = 'is-num';
      const b = el('button', 'sd-sort', c.label); b.type = 'button';
      if (sort.key === c.key) { b.dataset.dir = sort.dir === 1 ? '↑' : '↓'; th.setAttribute('aria-sort', sort.dir === 1 ? 'ascending' : 'descending'); }
      b.addEventListener('click', () => { sort = { key: c.key, dir: sort.key === c.key ? -sort.dir : 1 }; renderStreams(); });
      th.appendChild(b); head.appendChild(th);
    });
    const sorted = rows.slice().sort((a, b) => {
      const av = a[sort.key], bv = b[sort.key];
      return (typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv))) * sort.dir;
    });
    if (!sorted.length) { const tr = el('tr'); const td = el('td', 'sd-empty-cell', none); td.colSpan = COLS.length; tr.appendChild(td); body.appendChild(tr); return; }
    sorted.forEach((r) => {
      const tr = el('tr'); tr.dataset.key = r.id;
      COLS.forEach((c) => {
        const td = el('td', c.cls || '');
        const v = r[c.key];
        if (c.key === 'method') td.appendChild(badge(METHOD_TONE[v] || 'neutral', v));
        else if (c.key === 'state') td.appendChild(badge(STATE_TONE[v] || 'neutral', v));
        else if (c.key === 'bitrate_mbps') td.textContent = n1(v);
        else if (c.key === 'user' && window.gsPeople && window.gsPeople.canOpen(r)) td.appendChild(window.gsPeople.nameButton(r));
        else if (c.key === 'progress_pct') {
          const bar = el('span', 'sd-prog'); bar.setAttribute('aria-hidden', 'true');
          const fill = el('i'); fill.style.width = Math.min(100, v) + '%'; bar.appendChild(fill);
          td.append(bar, document.createTextNode(n0(v) + '%'));
        } else td.textContent = v;
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
  }

  /* Phone: compact rows and the playback sheet */
  function streamRow(r) {
    const b = el('button', 'sd-row');
    b.type = 'button';
    b.setAttribute('aria-expanded', sheetId === r.id ? 'true' : 'false');
    b.setAttribute('aria-controls', 'sheet');
    const th = el('span', 'sd-row__thumb'); th.setAttribute('aria-hidden', 'true');
    th.style.setProperty('--art', 'url(' + artFor(r) + ')');
    const main = el('span', 'sd-row__main');
    main.append(el('span', 'sd-row__name', r.user), el('span', 'sd-row__meta', r.title + ' \u00b7 ' + r.server));
    const bar = el('span', 'sd-prog'); bar.setAttribute('aria-hidden', 'true');
    const fill = el('i'); fill.style.width = Math.min(100, r.progress_pct) + '%'; bar.appendChild(fill);
    main.appendChild(bar);
    b.append(th, main, r._ended ? el('span', 'sd-row__ended', 'Ended') : badge(STATE_TONE[r.state] || 'neutral', r.state));
    if (!r._ended) b.addEventListener('click', () => { sheetId = sheetId === r.id ? null : r.id; renderStreams(); });
    pulseMarks(b, r);
    return b;
  }
  function renderSheet() {
    const sheet = document.getElementById('sheet');
    sheet.replaceChildren();
    const d = dash.data('streams');
    const r = view === 'rows' && sheetId && d.status === 'ok' ? d.data.find((x) => x.id === sheetId) : null;
    if (!r) { sheet.hidden = true; return; }
    sheet.hidden = false;
    const close = el('button', 'sd-sheet__close'); close.type = 'button'; close.setAttribute('aria-label', 'Close playback details');
    close.appendChild(icon('close', 18));
    close.addEventListener('click', () => { sheetId = null; renderStreams(); });
    const back = streamBack(r);
    const parts = Array.from(back.children).slice(2);
    const head = parts[0];
    const top = el('div', 'sd-sheet__top');
    const th = el('span', 'sd-sheet__thumb'); th.setAttribute('aria-hidden', 'true');
    th.style.setProperty('--art', 'url(' + artFor(r) + ')');
    top.append(th, head);
    const body = el('div', 'sd-sheet__body');
    body.append(...parts.slice(1));
    if (window.gsPeople && window.gsPeople.canOpen(r)) {
      const hb = el('button', 'st-btn st-btn--accent gp-histbtn');
      hb.type = 'button';
      hb.append(icon('history', 18), document.createTextNode(r.user + '\u2019s history'));
      hb.addEventListener('click', () => window.gsPeople.openHistory(r.user_id, r.user));
      body.appendChild(hb);
    }
    sheet.append(el('div', 'sd-sheet__grab'), close, top, body);
  }

  /* Alerts: what needs attention now */
  function computeAlerts() {
    const out = [];
    const sl = dash.data('server_load');
    const st = dash.data('streams');
    if (sl.status === 'ok') sl.data.forEach((s) => {
      if (s.status === 'Offline') out.push({ tone: 'danger', short: s.server + ' offline', title: s.server + ' is offline', detail: 'No streams can start on it', act: () => goTab('servers') });
      else if (s.status === 'Degraded') out.push({ tone: 'warning', short: s.server + ' degraded', title: s.server + ' is degraded', detail: 'CPU ' + n0(s.cpu_pct) + '% \u00b7 ' + s.streams + ' streams', act: () => { dash.setParams({ server: s.server, state: null, method: null }); goTab('streams'); } });
    });
    if (st.status === 'ok') st.data.filter((r) => r.state === 'Buffering').forEach((r) => {
      out.push({ tone: 'warning', buffering: true, title: r.user + ' is buffering', detail: r.title + ' on ' + r.server + (r.transcode_speed != null ? ' \u00b7 transcoder at ' + n1(r.transcode_speed) + '\u00d7' : ''), act: () => { view = 'rows'; sheetId = r.id; dash.setParams({ server: null, state: null, method: null }); goTab('streams'); } });
    });
    return out;
  }
  function renderAlerts() {
    const list = computeAlerts();
    const bar = document.getElementById('alertbar');
    bar.replaceChildren();
    const buffering = list.filter((a) => a.buffering).length;
    const parts = list.filter((a) => a.short).map((a) => a.short);
    if (buffering) parts.push(buffering + ' buffering');
    bar.hidden = !parts.length;
    if (parts.length) {
      const sh = shapeSvg('warning'); sh.setAttribute('class', 'sd-alertbar__shape');
      bar.append(sh, el('span', null, parts.join(' \u00b7 ')), el('b', null, 'View'));
      bar.onclick = () => {
        if (isMobile()) { goTab('alerts'); return; }
        if (buffering) { dash.setParams({ state: 'Buffering', method: null }); goTo('streams-card'); } else goTo('servers-card');
      };
    }
    const box = document.getElementById('alertlist');
    box.replaceChildren();
    document.getElementById('al-count').textContent = list.length ? String(list.length) : '';
    if (!list.length) box.appendChild(el('p', 'sd-allclear', 'Nothing needs attention right now.'));
    list.forEach((a) => {
      const b = el('button', 'sd-alert'); b.type = 'button';
      const ic = el('span', 'sd-alert__icon sd-alert__icon--' + a.tone); ic.appendChild(shapeSvg(a.tone));
      const tx = el('span', 'sd-alert__text'); tx.append(el('span', 'sd-alert__t', a.title), el('span', 'sd-alert__d', a.detail));
      b.append(ic, tx, icon('arrow', 18));
      b.addEventListener('click', a.act);
      box.appendChild(b);
    });
    return list.length;
  }

  /* Phone tab bar */
  const TABS = [
    { id: 'home', label: 'Home', icon: 'grid', link: 'summary', ok: () => can('streams') || can('bandwidth') || can('viewing') },
    { id: 'servers', label: 'Servers', icon: 'server', link: 'servers-card', ok: () => can('servers') },
    { id: 'streams', label: 'Streams', icon: 'play', link: 'streams-card', ok: () => can('streams') },
    { id: 'alerts', label: 'Alerts', icon: 'bell', link: 'alerts', ok: () => can('streams') && can('servers') },
    { id: 'people', label: 'Users', icon: 'users', link: 'people-card', ok: () => isAdmin() },
    { id: 'people', label: 'History', icon: 'history', link: 'people-card', ok: () => !isAdmin() && window.dash.me && window.dash.me() && can('history') },
    { id: 'requests', label: 'Requests', icon: 'film', link: 'requests', ok: () => can('requests') }
  ];
  const okTabs = () => TABS.filter((t) => t.ok());
  window.sdGoTab = (id) => { if (isMobile()) goTab(id); };
  function goTab(id) {
    if (id === 'requests') { if (window.gsRequests) window.gsRequests.open(); return; }
    if (root.dataset.page === 'requests' && window.gsRequests) window.gsRequests.close();
    mtab = id;
    root.dataset.mtab = id;
    renderAll();
    root.scrollIntoView({ block: 'start' });
    const t = TABS.find((x) => x.id === id);
    if (t) dash.setLink(t.link);
  }
  function renderTabbar(alertCount) {
    const bar = document.getElementById('tabbar');
    bar.replaceChildren();
    const tabs = okTabs();
    bar.classList.toggle('is-single', tabs.length < 2);
    tabs.forEach((t) => {
      const b = el('button', 'sd-tab'); b.type = 'button';
      b.setAttribute('aria-current', (root.dataset.page === 'requests' ? t.id === 'requests' : mtab === t.id) ? 'true' : 'false');
      b.setAttribute('aria-label', t.label + (t.id === 'alerts' && alertCount ? ', ' + alertCount + ' alerts' : ''));
      b.append(icon(t.icon, 20), el('span', null, t.label));
      if (t.id === 'alerts' && alertCount) b.appendChild(el('span', 'sd-tab__count', String(alertCount)));
      b.addEventListener('click', () => goTab(t.id));
      bar.appendChild(b);
    });
  }

  /* Appearance: light, dark or follow the device */
  const LOOKS = [{ id: 'light', label: 'Light', icon: 'sun' }, { id: 'dark', label: 'Dark', icon: 'moon' }, { id: 'auto', label: 'Match device', icon: 'auto' }];
  function renderLook() {
    const look = dash.params().look || 'auto';
    if (look === 'auto') delete root.dataset.look; else root.dataset.look = look;
    const box = document.getElementById('look');
    box.replaceChildren();
    LOOKS.forEach((l) => {
      const b = el('button', 'sd-lookbtn');
      b.type = 'button'; b.title = l.label; b.setAttribute('aria-label', l.label);
      b.setAttribute('aria-pressed', look === l.id ? 'true' : 'false');
      b.appendChild(icon(l.icon, 20));
      b.addEventListener('click', () => {
        if (isMobile() && look === l.id) { const i = LOOKS.findIndex((x) => x.id === l.id); dash.setParams({ look: LOOKS[(i + 1) % LOOKS.length].id }); }
        else dash.setParams({ look: l.id });
      });
      box.appendChild(b);
    });
  }

  /* Viewing heatmap */
  function renderHeat() {
    const vs = dash.data('viewing_summary');
    const vrow = vs.status === 'ok' && vs.data.length ? vs.data[0] : null;
    root.querySelectorAll('[data-source="viewing_summary"][data-field]').forEach((e) => {
      const v = vrow ? vrow[e.dataset.field] : null;
      e.textContent = v == null ? '\u2014' : typeof v === 'number' ? n0(v) : v;
    });
    const box = document.getElementById('heat');
    const d = dash.data('viewing_hours');
    box.replaceChildren();
    if (failed(d)) { box.appendChild(failLine()); return; }
    if (d.status !== 'ok') return;
    const order = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const max = d.data.reduce((m, r) => Math.max(m, r.streams), 1);
    const fmtH = (h) => (h % 12 === 0 ? 12 : h % 12) + (h < 12 ? ' AM' : ' PM');
    order.forEach((day) => {
      box.appendChild(el('span', 'sd-heat__day', day));
      for (let h = 0; h < 24; h++) {
        const r = d.data.find((x) => x.day === day && x.hour === h);
        const v = r ? r.streams : 0;
        const c = el('i', 'sd-heat__cell' + (v === max ? ' is-peak' : ''));
        if (v !== max) c.style.opacity = (0.1 + 0.9 * (v / max)).toFixed(2);
        c.title = day + ' ' + fmtH(h) + ': ' + v + (v === 1 ? ' stream' : ' streams');
        box.appendChild(c);
      }
    });
  }

  /* Server capacity rings and rebalance suggestions */
  function ringSvg(pct, color) {
    const r = 28, c = 2 * Math.PI * r;
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', '0 0 72 72'); svg.setAttribute('aria-hidden', 'true');
    const mk = (attrs) => { const e = document.createElementNS(svgNS, 'circle'); Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v)); svg.appendChild(e); return e; };
    mk({ cx: 36, cy: 36, r, fill: 'none', stroke: 'rgba(255,255,255,0.12)', 'stroke-width': 7, class: 'sd-ring__track' });
    if (pct > 0) mk({ cx: 36, cy: 36, r, fill: 'none', stroke: color, 'stroke-width': 7, 'stroke-linecap': 'round', 'stroke-dasharray': c.toFixed(1), 'stroke-dashoffset': (c * (1 - Math.min(pct, 100) / 100)).toFixed(1), transform: 'rotate(-90 36 36)' });
    const t = document.createElementNS(svgNS, 'text');
    t.setAttribute('x', 36); t.setAttribute('y', 41); t.setAttribute('text-anchor', 'middle'); t.setAttribute('fill', '#f6f6f4'); t.setAttribute('font-size', 15); t.setAttribute('class', 'sd-ring__pct');
    t.textContent = n0(pct) + '%';
    svg.appendChild(t);
    return svg;
  }
  let movesOpen = false;
  function suggestMoves() {
    const sl = dash.data('server_load'), st = dash.data('streams');
    if (sl.status !== 'ok' || st.status !== 'ok') return [];
    const hot = sl.data.filter((s) => s.status !== 'Offline' && s.cpu_pct >= 85);
    const cool = sl.data.filter((s) => s.status === 'Healthy' && s.cpu_pct < 60 && s.streams < (s.max_streams || 99)).sort((a, b) => a.cpu_pct - b.cpu_pct);
    const moves = [];
    hot.forEach((h) => {
      st.data.filter((r) => r.server === h.server && r.method === 'Transcode').slice(0, 2).forEach((r, i) => {
        const to = cool[i % Math.max(cool.length, 1)];
        if (to) moves.push({ r, from: h.server, to: to.server, toCpu: to.cpu_pct });
      });
    });
    return moves;
  }
  function renderCapacity() {
    const rings = document.getElementById('rings');
    const warn = document.getElementById('capwarn');
    const btn = document.getElementById('rebal');
    const movesBox = document.getElementById('moves');
    const d = dash.data('server_load');
    rings.replaceChildren(); warn.replaceChildren(); movesBox.replaceChildren();
    if (failed(d)) { rings.appendChild(failLine()); return; }
    if (d.status !== 'ok') return;
    d.data.forEach((s) => {
      const off = s.status === 'Offline';
      const unknown = !off && s.cpu_known === false;
      const color = off || unknown ? '#6c6c72' : s.cpu_pct >= 85 ? 'var(--amber)' : 'var(--volt)';
      const item = el('div', 'sd-ring');
      item.setAttribute('aria-label', s.server + ': ' + (off ? 'offline' : unknown ? 'no CPU data' : 'CPU ' + n0(s.cpu_pct) + '%') + ', ' + s.streams + ' of ' + s.max_streams + ' streams');
      const ring = ringSvg(off || unknown ? 0 : s.cpu_pct, color);
      if (unknown) ring.querySelector('text').textContent = '\u2014';
      item.append(ring, el('b', null, s.server), el('span', null, off ? 'offline' : unknown ? 'no CPU data' : s.streams + ' / ' + (s.max_streams || '\u2014')));
      rings.appendChild(item);
    });
    const hot = d.data.filter((s) => s.status !== 'Offline' && s.cpu_pct >= 85);
    if (hot.length) {
      const sh = shapeSvg('warning'); sh.style.color = 'var(--amber)';
      warn.append(sh, el('span', null, hot.map((s) => s.server + ' has been above 85% CPU' + (s.hot_for_min ? ' for ' + s.hot_for_min + ' min' : '')).join(' \u00b7 ')));
    } else if (d.data.every((s) => s.cpu_known === false || s.status === 'Offline')) {
      warn.append(el('span', null, 'No CPU data yet. Connect Netdata in Settings.'));
    } else {
      const dot = el('i', 'sd-srvsum__dot'); warn.append(dot, el('span', null, 'All servers under 85% CPU'));
    }
    const moves = suggestMoves();
    btn.disabled = !moves.length;
    btn.setAttribute('aria-expanded', movesOpen && moves.length ? 'true' : 'false');
    btn.onclick = () => { movesOpen = !movesOpen; renderCapacity(); };
    movesBox.hidden = !(movesOpen && moves.length);
    if (movesOpen && moves.length) {
      movesBox.appendChild(el('div', 'sd-moves__title', 'Suggested moves'));
      const ul = el('ul');
      moves.forEach((m) => {
        const li = el('li');
        const sh = shapeSvg('success'); sh.style.color = 'var(--volt)';
        li.append(sh, el('span', null, 'Move '), el('b', null, m.r.user), el('span', null, ' (' + m.r.title + ') from ' + m.from + ' to '), el('b', null, m.to), el('span', null, ' \u00b7 ' + n0(m.toCpu) + '% CPU'));
        ul.appendChild(li);
      });
      movesBox.append(ul, el('p', null, 'Preview only. Moving streams needs a connection to your servers.'));
    }
  }

  /* Settings, Display (saved in this browser only): card style by picture, size, cover art brightness and moving
     clips, next to a live preview that draws a real card with the current look. The Settings home shows a small
     copy of the same preview on its Display card. */
  const sizeInput = document.getElementById('card-size');
  const sizeOut = document.getElementById('card-size-out');
  const dock = document.getElementById('settings-dock');
  const gear = document.getElementById('gear');
  const resetBtn = document.getElementById('card-size-reset');
  gear.appendChild(icon('sliders', 22));
  document.getElementById('set-close').appendChild(icon('close', 18));
  // Cover art brightness: 0 is the original dim look, 100 the brightest; 30 by default.
  const ART_DEFAULT = 30;
  const lightInput = document.getElementById('art-light');
  const lightOut = document.getElementById('art-light-out');
  const artLight = () => { const v = Number(dash.params().artlight); return Number.isFinite(v) && dash.params().artlight != null ? Math.max(0, Math.min(100, v)) : ART_DEFAULT; };
  const styleBox = document.getElementById('card-style');
  const styleOf = () => (CARD_STYLES.includes(dash.params().cardstyle) ? dash.params().cardstyle : 'classic');
  const clipSwitch = document.getElementById('clip-switch');
  let clipShown = null;
  const STYLE_NAMES = { classic: 'Classic', side: 'Poster beside', top: 'Poster on top', banner: 'Banner', tiles: 'Tiles' };
  function syncReset() {
    resetBtn.disabled = Number(sizeInput.value) === 100 && Number(lightInput.value) === ART_DEFAULT && cardStyle === 'classic' && clipMode() === 'on';
  }
  function applyStyle(v) {
    const changed = cardStyle !== v;
    cardStyle = v;
    root.dataset.cstyle = v;
    styleBox.querySelectorAll('input').forEach((i) => { i.checked = i.value === v; i.parentElement.classList.toggle('is-on', i.checked); });
    // Brightness only changes the Classic card; the other styles show the poster at full brightness.
    const classic = v === 'classic';
    lightInput.disabled = !classic;
    document.getElementById('art-light-field').classList.toggle('is-off', !classic);
    document.getElementById('art-light-hint').textContent = classic
      ? 'How much of the poster shows through the shade. Text keeps a shadow so it stays readable.'
      : 'Only for the Classic style. The other styles show the poster at full brightness.';
    syncReset();
    if (changed) renderPreviews();
  }
  function applySize(v) {
    root.style.setProperty('--card-scale', String(v / 100));
    sizeInput.value = v;
    sizeInput.style.setProperty('--fill', ((v - 60) / 40) * 100 + '%');
    sizeOut.textContent = v + '%';
    syncReset();
    zoomPreviews();
  }
  function applyLight(v) {
    root.style.setProperty('--art-shade', String(1 - 0.6 * (v / 100)));
    lightInput.value = v;
    lightInput.style.setProperty('--fill', v + '%');
    lightOut.textContent = v + '%';
    syncReset();
  }
  function applyClipMode(v) {
    clipSwitch.checked = v === 'on';
    document.getElementById('clip-state').textContent = v === 'on' ? 'On' : 'Off';
    syncReset();
    if (clipShown !== null && clipShown !== v) renderPreviews();
    clipShown = v;
  }

  // ---- live preview ----
  const PV_W = { classic: 330, side: 430, top: 300, banner: 430, tiles: 190 };   // about the width each style gets in the grid
  const PV_SAMPLE = { id: 'preview-sample', user: 'mara_k', title: 'Night Harbor', kind: 'Episode', device: 'Living room TV', platform: 'tvOS',
    resolution: '1080p', method: 'Direct play', bitrate_mbps: 8.4, server: 'Atlas', state: 'Playing', progress_pct: 42, elapsed_min: 18,
    location: 'WAN', player: 'Plex for Apple TV', source_video: 'H.264 1080p', stream_video: 'H.264 1080p', video_decision: 'Direct play',
    source_audio: 'EAC3 5.1', stream_audio: 'EAC3 5.1', audio_decision: 'Direct play', container: 'MKV', transcode_reason: 'None',
    hw_accel: 'None', transcode_speed: null, throttled: false, quality: 'Original', subtitles: 'None', clip: null };
  let pvSide = 'front';
  let pvId = null;
  function previewRow() {
    const d = dash.data('streams');
    const rows = d.status === 'ok' ? d.data : [];
    // Keep showing the same stream while it lasts; otherwise prefer one that is playing and has a clip.
    const pick = rows.find((r) => r.id === pvId) || rows.find((r) => r.state === 'Playing' && r.clip)
      || rows.find((r) => r.state === 'Playing') || rows[0];
    pvId = pick ? pick.id : null;
    return pick ? { row: pick, live: true } : { row: PV_SAMPLE, live: false };
  }
  const previewStages = () => Array.from(document.querySelectorAll('[data-sd-preview]')).filter((s) => !dock.hidden && s.offsetParent !== null);
  function zoomPreviews() {
    previewStages().forEach((stage) => {
      const box = stage.firstElementChild;
      if (!box) return;
      const base = PV_W[cardStyle] || 330;
      const fit = (stage.clientWidth || base) / base;
      // The full preview follows the card size slider; the small one on the Settings home just fits its card.
      box.style.zoom = String(Math.min(fit, stage.dataset.sdPreview === 'mini' ? 1 : Number(sizeInput.value) / 100));
    });
  }
  function renderPreviews() {
    const stages = previewStages();
    if (!stages.length) { pvVideos.forEach((v) => v.pause()); return; }
    const { row, live } = previewRow();
    pvVideos.forEach((v) => { v._used = false; });
    stages.forEach((stage) => {
      const mini = stage.dataset.sdPreview === 'mini';
      // Front and back share one spot, like the real flip card, so the back keeps the front's size.
      const box = el('div', 'st-pv st-pv--' + cardStyle + (!mini && pvSide === 'back' ? ' is-back' : ''));
      box.style.width = (PV_W[cardStyle] || 330) + 'px';
      pvMode = true;
      try { box.append(streamFront(row)); if (!mini) box.append(streamBack(row)); } finally { pvMode = false; }
      box.inert = true;   // a picture of a card: nothing in it is clickable
      box.setAttribute('aria-hidden', 'true');
      stage.replaceChildren(box);
    });
    pvVideos.forEach((v, src) => { if (!v._used) { v.pause(); v.removeAttribute('src'); v.load(); pvVideos.delete(src); } });
    zoomPreviews();
    const note = document.getElementById('set-preview-note');
    note.textContent = (live ? 'Using ' + row.user + '\u2019s stream, playing now.' : 'A sample stream: nothing is playing right now.')
      + (pvSide === 'back' ? ' This is the back of the card, what you see when you select it.' : '')
      + ' Changes save as you make them, on this browser only.';
    window.dispatchEvent(new CustomEvent('gs:look', { detail: { style: cardStyle, styleName: STYLE_NAMES[cardStyle], size: Number(sizeInput.value),
      light: Number(lightInput.value), clips: clipMode() } }));
  }
  window.sdRenderPreviews = renderPreviews;
  window.sdLook = () => ({ style: cardStyle, styleName: STYLE_NAMES[cardStyle], size: Number(sizeInput.value), light: Number(lightInput.value), clips: clipMode() });
  window.sdBadge = badge;
  document.querySelectorAll('.st-seg [data-side]').forEach((b) => b.addEventListener('click', () => {
    pvSide = b.dataset.side;
    document.querySelectorAll('.st-seg [data-side]').forEach((x) => x.setAttribute('aria-pressed', x === b ? 'true' : 'false'));
    renderPreviews();
  }));
  if ('ResizeObserver' in window) new ResizeObserver(() => zoomPreviews()).observe(document.getElementById('settings'));

  // ---- open and close ----
  function openSettings(open, fresh) {
    const was = !dock.hidden;
    dock.hidden = !open;
    gear.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (!open) { pvVideos.forEach((v) => v.pause()); if (was) gear.focus(); return; }
    if (window.stOnOpen) window.stOnOpen(fresh);   // settings.js picks the home (admins) or Display (viewers)
    renderPreviews();
  }
  window.sdOpenSettings = openSettings;
  gear.addEventListener('click', () => openSettings(dock.hidden, true));
  document.getElementById('set-close').addEventListener('click', () => openSettings(false));
  document.getElementById('set-scrim').addEventListener('click', () => openSettings(false));
  // Escape closes Settings wherever focus is (a section re-render can drop focus to the page).
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !dock.hidden && !document.querySelector('.rq-modal')) openSettings(false); });
  sizeInput.addEventListener('input', () => applySize(Number(sizeInput.value)));
  sizeInput.addEventListener('change', () => dash.setParams({ cardsize: Number(sizeInput.value) }));
  clipSwitch.addEventListener('change', () => { const v = clipSwitch.checked ? 'on' : 'off'; dash.setParams({ clips: v }); applyClipMode(v); });
  styleBox.addEventListener('change', (e) => { if (e.target.name === 'cstyle') { dash.setParams({ cardstyle: e.target.value }); applyStyle(e.target.value); } });
  lightInput.addEventListener('input', () => applyLight(Number(lightInput.value)));
  lightInput.addEventListener('change', () => dash.setParams({ artlight: Number(lightInput.value) }));
  resetBtn.addEventListener('click', () => {
    dash.setParams({ cardsize: 100, artlight: ART_DEFAULT, cardstyle: 'classic', clips: 'on' });
    applySize(100); applyLight(ART_DEFAULT); applyStyle('classic'); applyClipMode('on');
  });

  // Page-load build ("Build and count", Glenn's pick of five): plays once, as the splash fades.
  // Navigation, header, pills, then cards come in; the big numbers count up and the bandwidth line draws.
  let introPlayed = false;
  function playIntro() {
    if (introPlayed) return;
    introPlayed = true;
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const steps = [];
    const add = (sel, kind, delay, gap) => root.querySelectorAll(sel).forEach((el, i) => {
      if (!el.hidden && el.offsetParent !== null) steps.push([el, kind, delay + i * (gap || 0)]);
    });
    add('.sd-nav', 'left', 0); add('.sd-top', 'rise', 120); add('.sd-lockup', 'left', 180); add('.sd-head', 'left', 220);
    add('#sub-card', 'rise', 260); add('.sd-alertbar', 'drop', 320); add('.sd-pills > .sd-pill', 'drop', 420, 70);
    add('.sd-main > .sd-card', 'rise', 640, 90); add('.sd-insights > .sd-card', 'rise', 820, 90);
    add('#streams-card', 'rise', 1000); add('#people-card', 'rise', 1080); add('#alerts', 'rise', 1140); add('.sd-tabbar', 'rise', 300);
    steps.forEach(([el, kind, d]) => {
      el.style.setProperty('--intro-d', d + 'ms');
      el.classList.add('gs-in', 'gs-in--' + kind);
      el.addEventListener('animationend', function done(e) {
        if (e.target !== el) return;
        el.classList.remove('gs-in', 'gs-in--' + kind); el.style.removeProperty('--intro-d');
        el.removeEventListener('animationend', done);
      });
    });
    root.classList.add('gs-intro-draw');
    setTimeout(() => root.classList.remove('gs-intro-draw'), 2200);
    // Count the big numbers and the pill counts up from zero, then put the live values back.
    const nums = Array.from(root.querySelectorAll('.sd-big, .sd-pill__num')).map((el) => {
      const to = Number(String(el.textContent).replace(/,/g, ''));
      return Number.isFinite(to) ? { el, to, dp: el.dataset.fmt === '1' ? 1 : 0 } : null;
    }).filter(Boolean);
    const start = performance.now() + 700, dur = 900;
    nums.forEach((n) => { n.el.textContent = n.dp ? n1(0) : '0'; });
    const step = (t) => {
      const x = Math.max(0, Math.min(1, (t - start) / dur));
      const e = 1 - Math.pow(1 - x, 3);
      nums.forEach((n) => { n.el.textContent = n.dp ? n1(n.to * e) : n0(Math.round(n.to * e)); });
      if (x < 1) requestAnimationFrame(step); else renderKpis();
    };
    requestAnimationFrame(step);
  }
  document.addEventListener('gs:ready', playIntro, { once: true });

  function renderAll() {
    const tabs = okTabs();
    if (tabs.length && !tabs.some((t) => t.id === mtab)) mtab = tabs[0].id;
    root.dataset.mtab = mtab;
    renderNav();
    document.getElementById('sample-badge').hidden = !(dash.isSample && dash.isSample());
    applySize(Number(dash.params().cardsize) || 100);
    applyLight(artLight());
    applyStyle(styleOf());
    applyClipMode(clipMode());
    renderLook();
    renderKpis();
    renderNewest();
    renderChart();
    renderDaily();
    renderServers();
    renderHeat();
    renderCapacity();
    renderChips();
    renderStreams();
    renderTabbar(renderAlerts());
    if (!dock.hidden) renderPreviews();
  }
  mq.addEventListener('change', renderAll);
  window.sdNavChanged = () => { renderNav(); renderTabbar(renderAlerts()); };
  dash.onData(renderAll);
})();
