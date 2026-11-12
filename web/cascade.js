/* Greystar Telemetry: "Cascade in" page changes (Glenn's pick 6 of 8, Nov 12 2026).

   When the person moves to another page (a phone tab, Requests and its sections, Books & comics, the viewer home's
   views, the cleanup board), the page title and then each card of the new page float up into place one after
   another: 26 px lower, 96% size and see-through to their place, 520 ms each, 60 ms apart, on a soft ease-out.
   Desktop dashboard jumps (the menu scrolling to a card on the same page) are not page changes and do not cascade.

   It works on whatever the page draws, so the pages need no changes: in the main area (#content) it takes the visible
   blocks top to bottom, stepping into a block that fills most of the screen and holds several parts (a grid, a list),
   and leaves out the top bar, the tab bar, sheets and anything already animating (the viewer home's own Sweep in, the
   page-load build). Pages that fill in after a fetch keep cascading for 700 ms as their cards arrive.

   Only transform and opacity move (Web Animations, fill: backwards), so phones run it on the graphics chip and nothing
   is left behind when it ends. Nothing moves for Reduce Motion. window.gsCascade.run() plays it on demand. */
(function () {
  'use strict';
  var DUR = 520, FIRST = 60, STEP = 60, MAX = 12, WATCH_MS = 700;
  var EASE = 'cubic-bezier(.16,1,.3,1)';
  var FRAMES = [{ opacity: 0, transform: 'translateY(26px) scale(.96)' }, { opacity: 1, transform: 'none' }];
  var SKIP = '.sd-top, .sd-tabbar, .sd-settings-dock, .gs-sheet, .us-scrim, .sd-dltray, .np, .rq-report, script, style, [hidden]';
  var root = document.getElementById('streams-app') || document.querySelector('.gs-app');
  var content = document.getElementById('content');
  var lastKey = null, watcher = null, count = 0, startedAt = 0, done = null;

  function still() { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); }
  function phone() { return !!(window.gsUI && window.gsUI.isPhone()); }

  /** What page is on screen. A change of this is a page change. */
  function key() {
    if (!root) return '';
    var rq = document.getElementById('requests-page');
    var sect = rq && !rq.hidden ? (rq.dataset.sect || '') + ':' + location.hash.split('?')[0] : '';
    return [root.dataset.page || 'dash', phone() ? root.dataset.mtab || '' : '', root.dataset.vview || '', sect].join('|');
  }

  function visible(e) {
    if (!(e instanceof HTMLElement) || e.matches(SKIP)) return false;
    var r = e.getBoundingClientRect();
    if (r.width < 24 || r.height < 16) return false;
    return r.bottom > 0 && r.top < window.innerHeight;   // on screen now; the rest needs no show
  }
  function busy(e) {
    if (!e.getAnimations) return false;
    return e.getAnimations({ subtree: true }).some(function (a) { return a.playState === 'running' || a.pending; });
  }

  /** The blocks to cascade under `node`, top to bottom. */
  function blocks(node, depth, out) {
    Array.prototype.forEach.call(node.children, function (c) {
      if (out.length >= MAX || !visible(c)) return;
      var kids = Array.prototype.filter.call(c.children, visible);
      var tall = c.getBoundingClientRect().height > window.innerHeight * 0.6;
      if (depth < 4 && kids.length > 1 && tall) blocks(c, depth + 1, out);
      else if (kids.length === 1 && depth < 4 && !c.matches('.sd-card, .rq-card, .vh-door, article, li, a, button')) blocks(c, depth + 1, out);
      else out.push(c);
    });
    return out;
  }

  function animate(list) {
    list.forEach(function (e) {
      if (e.dataset.gsCascaded === String(startedAt) || busy(e)) return;
      e.dataset.gsCascaded = String(startedAt);
      // Cards that arrive after a fetch keep their turn in the order, counted from when the page changed.
      var delay = Math.max(0, FIRST + Math.min(count++, MAX) * STEP - (Date.now() - startedAt));
      try { e.animate(FRAMES, { duration: DUR, delay: delay, easing: EASE, fill: 'backwards' }); }
      catch (err) { /* very old browser: no animation */ }
    });
  }

  function run() {
    if (!content || still()) return;
    if (watcher) { watcher.disconnect(); watcher = null; }
    clearTimeout(done);
    count = 0; startedAt = Date.now();
    animate(blocks(content, 0, []));
    if (!window.MutationObserver) return;
    var w = watcher = new MutationObserver(function (list) {
      var added = [];
      list.forEach(function (m) {
        Array.prototype.forEach.call(m.addedNodes, function (n) {
          if (n.nodeType !== 1 || !visible(n) || (n.closest && n.closest(SKIP))) return;
          var tall = n.getBoundingClientRect().height > window.innerHeight * 0.6;
          if (tall && n.children.length > 1) blocks(n, 1, added); else added.push(n);
        });
      });
      animate(added.slice(0, MAX));
    });
    w.observe(content, { childList: true, subtree: true });
    done = setTimeout(function () { w.disconnect(); if (watcher === w) watcher = null; }, WATCH_MS);
  }

  function check() {
    var k = key();
    if (lastKey !== null && k !== lastKey) run();
    lastKey = k;
  }

  // Each page's own hashchange handler draws it first; then this looks at what changed.
  window.addEventListener('hashchange', function () { requestAnimationFrame(check); });
  document.addEventListener('gs:ready', function () { lastKey = key(); });
  // The viewer home's doors switch views without a new address (gsHome.show): watch its view marker too.
  if (root && window.MutationObserver) {
    new MutationObserver(function () { requestAnimationFrame(check); })
      .observe(root, { attributes: true, attributeFilter: ['data-vview', 'data-mtab', 'data-page'] });
  }

  window.gsCascade = { run: run };
})();
