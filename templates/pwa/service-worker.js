const CACHE_NAME = 'connect-admin-cache-v{{ pwa_version }}';
const OFFLINE_URL = '/offline/';

const PRECACHE_ASSETS = [
  OFFLINE_URL,
  '/static/admin_ops/admin_ops.css',
  '/static/admin_ops/admin_ops.js',
  '/static/pwa/css/pwa.css',
  '/static/pwa/js/pwa-init.js',
  '/static/pwa/icons/icon-192x192.png',
  '/static/pwa/icons/icon-512x512.png',
  '/static/pwa/icons/maskable-icon-512x512.png',
  '/static/unfold/fonts/inter/styles.css',
  '/static/unfold/fonts/material-symbols/styles.css',
  '/static/unfold/css/styles.css',
  '/static/unfold/js/app.js',
];

// Install Event
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[Service Worker] Pre-caching offline fallback and assets');
      return cache.addAll(PRECACHE_ASSETS);
    }).then(() => self.skipWaiting())
  );
});

// Activate Event (Cleanup old caches)
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cache) => {
          if (cache !== CACHE_NAME) {
            console.log('[Service Worker] Clearing old cache', cache);
            return caches.delete(cache);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch Event
self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);

  // Exclude non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // 1. Navigation requests (HTML pages) -> Network-First, fallback to Offline Page
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .catch(() => {
          console.log('[Service Worker] Navigation failed, serving offline page');
          return caches.match(OFFLINE_URL);
        })
    );
    return;
  }

  // 2. Static Assets (CSS, JS, Fonts, Images under /static/) -> Stale-While-Revalidate
  if (url.pathname.includes('/static/')) {
    event.respondWith(
      caches.match(request).then((cachedResponse) => {
        const fetchPromise = fetch(request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const responseToCache = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, responseToCache);
            });
          }
          return networkResponse;
        }).catch(() => {
          // Silent catch for network failure in background revalidation
        });
        return cachedResponse || fetchPromise;
      })
    );
    return;
  }

  // 3. API calls and admin routes -> Network-First (never cache sensitive data)
  event.respondWith(
    fetch(request).catch(() => {
      return caches.match(request).then((cachedResponse) => {
        if (cachedResponse) return cachedResponse;
        if (url.pathname.includes('/api/')) {
          return new Response(JSON.stringify({
            status: 'error',
            message: 'You are offline. Please check your network connection.'
          }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' }
          });
        }
      });
    })
  );
});
