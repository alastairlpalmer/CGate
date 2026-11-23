/* The main app's service worker (docs/MOBILE_APP_PLAN.md, item D4).
 *
 * core.views.app_shell.service_worker serves this file at /sw.js, with one
 * line in front of it:
 *
 *   self.APP_SHELL = { version, offline, needed, wanted, staticPrefix, skip };
 *
 * What it keeps on the phone: the styles, the scripts, the fonts, the
 * icons, and one page that says "No signal". With those an installed app
 * starts without a download, and a page that cannot load shows the app's
 * own "No signal" page, not the browser's error.
 *
 * What it never keeps: a page of the app, or an answer from it. Nothing
 * with a horse, an owner or money in it is stored here, so there is
 * nothing to delete at sign-out. A page always comes from the network.
 *
 * C-vet has a worker of its own for /c-vet/ (templates/cvet/sw.js). A
 * browser gives a page to the worker with the longest matching scope, so
 * the two do not meet. `skip` keeps this one out of those addresses all
 * the same.
 *
 * The version changes with each deploy. That changes this file, so the
 * browser installs it again; the new one stores fresh copies and deletes
 * the old store.
 */
(function () {
    'use strict';

    var PREFIX = 'app-shell-';

    function startsWithAny(path, prefixes) {
        for (var i = 0; i < (prefixes || []).length; i++) {
            if (path.indexOf(prefixes[i]) === 0) return true;
        }
        return false;
    }

    // What to do with one request:
    //   'network'  leave it to the browser, as if there were no worker
    //   'kept'     the stored copy first; if there is none, fetch and store
    //   'page'     the network; with no signal, the "No signal" page
    // `request` needs method, url and mode. `origin` is the worker's own.
    function plan(request, config, origin) {
        if (request.method !== 'GET') return 'network';
        var url = new URL(request.url);
        if (url.origin !== origin) return 'network';
        if (startsWithAny(url.pathname, config.skip)) return 'network';
        if (url.pathname.indexOf(config.staticPrefix) === 0) {
            var address = url.pathname + url.search;
            // A file of this version cannot change under its address. A
            // file with another version, or with none, is not ours to keep.
            if (url.searchParams.get('v') === config.version) return 'kept';
            if (config.needed.indexOf(address) !== -1 || config.wanted.indexOf(address) !== -1) return 'kept';
            return 'network';
        }
        // A tap inside the app is not a navigation: htmx asks for it, and
        // js/connection.js says "No signal" in the page.
        if (request.mode === 'navigate') return 'page';
        return 'network';
    }

    function cacheName(config) { return PREFIX + config.version; }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { plan: plan, cacheName: cacheName, PREFIX: PREFIX };
        return;
    }

    var CONFIG = self.APP_SHELL;
    var CACHE = cacheName(CONFIG);

    // Store one address. A redirect (to the sign-in page) or an error is
    // never stored: it would then show in place of the file.
    function store(cache, url) {
        return fetch(url, { credentials: 'same-origin', cache: 'reload' }).then(function (response) {
            if (!response.ok || response.redirected) throw new Error('not stored: ' + url);
            return cache.put(url, response);
        });
    }

    self.addEventListener('install', function (event) {
        event.waitUntil(caches.open(CACHE).then(function (cache) {
            // The "No signal" page and what it needs: all of them, or the
            // install fails and the browser keeps the worker it had.
            return Promise.all(CONFIG.needed.map(function (url) { return store(cache, url); })).then(function () {
                // The scripts are stored as they are used too, so one
                // that fails here does not stop the install.
                return Promise.all(CONFIG.wanted.map(function (url) {
                    return store(cache, url).catch(function () { /* stored on first use */ });
                }));
            });
        }).then(function () { return self.skipWaiting(); }));
    });

    self.addEventListener('activate', function (event) {
        event.waitUntil(caches.keys().then(function (names) {
            return Promise.all(names.filter(function (name) {
                return name.indexOf(PREFIX) === 0 && name !== CACHE;
            }).map(function (name) { return caches.delete(name); }));
        }).then(function () {
            // The browser starts to load a page while this worker wakes
            // up, so the worker adds no wait to a page load.
            if (self.registration.navigationPreload) return self.registration.navigationPreload.enable();
        }).then(function () { return self.clients.claim(); }));
    });

    function kept(request) {
        return caches.open(CACHE).then(function (cache) {
            return cache.match(request).then(function (stored) {
                if (stored) return stored;
                return fetch(request).then(function (response) {
                    if (response.ok && response.type === 'basic') cache.put(request, response.clone());
                    return response;
                });
            });
        });
    }

    function page(event) {
        return Promise.resolve(event.preloadResponse).then(function (preloaded) {
            return preloaded || fetch(event.request);
        }).catch(function (error) {
            return caches.open(CACHE).then(function (cache) {
                return cache.match(CONFIG.offline);
            }).then(function (offline) {
                if (offline) return offline;
                throw error;
            });
        });
    }

    self.addEventListener('fetch', function (event) {
        var what = plan(event.request, CONFIG, self.location.origin);
        if (what === 'kept') event.respondWith(kept(event.request));
        else if (what === 'page') event.respondWith(page(event));
    });
})();
