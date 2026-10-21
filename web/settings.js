/* Greystar Telemetry: Settings screen for Plex servers, Netdata and alerts.
   Tokens are sent to the backend once and never come back to the browser. */
(function () {
  'use strict';

  var HEADERS = { 'Content-Type': 'application/json', 'X-Requested-With': 'greystar' };
  var tab = 'display';
  var state = null;
  var nodes = [];
  var discovered = null;
  var editingId = null;
  var removingId = null;
  var link = null; // { pin_id, code, auth_url, timer, started }
  var msgs = {};
  var busy = {};
  var drafts = {};
  var manualOpen = false;

  window.stActiveTab = function () { return tab; };

  // ---------- helpers ----------
  function el(tag, props) {
    var e = document.createElement(tag);
    props = props || {};
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v == null || v === false) return;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), v);
      else if (k === 'value') e.value = v;
      else if (k === 'checked') e.checked = !!v;
      else e.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) {
      var kid = arguments[i];
      if (kid == null || kid === false) continue;
      if (Array.isArray(kid)) kid.forEach(function (k) { if (k) e.appendChild(k); });
      else e.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    }
    return e;
  }

  function api(method, path, body) {
    return fetch(path, {
      method: method,
      headers: HEADERS,
      credentials: 'same-origin',
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (j) {
        if (!r.ok) {
          var d = j && j.detail;
          if (Array.isArray(d)) d = d.map(function (x) { return x.msg; }).join('; ');
          throw new Error(d || ('HTTP ' + r.status));
        }
        return j;
      });
    });
  }

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

  // ---------- tabs ----------
  function setTab(t) {
    tab = t;
    document.querySelectorAll('.st-tabs [role="tab"]').forEach(function (b) {
      var on = b.dataset.tab === t;
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      b.classList.toggle('gs-chip--selected', on);
      b.tabIndex = on ? 0 : -1;
    });
    ['display', 'plex', 'netdata', 'alerts'].forEach(function (p) {
      var pane = document.getElementById('st-pane-' + p);
      if (pane) pane.hidden = p !== t;
    });
    if (t !== 'display') loadState();
  }

  document.querySelectorAll('.st-tabs [role="tab"]').forEach(function (b) {
    b.addEventListener('click', function () { setTab(b.dataset.tab); });
    b.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      var tabs = Array.prototype.slice.call(document.querySelectorAll('.st-tabs [role="tab"]'));
      var i = tabs.indexOf(b) + (e.key === 'ArrowRight' ? 1 : -1);
      var next = tabs[(i + tabs.length) % tabs.length];
      next.focus();
      setTab(next.dataset.tab);
    });
  });

  // ---------- Plex: sign in ----------
  function startLink() {
    var win = window.open('about:blank', '_blank');
    withBusy('link', api('POST', '/api/settings/plex/link').then(function (pin) {
      link = { pin_id: pin.pin_id, code: pin.code, auth_url: pin.auth_url, started: Date.now() };
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
        link = null;
        msgs.plex = { kind: 'ok', text: 'Signed in to Plex. Finding your servers…' };
        loadState().then(discover);
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

  function unlink() {
    api('POST', '/api/settings/plex/unlink').then(function () {
      discovered = null;
      msgs.plex = { kind: 'ok', text: 'Signed out of Plex. Servers you already added keep their own access.' };
      return loadState();
    }).catch(function (e) { setMsg('plex', 'error', e.message); });
  }

  function discover() {
    return withBusy('discover', api('GET', '/api/settings/plex/discover').then(function (r) {
      discovered = r.servers || [];
      msgs.plex = discovered.length ? null : { kind: 'error', text: 'Your Plex account has no servers.' };
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

  function removeServer(id) {
    return withBusy('remove-' + id, api('DELETE', '/api/settings/servers/' + id).then(function () {
      removingId = null;
      if (discovered) discover();
      return afterChange();
    }).catch(function (e) { setMsg('plex', 'error', e.message); }));
  }

  // ---------- render: Plex ----------
  function renderAccount() {
    var sec = el('section', { class: 'st-section' }, el('h3', { text: 'Plex account' }));
    if (link) {
      sec.appendChild(el('p', { class: 'st-note' }, 'Approve access in the Plex tab that just opened. If it did not open, ',
        el('a', { href: link.auth_url, target: '_blank', rel: 'noopener', text: 'open the Plex sign-in page' }), '. Code ',
        el('span', { class: 'st-code', text: link.code })));
      sec.appendChild(el('div', { class: 'st-row' }, el('button', { type: 'button', class: 'st-btn', text: 'Cancel', onclick: cancelLink })));
    } else if (state.plex_account_linked) {
      sec.appendChild(el('p', { class: 'st-note', text: 'Signed in. The dashboard can list your servers and fetch their access tokens for you.' }));
      sec.appendChild(el('div', { class: 'st-row' },
        el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy.discover ? 'Finding servers…' : 'Find my servers', disabled: busy.discover, onclick: discover }),
        el('span', { class: 'st-spacer' }),
        el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Sign out of Plex', onclick: unlink })));
    } else {
      sec.appendChild(el('p', { class: 'st-note', text: 'Sign in once and the dashboard finds your Plex servers and their access tokens, so you never copy a token by hand.' }));
      sec.appendChild(el('div', { class: 'st-row' },
        el('button', { type: 'button', class: 'st-btn st-btn--accent', text: busy.link ? 'Opening Plex…' : 'Sign in with Plex', disabled: busy.link, onclick: startLink })));
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
        d.owned ? null : el('span', { class: 'st-tag', text: 'Shared with you' }),
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
    return el('section', { class: 'st-section' }, el('h3', { text: 'Servers on your Plex account' }),
      el('p', { class: 'st-note', text: 'Pick the address this dashboard should use. A local or private address is fastest.' }), list);
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
      el('div', { class: 'st-wide' }, field('Plex address', url)),
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
            ? el('span', { class: 'st-row' }, el('span', { class: 'st-note', text: 'Remove ' + s.name + '? Its history stays.' }),
                el('button', { type: 'button', class: 'st-btn st-btn--quiet', text: 'Keep', onclick: function () { removingId = null; render(); } }),
                el('button', { type: 'button', class: 'st-btn st-btn--danger', text: busy['remove-' + s.id] ? 'Removing…' : 'Remove', onclick: function () { removeServer(s.id); } }))
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
    var token = input({ type: 'password', placeholder: state.plex_account_linked ? 'Optional: uses your Plex sign-in' : 'X-Plex-Token', autocomplete: 'new-password' }, key + '-token');
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
        setMsg('netdata', 'ok', 'Connected' + (r.nodes ? ' to ' + r.nodes + ' nodes' : '') + '. ' + (parts.join(' · ') || 'Link servers to nodes under Plex servers.'));
      }).catch(function (e) { setMsg('netdata', 'error', e.message); }));
    };
    pane.appendChild(el('section', { class: 'st-section' },
      el('h3', { text: 'Server telemetry' }),
      el('p', { class: 'st-note', text: 'CPU and network traffic for the capacity rings, server status and throughput.' }),
      field('Source', mode), cloudOnly,
      el('details', { class: 'st-details' }, el('summary', { text: 'Advanced: metric names' }),
        el('div', { class: 'st-form' }, field('CPU context', cpu), field('Network context', net),
          el('div', { class: 'st-wide' }, field('Outbound dimension', dim, 'Windows nodes may use different names. Check one node’s charts in Netdata.')))),
      el('div', { class: 'st-row' },
        el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy['nd-save'] ? 'Saving…' : 'Save', disabled: busy['nd-save'], onclick: save }),
        el('button', { type: 'button', class: 'st-btn', text: busy['nd-test'] ? 'Testing…' : 'Test connection', disabled: busy['nd-test'] || nd.mode === 'off', onclick: test })),
      nd.error ? el('p', { class: 'st-msg st-msg--error' }, el('b', { text: 'Error: ' }), 'Last poll failed: ' + nd.error) : null,
      message('netdata')));
  }

  // ---------- render: Alerts ----------
  function renderAlerts(pane) {
    var val = el('output', { text: Math.round(state.hot_cpu_pct) + '%' });
    var range = el('input', { class: 'sd-range', type: 'range', min: 50, max: 100, step: 5, value: state.hot_cpu_pct, 'aria-label': 'Hot CPU threshold', id: 'al-hot' });
    range.addEventListener('input', function () { val.textContent = range.value + '%'; });
    pane.appendChild(el('section', { class: 'st-section' },
      el('div', { class: 'sd-field__row' }, el('h3', { text: 'Hot CPU threshold' }), val),
      range,
      el('p', { class: 'st-note', text: 'A server above this is marked degraded, turns amber on the capacity rings and gets an alert.' }),
      el('div', { class: 'st-row' },
        el('button', { type: 'button', class: 'st-btn st-btn--primary', text: busy.general ? 'Saving…' : 'Save', disabled: busy.general,
          onclick: function () {
            withBusy('general', api('PUT', '/api/settings/general', { hot_cpu_pct: Number(range.value) }).then(function () {
              msgs.alerts = { kind: 'ok', text: 'Saved.' };
              return afterChange();
            }).catch(function (e) { setMsg('alerts', 'error', e.message); }));
          } })),
      message('alerts')));
  }

  // ---------- render ----------
  function readOnlyNote() {
    if (!state || state.auth_configured) return null;
    return el('p', { class: 'st-msg st-msg--error' }, el('b', { text: 'Error: ' }),
      'Settings can’t be saved until a dashboard login is set. Add BASIC_AUTH_USER and BASIC_AUTH_PASSWORD to the server’s .env and restart.');
  }

  function render() {
    if (tab === 'display') return;
    var pane = document.getElementById('st-pane-' + tab);
    if (!pane) return;
    var active = document.activeElement && pane.contains(document.activeElement) ? document.activeElement.id : null;
    pane.querySelectorAll('input[id], select[id]').forEach(function (f) {
      if (f.type !== 'radio') drafts[f.id] = f.value;
    });
    pane.querySelectorAll('input[type="radio"]:checked').forEach(function (f) { drafts['radio:' + f.name] = f.value; });
    pane.replaceChildren();
    if (!state) {
      pane.appendChild(msgs.load ? message('load') : el('p', { class: 'st-note', text: 'Loading…' }));
      return;
    }
    var ro = readOnlyNote();
    if (ro) pane.appendChild(ro);
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
    }
    pane.querySelectorAll('input[id], select[id]').forEach(function (f) {
      if (f.type !== 'radio' && Object.prototype.hasOwnProperty.call(drafts, f.id)) f.value = drafts[f.id];
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
    Object.keys(drafts).forEach(function (k) { if (k.indexOf(prefix + '-') === 0) delete drafts[k]; });
  }

  setTab('display');
})();
