// Auris service worker: offline shell and offline listening.
// Generated audio (/api/audio/<cache key>) is content-addressed, so a stored
// response never goes stale; everything else prefers the network.
const VERSION = 'auris-v2';
const SHELL = `${VERSION}-shell`;
const AUDIO = `${VERSION}-audio`;
const AUDIO_LIMIT = 800;
const SHELL_FILES = [
  '/static/css/style.css', '/static/css/tokens.css', '/static/css/experience.css',
  '/static/css/reader-experience.css', '/static/js/common.js', '/static/js/icons.js',
  '/static/js/listening.js', '/static/js/reader.js', '/static/js/reader-tools.js',
  '/static/js/reader-export.js', '/static/js/reader-init.js', '/static/favicon.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((key) => !key.startsWith(VERSION)).map((key) => caches.delete(key)),
  )).then(() => self.clients.claim()));
});

async function trimAudio() {
  const cache = await caches.open(AUDIO);
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - AUDIO_LIMIT; i += 1) await cache.delete(keys[i]);
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname === '/api/events') return;

  if (url.pathname.startsWith('/api/audio/') && !request.headers.has('range')) {
    event.respondWith(caches.open(AUDIO).then(async (cache) => {
      const hit = await cache.match(request);
      if (hit) return hit;
      const response = await fetch(request);
      if (response.ok && response.status === 200) {
        cache.put(request, response.clone()).then(trimAudio);
      }
      return response;
    }));
    return;
  }

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(fetch(request).then((response) => {
      if (response.ok) caches.open(SHELL).then((cache) => cache.put(request, response.clone()));
      return response;
    }).catch(() => caches.match(request)));
    return;
  }

  // Other API calls go straight to the network (never answered from cache).
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).then((response) => {
      if (response.ok && request.mode === 'navigate') {
        const copy = response.clone();
        caches.open(SHELL).then((cache) => cache.put(request, copy));
      }
      return response;
    }).catch(async () => (await caches.match(request)) || new Response(
      JSON.stringify({error: 'Nincs kapcsolat az Auris szerverrel.'}),
      {status: 503, headers: {'Content-Type': 'application/json'}},
    )));
  }
});
