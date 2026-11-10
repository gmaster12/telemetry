/* Greystar Telemetry: shared page helpers, loaded before every page script.

   One copy of what each page file used to rebuild for itself:
     el / elc       build elements (props style, and the older tag + class + text style)
     api            fetch JSON with the CSRF header; 401 goes to the sign-in page
     ago / when / date / duration   time words, the same everywhere
     toast          a short message above the phone tab bar
     lock           holds the page still under a sheet, dialog or drawer (counted, so two can overlap)
     go / scroll    hash navigation and putting you back where you were on Back
     sheet          the one phone bottom sheet: dimmed page, page held still, swipe down or tap outside to close
     isPhone        the phone layout rule: 760 px and narrower, or a touch screen under 500 px tall (a phone on its side) */
(function () {
  'use strict';

  var PHONE = '(max-width: 760px), (max-height: 500px) and (pointer: coarse)';
  var phoneQuery = window.matchMedia(PHONE);
  function isPhone() { return phoneQuery.matches; }
  function reduced() { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); }

  // ---------- elements ----------
  function add(e, kid) {
    if (kid == null || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(function (k) { add(e, k); }); return; }
    e.appendChild(typeof kid === 'string' || typeof kid === 'number' ? document.createTextNode(String(kid)) : kid);
  }
  function el(tag, props) {
    var e = document.createElement(tag);
    props = props || {};
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v == null || v === false) return;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k === 'html') throw new Error('no html');
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), v);
      else if (k === 'value') e.value = v;
      else if (k === 'checked') e.checked = !!v;
      else if (k === 'style') e.setAttribute('style', v);
      else e.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) add(e, arguments[i]);
    return e;
  }
  function elc(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // ---------- server calls ----------
  var HEADERS = { 'Content-Type': 'application/json', 'X-Requested-With': 'greystar' };
  function api(method, path, body, opts) {
    return fetch(path, { method: method || 'GET', headers: HEADERS, credentials: 'same-origin', cache: opts && opts.cache, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        if (r.status === 401) { window.location.replace('/login.html'); throw new Error('Signed out.'); }
        return r.json().catch(function () { return null; }).then(function (j) {
          if (!r.ok) {
            var d = j && j.detail;
            if (Array.isArray(d)) d = d.map(function (x) { return x.msg; }).join('; ');
            var err = new Error(d || ('HTTP ' + r.status));
            err.status = r.status;
            throw err;
          }
          return j;
        });
      });
  }

  // ---------- time words ----------
  function secs(t) {
    if (t == null || t === '') return null;
    if (typeof t === 'string') { var d = new Date(t); return isNaN(d) ? null : d.getTime() / 1000; }
    return t > 1e12 ? t / 1000 : t; // milliseconds or seconds
  }
  /** "just now", "5 min ago", "3 h ago", "yesterday", "4 days ago", then a date. */
  function ago(t) {
    var ts = secs(t);
    if (!ts) return 'never';
    var s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 90) return 'just now';
    if (s < 3600) return Math.round(s / 60) + ' min ago';
    if (s < 86400) return Math.round(s / 3600) + ' h ago';
    if (s < 2 * 86400) return 'yesterday';
    if (s < 30 * 86400) return Math.round(s / 86400) + ' days ago';
    return date(ts * 1000, { month: 'short', day: 'numeric', year: 'numeric' });
  }
  /** A moment: "Nov 10, 2:52 AM". */
  function when(t) {
    var ts = secs(t);
    if (!ts) return 'never';
    var d = new Date(ts * 1000);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ', ' + d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }
  /** A day: "Nov 10, 2026" (a bare YYYY-MM-DD is read at noon so time zones do not move it). */
  function date(t, opts) {
    if (t == null || t === '') return '';
    var d = typeof t === 'string' ? new Date(t.length === 10 ? t + 'T12:00:00' : t) : new Date(t > 1e12 ? t : t * 1000);
    if (isNaN(d)) return '';
    return d.toLocaleDateString(undefined, opts || { month: 'short', day: 'numeric', year: 'numeric' });
  }
  /** How long: "under a minute", "14 min", "2 h 14 min", "3 days". */
  function duration(sec) {
    sec = Math.max(0, sec || 0);
    if (sec < 60) return 'under a minute';
    var m = Math.floor(sec / 60), h = Math.floor(m / 60), d = Math.floor(h / 24);
    if (d >= 2) return d + ' days';
    if (h >= 1) return h + ' h' + (m % 60 ? ' ' + (m % 60) + ' min' : '');
    return m + ' min';
  }

  // ---------- toast ----------
  function toast(text, tone) {
    var t = el('div', { class: 'gs-toast' + (tone === 'error' ? ' is-error' : ''), role: tone === 'error' ? 'alert' : 'status' }, text);
    (document.getElementById('streams-app') || document.body).appendChild(t);
    requestAnimationFrame(function () { t.classList.add('is-in'); });
    setTimeout(function () { t.classList.remove('is-in'); setTimeout(function () { t.remove(); }, 300); }, 4200);
    return t;
  }

  // ---------- holding the page still ----------
  var locks = {};
  function lock(owner, on) {
    if (on) locks[owner] = 1; else delete locks[owner];
    document.documentElement.classList.toggle('gs-locked', Object.keys(locks).length > 0);
  }

  // ---------- navigation and scroll positions ----------
  // Each history entry gets a key (in history.state); its scroll position is kept under that key while you scroll,
  // so Back can put you where you were. New entries have no key yet and start at the top.
  try { if ('scrollRestoration' in history) history.scrollRestoration = 'manual'; } catch (e) { /* ignore */ }
  var STORE = 'gs-scroll';
  var pos = {};
  try { pos = JSON.parse(sessionStorage.getItem(STORE) || '{}') || {}; } catch (e) { pos = {}; }
  var cur = null;
  var saveTimer = 0;
  function persist() { try { sessionStorage.setItem(STORE, JSON.stringify(pos)); } catch (e) { /* ignore */ } }
  window.addEventListener('scroll', function () {
    if (!cur) return;
    pos[cur] = Math.round(window.scrollY);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(persist, 400);
  }, { passive: true });
  window.addEventListener('pagehide', persist);
  /** Call once when a page's address takes over. Returns where to scroll on Back or reload, or null for the top. */
  function enter() {
    var st = history.state && typeof history.state === 'object' ? history.state : null;
    if (st && st.gsk) {
      cur = st.gsk;
      return pos[cur] != null ? pos[cur] : null;
    }
    cur = 'k' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    try { history.replaceState(Object.assign({}, st, { gsk: cur }), ''); } catch (e) { /* ignore */ }
    return null;
  }
  /** Scroll to y, waiting up to a second and a half for a page that is still drawing to get tall enough. */
  function restore(y) {
    var until = Date.now() + 1500;
    var key = cur;
    (function tryIt() {
      if (key !== cur) return; // the person went somewhere else meanwhile
      var max = document.documentElement.scrollHeight - window.innerHeight;
      window.scrollTo(0, Math.min(y, Math.max(0, max)));
      if (max < y && Date.now() < until) setTimeout(tryIt, 120);
    })();
  }
  function top() { window.scrollTo(0, 0); }
  /** Go to a hash address; going to the address you are on runs its page again (hashchange does not fire). */
  function go(hash) {
    if (location.hash === hash || (!location.hash && (hash === '#/home' || hash === '#/'))) {
      if (cur) delete pos[cur];   // tapping the page you are on takes you to its top
      window.dispatchEvent(new HashChangeEvent('hashchange'));
      return;
    }
    location.hash = hash;
  }
  /** Change the address without a new history entry, keeping the entry's scroll key. */
  function replace(hash) {
    try { history.replaceState(history.state, '', hash); } catch (e) { /* ignore */ }
  }

  // ---------- the phone bottom sheet ----------
  /* sheet(panel, { onClose, scrim, desktop }) attaches sheet behaviour to an overlay that already exists:
     on phones the panel gets .gs-sheet (styles in ui.css) and a grab bar; dragging the bar or the panel's
     top down by more than 90 px (or flicking) closes it; the page is held still while it is open.
     On a desktop it only holds the page still (desktop: 'menu' for a dropdown: not even that).
     Returns detach(). The caller still shows, hides and fills its panel. */
  var sheetSeq = 0;
  function sheet(panel, opts) {
    opts = opts || {};
    var id = 'sheet' + (++sheetSeq);
    var phone = isPhone();
    if (!phone && opts.desktop === 'menu') return function () {};   // a dropdown on a desktop: nothing to hold
    lock(id, true);
    if (!phone) return function () { lock(id, false); };
    panel.classList.add('gs-sheet');
    // A grab bar the panel draws itself (class gs-sheet__grab) survives the panel redrawing its contents; otherwise add one.
    var made = null;
    if (!panel.querySelector('.gs-sheet__grab')) { made = el('div', { class: 'gs-sheet__grab', 'aria-hidden': 'true' }); panel.insertBefore(made, panel.firstChild); }
    var startY = null, startT = 0, dy = 0;
    function down(e) {
      // Only from the grab bar, or from the panel's top while it is scrolled to its top.
      var fromGrab = !!(e.target.closest && e.target.closest('.gs-sheet__grab'));
      var r = panel.getBoundingClientRect();
      if (!fromGrab && (panel.scrollTop > 0 || e.clientY - r.top > 64)) return;
      if (e.target.closest && e.target.closest('input, textarea, select, [contenteditable="true"]')) return;
      startY = e.clientY; startT = Date.now(); dy = 0;
      panel.style.transition = 'none';
    }
    function move(e) {
      if (startY == null) return;
      dy = Math.max(0, e.clientY - startY);
      if (dy > 4) panel.style.transform = 'translateY(' + dy + 'px)';
    }
    function up() {
      if (startY == null) return;
      var fast = dy > 40 && Date.now() - startT < 250;
      startY = null;
      panel.style.transition = '';
      if (dy > 90 || fast) {
        panel.style.transform = 'translateY(100%)';
        setTimeout(function () { panel.style.transform = ''; if (opts.onClose) opts.onClose(); }, reduced() ? 0 : 180);
      } else panel.style.transform = '';
    }
    panel.addEventListener('pointerdown', down);
    window.addEventListener('pointermove', move, { passive: true });
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
    var scrim = opts.scrim || null;
    function scrimClick(e) { if (e.target === scrim && opts.onClose) opts.onClose(); }
    if (scrim) { scrim.classList.add('gs-sheet-scrim'); scrim.addEventListener('click', scrimClick); }
    return function detach() {
      lock(id, false);
      panel.classList.remove('gs-sheet');
      panel.style.transform = ''; panel.style.transition = '';
      panel.removeEventListener('pointerdown', down);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      if (made && made.parentNode) made.remove();
      if (scrim) { scrim.classList.remove('gs-sheet-scrim'); scrim.removeEventListener('click', scrimClick); }
    };
  }

  // ---------- strips that scroll sideways ----------
  /* Shows a fade at the edge where more is hidden, and scrolls the selected item into view.
     strip(box, '[aria-current="page"]') after filling it. */
  function strip(box, selectedSel) {
    if (!box) return;
    box.classList.add('gs-strip');
    function edges() {
      var max = box.scrollWidth - box.clientWidth;
      box.classList.toggle('is-more-right', max > 2 && box.scrollLeft < max - 2);
      box.classList.toggle('is-more-left', box.scrollLeft > 2);
    }
    if (!box._gsStrip) { box._gsStrip = true; box.addEventListener('scroll', edges, { passive: true }); window.addEventListener('resize', edges); }
    var sel = selectedSel ? box.querySelector(selectedSel) : null;
    if (sel) {
      var b = box.getBoundingClientRect(), s = sel.getBoundingClientRect();
      if (s.left < b.left + 8 || s.right > b.right - 8) box.scrollLeft += (s.left - b.left) - (b.width - s.width) / 2;
    }
    requestAnimationFrame(edges);
  }

  window.gsUI = {
    PHONE: PHONE, phoneQuery: phoneQuery, isPhone: isPhone, reduced: reduced,
    el: el, elc: elc, add: add, api: api,
    ago: ago, when: when, date: date, duration: duration,
    toast: toast, lock: lock,
    go: go, replace: replace, scroll: { enter: enter, restore: restore, top: top },
    sheet: sheet, strip: strip
  };
})();
