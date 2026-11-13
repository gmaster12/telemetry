/* Settings, Dashboard Cards: which admin cards show. Saved per admin account on the server
   (GET/PUT /api/settings/cards; /api/me carries `cards_hidden` so the page is right on first paint).
   Nothing saved means every card shows. A hidden card's menu place and phone tab go too (app.js asks off()). */
(function () {
  'use strict';
  var root = document.getElementById('streams-app');
  var KEYS = ['summary', 'bandwidth', 'servers', 'viewing', 'globe', 'capacity', 'streams', 'users', 'alerts'];
  var hidden = null;  // null until /api/me has answered

  function mine() {
    if (hidden) return hidden;
    var me = window.dash && window.dash.me && window.dash.me();
    return me && me.role === 'admin' ? (me.cards_hidden || []) : [];
  }
  function apply() {
    var h = mine();
    KEYS.forEach(function (k) { root.classList.toggle('gs-hide-' + k, h.indexOf(k) >= 0); });
    // When people watch and the globe share a row: one of them alone takes the whole row.
    var both = !root.classList.contains('gs-hide-viewing') && !root.classList.contains('gs-hide-globe') && !!(window.dash && window.dash.isAdmin && window.dash.isAdmin());
    root.classList.toggle('gs-insights-single', !both);
  }
  window.gsCards = {
    keys: KEYS,
    off: function (k) { return mine().indexOf(k) >= 0; },
    hidden: function () { return mine().slice(); },
    /** Save a new list (Settings) and redraw the dashboard. */
    save: function (list) {
      return window.gsUI.api('PUT', '/api/settings/cards', { hidden: list }).then(function (j) {
        hidden = j.hidden || [];
        apply();
        if (window.sdNavChanged) window.sdNavChanged();
        if (window.gsGlobeCard) window.gsGlobeCard.refresh();
        return j;
      });
    },
    load: function () { return window.gsUI.api('GET', '/api/settings/cards').then(function (j) { hidden = j.hidden || []; apply(); return j; }); },
    apply: apply
  };
  if (window.dash && window.dash.onData) window.dash.onData(apply);
})();
