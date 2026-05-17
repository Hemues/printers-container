/**
 * Self-unregistering service worker for Printers app.
 * The previous SW cached stale content and prevented updates.
 * This version unregisters itself so the app loads fresh from the server.
 */

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(names =>
      Promise.all(names.map(name => caches.delete(name)))
    ).then(() => self.clients.claim())
    .then(() => self.registration.unregister())
    .then(() => self.clients.matchAll()).then(clients => {
      clients.forEach(client => client.navigate(client.url));
    })
  );
});
