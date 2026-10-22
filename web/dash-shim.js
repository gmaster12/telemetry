/* Greystar Telemetry: a small stand-in for the dashboard runtime the page was
   designed in. Loads each dataset from the backend API, recomputes the page's
   calculations, and calls every onData callback when anything changes. */
(function () {
  'use strict';
  var tz = -new Date().getTimezoneOffset();
  var ENDPOINTS = {
    streams: '/api/streams',
    servers: '/api/servers',
    bandwidth: '/api/bandwidth',
    viewing_hours: '/api/viewing?tz=' + tz
  };
  var SECTION = { streams: 'streams', servers: 'servers', bandwidth: 'bandwidth', viewing_hours: 'viewing' };
  var REFRESH_MS = 10000;
  var me = null;
  var loaded = false;
  var KEEP = ['look', 'cardsize', 'artlight'];
  var store = {};
  var calcs = {};
  var callbacks = [];
  var params = {};

  try {
    var saved = JSON.parse(window.localStorage.getItem('gs-params') || '{}');
    KEEP.forEach(function (k) { if (saved[k] != null) params[k] = saved[k]; });
  } catch (e) { /* storage unavailable: start with defaults */ }

  function fire() {
    callbacks.forEach(function (fn) {
      try { fn(); } catch (e) { console.error(e); }
    });
  }

  function can(section) {
    return !!me && (me.role === 'admin' || (me.sections || []).indexOf(section) >= 0);
  }

  function toLogin() {
    window.location.replace('/login.html');
  }

  function load(id) {
    if (!can(SECTION[id])) {
      // Not shared with this viewer: an empty dataset keeps the page's calculations working.
      store[id] = { status: 'ok', data: [], sample: false, forbidden: true };
      return Promise.resolve();
    }
    return fetch(ENDPOINTS[id], { cache: 'no-store', credentials: 'same-origin' })
      .then(function (r) {
        if (r.status === 401) { toLogin(); throw new Error('Signed out'); }
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (j) {
        store[id] = { status: 'ok', data: j.rows || [], sample: !!j.sample, updated: j.updated };
      })
      .catch(function (e) {
        var prev = store[id];
        store[id] = prev && prev.status === 'ok'
          ? Object.assign({}, prev, { stale: true, message: String(e) })
          : { status: 'error', data: [], message: String(e) };
      });
  }

  function refresh() {
    if (!me) return Promise.resolve();
    return Promise.all(Object.keys(ENDPOINTS).map(load)).then(function () { loaded = true; fire(); });
  }

  function loadMe() {
    return fetch('/api/me', { cache: 'no-store', credentials: 'same-origin' }).then(function (r) {
      if (r.status === 401) { toLogin(); throw new Error('Signed out'); }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (j) {
      me = j;
      window.gsMe = j;
      document.dispatchEvent(new CustomEvent('gs:me', { detail: j }));
    });
  }

  window.dash = {
    data: function (id) {
      if (store[id]) return store[id];
      var c = calcs[id];
      if (c) {
        var inputs = c.inputs.map(function (i) { return window.dash.data(i); });
        if (inputs.some(function (x) { return x.status === 'error'; })) return { status: 'error', data: [] };
        if (inputs.some(function (x) { return x.status !== 'ok'; })) return { status: 'loading', data: [] };
        try {
          return { status: 'ok', data: c.fn.apply(null, inputs.map(function (x) { return x.data; })) };
        } catch (e) {
          console.error(e);
          return { status: 'error', data: [] };
        }
      }
      return { status: 'loading', data: [] };
    },
    calc: function (id, def) { calcs[id] = def; },
    loader: function () {},
    onData: function (fn) { callbacks.push(fn); try { fn(); } catch (e) { console.error(e); } },
    params: function () { return Object.assign({}, params); },
    setParams: function (obj) {
      Object.keys(obj).forEach(function (k) {
        if (obj[k] == null) delete params[k]; else params[k] = obj[k];
      });
      try {
        var keep = {};
        KEEP.forEach(function (k) { if (params[k] != null) keep[k] = params[k]; });
        window.localStorage.setItem('gs-params', JSON.stringify(keep));
      } catch (e) { /* ignore */ }
      fire();
    },
    resetParams: function () {
      Object.keys(params).forEach(function (k) { if (KEEP.indexOf(k) < 0) delete params[k]; });
      fire();
    },
    setLink: function (id) {
      try { window.history.replaceState(null, '', '#' + id); } catch (e) { /* ignore */ }
    },
    refresh: function () { return refresh(); },
    me: function () { return me; },
    can: can,
    isAdmin: function () { return !!me && me.role === 'admin'; },
    loaded: function () { return loaded; },
    isSample: function () {
      return Object.keys(ENDPOINTS).some(function (id) { return store[id] && store[id].sample && !store[id].forbidden; });
    },
    colors: []
  };

  var resizeTimer;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(fire, 150);
  });
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) refresh();
  });

  loadMe().then(function () { fire(); return refresh(); }).catch(function (e) { console.error(e); });
  setInterval(function () { if (!document.hidden) refresh(); }, REFRESH_MS);
})();
