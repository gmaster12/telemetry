/* Greystar Telemetry alerts service worker (app/push.py sends; web/push.js turns it on).
   Only shows pushed alerts and opens the site when one is tapped. It does not cache or touch any page loads. */
'use strict';

self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (e) {
  var m = {};
  try { m = e.data ? e.data.json() : {}; } catch (x) { m = { title: 'Greystar', body: e.data ? e.data.text() : '' }; }
  var title = m.title || 'Greystar';
  e.waitUntil(self.registration.showNotification(title, {
    body: m.body || '',
    icon: '/assets/icon-192.png',
    badge: '/assets/favicon-32.png',
    tag: m.tag || undefined,
    renotify: !!m.tag,
    data: { url: m.url || '/' }
  }));
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if (new URL(c.url).origin === self.location.origin && 'focus' in c) {
        return c.focus().then(function (w) { return w && 'navigate' in w ? w.navigate(url) : w; });
      }
    }
    return self.clients.openWindow(url);
  }));
});

// A browser that renews the subscription by itself: hand the new one to the site, which keeps the old device's owner.
self.addEventListener('pushsubscriptionchange', function (e) {
  e.waitUntil(self.registration.pushManager.getSubscription().then(function (sub) {
    if (!sub) return null;
    var j = sub.toJSON();
    return fetch('/api/push/subscribe', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'greystar' },
      body: JSON.stringify({ endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth, device: 'Renewed by the browser' }) });
  }).catch(function () {}));
});
