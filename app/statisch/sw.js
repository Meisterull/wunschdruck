/* Minimaler Service Worker: macht die App installierbar (PWA + Teilen-Ziel).
   Bewusst kein Caching — die App lebt im LAN, Offline-Betrieb ergibt keinen Sinn. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(clients.claim()));
self.addEventListener('fetch', () => {});
