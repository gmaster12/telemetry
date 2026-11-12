/* Greystar Telemetry: the phone tab bar fades away while the person scrolls and comes back when they stop
   (Glenn's pick A, Nov 12 2026). Phones only (the gsUI phone rule); desktops have the side menu.

   Any scroll counts, the window or a list inside the page (a capturing listener), and the Books frame says
   when the media site is scrolled (books.js calls gsTabFade.ping on its `scrolling` message). The bar comes back
   450 ms after the last scroll. It stays put while a sheet holds the page still (html.gs-locked), while a tab
   has keyboard focus, and for a moment after a page change (a jump to the top is not the person scrolling).
   The look is in downloads.css (html.gs-scrolling .sd-tabbar). */
(function () {
  'use strict';
  var IDLE_MS = 450, QUIET_MS = 800;
  var root = document.documentElement;
  var timer = null, quietUntil = 0;

  function show() { root.classList.remove('gs-scrolling'); }

  function ping() {
    if (!window.gsUI || !window.gsUI.isPhone()) { show(); return; }
    if (Date.now() < quietUntil || root.classList.contains('gs-locked')) return;
    var bar = document.getElementById('tabbar');
    if (bar && bar.contains(document.activeElement)) return;
    root.classList.add('gs-scrolling');
    clearTimeout(timer);
    timer = setTimeout(show, IDLE_MS);
  }

  document.addEventListener('scroll', function (e) {
    // A sheet or a dropdown scrolling inside itself is not the page moving under the bar.
    var t = e.target;
    if (t && t.closest && t.closest('.gs-sheet, .sd-dltray, .us-menu')) return;
    ping();
  }, { capture: true, passive: true });
  window.addEventListener('hashchange', function () { quietUntil = Date.now() + QUIET_MS; clearTimeout(timer); show(); });
  document.addEventListener('focusin', function (e) { var bar = document.getElementById('tabbar'); if (bar && bar.contains(e.target)) show(); });

  window.gsTabFade = { ping: ping };
})();
