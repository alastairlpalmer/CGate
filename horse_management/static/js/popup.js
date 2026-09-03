/* Shared pop-up sheet (templates/includes/_popup_sheet.html).
 *
 * A trigger is any element with `data-popup-title` that issues an htmx
 * request into #popup-body. This script:
 *   - opens the sheet (with a skeleton) when such a request starts,
 *   - moves focus in on load and back to the trigger on close,
 *   - asks before discarding unsaved edits,
 *   - closes and refreshes #main-content in place when a form answers
 *     204 + `HX-Trigger: popup:saved`, so the page shows the saved data
 *     and the Django message toast without a navigation.
 */
(function () {
    'use strict';

    document.addEventListener('alpine:init', function () {
        Alpine.store('popup', {
            open: false,
            title: '',
            dirty: false,
            opener: null,   // the trigger element, for focus return + abort
            href: '',       // full-page fallback URL, shown if the load fails

            show: function (title, opener) {
                this.title = title || '';
                this.opener = opener || null;
                this.href = (opener && opener.getAttribute('href')) || '';
                this.dirty = false;
                this.open = true;
                document.documentElement.classList.add('overflow-hidden');
                // The panel is display:none until Alpine applies x-show
                Alpine.nextTick(function () {
                    var panel = document.getElementById('popup-panel');
                    if (panel) { panel.focus({ preventScroll: true }); }
                });
            },

            // force=true skips the unsaved-changes check (used after a save).
            close: function (force) {
                if (!this.open) { return; }
                if (this.dirty && !force &&
                    !window.confirm('Discard your changes?')) { return; }
                var opener = this.opener;
                if (opener && window.htmx) { htmx.trigger(opener, 'htmx:abort'); }
                this.open = false;
                this.dirty = false;
                this.opener = null;
                document.documentElement.classList.remove('overflow-hidden');
                var body = document.getElementById('popup-body');
                if (body) { body.innerHTML = ''; }
                if (opener && document.body.contains(opener)) {
                    opener.focus({ preventScroll: true });
                }
            }
        });
    });

    function store() { return window.Alpine ? Alpine.store('popup') : null; }
    function popupBody() { return document.getElementById('popup-body'); }

    function isPopupTarget(target) {
        return target && target.id === 'popup-body';
    }

    function showSkeleton() {
        var body = popupBody();
        var tpl = document.getElementById('popup-skeleton');
        if (!body) { return; }
        body.innerHTML = '';
        if (tpl) { body.appendChild(tpl.content.cloneNode(true)); }
    }

    function showLoadError() {
        var body = popupBody();
        var s = store();
        if (!body) { return; }
        var link = s && s.href
            ? ' <a href="' + s.href + '" class="link">Open the full page instead.</a>'
            : '';
        body.innerHTML =
            '<div class="p-5 text-sm text-charcoal-light" role="alert">' +
            'That didn’t load. Check your signal and try again.' + link + '</div>';
    }

    // A boosted navigation (e.g. the "full edit page" link inside a form)
    // has left the page: close the sheet once the new page has landed.
    // (Closing on request start would remove the link mid-request.)
    document.body.addEventListener('htmx:afterSwap', function (e) {
        if (!e.detail.boosted) { return; }
        var s = store();
        if (s && s.open) { s.close(true); }
    });

    // Open on request start.
    document.body.addEventListener('htmx:beforeRequest', function (e) {
        var elt = e.detail.elt;
        if (!elt || !elt.dataset || elt.dataset.popupTitle === undefined) { return; }
        if (!isPopupTarget(e.detail.target)) { return; }
        var s = store();
        if (!s) { return; }
        showSkeleton();
        s.show(elt.dataset.popupTitle, elt);
    });

    // A form landed in the sheet. A fresh form (GET) starts clean; a form
    // re-rendered with errors after a POST still holds the user's edits,
    // so it stays dirty and closing it still asks first. Focus moves to
    // the first field on wide screens (on phones a text field would pop
    // the keyboard over the sheet, so the panel keeps focus).
    document.body.addEventListener('htmx:afterSwap', function (e) {
        if (!isPopupTarget(e.detail.target)) { return; }
        var s = store();
        var verb = e.detail.requestConfig && e.detail.requestConfig.verb;
        if (s) { s.dirty = !!verb && verb !== 'get'; }
        if (!window.matchMedia('(min-width: 640px)').matches) { return; }
        var first = e.detail.target.querySelector(
            'input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled])'
        );
        if (first) { first.focus({ preventScroll: true }); }
    });

    ['htmx:responseError', 'htmx:sendError', 'htmx:timeout'].forEach(function (name) {
        document.body.addEventListener(name, function (e) {
            if (!isPopupTarget(e.detail.target)) { return; }
            showLoadError();
        });
    });

    // Any edit inside the sheet marks it dirty.
    ['input', 'change'].forEach(function (name) {
        document.body.addEventListener(name, function (e) {
            var s = store();
            if (s && s.open && e.target.closest && e.target.closest('#popup-body')) {
                s.dirty = true;
            }
        });
    });

    // Re-fetch the current page and swap #main-content in place. Scroll,
    // filters and the URL all stay put; toasts and Alpine state come back
    // through the same chrome hook boosted navigations use (base.html).
    function refreshMain() {
        return fetch(window.location.href, {
            credentials: 'same-origin',
            headers: { 'Accept': 'text/html' }
        }).then(function (resp) {
            return resp.text();
        }).then(function (html) {
            var doc = new DOMParser().parseFromString(html, 'text/html');
            var fresh = doc.getElementById('main-content');
            var live = document.getElementById('main-content');
            if (!fresh || !live) { window.location.reload(); return; }
            live.replaceWith(fresh);
            if (window.htmx) { htmx.process(fresh); }
            if (window.Yardway && Yardway.afterMainSwap) {
                Yardway.afterMainSwap(fresh, html);
            }
        }).catch(function () {
            window.location.reload();
        });
    }

    document.body.addEventListener('popup:saved', function () {
        var s = store();
        if (s) { s.close(true); }
        refreshMain();
    });
})();
