/* The installed app (docs/MOBILE_APP_PLAN.md, phase 4).
 *
 * 1. Registers the service worker (item D4). Its address is in
 *    <meta name="app-worker">; an empty address means it is switched off
 *    (settings.APP_WORKER_ENABLED). The worker keeps the styles, scripts,
 *    fonts and icons on the phone and shows a page for no signal
 *    (static/js/app_worker.js).
 *
 * 2. Helps a phone to install the app (item D2). The dashboard holds a
 *    card, [data-install-card], with two parts:
 *      [data-install-part="button"]  Android: the browser can install the
 *                                    app, so the card has one button.
 *      [data-install-part="steps"]   iPhone and iPad: the person must use
 *                                    Share, then "Add to Home Screen".
 *    The card shows in a phone's browser until the person installs the
 *    app or closes the card. It never shows in the installed app. The
 *    same steps are in Help, for someone who closed it.
 *
 * 3. Says "app installed" to the usage figures (item D7), with no data
 *    about the person.
 */
(function (root) {
    'use strict';

    var KEY = 'yardway:install-card';

    // Is this the app opened from its icon, and not a browser tab?
    function isInstalled(win) {
        var match = win.matchMedia ? win.matchMedia('(display-mode: standalone)').matches : false;
        return !!match || (win.navigator && win.navigator.standalone === true);
    }

    // 'ios', 'android' or 'other'. An iPad says it is a Mac; a Mac has no
    // touch screen.
    function platform(nav) {
        var ua = (nav && nav.userAgent) || '';
        if (/iphone|ipad|ipod/i.test(ua)) return 'ios';
        if (nav && nav.platform === 'MacIntel' && nav.maxTouchPoints > 1) return 'ios';
        if (/android/i.test(ua)) return 'android';
        return 'other';
    }

    // Which part of the card to show: 'button', 'steps' or 'none'.
    //   state.installed   the app is open from its icon
    //   state.closed      the person closed the card, or installed the app
    //   state.touch       a phone or a tablet (a finger, not a mouse)
    //   state.canPrompt   the browser offered to install the app
    //   state.platform    from platform()
    function cardPart(state) {
        if (state.installed || state.closed || !state.touch) return 'none';
        if (state.canPrompt) return 'button';
        if (state.platform === 'ios') return 'steps';
        return 'none';
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { isInstalled: isInstalled, platform: platform, cardPart: cardPart, KEY: KEY };
        return;
    }

    var doc = root.document;
    var offer = null;  // the browser's offer to install (Android)

    function closed() {
        try { return root.localStorage.getItem(KEY) === 'closed'; } catch (err) { return false; }
    }

    function remember() {
        try { root.localStorage.setItem(KEY, 'closed'); } catch (err) { /* private mode: it shows again */ }
    }

    function track(name, properties) {
        if (root.Yardway && root.Yardway.track) root.Yardway.track(name, properties);
    }

    function show() {
        var card = doc.querySelector('[data-install-card]');
        if (!card) return;
        var part = cardPart({
            installed: isInstalled(root),
            closed: closed(),
            touch: !!(root.matchMedia && root.matchMedia('(pointer: coarse)').matches),
            canPrompt: !!offer,
            platform: platform(root.navigator),
        });
        var parts = card.querySelectorAll('[data-install-part]');
        for (var i = 0; i < parts.length; i++) {
            parts[i].hidden = parts[i].getAttribute('data-install-part') !== part;
        }
        card.hidden = part === 'none';
    }

    root.addEventListener('beforeinstallprompt', function (event) {
        // The app's own card asks, in its own words and at its own time.
        event.preventDefault();
        offer = event;
        show();
    });

    root.addEventListener('appinstalled', function () {
        offer = null;
        remember();
        show();
        track('app installed', { platform: platform(root.navigator) });
    });

    doc.addEventListener('click', function (event) {
        var target = event.target && event.target.closest ? event.target : null;
        if (!target) return;
        if (target.closest('[data-install-close]')) {
            remember();
            show();
            return;
        }
        if (target.closest('[data-install-now]') && offer) {
            var asked = offer;
            offer = null;  // the browser lets an offer be used one time
            asked.prompt();
            // Yes or no, the person has answered: the card has done its job.
            remember();
            show();
        }
    });

    doc.addEventListener('DOMContentLoaded', show);
    doc.addEventListener('htmx:afterSettle', show);
    doc.addEventListener('htmx:historyRestore', show);

    // ── The service worker ──
    root.addEventListener('load', function () {
        var meta = doc.querySelector('meta[name="app-worker"]');
        var url = meta ? meta.getAttribute('content') : '';
        if (!url || !root.navigator || !('serviceWorker' in root.navigator)) return;
        root.navigator.serviceWorker.register(url, { scope: '/' }).catch(function () {
            // No worker is the app as it was before: nothing to tell the person.
        });
    });
})(typeof window !== 'undefined' ? window : this);
