{% autoescape off %}// C-vet: the service worker (docs/C_VET_PLAN.md, section 5).
//
// It keeps a copy of the page and its files on the phone, so the page
// opens with no signal. It never stores data: the horse list and the
// notes are in IndexedDB (static/cvet/app.js). It never touches the API.
//
// Django writes the four values below (cvet.views.service_worker). The
// version changes with each deploy, which makes the browser install this
// file again and store fresh copies.
const VERSION = {{ version }};
const SHELL = {{ shell_url }};
const ASSETS = {{ asset_urls }};
// For a wide screen, or for work that needs a signal. Stored when a
// page first asks for them.
const WIDE = {{ wide_urls }};

const PREFIX = 'cvet-';
const CACHE = PREFIX + VERSION;

// Store one address. A sign-in redirect or an error page is never stored:
// it would then open in place of the app with no signal.
async function store(cache, url) {
    const response = await fetch(url, { credentials: 'same-origin', cache: 'reload' });
    if (response.ok && !response.redirected) {
        await cache.put(url, response);
        return true;
    }
    return false;
}

self.addEventListener('install', (event) => {
    event.waitUntil((async () => {
        const cache = await caches.open(CACHE);
        const results = await Promise.all([SHELL].concat(ASSETS).map((url) => store(cache, url)));
        if (results.includes(false)) {
            // Fail the install: half an app is worse than the old one.
            throw new Error('C-vet: could not store every file');
        }
        await self.skipWaiting();
    })());
});

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        const names = await caches.keys();
        await Promise.all(names
            .filter((name) => name.startsWith(PREFIX) && name !== CACHE)
            .map((name) => caches.delete(name)));
        await self.clients.claim();
    })());
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') return;
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return;

    const isShell = request.mode === 'navigate' && url.pathname === SHELL;
    const isAsset = ASSETS.includes(url.pathname + url.search);
    const isWide = WIDE.includes(url.pathname + url.search);
    if (!isShell && !isAsset && !isWide) return;  // the API and every other page: the network, as normal

    event.respondWith((async () => {
        const cache = await caches.open(CACHE);
        // The stored copy first, with or without a signal: a weak signal
        // must not leave the vet looking at a blank page.
        const stored = await cache.match(isShell ? SHELL : request);
        if (stored) return stored;
        const response = await fetch(request);
        // Kept from now on, so a computer opens the app with no signal too.
        if (isWide && response.ok) await cache.put(request, response.clone());
        return response;
    })());
});
{% endautoescape %}
