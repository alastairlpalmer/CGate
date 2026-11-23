/* A tap shows at once (docs/MOBILE_APP_PLAN.md, item B2).
 *
 * A tap on a link sends a request, and the page changes when the answer
 * is back. On a weak signal that is a second or more with no sign that
 * the tap was seen. So:
 *
 *   - A tap on a tab, a More-sheet item or a sidebar link marks that
 *     section as the current one at once. If the request fails, the mark
 *     goes back.
 *   - If the answer takes longer than 150 ms, the page content dims until
 *     the new content is in. A quick answer shows no dim at all.
 *   - A row or a button under a finger shows as pressed (:active styles).
 *
 * The mark for "a page is on its way" is a class on <html>, not on the
 * content: htmx keeps a copy of the page body for Back, and a dimmed copy
 * would come back dimmed.
 */
(function (root) {
    'use strict';

    var DIM_AFTER_MS = 150;
    var GIVE_UP_MS = 25000;     // longer than the limit for a request that reads
    var NAV_LINKS = '#mobile-tab-bar a[href], #more-sheet a[href], aside nav a[href]';

    /**
     * The path a tap on a link in the bars goes to, or '' when the tap
     * does not change the section: not a link in the bars, another site,
     * or a link that opens the pop-up sheet. Unit-tested.
     */
    function sectionPath(link, origin) {
        if (!link || !link.getAttribute) { return ''; }
        var href = link.getAttribute('href');
        if (!href || href.charAt(0) === '#') { return ''; }
        if (link.getAttribute('hx-get') || link.getAttribute('hx-post')) { return ''; }
        try {
            var url = new URL(href, origin);
            return url.origin === new URL(origin).origin ? url.pathname : '';
        } catch (err) {
            return '';
        }
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { sectionPath: sectionPath, DIM_AFTER_MS: DIM_AFTER_MS };
    }
    if (!root.document) { return; }

    var doc = root.document;
    var html = doc.documentElement;
    var dimTimer = null;
    var giveUpTimer = null;

    // iOS shows :active styles only when the page has a touch listener.
    doc.addEventListener('touchstart', function () {}, { passive: true });

    // page-leaving: a request for a page is in flight. page-pending: it has
    // taken long enough to show (the dim).
    function settle() {
        root.clearTimeout(dimTimer);
        root.clearTimeout(giveUpTimer);
        html.classList.remove('page-leaving', 'page-pending');
    }

    doc.addEventListener('htmx:beforeRequest', function (e) {
        var detail = e.detail || {};
        if (!detail.boosted) { return; }
        var link = detail.elt && detail.elt.closest ? detail.elt.closest(NAV_LINKS) : null;
        var path = sectionPath(link, root.location.href);
        if (path && root.Yardway && root.Yardway.syncNav) {
            root.Yardway.syncNav(path);
            // The More sheet closes on this same tap, and its own code
            // then takes the mark off the More tab: set it again after.
            root.setTimeout(function () {
                if (html.classList.contains('page-leaving')) { root.Yardway.syncNav(path); }
            }, 0);
        }
        settle();
        html.classList.add('page-leaving');
        dimTimer = root.setTimeout(function () { html.classList.add('page-pending'); }, DIM_AFTER_MS);
        giveUpTimer = root.setTimeout(settle, GIVE_UP_MS);
    });

    // The answer is here. With a view transition the content goes in a
    // frame or two later; a dim that started in that gap would be a flash
    // on a fast connection. A dim that is on stays until the content is in.
    doc.addEventListener('htmx:beforeSwap', function (e) {
        if (e.detail && e.detail.boosted) { root.clearTimeout(dimTimer); }
    });
    // The new content is in.
    doc.addEventListener('htmx:afterSwap', function (e) {
        if (e.detail && e.detail.boosted) { settle(); }
    });
    // No new content came: put the marks back as the address says.
    doc.addEventListener('htmx:afterRequest', function (e) {
        var detail = e.detail || {};
        if (!detail.boosted || detail.successful) { return; }
        settle();
        if (root.Yardway && root.Yardway.syncNav) { root.Yardway.syncNav(); }
    });
    doc.addEventListener('htmx:historyRestore', settle);
    root.addEventListener('pageshow', settle);
})(typeof window !== 'undefined' ? window : globalThis);
