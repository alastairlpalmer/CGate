/* Shared pop-up sheet (templates/includes/_popup_sheet.html).
 *
 * A trigger is any element with `data-popup-title` that issues an htmx
 * request into #popup-body. This script:
 *   - opens the sheet (with a skeleton) when such a request starts,
 *   - moves focus in on load and back to the trigger on close,
 *   - asks before discarding unsaved edits,
 *   - closes and refreshes #main-content in place when a form answers
 *     204 + `HX-Trigger: popup:saved`, so the page shows the saved data
 *     and the Django message toast without a navigation,
 *   - pushes one history entry while open so the phone's Back button
 *     closes the sheet instead of leaving the page,
 *   - owns the `confirm` store (includes/_confirm_sheet.html): a styled
 *     Yes/No dialog used for every hx-confirm in the app and for the
 *     sheet's own "discard changes?" question.
 */
(function () {
    'use strict';

    // ── Confirm dialog ──
    // ask() resolves true/false. The action button's label is taken from
    // opts.label, else from the question's leading verb ("Delete …?" →
    // "Delete"); destructive verbs turn it red.
    var DANGER_VERBS = /^(delete|remove|archive|deactivate|disconnect|discard)\b/i;
    var VERBS = /^(delete|remove|archive|restore|deactivate|disconnect|discard|email|send|confirm|cancel|move|depart)\b/i;

    document.addEventListener('alpine:init', function () {
        Alpine.store('confirm', {
            open: false,
            text: '',
            label: 'Confirm',
            danger: false,
            _resolve: null,
            _returnTo: null,

            ask: function (opts) {
                var self = this;
                var text = (opts && opts.text) || 'Are you sure?';
                var verb = text.match(VERBS);
                this.text = text;
                this.label = (opts && opts.label) || (verb ? verb[0].charAt(0).toUpperCase() + verb[0].slice(1) : 'Confirm');
                this.danger = (opts && opts.danger !== undefined) ? !!opts.danger : DANGER_VERBS.test(text);
                this._returnTo = document.activeElement;
                this.open = true;
                Alpine.nextTick(function () {
                    var panel = document.getElementById('confirm-panel');
                    var cancel = panel && panel.querySelector('button');
                    (cancel || panel) && (cancel || panel).focus({ preventScroll: true });
                });
                return new Promise(function (resolve) { self._resolve = resolve; });
            },

            decide: function (ok) {
                if (!this.open) { return; }
                this.open = false;
                var resolve = this._resolve;
                var back = this._returnTo;
                this._resolve = null;
                this._returnTo = null;
                if (back && document.body.contains(back)) { back.focus({ preventScroll: true }); }
                if (resolve) { resolve(!!ok); }
            }
        });

        Alpine.store('popup', {
            open: false,
            title: '',
            dirty: false,
            opener: null,   // the trigger element, for focus return + abort
            href: '',       // full-page fallback URL, shown if the load fails
            flashId: '',    // id of the row the trigger sat in, flashed after a save

            show: function (title, opener) {
                this.title = title || '';
                this.opener = opener || null;
                this.href = (opener && opener.getAttribute('href')) || '';
                // Remember which row the sheet was opened from, so the page
                // can point at what changed once it refreshes (UI audit 3.10).
                var anchor = opener && opener.closest ? opener.closest('[id]') : null;
                this.flashId = (anchor && anchor.id !== 'main-content' && anchor.id !== 'popup-body') ? anchor.id : '';
                this.dirty = false;
                this.open = true;
                document.documentElement.classList.add('overflow-hidden');
                // One history entry so the phone's Back button closes the
                // sheet (see the popstate handler below) instead of leaving.
                try { history.pushState({ yardwayPopup: true }, '', location.href); } catch (err) { /* ignore */ }
                // The panel is display:none until Alpine applies x-show
                Alpine.nextTick(function () {
                    var panel = document.getElementById('popup-panel');
                    if (panel) { panel.focus({ preventScroll: true }); }
                });
            },

            // force=true skips the unsaved-changes check (used after a save).
            // fromHistory=true means Back already popped our entry.
            close: function (force, fromHistory) {
                if (!this.open) { return; }
                var self = this;
                if (this.dirty && !force) {
                    Alpine.store('confirm').ask({ text: 'Discard your changes?', label: 'Discard', danger: true })
                        .then(function (ok) {
                            if (ok) {
                                self.close(true, fromHistory);
                            } else if (fromHistory) {
                                // Back was pressed and refused: restore our entry
                                try { history.pushState({ yardwayPopup: true }, '', location.href); } catch (err) { /* ignore */ }
                            }
                        });
                    return;
                }
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
                if (!fromHistory && history.state && history.state.yardwayPopup) {
                    popping = true;
                    history.back();
                }
            }
        });
    });

    // ── Back button ──
    // Capture-phase so this runs before htmx's own popstate handler, which
    // would otherwise restore the previous page from its history cache.
    var popping = false;
    window.addEventListener('popstate', function (e) {
        var s = store();
        var onPopupEntry = e.state && e.state.yardwayPopup;
        if (popping) {
            // Our own history.back() after an X/backdrop close
            popping = false;
            e.stopImmediatePropagation();
            return;
        }
        if (s && s.open && !onPopupEntry) {
            // Back pressed while the sheet is open: close it, stay on the page
            e.stopImmediatePropagation();
            s.close(false, true);
            return;
        }
        if (onPopupEntry && !(s && s.open)) {
            // Landed on a stale sheet entry (e.g. Back after a boosted
            // navigation left the sheet behind): skip over it
            e.stopImmediatePropagation();
            history.back();
        }
    }, true);

    // ── Styled confirm for every hx-confirm ──
    document.body.addEventListener('htmx:confirm', function (e) {
        if (!e.detail.question) { return; }
        var elt = e.detail.elt || e.target;
        var data = (elt && elt.dataset) || {};
        e.preventDefault();
        Alpine.store('confirm').ask({
            text: e.detail.question,
            label: data.confirmLabel,
            danger: data.confirmDanger !== undefined ? data.confirmDanger !== 'false' : undefined
        }).then(function (ok) {
            if (!ok) { return; }
            // Now the request really goes: spin the button that asked
            var btn = elt && (elt.matches('button') ? elt : elt.querySelector('button[type="submit"]'));
            if (btn) { btn.classList.add('btn-loading'); btn.disabled = true; }
            e.detail.issueRequest(true);
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
        var fresh = !!e.detail.target.querySelector('[data-popup-fresh]');
        if (s) { s.dirty = !!verb && verb !== 'get' && !fresh; }
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

    // After a refresh, point at the row the sheet was opened from: a short
    // cream-to-transparent flash answers "did that save?" without a toast.
    function flashRow(id) {
        if (!id) { return; }
        var el = document.getElementById(id);
        if (!el) { return; }
        el.classList.remove('flash');
        void el.offsetWidth;  // restart the animation if it was mid-run
        el.classList.add('flash');
        el.addEventListener('animationend', function () { el.classList.remove('flash'); }, { once: true });
    }

    // Re-fetch the current page and swap #main-content in place. Scroll,
    // filters and the URL all stay put; toasts and Alpine state come back
    // through the same chrome hook boosted navigations use (base.html).
    function refreshMain(flashId) {
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
            flashRow(flashId);
        }).catch(function () {
            window.location.reload();
        });
    }

    document.body.addEventListener('popup:saved', function () {
        var s = store();
        var flashId = s ? s.flashId : '';
        if (s) { s.close(true); }
        refreshMain(flashId);
    });
})();
