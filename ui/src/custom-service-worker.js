/**
 * Custom service worker for Printers app.
 * Forces immediate activation on install so new versions take effect right away.
 */

// Skip waiting phase — activate immediately when a new SW is installed
self.addEventListener('install', () => self.skipWaiting());

// Take control of all clients immediately upon activation
self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// Import Angular's ngsw-worker for full caching support
importScripts("./ngsw-worker.js");
