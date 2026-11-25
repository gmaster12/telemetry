/* Greystar sign-up: Plex account, then the subscription, then onto the server (backend/app/signup.py).
   The page only ever shows what /api/signup/status says, so a reload or a later visit lands on the right step.
   Stripe's script is loaded only when the payment form is needed, and only this page is allowed to load it. */
(function () {
  'use strict';
  var HEADERS = { 'Content-Type': 'application/json', 'X-Requested-With': 'greystar' };
  // Where to get the Plex app. Edit here.
  var DEVICES = [
    { name: 'TV or streaming box', how: 'Roku, Fire TV, Apple TV, Google TV, Samsung, LG, Xbox, PlayStation: open the app store on it, search for Plex and install it.' },
    { name: 'Phone or tablet', how: 'Install Plex from the App Store (iPhone, iPad) or Google Play (Android).' },
    { name: 'Computer', how: 'Nothing to install: open app.plex.tv in your browser.' }
  ];
  var PLEX_APPS = 'https://www.plex.tv/apps-devices/';
  var PLEX_WEB = 'https://app.plex.tv';
  var PLEX_LINK = 'https://plex.tv/link';

  var card = document.getElementById('card');
  var steps = document.getElementById('steps');
  var cfg = null;       // /api/signup/config
  var view = null;      // /api/signup/status
  var timer = null;
  var checkout = null;  // Stripe's embedded checkout, while it is on the page
  var shown = null;     // the step drawn now, so a poll that changes nothing does not redraw (and lose focus)

  // ---------- small helpers ----------
  function el(tag, props) {
    var e = document.createElement(tag);
    props = props || {};
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v == null || v === false) return;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) add(e, arguments[i]);
    return e;
  }
  function add(e, kid) {
    if (kid == null || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(function (k) { add(e, k); }); return; }
    e.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
  }
  function read(r) {
    return r.json().catch(function () { return null; }).then(function (j) {
      if (!r.ok) {
        var d = j && j.detail;
        if (Array.isArray(d)) d = d.map(function (x) { return x.msg; }).join('; ');
        throw new Error(d || ('Something went wrong (HTTP ' + r.status + ').'));
      }
      return j;
    });
  }
  function get(path) { return fetch(path, { credentials: 'same-origin', cache: 'no-store' }).then(read); }
  function post(path) { return fetch(path, { method: 'POST', headers: HEADERS, credentials: 'same-origin' }).then(read); }
  function link(href, text) { return el('a', { class: 'su__link', href: href, target: '_blank', rel: 'noopener', text: text }); }
  function dots() { return el('span', { class: 'su__dots', 'aria-hidden': 'true' }, el('i'), el('i'), el('i')); }
  function money(p) {
    var n = (p.amount / 100).toFixed(2);
    return p.currency === 'usd' ? '$' + n : n + ' ' + String(p.currency).toUpperCase();
  }
  function every(p) { return p.interval_count > 1 ? 'every ' + p.interval_count + ' ' + p.interval + 's' : 'a ' + p.interval; }
  function support() { return cfg && cfg.support ? el('p', { class: 'su__note' }, el('b', { text: 'Need a hand? ' }), cfg.support) : null; }
  function stopTimer() { if (timer) clearTimeout(timer); timer = null; }
  // With Stripe test keys the page is closed to the public; the test accounts get in from /signup.html?test
  function testing() { return !!(cfg && cfg.test) && /[?&]test(=|&|$)/.test(window.location.search); }
  function dropCheckout() { if (checkout) { try { checkout.destroy(); } catch (e) { /* already gone */ } checkout = null; } }

  function mark(step) {
    var order = ['plex', 'pay', 'working', 'done'], at = order.indexOf(step);
    steps.hidden = at < 0;
    Array.prototype.forEach.call(steps.children, function (li) {
      var i = order.indexOf(li.getAttribute('data-step'));
      li.className = i === at ? 'is-now' : i < at ? 'is-done' : '';
      if (i === at) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
    });
  }
  function draw(step, title, kids) {
    shown = step;
    mark(step);
    var h = el('h1', { class: 'su__title', tabindex: '-1', text: title });
    card.replaceChildren(h);
    add(card, kids);
    h.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }
  function errorBox(text) { return el('p', { class: 'su__msg su__msg--error', role: 'alert', text: text }); }

  // ---------- step 1: Plex account ----------
  function showPlex(problem) {
    stopTimer(); dropCheckout();
    var plan = cfg.plan;
    var msg = el('p', { class: 'su__msg su__msg--wait', role: 'status', hidden: true });
    var err = problem ? errorBox(problem) : el('p', { class: 'su__msg su__msg--error', role: 'alert', hidden: true });
    var go = el('button', { type: 'button', class: 'su__btn su__btn--go', text: 'Continue with Plex' });
    go.addEventListener('click', function () { plexSignIn(go, msg, err); });
    draw('plex', 'Join Greystar', [
      testing() ? el('p', { class: 'su__msg su__msg--warn' }, el('b', { text: 'Test mode. ' }),
        'No real money is taken: use a Stripe test card. Only the test accounts listed in Settings can continue.') : null,
      plan ? el('p', { class: 'su__price' }, el('b', { text: money(plan) }), el('span', { text: every(plan) })) : null,
      el('ul', { class: 'su__list' },
        el('li', { text: 'Movies and shows on your TV, phone, tablet and computer, through the Plex app.' }),
        el('li', { text: 'Missing something? Ask for it here and we add it.' }),
        el('li', { text: 'You are added to ' + (cfg.server || 'the server') + ' as soon as your payment goes through, usually within seconds.' })),
      el('p', { class: 'su__note' }, el('b', { text: 'First, your Plex account. ' }),
        'Plex is the free app you watch with. Press the button and a Plex window opens: sign in, or choose ', el('b', { text: 'Sign up' }),
        ' there to make a free account. Then you come straight back here.'),
      go, msg, err,
      el('p', { class: 'su__note' }, 'Already a member? ', el('a', { class: 'su__link', href: '/login.html', text: 'Sign in' }), '.')
    ]);
  }

  function plexSignIn(go, msg, err, label) {
    label = label || 'Continue with Plex';
    err.hidden = true;
    // Open the window during the click so pop-up blockers allow it.
    var win = window.open('about:blank', 'plex-auth', 'width=520,height=720');
    var fail = function (e) {
      try { if (win) win.close(); } catch (x) { /* ignore */ }
      stopTimer();
      go.disabled = false; go.textContent = label;
      msg.hidden = true;
      err.textContent = e.message; err.hidden = false;
    };
    go.disabled = true; go.textContent = 'Waiting for Plex…';
    post('/api/signup/plex/start').then(function (pin) {
      if (win) win.location = pin.auth_url;
      msg.replaceChildren(dots(), 'Finish in the Plex window. If it did not open, ', link(pin.auth_url, 'open the Plex page'), '.');
      msg.hidden = false;
      var started = Date.now();
      var poll = function () {
        get('/api/signup/plex/check/' + encodeURIComponent(pin.pin_id)).then(function (r) {
          if (r && r.pending) {
            if (Date.now() - started > 10 * 60 * 1000) throw new Error('The Plex sign-in timed out. Try again.');
            timer = setTimeout(poll, 2000);
            return;
          }
          try { if (win) win.close(); } catch (e) { /* ignore */ }
          stopTimer();
          route(r);
        }).catch(fail);
      };
      timer = setTimeout(poll, 2000);
    }).catch(fail);
  }

  // ---------- step 2: subscribe ----------
  function loadStripe() {
    if (window.Stripe) return Promise.resolve();
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = 'https://js.stripe.com/v3/';
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error('The payment form could not load. Check your connection, or turn off any ad blocker for this page, then try again.')); };
      document.head.appendChild(s);
    });
  }

  function showPay() {
    stopTimer(); dropCheckout();
    var plan = cfg.plan;
    var box = el('div', { class: 'su__pay', id: 'checkout' });
    var err = el('div', { class: 'su__stack' });
    draw('pay', 'Subscribe', [
      el('p', { class: 'su__who' }, 'Signed in to Plex as ', el('b', { text: view.name || view.email }), view.name && view.email ? ' (' + view.email + ')' : ''),
      plan ? el('p', { class: 'su__note' }, el('b', { text: money(plan) + ' ' + every(plan) + '. ' }),
        'Your receipt goes to your Plex email, which is how we know the subscription is yours.') : null,
      box, err
    ]);
    post('/api/signup/checkout').then(function (out) {
      if (out.paid) { route(out); return; }
      return loadStripe().then(function () {
        return window.Stripe(out.publishable_key).initEmbeddedCheckout({
          fetchClientSecret: function () { return Promise.resolve(out.client_secret); },
          onComplete: function () { dropCheckout(); view.step = 'working'; showWorking(); }
        });
      }).then(function (c) {
        if (shown !== 'pay') { try { c.destroy(); } catch (e) { /* ignore */ } return; }
        checkout = c;
        c.mount('#checkout');
      });
    }).catch(function (e) {
      box.remove();
      err.replaceChildren(errorBox(e.message),
        el('button', { type: 'button', class: 'su__btn su__btn--go', text: 'Try again', onclick: function () { start(); } }), support() || '');
    });
  }

  // ---------- step 3: setting up ----------
  function showWorking() {
    dropCheckout();
    var body = el('div', { class: 'su__stack' });
    var line = el('span');
    draw('working', 'Setting you up', [
      el('p', { class: 'su__msg su__msg--wait', role: 'status' }, dots(), line),
      body
    ]);
    var paint = function () {
      line.textContent = view.confirming ? 'Confirming your payment with Stripe…' : 'Payment received. Adding you to ' + (view.server || 'the server') + '…';
      if (view.confirming) {
        body.replaceChildren(el('p', { class: 'su__note', text: view.stuck
          ? 'Stripe has not confirmed the payment yet. We have been told and are looking at it. You do not need to pay again.'
          : 'Some ways of paying take a little while to confirm. You are added as soon as Stripe confirms it; you do not need to pay again.' }),
          view.stuck ? support() || '' : '');
      } else if (view.stuck) {
        body.replaceChildren(el('p', { class: 'su__msg su__msg--warn' }, el('b', { text: 'Your payment went through. ' }),
          'Adding you to Plex is taking longer than it should. We have been told and will finish it for you. You do not need to pay again. You can close this page; to see where it stands, come back here and continue with the same Plex account.'),
          support() || '');
      } else if (view.accept) {
        body.replaceChildren(el('p', { class: 'su__msg su__msg--warn' }, el('b', { text: 'One thing for you to do. ' }),
          'Plex sent an invite to ' + (view.email || 'your email') + '. Open that email and press Accept (or open ', link(PLEX_WEB, 'Plex in your browser'),
          ', which also shows the invite). This page carries on by itself once you have.'));
      } else {
        body.replaceChildren(el('p', { class: 'su__note', text: 'This usually takes a few seconds. Keep this page open.' }));
      }
    };
    paint();
    var poll = function () {
      get('/api/signup/status').then(function (v) {
        view = v;
        if (v.step !== 'working') { stopTimer(); route(v); return; }
        paint();
        timer = setTimeout(poll, 3000);
      }).catch(function () { timer = setTimeout(poll, 6000); });
    };
    stopTimer();
    timer = setTimeout(poll, 1500);
  }

  // ---------- step 4: you are in ----------
  var VIDEO = '/assets/how-to-request.mp4';
  var VIDEO_POSTER = '/assets/how-to-request.jpg';
  var BUFFER_EMAIL = 'info@greystarnetworks.com';
  // The last screen waits for these before it unlocks the button (backend: Signup.prepare). The first two are
  // true by the time this screen shows.
  var READY = [
    { key: null, text: 'Payment confirmed' },
    { key: null, text: function () { return 'Added to ' + (view.server || 'the server') + ' on Plex'; } },
    { key: 'matched', text: 'Subscription matched to your Plex account' },
    { key: 'billing', text: 'Billing details refreshed' },
    { key: 'requests', text: 'Requests account created' }
  ];

  function videoCard() {
    var box = el('div');
    var card = el('button', { type: 'button', class: 'su__video' },
      el('span', { class: 'su__play', 'aria-hidden': 'true' }),
      el('span', null, 'Watch: how to request a movie, show, ebook or audiobook', el('small', { text: '2 minutes · made for phones · no sound' })));
    card.addEventListener('click', function () {
      var v = el('video', { class: 'su__film', src: VIDEO, poster: VIDEO_POSTER, controls: true, playsinline: true, preload: 'metadata' });
      box.replaceChild(v, card);
      var p = v.play(); if (p && p.catch) p.catch(function () { /* they can press play */ });
    });
    box.appendChild(card);
    return box;
  }

  function showDone() {
    stopTimer(); dropCheckout();
    var server = view.server || 'the server';
    var title = el('h2'), fill = el('i'), bar = el('div', { class: 'su__bar', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100' }, fill);
    var note = el('span'), pct = el('b'), tasks = el('ul', { class: 'su__tasks' });
    var items = READY.map(function (t) { var li = el('li', { text: typeof t.text === 'function' ? t.text() : t.text }); tasks.appendChild(li); return li; });
    var open = el('button', { type: 'button', class: 'su__btn su__btn--go' });
    var opening = false;
    open.addEventListener('click', function () {
      opening = true; open.disabled = true; open.textContent = 'Opening…';
      post('/api/signup/finish').then(function () { window.location.replace('/'); })
        .catch(function () { window.location.replace('/login.html'); });   // the sign-up is done either way: Sign in with Plex works now
    });
    function paint(r) {
      r = r || {};
      var n = 0, now = false;
      READY.forEach(function (t, i) {
        var ok = !t.key || !!r[t.key];
        if (ok) n++;
        items[i].className = ok ? 'is-done' : (r.done || now) ? '' : 'is-now';
        if (!ok) now = true;
      });
      var p = r.done && !r.late ? 100 : Math.round(n / READY.length * 100);
      fill.style.width = p + '%'; pct.textContent = p + '%'; bar.setAttribute('aria-valuenow', String(p));
      title.textContent = r.done ? 'Your Greystar account is ready' : 'Getting your Greystar account ready';
      note.textContent = !r.done ? 'This takes about half a minute. Keep this page open.'
        : r.late ? 'You can sign in now. The rest finishes by itself in a few minutes.' : 'You can sign in now.';
      if (!opening) { open.disabled = !r.done; open.textContent = r.done ? 'Open Greystar and request something' : 'Getting ready…'; }
    }
    function poll() {
      get('/api/signup/status').then(function (v) {
        if (v.step !== 'done') { stopTimer(); route(v); return; }
        view = v; paint(v.ready);
        if (!(v.ready && v.ready.done)) timer = setTimeout(poll, 2000);
      }).catch(function () { timer = setTimeout(poll, 5000); });
    }
    draw('done', 'You’re in' + (view.name ? ', ' + view.name : ''), [
      el('p', { class: 'su__sub' }, 'Your subscription is active and ', el('b', { text: server }), ' is shared with your Plex account.'),
      el('div', { class: 'su__ready' }, title, bar, el('div', { class: 'su__pct' }, note, pct), tasks, open),
      el('div', { class: 'su__block' },
        el('h2', { text: '1. Install Plex on your TV and devices' }),
        el('ul', { class: 'su__list' }, DEVICES.map(function (d) { return el('li', null, el('b', { text: d.name + ': ' }), d.how); })),
        el('ol', { class: 'su__num' },
          el('li', null, 'Open Plex and sign in with the same Plex account', view.email ? el('b', { text: ' (' + view.email + ')' }) : null,
            '. On a TV it shows a 4-letter code: go to ', link(PLEX_LINK, 'plex.tv/link'), ' on your phone and type it in.'),
          el('li', null, 'Find ', el('b', { text: server }), ': open the menu and choose ', el('b', { text: 'More' }), '. Its Movies and TV Shows are listed there.'),
          el('li', null, 'Pin them so they stay on your home screen: highlight a library, open its menu (the three dots) and choose ', el('b', { text: 'Pin' }), '.')),
        el('p', { class: 'su__note' }, link(PLEX_APPS, 'Every device Plex works on'), ' · ', link(PLEX_WEB, 'Watch in your browser now')),
        el('p', { class: 'su__star' }, el('b', { class: 'su__star-mark', text: '*' }), 'If you experience any amount of repeated buffering, email us at ',
          el('a', { href: 'mailto:' + BUFFER_EMAIL, text: BUFFER_EMAIL }), ' and we can move you to a closer server that will resolve the problem.')),
      el('div', { class: 'su__block' },
        el('h2', { text: '2. Request movies, shows and books' }),
        videoCard(),
        el('ol', { class: 'su__num' },
          el('li', null, 'Come to ', el('b', { text: window.location.host }), ' and press ', el('b', { text: 'Sign in with Plex' }), '. There is no separate password.'),
          el('li', null, 'Open ', el('b', { text: 'Requests' }), ', search for a movie or show, and press Request. Ebooks and audiobooks are in the menu above the search box.'),
          el('li', null, 'We tell you here when it is ready. You can also see your subscription and what you have watched.'))),
      support()
    ]);
    paint(view.ready);
    if (!(view.ready && view.ready.done)) timer = setTimeout(poll, 1200);
  }

  // ---------- the other endings ----------
  function showMember() {
    stopTimer(); dropCheckout();
    draw('member', 'You already have an account', [
      el('p', { class: 'su__sub', text: 'That Plex account is already on a Greystar server, so there is nothing to pay for here.' }),
      el('a', { class: 'su__btn su__btn--go', href: '/login.html', text: 'Sign in' })
    ]);
  }
  function showPaying() {
    stopTimer(); dropCheckout();
    draw('paying', 'You already have a subscription', [
      el('p', { class: 'su__sub', text: 'There is already a subscription under your Plex email, so we have not charged you again. If you cannot see the server in Plex, get in touch and we will sort it out.' }),
      support(),
      el('a', { class: 'su__btn su__btn--quiet', href: '/login.html', text: 'Go to sign in' })
    ]);
  }
  function showClosed(message) {
    stopTimer(); dropCheckout();
    var kids = [el('p', { class: 'su__sub', text: message || 'Sign-ups are closed right now.' }), support()];
    if (cfg && cfg.resume) {   // someone who already paid can always get back to where they were
      var msg = el('p', { class: 'su__msg su__msg--wait', role: 'status', hidden: true });
      var err = el('p', { class: 'su__msg su__msg--error', role: 'alert', hidden: true });
      var go = el('button', { type: 'button', class: 'su__btn su__btn--quiet', text: 'Already paid here? Continue with Plex' });
      go.addEventListener('click', function () { plexSignIn(go, msg, err, 'Already paid here? Continue with Plex'); });
      kids.push(go, msg, err);
    }
    kids.push(el('a', { class: 'su__btn su__btn--quiet', href: '/login.html', text: 'Already a member? Sign in' }));
    draw('closed', 'Sign-ups are closed', kids);
  }

  function route(v) {
    view = v || { step: 'plex' };
    if (view.step === 'pay') showPay();
    else if (view.step === 'working') showWorking();
    else if (view.step === 'done') showDone();
    else if (view.step === 'member') showMember();
    else if (view.step === 'paying') showPaying();
    else if (view.step === 'closed') showClosed(view.message);
    else if (!cfg.open && !testing()) showClosed(cfg.message);
    else showPlex();
  }

  function start() {
    Promise.all([get('/api/signup/config'), get('/api/signup/status')]).then(function (r) {
      cfg = r[0];
      route(r[1]);
    }).catch(function (e) {
      draw('error', 'Something went wrong', [errorBox(e.message),
        el('button', { type: 'button', class: 'su__btn su__btn--go', text: 'Try again', onclick: start })]);
    });
  }

  start();
})();
