/* Greystar Telemetry: Settings screen for Plex servers, Netdata and alerts.
   Tokens are sent to the backend once and never come back to the browser. */
(function () {
  'use strict';

  var tab = 'display';
  var state = null;
  var nodes = [];
  var discovered = null;
  var editingId = null;
  var removingId = null;
  var link = null; // { pin_id, code, auth_url, timer, started, before: [linked account ids] }
  var unlinking = null;
  var msgs = {};
  var busy = {};
  var drafts = {};
  var manualOpen = false;
  var access = null;
  var accUsers = null;
  var accServers = [];
  var accPicker = null;
  var accEditing = null;
  var accRemoving = null;
  var me = null;

  window.stActiveTab = function () { return tab; };
  window.stOpenTab = function (t) { setTab(t); };
  window.stFocusUser = function (id) { accEditing = id || null; };
  function isAdmin() { return !!(me && me.role === 'admin'); }
  document.addEventListener('gs:me', function (e) { me = e.detail; });
  if (window.gsMe) me = window.gsMe;

  // ---------- helpers ----------
  var el = window.gsUI.el;   // shared helpers (ui.js)

  function api(method, path, body) { return window.gsUI.api(method, path, body); }

  function message(key) {
    var m = msgs[key];
    if (!m) return document.createDocumentFragment();
    return el('p', { class: 'st-msg st-msg--' + m.kind, role: m.kind === 'error' ? 'alert' : 'status' },
      m.kind === 'error' ? el('b', { text: 'Error: ' }) : null, m.text);
  }

  function setMsg(key, kind, text) {
    msgs[key] = kind ? { kind: kind, text: text } : null;
    render();
  }

  function field(label, input, hint) {
    var id = input.id || ('st-' + Math.random().toString(36).slice(2, 9));
    input.id = id;
    return el('div', { class: 'st-field' }, el('label', { for: id, text: label }), input, hint ? el('small', { text: hint }) : null);
  }

  function input(props, id) {
    return el('input', Object.assign({ class: 'st-input', type: 'text', autocomplete: 'off', spellcheck: 'false', id: id }, props));
  }

  function nodeInput(current, id) {
    if (state.netdata.mode === 'cloud' && nodes.length) {
      var sel = el('select', { class: 'st-select', id: id }, el('option', { value: '', text: 'Not linked' }));
      nodes.forEach(function (n) {
        var o = el('option', { value: n.id, text: n.name });
        if (n.id === current || (current && n.name.toLowerCase() === String(current).toLowerCase())) o.selected = true;
        sel.appendChild(o);
      });
      return sel;
    }
    return input({ value: current || '', placeholder: 'Netdata node ID or hostname', class: 'st-input is-mono' }, id);
  }

  function nodeName(id) {
    if (!id) return 'not linked';
    var n = nodes.find(function (x) { return x.id === id; });
    return n ? n.name : id;
  }

  function withBusy(key, promise) {
    busy[key] = true;
    render();
    return promise.finally(function () { busy[key] = false; render(); });
  }

  // ---------- data ----------
  function loadState() {
    return api('GET', '/api/settings').then(function (s) {
      state = s;
      if (s.netdata.mode === 'cloud' && s.netdata.has_token && s.netdata.space_id && s.netdata.room_id) {
        return api('GET', '/api/settings/netdata/nodes').then(function (r) { nodes = r.nodes || []; })
          .catch(function () { nodes = []; });
      }
      nodes = [];
    }).then(render).catch(function (e) { setMsg('load', 'error', e.message); });
  }

  function afterChange() {
    if (window.dash && window.dash.refresh) window.dash.refresh();
    return loadState();
  }

  // ---------- views: the Settings home, and one section at a time ----------
  // Admins land on the home of cards; each card opens its section, with a back arrow to the home.
  // Viewers only have Display, so they go straight there.
  var VIEWS = {
    home: { title: 'Settings', lead: 'Four groups. Open one of their sections to change it.' },
    globe: { title: 'Live streams globe', scope: 'account', lead: 'How the globe moves when a stream starts, and how it shows what is playing now. Saved to your admin account, so it looks the same on every device you sign in on.' },
    cards: { title: 'Dashboard Cards', scope: 'account', lead: 'Which cards show on your dashboard. Saved to your admin account, so the same cards show in every browser and on every device you sign in on. Other admins keep their own choice.' },
    display: { title: 'Display', scope: 'you', lead: 'Light or dark, and how the dashboard looks on this screen. Nobody else sees these changes, and your other devices keep their own.' },
    plex: { title: 'Plex servers', scope: 'all', lead: 'Where live streams come from. The dashboard asks each server what is playing every few seconds. Each server can belong to its own Plex account.' },
    netdata: { title: 'Netdata', scope: 'all', lead: 'CPU and upload speed for each server, read from Netdata. They drive the capacity rings, server status and the bandwidth chart.' },
    alerts: { title: 'Alerts', scope: 'all', lead: 'When a server counts as running hot. A hot server turns amber on the capacity rings and shows up under Alerts.' },
    access: { title: 'Access', scope: 'all', lead: 'Who can sign in and what each person can see. Anyone you share a Plex server with can sign in with Plex.' },
    billing: { title: 'Billing', scope: 'all', lead: 'Shows each person their own Stripe subscription. The key is read-only: nothing is ever charged or changed from here.' },
    reqlog: { title: 'Request log', scope: 'all', lead: 'Every movie, series, comic, ebook and audiobook requested through this site: who asked, when, and where it stands now.' },
    requests: { title: 'Requests', scope: 'all', lead: 'Connects your Seerr so people can find movies and series and request them here, without signing in to Seerr. Each person acts as their own Seerr account, so its permissions, limits and approvals still apply.' },
    books: { title: 'Books & comics', scope: 'all', lead: 'Shows the Greystar media site inside this dashboard, so people read comics and ebooks, listen to audiobooks and request more without signing in again.' },
    arr: { title: 'Radarr & Sonarr', scope: 'all', lead: 'Shows people how far their movies and shows have downloaded, and lets Unwatched cleanup remove titles nobody watched (with their files, so they are not downloaded again).' }
  };
  var SCOPE_WORDS = { you: 'This browser only', account: 'Your account', all: 'Everyone' };
  // The Settings home (Glenn's pick C, "Four rooms", Nov 13 2026): twelve sections in four groups. A group's scope tag
  // says who its changes reach; inside a section the exact tag still shows (This browser only, Your account, Everyone).
  var ROOMS = [
    { key: 'you', title: 'Your dashboard', scope: 'Just you', views: ['display', 'cards', 'globe'] },
    { key: 'data', title: 'Servers and data', scope: 'Everyone', views: ['plex', 'netdata', 'alerts'] },
    { key: 'media', title: 'Requests and media', scope: 'Everyone', views: ['requests', 'reqlog', 'arr', 'books'] },
    { key: 'people', title: 'People and billing', scope: 'Everyone', views: ['access', 'billing'] }
  ];
  function roomOf(view) { return ROOMS.filter(function (r) { return r.views.indexOf(view) >= 0; })[0] || null; }
  // Find a setting: words people might look for, per section.
  var FIND = {
    display: 'look theme light dark appearance card style size cover art brightness clips moving home layout',
    cards: 'dashboard cards hide show cards bandwidth servers when people watch capacity users alerts pills',
    globe: 'globe zoom zoom level zoom while turning spin speed turning rotation still rest europe asia americas busiest line dallas outline country city name dots colour color type server join nearby rings pulse night day trail quiet hours',
    plex: 'plex servers server token address ip url machine add remove link account owner',
    netdata: 'netdata cpu network upload bandwidth metrics space room node cloud agent contexts',
    alerts: 'alerts hot cpu threshold degraded amber',
    access: 'access people viewers sections permissions admins admin login password sync users turn off',
    billing: 'billing stripe subscription key customers unmatched match paypal',
    requests: 'requests seerr overseerr api key 4k approve link people movies series',
    reqlog: 'request log csv export needs you searches that found nothing import earlier books comics ebooks audiobooks not released not out yet',
    arr: 'radarr sonarr download progress unwatched cleanup delete remove days review auto',
    books: 'books comics ebooks audiobooks media site secret frame'
  };
  var summary = null;
  var summaryAt = 0;
  var summaryTimer = null;

  function setTab(t) {
    if (!isAdmin()) t = 'display';
    if (!VIEWS[t]) t = 'home';
    var changed = tab !== t;
    tab = t;
    var v = VIEWS[t];
    var panel = document.getElementById('settings');
    panel.dataset.view = t;
    var home = t === 'home';
    panel.classList.toggle('has-back', !home && isAdmin());
    document.getElementById('set-back').hidden = home || !isAdmin();
    var crumb = document.getElementById('set-crumb'), room = roomOf(t);
    crumb.hidden = home || !isAdmin();
    crumb.textContent = room ? 'Settings \u00b7 ' + room.title : 'Settings';
    var sibs = document.getElementById('set-sibs');
    sibs.hidden = home || !isAdmin() || !room;
    if (!sibs.hidden) {
      sibs.setAttribute('aria-label', 'Sections in ' + room.title);
      sibs.replaceChildren.apply(sibs, room.views.map(function (k) {
        return el('button', { type: 'button', class: 'st-sib', 'aria-current': k === t ? 'page' : null, text: VIEWS[k].title, onclick: function () { if (k !== tab) go(k); } });
      }));
    }
    document.getElementById('set-title').textContent = v.title;
    var scope = document.getElementById('set-scope');
    scope.hidden = !v.scope;
    scope.textContent = SCOPE_WORDS[v.scope] || 'Everyone';
    scope.className = 'st-scope st-scope--' + (v.scope || 'none');
    var lead = document.getElementById('set-lead');
    lead.textContent = v.lead;
    lead.hidden = false;
    document.getElementById('card-size-reset').hidden = t !== 'display';
    if (t !== 'globe') dropPreview();
    document.getElementById('st-home').hidden = !home;
    document.querySelectorAll('.st-pane').forEach(function (pane) { pane.hidden = pane.id !== 'st-pane-' + t; });
    if (changed) { panel.scrollTop = 0; }
    clearInterval(summaryTimer);
    if (home) {
      renderHome();
      loadSummary();
      summaryTimer = setInterval(function () { if (!document.getElementById('settings-dock').hidden && tab === 'home') loadSummary(); }, 20000);
    } else if (t === 'access') loadAccess();
    else if (t === 'billing') loadBilling();
    else if (t === 'requests') loadRequests();
    else if (t === 'books') loadBooks();
    else if (t === 'arr') loadArr();
    else if (t === 'reqlog') openLog(changed);
    else if (t === 'cards') loadCards();
    else if (t === 'globe') renderGlobe();
    else if (t !== 'display') loadState();
    if (window.sdRenderPreviews) window.sdRenderPreviews();
  }

  // Called when Settings opens. fresh: opened from the gear, so start at the home.
  window.stOnOpen = function (fresh) {
    if (fresh || !isAdmin()) setTab(isAdmin() ? 'home' : 'display');
    else if (tab === 'home') loadSummary();
    var t = document.getElementById('set-title');
    if (t) t.focus({ preventScroll: true });
  };

  function go(t) {
    setTab(t);
    var title = document.getElementById('set-title');
    if (title) title.focus({ preventScroll: true });
  }
  document.getElementById('set-back').appendChild(window.sdIcon ? window.sdIcon('arrow', 18) : document.createTextNode('<'));
  document.getElementById('set-back').addEventListener('click', function () { go('home'); });

  function loadSummary() {
    if (!isAdmin()) return Promise.resolve();
    return api('GET', '/api/settings/summary').then(function (r) {
      summary = r; summaryAt = Date.now();
      if (tab === 'home') renderHome();
    }).catch(function (e) { msgs.home = { kind: 'error', text: e.message }; if (tab === 'home') renderHome(); });
  }

  function since(ts) { return ts ? window.gsUI.ago(ts) : null; }   // "5 min ago"

  function badgeEl(tone, text) {
    if (window.sdBadge) return window.sdBadge(tone, text);
    return el('span', { class: 'gs-badge gs-badge--' + tone, text: text });
  }

  function names(list, max) {
    max = max || 2;
    return list.length <= max ? list.join(' and ') : list.slice(0, max).join(', ') + ' and ' + (list.length - max) + ' more';
  }

  // What needs a look, most important first. Each item opens the section that fixes it.
  function problems(s) {
    var out = [];
    if (!s) return out;
    if (!s.servers.total) out.push({ view: 'plex', tone: 'warning', text: 'No Plex servers yet. The dashboard shows sample data until you add one.' });
    if (s.servers.offline.length) out.push({ view: 'plex', tone: 'danger', text: names(s.servers.offline) + (s.servers.offline.length === 1 ? ' is' : ' are') + ' not answering.' });
    if (s.netdata.error) out.push({ view: 'netdata', tone: 'danger', text: 'Netdata: the last check failed.' });
    if (s.netdata.unlinked.length) out.push({ view: 'plex', tone: 'warning', text: names(s.netdata.unlinked) + (s.netdata.unlinked.length === 1 ? ' has' : ' have') + ' no Netdata node, so CPU and upload are unknown.' });
    else if (s.netdata.mode !== 'off' && !s.netdata.error && s.netdata.of && s.netdata.reporting < s.netdata.of)
      out.push({ view: 'netdata', tone: 'warning', text: 'CPU is coming in from ' + s.netdata.reporting + ' of ' + s.netdata.of + ' servers. Check the metric names.' });
    if (s.access.sync_error) out.push({ view: 'access', tone: 'danger', text: 'Loading people from Plex failed.' });
    else if (s.accounts && !s.access.synced_at) out.push({ view: 'access', tone: 'warning', text: 'People have not been loaded from Plex yet.' });
    if (s.billing.connected && s.billing.error) out.push({ view: 'billing', tone: 'danger', text: 'Stripe: the last sync failed.' });
    if (s.requests && s.requests.connected && s.requests.error) out.push({ view: 'requests', tone: 'danger', text: 'Seerr: ' + s.requests.error });
    if (s.requests && s.requests.pending) out.push({ view: 'requests', tone: 'warning', text: s.requests.pending + (s.requests.pending === 1 ? ' request is' : ' requests are') + ' waiting for approval.', go: '#/requests/manage' });
    if (s.requests && s.requests.needs_you) out.push({ view: 'reqlog', tone: 'danger', text: s.requests.needs_you + (s.requests.needs_you === 1 ? ' failed request needs' : ' failed requests need') + ' you: tried again once, then sent to you.' });
    if (s.cleanup && s.cleanup.mode !== 'off' && !(s.cleanup.radarr && s.cleanup.sonarr)) out.push({ view: 'arr', tone: 'warning', text: 'Unwatched cleanup needs Radarr and Sonarr connected to remove anything.' });
    if (s.cleanup && s.cleanup.due) out.push({ view: 'arr', tone: 'warning', text: s.cleanup.due + (s.cleanup.due === 1 ? ' unwatched title runs' : ' unwatched titles run') + ' out within 3 days.', go: '#/cleanup' });
    if (s.billing.connected && s.billing.unmatched) out.push({ view: 'billing', tone: 'warning', text: s.billing.unmatched + ' Stripe ' + (s.billing.unmatched === 1 ? 'customer is' : 'customers are') + ' not matched to a Plex user.' });
    return out;
  }

  // ---------- the home: "Needs a look", then four groups ("rooms"), each with a headline and a row per section ----------
  var findQ = '';
  // A section's state in a few words: [tone, words]. Tone gives the shape: success circle, warning triangle,
  // danger square, neutral ring (never colour alone).
  function rowState(view, s, look) {
    var gc = window.gsCards, gs = window.gsGlobeSettings ? window.gsGlobeSettings.get() : null;
    if (view === 'display') return ['neutral', look.styleName + ', clips ' + (look.clips === 'on' ? 'on' : 'off')];
    if (view === 'cards') { var off = gc ? gc.hidden().length : 0; return off ? ['neutral', off + (off === 1 ? ' card hidden' : ' cards hidden')] : ['success', 'All ' + (gc ? gc.keys.length : 9) + ' showing']; }
    if (view === 'globe') {
      if (!gs) return ['neutral', 'Default'];
      var bits = [gs.zoom > 1 ? 'Zooms to ' + gs.zoom + 'x' : 'No zoom'];
      if (gs.cruise > 1) bits.push('turns at ' + gs.cruise + 'x');
      else if (!gs.speed) bits.push('still');
      return ['neutral', bits.join(', ')];
    }
    if (!s) return null;
    var sv = s.servers, nd = s.netdata, al = s.alerts, ac = s.access, bl = s.billing, rq = s.requests || {}, lg = rq.log || {}, bk = s.books || {};
    if (view === 'plex') return !sv.total ? ['warning', 'No servers yet'] : sv.offline.length ? ['danger', names(sv.offline, 1) + (sv.offline.length === 1 ? ' is down' : ' are down')]
      : ['success', sv.enabled === 1 ? 'Online' : 'All ' + sv.enabled + ' online'];
    if (view === 'netdata') return nd.mode === 'off' ? ['neutral', 'Off'] : nd.error ? ['danger', 'Last check failed']
      : nd.of && nd.reporting < nd.of ? ['warning', nd.reporting + ' of ' + nd.of + ' reporting'] : ['success', nd.reporting + ' of ' + nd.of + ' reporting'];
    if (view === 'alerts') return al.hot.length ? ['warning', names(al.hot, 1) + (al.hot.length === 1 ? ' is hot' : ' are hot')] : ['neutral', 'Hot at ' + Math.round(al.hot_cpu_pct) + '% CPU'];
    if (view === 'access') return ac.sync_error ? ['danger', 'Loading people failed'] : !ac.synced_at ? ['warning', 'People not loaded'] : ['neutral', (ac.custom + ac.off) + ' custom or off'];
    if (view === 'billing') return !bl.connected ? ['neutral', 'Not connected'] : bl.error ? ['danger', 'Last sync failed'] : bl.unmatched ? ['warning', bl.unmatched + ' not matched'] : ['success', 'Synced ' + (since(bl.synced_at) || 'soon')];
    if (view === 'requests') return !rq.connected ? ['neutral', 'Not connected'] : rq.error ? ['danger', 'Seerr not answering'] : rq.pending ? ['warning', rq.pending + ' waiting'] : ['success', 'Seerr on'];
    if (view === 'reqlog') return rq.needs_you ? ['danger', rq.needs_you + ' need' + (rq.needs_you === 1 ? 's' : '') + ' you'] : lg.total ? ['neutral', (lg.day || 0) + ' in the last 24 h'] : ['neutral', 'Nothing yet'];
    if (view === 'books') return bk.enabled ? ['success', 'On'] : ['neutral', bk.has_secret ? 'Off' : 'Not connected'];
    if (view === 'arr') {
      var ar = s.arr || {}, on = ['radarr', 'sonarr'].filter(function (k) { return ar[k] && ar[k].url && ar[k].has_key; });
      if (!on.length) return ['neutral', 'Not connected'];
      if (['radarr', 'sonarr'].some(function (k) { return ar[k] && ar[k].error; })) return ['danger', 'Last check failed'];
      return on.length < 2 ? ['warning', (on[0] === 'radarr' ? 'Radarr' : 'Sonarr') + ' only'] : ['success', 'Connected'];
    }
    return null;
  }
  function shapeEl(tone) { return el('i', { class: 'st-shape st-shape--' + (tone || 'neutral'), 'aria-hidden': 'true' }); }
  function stateEl(st) {
    if (!st) return el('span', { class: 'st-rstate', text: '\u2026' });
    return el('span', { class: 'st-rstate' }, st[0] === 'neutral' ? null : shapeEl(st[0]), el('span', { text: st[1] }), st[0] !== 'neutral' ? el('span', { class: 'sd-sr', text: ' (' + { success: 'fine', warning: 'needs a look', danger: 'problem' }[st[0]] + ')' }) : null);
  }
  function sectionRow(view, s, look, extra) {
    return el('button', { type: 'button', class: 'st-rrow', onclick: function () { go(view); } },
      el('span', { class: 'st-rrow__name' }, VIEWS[view].title, extra ? el('small', { text: extra }) : null),
      stateEl(rowState(view, s, look)),
      window.sdIcon ? window.sdIcon('arrow', 16) : null);
  }
  function headline(room, s) {
    var big = function (n, unit) { return el('div', { class: 'st-room__big' }, el('b', { text: String(n) }), unit ? el('span', { text: unit }) : null); };
    if (room.key === 'you') {
      var gc = window.gsCards, off = gc ? gc.hidden().length : 0, all = gc ? gc.keys.length : 9;
      return big((all - off) + ' of ' + all, 'cards showing');
    }
    if (!s) return null;
    if (room.key === 'data') {
      var sv = s.servers;
      if (!sv.total) return big(0, 'servers yet');
      var up = (sv.list || []).filter(function (x) { return x.status !== 'Offline'; }).length;
      var rack = el('div', { class: 'st-rack', 'aria-hidden': 'true' }, (sv.list || []).map(function (x) {
        return el('i', { class: 'st-rack__u' + (x.status === 'Offline' ? ' is-down' : x.status === 'Degraded' ? ' is-hot' : ''), title: x.name + ': ' + (x.status || '') });
      }));
      return el('div', { class: 'st-room__head-r' }, big(up + ' of ' + (sv.list || []).length, 'servers up'), rack);
    }
    if (room.key === 'media') {
      var rq = s.requests || {}, lg = rq.log || {};
      return rq.connected || lg.total ? big(lg.week || 0, 'requests this week') : big('Off', 'requests not connected');
    }
    if (room.key === 'people') {
      return el('div', { class: 'st-room__pair' }, big(s.access.people, s.access.people === 1 ? 'person' : 'people'),
        s.billing.connected ? big(s.billing.customers, s.billing.customers === 1 ? 'customer' : 'customers') : null);
    }
    return null;
  }
  function needsBar(issues) {
    if (!summary) return el('section', { class: 'st-needs-bar is-quiet', 'aria-label': 'Needs a look' }, el('span', { class: 'st-needs-bar__t', text: msgs.home ? 'Could not load the overview: ' + msgs.home.text : 'Checking servers and connections\u2026' }));
    if (!issues.length) return el('section', { class: 'st-needs-bar is-quiet', 'aria-label': 'Needs a look' }, shapeEl('success'), el('span', { class: 'st-needs-bar__t', text: 'Nothing needs a look' }));
    return el('section', { class: 'st-needs-bar', 'aria-label': 'Needs a look' },
      el('span', { class: 'st-needs-bar__t', text: issues.length === 1 ? 'Needs a look' : issues.length + ' things need a look' }),
      el('ul', { class: 'st-needs-bar__list' }, issues.slice(0, 6).map(function (p) {
        return el('li', null, el('button', { type: 'button', class: 'st-needs-bar__item', onclick: function () {
          if (p.go) { if (window.sdOpenSettings) window.sdOpenSettings(false); location.hash = p.go; return; }
          go(p.view);
        } }, shapeEl(p.tone), el('span', { text: p.text.replace(/\.$/, '') })));
      })));
  }
  function findBox() {
    var input = el('input', { type: 'search', class: 'st-find__input', id: 'st-find', placeholder: 'Find a setting: zoom, Seerr, CPU, Stripe\u2026', autocomplete: 'off', value: findQ,
      'aria-label': 'Find a setting',
      oninput: function () { findQ = input.value; renderHomeBody(); },
      onkeydown: function (e) {
        if (e.key === 'Enter') { var first = document.querySelector('#st-home .st-found .st-rrow'); if (first) first.click(); }
        if (e.key === 'Escape' && findQ) { e.stopPropagation(); findQ = ''; input.value = ''; renderHomeBody(); }
      } });
    return el('label', { class: 'st-find' }, window.sdIcon ? window.sdIcon('search', 18) : null, input);
  }
  function found(q, s, look) {
    q = q.trim().toLowerCase();
    var hits = [];
    ROOMS.forEach(function (room) {
      room.views.forEach(function (v) {
        var title = VIEWS[v].title.toLowerCase(), words = (FIND[v] || '').split(' ');
        var word = title.indexOf(q) >= 0 ? null : (FIND[v] || '').indexOf(q) >= 0 ? words.filter(function (w) { return w.indexOf(q.split(' ')[0]) >= 0; })[0] || q : undefined;
        if (word !== undefined) hits.push({ view: v, room: room, word: word, first: title.indexOf(q) === 0 ? 0 : 1 });
      });
    });
    hits.sort(function (a, b) { return a.first - b.first; });
    if (!hits.length) return el('div', { class: 'st-found' }, el('p', { class: 'st-note', text: 'No setting matches \u201c' + q + '\u201d.' }));
    return el('div', { class: 'st-found', role: 'list', 'aria-label': 'Settings that match' }, hits.map(function (h) {
      return el('div', { role: 'listitem' }, sectionRow(h.view, s, look, h.room.title + (h.word ? ' \u00b7 ' + h.word : '')));
    }));
  }
  function renderHomeBody() {
    var body = document.getElementById('st-home-body');
    if (!body) return;
    var s = summary;
    var look = window.sdLook ? window.sdLook() : { styleName: 'Classic', size: 100, light: 30, clips: 'on', style: 'classic' };
    if (findQ.trim()) { body.replaceChildren(found(findQ, s, look)); return; }
    var rooms = ROOMS.map(function (room) {
      var views = room.views.filter(function (v) {
        if (v === 'cards' && !window.gsCards) return false;
        if (v === 'reqlog') { var rq = (s && s.requests) || {}; return !s || rq.connected || (rq.log || {}).total; }
        return true;
      });
      return el('section', { class: 'st-room st-room--' + room.key, 'aria-labelledby': 'st-room-' + room.key },
        el('div', { class: 'st-room__head' },
          el('div', { class: 'st-room__titles' },
            el('span', { class: 'st-scope st-room__scope ' + (room.key === 'you' ? 'st-scope--you' : 'st-scope--all'), text: room.scope }),
            el('h3', { class: 'st-room__title', id: 'st-room-' + room.key, text: room.title })),
          headline(room, s)),
        el('div', { class: 'st-room__rows' }, views.map(function (v) { return sectionRow(v, s, look); })));
    });
    body.replaceChildren(needsBar(problems(s)), el('div', { class: 'st-rooms' }, rooms));
  }
  function renderHome() {
    var box = document.getElementById('st-home');
    if (!box) return;
    var had = document.activeElement && document.activeElement.id === 'st-find';
    if (!document.getElementById('st-home-body')) box.replaceChildren(findBox(), el('div', { id: 'st-home-body', class: 'st-home__body' }));
    renderHomeBody();
    if (had) document.getElementById('st-find').focus();
  }

  window.addEventListener('gs:look', function () { if (tab === 'home') renderHomeBody(); });

  // ---------- Plex: sign in ----------
  function startLink() {
    var win = window.open('about:blank', '_blank');
    withBusy('link', api('POST', '/api/settings/plex/link').then(function (pin) {
      link = { pin_id: pin.pin_id, code: pin.code, auth_url: pin.auth_url, started: Date.now(),
        before: (state.plex_accounts || []).map(function (a) { return a.id; }) };
      if (win) win.location = pin.auth_url;
      pollLink();
    }).catch(function (e) {
      if (win) win.close();
      setMsg('plex', 'error', e.message);
    }));
  }

  function pollLink() {
    if (!link) return;
    if (Date.now() - link.started > 5 * 60 * 1000) {
      link = null;
      setMsg('plex', 'error', 'Sign-in timed out. Try again.');
      return;
    }
    api('GET', '/api/settings/plex/link/' + link.pin_id).then(function (r) {
      if (r.linked) {
        var again = link.before.indexOf(r.account.id) >= 0;
        link = null;
        msgs.plex = again
          ? { kind: 'error', text: (r.account.email || r.account.name) + ' was already linked. Plex signed you in with the account this browser is already using: sign out at app.plex.tv (or use a private window), then link the next account.' }
          : { kind: 'ok', text: 'Linked ' + (r.account.email || r.account.name) + '. Finding its servers…' };
        loadState().then(function () { if (!again) discover(r.account.id); });
      } else {
        link.timer = setTimeout(pollLink, 2000);
        render();
      }
    }).catch(function (e) {
      link = null;
      setMsg('plex', 'error', e.message);
    });
  }

  function cancelLink() {
    if (link && link.timer) clearTimeout(link.timer);
    link = null;
    render();
  }

  function unlink(id) {
    api('DELETE', '/api/settings/plex/accounts/' + encodeURIComponent(id)).then(function () {
      unlinking = null;
      if (discovered) discovered = discovered.filter(function (d) { return d.account !== id; });
      msgs.plex = { kind: 'ok', text: 'Unlinked. Servers you already added keep their own access.' };
      return loadState();
    }).catch(function (e) { setMsg('plex', 'error', e.message); });
  }

  function discover(accountId) {
    return withBusy('discover-' + (accountId || 'all'), api('GET', '/api/settings/plex/discover' + (accountId ? '?account=' + encodeURIComponent(accountId) : '')).then(function (r) {
      var found = r.servers || [];
      // Keep what other accounts already found; replace this account's results.
      discovered = (discovered || []).filter(function (d) { return accountId && d.account !== accountId; }).concat(found);
      var errs = (r.errors || []).join('; ');
      msgs.plex = errs ? { kind: 'error', text: 'Could not reach plex.tv for ' + errs }
        : !found.length ? { kind: 'error', text: 'That Plex account does not own any servers.' } : null;
    }).catch(function (e) { setMsg('plex', 'error', e.message); }));
  }

  // ---------- Plex: add / edit / remove ----------
  function addServer(body, key) {
    msgs[key] = null;
    return withBusy(key, api('POST', '/api/settings/servers', body).then(function (r) {
      msgs[key] = null;
      clearDrafts(key);
      if (key === 'manual') manualOpen = false;
      msgs.plex = { kind: 'ok', text: 'Added ' + body.name + '. Plex ' + (r.version || '') + ', ' + r.sessions + ' playing now.' };
      if (discovered) discovered.forEach(function (d) { if (d.machine_id === body.machine_id) d.added = true; });
      return afterChange();
    }).catch(function (e) { setMsg(key, 'error', e.message); }));
  }

  function testServer(body, key) {
    msgs[key] = null;
    return withBusy(key + '-test', api('POST', '/api/settings/servers/test', body).then(function (r) {
      setMsg(key, 'ok', 'Connected to ' + (r.friendly_name || 'Plex') + ' (Plex ' + (r.version || '?') + '). ' + r.sessions + ' playing now.');
    }).catch(function (e) { setMsg(key, 'error', e.message); }));
  }

  function patchServer(id, body, key) {
    return withBusy(key, api('PATCH', '/api/settings/servers/' + id, body).then(function () {
      editingId = null;
      clearDrafts(key);
      msgs[key] = null;
      return afterChange();
    }).catch(function (e) { setMsg(key, 'error', e.message); }));
  }

  var moveTo = {};  // server id -> where its history goes when it is removed
  function removeServer(id) {
    var to = moveTo[id] ? '?move_to=' + encodeURIComponent(moveTo[id]) : '';
    return withBusy('remove-' + id, api('DELETE', '/api/settings/servers/' + id + to).then(function () {
      removingId = null;
      delete moveTo[id];
      if (discovered) discover();
      return afterChange();
    }).catch(function (e) { setMsg('plex', 'error', e.message); }));
  }

  // ---------- render: Plex ----------
  function renderAccount() {
    var accounts = state.plex_accounts || [];
    var sec = el('section', { class: 'st-section' }, el('h3', { text: 'Plex accounts' }),
      el('p', { class: 'st-note', text: 'Link the Plex account that owns each server. The dashboard then finds that account’s servers, their access tokens and the people they are shared with.' }));
    if (accounts.length) {
      var list = el('ul', { class: 'st-list' });
      accounts.forEach(function (a) {
        list.appendChild(el('li', { class: 'st-item' },
          el('div', { class: 'st-item__top' },
            el('span', { class: 'st-dot st-dot--online', 'aria-hidden': 'true' }),
            el('span', { class: 'st-item__name', text: a.name || a.username || 'Plex account' }),
            el('span', { class: 'st-spacer' }),
            el('button', { type: 'button', class: 'st-btn', text: busy['discover-' + a.id] ? 'Finding…' : 'Find servers', disabled: busy['discover-' + a.id],
              onclick: function () { discover(a.id); } }),
            unlinking === a.id ? null : el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Unlink…', onclick: function () { unlinking = a.id; render(); } })),
          el('div', { class: 'st-item__meta', text: (a.email || '') + (a.servers.length ? ' · owns ' + a.servers.join(', ') : ' · no monitored servers yet') }),
          unlinking === a.id ? el('div', { class: 'st-row' },
            el('span', { class: 'st-note', text: 'Unlink ' + (a.email || a.name) + '? Its servers keep working, but its shared users stop updating.' }),
            el('span', { class: 'st-spacer' }),
            el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Keep', onclick: function () { unlinking = null; render(); } }),
            el('button', { type: 'button', class: 'st-btn st-btn--danger', text: 'Unlink', onclick: function () { unlink(a.id); } })) : null));
      });
      sec.appendChild(list);
    }
    if (link) {
      sec.appendChild(el('p', { class: 'st-note' }, 'Approve access in the Plex tab that just opened. If it did not open, ',
        el('a', { href: link.auth_url, target: '_blank', rel: 'noopener', text: 'open the Plex sign-in page' }), '. Code ',
        el('span', { class: 'st-code', text: link.code })));
      sec.appendChild(el('div', { class: 'st-row' }, el('button', { type: 'button', class: 'st-btn', text: 'Cancel', onclick: cancelLink })));
    } else {
      sec.appendChild(el('div', { class: 'st-row' },
        el('button', { type: 'button', class: 'st-btn st-btn--accent', text: busy.link ? 'Opening Plex…' : accounts.length ? 'Link another Plex account' : 'Sign in with Plex',
          disabled: busy.link, onclick: startLink })));
      if (accounts.length) {
        sec.appendChild(el('p', { class: 'st-note', text: 'Plex signs in with whatever account this browser is already using. To link a different one, sign out at app.plex.tv first, or do this in a private window.' }));
      }
    }
    sec.appendChild(message('plex'));
    return sec;
  }

  function renderDiscovered() {
    if (!discovered || !discovered.length) return null;
    var list = el('ul', { class: 'st-list' });
    discovered.forEach(function (d, i) {
      var key = 'disc-' + i;
      var item = el('li', { class: 'st-item' });
      item.appendChild(el('div', { class: 'st-item__top' },
        el('span', { class: 'st-item__name', text: d.name }),
        el('span', { class: 'st-tag', text: 'Plex ' + (d.version || '?') }),
        d.account_name ? el('span', { class: 'st-tag', text: d.account_name }) : null,
        el('span', { class: 'st-spacer' }),
        d.added ? el('span', { class: 'st-tag st-tag--ok', text: 'Added' }) : null));
      if (!d.added) {
        var group = 'conn-' + i;
        var conns = (d.connections || []).slice().sort(function (a, b) { return (a.relay - b.relay) || (b.local - a.local); });
        var connBox = el('div', { class: 'st-conn', role: 'radiogroup', 'aria-label': 'Address for ' + d.name });
        var custom = input({ placeholder: 'http://10.0.0.11:32400', class: 'st-input is-mono', 'aria-label': 'Custom address for ' + d.name }, key + '-custom');
        conns.forEach(function (c, j) {
          var label = c.relay ? 'Relay, slow' : c.local ? 'Local network' : 'Remote';
          connBox.appendChild(el('label', null, el('input', { type: 'radio', name: group, value: c.uri, checked: j === 0 }), el('code', { text: c.uri }), el('span', { class: 'st-tag', text: label })));
        });
        connBox.appendChild(el('label', null, el('input', { type: 'radio', name: group, value: '__custom', checked: !conns.length }), 'Custom address, for example over ZeroTier'));
        var name = input({ value: d.name }, key + '-name');
        var max = input({ type: 'number', min: 1, max: 200, value: 8 }, key + '-max');
        var node = nodeInput(null, key + '-node');
        var form = el('div', { class: 'st-form' },
          el('div', { class: 'st-wide' }, connBox), el('div', { class: 'st-wide' }, custom),
          field('Name on the dashboard', name), field('Stream slots', max, 'Shown as "4 / 8"'),
          el('div', { class: 'st-wide' }, field('Netdata node', node)));
        var pick = function () {
          var r = item.querySelector('input[name="' + group + '"]:checked');
          return !r || r.value === '__custom' ? custom.value.trim() : r.value;
        };
        item.appendChild(form);
        item.appendChild(el('div', { class: 'st-row' },
          el('button', { type: 'button', class: 'st-btn', text: busy[key + '-test'] ? 'Testing…' : 'Test', disabled: busy[key + '-test'],
            onclick: function () { testServer({ plex_url: pick(), machine_id: d.machine_id }, key); } }),
          el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy[key] ? 'Adding…' : 'Add server', disabled: busy[key],
            onclick: function () {
              addServer({ name: name.value.trim(), plex_url: pick(), machine_id: d.machine_id, max_streams: Number(max.value) || 8, netdata_node: node.value || null }, key);
            } })));
        item.appendChild(message(key));
      }
      list.appendChild(item);
    });
    return el('section', { class: 'st-section' }, el('h3', { text: 'Servers found on your Plex accounts' }),
      el('p', { class: 'st-note', text: 'Pick the address this dashboard should use. A local or private address is fastest.' }), list);
  }

  // Removing a server asks where its watch history goes, so covers and names follow it to the server it is now.
  function removeConfirm(s) {
    var others = (state.servers || []).filter(function (x) { return x.id !== s.id; });
    var pick = el('select', { class: 'st-select', id: 'move-' + s.id, onchange: function () { moveTo[s.id] = pick.value || null; } },
      el('option', { value: '', text: 'Keep it for when this server is added back' }),
      others.map(function (x) { return el('option', { value: String(x.id), text: x.name, selected: String(moveTo[s.id]) === String(x.id) }); }));
    return el('div', { class: 'st-remove' },
      el('label', { class: 'st-note', for: 'move-' + s.id, text: 'Remove ' + s.name + '? Move this server\'s history to:' }),
      pick,
      el('span', { class: 'st-row' },
        el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Keep server', onclick: function () { removingId = null; render(); } }),
        el('button', { type: 'button', class: 'st-btn st-btn--danger', text: busy['remove-' + s.id] ? 'Removing…' : 'Remove', onclick: function () { removeServer(s.id); } })));
  }

  function editForm(s) {
    var key = 'edit-' + s.id;
    var name = input({ value: s.name }, key + '-name');
    var url = input({ value: s.plex_url, class: 'st-input is-mono' }, key + '-url');
    var token = input({ type: 'password', placeholder: s.has_token ? 'Saved. Leave blank to keep it.' : 'Paste a Plex token', autocomplete: 'new-password' }, key + '-token');
    var max = input({ type: 'number', min: 1, max: 200, value: s.max_streams }, key + '-max');
    var cap = input({ type: 'number', min: 1, value: s.capacity_mbps }, key + '-cap');
    var node = nodeInput(s.netdata_node, key + '-node');
    var agent = state.netdata.mode === 'agent' ? input({ value: s.netdata_agent_url || '', placeholder: 'http://10.0.0.11:19999', class: 'st-input is-mono' }, key + '-agent') : null;
    var body = function () {
      var b = { name: name.value.trim(), plex_url: url.value.trim(), max_streams: Number(max.value) || 8, capacity_mbps: Number(cap.value) || 1000, netdata_node: node.value || '' };
      if (token.value) b.token = token.value;
      if (agent) b.netdata_agent_url = agent.value.trim();
      return b;
    };
    return el('div', { class: 'st-form' },
      field('Name', name), field('Stream slots', max),
      el('div', { class: 'st-wide' }, field('Plex address', url, 'Server changed IP? Change the address here instead of removing the server, so its history and covers stay with it.')),
      el('div', { class: 'st-wide' }, field('Plex token', token, 'Only needed if you did not sign in with Plex or the token changed.')),
      field('Uplink capacity, Mbps', cap), field('Netdata node', node),
      agent ? el('div', { class: 'st-wide' }, field('Netdata agent address', agent)) : null,
      el('div', { class: 'st-wide st-row' },
        el('button', { type: 'button', class: 'st-btn', text: busy[key + '-test'] ? 'Testing…' : 'Test', disabled: busy[key + '-test'],
          onclick: function () { var b = body(); testServer({ plex_url: b.plex_url, token: b.token, server_id: s.id }, key); } }),
        el('span', { class: 'st-spacer' }),
        el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Cancel', onclick: function () { editingId = null; msgs[key] = null; render(); } }),
        el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy[key] ? 'Saving…' : 'Save', disabled: busy[key],
          onclick: function () { patchServer(s.id, body(), key); } })),
      el('div', { class: 'st-wide' }, message(key)));
  }

  function renderServers() {
    var sec = el('section', { class: 'st-section' }, el('h3', { text: 'Your servers' }));
    if (!state.servers.length) {
      sec.appendChild(el('p', { class: 'st-note', text: 'No servers yet. The dashboard shows sample data until you add one.' }));
      return sec;
    }
    var list = el('ul', { class: 'st-list' });
    state.servers.forEach(function (s) {
      var st = s.state || {};
      var online = st.online === true;
      var item = el('li', { class: 'st-item' });
      item.appendChild(el('div', { class: 'st-item__top' },
        el('span', { class: 'st-dot ' + (!s.enabled ? '' : online ? 'st-dot--online' : st.online === false ? 'st-dot--offline' : ''), 'aria-hidden': 'true' }),
        el('span', { class: 'st-item__name', text: s.name }),
        el('span', { class: 'st-tag', text: !s.enabled ? 'Paused' : online ? 'Online' : st.online === false ? 'Offline' : 'Checking' }),
        el('span', { class: 'st-spacer' }),
        el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Edit', 'aria-expanded': editingId === s.id ? 'true' : 'false',
          onclick: function () { editingId = editingId === s.id ? null : s.id; removingId = null; render(); } })));
      item.appendChild(el('div', { class: 'st-item__meta is-mono', text: s.plex_url }));
      item.appendChild(el('div', { class: 'st-item__meta', text: 'Up to ' + s.max_streams + ' streams · Netdata: ' + nodeName(s.netdata_node) + (s.has_token ? '' : ' · No token saved') }));
      if (s.enabled && st.error) item.appendChild(el('p', { class: 'st-msg st-msg--error' }, el('b', { text: 'Error: ' }), st.error));
      if (editingId === s.id) {
        item.appendChild(editForm(s));
        item.appendChild(el('div', { class: 'st-row' },
          el('button', { type: 'button', class: 'st-btn', text: s.enabled ? 'Pause monitoring' : 'Resume monitoring',
            onclick: function () { patchServer(s.id, { enabled: !s.enabled }, 'edit-' + s.id); } }),
          el('span', { class: 'st-spacer' }),
          removingId === s.id
            ? removeConfirm(s)
            : el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Remove server…', onclick: function () { removingId = s.id; render(); } })));
      }
      list.appendChild(item);
    });
    sec.appendChild(list);
    return sec;
  }

  function renderManual() {
    var key = 'manual';
    var name = input({ placeholder: 'Atlas' }, key + '-name');
    var url = input({ placeholder: 'http://10.0.0.11:32400', class: 'st-input is-mono' }, key + '-url');
    var token = input({ type: 'password', placeholder: (state.plex_accounts || []).length === 1 ? 'Optional: uses your linked Plex account' : 'X-Plex-Token', autocomplete: 'new-password' }, key + '-token');
    var max = input({ type: 'number', min: 1, max: 200, value: 8 }, key + '-max');
    var cap = input({ type: 'number', min: 1, value: 1000 }, key + '-cap');
    var node = nodeInput(null, key + '-node');
    var body = function () {
      return { name: name.value.trim(), plex_url: url.value.trim(), token: token.value || null, max_streams: Number(max.value) || 8, capacity_mbps: Number(cap.value) || 1000, netdata_node: node.value || null };
    };
    return el('details', { class: 'st-section st-details', id: 'manual-details', open: !!msgs[key] || manualOpen, ontoggle: function (e) { manualOpen = e.target.open; } },
      el('summary', { text: 'Add a server by address' }),
      el('div', { class: 'st-form' },
        field('Name', name), field('Stream slots', max),
        el('div', { class: 'st-wide' }, field('Plex address', url, 'Include the port, usually 32400.')),
        el('div', { class: 'st-wide' }, field('Plex token', token)),
        field('Uplink capacity, Mbps', cap), field('Netdata node', node),
        el('div', { class: 'st-wide st-row' },
          el('button', { type: 'button', class: 'st-btn', text: busy[key + '-test'] ? 'Testing…' : 'Test', disabled: busy[key + '-test'],
            onclick: function () { var b = body(); testServer({ plex_url: b.plex_url, token: b.token }, key); } }),
          el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy[key] ? 'Adding…' : 'Add server', disabled: busy[key],
            onclick: function () { addServer(body(), key); } })),
        el('div', { class: 'st-wide' }, message(key))));
  }

  // ---------- render: Netdata ----------
  function renderNetdata(pane) {
    var nd = state.netdata;
    var mode = el('select', { class: 'st-select', id: 'nd-mode' },
      el('option', { value: 'cloud', text: 'Netdata Cloud' }),
      el('option', { value: 'agent', text: 'Directly from each agent' }),
      el('option', { value: 'off', text: 'Off' }));
    mode.value = nd.mode;
    var space = input({ value: nd.space_id || '', class: 'st-input is-mono' }, 'nd-space');
    var room = input({ value: nd.room_id || '', class: 'st-input is-mono' }, 'nd-room');
    var token = input({ type: 'password', placeholder: nd.has_token ? 'Saved. Leave blank to keep it.' : 'Netdata Cloud API token', autocomplete: 'new-password' }, 'nd-token');
    var cpu = input({ value: nd.cpu_context, class: 'st-input is-mono' }, 'nd-cpu');
    var net = input({ value: nd.net_context, class: 'st-input is-mono' }, 'nd-net');
    var dim = input({ value: nd.net_out_dimension, class: 'st-input is-mono' }, 'nd-dim');
    var cloudOnly = el('div', { class: 'st-form' },
      field('Space ID', space), field('Room ID', room),
      el('div', { class: 'st-wide' }, field('API token', token, 'In Netdata Cloud: your profile, then API Tokens. Scope: all.')));
    cloudOnly.hidden = mode.value !== 'cloud';
    mode.addEventListener('change', function () { cloudOnly.hidden = mode.value !== 'cloud'; });
    var save = function () {
      var b = { mode: mode.value, space_id: space.value.trim(), room_id: room.value.trim(), cpu_context: cpu.value.trim(), net_context: net.value.trim(), net_out_dimension: dim.value.trim() };
      if (token.value) b.token = token.value.trim();
      return withBusy('nd-save', api('PUT', '/api/settings/netdata', b).then(function () {
        msgs.netdata = { kind: 'ok', text: 'Saved.' };
        delete drafts['nd-token'];
        return afterChange();
      }).catch(function (e) { setMsg('netdata', 'error', e.message); }));
    };
    var test = function () {
      return withBusy('nd-test', api('POST', '/api/settings/netdata/test').then(function (r) {
        var parts = Object.keys(r.values || {}).map(function (k) {
          var v = r.values[k];
          return k + ': ' + (v.cpu_pct != null ? v.cpu_pct + '% CPU' : 'no CPU data') + (v.net_out_mbps != null ? ', ' + v.net_out_mbps + ' Mbps out' : '');
        });
        var head = 'Connected' + (r.nodes ? ' to ' + r.nodes + ' nodes' + (r.node_names && r.node_names.length ? ' (' + r.node_names.join(', ') + ')' : '') : '') + '. ';
        ndContexts = r.contexts || {};
        if (!r.ok) setMsg('netdata', 'error', head + 'Reading metrics failed: ' + r.error);
        else {
          var none = Object.keys(r.values || {}).every(function (k) { return r.values[k].cpu_pct == null && r.values[k].net_out_mbps == null; });
          setMsg('netdata', 'ok', head + (parts.join(' · ') || '') + (none ? ' Next: in Plex servers, Edit each server and pick its Netdata node.' : ''));
        }
      }).catch(function (e) { setMsg('netdata', 'error', e.message); }));
    };
    pane.appendChild(el('section', { class: 'st-section' },
      el('h3', { text: 'Connection' }),
      el('p', { class: 'st-note', text: 'Where the numbers come from. Netdata Cloud is simplest when your agents already stream to it.' }),
      field('Source', mode), cloudOnly,
      el('details', { class: 'st-details' }, el('summary', { text: 'Advanced: metric names' }),
        el('div', { class: 'st-form' }, field('CPU context', cpu), field('Network context', net),
          el('div', { class: 'st-wide' }, field('Outbound dimension', dim, 'Windows nodes may use different names. Check one node’s charts in Netdata.')))),
      el('div', { class: 'st-row' },
        el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy['nd-save'] ? 'Saving…' : 'Save connection', disabled: busy['nd-save'], onclick: save }),
        el('button', { type: 'button', class: 'st-btn', text: busy['nd-test'] ? 'Testing…' : 'Test connection', disabled: busy['nd-test'] || nd.mode === 'off', onclick: test })),
      nd.error ? el('p', { class: 'st-msg st-msg--error' }, el('b', { text: 'Error: ' }), 'Last poll failed: ' + nd.error) : null,
      message('netdata'),
      contextHints(cpu, net)));
  }

  // After Test connection: the CPU and network metric names this Netdata room really has.
  var ndContexts = null;
  function contextHints(cpuInput, netInput) {
    if (!ndContexts || (!(ndContexts.cpu || []).length && !(ndContexts.net || []).length)) return document.createDocumentFragment();
    var pick = function (list, target, label) {
      if (!list || !list.length) return null;
      return el('div', { class: 'st-field' }, el('span', { class: 'st-note', text: label }),
        el('div', { class: 'st-row' }, list.map(function (c) {
          return el('button', { type: 'button', class: 'st-btn st-btn--quiet is-mono', text: c, title: 'Use ' + c, onclick: function () {
            target.value = c; drafts[target.id] = c;
            var adv = document.querySelector('#st-pane-netdata details'); if (adv) adv.open = true;
          } });
        })));
    };
    return el('div', { class: 'st-hintbox' }, el('h3', { text: 'Metric names found in your Netdata room' }),
      el('p', { class: 'st-note', text: 'Click one to use it under Advanced, then Save and Test again. Windows servers often name CPU and network metrics differently.' }),
      pick(ndContexts.cpu, cpuInput, 'CPU'), pick(ndContexts.net, netInput, 'Network'));
  }

  // ---------- render: Alerts ----------
  function renderAlerts(pane) {
    var val = el('output', { text: Math.round(state.hot_cpu_pct) + '%' });
    var range = el('input', { class: 'sd-range', type: 'range', min: 50, max: 100, step: 5, value: state.hot_cpu_pct, 'aria-label': 'Hot CPU threshold', id: 'al-hot' });
    var fill = function () { range.style.setProperty('--fill', ((Number(range.value) - 50) / 50) * 100 + '%'); };
    fill();
    range.addEventListener('input', function () { val.textContent = range.value + '%'; fill(); });
    // Saves when you let go of the slider.
    range.addEventListener('change', function () {
      var v = Number(range.value);
      api('PUT', '/api/settings/general', { hot_cpu_pct: v }).then(function () {
        delete drafts['al-hot'];
        msgs.alerts = { kind: 'ok', text: 'Saved. A server above ' + v + '% CPU now counts as hot.' };
        return afterChange();
      }).catch(function (e) { setMsg('alerts', 'error', e.message); });
    });
    pane.appendChild(el('section', { class: 'st-section' },
      el('div', { class: 'sd-field__row' }, el('label', { for: 'al-hot', text: 'Hot CPU threshold' }), val),
      range,
      el('div', { class: 'sd-field__ticks' }, el('span', { text: '50%' }), el('span', { text: '100%' })),
      el('p', { class: 'st-note', text: 'A server whose CPU stays above this is marked Degraded, turns amber on its capacity ring and gets an alert. Lower it to hear about busy servers sooner. Saves when you let go.' }),
      message('alerts')));
  }

  // ---------- Access: who can sign in and what they see ----------
  function loadAccess() {
    if (!isAdmin()) return Promise.resolve();
    return Promise.all([api('GET', '/api/settings/access'), api('GET', '/api/settings'), loadAccUsers(true)]).then(function (r) {
      access = r[0];
      state = r[1];
    }).then(render).catch(function (e) { setMsg('load', 'error', e.message); });
  }

  // Everyone, once: the picker searches and groups them in the browser (fine for thousands).
  function loadAccUsers(quiet) {
    return api('GET', '/api/users?limit=5000').then(function (r) {
      accUsers = r.rows || [];
      accServers = r.servers || [];
      if (accPicker) accPicker.setData({ servers: accServers, people: accUsers.map(function (u) {
        return { key: u.id, name: u.name || u.username || 'Plex user', username: u.username, email: u.email, servers: u.servers || [],
          plays_30d: u.plays_30d, admin: u.admin, disabled: u.disabled, owner: u.owner, billing: u.billing_status };
      }) });
      if (!quiet) render();
    }).catch(function (e) { setMsg('acc-users', 'error', e.message); });
  }

  function sectionChecks(prefix, chosen) {
    var box = el('div', { class: 'st-checks', role: 'group' });
    access.sections.forEach(function (sec) {
      box.appendChild(el('label', { title: sec.hint },
        el('input', { type: 'checkbox', id: prefix + '-' + sec.key, value: sec.key, checked: chosen.indexOf(sec.key) >= 0 }), sec.label));
    });
    return box;
  }

  function checked(prefix) {
    return access.sections.map(function (s) { return s.key; }).filter(function (k) {
      var c = document.getElementById(prefix + '-' + k);
      return c && c.checked;
    });
  }

  var when = window.gsUI.when;   // a moment: "Nov 10, 2:52 AM" (it was called ago, but it gives a date)


  function accessEditor(u, describe) {
    var key = 'acc-u';
    var label = function (k) { var x = access.sections.find(function (s) { return s.key === k; }); return x ? x.label : k; };
    var head = el('div', { class: 'st-editor__head' },
      el('div', null, el('b', { text: u.name || u.username }),
        el('span', { text: [u.username && u.username !== u.name ? '@' + u.username : null, u.email || 'no email on file',
          u.servers && u.servers.length ? 'on ' + u.servers.join(', ') : 'not on a monitored server'].filter(Boolean).join(' · ') }),
        el('span', { text: describe(u) + ' · ' + (u.last_login ? 'last signed in ' + when(u.last_login) : 'never signed in') })));
    var close = el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: u.admin ? 'Close' : 'Cancel', onclick: function () { accEditing = null; render(); } });
    if (u.admin) {
      return el('div', { class: 'st-editor' }, head, el('p', { class: 'st-note', text: 'The administrator Plex account always sees everything and cannot be turned off.' }),
        el('div', { class: 'st-row' }, el('span', { class: 'st-spacer' }), close));
    }
    var useDef = el('input', { type: 'radio', name: key + '-mode', value: 'default', id: key + '-def', checked: !u.custom_sections });
    var useCustom = el('input', { type: 'radio', name: key + '-mode', value: 'custom', id: key + '-cus', checked: !!u.custom_sections });
    var off = el('input', { type: 'checkbox', id: key + '-off', checked: u.disabled });
    var box = el('div', { id: key + '-sbox' }, sectionChecks(key + '-s', u.custom_sections || u.sections));
    setTimeout(function () {
      var sync = function () { box.querySelectorAll('input').forEach(function (c) { c.disabled = useDef.checked; }); };
      useDef.onchange = sync; useCustom.onchange = sync; sync();
    }, 0);
    return el('div', { class: 'st-editor' }, head,
      el('div', { class: 'st-row' },
        el('label', { class: 'st-check' }, useDef, 'Default access (' + (access.default_sections.map(label).join(', ') || 'nothing') + ')'),
        el('label', { class: 'st-check' }, useCustom, 'Custom')),
      box,
      el('label', { class: 'st-check' }, off, 'Turn off this person’s access'),
      el('div', { class: 'st-row' }, el('span', { class: 'st-spacer' }), close,
        el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy[key] ? 'Saving…' : 'Save', disabled: busy[key],
          onclick: function () {
            var body = { disabled: off.checked };
            if (useDef.checked) body.use_defaults = true; else body.sections = checked(key + '-s');
            withBusy(key, api('PUT', '/api/settings/users/' + encodeURIComponent(u.id), body).then(function () {
              accEditing = null; clearDrafts(key);
              msgs['acc-users'] = { kind: 'ok', text: 'Saved access for ' + (u.name || u.username) + '.' };
              return loadAccUsers(false);
            }).catch(function (e) { setMsg('acc-users', 'error', e.message); }));
          } })));
  }

  function renderAccess(pane) {
    if (!access) { pane.appendChild(el('p', { class: 'st-note', text: 'Loading…' })); return; }
    var label = function (k) { var x = access.sections.find(function (s) { return s.key === k; }); return x ? x.label : k; };

    // Who can sign in
    pane.appendChild(el('section', { class: 'st-section' },
      el('h3', { text: 'Who can sign in' }),
      el('p', { class: 'st-note', text: 'Anyone you share a Plex server with can sign in with their Plex account and sees only the sections you allow. People who stream from your servers are added automatically.' }),
      el('p', { class: 'st-note' }, 'The only Plex account with administrator access is ',
        access.admin_plex_email ? el('b', { text: access.admin_plex_email }) : el('b', { text: 'not set' }),
        access.admin_plex_email ? '. Change it with ADMIN_PLEX_EMAIL on the server.' : '. Set ADMIN_PLEX_EMAIL on the server.')));

    // Defaults
    pane.appendChild(el('section', { class: 'st-section' },
      el('h3', { text: 'Default access for Plex users' }),
      el('p', { class: 'st-note', text: 'What everyone sees unless you give them custom access below. Saves as you tick.' }),
      (function () {
        var box = sectionChecks('acc-def', access.default_sections);
        // Saves as soon as a box is ticked or cleared.
        box.addEventListener('change', function () {
          var picked = checked('acc-def');
          api('PUT', '/api/settings/access/defaults', { sections: picked }).then(function () {
            clearDrafts('acc-def');
            msgs['acc-def'] = { kind: 'ok', text: 'Saved. People on the default now see ' + (picked.length ? picked.map(function (k) { var x = access.sections.find(function (z) { return z.key === k; }); return x ? x.label : k; }).join(', ') : 'nothing') + '.' };
            return loadAccess();
          }).catch(function (e) { setMsg('acc-def', 'error', e.message); });
        });
        return box;
      })(),
      message('acc-def')));

    // People: find one person at a time, plus a short list of everyone not on the defaults.
    var sync = state && state.users_sync || {};
    if (!accPicker) {
      accPicker = window.gsUserPicker({ idPrefix: 'acc', inline: true, placeholder: 'Find someone to change their access',
        icon: window.sdIcon, onPick: function (p) {
          accEditing = p.key; render();
          var first = document.querySelector('#st-pane-access .st-editor input, #st-pane-access .st-editor button');
          if (first) first.focus();
        } });
      loadAccUsers(true);
    }
    var editing = accEditing ? (accUsers || []).find(function (u) { return u.id === accEditing; }) : null;
    var changed = (accUsers || []).filter(function (u) { return !u.admin && (u.custom_sections || u.disabled); });
    var describe = function (u) {
      return u.admin ? 'Administrator, sees everything' : u.disabled ? 'Access turned off'
        : (u.custom_sections ? 'Custom: ' : 'Default: ') + (u.sections.length ? u.sections.map(label).join(', ') : 'nothing');
    };
    pane.appendChild(el('section', { class: 'st-section' },
      el('h3', { text: 'People' }),
      el('p', { class: 'st-note', text: 'Find anyone by username or email to change what they can see. Results group by Plex server.' }),
      accPicker.el,
      editing ? accessEditor(editing, describe) : null,
      message('acc-users'),
      el('p', { class: 'st-note', text: sync.synced_at ? 'Shared users last loaded from Plex ' + when(sync.synced_at) + '.' : 'Load the people you share servers with from your Plex account.' }),
      sync.error ? el('p', { class: 'st-msg st-msg--error' }, el('b', { text: 'Error: ' }), sync.error) : null,
      el('div', { class: 'st-row' },
        el('button', { type: 'button', class: 'st-btn', text: busy['acc-sync'] ? 'Loading users…' : 'Sync Plex users', disabled: busy['acc-sync'],
          onclick: function () {
            withBusy('acc-sync', api('POST', '/api/settings/users/sync').then(function (r) {
              msgs['acc-users'] = { kind: 'ok', text: 'Loaded ' + r.users + ' people from your linked Plex accounts.' };
              return loadAccess();
            }).catch(function (e) { setMsg('acc-users', 'error', e.message); }));
          } }),
        el('button', { type: 'button', class: 'st-btn', text: busy['acc-import'] ? 'Importing…' : 'Import Plex watch history', disabled: busy['acc-import'],
          title: 'Copies each server’s own watch history into the dashboard. Safe to run again.',
          onclick: function () {
            withBusy('acc-import', api('POST', '/api/settings/history/import').then(function (r) {
              var t = 'Imported ' + r.added + ' plays' + (r.already_had ? ', ' + r.already_had + ' were already here' : '') + '.';
              if (r.errors && r.errors.length) t += ' Problems: ' + r.errors.join('; ');
              msgs['acc-users'] = { kind: r.errors && r.errors.length ? 'error' : 'ok', text: t };
              return loadAccUsers(false);
            }).catch(function (e) { setMsg('acc-users', 'error', e.message); }));
          } }))));
    var cl = el('ul', { class: 'st-list' });
    changed.forEach(function (u) {
      cl.appendChild(el('li', { class: 'st-item' },
        el('div', { class: 'st-item__top' },
          el('span', { class: 'st-dot ' + (u.disabled ? 'st-dot--offline' : ''), 'aria-hidden': 'true' }),
          el('span', { class: 'st-item__name', text: u.name || u.username }),
          el('span', { class: 'st-spacer' }),
          el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Change', onclick: function () { accEditing = u.id; render(); } })),
        el('div', { class: 'st-item__meta', text: describe(u) })));
    });
    pane.appendChild(el('details', { class: 'st-section st-details', id: 'acc-changed' },
      el('summary', { text: 'Custom access or turned off (' + changed.length + ')' }),
      changed.length ? cl : el('p', { class: 'st-note', text: 'Everyone uses the default access.' })));

    // Local admin logins
    var aUser = input({ placeholder: 'e.g. ops', autocomplete: 'off' }, 'acc-new-user');
    var aPw = input({ type: 'password', autocomplete: 'new-password' }, 'acc-new-pw');
    var aPw2 = input({ type: 'password', autocomplete: 'new-password' }, 'acc-new-pw2');
    var admins = el('ul', { class: 'st-list' });
    access.admins.forEach(function (a) {
      var mine = me && me.kind === 'local' && String(me.id) === String(a.id);
      admins.appendChild(el('li', { class: 'st-item' },
        el('div', { class: 'st-item__top' },
          el('span', { class: 'st-item__name', text: a.username }),
          mine ? el('span', { class: 'st-tag', text: 'You' }) : null,
          el('span', { class: 'st-spacer' }),
          mine ? null : accRemoving === a.id
            ? el('span', { class: 'st-row' }, el('span', { class: 'st-note', text: 'Delete ' + a.username + '?' }),
                el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Keep', onclick: function () { accRemoving = null; render(); } }),
                el('button', { type: 'button', class: 'st-btn st-btn--danger', text: busy['acc-del-' + a.id] ? 'Deleting…' : 'Delete', onclick: function () {
                  withBusy('acc-del-' + a.id, api('DELETE', '/api/settings/admins/' + a.id).then(function () {
                    accRemoving = null; return loadAccess();
                  }).catch(function (e) { setMsg('acc-admins', 'error', e.message); }));
                } }))
            : el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Delete…', onclick: function () { accRemoving = a.id; render(); } })),
        el('div', { class: 'st-item__meta', text: 'Created ' + when(a.created) + ' · ' + (a.last_login ? 'last signed in ' + when(a.last_login) : 'never signed in') })));
    });
    var adminSec = el('details', { class: 'st-section st-details', id: 'acc-admins-box', open: !!msgs['acc-admins'] || !!drafts['acc-new-user'] },
      el('summary', { text: 'Admin logins, not Plex accounts (' + access.admins.length + ')' }),
      el('p', { class: 'st-note', text: 'Username and password logins with full administrator access. Use them for people who should manage the dashboard without a Plex account.' }),
      access.admins.length ? admins : el('p', { class: 'st-note', text: 'None yet.' }),
      el('details', { class: 'st-details', id: 'acc-add', open: !!msgs['acc-admins'] || !!drafts['acc-new-user'] },
        el('summary', { text: 'Add an admin login' }),
        el('div', { class: 'st-form' },
          el('div', { class: 'st-wide' }, field('Username', aUser, 'Letters, numbers, dots, dashes, underscores or @.')),
          field('Password', aPw, 'At least 12 characters.'), field('Repeat password', aPw2),
          el('div', { class: 'st-wide st-row' },
            el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy['acc-add'] ? 'Adding…' : 'Add admin login', disabled: busy['acc-add'],
              onclick: function () {
                if (aPw.value !== aPw2.value) { setMsg('acc-admins', 'error', 'The passwords do not match.'); return; }
                withBusy('acc-add', api('POST', '/api/settings/admins', { username: aUser.value.trim(), password: aPw.value }).then(function () {
                  clearDrafts('acc-new');
                  msgs['acc-admins'] = { kind: 'ok', text: 'Added ' + aUser.value.trim() + '. They can sign in under Administrator sign-in.' };
                  return loadAccess();
                }).catch(function (e) { setMsg('acc-admins', 'error', e.message); }));
              } })))),
      message('acc-admins'));
    pane.appendChild(adminSec);

    if (me && me.kind === 'local') {
      var cur = input({ type: 'password', autocomplete: 'current-password' }, 'acc-pw-cur');
      var nw = input({ type: 'password', autocomplete: 'new-password' }, 'acc-pw-new');
      var nw2 = input({ type: 'password', autocomplete: 'new-password' }, 'acc-pw-new2');
      pane.appendChild(el('section', { class: 'st-section' },
        el('h3', { text: 'Change my password' }),
        el('div', { class: 'st-form' },
          el('div', { class: 'st-wide' }, field('Current password', cur)),
          field('New password', nw, 'At least 12 characters.'), field('Repeat new password', nw2),
          el('div', { class: 'st-wide st-row' },
            el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy['acc-pw'] ? 'Saving…' : 'Change password', disabled: busy['acc-pw'],
              onclick: function () {
                if (nw.value !== nw2.value) { setMsg('acc-pw', 'error', 'The new passwords do not match.'); return; }
                withBusy('acc-pw', api('PUT', '/api/settings/admins/me/password', { current: cur.value, new: nw.value }).then(function () {
                  clearDrafts('acc-pw');
                  msgs['acc-pw'] = { kind: 'ok', text: 'Password changed. Other sessions for this login were signed out.' };
                }).catch(function (e) { setMsg('acc-pw', 'error', e.message); }));
              } }))),
        message('acc-pw')));
    }
  }

  // ---------- Billing: Stripe subscriptions (PayPal later) ----------
  var billing = null;
  var unmatched = null;
  var unmatchedQ = '';
  var unmatchedTimer = null;
  var linking = null; // { email } while picking the Plex user for a Stripe email
  var linkPicker = null;
  var unlinkingEmail = null;
  var editingKey = false;

  function loadBilling() {
    if (!isAdmin()) return Promise.resolve();
    return Promise.all([api('GET', '/api/settings/billing'), api('GET', '/api/settings'), loadUnmatched(true), accUsers ? null : loadAccUsers(true)]).then(function (r) {
      billing = r[0];
      state = r[1];
    }).then(render).catch(function (e) { setMsg('load', 'error', e.message); });
  }

  function loadUnmatched(quiet) {
    return api('GET', '/api/settings/billing/unmatched?q=' + encodeURIComponent(unmatchedQ.trim())).then(function (r) {
      unmatched = r.rows || [];
      if (!quiet) render();
    }).catch(function (e) { setMsg('billing', 'error', e.message); });
  }

  function peopleForPicker() {
    return (accUsers || []).map(function (u) {
      return { key: u.id, name: u.name || u.username || 'Plex user', username: u.username, email: u.email, servers: u.servers || [],
        plays_30d: u.plays_30d, admin: u.admin, disabled: u.disabled, owner: u.owner, billing: u.billing_status };
    });
  }

  function renderBilling(pane) {
    if (!billing) { pane.appendChild(el('p', { class: 'st-note', text: 'Loading…' })); return; }
    var c = billing.counts || {};
    // Stripe connection
    var key = input({ type: 'password', placeholder: 'rk_live_…', autocomplete: 'new-password', class: 'st-input is-mono' }, 'bl-key');
    var keyForm = el('div', { class: 'st-form' },
      el('div', { class: 'st-wide' }, field('Restricted API key', key, 'In Stripe: Developers, API keys, Create restricted key. Give it Read access to Customers, Subscriptions, Products and Charges, and nothing else.')),
      el('div', { class: 'st-wide st-row' },
        el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy['bl-save'] ? 'Checking…' : 'Save key', disabled: busy['bl-save'],
          onclick: function () {
            withBusy('bl-save', api('PUT', '/api/settings/billing', { key: key.value.trim() }).then(function (r) {
              clearDrafts('bl'); editingKey = false;
              msgs.billing = r.missing && r.missing.length
                ? { kind: 'error', text: 'Saved, but the key cannot read ' + r.missing.join(', ') + '. Add Read access for those in Stripe.' }
                : { kind: 'ok', text: 'Saved. Loading customers and subscriptions from Stripe now.' };
              setTimeout(loadBilling, 4000);
              return loadBilling();
            }).catch(function (e) { setMsg('billing', 'error', e.message); }));
          } }),
        billing.has_key ? el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Cancel', onclick: function () { editingKey = false; render(); } }) : null));
    var stripeSec = el('section', { class: 'st-section' }, el('h3', { text: 'Stripe' }));
    if (billing.has_key && !editingKey) {
      stripeSec.append(
        el('p', { class: 'st-note', text: billing.synced_at ? 'Connected. Last loaded from Stripe ' + when(billing.synced_at) + '; it refreshes every 15 minutes.' : 'Connected. Loading from Stripe…' }),
        el('p', { class: 'st-note', text: (c.customers || 0) + ' Stripe customers · ' + (c.matched || 0) + ' matched to a Plex user · ' + (c.unmatched || 0) + ' not matched · ' + (c.links || 0) + ' linked by hand' }),
        billing.error ? el('p', { class: 'st-msg st-msg--error' }, el('b', { text: 'Error: ' }), 'Last sync failed: ' + billing.error) : document.createDocumentFragment(),
        el('div', { class: 'st-row' },
          el('button', { type: 'button', class: 'st-btn', text: busy['bl-sync'] ? 'Syncing…' : 'Sync now', disabled: busy['bl-sync'], onclick: function () {
            withBusy('bl-sync', api('POST', '/api/settings/billing/sync').then(function (r) {
              msgs.billing = { kind: 'ok', text: 'Loaded ' + r.customers + ' customers, ' + r.subscriptions + ' subscriptions and ' + r.charges + ' recent payments.' };
              return Promise.all([loadBilling(), loadAccUsers(true)]);
            }).catch(function (e) { setMsg('billing', 'error', e.message); }));
          } }),
          el('span', { class: 'st-spacer' }),
          el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Replace key', onclick: function () { editingKey = true; render(); } }),
          el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Disconnect', onclick: function () {
            api('DELETE', '/api/settings/billing').then(function () { msgs.billing = { kind: 'ok', text: 'Disconnected. Saved data stays until you connect again.' }; return loadBilling(); })
              .catch(function (e) { setMsg('billing', 'error', e.message); });
          } })));
    } else {
      stripeSec.append(el('p', { class: 'st-note', text: 'Connect Stripe to show each person their subscription. Customers match Plex users by email; you can link the rest by hand below.' }), keyForm);
    }
    stripeSec.appendChild(message('billing'));
    pane.appendChild(stripeSec);

    // Linking a Stripe email to a Plex user
    if (linking) {
      if (!linkPicker) {
        linkPicker = window.gsUserPicker({ idPrefix: 'blp', inline: true, placeholder: 'Find the Plex user this payer is', icon: window.sdIcon,
          onPick: function (p) {
            var email = linking.email;
            withBusy('bl-link', api('POST', '/api/settings/billing/links', { email: email, plex_id: p.key }).then(function () {
              linking = null;
              msgs['bl-links'] = { kind: 'ok', text: email + ' is now linked to ' + p.name + '. Their subscription card updates right away.' };
              return Promise.all([loadBilling(), loadAccUsers(true)]);
            }).catch(function (e) { setMsg('bl-links', 'error', e.message); }));
          } });
      }
      linkPicker.setData({ servers: accServers, people: peopleForPicker() });
    }
    var linkBox = linking ? el('div', { class: 'st-editor' },
      el('div', { class: 'st-editor__head' }, el('div', null, el('b', { text: 'Link ' + linking.email }),
        el('span', { text: 'Pick the Plex account this Stripe customer watches with. The link is permanent until you remove it.' }))),
      linkPicker.el,
      el('div', { class: 'st-row' }, el('span', { class: 'st-spacer' }),
        el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Cancel', onclick: function () { linking = null; render(); } }))) : null;

    var q = input({ type: 'search', placeholder: 'Search Stripe email or name', value: unmatchedQ }, 'bl-q');
    q.addEventListener('input', function () {
      unmatchedQ = q.value;
      clearTimeout(unmatchedTimer);
      unmatchedTimer = setTimeout(function () { loadUnmatched(false); }, 220);
    });
    var list = el('ul', { class: 'st-list' });
    (unmatched || []).slice(0, 50).forEach(function (u) {
      list.appendChild(el('li', { class: 'st-item' },
        el('div', { class: 'st-item__top' },
          el('span', { class: 'st-item__name', text: u.email }),
          u.status ? el('span', { class: 'st-tag', text: statusLabel(u.status) }) : null,
          el('span', { class: 'st-spacer' }),
          el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Link to a Plex user', onclick: function () { linking = { email: u.email }; render(); } })),
        el('div', { class: 'st-item__meta', text: [u.name, u.plan, 'customer since ' + when(u.created)].filter(Boolean).join(' · ') })));
    });
    var manualEmail = input({ type: 'email', placeholder: 'payer@example.com' }, 'bl-manual');
    pane.appendChild(el('section', { class: 'st-section' },
      el('h3', { text: 'Not matched to a Plex user (' + (c.unmatched || 0) + ')' }),
      c.unmatched_ended ? el('p', { class: 'st-note', text: c.unmatched_ended + (c.unmatched_ended === 1 ? ' canceled or expired customer is' : ' canceled or expired customers are') + ' not shown.' }) : null,
      el('p', { class: 'st-note', text: 'These Stripe customers pay with an email no Plex account uses. Link each one to the right Plex user.' }),
      linkBox,
      field('Search', q),
      unmatched && !unmatched.length ? el('p', { class: 'st-note', text: unmatchedQ.trim() ? 'No unmatched customer matches that.' : 'Every Stripe customer is matched. Nice.' }) : list,
      unmatched && unmatched.length > 50 ? el('p', { class: 'st-note', text: 'Showing 50. Search to find someone else.' }) : null,
      el('details', { class: 'st-details' }, el('summary', { text: 'Link any Stripe email by hand' }),
        el('div', { class: 'st-row' }, el('div', { style: 'flex: 1 1 240px' }, field('Stripe email', manualEmail)),
          el('button', { type: 'button', class: 'st-btn', text: 'Choose Plex user', onclick: function () {
            var v = manualEmail.value.trim();
            if (!/^[^@\s]+@[^@\s]+$/.test(v)) { setMsg('bl-links', 'error', 'Enter the email address the person pays Stripe with.'); return; }
            linking = { email: v.toLowerCase() }; clearDrafts('bl-manual'); render();
          } }))),
      message('bl-links')));

    // Manual links
    var links = el('ul', { class: 'st-list' });
    (billing.links || []).forEach(function (l) {
      links.appendChild(el('li', { class: 'st-item' },
        el('div', { class: 'st-item__top' },
          el('span', { class: 'st-item__name', text: l.email }),
          el('span', { class: 'st-note', text: 'linked to ' + (l.title || l.username || l.plex_id) }),
          el('span', { class: 'st-spacer' }),
          unlinkingEmail === l.email
            ? el('span', { class: 'st-row' },
                el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Keep', onclick: function () { unlinkingEmail = null; render(); } }),
                el('button', { type: 'button', class: 'st-btn st-btn--danger', text: 'Remove link', onclick: function () {
                  api('DELETE', '/api/settings/billing/links/' + encodeURIComponent(l.email)).then(function () {
                    unlinkingEmail = null; return Promise.all([loadBilling(), loadUnmatched(true), loadAccUsers(true)]);
                  }).catch(function (e) { setMsg('bl-links', 'error', e.message); });
                } }))
            : el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Remove…', onclick: function () { unlinkingEmail = l.email; render(); } })),
        el('div', { class: 'st-item__meta', text: (l.plex_email ? 'Plex email ' + l.plex_email + ' · ' : '') + 'linked ' + when(l.created) })));
    });
    pane.appendChild(el('details', { class: 'st-section st-details', open: !!unlinkingEmail },
      el('summary', { text: 'Linked by hand (' + (billing.links || []).length + ')' }),
      (billing.links || []).length ? links : el('p', { class: 'st-note', text: 'No manual links yet.' })));

    pane.appendChild(el('section', { class: 'st-section' }, el('h3', { text: 'PayPal' }),
      el('p', { class: 'st-note', text: 'Not connected yet. PayPal subscriptions will show on the same card once it is set up.' })));
  }

  // ---------- Books & comics: the Greystar media site ----------
  var bks = null;
  function loadBooks() {
    if (!isAdmin()) return Promise.resolve();
    return Promise.all([api('GET', '/api/settings/books'), state ? null : api('GET', '/api/settings')]).then(function (r) {
      bks = r[0];
      if (r[1]) state = r[1];
    }).then(render).catch(function (e) { setMsg('load', 'error', e.message); });
  }
  function booksSaved(b, text) {
    bks = b; clearDrafts('bk');
    msgs.bk = { kind: 'ok', text: text };
    if (window.dash && window.dash.refresh) window.dash.refresh();
    render();
  }
  function renderBooks(pane) {
    if (!bks) { pane.appendChild(el('p', { class: 'st-note', text: 'Loading…' })); return; }
    var url = input({ value: bks.url || '', placeholder: 'https://media.greystarnetworks.com', class: 'st-input is-mono' }, 'bk-url');
    var secret = input({ type: 'password', placeholder: bks.has_secret ? 'Saved. Leave blank to keep it.' : 'Paste the secret from the media site', autocomplete: 'new-password' }, 'bk-secret');
    var on = el('input', { type: 'checkbox', id: 'bk-on', checked: bks.enabled, disabled: !bks.has_secret || busy['bk-on'], onchange: function (e) {
      var want = e.target.checked;
      withBusy('bk-on', api('PUT', '/api/settings/books', { enabled: want }).then(function (b) {
        booksSaved(b, want ? 'Books & comics is on. People with access see it in the menu after a reload.' : 'Books & comics is off.');
      }).catch(function (err) { setMsg('bk', 'error', err.message); }));
    } });
    var sec = el('section', { class: 'st-section' }, el('h3', { text: 'Media site' }),
      el('p', { class: 'st-note', text: 'Make the shared secret on the media site: Administration, Settings, Telemetry control panel, Make secret. Paste it here; it is saved encrypted and never shown again. Turn the link on there too.' }),
      el('div', { class: 'st-form' },
        el('div', { class: 'st-wide' }, field('Media site address', url, 'For example https://media.greystarnetworks.com')),
        el('div', { class: 'st-wide' }, field('Shared secret', secret)),
        el('div', { class: 'st-wide st-row' },
          el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy['bk-save'] ? 'Saving…' : 'Save', disabled: busy['bk-save'], onclick: function () {
            var b = { url: url.value.trim() };
            if (secret.value.trim()) b.secret = secret.value.trim();
            withBusy('bk-save', api('PUT', '/api/settings/books', b).then(function (r) { booksSaved(r, 'Saved.'); })
              .catch(function (err) { setMsg('bk', 'error', err.message); }));
          } }),
          bks.has_secret ? el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Forget secret', onclick: function () {
            api('DELETE', '/api/settings/books').then(function (r) { booksSaved(r, 'Secret forgotten. Books & comics is off.'); })
              .catch(function (err) { setMsg('bk', 'error', err.message); });
          } }) : null)),
      el('label', { class: 'st-check', for: 'bk-on' }, on, ' Show Books & comics to people with access'),
      bks.enabled ? el('div', { class: 'st-row' }, el('a', { class: 'st-btn', href: '#/books', text: 'Open Books & comics', onclick: function () { if (window.sdOpenSettings) window.sdOpenSettings(false); } })) : null);
    sec.appendChild(message('bk'));
    pane.appendChild(sec);
    pane.appendChild(el('section', { class: 'st-section' }, el('h3', { text: 'Who sees it' }),
      el('p', { class: 'st-note', text: 'Books & comics is an access section, on for everyone by default. Turn it off for someone in Access, like any other section. It needs a Plex sign-in: the media site finds the person by their Plex account (or makes one for them, approved straight away).' }),
      el('p', { class: 'st-note', text: 'Everyone can request comics, ebooks and audiobooks. Listening to audiobooks needs the Audiobooks add-on, which is turned on per person on the media site.' })));
  }

  // ---------- Radarr & Sonarr (download progress, Unwatched cleanup) ----------
  var arrs = null;
  var ARR_NAMES = { radarr: 'Radarr', sonarr: 'Sonarr', radarr4k: 'Radarr 4K', sonarr4k: 'Sonarr 4K' };
  function loadArr() {
    if (!isAdmin()) return Promise.resolve();
    return Promise.all([api('GET', '/api/settings/arr'), state ? null : api('GET', '/api/settings')]).then(function (r) {
      arrs = r[0];
      if (r[1]) state = r[1];
    }).then(render).catch(function (e) { setMsg('load', 'error', e.message); });
  }
  function arrSlot(slot) {
    var a = arrs[slot] || {};
    var on = a.url && a.has_key;
    var url = input({ value: a.url || '', placeholder: slot.indexOf('radarr') === 0 ? 'http://10.0.0.5:7878' : 'http://10.0.0.5:8989', class: 'st-input is-mono' }, 'arr-url-' + slot);
    var key = input({ type: 'password', placeholder: a.has_key ? 'Saved. Leave blank to keep it.' : 'API key (Settings, General)', autocomplete: 'new-password' }, 'arr-key-' + slot);
    var sec = el('section', { class: 'st-section' }, el('h3', null, ARR_NAMES[slot], ' ', on ? badgeEl(a.error ? 'danger' : 'success', a.error ? 'Last check failed' : 'Connected') : badgeEl('neutral', 'Not set up')));
    if (a.error) sec.appendChild(el('p', { class: 'st-msg st-msg--error' }, el('b', { text: 'Error: ' }), a.error));
    sec.appendChild(el('div', { class: 'st-form' },
      el('div', { class: 'st-wide' }, field('Address', url, 'The address this server can reach it at, with the port')),
      el('div', { class: 'st-wide' }, field('API key', key)),
      el('div', { class: 'st-wide st-row' },
        el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy['arr-save-' + slot] ? 'Checking…' : 'Save', disabled: busy['arr-save-' + slot], onclick: function () {
          var b = { url: url.value.trim() };
          if (key.value.trim()) b.key = key.value.trim();
          withBusy('arr-save-' + slot, api('PUT', '/api/settings/arr/' + slot, b).then(function (r) {
            clearDrafts('arr');
            msgs['arr-' + slot] = { kind: 'ok', text: r.removed ? 'Removed.' : 'Connected to ' + ARR_NAMES[slot] + ' ' + (r.version || '') + '.' };
            return loadArr();
          }).catch(function (e) { setMsg('arr-' + slot, 'error', e.message); }));
        } }),
        on ? el('button', { type: 'button', class: 'st-btn', text: busy['arr-test-' + slot] ? 'Testing…' : 'Test', disabled: busy['arr-test-' + slot], onclick: function () {
          withBusy('arr-test-' + slot, api('POST', '/api/settings/arr/' + slot + '/test').then(function (r) {
            msgs['arr-' + slot] = { kind: 'ok', text: 'Working: ' + (r.name || ARR_NAMES[slot]) + ' ' + (r.version || '') + '.' };
            return loadArr();
          }).catch(function (e) { setMsg('arr-' + slot, 'error', e.message); return loadArr(); }));
        } }) : null,
        on ? el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Remove', onclick: function () {
          api('DELETE', '/api/settings/arr/' + slot).then(function () { msgs['arr-' + slot] = { kind: 'ok', text: 'Removed.' }; return loadArr(); })
            .catch(function (e) { setMsg('arr-' + slot, 'error', e.message); });
        } }) : null)));
    sec.appendChild(message('arr-' + slot));
    return sec;
  }
  function renderArr(pane) {
    if (!arrs) { pane.appendChild(el('p', { class: 'st-note', text: 'Loading…' })); return; }
    var top = el('section', { class: 'st-section' }, el('h3', { text: 'Copy from Seerr' }),
      el('p', { class: 'st-note', text: 'Seerr already knows your Radarr and Sonarr. This copies their addresses and keys from Seerr, then tests each one from this server. If Seerr reaches them at an address this server cannot, change it below.' }),
      el('div', { class: 'st-row' },
        el('button', { type: 'button', class: 'st-btn st-btn--accent', text: busy['arr-seerr'] ? 'Copying…' : 'Copy from Seerr', disabled: busy['arr-seerr'], onclick: function () {
          withBusy('arr-seerr', api('POST', '/api/settings/arr/from-seerr').then(function (r) {
            msgs.arrtop = { kind: (r.found || []).every(function (x) { return x.ok; }) ? 'ok' : 'error', text: (r.found || []).map(function (x) {
              return ARR_NAMES[x.slot] + ': ' + (x.ok ? 'working' : x.error) + ' (' + x.url + ')';
            }).join('. ') + '.' };
            return loadArr();
          }).catch(function (e) { setMsg('arrtop', 'error', e.message); }));
        } }),
        el('a', { class: 'st-btn', href: '#/cleanup', text: 'Open Unwatched cleanup', onclick: function () { if (window.sdOpenSettings) window.sdOpenSettings(false); } })));
    top.appendChild(message('arrtop'));
    pane.appendChild(top);
    pane.appendChild(arrSlot('radarr'));
    pane.appendChild(arrSlot('sonarr'));
    var four = el('details', { class: 'st-section st-details' }, el('summary', { text: '4K servers (optional)' }),
      el('p', { class: 'st-note', text: 'Only if 4K requests go to their own Radarr or Sonarr. Without these, 4K titles use the ones above.' }),
      arrSlot('radarr4k'), arrSlot('sonarr4k'));
    if ((arrs.radarr4k || {}).url || (arrs.sonarr4k || {}).url) four.open = true;
    pane.appendChild(four);
  }

  // ---------- Requests: the Seerr connection ----------
  var rqs = null;
  var rqLinks = null;
  var rqEditing = false;
  function loadRequests() {
    if (!isAdmin()) return Promise.resolve();
    return Promise.all([api('GET', '/api/settings/requests'), api('GET', '/api/settings/requests/links'), state ? null : api('GET', '/api/settings')]).then(function (r) {
      rqs = r[0]; rqLinks = r[1].rows || [];
      if (r[2]) state = r[2];
    }).then(render).catch(function (e) { setMsg('load', 'error', e.message); });
  }
  function renderRequests(pane) {
    if (!rqs) { pane.appendChild(el('p', { class: 'st-note', text: 'Loading…' })); return; }
    var url = input({ value: rqs.url || '', placeholder: 'https://requests.example.com', class: 'st-input is-mono' }, 'rq-url');
    var key = input({ type: 'password', placeholder: rqs.has_key ? 'Saved. Leave blank to keep it.' : 'Seerr API key', autocomplete: 'new-password' }, 'rq-key');
    var connected = rqs.url && rqs.has_key;
    var sec = el('section', { class: 'st-section' }, el('h3', { text: 'Seerr' }));
    if (connected && !rqEditing) {
      sec.append(
        el('p', { class: 'st-note' }, 'Connected to ', el('b', { text: rqs.url }), '. ' + (rqs.links ? rqs.links + (rqs.links === 1 ? ' person has' : ' people have') + ' used Requests so far.' : 'Nobody has used Requests yet.')),
        rqs.error ? el('p', { class: 'st-msg st-msg--error' }, el('b', { text: 'Error: ' }), rqs.error) : document.createDocumentFragment(),
        el('div', { class: 'st-row' },
          el('button', { type: 'button', class: 'st-btn', text: busy['rq-test'] ? 'Testing…' : 'Test connection', disabled: busy['rq-test'], onclick: function () {
            withBusy('rq-test', api('POST', '/api/settings/requests/test').then(function (r) {
              msgs.rq = { kind: 'ok', text: 'Connected to ' + (r.title || 'Seerr') + ' ' + (r.version || '') + ' as ' + (r.admin || 'its administrator') + '. 4K: movies ' + (r.movie4k ? 'on' : 'off') + ', series ' + (r.series4k ? 'on' : 'off') + '.' };
              return loadRequests();
            }).catch(function (e) { setMsg('rq', 'error', e.message); }));
          } }),
          el('a', { class: 'st-btn', href: '#/requests', text: 'Open Requests', onclick: function () { if (window.sdOpenSettings) window.sdOpenSettings(false); } }),
          el('button', { type: 'button', class: 'st-btn', text: 'Request log', onclick: function () { go('reqlog'); } }),
          el('span', { class: 'st-spacer' }),
          el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Change', onclick: function () { rqEditing = true; render(); } }),
          el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Disconnect', onclick: function () {
            api('DELETE', '/api/settings/requests').then(function () { msgs.rq = { kind: 'ok', text: 'Disconnected. People see "Requests are not set up yet" until you connect again.' }; return loadRequests(); })
              .catch(function (e) { setMsg('rq', 'error', e.message); });
          } })));
    } else {
      sec.append(
        el('p', { class: 'st-note', text: 'Use the address people open Seerr at, and the API key from Seerr: Settings, General, API Key. The key stays on this server and is saved encrypted.' }),
        el('div', { class: 'st-form' },
          el('div', { class: 'st-wide' }, field('Seerr address', url, 'For example https://perseus.greystarnetworks.com')),
          el('div', { class: 'st-wide' }, field('API key', key)),
          el('div', { class: 'st-wide st-row' },
            el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy['rq-save'] ? 'Checking…' : 'Connect', disabled: busy['rq-save'], onclick: function () {
              var b = { url: url.value.trim() };
              if (key.value.trim()) b.key = key.value.trim();
              withBusy('rq-save', api('PUT', '/api/settings/requests', b).then(function (r) {
                rqEditing = false; clearDrafts('rq');
                msgs.rq = { kind: 'ok', text: 'Connected to ' + (r.title || 'Seerr') + ' ' + (r.version || '') + '. People can now use Requests.' };
                if (window.dash && window.dash.refresh) window.dash.refresh();
                return loadRequests();
              }).catch(function (e) { setMsg('rq', 'error', e.message); }));
            } }),
            connected ? el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Cancel', onclick: function () { rqEditing = false; render(); } }) : null)));
    }
    sec.appendChild(message('rq'));
    pane.appendChild(sec);

    pane.appendChild(el('section', { class: 'st-section' }, el('h3', { text: 'How people get a Seerr account' }),
      el('p', { class: 'st-note', text: 'The first time someone opens Requests, this site finds their Seerr account by email or Plex username. If they have none, it imports them from Plex, the same as Seerr\u2019s Import Plex users. If Seerr\u2019s own Plex account cannot see them (their server belongs to another Plex account), it makes the account with their Plex email, so signing in to Seerr with Plex later lands on the same account.' }),
      el('p', { class: 'st-note', text: 'New accounts get Seerr\u2019s default permissions. Change limits, 4K and auto-approve in Seerr, under Users. Turn Requests off for someone in Access, like any other section.' })));

    var list = el('ul', { class: 'st-list' });
    (rqLinks || []).forEach(function (l) {
      list.appendChild(el('li', { class: 'st-item' },
        el('div', { class: 'st-item__top' },
          el('span', { class: 'st-item__name', text: l.name }),
          el('span', { class: 'st-tag', text: { matched: 'Found in Seerr', imported: 'Imported from Plex', created: 'Made by email' }[l.how] || l.how }),
          el('span', { class: 'st-spacer' }),
          el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Relink', title: 'Forget this link; it is looked up again next time they open Requests.', onclick: function () {
            api('DELETE', '/api/settings/requests/links/' + encodeURIComponent(l.plex_id)).then(loadRequests).catch(function (e) { setMsg('rq', 'error', e.message); });
          } })),
        el('div', { class: 'st-item__meta', text: (l.username ? '@' + l.username + ' · ' : '') + 'Seerr user ' + l.seerr_id + ' · since ' + when(l.created) })));
    });
    pane.appendChild(el('details', { class: 'st-section st-details' },
      el('summary', { text: 'People linked to Seerr (' + (rqLinks || []).length + ')' }),
      (rqLinks || []).length ? list : el('p', { class: 'st-note', text: 'Nobody yet.' })));
  }

  function statusLabel(st) {
    return { active: 'Active', trialing: 'Trial', past_due: 'Past due', unpaid: 'Unpaid', canceled: 'Canceled', incomplete: 'Incomplete',
      incomplete_expired: 'Expired', paused: 'Paused' }[st] || st;
  }

  // ---------- Request log: every request made through this site (admins) ----------
  var LOG_GROUPS = [['all', 'All'], ['waiting', 'Waiting for approval'], ['approved', 'Approved'], ['available', 'Available'],
    ['problem', 'Declined or failed'], ['ended', 'Canceled or removed'], ['unreleased', 'Not out yet']];
  var LOG_KINDS = [['', 'All'], ['movie', 'Movies'], ['tv', 'Series'], ['comic', 'Comics'], ['ebook', 'Ebooks'], ['audiobook', 'Audiobooks']];
  var KIND_NAME = { movie: 'Movie', tv: 'Series', comic: 'Comic', ebook: 'Ebook', audiobook: 'Audiobook' };
  var lg = { q: '', kind: '', group: 'all', page: 1, rows: [], data: null, busy: false, seq: 0, msg: null, timer: 0, ui: null };
  function logQuery() {
    return 'q=' + encodeURIComponent(lg.q) + '&kind=' + encodeURIComponent(lg.kind) + '&group=' + encodeURIComponent(lg.group);
  }
  function openLog(fresh) {
    var pane = document.getElementById('st-pane-reqlog');
    if (!pane) return;
    if (fresh || !lg.ui) buildLog(pane);
    loadLog(false);
    loadNeeds();
    loadMisses();
  }
  // Needs you: failed requests a person tried again once and then sent to the admin (app/request_help.py).
  function loadNeeds() {
    if (!isAdmin() || !lg.ui) return;
    api('GET', '/api/settings/requests/help').then(function (d) { paintNeeds(d.rows || []); }).catch(function () { paintNeeds([]); });
  }
  function paintNeeds(rows) {
    var box = lg.ui && lg.ui.needs;
    if (!box) return;
    box.hidden = !rows.length;
    if (!rows.length) { box.replaceChildren(); return; }
    box.replaceChildren(
      el('h3', { class: 'st-needs__title' }, el('span', { class: 'st-needs__mark', 'aria-hidden': 'true' }), 'Needs you (' + rows.length + ')'),
      el('p', { class: 'st-note', text: 'Each of these failed, the person tried it again once, it failed again, and they asked you to look.' }),
      el('ul', { class: 'st-needs' }, rows.map(function (r) {
        var done = el('button', { type: 'button', class: 'st-btn', text: 'Mark handled', onclick: function () {
          done.disabled = true; done.textContent = 'Saving…';
          api('POST', '/api/settings/requests/help/' + r.request + '/handled').then(function () { loadNeeds(); }).catch(function (e) { done.disabled = false; done.textContent = 'Mark handled'; lg.msg = { kind: 'error', text: e.message }; paintLog(); });
        } });
        return el('li', null,
          el('div', { class: 'st-needs__what' },
            el('a', { class: 'st-log__title', href: '#/requests/' + (r.kind === 'tv' ? 'series/' : 'movie/') + r.tmdb, text: r.title, onclick: function () { if (window.sdOpenSettings) window.sdOpenSettings(false); } }),
            el('span', { class: 'st-log__meta', text: [r.kind === 'tv' ? 'Series' : 'Movie', r.who, 'asked ' + when(r.notified_at)].join(' · ') })),
          done);
      })));
  }
  // Searches for movies and shows that found nothing: the words and how often, never who searched.
  var missDays = 7;
  function loadMisses() {
    if (!isAdmin() || !lg.ui) return;
    api('GET', '/api/settings/requests/misses?days=' + missDays).then(function (d) { paintMisses(d.rows || []); }).catch(function () { paintMisses(null); });
  }
  function paintMisses(rows) {
    var box = lg.ui && lg.ui.misses;
    if (!box) return;
    var seg = el('div', { class: 'st-seg', role: 'group', 'aria-label': 'Period' }, [7, 30].map(function (n) {
      return el('button', { type: 'button', 'aria-pressed': n === missDays ? 'true' : 'false', text: n + ' days', onclick: function () { missDays = n; loadMisses(); } });
    }));
    box.replaceChildren(
      el('div', { class: 'st-row' }, el('h3', { text: 'Searches that found nothing' }), el('span', { class: 'st-spacer' }), seg),
      el('p', { class: 'st-note', text: 'Movie and show searches with no results here. Only the words are kept, not who searched. Book searches are in the media site, Administration, Requests.' }),
      rows === null ? el('p', { class: 'st-note', text: 'Could not load.' })
        : !rows.length ? el('p', { class: 'st-note', text: 'None in the last ' + missDays + ' days.' })
        : el('ol', { class: 'st-misses' }, rows.map(function (r) {
          return el('li', null, el('span', { class: 'st-misses__term', text: r.term }), el('b', { text: r.count + (r.count === 1 ? ' time' : ' times') }), el('span', { class: 'st-log__meta', text: 'last ' + when(r.last_at) }));
        })));
  }
  function loadLog(more) {
    if (!isAdmin()) return Promise.resolve();
    var seq = ++lg.seq;
    lg.busy = true;
    paintLog();
    return api('GET', '/api/settings/requests/log?' + logQuery() + '&page=' + (more ? lg.page + 1 : 1)).then(function (d) {
      if (seq !== lg.seq) return;
      lg.data = d; lg.page = d.page; lg.rows = more ? lg.rows.concat(d.rows) : d.rows;
      if (lg.msg && lg.msg.kind === 'error') lg.msg = null;
    }).catch(function (e) { if (seq === lg.seq) lg.msg = { kind: 'error', text: e.message }; })
      .then(function () { if (seq === lg.seq) { lg.busy = false; paintLog(); } });
  }
  function buildLog(pane) {
    var search = el('input', { type: 'search', class: 'st-input st-log__search', id: 'lg-q', placeholder: 'Search by person, email, title or author',
      'aria-label': 'Search the request log', value: lg.q, oninput: function () {
        clearTimeout(lg.timer);
        lg.timer = setTimeout(function () { lg.q = search.value.trim(); loadLog(false); }, 250);
      } });
    var kinds = el('div', { class: 'st-seg st-log__kinds', role: 'group', 'aria-label': 'Type' }, LOG_KINDS.map(function (k) {
      return el('button', { type: 'button', 'data-kind': k[0], 'aria-pressed': lg.kind === k[0] ? 'true' : 'false', text: k[1], onclick: function () {
        lg.kind = k[0];
        kinds.querySelectorAll('button').forEach(function (b) { b.setAttribute('aria-pressed', b.dataset.kind === lg.kind ? 'true' : 'false'); });
        loadLog(false);
      } });
    }));
    var ui = {
      facts: el('dl', { class: 'st-facts st-log__facts' }),
      csv: el('a', { class: 'st-btn', download: '', text: 'Download CSV' }),
      imp: el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Import earlier requests', onclick: importLog }),
      chips: el('div', { class: 'st-log__chips', role: 'group', 'aria-label': 'Status' }),
      msg: el('div', { class: 'st-log__msg' }),
      list: el('ol', { class: 'st-log', 'aria-live': 'polite', 'aria-busy': 'false' }),
      more: el('button', { type: 'button', class: 'st-btn st-log__more', text: 'Show more', onclick: function () { loadLog(true); } }),
      needs: el('section', { class: 'st-section st-needs__box', hidden: true }),
      misses: el('section', { class: 'st-section st-misses__box' })
    };
    if (window.sdIcon) ui.csv.prepend(window.sdIcon('down', 16));
    lg.ui = ui;
    pane.replaceChildren(
      ui.needs,
      el('section', { class: 'st-section st-log__top' },
        el('div', { class: 'st-row' }, ui.facts, el('span', { class: 'st-spacer' }), ui.imp, ui.csv),
        el('div', { class: 'st-log__tools' }, search, kinds),
        ui.chips),
      ui.msg,
      el('section', { class: 'st-section st-log__box' }, ui.list, ui.more),
      ui.misses);
  }
  function importLog() {
    var b = lg.ui && lg.ui.imp;
    if (b) { b.disabled = true; b.textContent = 'Importing…'; }
    api('POST', '/api/settings/requests/log/import').then(function (r) {
      lg.msg = { kind: 'ok', text: r.added ? 'Added ' + r.added + (r.added === 1 ? ' earlier request.' : ' earlier requests.') + ' They are marked Imported. Seerr does not record where a request came from, so a few may have been made in Seerr itself.'
        : 'Nothing new to add. Every earlier request from people linked to Seerr is already here.' };
      return loadLog(false);
    }).catch(function (e) { lg.msg = { kind: 'error', text: e.message }; paintLog(); })
      .then(function () { if (b) { b.disabled = false; b.textContent = 'Import earlier requests'; } });
  }
  function logDay(ts) {
    var d = new Date(ts * 1000), today = new Date();
    var key = d.toDateString();
    var y = new Date(today); y.setDate(today.getDate() - 1);
    var long = d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: d.getFullYear() === today.getFullYear() ? undefined : 'numeric' });
    return { key: key, text: key === today.toDateString() ? 'Today, ' + long : key === y.toDateString() ? 'Yesterday, ' + long : long };
  }
  function seasonText(list) {
    if (!list || !list.length) return null;
    var run = list.every(function (n, i) { return i === 0 || n === list[i - 1] + 1; });
    if (list.length === 1) return 'Season ' + list[0];
    return 'Seasons ' + (run && list.length > 2 ? list[0] + ' to ' + list[list.length - 1] : list.join(', '));
  }
  // Not out yet: "Out Dec 1" (this year) or "Out Dec 1, 2027"; no date known: "Not out yet".
  function soonText(u) {
    if (!u.date) return 'Not out yet';
    var d = new Date(u.date + 'T12:00:00');
    var txt = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
    return (u.what === 'season' ? 'Airs ' : u.what === 'cinema' ? 'In cinemas ' : 'Out ') + txt;
  }
  function logRow(r, base) {
    var at = new Date(r.at * 1000);
    var book = !!KIND_NAME[r.kind] && r.kind !== 'movie' && r.kind !== 'tv';
    var poster = el('span', { class: 'st-log__poster' + (book ? ' st-log__poster--book st-log__poster--' + r.kind : ''), 'aria-hidden': 'true' },
      book ? (r.title || '?').trim().charAt(0).toUpperCase() : null);
    if (r.poster && (base || r.poster.charAt(0) === '/')) {
      var im = new Image();
      im.onload = function () { poster.style.backgroundImage = 'url("' + im.src + '")'; poster.textContent = ''; };
      im.src = r.poster.charAt(0) === '/' ? r.poster : base + r.poster;
    }
    var href = book ? (r.open ? '#/books' + r.open : null) : '#/requests/' + (r.kind === 'movie' ? 'movie/' : 'series/') + r.tmdb;
    var bits = [r.year, KIND_NAME[r.kind] || r.kind, seasonText(r.seasons), r.sub].filter(Boolean).join(' · ');
    var soon = r.unreleased ? el('span', { class: 'st-tag st-tag--soon', title: 'Not released yet. It downloads once it is out.' },
      el('span', { class: 'st-shape-ring', 'aria-hidden': 'true' }), soonText(r.unreleased)) : null;
    var sub = [r.decided && r.decided.by ? (r.state === 'declined' ? 'Declined by ' : 'Approved by ') + r.decided.by : null,
      r.hint, r.ended_at ? when(r.ended_at) : null].filter(Boolean).join(' · ') || null;
    return el('li', { class: 'st-log__row' + (r.unreleased ? ' is-unreleased' : '') },
      poster,
      el('div', { class: 'st-log__what' },
        href ? el('a', { class: 'st-log__title', href: href, text: r.title, onclick: function () { if (window.sdOpenSettings) window.sdOpenSettings(false); } })
          : el('span', { class: 'st-log__title', text: r.title }),
        el('span', { class: 'st-log__meta' }, bits, soon, r.is4k ? el('span', { class: 'st-tag', text: '4K' }) : null, r.source === 'imported' ? el('span', { class: 'st-tag', text: 'Imported', title: 'Brought in from Seerr; logged before the request log existed.' }) : null,
          r.help === 'notified' ? el('span', { class: 'st-tag st-tag--danger', text: 'Needs you', title: 'They tried it again once and then asked you to look.' })
            : r.help === 'retried' ? el('span', { class: 'st-tag', text: 'Tried again', title: 'They used their one try again.' }) : null)),
      el('div', { class: 'st-log__who' },
        el('button', { type: 'button', class: 'st-log__name', text: r.who.name, title: 'Show only ' + r.who.name + '’s requests', onclick: function () {
          var q = r.who.username || r.who.email || r.who.name;
          var box = document.getElementById('lg-q'); if (box) box.value = q;
          lg.q = q; loadLog(false);
        } }),
        el('span', { class: 'st-log__meta', text: r.who.admin_login ? 'Admin login' : [r.who.username ? '@' + r.who.username : null, r.who.email].filter(Boolean).join(' · ')
          || (r.who.kind === 'media' ? 'Media site account' : '') })),
      el('time', { class: 'st-log__when', datetime: at.toISOString(), title: at.toLocaleString(undefined, { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit' }) },
        el('b', { text: at.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) }),
        el('span', { class: 'st-log__meta st-log__date', text: at.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }) })),
      el('div', { class: 'st-log__state' }, badgeEl(r.tone, r.label), sub ? el('span', { class: 'st-log__meta', text: sub }) : null));
  }
  function paintLog() {
    var ui = lg.ui, d = lg.data;
    if (!ui) return;
    ui.list.setAttribute('aria-busy', lg.busy ? 'true' : 'false');
    ui.csv.href = '/api/settings/requests/log.csv?' + logQuery() + '&tz=' + (-new Date().getTimezoneOffset());
    var t = (d && d.totals) || {};
    ui.facts.replaceChildren.apply(ui.facts, [[t.day || 0, 'last 24 hours'], [t.week || 0, 'last 7 days'], [t.total || 0, 'all time'], [t.people || 0, t.people === 1 ? 'person' : 'people']].map(function (f) {
      return el('div', null, el('dt', { text: f[1] }), el('dd', { text: String(f[0]) }));
    }));
    var c = (d && d.counts) || {};
    ui.chips.replaceChildren.apply(ui.chips, LOG_GROUPS.map(function (g) {
      return el('button', { type: 'button', class: 'st-log__chip' + (g[0] === 'unreleased' ? ' st-log__chip--soon' : ''), 'aria-pressed': lg.group === g[0] ? 'true' : 'false', onclick: function () { lg.group = g[0]; loadLog(false); } },
        g[0] === 'unreleased' ? el('span', { class: 'st-shape-ring', 'aria-hidden': 'true' }) : null, g[1], el('b', { text: String(c[g[0]] || 0) }));
    }));
    var notes = [];
    if (lg.msg) notes.push(el('p', { class: 'st-msg st-msg--' + lg.msg.kind, role: lg.msg.kind === 'error' ? 'alert' : 'status' }, lg.msg.kind === 'error' ? el('b', { text: 'Error: ' }) : null, lg.msg.text));
    if (d && d.error) notes.push(el('p', { class: 'st-msg st-msg--error', role: 'alert' }, el('b', { text: 'Seerr: ' }), d.error + ' The log still shows who asked for what; the status shows Unknown until Seerr answers.'));
    if (d && d.media_error) notes.push(el('p', { class: 'st-msg st-msg--error', role: 'alert' }, el('b', { text: 'Media site: ' }), d.media_error + ' Comic, ebook and audiobook requests show again once it answers.'));
    ui.msg.replaceChildren.apply(ui.msg, notes);
    if (!d) { ui.list.replaceChildren(el('li', { class: 'st-note st-log__empty', text: 'Loading…' })); ui.more.hidden = true; return; }
    if (!lg.rows.length) {
      var filtered = lg.q || lg.kind || lg.group !== 'all';
      ui.list.replaceChildren(el('li', { class: 'st-log__empty' },
        el('b', { text: filtered ? 'No requests match.' : 'No requests yet.' }),
        el('span', { class: 'st-note', text: filtered ? 'Try another search, type or status.' : 'Requests made on this site show up here as they are made. Movie and series requests from before the log existed can be brought in with Import earlier requests.' })));
      ui.more.hidden = true;
      return;
    }
    var out = [], last = null;
    lg.rows.forEach(function (r) {
      var day = logDay(r.at);
      if (day.key !== last) {
        last = day.key;
        var n = lg.rows.filter(function (x) { return logDay(x.at).key === day.key; }).length;
        out.push(el('li', { class: 'st-log__day' }, el('span', { text: day.text }), el('span', { class: 'st-log__meta', text: n + (n === 1 ? ' request' : ' requests') })));
      }
      out.push(logRow(r, d.image_base));
    });
    ui.list.replaceChildren.apply(ui.list, out);
    ui.more.hidden = !d.more;
    ui.more.disabled = lg.busy;
    ui.more.textContent = lg.busy ? 'Loading…' : 'Show more (' + (d.matched - lg.rows.length) + ' left)';
  }

  // ---------- Dashboard Cards: which admin cards show (cards.js; saved per admin account) ----------
  var cardsList = null;
  var CARD_HINTS = {
    summary: 'The Transcoding, Buffering and Paused pills under the title.',
    bandwidth: 'Last hour of upload speed, and the peak for each of the last 7 days.',
    servers: 'Every server with its streams, throughput and status.',
    viewing: 'Busiest hours of the week, and the best time for maintenance.',
    globe: 'Where new streams start, by city.',
    capacity: 'CPU on each server now, and Rebalance.',
    streams: 'Everything playing now, as cards, rows or a table.',
    users: 'Find a person and open their playback history.',
    alerts: 'The Alerts list and the strip under the title about hot servers and buffering.'
  };
  function loadCards() {
    if (!window.gsCards) return;
    window.gsCards.load().then(function (j) { cardsList = j.cards; renderCards(); })
      .catch(function (e) { msgs.cards = { kind: 'error', text: e.message }; renderCards(); });
    renderCards();
  }
  var cardsPending = null;  // the list being saved, so the switches do not jump back while it saves
  function saveCards(list, okText) {
    cardsPending = list;
    renderCards();
    return window.gsCards.save(list).then(function () { msgs.cards = { kind: 'ok', text: okText(list) }; })
      .catch(function (err) { msgs.cards = { kind: 'error', text: err.message }; })
      .then(function () { cardsPending = null; renderCards(); });
  }
  function renderCards() {
    var pane = document.getElementById('st-pane-cards');
    if (!pane) return;
    if (!cardsList) { pane.replaceChildren(msgs.cards ? message('cards') : el('p', { class: 'st-note', text: 'Loading…' })); return; }
    var hidden = cardsPending || window.gsCards.hidden();
    var saving = !!cardsPending;
    var shownText = function (list) { var n = cardsList.length - list.length; return 'Saved. ' + (list.length ? n + ' of ' + cardsList.length + ' cards showing.' : 'Every card shows.'); };
    var rows = cardsList.map(function (c) {
      var id = 'cards-' + c.key;
      var box = el('input', { type: 'checkbox', class: 'st-switch', id: id, checked: hidden.indexOf(c.key) < 0, disabled: saving, onchange: function () {
        var off = cardsList.map(function (x) { return x.key; }).filter(function (k) { var b = document.getElementById('cards-' + k); return b && !b.checked; });
        saveCards(off, shownText).then(function () { var b = document.getElementById(id); if (b) b.focus(); });
      } });
      return el('label', { class: 'st-switchrow st-cardrow', for: id },
        el('div', null, el('span', { class: 'st-switchrow__label', text: c.label }), el('p', { class: 'sd-field__hint', text: CARD_HINTS[c.key] || '' })), box);
    });
    pane.replaceChildren(el('section', { class: 'st-section' },
      el('h3', { text: 'Cards on the dashboard' }),
      el('p', { class: 'st-note', text: 'Turn a card off to hide it; the rest keep their usual order. When every card on a page is off, that page leaves the menu and the phone tabs too. Saves as you change it.' }),
      el('div', { class: 'st-cardrows', role: 'group', 'aria-label': 'Cards on the dashboard' }, rows),
      el('div', { class: 'st-row' }, el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Show every card', disabled: !hidden.length || saving, onclick: function () {
        saveCards([], shownText);
      } })),
      message('cards')));
  }

  // ---------- Live streams globe (Settings, Your dashboard): saved per admin account, applied to the card at once ----------
  var GLOBE_UI = [
    { h: 'When a stream starts', items: [
      { k: 'zoom', type: 'range', label: 'Zoom level', min: 1, max: 6, step: 0.5, ticks: ['Off', '2x', '3x', '4x', '5x', '6x'],
        val: function (v) { return v <= 1 ? 'Off' : 'Up to ' + v + 'x'; },
        hint: 'How close the globe moves in on the new stream\u2019s country. Small countries go all the way; big ones like the US stop once they fill the globe. Off keeps the whole globe in view.' },
      { k: 'hold', type: 'seg', label: 'Stay zoomed for', opts: [[2, '2 s'], [3, '3 s'], [5, '5 s']], hint: 'It keeps turning the whole time, then zooms back out.' },
      { k: 'many', type: 'seg', label: 'Several at once', opts: [['busy', 'Busiest area'], ['each', 'One by one']], hint: 'Three or more new streams together: one swing to where most of them are, or each city in turn.' },
      { k: 'line', type: 'switch', label: 'Line from Dallas', hint: 'A line flies out from the servers to the city.' },
      { k: 'outline', type: 'switch', label: 'Outline the country', hint: 'A white glow around the country, fading with the ping.' },
      { k: 'label', type: 'switch', label: 'City name', hint: 'Shows \u201cToronto, CA\u201d beside the new dot.' }] },
    { h: 'Turning', items: [
      { k: 'cruise', type: 'range', label: 'Zoom while turning', min: 1, max: 4, step: 0.25, ticks: ['Whole globe', '2x', '3x', '4x'],
        val: function (v) { return v <= 1 ? 'Whole globe' : v + 'x'; },
        hint: 'Stays zoomed in this far all the time while it turns. A new stream zooms in from here and comes back to it.' },
      { k: 'speed', type: 'steps', label: 'Speed', steps: [0, 240, 180, 120, 75, 60, 45, 30, 20], ticks: ['Still', 'Slow', 'Fast'],
        val: function (v) { return v ? 'One turn every ' + (v >= 120 ? v / 60 + ' min' : v + ' s') : 'Still'; },
        hint: 'Still: it swings to each new stream, then drifts back to where it rests.' },
      { k: 'rest', type: 'seg', label: 'Rests over', opts: [['am', 'Americas'], ['eu', 'Europe'], ['as', 'Asia'], ['busy', 'Busiest now']], hint: 'Where it faces when the page opens. Busiest now: where most streams are playing.' }] },
    { h: 'Streams playing now', items: [
      { k: 'color', type: 'seg', label: 'Colour dots by', opts: [['type', 'Type'], ['server', 'Server']], hint: 'The same choice as the Type / Server switch on the card.' },
      { k: 'join_km', type: 'seg', label: 'Join nearby cities', opts: [[0, 'Off'], [50, '50 km'], [130, '130 km'], [300, '300 km']], hint: 'Places this close share one dot, like \u201cDallas, TX area\u201d.' },
      { k: 'rings', type: 'switch', label: 'Pulsing rings', hint: 'A soft ring beats around each playing dot.' }] },
    { h: 'Extras', items: [
      { k: 'night', type: 'switch', label: 'Day and night', hint: 'Dims the land on the side of the world where it is night right now.' },
      { k: 'trail', type: 'switch', label: 'Today\u2019s trail', hint: 'A faint ring wherever a stream started today, bigger where more did. Starts over at midnight.' },
      { k: 'quiet', type: 'switch', label: 'Quiet hours', hint: 'From 1 AM to 7 AM it stops swinging and zooming; new streams ping where they are.' }] }
  ];
  var TEST_CITIES = [['Sydney, AU', -33.9, 151.2], ['London, GB', 51.5, -0.1], ['Toronto, CA', 43.7, -79.4], ['Tokyo, JP', 35.7, 139.7],
    ['Sao Paulo, BR', -23.6, -46.6], ['Berlin, DE', 52.5, 13.4], ['Manila, PH', 14.6, 121.0], ['Seattle, WA', 47.6, -122.3],
    ['Mexico City, MX', 19.4, -99.1], ['Dublin, IE', 53.3, -6.3], ['Miami, FL', 25.8, -80.2], ['Auckland, NZ', -36.8, 174.8]];
  var pv = null, pvTest = 0, globeSaved = null;
  function gset() { return window.gsGlobeSettings; }
  function dropPreview() { if (pv) { pv.destroy(); pv = null; } }
  function feedPreview() {
    if (!pv) return;
    var st = gset().get(), snap = window.gsGlobeCard ? window.gsGlobeCard.snapshot() : { active: [] };
    var give = function (list) {
      if (pv) pv.setActive(list.map(function (d) { return { id: d.id, label: d.label, lat: d.lat, lon: d.lon, cat: st.color === 'server' ? d.server : d.type }; }));
    };
    if (snap.active.length) give(snap.active);
    var midnight = new Date(); midnight.setHours(0, 0, 0, 0);
    if (!snap.active.length || st.trail) {
      api('GET', '/api/globe' + (st.trail ? '?trail_since=' + Math.floor(midnight.getTime() / 1000) : '')).then(function (j) {
        if (!snap.active.length) give(j.active || []);
        if (pv && j.trail) pv.setTrail(j.trail);
      }).catch(function () { /* the preview still turns */ });
    }
  }
  function testPing(n) {
    if (!pv) return;
    var pick = n === 3 ? [TEST_CITIES[1], TEST_CITIES[5], TEST_CITIES[9]] : [TEST_CITIES[pvTest++ % TEST_CITIES.length]];
    pick.forEach(function (c) { pv.ping({ label: c[0], lat: c[1], lon: c[2] }); });
    var live = document.getElementById('st-globe-live');
    if (live) live.textContent = 'Test stream from ' + pick.map(function (c) { return c[0]; }).join(', ') + '.';
  }
  function changeGlobe(patch) {
    globeSaved = 'saving';
    paintGlobeSaved();
    gset().set(patch).then(function () { globeSaved = 'ok'; }).catch(function (e) { globeSaved = e.message || 'Could not save.'; }).then(paintGlobeSaved);
  }
  function paintGlobeSaved() {
    var m = document.getElementById('st-globe-saved');
    if (!m) return;
    m.textContent = globeSaved === 'saving' ? 'Saving\u2026' : globeSaved === 'ok' ? 'Saved to your account.' : globeSaved ? 'Not saved: ' + globeSaved : '';
    m.className = 'st-globeset__saved' + (globeSaved && globeSaved !== 'saving' && globeSaved !== 'ok' ? ' is-error' : '');
  }
  function globeControl(it, st) {
    var id = 'gl-' + it.k, hint = it.hint ? el('p', { class: 'sd-field__hint', id: id + '-h', text: it.hint }) : null;
    if (it.type === 'switch') {
      var box = el('input', { type: 'checkbox', class: 'st-switch', id: id, checked: !!st[it.k], 'aria-describedby': hint ? id + '-h' : null, onchange: function () {
        var p = {}; p[it.k] = box.checked; changeGlobe(p);
      } });
      return el('label', { class: 'st-switchrow st-glrow', for: id }, el('div', null, el('span', { class: 'st-switchrow__label', text: it.label }), hint), box);
    }
    if (it.type === 'seg') {
      var seg = el('div', { class: 'st-seg st-seg--lg', role: 'group', 'aria-label': it.label }, it.opts.map(function (o) {
        return el('button', { type: 'button', 'aria-pressed': String(st[it.k] === o[0]), text: o[1], onclick: function () {
          Array.prototype.forEach.call(seg.children, function (b) { b.setAttribute('aria-pressed', String(b === this)); }, this);
          var p = {}; p[it.k] = o[0]; changeGlobe(p);
        } });
      }));
      return el('div', { class: 'st-glrow st-glrow--seg' }, el('div', null, el('span', { class: 'st-switchrow__label', text: it.label }), hint), seg);
    }
    // range: a slider with its value in words; "steps" walks a list (Speed)
    var steps = it.type === 'steps', cur = st[it.k];
    var idx = steps ? Math.max(0, it.steps.indexOf(cur)) : cur;
    var out = el('output', { class: 'st-glrow__val', for: id, text: it.val(cur) });
    var rng = el('input', { type: 'range', class: 'sd-range', id: id, min: steps ? 0 : it.min, max: steps ? it.steps.length - 1 : it.max, step: steps ? 1 : it.step, value: idx,
      'aria-describedby': hint ? id + '-h' : null });
    var fill = function () {
      var lo = Number(rng.min), hi = Number(rng.max);
      rng.style.setProperty('--fill', ((Number(rng.value) - lo) / (hi - lo) * 100) + '%');
    };
    rng.addEventListener('input', function () {
      var v = steps ? it.steps[Number(rng.value)] : Number(rng.value);
      out.textContent = it.val(v);
      rng.setAttribute('aria-valuetext', it.val(v));
      fill();
      var p = {}; p[it.k] = v; changeGlobe(p);
    });
    rng.setAttribute('aria-valuetext', it.val(cur));
    fill();
    return el('div', { class: 'st-glrow st-glrow--range' },
      el('div', { class: 'st-glrow__top' }, el('label', { class: 'st-switchrow__label', for: id, text: it.label }), out),
      rng, el('div', { class: 'st-glrow__ticks', 'aria-hidden': 'true' }, it.ticks.map(function (t) { return el('span', { text: t }); })), hint);
  }
  function renderGlobe() {
    var pane = document.getElementById('st-pane-globe');
    if (!pane || !gset()) return;
    var st = gset().get();
    dropPreview();
    if (!renderGlobe.hooked) { renderGlobe.hooked = true; gset().on(function (x) { if (pv) { pv.setOptions(x); feedPreview(); } if (tab === 'home') renderHomeBody(); }); }
    var stage = el('div', { class: 'st-globeset__stage' });
    var preview = el('aside', { class: 'st-preview st-globeset__pv', 'aria-label': 'Preview' },
      el('div', { class: 'st-preview__head' }, el('span', { class: 'st-preview__label' }, el('i'), 'Preview')),
      stage,
      el('div', { class: 'st-row' },
        el('button', { type: 'button', class: 'st-btn st-btn--primary', onclick: function () { testPing(1); } }, window.sdIcon ? window.sdIcon('play', 16) : null, 'Play a test stream'),
        el('button', { type: 'button', class: 'st-btn', text: 'Three at once', onclick: function () { testPing(3); } })),
      el('p', { class: 'st-preview__note', text: 'Test streams only play here, never on the dashboard. Your device asking for less motion keeps the globe still and never zooms in on new streams, whatever is set here.' }),
      el('p', { class: 'sd-sr', id: 'st-globe-live', 'aria-live': 'polite' }));
    var ctl = el('div', { class: 'st-globeset__ctl' }, GLOBE_UI.map(function (sec) {
      return el('section', { class: 'st-section st-glsec' }, el('h3', { text: sec.h }), sec.items.map(function (it) { return globeControl(it, st); }));
    }), el('div', { class: 'st-row' },
      el('button', { type: 'button', class: 'st-btn', text: 'Reset globe to default', onclick: function () { changeGlobe(gset().defaults); renderGlobe(); } }),
      el('span', { class: 'st-spacer' }),
      el('p', { class: 'st-globeset__saved', id: 'st-globe-saved', role: 'status' })));
    pane.replaceChildren(ctl, preview);
    paintGlobeSaved();
    var canvas = el('canvas', { role: 'img', 'aria-label': 'Preview of the live streams globe with these settings' });
    stage.appendChild(canvas);
    pv = window.gsGlobe.create(canvas, { settings: st, colorOf: function (cat, light) { return window.gsGlobeCard ? window.gsGlobeCard.look(cat, light) : null; } });
    feedPreview();
  }
  new MutationObserver(function () { if (document.getElementById('settings-dock').hidden) dropPreview(); else if (tab === 'globe' && !pv) renderGlobe(); })
    .observe(document.getElementById('settings-dock'), { attributes: true, attributeFilter: ['hidden'] });

  // ---------- render ----------
  function render() {
    if (tab === 'display' || tab === 'home' || tab === 'reqlog' || tab === 'cards' || tab === 'globe') return;
    var pane = document.getElementById('st-pane-' + tab);
    if (!pane) return;
    var active = document.activeElement && pane.contains(document.activeElement) ? document.activeElement.id : null;
    pane.querySelectorAll('input[id], select[id]').forEach(function (f) {
      if (f.type === 'checkbox') drafts['chk:' + f.id] = f.checked;
      else if (f.type !== 'radio') drafts[f.id] = f.value;
    });
    pane.querySelectorAll('input[type="radio"]:checked').forEach(function (f) { drafts['radio:' + f.name] = f.value; });
    pane.replaceChildren();
    if (!state) {
      pane.appendChild(msgs.load ? message('load') : el('p', { class: 'st-note', text: 'Loading…' }));
      return;
    }
    if (tab === 'plex') {
      pane.appendChild(renderAccount());
      var disc = renderDiscovered();
      if (disc) pane.appendChild(disc);
      pane.appendChild(renderServers());
      pane.appendChild(renderManual());
    } else if (tab === 'netdata') {
      renderNetdata(pane);
    } else if (tab === 'alerts') {
      renderAlerts(pane);
    } else if (tab === 'billing') {
      renderBilling(pane);
    } else if (tab === 'requests') {
      renderRequests(pane);
    } else if (tab === 'books') {
      renderBooks(pane);
    } else if (tab === 'arr') {
      renderArr(pane);
    } else if (tab === 'access') {
      renderAccess(pane);
    }
    pane.querySelectorAll('input[id], select[id]').forEach(function (f) {
      if (f.type === 'checkbox') { if (Object.prototype.hasOwnProperty.call(drafts, 'chk:' + f.id)) f.checked = drafts['chk:' + f.id]; }
      else if (f.type !== 'radio' && Object.prototype.hasOwnProperty.call(drafts, f.id)) f.value = drafts[f.id];
    });
    pane.querySelectorAll('input[type="radio"]').forEach(function (f) {
      var v = drafts['radio:' + f.name];
      if (v != null) f.checked = f.value === v;
    });
    var ndMode = document.getElementById('nd-mode');
    if (ndMode) ndMode.dispatchEvent(new Event('change'));
    if (active && document.getElementById(active)) document.getElementById(active).focus();
  }

  function clearDrafts(prefix) {
    Object.keys(drafts).forEach(function (k) {
      var id = k.indexOf('chk:') === 0 ? k.slice(4) : k.indexOf('radio:') === 0 ? k.slice(6) : k;
      if (id.indexOf(prefix + '-') === 0) delete drafts[k];
    });
  }

  setTab(isAdmin() ? 'home' : 'display');
})();
