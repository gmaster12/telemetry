/* Greystar Telemetry: Phone & computer alerts (Web Push; app/push.py sends, web/sw.js shows them).

   Opened from the notifications sheet ("Get these on your phone or computer") and the user menu. Each device is
   turned on by itself: the browser asks for permission, then gives a subscription the site keeps.

   iPhone and iPad (Apple's rule, not ours): alerts need iOS or iPadOS 16.4 or newer, and they only work from the app
   added to the Home Screen from Safari, not in a Safari tab. The panel says so and walks through Add to Home Screen.

   window.gsPush = { open(), state() } */
(function () {
  'use strict';
  var U = window.gsUI;
  var el = U.el;
  var root = null, panel = null, detach = null, opener = null;
  var cfg = null, busy = false, msg = null;

  // ---------- what this device can do ----------
  var ua = navigator.userAgent || '';
  var isIOS = /iP(hone|ad|od)/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  var iosVer = (function () { var m = ua.match(/OS (\d+)[_.](\d+)/); return m ? [+m[1], +m[2]] : null; })();
  var iosTooOld = isIOS && iosVer && (iosVer[0] < 16 || (iosVer[0] === 16 && iosVer[1] < 4));
  var standalone = (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) || navigator.standalone === true;
  var supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && window.isSecureContext;

  function deviceName() {
    var os = /Android/.test(ua) ? 'Android' : isIOS ? (/iPad/.test(ua) || navigator.maxTouchPoints > 1 && !/iPhone/.test(ua) ? 'iPad' : 'iPhone')
      : /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) ? 'Mac' : /CrOS/.test(ua) ? 'Chromebook' : /Linux/.test(ua) ? 'Linux' : 'This device';
    var br = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Firefox\//.test(ua) ? 'Firefox' : /SamsungBrowser/.test(ua) ? 'Samsung Internet'
      : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : '';
    return os + (standalone ? ' · Home Screen app' : br ? ' · ' + br : '');
  }
  // How to allow alerts by hand, for the browser in use. Shown when the browser blocked them, or answered without
  // asking (Chrome's quieter messaging, or a prompt dismissed a few times: Chrome then stops asking for a while).
  function allowSteps() {
    var android = /Android/.test(ua), mac = /Mac OS X/.test(ua) && !isIOS, win = /Windows/.test(ua);
    var br = /Edg\//.test(ua) ? 'edge' : /Firefox\//.test(ua) ? 'firefox' : /SamsungBrowser/.test(ua) ? 'samsung'
      : /OPR\//.test(ua) ? 'opera' : /Chrome\//.test(ua) ? 'chrome' : /Safari\//.test(ua) ? 'safari' : 'other';
    var s;
    if (android && br !== 'firefox') {
      s = ['Tap the icon to the left of the web address at the top.', 'Tap Permissions (or Site settings), then Notifications.',
        'Choose Allow.', 'Come back here and tap Turn on alerts again.'];
    } else if (br === 'chrome' || br === 'opera') {
      s = ['Look at the right end of the address bar: if there is a bell with a line through it, click it and choose Allow. Otherwise:',
        'Click the icon to the left of the web address (two sliders), then Site settings.',
        'Next to Notifications, choose Allow.', 'Come back to this tab, reload the page, and tap Turn on alerts again.'];
    } else if (br === 'edge') {
      s = ['Look at the right end of the address bar: if there is a bell with a line through it, click it and choose Allow. Otherwise:',
        'Click the lock icon to the left of the web address, then Permissions for this site.',
        'Next to Notifications, choose Allow.', 'Reload the page and tap Turn on alerts again.'];
    } else if (br === 'firefox') {
      s = ['Click the icon to the left of the web address (a lock, or a speech bubble with a line through it).',
        'Next to Send notifications, remove Blocked (click the \u2715), or choose Allow.', 'Reload the page and tap Turn on alerts again.'];
    } else if (br === 'safari' && mac) {
      s = ['In the menu bar choose Safari, then Settings (or Preferences), then Websites.', 'Choose Notifications on the left.',
        'Find streaming.greystarnetworks.com and set it to Allow.', 'Reload the page and tap Turn on alerts again.'];
    } else {
      s = ['Open this browser\u2019s settings for streaming.greystarnetworks.com (often the icon to the left of the web address).',
        'Set Notifications to Allow.', 'Reload the page and tap Turn on alerts again.'];
    }
    var os = win ? 'Still nothing pops up? In Windows, open Settings, System, Notifications, and make sure your browser is turned on (and Do not disturb is off).'
      : mac ? 'Still nothing pops up? On the Mac, open System Settings, Notifications, choose your browser, and turn on Allow notifications (and check Focus is off).'
      : android ? 'Still nothing pops up? In Android Settings, Apps, choose your browser, Notifications, and make sure they are on.' : null;
    var box = el('div', { class: 'pu-help' }, el('ol', null, s.map(function (t) { return el('li', { text: t }); })));
    if (os) box.appendChild(el('p', { class: 'pu-muted', text: os }));
    box.appendChild(el('button', { type: 'button', class: 'nt-btn', text: 'Reload the page', onclick: function () { location.reload(); } }));
    return box;
  }
  function keyBytes(b64) {
    var s = (b64 + '='.repeat((4 - b64.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    var raw = atob(s), out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function registration() {
    return navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(function () { return navigator.serviceWorker.ready; });
  }
  function currentSub() {
    if (!supported) return Promise.resolve(null);
    return navigator.serviceWorker.getRegistration('/').then(function (r) { return r ? r.pushManager.getSubscription() : null; }).catch(function () { return null; });
  }

  // ---------- talking to the site ----------
  function load() {
    return currentSub().then(function (sub) {
      return U.api('GET', '/api/push' + (sub ? '?endpoint=' + encodeURIComponent(sub.endpoint) : '')).then(function (c) {
        cfg = c; cfg.sub = sub; return c;
      });
    });
  }
  function turnOn() {
    if (busy) return;
    busy = true; msg = null; paint();
    // Permission is asked inside the tap (Safari only allows it there).
    Promise.resolve(Notification.permission === 'granted' ? 'granted' : Notification.requestPermission()).then(function (perm) {
      if (perm !== 'granted') throw new Error(perm === 'denied' ? 'blocked' : 'quiet');
      return registration();
    }).then(function (reg) {
      return reg.pushManager.getSubscription().then(function (old) {
        return old || reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(cfg.publicKey) });
      });
    }).then(function (sub) {
      var j = sub.toJSON();
      return U.api('POST', '/api/push/subscribe', { endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth, device: deviceName() });
    }).then(function () {
      msg = ['ok', 'Alerts are on for this device. Send a test to check.'];
      return load();
    }).catch(function (e) {
      msg = e.message === 'blocked' ? null
        : e.message === 'quiet' ? ['help', 'Your browser did not show the Allow question. It may be hiding it, or it stops asking after a few times. Allow alerts by hand:']
        : ['error', e.message || 'Alerts could not be turned on.'];
      return load();
    }).then(function () { busy = false; paint(); });
  }
  function turnOff() {
    if (busy) return;
    busy = true; paint();
    currentSub().then(function (sub) {
      if (!sub) return null;
      var ep = sub.endpoint;
      return sub.unsubscribe().catch(function () {}).then(function () { return U.api('POST', '/api/push/unsubscribe', { endpoint: ep }); });
    }).then(function () { msg = ['ok', 'Alerts are off for this device.']; return load(); })
      .catch(function (e) { msg = ['error', e.message]; })
      .then(function () { busy = false; paint(); });
  }
  function test() {
    if (busy) return;
    busy = true; msg = null; paint();
    U.api('POST', '/api/push/test').then(function (r) {
      var ok = r.results.filter(function (x) { return x.ok; }).length;
      msg = ok ? ['ok', 'Test sent to ' + ok + (ok === 1 ? ' device' : ' devices') + '. It should pop up within a few seconds.']
        : ['error', 'The test could not be delivered. Turn alerts off and on again on this device.'];
      return load();
    }).catch(function (e) { msg = ['error', e.message]; })
      .then(function () { busy = false; paint(); });
  }
  function choose(key, on) {
    var all = Object.assign({}, cfg.choices); all[key] = on;
    cfg.choices = all;
    U.api('PUT', '/api/push/choices', { choices: all }).then(function (r) { cfg.choices = r.choices; }).catch(function (e) { U.toast(e.message, 'error'); load().then(paint); });
  }
  function forget(d) {
    U.api('DELETE', '/api/push/devices/' + d.id).then(function () { return load(); }).then(paint).catch(function (e) { U.toast(e.message, 'error'); });
  }

  // ---------- the panel ----------
  function iosSteps(open) {
    var d = el('details', { class: 'pu-ios' + (isIOS ? ' is-here' : '') });
    if (open) d.open = true;
    d.append(el('summary', { text: 'Using an iPhone or iPad?' }),
      el('p', { text: 'Apple only allows website alerts on iPhone and iPad with iOS or iPadOS 16.4 or newer, and only from the Telemetry app on your Home Screen, not from a Safari tab. To check your version: Settings, General, About.' }),
      el('ol', null,
        el('li', { text: 'Open streaming.greystarnetworks.com in Safari.' }),
        el('li', null, 'Tap the Share button ', el('span', { class: 'pu-key', 'aria-label': 'Share icon', text: '\u2191' }), ' at the bottom of the screen (at the top on iPad).'),
        el('li', { text: 'Scroll down and tap Add to Home Screen, then Add.' }),
        el('li', { text: 'Open Telemetry from the new icon on your Home Screen and sign in.' }),
        el('li', { text: 'Open the bell, tap Get these on your phone or computer, then Turn on alerts and choose Allow.' })),
      el('p', { class: 'pu-muted', text: 'Older iPhones and iPads cannot get these alerts; you still see everything under the bell on the site.' }));
    return d;
  }

  function statusBlock() {
    var on = !!(cfg && cfg.thisDevice);
    var box = el('div', { class: 'pu-status' + (on ? ' is-on' : '') });
    var line = function (t, s) { box.append(el('b', { text: t }), el('span', { text: s })); };
    var act = el('div', { class: 'pu-actions' });
    if (iosTooOld) {
      line('This ' + (/iPad/.test(ua) ? 'iPad' : 'iPhone') + ' cannot get alerts yet', 'It has iOS ' + iosVer.join('.') + '. Alerts need iOS 16.4 or newer: Settings, General, Software Update.');
    } else if (isIOS && !standalone) {
      line('Add Telemetry to your Home Screen first', 'On iPhone and iPad, alerts only work from the Home Screen app. Follow the steps below, then turn alerts on from there.');
    } else if (!supported) {
      line('This browser cannot show alerts', 'Try Chrome, Edge, Firefox or Safari. Everything still shows under the bell here.');
    } else if (Notification.permission === 'denied') {
      line('Alerts are blocked for this site', 'This browser was told not to allow alerts from this site. To turn them on:');
      box.appendChild(allowSteps());
    } else if (on) {
      line('On for this device', 'New things show on this device even when the site is closed.');
      act.append(el('button', { type: 'button', class: 'nt-btn nt-btn--accent', disabled: busy, text: 'Send a test', onclick: test }),
        el('button', { type: 'button', class: 'nt-btn', disabled: busy, text: 'Turn off for this device', onclick: turnOff }));
    } else {
      line('Off for this device', 'Get a pop-up on this ' + (/Android|iPhone|iPad/.test(deviceName()) ? 'phone' : 'computer') + ' when something you asked for is ready, even when the site is closed.');
      act.append(el('button', { type: 'button', class: 'nt-btn nt-btn--accent', disabled: busy, text: busy ? 'Turning on\u2026' : 'Turn on alerts', onclick: turnOn }));
    }
    if (act.childNodes.length) box.appendChild(act);
    if (msg && msg[1]) box.appendChild(el('p', { class: 'pu-msg pu-msg--' + msg[0], role: 'status', text: msg[1] }));
    if (msg && msg[0] === 'help') box.appendChild(allowSteps());
    return box;
  }

  function choicesBlock() {
    if (!cfg) return null;
    var list = el('div', { class: 'pu-choices' });
    cfg.labels.forEach(function (c) {
      var id = 'pu-c-' + c.key;
      var box = el('input', { type: 'checkbox', id: id });
      box.checked = !!cfg.choices[c.key];
      box.addEventListener('change', function () { choose(c.key, box.checked); });
      list.appendChild(el('label', { class: 'pu-choice', for: id }, box, el('span', { text: c.label })));
    });
    return el('div', { class: 'pu-sec' }, el('h3', { class: 'pu-h', text: 'Send me alerts for' }), list,
      el('p', { class: 'pu-muted', text: 'These apply to every device you turn on.' }));
  }

  function devicesBlock() {
    var devs = (cfg && cfg.devices) || [];
    if (!devs.length) return null;
    return el('div', { class: 'pu-sec' }, el('h3', { class: 'pu-h', text: 'Your devices with alerts on' }),
      el('ul', { class: 'pu-devs' }, devs.map(function (d) {
        return el('li', null, el('span', { text: d.device || 'A device' }), d.this ? el('span', { class: 'pu-tag', text: 'This device' }) : null,
          d.this ? null : el('button', { type: 'button', class: 'nt-btn', text: 'Remove', onclick: function () { forget(d); } }));
      })));
  }

  function paint() {
    if (!panel) return;
    var close = el('button', { type: 'button', class: 'nt-x', 'aria-label': 'Close', onclick: hide }, '\u2715');
    var head = el('header', { class: 'nt-head' },
      el('div', { class: 'nt-head__text' },
        el('span', { class: 'nt-eyebrow', text: 'Alerts' }),
        el('h2', { class: 'nt-title', id: 'pu-title', text: 'Phone & computer alerts' }),
        el('span', { class: 'nt-lead', text: 'Get a pop-up when your movies and shows are ready, when someone replies to a problem you reported, and more.' })),
      close);
    var body = el('div', { class: 'nt-body pu-body' },
      cfg ? statusBlock() : el('p', { class: 'pu-muted', text: 'Loading\u2026' }),
      iosSteps(isIOS && !standalone), choicesBlock(), devicesBlock());
    panel.replaceChildren(el('div', { class: 'gs-sheet__grab', 'aria-hidden': 'true' }), head, body,
      el('footer', { class: 'nt-foot' }, el('button', { type: 'button', class: 'nt-btn nt-btn--lg', text: 'Done', onclick: hide })));
  }

  function build() {
    root = el('div', { class: 'nt-scrim pu-scrim', hidden: true, onclick: function (e) { if (e.target === root) hide(); } });
    panel = el('section', { class: 'nt-sheet pu-sheet', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'pu-title', tabindex: '-1' });
    root.appendChild(panel);
    (document.getElementById('streams-app') || document.body).appendChild(root);
    root.addEventListener('keydown', function (e) { if (e.key === 'Escape') { e.stopPropagation(); hide(); } });
  }
  function open() {
    if (!root) build();
    opener = document.activeElement;
    msg = null;
    paint();
    root.hidden = false;
    document.documentElement.classList.add('nt-open');
    detach = U.sheet(panel, { onClose: hide, scrim: root });
    requestAnimationFrame(function () { root.classList.add('is-in'); panel.focus({ preventScroll: true }); });
    load().then(paint).catch(function (e) { msg = ['error', e.message]; paint(); });
  }
  function hide() {
    if (!root || root.hidden) return;
    root.classList.remove('is-in');
    document.documentElement.classList.remove('nt-open');
    if (detach) { detach(); detach = null; }
    setTimeout(function () { root.hidden = true; }, 220);
    if (opener && opener.focus) opener.focus({ preventScroll: true });
  }
  // On, off, or "can't" for this device: the notifications sheet's strip shows it.
  function state() {
    if (iosTooOld || !supported && !isIOS) return Promise.resolve('unsupported');
    if (isIOS && !standalone) return Promise.resolve('ios-home');
    return currentSub().then(function (s) { return s ? 'on' : 'off'; });
  }
  // A device that is already on: tell the site again on each visit, so a renewed or moved subscription stays known.
  if (supported) {
    setTimeout(function () {
      currentSub().then(function (sub) {
        if (!sub) return;
        var j = sub.toJSON();
        U.api('POST', '/api/push/subscribe', { endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth, device: deviceName() }).catch(function () {});
      });
    }, 4000);
  }
  window.gsPush = { open: open, state: state };
})();
