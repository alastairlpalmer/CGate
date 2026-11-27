/* Built by tools/build_js.mjs from static/js/bundle.txt. Do not edit. */
/* ── js/overlay_back.js ── */
/* Back closes a sheet or a drawer (docs/MOBILE_APP_PLAN.md, item A7).
 *
 * On a phone, people close a sheet with the Back gesture. Without help,
 * Back leaves the page and the sheet stays open over the page before.
 * An overlay that calls opened() gets one history entry of its own, so
 * Back closes it and the page stays. The pop-up sheet (popup.js) does the
 * same for itself; the two work together, because each one only acts on
 * its own entries.
 *
 *   Yardway.overlay.opened(name, close, isOpen)   the overlay is now open
 *   Yardway.overlay.closed(name)                  it closed (X, scrim, Escape…)
 *   Yardway.overlay.sync(name, open, close, isOpen)   one call for both
 *
 * `close` is what Back calls. It must close the overlay; it may call
 * closed() too, which then does nothing. `isOpen` is optional: when it
 * says no, the overlay is forgotten. That covers an overlay that a page
 * change took away without a call to closed(); a Back that "closed" such
 * a one would be a press that did nothing.
 *
 * htmx changes the address in this order (1.9, with view transitions):
 * it saves the page (htmx:beforeHistorySave) and marks the current entry
 * as its own, which wipes our mark; then it pushes the new address
 * (htmx:beforeHistoryUpdate, htmx:pushedIntoHistory); then it puts the
 * new content in (htmx:afterSwap, htmx:afterSettle).
 *
 * Four rules keep the history and the page in step:
 *
 *   1. An entry is only removed (history.back()) when no htmx request is
 *      in flight. A request can change the address a moment later, and a
 *      Back that lands after that change would undo the wrong entry. The
 *      entry is removed when the requests are done, if it is still there.
 *   2. When htmx changes the address while our entry is the current one
 *      (a link inside a drawer), the change replaces our entry. Without
 *      this the entry stays as a second copy of the page, and one Back
 *      press seems to do nothing. The pop-up sheet's entry is treated the
 *      same way (a link inside a sheet).
 *   3. An overlay that is still open when the new content is in (a filter
 *      on the list under the horse preview) gets a new entry on top.
 *   4. If the address still changes while a history.back() of ours is on
 *      its way (rule 1 is a matter of timing), that Back lands one entry
 *      too low. It is followed by one step forward, to the new page.
 */
(function (root) {
    'use strict';

    // A request that never reports its end must not block rule 1 for
    // ever. The longest request is a save, at 60 seconds (base.html).
    var REQUEST_LIMIT_MS = 65000;

    /**
     * The logic, apart from the browser: `history` needs state, pushState,
     * back and forward; `later(fn)` runs fn after the page has been quiet
     * for a moment; `clock()` is the time in milliseconds. Unit-tested in
     * tests/overlay_back.test.js.
     */
    function create(history, location, later, clock) {
        var stack = [];     // open overlays, the last one opened last
        var popping = 0;    // our own history.back() calls still to land
        var raced = false;  // the address changed while one was on its way
        var leaving = false;    // htmx saved the page while our entry was current
        var changed = false;    // the address changed; the new content is not in yet
        var busy = 0;       // htmx requests in flight
        var lastStart = 0;  // when the newest of them started
        var checking = false;
        var now = clock || function () { return Date.now(); };

        function requestInFlight() {
            return busy > 0 && now() - lastStart < REQUEST_LIMIT_MS;
        }

        function indexOf(name) {
            for (var i = 0; i < stack.length; i++) {
                if (stack[i].name === name) { return i; }
            }
            return -1;
        }
        function entryName(state) {
            var s = state === undefined ? history.state : state;
            return (s && s.yardwayOverlay) || '';
        }
        function push(name) {
            try { history.pushState({ yardwayOverlay: name }, '', location.href); } catch (err) { /* ignore */ }
        }
        // Forget every overlay that says it is not open any more.
        function forgetGone() {
            stack = stack.filter(function (item) {
                try { return !item.isOpen || !!item.isOpen(); } catch (err) { return false; }
            });
        }

        // Remove the current entry when it is ours and its overlay is
        // gone. Waits while a request is in flight (rule 1).
        function dropDeadEntry() {
            forgetGone();
            var name = entryName();
            if (!name || indexOf(name) !== -1) { return; }
            if (requestInFlight()) { checkLater(); return; }
            popping += 1;
            history.back();
        }
        function checkLater() {
            if (checking) { return; }
            checking = true;
            later(function () { checking = false; dropDeadEntry(); });
        }

        function opened(name, close, isOpen) {
            var i = indexOf(name);
            if (i !== -1) { stack[i].close = close; stack[i].isOpen = isOpen; return; }
            stack.push({ name: name, close: close, isOpen: isOpen });
            // After a reload, or a return from another page, the entry is
            // already the current one: a second one would cost a dead Back.
            if (entryName() !== name) { push(name); }
        }

        function closed(name) {
            var i = indexOf(name);
            if (i === -1) { return; }
            stack.splice(i, 1);
            dropDeadEntry();
        }

        // Returns true when the event is ours and nobody else may act on it.
        function onPop(state) {
            leaving = false;
            if (popping > 0) {
                popping -= 1;
                if (raced && popping === 0) {
                    // Rule 4: the new page is one entry above this one.
                    raced = false;
                    history.forward();
                    return true;
                }
                // Two overlays closed out of order leave one more of ours.
                dropDeadEntry();
                return true;
            }
            forgetGone();
            var landed = entryName(state);
            var top = stack.length ? stack[stack.length - 1] : null;
            if (top && landed !== top.name) {
                // Back with an overlay open: close it, stay on the page.
                stack.pop();
                try { top.close(); } catch (err) { /* the overlay is gone */ }
                return true;
            }
            if (landed && indexOf(landed) === -1) {
                // An entry whose overlay is gone (after a full page load):
                // step over it. The next event is a real one.
                history.back();
                return true;
            }
            return false;
        }

        // htmx is about to save the page, and then to mark the current
        // entry as its own. Remember whose it was.
        function pageWillBeSaved() {
            var s = history.state;
            leaving = !!(s && (s.yardwayOverlay || s.yardwayPopup));
        }
        // htmx is about to push an address (rule 2). `update` is the
        // event's detail.history: {type, path}.
        function historyWillUpdate(update) {
            var ours = leaving || !!entryName();
            leaving = false;
            if (popping > 0) { raced = true; return; }
            if (ours && update && update.type === 'push') { update.type = 'replace'; }
        }
        // The address changed. The old content is still in the page, so an
        // overlay in it still looks open: wait for the new content.
        function historyUpdated() { changed = true; }
        // The new content is in (rule 3).
        function pageSettled() {
            if (!changed) { return; }
            changed = false;
            if (popping > 0) { return; }
            forgetGone();
            var top = stack.length ? stack[stack.length - 1] : null;
            if (top && entryName() !== top.name) { push(top.name); }
        }

        function requestStarted() { busy += 1; lastStart = now(); }
        function requestEnded() {
            busy = Math.max(0, busy - 1);
            // With a view transition the address changes a frame or two
            // after the request ends, so look again a little later.
            if (busy === 0) { checkLater(); }
        }

        return {
            opened: opened,
            closed: closed,
            sync: function (name, open, close, isOpen) {
                if (open) { opened(name, close, isOpen); } else { closed(name); }
            },
            onPop: onPop,
            pageWillBeSaved: pageWillBeSaved,
            historyWillUpdate: historyWillUpdate,
            historyUpdated: historyUpdated,
            pageSettled: pageSettled,
            requestStarted: requestStarted,
            requestEnded: requestEnded,
            isOpen: function (name) { return indexOf(name) !== -1; },
            anyOpen: function () { forgetGone(); return stack.length > 0; }
        };
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { create: create };
    }
    if (!root.document) { return; }

    var overlay = create(root.history, root.location, function (fn) { root.setTimeout(fn, 250); });
    root.Yardway = root.Yardway || {};
    root.Yardway.overlay = overlay;

    // Capture, as in popup.js: before htmx's own handler, which would put
    // the page before back from its store.
    root.addEventListener('popstate', function (e) {
        if (overlay.onPop(e.state)) { e.stopImmediatePropagation(); }
    }, true);

    var doc = root.document;
    // beforeSend, not beforeRequest: a request that a listener cancels in
    // beforeRequest never reports an end.
    doc.addEventListener('htmx:beforeSend', function () { overlay.requestStarted(); });
    doc.addEventListener('htmx:afterRequest', function () { overlay.requestEnded(); });
    doc.addEventListener('htmx:beforeHistorySave', function () { overlay.pageWillBeSaved(); });
    doc.addEventListener('htmx:beforeHistoryUpdate', function (e) {
        overlay.historyWillUpdate(e.detail && e.detail.history);
    });
    doc.addEventListener('htmx:pushedIntoHistory', function () { overlay.historyUpdated(); });
    doc.addEventListener('htmx:replacedInHistory', function () { overlay.historyUpdated(); });
    doc.addEventListener('htmx:afterSettle', function () { overlay.pageSettled(); });
})(typeof window !== 'undefined' ? window : globalThis);

;
/* ── js/popup.js ── */
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
            accent: false,  // outlined panel (trigger has data-popup-accent)
            opener: null,   // the trigger element, for focus return + abort
            href: '',       // full-page fallback URL, shown if the load fails
            flashId: '',    // id of the row the trigger sat in, flashed after a save

            show: function (title, opener) {
                this.title = title || '';
                this.opener = opener || null;
                this.href = (opener && opener.getAttribute('href')) || '';
                this.accent = !!(opener && opener.dataset && opener.dataset.popupAccent !== undefined);
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
                this.accent = false;
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
        body.classList.remove('popup-busy');
        var link = s && s.href
            ? ' <a href="' + s.href + '" class="link">Open the full page instead.</a>'
            : '';
        body.innerHTML =
            '<div class="p-5 text-sm text-charcoal-light" role="alert">' +
            'That didn’t load. Check your signal and try again.' + link + '</div>';
    }

    // A save that failed. The form stays, with what the person typed, and
    // the reason goes above it. Before, the form was replaced by "That
    // didn't load", and a record typed in a field with no signal was lost
    // (docs/MOBILE_APP_PLAN.md, item A3). Save stays active: base.html
    // frees the button when the request ends.
    function showSaveError(kind, status) {
        var body = popupBody();
        if (!body) { return; }
        body.classList.remove('popup-busy');
        var old = body.querySelector('[data-save-error]');
        if (old) { old.remove(); }
        var text;
        if (kind === 'htmx:sendError') {
            text = 'No signal. The record may not be saved. Your entries are still here. Close this and check before you save it again.';
        } else if (kind === 'htmx:timeout') {
            text = 'No answer from the server. The record may be saved. Close this and check before you save it again.';
        } else if (status === 403) {
            text = 'Not saved. Your role does not have access to do this.';
        } else if (status === 413) {
            text = 'Not saved. The file is too large.';
        } else {
            text = 'Not saved. The fault is on our side. Your entries are still here. Try again.';
        }
        var box = document.createElement('div');
        box.className = 'alert-error popup-save-error';
        box.setAttribute('role', 'alert');
        box.setAttribute('data-save-error', '');
        box.setAttribute('tabindex', '-1');
        box.textContent = text;
        body.insertBefore(box, body.firstChild);
        // The sheet may be scrolled to its foot, where Save is.
        if (box.scrollIntoView) { box.scrollIntoView({ block: 'nearest' }); }
        box.focus({ preventScroll: true });
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
        if (s.open && elt.closest('#popup-body')) {
            // A trigger inside the sheet (a quick-view action) loads the
            // next step into the same sheet. Keep the original opener and
            // history entry — focus still returns to where the user
            // started — and leave the current content up (dimmed) until
            // the swap lands, so the element issuing the request stays in
            // the DOM for the whole round trip.
            s.title = elt.dataset.popupTitle;
            s.dirty = false;
            var body = popupBody();
            if (body) { body.classList.add('popup-busy'); }
            return;
        }
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
        e.detail.target.classList.remove('popup-busy');
        var s = store();
        var verb = e.detail.requestConfig && e.detail.requestConfig.verb;
        var fresh = !!e.detail.target.querySelector('[data-popup-fresh]');
        if (s) { s.dirty = !!verb && verb !== 'get' && !fresh; }
        // A form back with errors: put the user on the first field in
        // error, at every width, so they hear and see what to fix (on
        // phones focus used to drop to the page body).
        var invalid = e.detail.target.querySelector('[aria-invalid="true"]');
        if (invalid) { invalid.focus(); return; }
        if (!window.matchMedia('(min-width: 640px)').matches) {
            var panel = document.getElementById('popup-panel');
            if (panel) { panel.focus({ preventScroll: true }); }
            return;
        }
        var first = e.detail.target.querySelector(
            'input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled])'
        );
        if (first) { first.focus({ preventScroll: true }); }
    });

    // Keep Tab inside the open sheet (it is aria-modal): from the last
    // control Tab went on to the sidebar and page behind the scrim.
    // The confirm dialog ("Discard your changes?") opens over the sheet:
    // then Tab stays in the dialog. It went back into the sheet, so the
    // dialog's buttons could not be reached with the keyboard.
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Tab') { return; }
        var s = store();
        var confirmOpen = window.Alpine && Alpine.store('confirm') && Alpine.store('confirm').open;
        var panel = document.getElementById(confirmOpen ? 'confirm-panel' : 'popup-panel');
        if (!panel || (!confirmOpen && (!s || !s.open))) { return; }
        var focusable = Array.prototype.filter.call(
            panel.querySelectorAll(
                'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), ' +
                'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
            ),
            function (el) { return el.offsetParent !== null || el === document.activeElement; }
        );
        if (!focusable.length) { e.preventDefault(); panel.focus(); return; }
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        var inside = panel.contains(document.activeElement);
        if (e.shiftKey && (document.activeElement === first || !inside)) {
            e.preventDefault(); last.focus();
        } else if (!e.shiftKey && (document.activeElement === last || !inside)) {
            e.preventDefault(); first.focus();
        }
    });

    ['htmx:responseError', 'htmx:sendError', 'htmx:timeout'].forEach(function (name) {
        document.body.addEventListener(name, function (e) {
            if (!isPopupTarget(e.detail.target)) { return; }
            var verb = e.detail.requestConfig && e.detail.requestConfig.verb;
            var body = popupBody();
            if (verb && verb !== 'get' && body && body.querySelector('form')) {
                showSaveError(name, e.detail.xhr ? e.detail.xhr.status : 0);
                return;
            }
            showLoadError();
        });
    });
    // The next try starts: the old reason goes.
    document.body.addEventListener('htmx:beforeRequest', function (e) {
        if (!isPopupTarget(e.detail.target)) { return; }
        var old = popupBody() && popupBody().querySelector('[data-save-error]');
        if (old) { old.remove(); }
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
    //
    // opts.quiet is for a refresh nobody asked for (the app came back to
    // the front, static/js/connection.js): a failure then changes nothing.
    // After a save, a failure says so. It does not reload: with no signal,
    // a reload puts the browser's error page in place of the app.
    function refreshMain(flashId, opts) {
        var quiet = !!(opts && opts.quiet);
        var redirected = false;
        var ok = false;
        return fetch(window.location.href, {
            credentials: 'same-origin',
            // Only the title, the messages and #main-content come back
            // (core.middleware.MainOnlyMiddleware), not the whole page.
            headers: { 'Accept': 'text/html', 'X-Main-Only': '1' }
        }).then(function (resp) {
            redirected = resp.redirected;
            ok = resp.ok;
            return resp.text();
        }).then(function (html) {
            var doc = new DOMParser().parseFromString(html, 'text/html');
            var fresh = doc.getElementById('main-content');
            var live = document.getElementById('main-content');
            if (!fresh || !live || !ok) {
                // A sign-in that ended sends us to the sign-in page: go there.
                if (redirected || !quiet) { window.location.reload(); }
                return;
            }
            live.replaceWith(fresh);
            if (window.htmx) { htmx.process(fresh); }
            if (window.Yardway && Yardway.afterMainSwap) {
                Yardway.afterMainSwap(fresh, html);
            }
            flashRow(flashId);
        }).catch(function () {
            if (quiet) { return; }
            if (window.Yardway && Yardway.notice) {
                Yardway.notice('warning', 'Saved. The page is not up to date: no signal.', {
                    label: 'Refresh',
                    run: function () { refreshMain(flashId); }
                });
            }
        });
    }
    window.Yardway = window.Yardway || {};
    window.Yardway.refreshMain = refreshMain;

    // Where focus goes back after a save. The refresh replaces
    // #main-content, opener included, so focus fell to the top of the page
    // (pre-launch review). Note the opener's place now, and find the same
    // control in the new content.
    function openerKey(opener) {
        if (!opener || !opener.getAttribute) { return null; }
        return {
            id: opener.id || '',
            href: opener.getAttribute('hx-get') || opener.getAttribute('href') || '',
            text: (opener.textContent || '').trim()
        };
    }
    function refocus(key, flashId) {
        var main = document.getElementById('main-content');
        if (!main || (document.activeElement && document.activeElement !== document.body &&
                      document.body.contains(document.activeElement))) { return; }
        var target = null;
        if (key && key.id) { target = document.getElementById(key.id); }
        if (!target && key && key.href) {
            var same = Array.prototype.filter.call(
                main.querySelectorAll('a[href], button, [hx-get]'),
                function (el) {
                    return (el.getAttribute('hx-get') || el.getAttribute('href') || '') === key.href &&
                        (el.textContent || '').trim() === key.text;
                }
            );
            if (same.length === 1) { target = same[0]; }
        }
        if (!target && flashId) {
            var row = document.getElementById(flashId);
            target = row && row.querySelector('a[href], button');
        }
        if (target) {
            target.focus({ preventScroll: true });
        } else {
            if (!main.hasAttribute('tabindex')) { main.setAttribute('tabindex', '-1'); }
            main.focus({ preventScroll: true });
        }
    }

    document.body.addEventListener('popup:saved', function () {
        var s = store();
        var flashId = s ? s.flashId : '';
        var key = openerKey(s && s.opener);
        if (s) { s.close(true); }
        var done = refreshMain(flashId);
        if (done && done.then) { done.then(function () { refocus(key, flashId); }); }
    });
})();

;
/* ── js/connection.js ── */
/* What a person sees when a request fails, the signal goes, the app comes
 * back to the front, or a new version is live (docs/MOBILE_APP_PLAN.md,
 * items A2, A4, A5 and A6).
 *
 *   - A tap or a save that fails shows a message, with Try again where
 *     that is safe. Before, nothing showed.
 *   - A request has a time limit. Before, it could wait for ever.
 *   - A "No signal" mark shows while the browser reports no connection.
 *   - A 403 or 404 on a tap shows the app's own page for it.
 *   - After 5 minutes in the background, the page is fetched again.
 *   - When the server runs a newer version than this page, the next tap
 *     loads the page in full, so new HTML never runs with old scripts.
 *   - At sign-out, the copies of pages that htmx keeps for the Back
 *     button are deleted.
 *
 * The pop-up sheet shows its own messages for requests into the sheet
 * (static/js/popup.js). The Back button is static/js/overlay_back.js.
 */
(function (root) {
    'use strict';

    // ── Pure helpers, unit-tested in tests/connection.test.js ──

    // The limit covers the download too. On a very weak signal a long
    // list takes more than 10 seconds to arrive, so 20, not less.
    var GET_LIMIT_MS = 20000;
    var SAVE_LIMIT_MS = 60000;      // a save can push to Xero
    var UPLOAD_LIMIT_MS = 300000;   // a photo on a weak signal
    var AWAY_MS = 5 * 60 * 1000;

    // Events a person causes. A request started by anything else (a card
    // that loads by itself, a count that refreshes) fails without a
    // message: the person did not ask for it.
    var USER_EVENTS = ['click', 'submit', 'change', 'input', 'keyup', 'keydown', 'search'];

    /** The time limit for one request, in milliseconds. */
    function timeLimit(verb, hasFiles) {
        if (hasFiles) { return UPLOAD_LIMIT_MS; }
        return (verb || 'get').toLowerCase() === 'get' ? GET_LIMIT_MS : SAVE_LIMIT_MS;
    }

    /**
     * Did a person start this request?
     *
     * `eventType` is the type of the event that triggered it, or '' for a
     * request made in code. `triggerSpec` is the element's hx-trigger.
     */
    function startedByPerson(eventType, triggerSpec) {
        if (/\b(load|every|revealed|intersect)\b/.test(triggerSpec || '')) { return false; }
        if (!eventType) { return true; }    // htmx.ajax(): a row tap opens a preview
        return USER_EVENTS.indexOf(eventType) !== -1;
    }

    /**
     * The words for a failed request.
     *
     * `kind` is 'send' (no connection), 'timeout' or 'response' (the
     * server answered with an error, `status`). Returns {text, retry}:
     * retry is true when the same request can be sent again with no risk.
     * A save is never sent again by this message. After a timeout, the
     * first one may have arrived.
     */
    function failureMessage(kind, verb, status) {
        var reading = (verb || 'get').toLowerCase() === 'get';
        if (kind === 'send') {
            return reading
                ? { text: 'No signal. That did not load.', retry: true }
                : { text: 'No signal. The record may not be saved. Your entries are still here. Check before you save it again.', retry: false };
        }
        if (kind === 'timeout') {
            return reading
                ? { text: 'No answer from the server. Try again.', retry: true }
                : { text: 'No answer from the server. The record may be saved. Check before you save it again.', retry: false };
        }
        if (status === 403) { return { text: 'Your role does not have access to that.', retry: false }; }
        if (status === 404) { return { text: 'That is not there any more.', retry: false }; }
        if (status === 413) { return { text: 'Not saved. The file is too large.', retry: false }; }
        if (status === 429) { return { text: 'Too many tries. Wait a minute, then try again.', retry: false }; }
        if (reading) { return { text: 'That did not load. The fault is on our side. Try again.', retry: true }; }
        return { text: 'Not saved. The fault is on our side. Your entries are still here. Try again.', retry: false };
    }

    /** Is it safe to fetch the page again without asking? */
    function safeToRefresh(state) {
        return !(state.offline || state.sheetOpen || state.overlayOpen || state.edited ||
                 state.rowsSelected || state.typing);
    }

    /** Has the app been in the background long enough to refresh? */
    function awayLongEnough(hiddenAt, now) {
        return !!hiddenAt && now - hiddenAt >= AWAY_MS;
    }

    // ── What happens on phones (docs/MOBILE_APP_PLAN.md, item D7) ──
    // The usage figures get four events: a request that failed, time with
    // no signal, a tap that was slow, and "app installed"
    // (js/app_install.js). They say how often a yard's signal fails,
    // which decides the work on "no signal" (section E of the plan). An
    // event has no name, number or text of a record in it.
    var SLOW_MS = 2000;

    /**
     * An address with its numbers and keys taken out: /horses/54/edit/
     * becomes /horses/:id/edit/. A query string is left off: the search
     * box puts typed names in it.
     */
    function pathPattern(url) {
        var path = String(url || '').replace(/^[a-z]+:\/\/[^/]+/i, '').split(/[?#]/)[0];
        return path.split('/').map(function (part) {
            if (/^\d+$/.test(part)) { return ':id'; }
            // A key in a sign-in link or an unsubscribe link: letters with
            // digits, or a long part that is not plain words with hyphens
            // (the name of a Help guide is).
            var mixed = /\d/.test(part) && /[a-z]/i.test(part);
            if ((mixed && part.length >= 8) || (part.length >= 16 && !/^[a-z-]+$/.test(part))) { return ':key'; }
            return part;
        }).join('/');
    }

    /** Was a tap slow enough to count? Rounded, so it says no exact time. */
    function slowTap(ms) {
        if (!(ms >= SLOW_MS)) { return null; }
        return { seconds: Math.round(ms / 100) / 10 };
    }

    /** Send one event, if the usage figures are on and loaded. */
    function track(ph, name, properties) {
        if (!ph || !ph.__loaded || typeof ph.capture !== 'function') { return false; }
        try { ph.capture(name, properties || {}); } catch (err) { return false; }
        return true;
    }

    var api = {
        timeLimit: timeLimit,
        startedByPerson: startedByPerson,
        failureMessage: failureMessage,
        safeToRefresh: safeToRefresh,
        awayLongEnough: awayLongEnough,
        pathPattern: pathPattern,
        slowTap: slowTap,
        track: track,
        SLOW_MS: SLOW_MS,
        AWAY_MS: AWAY_MS
    };
    if (typeof module !== 'undefined' && module.exports) { module.exports = api; }
    if (!root.document) { return; }

    var doc = root.document;
    var Yardway = root.Yardway = root.Yardway || {};

    // ── Messages ──
    // Built here, not by Alpine: a message must show even when the page's
    // own scripts are the thing that failed. One at a time: a second
    // failure replaces the first.

    function clearNotice() {
        var old = doc.querySelectorAll('[data-request-error]');
        Array.prototype.forEach.call(old, function (el) { el.remove(); });
    }

    /**
     * Show a message in the toast area. `action` is optional:
     * {label, run}. Returns the element.
     */
    function notice(kind, text, action) {
        var box = doc.getElementById('toast-container');
        if (!box) { return null; }
        clearNotice();
        var el = doc.createElement('div');
        el.className = kind === 'warning' ? 'toast-warning' : kind === 'info' ? 'toast-info' : 'toast-error';
        el.setAttribute('data-request-error', '');
        el.setAttribute('role', kind === 'info' ? 'status' : 'alert');
        var words = doc.createElement('span');
        words.className = 'toast-words';
        words.textContent = text;
        el.appendChild(words);
        if (action) {
            var button = doc.createElement('button');
            button.type = 'button';
            button.className = 'toast-action';
            button.textContent = action.label;
            button.addEventListener('click', function () { el.remove(); action.run(); });
            el.appendChild(button);
        }
        var dismiss = doc.createElement('button');
        dismiss.type = 'button';
        dismiss.className = 'toast-dismiss';
        dismiss.setAttribute('aria-label', 'Dismiss');
        dismiss.textContent = '×';
        dismiss.addEventListener('click', function () { el.remove(); });
        el.appendChild(dismiss);
        box.appendChild(el);
        return el;
    }
    Yardway.notice = notice;

    function installed() {
        var match = root.matchMedia ? root.matchMedia('(display-mode: standalone)').matches : false;
        return !!match || root.navigator.standalone === true;
    }
    // Each event says if it came from the app opened from its icon.
    Yardway.track = function (name, properties) {
        var all = { app_display: installed() ? 'installed' : 'browser' };
        Object.keys(properties || {}).forEach(function (key) { all[key] = properties[key]; });
        return track(root.posthog, name, all);
    };

    // ── Time limits ──
    doc.addEventListener('htmx:configRequest', function (e) {
        var detail = e.detail || {};
        if (detail.timeout) { return; }     // set on the element with hx-request
        var elt = detail.elt;
        var form = elt && elt.closest ? elt.closest('form') : null;
        var hasFiles = !!form && Array.prototype.some.call(
            form.querySelectorAll('input[type="file"]'),
            function (input) { return input.files && input.files.length > 0; }
        );
        detail.timeout = timeLimit(detail.verb, hasFiles);
    });

    // ── A request that failed ──
    function isSheet(target) { return !!target && target.id === 'popup-body'; }

    function onFailure(kind, e) {
        var detail = e.detail || {};
        var config = detail.requestConfig || {};
        var event = config.triggeringEvent;
        var elt = detail.elt;
        var spec = elt && elt.getAttribute ? elt.getAttribute('hx-trigger') : '';
        var byPerson = startedByPerson(event ? event.type : '', spec);
        var status = detail.xhr ? detail.xhr.status : 0;
        if (byPerson) {
            Yardway.track('app request failed', {
                kind: kind, status: status, verb: (config.verb || 'get').toLowerCase(),
                path: pathPattern(config.path), in_sheet: isSheet(detail.target)
            });
        }
        if (isSheet(detail.target)) { return; }     // popup.js says it in the sheet
        // A part that loads by itself says it in place (showLazyFailure),
        // with its own Try again. A second message on top covered it.
        if (elt && elt.closest && elt.closest('[data-lazy-load]')) { return; }
        if (!byPerson) { return; }
        var message = failureMessage(kind, config.verb, status);
        var action = null;
        if (message.retry && event && elt) {
            action = {
                label: 'Try again',
                run: function () {
                    if (elt.isConnected && root.htmx) { root.htmx.trigger(elt, event.type); }
                    else { root.location.reload(); }
                }
            };
        }
        notice('error', message.text, action);
    }
    // A part of the page that loads after the page ([data-lazy-load]: the
    // dashboard's Timeline and Money, the notification panel) and failed. It stayed a grey
    // placeholder for good, with no word (pre-launch review). It now says
    // so, with a Try again button.
    function showLazyFailure(e) {
        var elt = e.detail && e.detail.elt;
        var box = elt && elt.closest ? elt.closest('[data-lazy-load]') : null;
        if (!box) { return; }
        var busy = box.querySelector('[aria-busy="true"]');
        var place = busy || box;
        // The notification panel keeps its address in data-src and is
        // loaded by js/notifications.js, not by hx-get.
        var url = box.getAttribute('hx-get') || box.getAttribute('data-src');
        var note = doc.createElement('div');
        note.className = 'p-4 text-sm text-charcoal flex flex-wrap items-center gap-3';
        note.setAttribute('role', 'status');
        note.setAttribute('data-lazy-failed', '');
        var text = doc.createElement('span');
        text.textContent = 'This did not load.';
        var button = doc.createElement('button');
        button.type = 'button';
        button.className = 'btn-secondary';
        button.textContent = 'Try again';
        button.addEventListener('click', function () {
            if (root.htmx && url) {
                note.remove();
                if (busy) { busy.setAttribute('aria-busy', 'true'); }
                root.htmx.ajax('GET', url, { source: box, target: box, swap: box.getAttribute('hx-swap') || 'innerHTML' });
            } else { root.location.reload(); }
        });
        note.appendChild(text);
        note.appendChild(button);
        var old = place.querySelector('[data-lazy-failed]');
        if (old) { old.remove(); }
        if (busy) { busy.removeAttribute('aria-busy'); }
        Array.prototype.forEach.call(place.querySelectorAll('[class*="skeleton"]'), function (bone) {
            bone.style.display = 'none';
        });
        place.appendChild(note);
    }
    ['htmx:sendError', 'htmx:timeout', 'htmx:responseError'].forEach(function (name) {
        doc.addEventListener(name, showLazyFailure);
    });

    doc.addEventListener('htmx:sendError', function (e) { onFailure('send', e); });
    doc.addEventListener('htmx:timeout', function (e) { onFailure('timeout', e); });
    doc.addEventListener('htmx:responseError', function (e) { onFailure('response', e); });

    // The next request that works takes the message away.
    doc.addEventListener('htmx:afterRequest', function (e) {
        if (e.detail && e.detail.successful) { clearNotice(); }
    });

    // A tap that took more than 2 seconds to get its answer.
    var started = typeof WeakMap === 'function' ? new WeakMap() : null;
    doc.addEventListener('htmx:beforeRequest', function (e) {
        var detail = e.detail || {};
        if (started && detail.xhr) { started.set(detail.xhr, Date.now()); }
    });
    doc.addEventListener('htmx:afterRequest', function (e) {
        var detail = e.detail || {};
        if (!started || !detail.xhr || !started.has(detail.xhr) || !detail.successful) { return; }
        var slow = slowTap(Date.now() - started.get(detail.xhr));
        if (!slow) { return; }
        var config = detail.requestConfig || {};
        var event = config.triggeringEvent;
        var spec = detail.elt && detail.elt.getAttribute ? detail.elt.getAttribute('hx-trigger') : '';
        if (!startedByPerson(event ? event.type : '', spec)) { return; }
        Yardway.track('app slow tap', {
            seconds: slow.seconds, verb: (config.verb || 'get').toLowerCase(), path: pathPattern(config.path)
        });
    });
    // htmx keeps a copy of the page for Back. A message in that copy would
    // come back with buttons that do nothing.
    doc.addEventListener('htmx:beforeHistorySave', clearNotice);

    // ── The server's own pages for "no access" and "not found" ──
    // htmx does not put an error response in the page. For a tap on a
    // link, the app's 403 and 404 pages say more than a message can, and
    // they have the same frame, so let them in.
    //
    // ── A newer version on the server ──
    // A tap replaces only the content of the page; its scripts and styles
    // stay as they were loaded. When the server says it runs another
    // version, load the page in full instead (once for each deploy).
    function pageVersion() {
        var meta = doc.querySelector('meta[name="app-version"]');
        return meta ? meta.getAttribute('content') : '';
    }
    doc.addEventListener('htmx:beforeSwap', function (e) {
        var detail = e.detail || {};
        var xhr = detail.xhr;
        if (!xhr || !detail.boosted) { return; }
        var verb = (detail.requestConfig && detail.requestConfig.verb) || 'get';
        var served = xhr.getResponseHeader('X-App-Version');
        var mine = pageVersion();
        if (verb === 'get' && served && mine && served !== mine && xhr.status >= 200 && xhr.status < 300) {
            detail.shouldSwap = false;
            var path = detail.pathInfo && (detail.pathInfo.responsePath || detail.pathInfo.finalRequestPath);
            root.location.assign(path || root.location.href);
            return;
        }
        if ((xhr.status === 403 || xhr.status === 404) &&
                (xhr.responseText || '').indexOf('id="main-content"') !== -1) {
            detail.shouldSwap = true;
            detail.isError = false;
        }
    });

    // ── No signal ──
    // navigator.onLine is only sure when it says no: "online" can still be
    // a signal too weak to use. So this mark informs; it never blocks.
    function showSignal() {
        var mark = doc.getElementById('offline-mark');
        if (!mark) { return; }
        var offline = root.navigator.onLine === false;
        if (offline) {
            // Under the top bars on a phone; near the top on a computer.
            var bar = doc.querySelector('.app-bar');
            var box = bar ? bar.getBoundingClientRect() : null;
            mark.style.top = ((box && box.height) ? Math.max(box.bottom, 0) + 8 : 12) + 'px';
        }
        mark.hidden = !offline;
    }
    // How long the phone had no signal. Said when the signal is back: an
    // event cannot be sent before that.
    var signalLostAt = root.navigator.onLine === false ? Date.now() : 0;
    root.addEventListener('offline', function () { signalLostAt = Date.now(); showSignal(); });
    root.addEventListener('online', function () {
        showSignal();
        clearNotice();
        if (signalLostAt) {
            Yardway.track('app no signal', { seconds: Math.round((Date.now() - signalLostAt) / 1000) });
            signalLostAt = 0;
        }
    });
    doc.addEventListener('DOMContentLoaded', showSignal);
    doc.addEventListener('htmx:historyRestore', showSignal);
    showSignal();

    // ── Back to the front after a long time ──
    // An installed app has no reload button, and pull-to-refresh is off
    // (input.css). Without this, the app shows the data of the last visit.
    doc.addEventListener('input', function (e) {
        var main = e.target && e.target.closest ? e.target.closest('#main-content') : null;
        // The mark is on the element, so a new page starts clean.
        if (main) { main.setAttribute('data-edited', ''); }
    });

    function pageState() {
        var main = doc.getElementById('main-content');
        var active = doc.activeElement;
        var store = root.Alpine && root.Alpine.store ? root.Alpine.store('popup') : null;
        var confirm = root.Alpine && root.Alpine.store ? root.Alpine.store('confirm') : null;
        return {
            offline: root.navigator.onLine === false,
            sheetOpen: !!(store && store.open) || !!(confirm && confirm.open),
            overlayOpen: !!(Yardway.overlay && Yardway.overlay.anyOpen && Yardway.overlay.anyOpen()),
            edited: !!main && main.hasAttribute('data-edited'),
            rowsSelected: !!main && !!main.querySelector('input[type="checkbox"][data-horse-checkbox]:checked, input[type="checkbox"][data-split-checkbox]:checked'),
            typing: !!active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)
        };
    }

    // Only where there is no reload button to hand: the installed app, and
    // a phone or a tablet. On a computer a page that changed by itself
    // while its tab was in the background would be a surprise.
    function appLike() {
        var match = root.matchMedia ? function (q) { return root.matchMedia(q).matches; } : function () { return false; };
        return match('(display-mode: standalone)') || root.navigator.standalone === true || match('(pointer: coarse)');
    }

    var hiddenAt = 0;
    doc.addEventListener('visibilitychange', function () {
        if (doc.visibilityState === 'hidden') { hiddenAt = Date.now(); return; }
        var long = awayLongEnough(hiddenAt, Date.now());
        hiddenAt = 0;
        showSignal();
        if (!long || !appLike() || !Yardway.refreshMain || !safeToRefresh(pageState())) { return; }
        Yardway.refreshMain('', { quiet: true });
    });

    // ── Sign-out ──
    // htmx keeps the last pages in localStorage for the Back button. They
    // hold names, phone numbers and amounts, and a phone can be shared.
    // The sign-in page deletes them too (registration/_auth_head.html),
    // which covers a sign-in that ended by itself.
    doc.addEventListener('submit', function (e) {
        var form = e.target;
        var action = form && form.getAttribute ? (form.getAttribute('action') || '') : '';
        if (/\/logout\/?$/.test(action.split('?')[0])) {
            try { root.localStorage.removeItem('htmx-history-cache'); } catch (err) { /* private mode */ }
        }
    }, true);
})(typeof window !== 'undefined' ? window : globalThis);

;
/* ── js/drafts.js ── */
// Keeps typed fields when the sign-in ends during a save. See js/drafts_offer.js.
(function (root) {
    'use strict';

    var KEY = 'cgate-form-draft';
    var INTERRUPT_MS = 2 * 60 * 1000;
    var OFFER_MS = 2 * 60 * 60 * 1000;
    var SKIP_TYPES = { password: 1, file: 1, hidden: 1, submit: 1, button: 1, reset: 1, image: 1 };

    function pathOf(url, base) {
        try {
            var u = new URL(url, base);
            return u.pathname + u.search;
        } catch (err) { return ''; }
    }

    function collect(elements) {
        var fields = [];
        for (var i = 0; i < elements.length; i++) {
            var el = elements[i];
            var type = (el.type || '').toLowerCase();
            if (!el.name || el.disabled || SKIP_TYPES[type]) { continue; }
            if (el.name === 'csrfmiddlewaretoken' || el.name === 'form_token') { continue; }
            if (type === 'checkbox' || type === 'radio') {
                fields.push([el.name, el.checked ? (el.value || 'on') : null]);
            } else if (el.tagName === 'SELECT' && el.multiple) {
                var chosen = [];
                for (var j = 0; j < el.options.length; j++) {
                    if (el.options[j].selected) { chosen.push(el.options[j].value); }
                }
                fields.push([el.name, chosen]);
            } else {
                fields.push([el.name, el.value]);
            }
        }
        return fields;
    }

    function keepable(elements, action) {
        if (/\/accounts\/(login|logout|password|reset|two-step|confirm)/.test(action || '')) { return false; }
        for (var i = 0; i < elements.length; i++) {
            if ((elements[i].type || '').toLowerCase() === 'password') { return false; }
        }
        return true;
    }

    function decide(draft, now, onSignInPage) {
        if (!draft || !draft.time) { return 'drop'; }
        var age = now - draft.time;
        if (onSignInPage) { return age <= INTERRUPT_MS ? 'interrupt' : 'drop'; }
        if (draft.interrupted) { return age <= OFFER_MS ? 'offer' : 'drop'; }
        return 'drop';
    }

    var api = { collect: collect, keepable: keepable, decide: decide, pathOf: pathOf,
                INTERRUPT_MS: INTERRUPT_MS, OFFER_MS: OFFER_MS };
    if (typeof module !== 'undefined' && module.exports) { module.exports = api; }
    if (!root.document) { return; }

    var doc = root.document;

    function read() {
        try { return JSON.parse(root.sessionStorage.getItem(KEY) || 'null'); } catch (err) { return null; }
    }
    function write(draft) {
        try {
            if (draft) { root.sessionStorage.setItem(KEY, JSON.stringify(draft)); }
            else { root.sessionStorage.removeItem(KEY); }
        } catch (err) { /* private mode: nothing kept */ }
    }

    doc.addEventListener('submit', function (e) {
        var form = e.target;
        if (!form || !form.elements || (form.method || '').toLowerCase() !== 'post') { return; }
        if (form.hasAttribute('data-no-draft')) { return; }
        var action = pathOf(form.getAttribute('hx-post') || form.getAttribute('action') || root.location.href, root.location.href);
        if (!keepable(form.elements, action)) { return; }
        var store = root.Alpine && root.Alpine.store && root.Alpine.store('popup');
        var inSheet = !!form.closest('#popup-body');
        write({
            page: root.location.pathname + root.location.search,
            action: action,
            title: inSheet && store ? store.title : '',
            fields: collect(form.elements),
            time: Date.now(),
            interrupted: false
        });
    }, true);

    doc.addEventListener('htmx:afterRequest', function (e) {
        var d = e.detail || {};
        var verb = ((d.requestConfig && d.requestConfig.verb) || '').toLowerCase();
        if (!d.successful || verb !== 'post') { return; }
        var draft = read();
        if (draft && !draft.interrupted) { write(null); }
    });

    function offer(draft) {
        root.cgateDraft = { draft: draft, read: read, write: write, pathOf: pathOf, skip: SKIP_TYPES };
        var src = root.YARDWAY_DRAFTS_OFFER ||
            ((doc.querySelector('script[src*="js/drafts.js"]') || {}).src || '').replace('js/drafts.js', 'js/drafts_offer.js');
        if (!src) { return; }
        var tag = doc.createElement('script');
        tag.src = src;
        doc.head.appendChild(tag);
    }

    function onLoad() {
        var draft = read();
        if (!draft) { return; }
        var onSignIn = /\/accounts\/login\/?$/.test(root.location.pathname);
        var what = decide(draft, Date.now(), onSignIn);
        if (what === 'interrupt') { draft.interrupted = true; write(draft); return; }
        if (what === 'offer') { offer(draft); return; }
        write(null);
    }
    if (doc.readyState === 'loading') { doc.addEventListener('DOMContentLoaded', onLoad); } else { onLoad(); }
})(typeof window !== 'undefined' ? window : globalThis);

;
/* ── js/tap_feedback.js ── */
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

;
/* ── js/app_install.js ── */
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

;
/* ── js/share_file.js ── */
/* A PDF goes to the phone's share sheet (docs/MOBILE_APP_PLAN.md, item D6).
 *
 * A link to a PDF, in an installed app on an iPhone, opens the PDF in the
 * app's own window: there is no Back, and the person must close the app.
 * And what someone does with an invoice on a phone is send it. So on a
 * phone or a tablet that can share a file, a link with
 *
 *   data-share-file="INV00012.pdf"  data-share-title="Invoice INV00012"
 *
 * downloads the file and gives it to the share sheet (Mail, Messages,
 * WhatsApp, Save to Files, Print). The page stays where it is.
 *
 * Everywhere else the link works as it did: a computer downloads the file,
 * and so does a phone that cannot share a file. When anything fails, the
 * link is followed in the old way, so a fault here never hides a PDF.
 *
 * A browser lets a page open the share sheet only for a short time after a
 * tap. When the download takes longer, a message says the file is ready,
 * with a Share button: that tap is a new one.
 */
(function (root) {
    'use strict';

    var PDF = 'application/pdf';

    // Can this device give a PDF to its share sheet? `touch`: a finger,
    // not a mouse. A computer can often share too, but it has a Downloads
    // folder, which is what the office wants.
    function canShare(nav, touch, FileClass) {
        if (!touch || !nav || typeof nav.share !== 'function' || typeof nav.canShare !== 'function') return false;
        try {
            return !!nav.canShare({ files: [new FileClass([''], 'a.pdf', { type: PDF })] });
        } catch (err) {
            return false;
        }
    }

    // What to do when the share sheet did not take the file:
    //   'done'  the person closed the sheet: nothing more to do
    //   'ask'   the browser wants a new tap
    //   'open'  anything else: follow the link in the old way
    function afterShareError(error) {
        var name = error && error.name;
        if (name === 'AbortError') return 'done';
        if (name === 'NotAllowedError') return 'ask';
        return 'open';
    }

    // Is this answer the PDF, and not the sign-in page or an error page?
    function isPdf(response) {
        var type = (response.headers && response.headers.get('Content-Type')) || '';
        return !!response.ok && !response.redirected && type.toLowerCase().indexOf(PDF) === 0;
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { canShare: canShare, afterShareError: afterShareError, isPdf: isPdf };
        return;
    }

    var doc = root.document;

    function touch() {
        return !!(root.matchMedia && root.matchMedia('(pointer: coarse)').matches);
    }

    function busy(link, on) {
        if (on) link.setAttribute('aria-busy', 'true'); else link.removeAttribute('aria-busy');
        link.classList.toggle('is-loading', on);
    }

    function share(data, name) {
        return root.navigator.share(data).catch(function (error) {
            var next = afterShareError(error);
            if (next === 'done') return;
            if (next === 'ask' && root.Yardway && root.Yardway.notice) {
                root.Yardway.notice('info', name + ' is ready.', {
                    label: 'Share',
                    run: function () { root.navigator.share(data).catch(function () { /* closed, or refused again */ }); }
                });
                return;
            }
            throw error;
        });
    }

    doc.addEventListener('click', function (event) {
        var link = event.target && event.target.closest ? event.target.closest('a[data-share-file]') : null;
        if (!link || event.defaultPrevented) return;
        if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if (!canShare(root.navigator, touch(), root.File)) return;
        event.preventDefault();
        if (link.getAttribute('aria-busy') === 'true') return;  // a second tap while the file comes
        var name = link.getAttribute('data-share-file');
        busy(link, true);
        root.fetch(link.href, { credentials: 'same-origin' }).then(function (response) {
            if (!isPdf(response)) throw new Error('not the PDF');
            return response.blob();
        }).then(function (blob) {
            var file = new root.File([blob], name, { type: PDF });
            return share({ files: [file], title: link.getAttribute('data-share-title') || name }, name);
        }).then(function () {
            busy(link, false);
        }, function () {
            busy(link, false);
            root.location.href = link.href;  // the old way
        });
    });
})(typeof window !== 'undefined' ? window : this);

;
/* ── js/share_text.js ── */
/* The day's lots go to the team as text (docs/TRAINING_BOARD_REDESIGN_PLAN.md, 5.5).
 *
 * A yard sends its lots as a message. So a button with
 *
 *   data-share-text="#share-board"  data-share-title="Lots, Wed 25 Nov"
 *
 * takes the text from that element (a hidden <textarea>, which the server
 * filled) and gives it to the phone's share sheet: WhatsApp, Messages, Mail.
 * A computer has no share sheet that helps, so there the text is copied and
 * a message says so. data-share-copy always copies.
 *
 * The listener is on the document, so it is added one time and works after
 * each page swap.
 */
(function (root) {
    'use strict';

    // What to do with the text: 'share' on a device with a finger and a
    // share sheet, 'copy' everywhere else and when the button asks for it.
    function plan(nav, touch, copyOnly) {
        if (copyOnly) return 'copy';
        if (touch && nav && typeof nav.share === 'function') return 'share';
        return 'copy';
    }

    // The share sheet did not take the text: 'done' when the person closed
    // it, 'copy' for anything else, so the text is never lost.
    function afterShareError(error) {
        return error && error.name === 'AbortError' ? 'done' : 'copy';
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { plan: plan, afterShareError: afterShareError };
        return;
    }

    var doc = root.document;

    function touch() {
        return !!(root.matchMedia && root.matchMedia('(pointer: coarse)').matches);
    }

    function say(kind, text) {
        if (root.Yardway && root.Yardway.notice) root.Yardway.notice(kind, text);
    }

    // The old way to copy, for a browser with no clipboard API or a page
    // that is not on https.
    function copyOldWay(text) {
        var box = doc.createElement('textarea');
        box.value = text;
        box.setAttribute('readonly', '');
        box.style.position = 'fixed';
        box.style.opacity = '0';
        doc.body.appendChild(box);
        box.select();
        var ok = false;
        try { ok = doc.execCommand('copy'); } catch (err) { ok = false; }
        box.remove();
        return ok ? Promise.resolve() : Promise.reject(new Error('copy'));
    }

    function copy(text) {
        var clip = root.navigator && root.navigator.clipboard;
        var done = clip && clip.writeText
            ? clip.writeText(text).catch(function () { return copyOldWay(text); })
            : copyOldWay(text);
        return done.then(function () {
            say('info', 'Copied. Paste it in a message to the team.');
        }, function () {
            say('error', 'The text was not copied. Use Print in its place.');
        });
    }

    doc.addEventListener('click', function (event) {
        var button = event.target.closest && event.target.closest('[data-share-text]');
        if (!button) return;
        var source = doc.querySelector(button.getAttribute('data-share-text'));
        if (!source) return;
        event.preventDefault();
        var text = source.value || source.textContent || '';
        var title = button.getAttribute('data-share-title') || '';
        if (plan(root.navigator, touch(), button.hasAttribute('data-share-copy')) === 'copy') {
            copy(text);
            return;
        }
        root.navigator.share({ title: title, text: text }).catch(function (error) {
            if (afterShareError(error) === 'copy') copy(text);
        });
    });
})(typeof window !== 'undefined' ? window : this);

;
/* ── js/notifications.js ── */
/* The notification centre drawer (base.html, notifications/centre_views.py).
 *
 * Alpine store `notif`: open/close the drawer. Opening loads the panel
 * (/notifications/panel/) into #notif-panel; closing asks both badges to
 * refresh, so a notification read in the drawer drops off the count.
 * A save in the pop-up sheet (popup:saved) reloads an open panel with a
 * fresh sync: a vaccination recorded from the drawer closes its row.
 */
(function () {
    'use strict';

    function panel() { return document.getElementById('notif-panel'); }

    function load(fresh, tab) {
        var el = panel();
        if (!el || !window.htmx) { return; }
        var query = [];
        if (fresh) { query.push('fresh=1'); }
        if (tab) { query.push('tab=' + encodeURIComponent(tab)); }
        var url = el.getAttribute('data-src') + (query.length ? '?' + query.join('&') : '');
        window.htmx.ajax('GET', url, { source: el, target: el, swap: 'innerHTML' }).catch(function () { /* said by js/connection.js */ });
    }

    document.addEventListener('alpine:init', function () {
        Alpine.store('notif', {
            open: false,
            _returnTo: null,

            toggle: function () {
                if (this.open) { this.close(); } else { this.show(); }
            },

            // ``tab``: open on that tab ('new', 'action' or 'all').
            show: function (tab) {
                var self = this;
                this._returnTo = document.activeElement;
                this.open = true;
                // The phone's Back button closes the drawer (overlay_back.js).
                if (window.Yardway && Yardway.overlay) {
                    Yardway.overlay.opened('notif', function () { self.close(); }, function () { return self.open; });
                }
                load(false, tab);
                Alpine.nextTick(function () {
                    var drawer = document.getElementById('notif-drawer');
                    if (drawer) { drawer.focus({ preventScroll: true }); }
                });
            },

            close: function () {
                if (!this.open) { return; }
                this.open = false;
                // Before the badges ask for their counts: a request in
                // flight makes the entry wait (overlay_back.js, rule 1).
                if (window.Yardway && Yardway.overlay) { Yardway.overlay.closed('notif'); }
                if (window.htmx) { window.htmx.trigger(document.body, 'notif:changed'); }
                var back = this._returnTo;
                this._returnTo = null;
                if (back && document.body.contains(back) && back.focus) {
                    back.focus({ preventScroll: true });
                }
            },
        });
    });

    document.addEventListener('popup:saved', function () {
        if (window.Alpine && Alpine.store('notif') && Alpine.store('notif').open) { load(true); }
    });
    // A boosted page change closes the drawer: its rows point at the old page.
    document.addEventListener('htmx:beforeHistorySave', function () {
        if (window.Alpine && Alpine.store('notif')) { Alpine.store('notif').close(); }
    });
}());

;
/* ── js/lightbox.js ── */
/* Photo lightbox (templates/includes/_photo_lightbox.html).
 *
 * Any element with `data-lightbox-src` opens the full-screen viewer:
 *
 *   data-lightbox-src      full-resolution image URL (required)
 *   data-lightbox-title    heading, e.g. the horse name
 *   data-lightbox-caption  small line under the photo, e.g. age / colour / sex
 *   data-lightbox-href     optional link out of the viewer, e.g. the horse page
 *   data-lightbox-group    name of the set to step through (swipe, arrow keys,
 *                          arrow buttons). Triggers with the same name, in
 *                          page order, make one gallery; a trigger with no
 *                          group stands alone.
 *
 * The listeners run in the capture phase and stop the event, so a trigger
 * inside a boosted <a> (a horse-list row) shows the photo instead of
 * navigating. Without Alpine the event is left alone and the link works as
 * before, so the viewer is an enhancement and never a dead end.
 */
(function () {
    'use strict';

    var SWIPE_MIN_PX = 45;      // shorter drags are a tap or a stray finger
    var SWIPE_MAX_MS = 800;     // slower drags are a hold, not a swipe

    function photoFrom(el) {
        return {
            src: el.getAttribute('data-lightbox-src'),
            title: el.getAttribute('data-lightbox-title') || '',
            caption: el.getAttribute('data-lightbox-caption') || '',
            href: el.getAttribute('data-lightbox-href') || ''
        };
    }

    // The photos to step through: the trigger's group, in page order. A
    // trigger with no group (or the only one in its group) shows on its own.
    // Hidden triggers are dropped — the horse list holds every horse twice,
    // once in the phone cards and once in the desktop table, and only the
    // set on screen should be stepped through.
    function galleryFor(el) {
        var group = el.getAttribute('data-lightbox-group');
        var alone = { photos: [photoFrom(el)], index: 0 };
        if (!group) { return alone; }
        var sel = '[data-lightbox-src][data-lightbox-group="' +
                  group.replace(/"/g, '\\"') + '"]';
        var els = Array.prototype.slice.call(document.querySelectorAll(sel))
            .filter(function (node) { return node.getClientRects().length > 0; });
        var index = els.indexOf(el);
        if (index < 0) { return alone; }
        return { photos: els.map(photoFrom), index: index };
    }

    document.addEventListener('alpine:init', function () {
        Alpine.store('lightbox', {
            open: false,
            photos: [],
            index: 0,
            src: '',
            title: '',
            caption: '',
            href: '',
            loading: true,
            failed: false,
            opener: null,   // the trigger element, for focus return
            touch: null,    // where the finger currently down went down
            swiped: 0,      // time of the last swipe, to swallow its click

            show: function (gallery, opener) {
                var self = this;
                this.photos = gallery.photos;
                this.opener = opener || null;
                this.open = true;
                // The phone's Back button closes the viewer (overlay_back.js).
                if (window.Yardway && Yardway.overlay) {
                    Yardway.overlay.opened('lightbox', function () { self.close(); }, function () { return self.open; });
                }
                this.at(gallery.index);
                document.documentElement.classList.add('overflow-hidden');
                // The panel is display:none until Alpine applies x-show
                Alpine.nextTick(function () {
                    var panel = document.getElementById('lightbox-panel');
                    if (panel) { panel.focus({ preventScroll: true }); }
                });
            },

            at: function (i) {
                var photo = this.photos[i];
                if (!photo) { return; }
                this.index = i;
                this.src = photo.src;
                this.title = photo.title;
                this.caption = photo.caption;
                this.href = photo.href;
                this.loading = true;
                this.failed = false;
                this.preload();
            },

            // The photo either side is fetched now, so a swipe shows it at once.
            preload: function () {
                var self = this;
                [this.index - 1, this.index + 1].forEach(function (i) {
                    var photo = self.photos[self.wrap(i)];
                    if (photo) { (new Image()).src = photo.src; }
                });
            },

            wrap: function (i) {
                var n = this.photos.length;
                return n ? ((i % n) + n) % n : 0;
            },

            step: function (by) {
                if (this.photos.length < 2) { return; }
                this.at(this.wrap(this.index + by));
            },

            next: function () { this.step(1); },
            prev: function () { this.step(-1); },

            swipeBegin: function (e) {
                if (!e.touches || e.touches.length !== 1) { this.touch = null; return; }
                this.touch = { x: e.touches[0].clientX, y: e.touches[0].clientY,
                               at: Date.now() };
            },

            swipeEnd: function (e) {
                var start = this.touch;
                this.touch = null;
                if (!start || !e.changedTouches || !e.changedTouches.length) { return; }
                var dx = e.changedTouches[0].clientX - start.x;
                var dy = e.changedTouches[0].clientY - start.y;
                // A swipe is long, quick, and more sideways than up or down.
                if (Math.abs(dx) < SWIPE_MIN_PX ||
                    Math.abs(dx) < Math.abs(dy) * 1.5 ||
                    Date.now() - start.at > SWIPE_MAX_MS) { return; }
                this.swiped = Date.now();
                this.step(dx < 0 ? 1 : -1);
            },

            // A tap on the photo closes the viewer. The tail of a swipe can
            // also arrive as a click, which must not close it.
            tap: function () {
                if (Date.now() - this.swiped < 500) { return; }
                this.close();
            },

            close: function () {
                if (!this.open) { return; }
                this.open = false;
                if (window.Yardway && Yardway.overlay) { Yardway.overlay.closed('lightbox'); }
                var opener = this.opener;
                this.opener = null;
                this.touch = null;
                // The pop-up sheet locks scrolling too — leave its lock alone.
                var popup = window.Alpine ? Alpine.store('popup') : null;
                if (!popup || !popup.open) {
                    document.documentElement.classList.remove('overflow-hidden');
                }
                if (opener && document.body.contains(opener)) {
                    opener.focus({ preventScroll: true });
                }
            }
        });
    });

    function triggerFor(node) {
        return node && node.closest ? node.closest('[data-lightbox-src]') : null;
    }

    // Returns false when the viewer cannot open (Alpine not started yet), so
    // the caller leaves the event alone and the underlying link still works.
    function openViewer(el) {
        if (!window.Alpine || !Alpine.store('lightbox')) { return false; }
        Alpine.store('lightbox').show(galleryFor(el), el);
        return true;
    }

    document.addEventListener('click', function (e) {
        // Let people open the photo in a new tab or window as usual.
        if (e.button > 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) { return; }
        var el = triggerFor(e.target);
        if (!el || !openViewer(el)) { return; }
        e.preventDefault();
        e.stopPropagation();
    }, true);

    document.addEventListener('keydown', function (e) {
        var store = window.Alpine ? Alpine.store('lightbox') : null;
        if (store && store.open &&
            (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
            store.step(e.key === 'ArrowRight' ? 1 : -1);
            e.preventDefault();
            return;
        }
        if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') { return; }
        var el = triggerFor(e.target);
        if (!el || el !== e.target || !openViewer(el)) { return; }
        e.preventDefault();
        e.stopPropagation();
    }, true);
})();

;
/* ── js/more_menu.js ── */
/* "More" menu (templates/includes/_more_button.html).
 *
 * One page shows one main button. The other actions go in a More menu:
 *
 *   <div class="more-menu" x-data="moreMenu" @click.outside="close(false)"
 *        @keydown.escape="close(true)">
 *     {% include 'includes/_more_button.html' with label="More actions" %}
 *     <div class="more-menu-panel" x-ref="panel" x-show="open" x-cloak
 *          role="menu" @keydown="onPanelKey($event)" @click="close(false)">
 *       <a role="menuitem" class="more-menu-item" href="…">Edit</a>
 *       <div class="more-menu-sep" role="separator"></div>
 *       <form …><button role="menuitem" class="more-menu-item is-danger">Delete</button></form>
 *     </div>
 *   </div>
 *
 * Once Alpine runs, the panel is placed against the window: under the
 * button, or above it when there is no room below, and never past an edge
 * of the window. So a card that clips its content (overflow hidden or
 * auto) cannot cut it off, and a button that wraps to the left on a phone
 * does not push it off the screen. It follows the button as the page
 * scrolls, and closes when the button leaves the window. While it is
 * open, the row or cell that holds it (the closest td or li) gets
 * `has-open-menu`, which lifts it over the rows after it.
 *
 * Keyboard: Enter, Space or Down on the button opens the menu and puts focus
 * on the first item. Up and Down move, Home and End jump, Escape closes and
 * puts focus back on the button, Tab closes and moves on. A mouse click
 * opens the menu without moving focus.
 */
(function () {
    'use strict';

    // The item to focus after a key press, or -1 when there are no items.
    // `current` is -1 when focus is not on an item yet.
    function nextIndex(current, count, key) {
        if (!count) return -1;
        if (key === 'Home') return 0;
        if (key === 'End') return count - 1;
        if (key === 'ArrowDown') return current < 0 ? 0 : (current + 1) % count;
        if (key === 'ArrowUp') return current < 0 ? count - 1 : (current - 1 + count) % count;
        return current;
    }

    // Where a fixed panel goes: its right edge under the button's right
    // edge (its left edge under the button's left edge with `alignLeft`,
    // for a button at the start of a row), kept inside the window, and above the button when it does not
    // fit below. `rect` is the button's getBoundingClientRect().
    function placeFixed(rect, width, height, viewWidth, viewHeight, alignLeft) {
        var GAP = 6, EDGE = 8;
        var start = alignLeft ? rect.left : rect.right - width;
        var left = Math.min(Math.max(start, EDGE), viewWidth - width - EDGE);
        left = Math.max(left, EDGE);
        var top = rect.bottom + GAP;
        if (top + height > viewHeight - EDGE && rect.top - GAP - height >= EDGE) {
            top = rect.top - GAP - height;
        }
        return { top: Math.round(top), left: Math.round(left) };
    }

    // The box that position: fixed is measured from: the window, unless an
    // ancestor has a transform, filter or perspective.
    function fixedOrigin(el) {
        for (var node = el.parentElement; node && node !== document.body; node = node.parentElement) {
            var cs = window.getComputedStyle(node);
            if ((cs.transform && cs.transform !== 'none') ||
                (cs.filter && cs.filter !== 'none') ||
                (cs.perspective && cs.perspective !== 'none')) {
                var r = node.getBoundingClientRect();
                return { top: r.top, left: r.left };
            }
        }
        return { top: 0, left: 0 };
    }

    // The row an item of a shared menu acts on is named by a placeholder
    // in its address: /horses/0/move/ becomes /horses/52/move/.
    function rowUrl(template, pk) {
        return String(template).replace('/0/', '/' + pk + '/');
    }

    function rowTitle(template, name) {
        return String(template).replace('{name}', name);
    }

    function menuData() {
            return {
                open: false,
                // Named here so that each menu has its own. Alpine puts a
                // field that a component did not name on the outermost
                // x-data of the page (the body): every menu then shared
                // one `_onMove`, and destroy() removed another menu's
                // listener and left its own.
                _onMove: null,
                _host: null,
                // The button the panel is placed against.
                trigger: function () { return this.$refs.trigger; },
                init: function () {
                    var self = this;
                    // Follow the button. A phone hides its address bar as the
                    // page scrolls, which is a resize, so closing on every
                    // move would shut the menu under the reader's thumb.
                    // Close only once the button has left the window.
                    this._onMove = function () {
                        if (!self.open) return;
                        var button = self.trigger();
                        if (!button || !button.isConnected) { self.close(false); return; }
                        var r = button.getBoundingClientRect();
                        if (r.bottom < 0 || r.top > window.innerHeight) self.close(false);
                        else self.place();
                    };
                    window.addEventListener('scroll', this._onMove, true);
                    window.addEventListener('resize', this._onMove);
                },
                destroy: function () {
                    if (!this._onMove) return;
                    window.removeEventListener('scroll', this._onMove, true);
                    window.removeEventListener('resize', this._onMove);
                },
                place: function () {
                    var panel = this.$refs.panel, trigger = this.trigger();
                    if (!panel || !trigger) return;
                    // On a phone the tab bar covers the foot of the window,
                    // so the room below ends at its top edge.
                    var bottom = window.innerHeight;
                    var bar = document.getElementById('mobile-tab-bar');
                    if (bar && bar.getClientRects().length) {
                        bottom = Math.min(bottom, bar.getBoundingClientRect().top);
                    }
                    var pos = placeFixed(trigger.getBoundingClientRect(),
                        panel.offsetWidth, panel.offsetHeight,
                        window.innerWidth, bottom, panel.classList.contains('is-left'));
                    // Inside a transformed parent (the horse sheet on a
                    // phone slides on a transform) "fixed" is measured from
                    // that parent, not the window, so take its corner off.
                    var origin = fixedOrigin(panel);
                    panel.style.position = 'fixed';
                    panel.style.top = (pos.top - origin.top) + 'px';
                    panel.style.left = (pos.left - origin.left) + 'px';
                    panel.style.right = 'auto';
                    panel.style.marginTop = '0';
                },
                items: function () {
                    var panel = this.$refs.panel;
                    if (!panel) return [];
                    // Leave out items hidden at this width (md:hidden and the
                    // like): they cannot take focus.
                    return Array.prototype.slice.call(
                        panel.querySelectorAll('[role="menuitem"]:not([disabled])'))
                        .filter(function (el) { return el.getClientRects().length > 0; });
                },
                show: function (focusFirst) {
                    this.open = true;
                    var button = this.trigger();
                    this._host = button ? button.closest('td, li') : null;
                    if (this._host) this._host.classList.add('has-open-menu');
                    var self = this;
                    this.$nextTick(function () {
                        self.place();
                        if (focusFirst) {
                            var list = self.items();
                            if (list.length) list[0].focus();
                        }
                    });
                },
                close: function (returnFocus) {
                    if (!this.open) return;
                    this.open = false;
                    if (this._host) this._host.classList.remove('has-open-menu');
                    var button = this.trigger();
                    if (returnFocus && button && button.isConnected) button.focus();
                },
                toggle: function () {
                    if (this.open) this.close(false); else this.show(false);
                },
                onTriggerKey: function (e) {
                    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        this.show(true);
                    }
                },
                onPanelKey: function (e) {
                    if (e.key === 'Escape') {
                        e.preventDefault();
                        e.stopPropagation();
                        this.close(true);
                        return;
                    }
                    if (e.key === 'Tab') {
                        this.close(false);
                        return;
                    }
                    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].indexOf(e.key) !== -1) {
                        e.preventDefault();
                        // The key is the menu's. On the horse list the page
                        // also reads the arrows, to go to the next horse.
                        e.stopPropagation();
                        var list = this.items();
                        var i = nextIndex(list.indexOf(document.activeElement), list.length, e.key);
                        if (i >= 0) list[i].focus();
                    }
                }
            };
    }

    // One menu for all the rows of a list (templates/horses/_row_menu.html).
    // A list of 75 horses had 75 menus: 75 Alpine components and 300 links
    // for htmx to set up, to show one menu at a time. Now a row has only
    // its button, `data-row-menu="<pk>" data-name="<name>"`, and this one
    // panel is filled for the row when its button is used.
    function rowMenuData() {
        var menu = menuData();
        var baseInit = menu.init, baseDestroy = menu.destroy, baseClose = menu.close;
        menu._trigger = null;
        menu._onClick = null;
        menu._onKey = null;
        menu.trigger = function () { return this._trigger; };
        menu.init = function () {
            baseInit.call(this);
            var self = this;
            this._onClick = function (e) {
                var button = e.target.closest ? e.target.closest('[data-row-menu]') : null;
                if (button) { self.openFor(button, false); return; }
                // A click on an item closes the panel itself (@click).
                if (self.open && !self.$refs.panel.contains(e.target)) self.close(false);
            };
            // On the way down (capture), so the key is taken before the
            // page's own key handlers see it.
            this._onKey = function (e) {
                var button = e.target.closest ? e.target.closest('[data-row-menu]') : null;
                if (button && (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ')) {
                    e.preventDefault();
                    e.stopPropagation();
                    self.openFor(button, true);
                } else if (e.key === 'Escape' && self.open && !self.$refs.panel.contains(e.target)) {
                    e.stopPropagation();
                    self.close(true);
                }
            };
            document.addEventListener('click', this._onClick);
            document.addEventListener('keydown', this._onKey, true);
        };
        menu.destroy = function () {
            baseDestroy.call(this);
            document.removeEventListener('click', this._onClick);
            document.removeEventListener('keydown', this._onKey, true);
        };
        menu.openFor = function (button, fromKey) {
            var same = this.open && this._trigger === button;
            if (same && !fromKey) { this.close(false); return; }
            if (this.open && !same) this.close(false);
            this._trigger = button;
            this.fill(button);
            button.setAttribute('aria-expanded', 'true');
            this.show(fromKey);
        };
        menu.close = function (returnFocus) {
            if (this._trigger) this._trigger.setAttribute('aria-expanded', 'false');
            baseClose.call(this, returnFocus);
        };
        menu.fill = function (button) {
            var panel = this.$refs.panel;
            var pk = button.getAttribute('data-row-menu');
            var name = button.getAttribute('data-name') || '';
            var row = button.closest('tr, li');
            var page = row ? row.querySelector('[data-horse-link]') : null;
            panel.setAttribute('aria-label', 'More actions for ' + name);
            Array.prototype.forEach.call(panel.querySelectorAll('[role="menuitem"]'), function (item) {
                var d = item.dataset;
                if (d.rowHref) item.setAttribute('href', rowUrl(d.rowHref, pk));
                if (d.rowGet) item.setAttribute('hx-get', rowUrl(d.rowGet, pk));
                if (d.rowTitle) item.setAttribute('data-popup-title', rowTitle(d.rowTitle, name));
                // The row's own link knows which list to go back to.
                if (d.rowPage !== undefined && page) item.setAttribute('href', page.getAttribute('href'));
            });
            // htmx reads hx-get when it sets a link up, so set them up again.
            if (typeof window !== 'undefined' && window.htmx) window.htmx.process(panel);
        };
        return menu;
    }

    function register(Alpine) {
        Alpine.data('moreMenu', menuData);
        Alpine.data('rowMenu', rowMenuData);
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            nextIndex: nextIndex, placeFixed: placeFixed, rowUrl: rowUrl, rowTitle: rowTitle,
            menuData: menuData, rowMenuData: rowMenuData,
        };
    }
    if (typeof document !== 'undefined') {
        document.addEventListener('alpine:init', function () { register(window.Alpine); });
    }
})();

;
/* ── js/formset_rows.js ── */
/* Add and remove rows of a Django formset in the browser
 * (templates/horses/partials/_ownership_rows.html, the new arrival page's
 * other owners).
 *
 *   <div x-data="formsetRows('ownership_shares')">
 *     {{ formset.management_form }}
 *     <div x-ref="rows"> ...one [data-formset-row] per form... </div>
 *     <template x-ref="blank"> ...the empty form's row (__prefix__)... </template>
 *     <button type="button" @click="add()">Add owner</button>
 *   </div>
 *
 * add() copies the blank row with the next form number and adds one to
 * TOTAL_FORMS, so the server reads the new row. remove() takes a row that
 * is not saved yet off the page: Django reads a missing extra form as an
 * empty one and skips it, so the numbers may have a gap. `count` is the
 * number of rows on the page, for "show the share box when there is more
 * than one owner".
 */
(function () {
    'use strict';

    // The blank row's HTML with __prefix__ replaced by the form number.
    function rowHtml(blank, index) {
        return String(blank).replace(/__prefix__/g, String(index));
    }

    function register(Alpine) {
        Alpine.data('formsetRows', function (prefix) {
            return {
                count: 0,
                init: function () {
                    this.count = this.$refs.rows ? this.$refs.rows.querySelectorAll('[data-formset-row]').length : 0;
                },
                total: function () {
                    return document.getElementById('id_' + prefix + '-TOTAL_FORMS');
                },
                add: function () {
                    var total = this.total();
                    if (!total || !this.$refs.blank) return;
                    var index = parseInt(total.value, 10) || 0;
                    this.$refs.rows.insertAdjacentHTML('beforeend', rowHtml(this.$refs.blank.innerHTML, index));
                    total.value = String(index + 1);
                    this.count += 1;
                    var rows = this.$refs.rows;
                    this.$nextTick(function () {
                        var all = rows.querySelectorAll('[data-formset-row]');
                        var last = all[all.length - 1];
                        var first = last && last.querySelector('select, input:not([type=hidden])');
                        if (first) first.focus();
                    });
                },
                remove: function (el) {
                    var row = el.closest('[data-formset-row]');
                    if (!row) return;
                    row.remove();
                    this.count = Math.max(0, this.count - 1);
                }
            };
        });
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { rowHtml: rowHtml };
    }
    if (typeof document !== 'undefined') {
        document.addEventListener('alpine:init', function () { register(window.Alpine); });
    }
})();

;
/* ── js/date_quick.js ── */
/* Yesterday, Today and Tomorrow under each date box (Dev board, 21 Nov
 * 2026: "each date field should have a button for yesterday, today,
 * tomorrow").
 *
 * Runs on every htmx:load (the first page, a boosted page swap, a pop-up
 * sheet), so every <input type="date"> gets the three buttons once. A
 * button puts that day in the box and fires input and change, so Alpine
 * and htmx see the new value. The button for the day in the box is
 * pressed (aria-pressed). A day outside the box's min or max is disabled.
 *
 * Left out:
 *   - a box with data-date-quick="off", or inside an element with it
 *   - a box in a GET form (list filters: a range, not one day)
 *   - a horse's date of birth and an expiry date (date_of_birth,
 *     expiry_date): never yesterday or tomorrow
 *   - a disabled or read-only box
 *
 * "Today" is the device's date, as the date box itself uses.
 *
 * A date that is weeks or months away (the next due date of a
 * vaccination) gets its own buttons in place of the three days
 * (docs/MOBILE_APP_PLAN.md, C8). The box names them:
 *
 *   data-date-quick="+6m +12m"       "+6 months", "+12 months"
 *   data-date-quick="+6w +8w"        "+6 weeks", "+8 weeks"
 *   data-date-quick-from="date_given"
 *
 * They count from the date in the box named by data-date-quick-from (the
 * date the vaccination was given), or from today when that box is empty
 * or not named.
 */
(function () {
    'use strict';

    var DAYS = [['Yesterday', -1], ['Today', 0], ['Tomorrow', 1]];
    // A horse's date of birth and a document's expiry are never
    // yesterday or tomorrow. A foal's date of birth often is: it keeps them.
    var SKIP_NAME = /(^|[-_])(date_of_birth|expiry_date)$/i;

    function pad(n) { return (n < 10 ? '0' : '') + n; }

    // The ISO date `offset` days from `now` (a Date), in local time.
    function isoDay(now, offset) {
        var d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + offset, 12);
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
    }

    var UNITS = { d: ['day', 'days'], w: ['week', 'weeks'], m: ['month', 'months'] };

    // "+6m +12m" → [{ amount: 6, unit: 'm', label: '+6 months' }, …].
    // Anything that is not such a list gives [].
    function parseSpec(spec) {
        var out = [];
        String(spec || '').split(/[\s,]+/).forEach(function (token) {
            var m = /^\+(\d{1,3})([dwm])$/.exec(token);
            if (!m) return;
            var amount = parseInt(m[1], 10);
            out.push({ amount: amount, unit: m[2], label: '+' + amount + ' ' + UNITS[m[2]][amount === 1 ? 0 : 1] });
        });
        return out;
    }

    // The ISO date `amount` days, weeks or months after the ISO date
    // `from`. A month that is too short gives its last day: 31 August
    // + 6 months is 28 February.
    function shift(from, amount, unit) {
        var parts = String(from).split('-');
        var y = parseInt(parts[0], 10), mo = parseInt(parts[1], 10) - 1, d = parseInt(parts[2], 10);
        var out;
        if (unit === 'm') {
            var last = new Date(y, mo + amount + 1, 0, 12).getDate();
            out = new Date(y, mo + amount, Math.min(d, last), 12);
        } else {
            out = new Date(y, mo, d + amount * (unit === 'w' ? 7 : 1), 12);
        }
        return out.getFullYear() + '-' + pad(out.getMonth() + 1) + '-' + pad(out.getDate());
    }

    // The box the buttons count from, or null.
    function fromBox(box) {
        var name = box.dataset ? box.dataset.dateQuickFrom : '';
        if (!name || !box.form || !box.form.querySelector) return null;
        return box.form.querySelector('input[name$="' + name + '"]');
    }

    // The day a button stands for, today.
    function dayOf(box, b) {
        if (b.dataset.unit) {
            var from = fromBox(box);
            var start = from && /^\d{4}-\d{2}-\d{2}$/.test(from.value) ? from.value : isoDay(new Date(), 0);
            return shift(start, parseInt(b.dataset.amount, 10), b.dataset.unit);
        }
        return isoDay(new Date(), parseInt(b.dataset.offset, 10));
    }

    // True when a box should get no buttons. `box` needs only name,
    // disabled, readOnly, dataset, closest() and form.
    function skip(box) {
        if (!box || box.disabled || box.readOnly) return true;
        if (box.dataset && (box.dataset.dateQuick === 'off' || box.dataset.dateQuickDone)) return true;
        if (box.closest && box.closest('[data-date-quick="off"]')) return true;
        if (box.form && String(box.form.getAttribute ? box.form.getAttribute('method') || 'get' : box.form.method).toLowerCase() === 'get') return true;
        if (SKIP_NAME.test(box.name || '')) return true;
        return false;
    }

    // Is the ISO day inside the box's min and max? (ISO dates sort as text.)
    function allowed(day, min, max) {
        return !(min && day < min) && !(max && day > max);
    }

    function labelFor(box) {
        var label = box.id && document.querySelector('label[for="' + box.id + '"]');
        var text = label ? label.textContent.replace(/[*\s]+$/, '').trim() : '';
        return text || 'date';
    }

    function refresh(box, bar) {
        var buttons = bar.querySelectorAll('button');
        for (var i = 0; i < buttons.length; i++) {
            var b = buttons[i];
            var day = dayOf(box, b);
            b.disabled = !allowed(day, box.min, box.max);
            b.setAttribute('aria-pressed', box.value === day ? 'true' : 'false');
        }
    }

    function enhance(box) {
        if (skip(box)) return;
        box.dataset.dateQuickDone = '1';
        var bar = document.createElement('div');
        bar.className = 'date-quick';
        bar.setAttribute('role', 'group');
        bar.setAttribute('aria-label', 'Quick dates for ' + labelFor(box));
        var later = parseSpec(box.dataset.dateQuick);
        var choices = later.length
            ? later.map(function (c) { return { label: c.label, set: { amount: String(c.amount), unit: c.unit } }; })
            : DAYS.map(function (pair) { return { label: pair[0], set: { offset: String(pair[1]) } }; });
        choices.forEach(function (choice) {
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'date-quick-btn';
            b.textContent = choice.label;
            Object.keys(choice.set).forEach(function (key) { b.dataset[key] = choice.set[key]; });
            b.addEventListener('click', function () {
                box.value = dayOf(box, b);
                box.dispatchEvent(new Event('input', { bubbles: true }));
                box.dispatchEvent(new Event('change', { bubbles: true }));
                refresh(box, bar);
            });
            bar.appendChild(b);
        });
        box.insertAdjacentElement('afterend', bar);
        // "+6 months" counts from another box: when that date changes,
        // the button that is pressed can change too.
        var from = later.length ? fromBox(box) : null;
        if (from) {
            from.addEventListener('input', function () { refresh(box, bar); });
            from.addEventListener('change', function () { refresh(box, bar); });
        }
        box.addEventListener('input', function () { refresh(box, bar); });
        box.addEventListener('change', function () { refresh(box, bar); });
        refresh(box, bar);
    }

    function enhanceAll(root) {
        var scope = root && root.querySelectorAll ? root : document;
        var boxes = scope.querySelectorAll('input[type="date"]');
        for (var i = 0; i < boxes.length; i++) enhance(boxes[i]);
        if (scope.matches && scope.matches('input[type="date"]')) enhance(scope);
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { isoDay: isoDay, skip: skip, allowed: allowed, parseSpec: parseSpec, shift: shift };
    }
    if (typeof document !== 'undefined') {
        document.addEventListener('htmx:load', function (e) { enhanceAll(e.target); });
        document.addEventListener('DOMContentLoaded', function () { enhanceAll(document); });
    }
})();

;
/* ── js/settings_nav.js ── */
/* Settings sections (templates/settings.html).
 *
 * The settings page holds every group (Business, Users and roles, Yard
 * setup, Billing, Your preferences, Data). On a wide screen a menu on the
 * left shows one group at a time; on a phone the same menu is a row of
 * chips above it. Without JavaScript every group shows, as before.
 *
 * Which group opens:
 *   1. the one named in the address (#billing), or the one that holds the
 *      named element (#services lives in Billing), so old links still work;
 *   2. else the last one open in this browser tab, so a form that sends you
 *      back to the settings page (edit a rate, add a user) returns you to
 *      where you were;
 *   3. else the first.
 *
 * Groups that fold (a site and its locations, here and on the Locations
 * page) stay as the reader left them in this browser tab: Archive and
 * Delete reload the page, and the site the reader was working in must not
 * close under them.
 */
(function () {
    'use strict';

    var STORE_KEY = 'cgate:settings-section';
    var FOLDS_KEY = 'cgate:settings-folds';

    // `ids`: the groups on the page, in order. `hashOwner`: the group that
    // holds the element the hash names (or ''). `stored`: the last group
    // open in this tab (or '').
    function pickSection(ids, hash, hashOwner, stored) {
        if (!ids.length) return '';
        var name = (hash || '').replace(/^#/, '');
        if (name && ids.indexOf(name) !== -1) return name;
        if (hashOwner && ids.indexOf(hashOwner) !== -1) return hashOwner;
        if (stored && ids.indexOf(stored) !== -1) return stored;
        return ids[0];
    }

    function readStore() {
        try { return window.sessionStorage.getItem(STORE_KEY) || ''; } catch (err) { return ''; }
    }

    function writeStore(id) {
        try { window.sessionStorage.setItem(STORE_KEY, id); } catch (err) { /* private mode */ }
    }

    // What the reader did to each fold: { key: true (opened) or false
    // (closed) }. A fold the reader has not touched is not in it, and the
    // page decides if it starts open. `raw` is what the store holds.
    function parseFolds(raw) {
        var out = {};
        try {
            var data = JSON.parse(raw || '{}');
            if (!data || typeof data !== 'object' || Array.isArray(data)) return out;
            Object.keys(data).forEach(function (key) {
                if (typeof data[key] === 'boolean') out[key] = data[key];
            });
        } catch (err) { /* a damaged store is an empty one */ }
        return out;
    }

    // The record after one fold opens or closes. The old record is not changed.
    function withFold(folds, key, open) {
        var out = {};
        Object.keys(folds).forEach(function (item) { out[item] = folds[item]; });
        out[key] = !!open;
        return out;
    }

    function readFolds() {
        try { return parseFolds(window.sessionStorage.getItem(FOLDS_KEY)); } catch (err) { return {}; }
    }

    function writeFolds(folds) {
        try { window.sessionStorage.setItem(FOLDS_KEY, JSON.stringify(folds)); } catch (err) { /* private mode */ }
    }

    function register(Alpine) {
        // On a <details data-fold-key="...">. The page decides if a fold
        // starts open; one the reader opened or closed in this tab stays so.
        Alpine.data('settingsFold', function () {
            return {
                init: function () {
                    var el = this.$el;
                    var key = el.dataset.foldKey;
                    if (!key) return;
                    var folds = readFolds();
                    if (Object.prototype.hasOwnProperty.call(folds, key)) el.open = folds[key];
                    el.addEventListener('toggle', function () {
                        writeFolds(withFold(readFolds(), key, el.open));
                    });
                }
            };
        });

        Alpine.data('settingsSections', function () {
            return {
                current: '',
                ids: [],
                init: function () {
                    var self = this;
                    this.ids = Array.prototype.map.call(
                        this.$root.querySelectorAll('section[data-settings-section]'),
                        function (el) { return el.id; });
                    this.fromHash(true);
                    window.addEventListener('hashchange', function () { self.fromHash(false); });
                },
                fromHash: function (first) {
                    var hash = window.location.hash;
                    var owner = '';
                    if (hash.length > 1) {
                        var target = document.getElementById(hash.slice(1));
                        var section = target && target.closest('section[data-settings-section]');
                        owner = section ? section.id : '';
                    }
                    this.current = pickSection(this.ids, hash, owner, first ? readStore() : '');
                    writeStore(this.current);
                    // An element inside the group (#services): scroll to it once shown.
                    if (owner && hash.slice(1) !== owner) {
                        this.$nextTick(function () {
                            var el = document.getElementById(hash.slice(1));
                            if (el) el.scrollIntoView({ block: 'start' });
                        });
                    }
                },
                show: function (id) {
                    this.current = id;
                    writeStore(id);
                    if (window.history && window.history.replaceState) {
                        window.history.replaceState(null, '', '#' + id);
                    }
                    var top = this.$refs.panels;
                    if (top && top.getBoundingClientRect().top < 0) top.scrollIntoView({ block: 'start' });
                }
            };
        });
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { pickSection: pickSection, parseFolds: parseFolds, withFold: withFold };
    }
    if (typeof document !== 'undefined') {
        document.addEventListener('alpine:init', function () { register(window.Alpine); });
    }
})();

;
/* ── js/photo_quick_add.js ── */
/* Quick-add photo form (templates/horses/partials/photo_form.html).
 *
 * Registered as an Alpine component so the same form works on its full
 * page and inside the pop-up sheet (where an inline <script> would race
 * Alpine's initialisation of the swapped-in markup). Accumulates files
 * from the camera and gallery inputs into one DataTransfer and mirrors
 * them onto the real, submitted `images` input.
 *
 * A photo is made smaller on the phone before it is sent
 * (docs/MOBILE_APP_PLAN.md, item B8). A phone photo is 3 to 10 MB; the
 * server takes 5 MB at most, and on a weak signal a full-size photo takes
 * minutes. The longest side becomes 2,000 px at most and the file a JPEG,
 * about 500 KB. A photo that cannot be read here (HEIC on some phones), a
 * PNG, and a photo that is small already go as they are; the server
 * checks every file as before.
 */
(function (root) {
    'use strict';

    // ── Pure helpers, unit-tested in tests/photo_quick_add.test.js ──

    var MAX_SIDE = 2000;
    var JPEG_QUALITY = 0.82;
    var SMALL_ENOUGH_BYTES = 700 * 1024;

    /** The size to draw a photo at: the longest side MAX_SIDE at most. */
    function fitWithin(width, height, maxSide) {
        var longest = Math.max(width, height);
        if (!longest || longest <= maxSide) { return { width: width, height: height, scale: 1 }; }
        var scale = maxSide / longest;
        return { width: Math.round(width * scale), height: Math.round(height * scale), scale: scale };
    }

    /**
     * Is this a file to make smaller? Camera photos are JPEG (an iPhone
     * hands a HEIC photo to the page as JPEG). A PNG can have clear parts
     * that a JPEG cannot keep, and is most often a screenshot: leave it.
     */
    function canShrink(type) {
        return type === 'image/jpeg' || type === 'image/webp';
    }

    /** After the change the file is a JPEG, so its name must say so. */
    function jpegName(name) {
        var base = String(name || 'photo').replace(/\.[A-Za-z0-9]{2,5}$/, '');
        return (base || 'photo') + '.jpg';
    }

    /** "480 KB" or "3.2 MB". */
    function sizeLabel(bytes) {
        if (bytes < 1024 * 1024) { return Math.max(1, Math.round(bytes / 1024)) + ' KB'; }
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            fitWithin: fitWithin, canShrink: canShrink, jpegName: jpegName, sizeLabel: sizeLabel,
            MAX_SIDE: MAX_SIDE,
        };
    }
    if (!root.document) { return; }

    var doc = root.document;

    /** Resolves with a smaller JPEG, or with the file itself when that is not possible or not useful. */
    function shrink(file) {
        return new Promise(function (resolve) {
            if (!canShrink(file.type) || !root.URL || !root.File) { resolve(file); return; }
            var url = URL.createObjectURL(file);
            var image = new Image();
            var done = function (result) { URL.revokeObjectURL(url); resolve(result); };
            image.onerror = function () { done(file); };
            image.onload = function () {
                try {
                    // The browser has turned the photo upright already (the
                    // phone records which way up it was held).
                    var fit = fitWithin(image.naturalWidth, image.naturalHeight, MAX_SIDE);
                    if (fit.scale === 1 && file.size <= SMALL_ENOUGH_BYTES) { done(file); return; }
                    var canvas = doc.createElement('canvas');
                    canvas.width = fit.width;
                    canvas.height = fit.height;
                    canvas.getContext('2d').drawImage(image, 0, 0, fit.width, fit.height);
                    canvas.toBlob(function (blob) {
                        if (!blob || blob.size >= file.size) { done(file); return; }
                        done(new File([blob], jpegName(file.name), { type: 'image/jpeg', lastModified: file.lastModified }));
                    }, 'image/jpeg', JPEG_QUALITY);
                } catch (err) {
                    done(file);
                }
            };
            image.src = url;
        });
    }

    doc.addEventListener('alpine:init', function () {
        Alpine.data('quickPhotoAdd', function (category) {
            return {
                category: category || 'condition',
                dt: new DataTransfer(),
                items: [],
                nextKey: 0,
                preparing: 0,   // photos still being made smaller
                progress: 0,    // percent of the upload sent; 0 when none is running

                init: function () {
                    var self = this;
                    // htmx reports how much of the upload has gone.
                    this.$el.addEventListener('htmx:xhr:progress', function (e) {
                        var d = e.detail || {};
                        if (d.lengthComputable && d.total) { self.progress = Math.max(1, Math.round((d.loaded / d.total) * 100)); }
                    });
                    this.$el.addEventListener('htmx:afterRequest', function () { self.progress = 0; });
                },

                get totalMB() {
                    return this.items.reduce(function (sum, item) { return sum + item.size; }, 0) / (1024 * 1024);
                },
                get totalLabel() {
                    return sizeLabel(this.items.reduce(function (sum, item) { return sum + item.size; }, 0));
                },

                // One at a time: a phone holds a full-size photo in memory
                // while it is drawn, and four at once can be too much.
                addFiles: function (fileList) {
                    var self = this;
                    var files = Array.prototype.slice.call(fileList);
                    this.preparing += files.length;
                    return files.reduce(function (chain, file) {
                        return chain.then(function () { return shrink(file); }).then(function (ready) {
                            self.dt.items.add(ready);
                            self.items.push({
                                key: self.nextKey++,
                                name: ready.name,
                                size: ready.size,
                                url: URL.createObjectURL(ready)
                            });
                            self.preparing -= 1;
                            self.sync();
                        });
                    }, Promise.resolve());
                },

                remove: function (index) {
                    URL.revokeObjectURL(this.items[index].url);
                    this.dt.items.remove(index);
                    this.items.splice(index, 1);
                    this.sync();
                },

                sync: function () {
                    if (this.$refs.images) { this.$refs.images.files = this.dt.files; }
                }
            };
        });
    });
})(typeof window !== 'undefined' ? window : globalThis);

;
/* ── js/service_picker.js ── */
/**
 * Services directory picker (billing.forms.ServicePickerMixin).
 *
 * A record form's "Service" <select> carries, per option, the item's price
 * (data-price) and the record fields it fills (data-fill, JSON). Choosing a
 * service copies the price into the cost input and locks it behind an
 * "Override price" button; choosing "Other" unlocks everything and clears
 * what the picker filled in. The server repeats the fill for a form posted
 * without JavaScript, so this only saves clicks — it never decides price.
 *
 * Runs on every htmx:load, so pop-up sheets and boosted page swaps pick it
 * up without per-page wiring. The pure part (decide) is exported for
 * static/js/tests.
 */
(function (root) {
    'use strict';

    var OTHER = 'other';

    /**
     * What the form should do for the selected option.
     * option: {value, price, fill} (or null); costValue: the cost input's
     * current text. A preset whose cost differs from its price counts as
     * overridden — the input stays editable and the button offers the
     * set price back.
     */
    function decide(option, costValue) {
        if (!option || !option.value) {
            return { mode: 'none' };
        }
        if (option.value === OTHER) {
            return { mode: 'other' };
        }
        var price = option.price == null ? '' : String(option.price);
        var typed = costValue == null ? '' : String(costValue).trim();
        var overridden = typed !== '' && Number(typed) !== Number(price);
        return {
            mode: 'preset',
            price: price,
            fill: option.fill || {},
            overridden: overridden
        };
    }

    function formatPrice(price) {
        var n = Number(price);
        return '£' + (isNaN(n) ? price : n.toFixed(2));
    }

    var api = { decide: decide, formatPrice: formatPrice, OTHER: OTHER };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
        return;
    }
    root.YardwayServicePicker = api;

    if (typeof document === 'undefined') {
        return;
    }

    // ── DOM wiring ───────────────────────────────────────────────────

    function readOption(select) {
        var o = select.options[select.selectedIndex];
        if (!o) {
            return null;
        }
        var fill = {};
        if (o.dataset.fill) {
            try { fill = JSON.parse(o.dataset.fill); } catch (e) { fill = {}; }
        }
        return { value: o.value, price: o.dataset.price || null, fill: fill };
    }

    function costInput(select) {
        var form = select.form;
        if (!form) {
            return null;
        }
        return form.querySelector('[data-service-cost]')
            || form.querySelector('[name="cost"]')
            || form.querySelector('[name="amount"]');
    }

    function fieldIn(form, name) {
        return form ? form.querySelector('[name="' + name + '"]') : null;
    }

    function overrideButton(cost, create) {
        var wrap = cost.parentNode;
        var btn = wrap ? wrap.querySelector('.service-price-override') : null;
        if (!btn && create && wrap) {
            btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'service-price-override text-xs text-forest underline mt-1';
            cost.insertAdjacentElement('afterend', btn);
        }
        return btn;
    }

    function lock(cost, price) {
        cost.readOnly = true;
        cost.classList.add('bg-sage-50');
        cost.dataset.servicePrice = price;
        var btn = overrideButton(cost, true);
        if (btn) {
            btn.textContent = 'Override price';
            btn.hidden = false;
        }
    }

    // Editable, but the set price is one click away.
    function unlockOverridden(cost, price) {
        cost.readOnly = false;
        cost.classList.remove('bg-sage-50');
        cost.dataset.servicePrice = price;
        var btn = overrideButton(cost, true);
        if (btn) {
            btn.textContent = 'Use set price (' + formatPrice(price) + ')';
            btn.hidden = false;
        }
    }

    function unlock(cost) {
        cost.readOnly = false;
        cost.classList.remove('bg-sage-50');
        delete cost.dataset.servicePrice;
        var btn = overrideButton(cost, false);
        if (btn) {
            btn.hidden = true;
        }
    }

    function applied(select) {
        if (!select._serviceApplied) {
            select._serviceApplied = {};
        }
        return select._serviceApplied;
    }

    // Copy the item's defaults in. Selects always follow the service
    // (their default is never "blank"); text inputs only when empty or
    // still holding what a previous pick filled in.
    function fillFields(select, fill) {
        var form = select.form;
        var previous = applied(select);
        var next = {};
        Object.keys(fill).forEach(function (name) {
            var field = fieldIn(form, name);
            if (!field) {
                return;
            }
            var value = String(fill[name]);
            if (field.tagName === 'SELECT' || !field.value || field.value === String(previous[name])) {
                field.value = value;
            }
            next[name] = value;
        });
        select._serviceApplied = next;
    }

    function clearFilled(select) {
        var form = select.form;
        var previous = applied(select);
        Object.keys(previous).forEach(function (name) {
            var field = fieldIn(form, name);
            if (field && field.tagName !== 'SELECT' && field.value === String(previous[name])) {
                field.value = '';
            }
        });
        select._serviceApplied = {};
    }

    function firstFillField(select) {
        var names = Object.keys(applied(select));
        for (var i = 0; i < names.length; i++) {
            var field = fieldIn(select.form, names[i]);
            if (field && field.tagName !== 'SELECT') {
                return field;
            }
        }
        return null;
    }

    /** On change: apply the pick. initial=true only restores the locked
        state after a re-render (validation error) without touching values. */
    function apply(select, initial) {
        var cost = costInput(select);
        var state = decide(readOption(select), cost ? cost.value : '');
        if (state.mode === 'preset') {
            if (!initial) {
                if (cost) {
                    cost.value = state.price;
                }
                fillFields(select, state.fill);
                state.overridden = false;
            } else {
                select._serviceApplied = state.fill;
            }
            if (cost) {
                if (state.overridden) {
                    unlockOverridden(cost, state.price);
                } else {
                    lock(cost, state.price);
                }
            }
            return;
        }
        if (cost) {
            if (!initial && cost.readOnly && cost.dataset.servicePrice === cost.value) {
                cost.value = '';
            }
            unlock(cost);
        }
        if (!initial) {
            var focus = firstFillField(select) || cost;
            clearFilled(select);
            if (state.mode === OTHER && focus) {
                focus.focus();
            }
        }
    }

    function init(select) {
        if (select.dataset.servicePickerReady) {
            return;
        }
        select.dataset.servicePickerReady = '1';
        apply(select, true);
    }

    function initWithin(rootEl) {
        if (!rootEl || !rootEl.querySelectorAll) {
            return;
        }
        if (rootEl.matches && rootEl.matches('select[data-service-picker]')) {
            init(rootEl);
        }
        rootEl.querySelectorAll('select[data-service-picker]').forEach(init);
    }

    document.addEventListener('change', function (e) {
        var t = e.target;
        if (t && t.matches && t.matches('select[data-service-picker]')) {
            apply(t, false);
        }
    });

    document.addEventListener('click', function (e) {
        var btn = e.target && e.target.closest ? e.target.closest('.service-price-override') : null;
        if (!btn) {
            return;
        }
        var cost = btn.previousElementSibling;
        if (!cost || !cost.dataset) {
            return;
        }
        var price = cost.dataset.servicePrice || '';
        if (cost.readOnly) {
            unlockOverridden(cost, price);
            cost.focus();
            cost.select();
        } else {
            cost.value = price;
            lock(cost, price);
        }
    });

    document.addEventListener('htmx:load', function (e) {
        initWithin(e.detail && e.detail.elt ? e.detail.elt : document);
    });
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { initWithin(document); });
    } else {
        initWithin(document);
    }
})(typeof window !== 'undefined' ? window : this);

;
/* ── js/coord_picker.js ── */
/* Coordinate picker (templates/locations/_coord_picker.html).
 *
 * One Alpine component for the three ways to enter a latitude/longitude
 * pair: a pasted "lat, lng" string, a Google Maps link (read by the server
 * at /locations/parse-link/, because short links need a cross-origin
 * redirect), and a draggable pin on a Leaflet map with OpenStreetMap
 * tiles. All three write to the form's hidden latitude/longitude inputs.
 *
 * Leaflet is lazy-loaded by the [data-map] loader in base.html; this
 * component asks for it on init and mounts the map on `leaflet:ready`.
 * It works inside the pop-up sheet (the map is sized after the sheet's
 * open transition) and on the full page.
 */
(function () {
    'use strict';

    var PAIR_RE = /^\s*([-+]?\d+(?:\.\d+)?)\s*(?:,\s*|\s+)([-+]?\d+(?:\.\d+)?)\s*$/;

    function round6(n) { return Math.round(n * 1e6) / 1e6; }

    document.addEventListener('alpine:init', function () {
        Alpine.data('coordPicker', function (centreLat, centreLng, centreZoom, parseUrl) {
            return {
                lat: '',
                lng: '',
                linkNote: '',
                map: null,
                marker: null,
                _ro: null,

                init: function () {
                    var self = this;
                    if (window.Yardway && Yardway.volatile) { Yardway.volatile(this.$el); }
                    var form = this.$el.closest('form');
                    this.latInput = form ? form.querySelector('input[name="latitude"]') : null;
                    this.lngInput = form ? form.querySelector('input[name="longitude"]') : null;
                    this.lat = (this.latInput && this.latInput.value) || '';
                    this.lng = (this.lngInput && this.lngInput.value) || '';
                    if (window.Yardway && Yardway.ensureLeaflet) { Yardway.ensureLeaflet(); }
                    if (window.L) {
                        this.$nextTick(function () { self.mount(); });
                    } else {
                        window.addEventListener('leaflet:ready', function () { self.mount(); }, { once: true });
                    }
                },

                destroy: function () {
                    if (this._ro) { this._ro.disconnect(); }
                    if (this.map) { this.map.remove(); this.map = null; this.marker = null; }
                },

                has: function () { return this.lat !== '' && this.lng !== ''; },

                display: function () { return this.has() ? this.lat + ', ' + this.lng : 'Not set'; },

                // Write the pair everywhere: the hidden inputs (the form),
                // the pin (the map) and the text box (the operator).
                set: function (lat, lng, fromMap) {
                    this.lat = lat === '' ? '' : String(round6(Number(lat)));
                    this.lng = lng === '' ? '' : String(round6(Number(lng)));
                    if (this.latInput) { this.latInput.value = this.lat; }
                    if (this.lngInput) { this.lngInput.value = this.lng; }
                    if (this.$refs.text) { this.$refs.text.value = this.has() ? this.lat + ', ' + this.lng : ''; }
                    // The sheet tracks edits through input events on real fields.
                    if (this.latInput) { this.latInput.dispatchEvent(new Event('input', { bubbles: true })); }
                    if (!fromMap) { this.placePin(); }
                },

                clear: function () {
                    this.set('', '');
                    if (this.$refs.link) { this.$refs.link.value = ''; }
                    this.linkNote = '';
                },

                applyText: function (value) {
                    var m = PAIR_RE.exec(value || '');
                    if (!m) {
                        if (!(value || '').trim()) { this.set('', ''); }
                        return;  // the server explains a bad pair on save
                    }
                    this.set(m[1], m[2]);
                    if (this.map && this.has()) { this.map.setView([Number(this.lat), Number(this.lng)], Math.max(this.map.getZoom(), 15)); }
                },

                applyLink: function (value) {
                    var self = this;
                    value = (value || '').trim();
                    this.linkNote = '';
                    if (!value || !parseUrl) { return; }
                    this.linkNote = 'Reading the link…';
                    fetch(parseUrl + '?link=' + encodeURIComponent(value), {
                        credentials: 'same-origin',
                        headers: { 'Accept': 'application/json' }
                    }).then(function (r) { return r.json(); }).then(function (data) {
                        if (data && data.ok) {
                            self.set(data.latitude, data.longitude);
                            self.linkNote = 'Found ' + self.lat + ', ' + self.lng + ' in the link.';
                            // The pair is now in the form; keep the link out of
                            // the save so the server does not fetch it twice.
                            if (self.$refs.link) { self.$refs.link.value = ''; }
                            if (self.map) { self.map.setView([Number(self.lat), Number(self.lng)], Math.max(self.map.getZoom(), 15)); }
                        } else {
                            self.linkNote = (data && data.error) || 'That link could not be read.';
                        }
                    }).catch(function () {
                        self.linkNote = 'That link could not be read. It will be tried again on save.';
                    });
                },

                mount: function () {
                    var self = this;
                    var el = this.$refs.map;
                    if (this.map || !el || !window.L || !document.body.contains(el)) { return; }
                    var start = this.has() ? [Number(this.lat), Number(this.lng)] : [centreLat, centreLng];
                    var zoom = this.has() ? 16 : centreZoom;
                    this.map = L.map(el, { zoomControl: true, attributionControl: false, tap: false }).setView(start, zoom);
                    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
                        maxZoom: 19,
                        attribution: '&copy; OpenStreetMap contributors'
                    }).addTo(this.map);
                    this.map.on('click', function (e) { self.set(e.latlng.lat, e.latlng.lng, true); self.placePin(); });
                    this.placePin();
                    // Inside the pop-up sheet the container has no size until
                    // the open transition ends; without this the tiles render
                    // at zero width.
                    setTimeout(function () { if (self.map) { self.map.invalidateSize(); } }, 250);
                    if (window.ResizeObserver) {
                        this._ro = new ResizeObserver(function () { if (self.map) { self.map.invalidateSize(); } });
                        this._ro.observe(el);
                    }
                },

                placePin: function () {
                    var self = this;
                    if (!this.map) { return; }
                    if (!this.has()) {
                        if (this.marker) { this.map.removeLayer(this.marker); this.marker = null; }
                        return;
                    }
                    var pos = [Number(this.lat), Number(this.lng)];
                    if (this.marker) {
                        this.marker.setLatLng(pos);
                        return;
                    }
                    this.marker = L.marker(pos, { draggable: true, keyboard: false }).addTo(this.map);
                    this.marker.on('dragend', function () {
                        var p = self.marker.getLatLng();
                        self.set(p.lat, p.lng, true);
                    });
                }
            };
        });
    });
})();

;
/* ── js/geo.js ── */
/* Pure geography helpers for the nearest-location chip and the Near you
 * card (LOCATION_MAPPING_PLAN.md, phase 2).
 *
 * A classic browser script that assigns window.YardwayGeo, with a
 * module.exports guard so `node --test static/js/tests` can import it.
 * No DOM, no fetch, no Alpine: everything here is a function of its
 * arguments, which is what makes it testable.
 */
(function (root) {
    'use strict';

    var EARTH_RADIUS_M = 6371000;
    // A reading less precise than this is not a location answer, whatever
    // the device. Desktops locate by wifi or IP and fail this by a mile.
    var MAX_ACCURACY_M = 100;
    // Within this distance of a location's point the chip names it.
    var DEFAULT_NEAR_RADIUS_M = 150;
    // How many "Not here?" alternatives to offer after the closest.
    var ALTERNATIVES = 3;
    // A location opened within this window counts as "last used".
    var LAST_USED_MAX_AGE_MS = 2 * 60 * 60 * 1000;

    function toRad(deg) { return deg * Math.PI / 180; }

    function haversineMetres(lat1, lng1, lat2, lng2) {
        var phi1 = toRad(lat1), phi2 = toRad(lat2);
        var dPhi = toRad(lat2 - lat1);
        var dLambda = toRad(lng2 - lng1);
        var a = Math.sin(dPhi / 2) * Math.sin(dPhi / 2) +
            Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLambda / 2) * Math.sin(dLambda / 2);
        return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
    }

    function passesAccuracyGate(accuracy, maxAccuracy) {
        var limit = typeof maxAccuracy === 'number' ? maxAccuracy : MAX_ACCURACY_M;
        return typeof accuracy === 'number' && isFinite(accuracy) && accuracy >= 0 && accuracy <= limit;
    }

    function hasPoint(item) {
        return item && typeof item.lat === 'number' && typeof item.lng === 'number' &&
            isFinite(item.lat) && isFinite(item.lng);
    }

    // Every location with a point, nearest first, each with its distance.
    function rankByDistance(position, locations) {
        return (locations || []).filter(hasPoint).map(function (loc) {
            return { location: loc, distance: haversineMetres(position.lat, position.lng, loc.lat, loc.lng) };
        }).sort(function (a, b) { return a.distance - b.distance; });
    }

    /* The distance ladder (plan 5.4). Evaluated in order, first match wins:
     *   1  exactly one location within the near radius   → that location
     *   2  two or more within it                          → the closest + up to three others
     *   3  within a site's radius of its centre            → the site
     *   4  anything else                                   → null
     * `locations` are {pk, name, site, lat, lng}; `sites` are
     * {name, lat, lng, radius_m, count}. Archived locations must never be
     * passed in — the server filters them.
     */
    function resolveLadder(position, locations, sites, opts) {
        opts = opts || {};
        var near = typeof opts.nearRadiusM === 'number' ? opts.nearRadiusM : DEFAULT_NEAR_RADIUS_M;
        if (!position || typeof position.lat !== 'number' || typeof position.lng !== 'number') return null;

        var ranked = rankByDistance(position, locations);
        var within = ranked.filter(function (r) { return r.distance <= near; });
        if (within.length === 1) {
            return { step: 1, location: within[0].location, distance: within[0].distance, alternatives: [] };
        }
        if (within.length > 1) {
            return {
                step: 2,
                location: within[0].location,
                distance: within[0].distance,
                alternatives: within.slice(1, 1 + ALTERNATIVES).map(function (r) {
                    return { location: r.location, distance: r.distance };
                })
            };
        }

        var best = null;
        (sites || []).forEach(function (site) {
            if (!hasPoint(site) || !site.name) return;
            var radius = typeof site.radius_m === 'number' ? site.radius_m : 1500;
            var d = haversineMetres(position.lat, position.lng, site.lat, site.lng);
            if (d <= radius && (!best || d < best.distance)) {
                best = { step: 3, site: site, distance: d, count: site.count || 0 };
            }
        });
        return best;
    }

    function formatDistance(metres) {
        if (typeof metres !== 'number' || !isFinite(metres) || metres < 0) return '';
        if (metres < 1000) return Math.round(metres) + ' m';
        return (metres / 1000).toFixed(1) + ' km';
    }

    // The "last used" fallback (plan 5.5): a location opened recently.
    function lastUsedIsFresh(entry, now, maxAgeMs) {
        var age = typeof maxAgeMs === 'number' ? maxAgeMs : LAST_USED_MAX_AGE_MS;
        if (!entry || typeof entry.at !== 'number' || !entry.pk) return false;
        var t = typeof now === 'number' ? now : Date.now();
        return t - entry.at >= 0 && t - entry.at <= age;
    }

    var api = {
        MAX_ACCURACY_M: MAX_ACCURACY_M,
        DEFAULT_NEAR_RADIUS_M: DEFAULT_NEAR_RADIUS_M,
        LAST_USED_MAX_AGE_MS: LAST_USED_MAX_AGE_MS,
        haversineMetres: haversineMetres,
        passesAccuracyGate: passesAccuracyGate,
        rankByDistance: rankByDistance,
        resolveLadder: resolveLadder,
        formatDistance: formatDistance,
        lastUsedIsFresh: lastUsedIsFresh
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    } else {
        root.YardwayGeo = api;
    }
})(typeof window !== 'undefined' ? window : this);

;
/* ── js/near_you.js ── */
/* The nearest-location chip and the shared position (plan phase 2).
 *
 * `nearYou` is the Alpine component on the dashboard header. It never
 * blocks a render and never shows an error: it mounts after the page,
 * asks the Permissions API (no prompt) whether geolocation is granted,
 * offers a quiet "Use my location" button when it would have to ask,
 * discards any reading coarser than 100 m, runs the distance ladder
 * (static/js/geo.js) over the locations the page already carries, and
 * renders one chip — or nothing.
 *
 * Also here, because every page needs it: remembering the last opened
 * location (from a [data-remember-location] marker) for the "Back to …"
 * fallback and the Near you card.
 */
(function () {
    'use strict';

    var LAST_KEY = 'yardway.lastLocation';
    var GRANTED_KEY = 'yardway.geoGranted';
    var DENIED_SESSION_KEY = 'yardway.geoDenied';

    function readJSON(storage, key) {
        try { return JSON.parse(storage.getItem(key)); } catch (err) { return null; }
    }
    function writeJSON(storage, key, value) {
        try { storage.setItem(key, JSON.stringify(value)); } catch (err) { /* private mode, quota */ }
    }

    window.Yardway = window.Yardway || {};

    window.Yardway.rememberLocation = function (pk, name) {
        if (!pk) return;
        writeJSON(localStorage, LAST_KEY, { pk: Number(pk), name: name || '', at: Date.now() });
    };
    window.Yardway.lastLocation = function () {
        var entry = readJSON(localStorage, LAST_KEY);
        return window.YardwayGeo && YardwayGeo.lastUsedIsFresh(entry) ? entry : null;
    };

    function scanRemember() {
        var el = document.querySelector('[data-remember-location]');
        if (el) window.Yardway.rememberLocation(el.dataset.rememberLocation, el.dataset.rememberName);
    }
    document.addEventListener('DOMContentLoaded', scanRemember);
    document.addEventListener('htmx:afterSwap', scanRemember);

    // htmx's history cache snapshots the live DOM, Alpine-expanded x-if and
    // x-for clones and Leaflet panes included, then Alpine initialises that
    // snapshot again on Back and expands everything a second time. A
    // component that renders its own children registers here at init (its
    // markup is still the server's at that point): the markup is kept in an
    // inert <template> inside the component, travels with the snapshot,
    // and is put back when the snapshot is restored, before Alpine runs.
    //
    // It is restored on Back, never reset before the snapshot: resetting
    // the live DOM then detached the very link that issued the boosted
    // request, htmx could no longer read hx-select off its ancestors, and
    // the whole page landed inside #main-content.
    window.Yardway.volatile = function (el) {
        if (!el || el.querySelector(':scope > template[data-pristine]')) return;
        var keep = document.createElement('template');
        keep.setAttribute('data-pristine', '');
        keep.innerHTML = el.innerHTML;
        el.appendChild(keep);
    };
    document.addEventListener('DOMContentLoaded', function () {
        document.body.addEventListener('htmx:historyRestore', function () {
            document.querySelectorAll('template[data-pristine]').forEach(function (keep) {
                var el = keep.parentElement;
                if (!el) return;
                var html = keep.innerHTML;
                el.innerHTML = html;
                window.Yardway.volatile(el);
            });
        });
    });

    // The last position that passed the accuracy gate, shared with the
    // Near you card so the page asks the phone once.
    window.Yardway.position = null;

    document.addEventListener('alpine:init', function () {
        Alpine.data('nearYou', function () {
            return {
                chip: null,        // {kind: 'gps'|'site'|'last', ...}
                askable: false,    // show the "Use my location" button
                showAlternatives: false,
                data: null,

                init: function () {
                    var self = this;
                    window.Yardway.volatile(this.$el);
                    var node = document.getElementById('near-you-data');
                    if (!node || !window.YardwayGeo) return;
                    try { this.data = JSON.parse(node.textContent); } catch (err) { return; }
                    this.renderFallback();
                    // Never block the render: everything below is async.
                    setTimeout(function () { self.start(); }, 0);
                },

                start: function () {
                    var self = this;
                    if (!('geolocation' in navigator)) return;
                    if (!window.isSecureContext) {
                        if (!window.__yardwayInsecureWarned) {
                            window.__yardwayInsecureWarned = true;
                            console.warn('Yardway: location needs HTTPS; the nearest-location chip is off.');
                        }
                        return;
                    }
                    if (!this.data.locations.length && !this.data.sites.length) return;
                    try { if (sessionStorage.getItem(DENIED_SESSION_KEY)) return; } catch (err) { /* ignore */ }
                    // A position the page already has (another component asked) is enough.
                    if (window.Yardway.position) { this.resolve(window.Yardway.position); return; }

                    var granted = false;
                    try { granted = localStorage.getItem(GRANTED_KEY) === '1'; } catch (err) { /* ignore */ }

                    if (navigator.permissions && navigator.permissions.query) {
                        navigator.permissions.query({ name: 'geolocation' }).then(function (status) {
                            // 'prompt' means asking now would pop a dialog, whatever
                            // localStorage remembers (the user may have reset the site's
                            // permission): only the button may do that. 'denied': nothing.
                            if (status.state === 'granted') self.locate();
                            else if (status.state === 'prompt') self.askable = true;
                        }).catch(function () { self.askable = !granted; if (granted) self.locate(); });
                    } else {
                        // No Permissions API (older Safari): the remembered grant is
                        // the only way to know a request will not prompt.
                        if (granted) this.locate(); else this.askable = true;
                    }
                },

                // The button: the one place a permission prompt may come from.
                use: function () {
                    this.askable = false;
                    this.locate();
                },

                locate: function () {
                    var self = this;
                    navigator.geolocation.getCurrentPosition(function (pos) {
                        try { localStorage.setItem(GRANTED_KEY, '1'); } catch (err) { /* ignore */ }
                        if (!YardwayGeo.passesAccuracyGate(pos.coords.accuracy)) return;  // desktop, indoors: nothing
                        var p = { lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy: pos.coords.accuracy, at: Date.now() };
                        window.Yardway.position = p;
                        window.dispatchEvent(new CustomEvent('yardway:position', { detail: p }));
                        self.resolve(p);
                    }, function (err) {
                        if (err && err.code === 1) {
                            try { sessionStorage.setItem(DENIED_SESSION_KEY, '1'); } catch (e) { /* ignore */ }
                        } else {
                            console.debug('Yardway: no position', err && err.message);
                        }
                        // Every failure renders the same thing: nothing new.
                    }, { enableHighAccuracy: true, timeout: 3000, maximumAge: 60000 });
                },

                resolve: function (position) {
                    var d = this.data;
                    var answer = YardwayGeo.resolveLadder(position, d.locations, d.sites, { nearRadiusM: d.near_radius_m });
                    if (!answer) { this.renderFallback(); return; }
                    if (answer.step === 3) {
                        this.chip = {
                            kind: 'site',
                            label: answer.site.name + ' · ' + answer.count + ' location' + (answer.count === 1 ? '' : 's'),
                            href: d.urls.dashboard + '?site=' + encodeURIComponent(answer.site.name),
                            alternatives: []
                        };
                        return;
                    }
                    this.chip = {
                        kind: 'gps',
                        label: answer.location.name + ' · ' + YardwayGeo.formatDistance(answer.distance),
                        href: this.horsesUrl(answer.location.pk),
                        alternatives: answer.alternatives.map(function (a) {
                            return {
                                label: a.location.name + ' · ' + YardwayGeo.formatDistance(a.distance),
                                href: this.horsesUrl(a.location.pk)
                            };
                        }, this)
                    };
                },

                // "Back to <name>": a location opened in the last two hours.
                renderFallback: function () {
                    var last = window.Yardway.lastLocation();
                    if (!last) return;
                    // Only offer what still exists and is not archived.
                    var known = this.data.locations.some(function (l) { return l.pk === last.pk; }) ||
                        (this.data.all_pks || []).indexOf(last.pk) !== -1;
                    if (!known) return;
                    this.chip = { kind: 'last', label: 'Back to ' + last.name, href: this.horsesUrl(last.pk), alternatives: [] };
                },

                horsesUrl: function (pk) {
                    return this.data.urls.horses + '?group_by=location&location=' + pk;
                }
            };
        });
    });
})();

;
/* ── js/split_view.js ── */
/**
 * List split view — the preview pane, and its sheet on a phone. The horse
 * list was the first to use it; the owner and invoice lists use the same
 * script.
 *
 * One selection model drives both shapes:
 *   - From `lg` up the pane is a column beside the list. Arrow keys (or
 *     J/K) step through the rows, Esc closes it.
 *   - Below `lg` the same markup is a bottom sheet that opens at a peek
 *     height. The list above it keeps scrolling — there is no scrim and
 *     nothing is trapped — and dragging the sheet up fills the screen.
 *     Dragging past the top opens the horse's own page.
 *
 * Rows stay ordinary links to the record's page, so the list works with
 * no JavaScript at all; the click handler here is what turns a link into
 * a selection. The preview itself is fetched with htmx into the pane's
 * body, which is also what makes the pop-up sheet's "refresh
 * #main-content" behave: the selection is put back afterwards from
 * sessionStorage.
 *
 * Markup (the horse list's older data-horse-* names still work):
 *
 *   <div data-split-view                      the list and the pane
 *        data-split-pane="owner-preview"      the pane's id; default horse-preview
 *        data-preview-url="/owners/0/preview/">
 *     <tr data-split-row data-split-pk="7">   one row per record
 *       <a data-split-link href="/owners/7/">  the record's own page
 *     ...
 *     <aside id="owner-preview" class="split-view-pane" hidden>
 *       <div id="owner-preview-body"></div>   the server's partial goes here;
 *     </aside>                                its root is #owner-preview-content
 *   </div>
 *
 * The counter, the arrows and "Open full page" are found by id:
 * <pane id>-counter, -prev, -next and -open-full.
 *
 * Tabs. A pane can hold more than one view of a record (the invoice pane:
 * Invoice, Owner, PDF). The partial's root carries x-data="paneTabs('a')"
 * and its buttons call pick('b'). The tab is remembered per page, so the
 * arrows flick through the same view of each record. A link in a row with
 * data-split-tab="b" opens the pane on that tab.
 */
(function (root) {
    'use strict';

    // ── Pure helpers, unit-tested in tests/split_view.test.js ──

    /** Step a selection by `delta`, stopping at both ends of the list. */
    function stepIndex(current, total, delta) {
        if (!total) { return -1; }
        if (current < 0) { return delta > 0 ? 0 : total - 1; }
        var next = current + delta;
        if (next < 0) { return 0; }
        if (next > total - 1) { return total - 1; }
        return next;
    }

    /**
     * Where a drag lands once the finger lifts.
     *
     * `visible` is how much of the sheet is showing, in pixels, at the
     * moment of release. Past the sheet's full height by `overshoot` the
     * answer is 'page': the reader has asked for more than the sheet
     * holds, so the horse's own page is what they get.
     */
    // A release faster than this (px per ms) is a flick.
    var FLICK = 0.45;

    function snapFor(visible, peekHeight, fullHeight, overshoot, velocity, from) {
        if (visible >= fullHeight + (overshoot || 64)) { return 'page'; }
        // A flick goes one step in its direction, however short the drag:
        // waiting for the finger to cross the midpoint is what made the
        // sheet feel heavy. `velocity` is px/ms, up positive; `from` is the
        // state the drag began in.
        if (velocity !== undefined && Math.abs(velocity) >= FLICK) {
            if (velocity > 0) { return 'full'; }
            return from === 'full' ? 'peek' : 'closed';
        }
        var midpoint = (peekHeight + fullHeight) / 2;
        if (visible >= midpoint) { return 'full'; }
        // Dragged well below the peek height: the reader is putting it away.
        if (visible <= peekHeight * 0.6) { return 'closed'; }
        return 'peek';
    }

    /**
     * The vertical offset in a computed `transform`, in pixels.
     *
     * A drag has to start from where the sheet actually is. Reading the
     * class instead would be a guess, and the guess is wrong the moment
     * the finger lands during the settle animation.
     */
    function translateYOf(transformText) {
        if (!transformText || transformText === 'none') { return 0; }
        var m = transformText.match(/matrix3d\(([^)]+)\)/);
        if (m) { return parseFloat(m[1].split(',')[13]) || 0; }
        m = transformText.match(/matrix\(([^)]+)\)/);
        if (m) { return parseFloat(m[1].split(',')[5]) || 0; }
        return 0;
    }

    /**
     * The ids of a pane's parts. Every part is named after the pane, so
     * one page can hold any pane without the script knowing its name.
     */
    function paneIds(paneId) {
        var id = paneId || 'horse-preview';
        return {
            pane: id,
            body: id + '-body',
            content: id + '-content',
            counter: id + '-counter',
            prev: id + '-prev',
            next: id + '-next',
            openFull: id + '-open-full'
        };
    }

    /** The preview URL for one record: the route has a 0 where the key goes. */
    function fillUrl(template, pk) {
        return template ? template.replace('/0/', '/' + pk + '/') : '';
    }

    var api = {
        stepIndex: stepIndex, snapFor: snapFor, translateYOf: translateYOf,
        paneIds: paneIds, fillUrl: fillUrl
    };
    root.YardwaySplitView = api;
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
        return;
    }

    // ── DOM wiring ──

    var DESKTOP = '(min-width: 1024px)';
    var OVERSHOOT = 64;

    function el(id) { return document.getElementById(id); }
    function root_() { return document.querySelector('[data-split-view]'); }
    function ids() {
        var container = root_();
        return paneIds(container ? container.getAttribute('data-split-pane') : '');
    }
    function pane() { return el(ids().pane); }
    function body_() { return el(ids().body); }

    var ROW = '[data-split-row], [data-horse-row]';
    var LINK = '[data-split-link], [data-horse-link]';
    function isDesktop() { return window.matchMedia(DESKTOP).matches; }

    function rows() {
        var container = root_();
        if (!container) { return []; }
        return Array.prototype.slice.call(
            container.querySelectorAll(ROW)
        ).filter(function (row) {
            // A row inside a collapsed group card is not on screen, so it
            // is not somewhere the arrow keys should land.
            return row.offsetParent !== null;
        });
    }

    // A record can have two rows: a card for phones and a table row for
    // wide screens, one of which is always display:none. Take the one on
    // screen — the hidden twin has no offsetParent, and reading the wrong
    // one makes a perfectly good selection look like a stale one.
    function rowsFor(pk) {
        var container = root_();
        if (!container || pk === null || pk === undefined) { return []; }
        return Array.prototype.slice.call(container.querySelectorAll(ROW))
            .filter(function (row) { return pkOf(row) === String(pk); });
    }

    function rowFor(pk) {
        var matches = rowsFor(pk);
        for (var i = 0; i < matches.length; i++) {
            if (matches[i].offsetParent !== null) { return matches[i]; }
        }
        return matches[0] || null;
    }

    function pkOf(row) {
        if (!row) { return null; }
        return row.getAttribute('data-split-pk') || row.getAttribute('data-horse-pk');
    }

    function previewUrl(pk) {
        var container = root_();
        // Django reverses the route with a 0 in it; swap in the real key.
        return fillUrl(container ? container.getAttribute('data-preview-url') : '', pk);
    }

    function detailUrl(pk) {
        var row = rowFor(pk);
        var link = row ? row.querySelector(LINK) : null;
        return link ? link.getAttribute('href') : '';
    }

    // The key the selection is remembered under. Per path, so the horse
    // list and a filtered horse list do not inherit each other's pick.
    function memoryKey() { return 'yardway:split:' + window.location.pathname; }

    function remember(pk) {
        try {
            if (pk) { sessionStorage.setItem(memoryKey(), String(pk)); }
            else { sessionStorage.removeItem(memoryKey()); }
        } catch (err) { /* private mode: the pane just does not persist */ }
    }

    function remembered() {
        try { return sessionStorage.getItem(memoryKey()); } catch (err) { return null; }
    }

    // ── Tabs ──

    function tabKey() { return 'yardway:split-tab:' + window.location.pathname; }

    function rememberedTab() {
        try { return sessionStorage.getItem(tabKey()); } catch (err) { return null; }
    }

    function setTab(name) {
        try { sessionStorage.setItem(tabKey(), name); } catch (err) { /* private mode */ }
        // A pane that is already showing this record switches in place.
        window.dispatchEvent(new CustomEvent('split-tab', { detail: name }));
    }

    document.addEventListener('alpine:init', function () {
        window.Alpine.data('paneTabs', function (first, allowed) {
            return {
                tab: first,
                init: function () {
                    var kept = rememberedTab();
                    if (kept && (!allowed || allowed.indexOf(kept) !== -1)) { this.tab = kept; }
                },
                pick: function (name) { this.tab = name; setTab(name); },
                // From a row link: only a tab this pane has.
                show: function (name) {
                    if (!allowed || allowed.indexOf(name) !== -1) { this.tab = name; }
                }
            };
        });
    });

    var state = { pk: null, sheet: 'closed' };

    // On a phone the preview is a sheet over the list, so the Back
    // button closes it (static/js/overlay_back.js). On a computer it is a
    // pane beside the list, and Back stays a page change.
    function backCloses(on) {
        var overlay = window.Yardway && window.Yardway.overlay;
        if (!overlay) { return; }
        overlay.sync('preview', on && !isDesktop() && state.pk !== null, close, function () {
            return state.pk !== null && !isDesktop() && !!root_();
        });
    }

    // Both twins are marked, not only the one on screen: a rotation
    // swaps which is which without another click to put it right.
    function markRows() {
        var container = root_();
        if (!container) { return; }
        var all = container.querySelectorAll(ROW);
        Array.prototype.forEach.call(all, function (row) {
            var on = state.pk !== null && pkOf(row) === state.pk;
            row.classList.toggle('is-selected', on);
            if (on) { row.setAttribute('aria-current', 'true'); }
            else { row.removeAttribute('aria-current'); }
        });
    }

    function updateChrome() {
        var list = rows();
        var index = -1;
        for (var i = 0; i < list.length; i++) {
            if (pkOf(list[i]) === state.pk) { index = i; break; }
        }
        var names = ids();
        var counter = el(names.counter);
        if (counter) {
            counter.textContent = index < 0 ? '' : (index + 1) + ' of ' + list.length;
        }
        var prev = el(names.prev);
        var next = el(names.next);
        if (prev) { prev.disabled = index <= 0; }
        if (next) { next.disabled = index < 0 || index >= list.length - 1; }
        var full = el(names.openFull);
        if (full) { full.setAttribute('href', detailUrl(state.pk) || '#'); }
    }

    function setSheet(mode) {
        var panel = pane();
        if (!panel) { return; }
        state.sheet = mode;
        panel.classList.remove('is-peek', 'is-full');
        if (mode === 'peek') { panel.classList.add('is-peek'); }
        if (mode === 'full') { panel.classList.add('is-full'); }
        var body = body_();
        if (body) {
            // At peek height the body must not swallow the drag: the whole
            // gesture belongs to the sheet until it is open.
            body.style.overflowY = (mode === 'full' || isDesktop()) ? 'auto' : 'hidden';
        }
    }

    function load(panel) {
        var names = ids();
        window.htmx.ajax('GET', previewUrl(state.pk), {
            source: panel,
            target: '#' + names.body,
            select: '#' + names.content,
            swap: 'innerHTML'
        }).catch(function () { /* said by js/connection.js */ });
    }

    function open(pk, opts) {
        var container = root_();
        var panel = pane();
        if (!container || !panel || !pk) { return; }
        var changed = state.pk !== String(pk);
        state.pk = String(pk);
        container.classList.add('is-open');
        if (panel.hidden) {
            panel.hidden = false;
            // Read layout once so the sheet starts from its closed
            // position: unhidden and moved in the same frame, it has no
            // start to animate from and jumps in instead of sliding up.
            void panel.offsetHeight;
        }
        remember(state.pk);
        if (changed && window.htmx) {
            // The pane is named as the source so htmx reads hx-push-url
            // and hx-select off it rather than off the boosted <body>.
            // `select` is still passed by name: "unset" is an
            // attribute-only word that this API would read as a literal
            // selector, matching nothing and blanking the pane.
            load(panel);
        }
        if (!isDesktop()) { setSheet('peek'); }
        backCloses(true);
        markRows();
        updateChrome();
        if (opts && opts.focusRow) {
            var row = rowFor(state.pk);
            if (row && row.scrollIntoView) {
                row.scrollIntoView({ block: 'nearest' });
            }
        }
    }

    function close() {
        var container = root_();
        var panel = pane();
        state.pk = null;
        remember(null);
        backCloses(false);
        if (container) { container.classList.remove('is-open'); }
        if (panel) {
            setSheet('closed');
            // Wait for the slide out before taking it out of the tree, so
            // the sheet does not vanish mid-animation.
            if (isDesktop()) { panel.hidden = true; }
            else { window.setTimeout(function () { if (!state.pk) { panel.hidden = true; } }, 250); }
        }
        markRows();
        updateChrome();
    }

    function step(delta) {
        var list = rows();
        var index = -1;
        for (var i = 0; i < list.length; i++) {
            if (pkOf(list[i]) === state.pk) { index = i; break; }
        }
        var next = stepIndex(index, list.length, delta);
        if (next < 0 || next === index) { return; }
        open(pkOf(list[next]), { focusRow: true });
    }

    function goToPage() {
        var url = detailUrl(state.pk);
        if (url) { window.location.href = url; }
    }

    // ── Clicks on the list ──

    // Capture, not bubble: hx-boost listens for clicks on <body>, which is
    // an ancestor of every row, so a bubbling listener here would run after
    // htmx had already started navigating to the horse page. Stopping the
    // click on the way down is what keeps the reader on the list.
    document.addEventListener('click', function (e) {
        var container = root_();
        if (!container || !container.contains(e.target)) { return; }
        // Modified clicks are the reader asking for a new tab; leave them.
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) { return; }
        var row = e.target.closest ? e.target.closest(ROW) : null;
        if (!row) { return; }
        // Anything with its own job — an owner link, an action button —
        // keeps it. A link marked data-split-tab opens the pane on that
        // tab instead of leaving the list.
        var own = e.target.closest('a, button, input, label, select, summary, [role="button"]');
        var tab = own ? own.getAttribute('data-split-tab') : null;
        if (own && !tab && !own.matches(LINK)) { return; }
        // With Select on, the row's job is the tick box.
        if (container.classList.contains('is-selecting')) {
            var box = row.querySelector('input[data-split-checkbox], input[data-horse-checkbox]');
            if (!box || e.target === box) { return; }
            e.preventDefault();
            e.stopPropagation();
            box.click();
            return;
        }
        e.preventDefault();
        e.stopPropagation();
        if (tab) { setTab(tab); }
        open(pkOf(row));
    }, true);

    // ── Keyboard ──

    document.addEventListener('keydown', function (e) {
        if (!root_()) { return; }
        var t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
                  t.tagName === 'SELECT' || t.isContentEditable)) { return; }
        // The pop-up sheet owns Escape while it is open.
        var store = window.Alpine && Alpine.store && Alpine.store('popup');
        if (store && store.open) { return; }
        if (e.key === 'Escape') {
            if (state.pk) { e.preventDefault(); close(); }
            return;
        }
        if (e.metaKey || e.ctrlKey || e.altKey) { return; }
        if (e.key === 'ArrowDown' || e.key === 'j' || e.key === 'J') {
            e.preventDefault();
            step(1);
        } else if (e.key === 'ArrowUp' || e.key === 'k' || e.key === 'K') {
            e.preventDefault();
            step(-1);
        }
    });

    // ── Pane chrome ──

    document.addEventListener('click', function (e) {
        var hit = e.target.closest ? e.target.closest('[data-split-action]') : null;
        if (!hit) { return; }
        var action = hit.getAttribute('data-split-action');
        if (action === 'close') { e.preventDefault(); close(); }
        if (action === 'prev') { e.preventDefault(); step(-1); }
        if (action === 'next') { e.preventDefault(); step(1); }
    });

    // ── Dragging the sheet (phones) ──

    // --sheet-peek is a CSS length in whatever unit the stylesheet finds
    // clearest; today it is 34dvh. getPropertyValue hands back that text,
    // and parseFloat("34dvh") is 34 — so the drag believed the sheet
    // peeked at 34 pixels when it peeks at about 290. Every drag began by
    // throwing the sheet down to where those 34 pixels would put it, and
    // filling the screen then asked for a pull longer than the screen.
    // The only honest way to get pixels out of a CSS length is to let the
    // browser lay one out.
    var peekPx = null;

    function peekHeight(panel, full) {
        if (peekPx !== null) { return peekPx; }
        var probe = document.createElement('div');
        probe.style.cssText = 'position:absolute;top:0;left:0;width:0;' +
            'visibility:hidden;pointer-events:none;height:var(--sheet-peek)';
        panel.appendChild(probe);
        peekPx = probe.offsetHeight || Math.round(full / 3);
        panel.removeChild(probe);
        return peekPx;
    }

    // dvh moves with the window, and with a phone's address bar.
    window.addEventListener('resize', function () { peekPx = null; });
    window.addEventListener('orientationchange', function () { peekPx = null; });

    function sheetHeights() {
        var panel = pane();
        if (!panel) { return { peek: 0, full: 0 }; }
        var full = panel.offsetHeight;
        return { peek: peekHeight(panel, full), full: full };
    }

    var drag = null;

    function dragStart(e) {
        var panel = pane();
        if (!panel || isDesktop() || state.sheet === 'closed') { return; }
        var body = body_();
        var inBody = body && body.contains(e.target);
        // While the sheet is open, its body scrolls; only a pull from the
        // very top of that scroll is a drag rather than a scroll.
        if (inBody && state.sheet === 'full' && body.scrollTop > 0) { return; }
        var heights = sheetHeights();
        drag = {
            y: e.touches[0].clientY,
            // Where the sheet is now, not where its class says it should
            // be: a finger landing during the settle animation picks the
            // sheet up where it sees it, with no jump.
            from: heights.full - translateYOf(getComputedStyle(panel).transform),
            heights: heights,
            inBody: inBody,
            moved: false,
            frame: 0,
            state: state.sheet,
            lastY: e.touches[0].clientY,
            lastT: Date.now(),
            velocity: 0
        };
        panel.classList.add('is-dragging');
    }

    function dragMove(e) {
        var panel = pane();
        if (!drag || !panel) { return; }
        var delta = drag.y - e.touches[0].clientY;   // up is positive
        // A downward pull from inside a scrollable body is a scroll, not a
        // drag, unless the sheet is already at peek height.
        if (drag.inBody && state.sheet === 'full' && delta > 0) { return; }
        drag.visible = Math.max(0, drag.from + delta);
        drag.moved = drag.moved || Math.abs(delta) > 4;
        // Speed over the last stretch of the gesture, smoothed, up positive.
        var now = Date.now();
        var y = e.touches[0].clientY;
        var dt = Math.max(1, now - drag.lastT);
        drag.velocity = 0.6 * ((drag.lastY - y) / dt) + 0.4 * drag.velocity;
        drag.lastY = y;
        drag.lastT = now;
        if (drag.moved && e.cancelable) { e.preventDefault(); }
        paint(panel);
    }

    // One write per frame. touchmove reports faster than the screen
    // redraws, and a second write in the same frame is work nobody sees.
    function paint(panel) {
        if (!drag || drag.frame) { return; }
        if (!window.requestAnimationFrame) { place(panel); return; }
        drag.frame = requestAnimationFrame(function () {
            if (!drag) { return; }
            drag.frame = 0;
            place(panel);
        });
    }

    function place(panel) {
        panel.style.transform =
            'translateY(' + Math.max(0, drag.heights.full - drag.visible) + 'px)';
    }

    function dragEnd() {
        var panel = pane();
        if (!drag || !panel) { return; }
        if (drag.frame && window.cancelAnimationFrame) { cancelAnimationFrame(drag.frame); }
        panel.classList.remove('is-dragging');
        panel.style.transform = '';
        var visible = drag.visible;
        var heights = drag.heights;
        var moved = drag.moved;
        // A finger that stopped before lifting is not a flick.
        var velocity = Date.now() - drag.lastT > 80 ? 0 : drag.velocity;
        var from = drag.state;
        drag = null;
        if (!moved || visible === undefined) { return; }
        var landing = snapFor(visible, heights.peek, heights.full, OVERSHOOT, velocity, from);
        if (landing === 'page') { goToPage(); return; }
        if (landing === 'closed') { close(); return; }
        setSheet(landing);
    }

    function bindDrag() {
        var panel = pane();
        if (!panel || panel.dataset.dragBound) { return; }
        panel.dataset.dragBound = '1';
        panel.addEventListener('touchstart', dragStart, { passive: true });
        panel.addEventListener('touchmove', dragMove, { passive: false });
        panel.addEventListener('touchend', dragEnd);
        panel.addEventListener('touchcancel', dragEnd);
    }

    // ── Coming back after a swap ──
    // The pop-up sheet refreshes #main-content in place after a save, and
    // boosted navigations replace it too. Both rebuild the list, so the
    // selection is re-applied from what was remembered — and dropped when
    // the horse is no longer on the page.

    // Alpine initialises the swapped tree in the same tick, and a group
    // card's x-show settles a frame later — so the visibility test below
    // has to wait for that frame or a perfectly good row reads as hidden.
    function restore() {
        if (window.requestAnimationFrame) { requestAnimationFrame(restoreNow); }
        else { restoreNow(); }
    }

    function restoreNow() {
        var container = root_();
        if (!container) { state.pk = null; backCloses(false); return; }
        bindDrag();
        var pk = remembered();
        state.pk = null;
        // Not just "is the row in the page" but "is it on screen": a
        // remembered record can come back inside a collapsed group card,
        // and a pane whose counter and arrows have nothing to count is
        // worse than no pane.
        var back = rowFor(pk);
        if (!pk || !back || back.offsetParent === null) {
            close();
            return;
        }
        state.pk = String(pk);
        container.classList.add('is-open');
        var panel = pane();
        if (panel) {
            panel.hidden = false;
            setSheet(isDesktop() ? 'closed' : 'peek');
        }
        if (window.htmx) {
            // The pane is named as the source so htmx reads hx-push-url
            // and hx-select off it rather than off the boosted <body>.
            // `select` is still passed by name: "unset" is an
            // attribute-only word that this API would read as a literal
            // selector, matching nothing and blanking the pane.
            load(panel);
        }
        backCloses(true);
        markRows();
        updateChrome();
    }

    document.addEventListener('DOMContentLoaded', restore);
    // Back to a list puts the page back as it was left, with the sheet
    // drawn open, but nothing here knew which record it showed.
    document.addEventListener('htmx:historyRestore', restore);
    document.addEventListener('htmx:afterSwap', function (e) {
        if (e.target && (e.target.id === 'main-content' || e.target.id === 'list-results')) {
            restore();
        }
    });
    window.addEventListener('pageshow', function (e) { if (e.persisted) { restore(); } });

    // #main-content is also swapped by hand (base.html's afterMainSwap,
    // used by the pop-up sheet), which fires no htmx event — so hook the
    // chain rather than miss it.
    window.Yardway = window.Yardway || {};
    var previousHook = window.Yardway.afterMainSwap;
    window.Yardway.afterMainSwap = function (main, responseText) {
        if (previousHook) { previousHook(main, responseText); }
        restore();
    };

    // A rotation or a resize across the breakpoint changes which shape the
    // pane is; re-apply the right one.
    window.matchMedia(DESKTOP).addEventListener('change', function () {
        if (!state.pk) { return; }
        setSheet(isDesktop() ? 'closed' : 'peek');
        backCloses(true);
    });
})(typeof window !== 'undefined' ? window : globalThis);

;
/* ── js/horse_step.js ── */
// Previous and next horse with the ← and → keys on the horse page
// (docs/HORSE_PAGE_UX_REVIEW.md E1), as ↑ and ↓ step through the list's
// preview pane (split_view.js). The links are the stepper's rel="prev" and
// rel="next" in templates/horses/horse_detail.html; a click on them goes
// through htmx's boost like a tap does.
(function () {
    'use strict';

    // The key means "step" only when nothing else on the page wants it: not
    // while typing, not inside a menu, a sheet or a scrolling diagram, and
    // not with a modifier (Alt + ← is the browser's Back).
    function stepFor(e) {
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) { return null; }
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') { return null; }
        var t = e.target;
        if (t && t.closest) {
            if (t.closest('input, textarea, select, [contenteditable="true"], [role="menu"], [role="dialog"], .ped-scroll')) {
                return null;
            }
        }
        return e.key === 'ArrowLeft' ? 'prev' : 'next';
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { stepFor: stepFor };
    }
    if (typeof document === 'undefined') { return; }

    document.addEventListener('keydown', function (e) {
        var stepper = document.querySelector('[data-testid="horse-stepper"]');
        if (!stepper) { return; }
        var popup = window.Alpine && Alpine.store && Alpine.store('popup');
        if (popup && popup.open) { return; }
        var rel = stepFor(e);
        if (!rel) { return; }
        var link = stepper.querySelector('a[rel="' + rel + '"]');
        if (!link) { return; }
        e.preventDefault();
        link.click();
    });
})();

;
/* ── js/panel_resize.js ── */
/**
 * Resizable panels on desktop.
 *
 * A host element holds two panels side by side and a bar between them.
 * Drag the bar to give either panel more room. The width is remembered
 * per page (and per host) in localStorage, so it is still there on the
 * next visit. Double-click the bar to go back to the default width. With
 * the bar focused, Left and Right resize by keyboard.
 *
 * Markup:
 *
 *   <div data-resize-host="horse-list"        id for the stored width
 *        data-resize-var="--split-pane-w"     CSS variable that gets the px width
 *        data-resize-panel="#horse-preview"   the panel whose width is set
 *        data-resize-side="right"             which side that panel is on
 *        data-resize-min="336"                the panel's minimum width
 *        data-resize-min-other="448"          the other side's minimum width
 *        data-resize-gap="16">                space between the two sides
 *     ...
 *     <div data-resize-bar role="separator" tabindex="0" ...></div>
 *   </div>
 *
 * While a width is set, the host has the class `has-resize-width`, so the
 * CSS can switch from its default sizing to the variable.
 */
(function (root) {
    'use strict';

    /**
     * The width a drag (or a remembered width) is allowed to take.
     *
     * The panel never gets narrower than `minPanel`, and the other side
     * keeps at least `minOther`, so neither can be squeezed into
     * uselessness. When the host is too narrow for both, the panel keeps
     * its minimum and the other side gives way.
     */
    function clampPanelWidth(width, available, minPanel, minOther) {
        var lo = minPanel || 0;
        var hi = available - (minOther || 0);
        var w = Math.round(Number(width) || 0);
        if (hi < lo) { return lo; }
        if (w < lo) { return lo; }
        if (w > hi) { return hi; }
        return w;
    }

    /** The new panel width for a drag of `dx` pixels (right is positive). */
    function dragWidth(startWidth, dx, side) {
        // A panel on the right grows when the bar moves left; a panel on
        // the left grows when it moves right.
        return side === 'left' ? startWidth + dx : startWidth - dx;
    }

    var api = { clampPanelWidth: clampPanelWidth, dragWidth: dragWidth };
    root.YardwayPanelResize = api;
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
        return;
    }

    // ── DOM wiring ──

    var DESKTOP = '(min-width: 1024px)';
    var KEY_STEP = 32;

    function isDesktop() { return window.matchMedia(DESKTOP).matches; }

    function num(host, name, fallback) {
        var v = parseFloat(host.getAttribute(name));
        return isNaN(v) ? fallback : v;
    }

    function config(host) {
        return {
            id: host.getAttribute('data-resize-host') || 'panel',
            cssVar: host.getAttribute('data-resize-var') || '--panel-w',
            side: host.getAttribute('data-resize-side') === 'left' ? 'left' : 'right',
            min: num(host, 'data-resize-min', 320),
            minOther: num(host, 'data-resize-min-other', 320),
            gap: num(host, 'data-resize-gap', 16)
        };
    }

    function panelOf(host) {
        var sel = host.getAttribute('data-resize-panel');
        return sel ? host.querySelector(sel) : null;
    }

    function key(host) {
        return 'yardway:panel-width:' + window.location.pathname + ':' + config(host).id;
    }

    function stored(host) {
        try { return parseInt(localStorage.getItem(key(host)), 10) || null; }
        catch (err) { return null; }
    }

    function store(host, px) {
        try {
            if (px) { localStorage.setItem(key(host), String(px)); }
            else { localStorage.removeItem(key(host)); }
        } catch (err) { /* private mode: the width lasts for this page only */ }
    }

    function clampFor(host, px) {
        var c = config(host);
        return clampPanelWidth(px, host.clientWidth - c.gap, c.min, c.minOther);
    }

    function setWidth(host, px) {
        var c = config(host);
        if (px) {
            host.style.setProperty(c.cssVar, px + 'px');
            host.classList.add('has-resize-width');
        } else {
            host.style.removeProperty(c.cssVar);
            host.classList.remove('has-resize-width');
        }
        var bar = host.querySelector('[data-resize-bar]');
        var panel = panelOf(host);
        if (bar) {
            bar.setAttribute('aria-valuemin', String(c.min));
            var now = px || (panel ? Math.round(panel.getBoundingClientRect().width) : 0);
            if (now) { bar.setAttribute('aria-valuenow', String(now)); }
        }
    }

    function applyStored(host) {
        var px = stored(host);
        // A hidden host (display:none below lg) has no width to clamp
        // against; apply the stored value as is and clamp it once shown.
        if (px && host.clientWidth) { px = clampFor(host, px); }
        setWidth(host, px);
    }

    function applyAll() {
        var hosts = document.querySelectorAll('[data-resize-host]');
        Array.prototype.forEach.call(hosts, applyStored);
    }

    function barOf(e) {
        return e.target && e.target.closest ? e.target.closest('[data-resize-bar]') : null;
    }

    var drag = null;

    document.addEventListener('pointerdown', function (e) {
        var bar = barOf(e);
        if (!bar || e.button !== 0 || !isDesktop()) { return; }
        var host = bar.closest('[data-resize-host]');
        var panel = host && panelOf(host);
        if (!panel) { return; }
        e.preventDefault();
        drag = {
            host: host,
            side: config(host).side,
            startX: e.clientX,
            startW: panel.getBoundingClientRect().width,
            width: null
        };
        if (bar.setPointerCapture) { bar.setPointerCapture(e.pointerId); }
        document.documentElement.classList.add('is-resizing-panel');
    });

    document.addEventListener('pointermove', function (e) {
        if (!drag) { return; }
        var px = clampFor(drag.host, dragWidth(drag.startW, e.clientX - drag.startX, drag.side));
        drag.width = px;
        setWidth(drag.host, px);
    });

    function endDrag() {
        if (!drag) { return; }
        if (drag.width) { store(drag.host, drag.width); }
        drag = null;
        document.documentElement.classList.remove('is-resizing-panel');
    }
    document.addEventListener('pointerup', endDrag);
    document.addEventListener('pointercancel', endDrag);

    document.addEventListener('dblclick', function (e) {
        var bar = barOf(e);
        var host = bar && bar.closest('[data-resize-host]');
        if (!host) { return; }
        store(host, null);
        setWidth(host, null);
    });

    document.addEventListener('keydown', function (e) {
        var bar = barOf(e);
        if (!bar) { return; }
        var host = bar.closest('[data-resize-host]');
        var panel = host && panelOf(host);
        if (!panel) { return; }
        var dx = e.key === 'ArrowLeft' ? -KEY_STEP : e.key === 'ArrowRight' ? KEY_STEP : 0;
        if (!dx) { return; }
        e.preventDefault();
        var px = clampFor(host, dragWidth(panel.getBoundingClientRect().width, dx, config(host).side));
        setWidth(host, px);
        store(host, px);
    });

    // Apply the stored widths on load and whenever htmx or the pop-up
    // sheet puts new markup in the page (boosted navigation included).
    document.addEventListener('DOMContentLoaded', applyAll);
    document.addEventListener('htmx:afterSettle', applyAll);
    window.addEventListener('pageshow', function (e) { if (e.persisted) { applyAll(); } });
    window.Yardway = window.Yardway || {};
    var previousHook = window.Yardway.afterMainSwap;
    window.Yardway.afterMainSwap = function (main, responseText) {
        if (previousHook) { previousHook(main, responseText); }
        applyAll();
    };

    // A narrower window can leave a stored width too wide for the other
    // side to stay usable; clamp it again rather than squash that side.
    window.addEventListener('resize', function () { if (!drag) { applyAll(); } });
})(typeof window !== 'undefined' ? window : globalThis);

;
/* ── js/map_layout.js ── */
/* Badge layout for the location map (static/js/location_map.js).
 *
 * Pure functions, no DOM, so `node --test static/js/tests` can check them
 * the way static/js/geo.js is checked. Written as a classic browser script
 * that assigns window.YardwayMapLayout, with a module.exports guard.
 *
 * The map draws every badge at its server-computed anchor. When two
 * anchors are closer together than a badge is wide, the badges would
 * cover each other and the counts could not be read, which is what the
 * map is for. spreadBadges() pushes overlapping badges apart, a little at
 * a time over a few passes, until none overlap and none is cut off by the
 * edge of the map. The caller draws a leader line from each moved badge
 * back to its anchor, so a badge never seems to stand for the wrong location.
 */
(function (root) {
    'use strict';

    // How much of a separation badge `a` takes: none when it is the
    // highlighted badge (that one stays on its anchor), all of it when the
    // other is; otherwise the lighter badge (fewer horses) moves more, so
    // the counts that matter most stay closest to their locations.
    function share(a, b) {
        if (a.fixed) return 0;
        if (b.fixed) return 1;
        if (a.count === b.count) return 0.5;
        return a.count < b.count ? 0.75 : 0.25;
    }

    /**
     * spreadBadges(items, opts) → [{x, y}] in the same order as `items`.
     *
     * items  [{x, y, count, fixed, r}] — anchor position in container
     *        pixels, the horse count, whether the badge may move (fixed =
     *        no), and an optional radius. The radius defaults to half the
     *        badge size; a caller can add small fixed items as obstacles
     *        (the map does this for the highlighted location's name) so
     *        badges keep clear of them too.
     * opts   size    badge diameter in px (44)
     *        gap     clear space wanted between two badges (4)
     *        width   container width in px; badges are kept inside
     *        height  container height in px
     *        passes  relaxation passes (30); each pass separates every
     *                overlapping pair once, and the loop stops early when
     *                a pass moves nothing. A cluster clamped into a corner
     *                needs the most.
     */
    function spreadBadges(items, opts) {
        opts = opts || {};
        var size = opts.size || 44;
        var gap = opts.gap == null ? 4 : opts.gap;
        var passes = opts.passes || 30;
        var width = opts.width, height = opts.height;

        function clamp(p) {
            if (width != null) p.x = Math.min(Math.max(p.x, p.r), Math.max(p.r, width - p.r));
            if (height != null) p.y = Math.min(Math.max(p.y, p.r), Math.max(p.r, height - p.r));
        }

        var out = items.map(function (it) {
            var p = {
                x: Number(it.x) || 0, y: Number(it.y) || 0, count: Number(it.count) || 0,
                fixed: !!it.fixed, r: it.r != null ? Number(it.r) : size / 2
            };
            clamp(p);
            return p;
        });

        for (var pass = 0; pass < passes; pass++) {
            var moved = false;
            for (var i = 0; i < out.length; i++) {
                for (var j = i + 1; j < out.length; j++) {
                    var a = out[i], b = out[j];
                    if (a.fixed && b.fixed) continue;
                    var minD = a.r + b.r + gap;
                    var dx = b.x - a.x, dy = b.y - a.y;
                    var d = Math.sqrt(dx * dx + dy * dy);
                    if (d >= minD) continue;
                    if (d < 1e-6) { dx = 0; dy = 1; d = 1; }   // same anchor: part them vertically
                    var push = minD - d;
                    var ux = dx / d, uy = dy / d;
                    var wa = share(a, b);
                    a.x -= ux * push * wa;
                    a.y -= uy * push * wa;
                    b.x += ux * push * (1 - wa);
                    b.y += uy * push * (1 - wa);
                    clamp(a);
                    clamp(b);
                    moved = true;
                }
            }
            if (!moved) break;
        }
        return out.map(function (p) { return { x: p.x, y: p.y }; });
    }

    // Does a circle (a badge) reach into a box (a name)? Nearest point of
    // the box to the centre, then the distance to it.
    function circleHitsBox(cx, cy, r, box) {
        var nx = Math.max(box.x, Math.min(cx, box.x + box.w));
        var ny = Math.max(box.y, Math.min(cy, box.y + box.h));
        var dx = cx - nx, dy = cy - ny;
        return dx * dx + dy * dy < r * r;
    }

    function boxesOverlap(a, b) {
        return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    }

    var api = { spreadBadges: spreadBadges, circleHitsBox: circleHitsBox, boxesOverlap: boxesOverlap };
    root.YardwayMapLayout = api;
    if (typeof module !== 'undefined' && module.exports) { module.exports = api; }
})(typeof window !== 'undefined' ? window : globalThis);

;
/* ── js/location_map.js ── */
/* The location map (templates/partials/location_map.html) and the Near
 * you dashboard card (templates/partials/dashboard/near_you.html).
 *
 * `locationMap` draws one site: a Leaflet map with no tile layer (one flat
 * surface tone — plan 2.3), a FeatureGroup of shapes, and the server-
 * rendered badges positioned above it at their server-computed anchors.
 * The rendering rule is written once, in draw():
 *     if boundary → polygon; else if point → circle sized by capacity.
 * Colour comes from the payload (capacity state) — the shapes never
 * choose their own.
 *
 * `nearYouCard` decides which site the card shows and which location it
 * lights up, from two independent ladders (plan 6.7), and tells the map.
 *
 * Both are safe to run twice and re-initialise after an hx-boost swap:
 * Alpine creates them for the swapped-in markup and destroys the old ones.
 */
(function () {
    'use strict';

    var BADGE_PX = 44;
    var BADGE_GAP = 4;             // clear space kept between two badges
    var LEADER_MIN_PX = 6;         // a badge pushed further than this off its anchor gets a leader line
    var LABEL_H = 20;              // a name label's height (text-xs, leading-4, py-0.5)
    var COMPACT_LOCATIONS = 4;       // the compact crop around a highlight, on a phone
    var COMPACT_LOCATIONS_WIDE = 8;  // ... and on a map at least COMPACT_WIDE_PX wide
    var COMPACT_WIDE_PX = 480;
    // How much fits depends on the map's size. One badge per BADGE_CELL
    // square keeps counts readable; one name per LABEL_CELL_W × LABEL_CELL_H
    // keeps names apart. The compact card is a glance at where the horses
    // are: past its room (never fewer than COMPACT_BADGE_MIN) it drops the
    // rings of empty locations, their shapes staying drawn.
    var BADGE_CELL = 110;
    var LABEL_CELL_W = 170, LABEL_CELL_H = 90;
    var COMPACT_BADGE_MIN = 6;
    var FIT_PADDING = [36, 36];
    var LABEL_HIDE_BELOW = 1.25;   // zoom levels under the fit zoom at which names hide
    // A dense site (more badges than this) shows names only once zoomed in
    // a level, and until then only the badges of locations that hold
    // horses: at the fit zoom ponds, woods and rested fields would bury
    // the capacity rings, which are what the map is for. Their shapes stay
    // drawn and tappable.
    var LABEL_DENSE_COUNT = 12;
    var STROKE_WEIGHT = 2.5;
    // The base map behind the shapes: OpenStreetMap, muted by CSS so the
    // river, woods, tracks and buildings read as context under the parcels
    // (the same tiles the coordinate picker uses). Off by default on the
    // dashboard card, on by default on the Map tab; the last choice on each
    // is remembered on this device.
    var TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
    var TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
    var TILE_KEY = 'yardway.mapTiles.';
    var FILL_PLAIN = 0.2;
    var FILL_OVER_TILES = 0.12;

    function haversine(a, b) {
        return window.YardwayGeo ? YardwayGeo.haversineMetres(a[0], a[1], b[0], b[1]) : Infinity;
    }

    document.addEventListener('alpine:init', function () {
        Alpine.data('locationMap', function (opts) {
            opts = opts || {};
            return {
                variant: opts.variant || 'full',
                // Whether a tap opens the location's page or hands it to
                // the board beside the map. The phone always picks; the
                // desktop board asks for it, and the standalone map does
                // not, so its badges stay ordinary links.
                picks: opts.picks !== undefined ? !!opts.picks : opts.variant === 'phone',
                highlight: opts.highlight || null,
                tiles: false,     // the base map is showing
                payload: null,
                map: null,
                group: null,
                layers: {},       // pk → Leaflet layer
                bounds: {},       // pk → LatLngBounds (known without a map view)
                badges: {},       // pk → <a>
                labels: {},       // pk → <span>
                leaders: {},      // pk → <g> (line + dot back to the anchor)
                visible: null,    // set of pks shown (compact crop) or null for all
                fitZoom: null,

                init: function () {
                    var self = this;
                    if (window.Yardway && Yardway.volatile) { Yardway.volatile(this.$el); }
                    var node = this.$el.querySelector('script[type="application/json"]');
                    if (!node) return;
                    try { this.payload = JSON.parse(node.textContent); } catch (err) { return; }
                    this.$el.querySelectorAll('[data-map-badge]').forEach(function (a) { self.badges[a.dataset.mapBadge] = a; });
                    this.$el.querySelectorAll('[data-map-label]').forEach(function (s) { self.labels[s.dataset.mapLabel] = s; });
                    if (window.Yardway && Yardway.ensureLeaflet) { Yardway.ensureLeaflet(); }
                    if (window.L) {
                        this.$nextTick(function () { self.mount(); });
                    } else {
                        window.addEventListener('leaflet:ready', function () { self.mount(); }, { once: true });
                    }
                    // The Near you card re-aims the map once it knows where you are.
                    this._onFocus = function (e) {
                        if (!e.detail || e.detail.site !== self.$el.dataset.site) return;
                        self.focus(e.detail.highlight);
                    };
                    window.addEventListener('yardway:map-focus', this._onFocus);
                    // The board beside the map points at a location by
                    // hovering its row; the shape has to answer, or the
                    // two lists read as unrelated.
                    this._onHover = function (e) {
                        if (!e.detail || e.detail.site !== self.$el.dataset.site) return;
                        self.highlightShape(e.detail.pk);
                    };
                    window.addEventListener('yardway:map-hover', this._onHover);
                    this._onResize = function () { self.sizeChanged(); };
                    window.addEventListener('resize', this._onResize);
                },

                destroy: function () {
                    if (this._sizeWatch) { this._sizeWatch.disconnect(); this._sizeWatch = null; }
                    window.removeEventListener('yardway:map-focus', this._onFocus);
                    window.removeEventListener('yardway:map-hover', this._onHover);
                    window.removeEventListener('resize', this._onResize);
                    this.unmount();
                },

                // ── The base map ──
                // On unless this person switched it off: the shapes read
                // better with the ground under them, on the dashboard card
                // and the phone map as much as on the full map.
                tilesWanted: function () {
                    var fallback = true;
                    try {
                        var saved = localStorage.getItem(TILE_KEY + this.variant);
                        if (saved === '1') return true;
                        if (saved === '0') return false;
                    } catch (err) { /* private mode */ }
                    return fallback;
                },

                toggleTiles: function () {
                    this.setTiles(!this.tiles);
                    try { localStorage.setItem(TILE_KEY + this.variant, this.tiles ? '1' : '0'); } catch (err) { /* ignore */ }
                },

                setTiles: function (on) {
                    var self = this;
                    this.tiles = !!on;
                    if (!this.map) return;
                    if (this.tiles) {
                        if (!this._tileLayer) {
                            this._tileLayer = L.tileLayer(TILE_URL, { maxNativeZoom: 19, maxZoom: 21, attribution: TILE_ATTRIBUTION });
                        }
                        this._tileLayer.addTo(this.map);
                        if (!this._attribution) this._attribution = L.control.attribution({ prefix: false });
                        this._attribution.addTo(this.map);
                    } else {
                        if (this._tileLayer) this.map.removeLayer(this._tileLayer);
                        if (this._attribution) this._attribution.remove();
                    }
                    // Lighter fills over the map so what is under a parcel shows.
                    var fill = this.tiles ? FILL_OVER_TILES : FILL_PLAIN;
                    Object.keys(this.layers).forEach(function (pk) {
                        var layer = self.layers[pk];
                        if (layer && layer.setStyle) layer.setStyle({ fillOpacity: fill });
                    });
                },

                mount: function () {
                    var self = this;
                    var el = this.$refs.map;
                    if (this.map || !el || !window.L || !document.body.contains(el)) return;
                    // The Locations page carries both shapes of itself, and
                    // one of them is always display:none. Mounting the hidden
                    // one would build a second Leaflet map and, on the phone,
                    // fetch a screen of tiles nobody can see — so wait until
                    // the container has a size, and let the observer below
                    // mount it if the window ever changes its mind.
                    this.watchForSize(el);
                    if (!el.clientWidth || !el.clientHeight) { return; }
                    var full = this.variant === 'full';
                    // Two ways in. With a mouse or trackpad (a fine pointer)
                    // both maps are for looking: drag to move, +/- and a double
                    // click to zoom, the wheel to zoom — on the Map tab as it
                    // comes, on the dashboard card with Ctrl (⌘) held, so a
                    // scroll down the dashboard never sticks on the map (a
                    // trackpad pinch arrives as a Ctrl wheel, so it just
                    // works). On a touch screen one finger scrolls the page;
                    // on the Map tab two fingers move and zoom the map, and
                    // the dashboard card opens the Map tab on a tap.
                    // The phone map is different: it is the whole page, with
                    // no page under it to scroll, so one finger moves it and
                    // a pinch zooms it, as in any maps app.
                    var coarse = !!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);
                    var fine = !coarse;
                    var phone = this.variant === 'phone';
                    this.map = L.map(el, {
                        attributionControl: false,
                        zoomControl: full || fine,
                        dragging: fine || phone,
                        scrollWheelZoom: fine,
                        touchZoom: full || phone,
                        doubleClickZoom: full || fine || phone,
                        boxZoom: false,
                        keyboard: full,
                        tap: false,
                        zoomSnap: 0.25,
                        maxZoom: 21,
                        minZoom: 3
                    });
                    this.setTiles(this.tilesWanted());
                    if (fine && !full) {
                        // Plain wheel: the page scrolls and a hint says how to
                        // zoom. Leaflet's own wheel listener sits on this element
                        // in the bubble phase; stopping the event here, in the
                        // capture phase, keeps it from zooming.
                        var mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '\u2318' : 'Ctrl';
                        el.addEventListener('wheel', function (e) {
                            if (e.ctrlKey || e.metaKey) return;
                            e.stopImmediatePropagation();
                            self.hint('Hold ' + mod + ' and scroll to zoom the map');
                        }, { capture: true, passive: true });
                    }
                    if (full && coarse) {
                        el.addEventListener('touchstart', function (e) {
                            if (e.touches.length === 1) self.hint('Use two fingers to move the map');
                        }, { passive: true });
                    }
                    if (full) {
                        // A tap outside every shape opens the Locations list. It
                        // waits a beat so a double click still zooms instead;
                        // shape and badge taps never reach here (stopped below,
                        // and badges sit outside Leaflet's container).
                        this.map.on('click', function () {
                            clearTimeout(self._bgTimer);
                            self._bgTimer = setTimeout(function () {
                                if (self.payload.urls && self.payload.urls.list) window.location.assign(self.payload.urls.list);
                            }, 300);
                        });
                        this.map.on('dblclick', function () { clearTimeout(self._bgTimer); });
                    }
                    if (this.picks) {
                        // Tapping bare ground puts the field detail away,
                        // rather than leaving the page as the full map does.
                        this.map.on('click', function () {
                            window.dispatchEvent(new CustomEvent('yardway:map-pick', {
                                detail: { site: self.$el.dataset.site, pk: null }
                            }));
                        });
                    }
                    this.group = L.featureGroup().addTo(this.map);
                    this.draw();
                    // Badges are DOM outside Leaflet's panes, so they must be
                    // moved by hand. During a drag every frame is a light pass
                    // (same badges and names, new positions). An animated zoom
                    // fires zoomanim once, with where it will end: the badges
                    // are sent there at once and CSS carries them along Leaflet's
                    // own curve, so they arrive with the shapes instead of
                    // jumping after them. zoomend and moveend decide afresh what
                    // fits; a resize does too.
                    this.map.on('zoomanim', function (e) {
                        self._anim = e;
                        self.$el.classList.add('is-zooming');
                        self.place(true);
                    });
                    this.map.on('zoomend moveend', function () {
                        self._anim = null;
                        self.$el.classList.remove('is-zooming');
                        self.place();
                    });
                    this.map.on('move zoom viewreset', function () { if (!self._anim) self.place(true); });
                    this.map.on('resize', function () { self.place(); });
                    if (window.ResizeObserver) {
                        // A compact map inside a hidden card (the Near you card shows
                        // one site at a time) has no size to fit to; fit once it appears.
                        this._ro = new ResizeObserver(function () {
                            if (!self.map) return;
                            self.map.invalidateSize();
                            if (self._needsFit && el.clientWidth > 0) { self.focus(self.highlight); }
                        });
                        this._ro.observe(el);
                    }
                    this.focus(this.highlight);
                },

                // Follow the container's size for the life of the component:
                // build the map when CSS gives the container a size, take it
                // down when CSS takes the size away again. The second half
                // matters because both shapes of the Locations page ship in
                // one response — a window dragged across the breakpoint hands
                // the map from one shell to the other, and without a teardown
                // the shell that lost it kept a live Leaflet map at 0x0 for
                // the rest of the session.
                watchForSize: function (el) {
                    if (this._sizeWatch || !window.ResizeObserver) return;
                    var self = this;
                    this._sizeWatch = new ResizeObserver(function () {
                        var sized = !!(el.clientWidth && el.clientHeight);
                        if (sized && !self.map) { self.mount(); }
                        else if (!sized && self.map) { self.unmount(); }
                    });
                    this._sizeWatch.observe(el);
                },

                // Which shell has the screen is a question of window width,
                // and an element CSS has just set to display:none has no box
                // to report a change from — so the window is asked directly.
                // This is what hands the map over when a window is dragged
                // across the breakpoint, in both directions.
                sizeChanged: function () {
                    var el = this.$refs.map;
                    if (!el || !window.L) return;
                    var sized = !!(el.clientWidth && el.clientHeight);
                    if (sized && !this.map) { this.mount(); }
                    else if (!sized && this.map) { this.unmount(); }
                },

                // Take the Leaflet map down but keep the component. Every
                // field mount() sets is cleared, so the next mount() starts
                // from the same place a first one does; the badges are DOM of
                // our own and stay where they are, hidden with the shell,
                // until the next focus() puts them back.
                unmount: function () {
                    clearTimeout(this._bgTimer);
                    if (this._ro) { this._ro.disconnect(); this._ro = null; }
                    if (this.map) { this.map.remove(); this.map = null; }
                    this.group = null;
                    this._tileLayer = null;
                    this._attribution = null;
                    this._anim = null;
                    this.fitZoom = null;
                },

                // A short notice over the map (how to move it on a phone).
                hint: function (text) {
                    var self = this;
                    var node = this.$refs.hint;
                    if (!node) return;
                    node.textContent = text;
                    node.hidden = false;
                    clearTimeout(this._hintTimer);
                    this._hintTimer = setTimeout(function () { node.hidden = true; }, 1600);
                },

                // The rendering rule, once.
                draw: function () {
                    var self = this;
                    var full = this.variant === 'full';
                    // The phone map picks rather than navigates: its tap
                    // opens the field in the sheet beside it, and the
                    // sheet is what static/js/locations_mobile.js drives.
                    var picks = this.picks;
                    var interactive = full || picks;
                    this.payload.locations.forEach(function (loc) {
                        // On the phone the colour answers the rotation —
                        // grazing, over, rested, recovering — not capacity
                        // alone, so the map and the list say the same thing.
                        var colour = (picks && loc.rest_colour) ? loc.rest_colour : loc.colour;
                        var style = { color: colour, weight: STROKE_WEIGHT, fillColor: colour, fillOpacity: self.tiles ? FILL_OVER_TILES : FILL_PLAIN, opacity: 1 };
                        var layer = null;
                        if (loc.boundary) {
                            layer = L.geoJSON(loc.boundary, { style: style, interactive: interactive });
                        } else if (loc.lat != null && loc.lng != null && loc.is_barn) {
                            // A barn is a building: a square, so the eye
                            // tells it from ground at a glance. Same point,
                            // same size as the circle it would have been.
                            layer = L.rectangle(
                                L.latLng(loc.lat, loc.lng).toBounds((loc.radius_m || 15) * 2),
                                Object.assign({ interactive: interactive }, style)
                            );
                        } else if (loc.lat != null && loc.lng != null) {
                            layer = L.circle([loc.lat, loc.lng], Object.assign({ radius: loc.radius_m || 25, interactive: interactive }, style));
                        }
                        if (!layer) return;   // no point: draw nothing, not even a placeholder
                        if (interactive) {
                            layer.on('click', function (e) {
                                L.DomEvent.stopPropagation(e);   // not a background tap
                                if (picks) {
                                    // Leaflet layers are not in the page's
                                    // DOM, so there is nothing to delegate
                                    // from: the sheet is told by event.
                                    window.dispatchEvent(new CustomEvent('yardway:map-pick', {
                                        detail: { site: self.$el.dataset.site, pk: loc.pk }
                                    }));
                                    return;
                                }
                                window.location.assign(loc.urls.detail);
                            });
                        }
                        // Leaflet only attaches layers once the map has a view, and a
                        // circle's getBounds() needs the map, so keep bounds of our own.
                        self.bounds[loc.pk] = loc.boundary
                            ? layer.getBounds()
                            : L.latLng(loc.lat, loc.lng).toBounds((loc.radius_m || 25) * 2);
                        layer.addTo(self.group);
                        self.layers[loc.pk] = layer;
                    });
                },

                // Which locations to show, and where to look.
                focus: function (highlight) {
                    if (!this.map) return;
                    this.highlight = highlight || null;
                    this._decided = null;
                    var self = this;
                    Object.keys(this.badges).forEach(function (pk) {
                        self.badges[pk].classList.toggle('is-highlight', String(pk) === String(self.highlight));
                    });
                    this.visible = null;
                    var el = this.$refs.map;
                    if (this.variant === 'compact' && this.highlight) {
                        var target = this.payload.locations.filter(function (l) { return l.pk === self.highlight && l.anchor; })[0];
                        if (target) {
                            var crop = el && el.clientWidth >= COMPACT_WIDE_PX ? COMPACT_LOCATIONS_WIDE : COMPACT_LOCATIONS;
                            var nearest = this.payload.locations.filter(function (l) { return l.anchor; })
                                .sort(function (a, b) { return haversine(target.anchor, a.anchor) - haversine(target.anchor, b.anchor); })
                                .slice(0, crop);
                            this.visible = {};
                            nearest.forEach(function (l) { self.visible[l.pk] = true; });
                        }
                    }
                    var bounds = L.latLngBounds([]);
                    Object.keys(this.layers).forEach(function (pk) {
                        var show = !self.visible || self.visible[pk];
                        var layer = self.layers[pk];
                        if (show) {
                            if (!self.group.hasLayer(layer)) self.group.addLayer(layer);
                            bounds.extend(self.bounds[pk]);
                        } else if (self.group.hasLayer(layer)) {
                            self.group.removeLayer(layer);
                        }
                    });
                    // fitBounds on a 0×0 container (display:none) computes a nonsense
                    // zoom; remember to fit when the ResizeObserver sees a size.
                    this._needsFit = !el || el.clientWidth === 0 || el.clientHeight === 0;
                    if (bounds.isValid() && !this._needsFit) {
                        this.map.invalidateSize();
                        this.map.fitBounds(bounds, Object.assign(
                            { animate: false }, this.fitPadding(el)
                        ));
                        this.fitZoom = this.map.getZoom();
                    }
                    this.place();
                },

                // Lift one shape out of the rest: a heavier stroke and a
                // fuller wash, so the eye finds it without the colour
                // changing meaning.
                highlightShape: function (pk) {
                    var self = this;
                    Object.keys(this.layers).forEach(function (key) {
                        var layer = self.layers[key];
                        var on = String(key) === String(pk);
                        if (!layer.setStyle) { return; }
                        layer.setStyle({
                            weight: on ? STROKE_WEIGHT + 2 : STROKE_WEIGHT,
                            fillOpacity: on
                                ? (self.tiles ? FILL_OVER_TILES + 0.14 : FILL_PLAIN + 0.16)
                                : (self.tiles ? FILL_OVER_TILES : FILL_PLAIN),
                        });
                    });
                    Object.keys(this.badges).forEach(function (key) {
                        self.badges[key].classList.toggle('is-hover', String(key) === String(pk));
                    });
                },

                // How much of the container the shapes must keep clear.
                // On the phone the sheet lies over the bottom of the map,
                // so a plain inset would fit the yard into ground nobody
                // can see. The sheet's real edge is measured rather than
                // repeated as a number here.
                fitPadding: function (el) {
                    if (this.variant !== 'phone' || !el) { return { padding: FIT_PADDING }; }
                    var box = el.getBoundingClientRect();
                    var sheet = document.querySelector('[data-loc-sheet]');
                    var bottom = sheet ? box.bottom - sheet.getBoundingClientRect().top : 0;
                    var top = FIT_PADDING[1];
                    bottom = Math.max(FIT_PADDING[1], bottom + 12);
                    // A sheet pulled up to full would leave no band at all.
                    if (top + bottom > box.height * 0.7) { return { padding: FIT_PADDING }; }
                    return {
                        paddingTopLeft: [FIT_PADDING[0], Math.round(top)],
                        paddingBottomRight: [FIT_PADDING[0], Math.round(bottom)]
                    };
                },

                // Where a lat/lng lands in the container: now, or — during an
                // animated zoom — where it will land when the zoom ends.
                projector: function () {
                    var map = this.map, a = this._anim;
                    if (!a) return function (ll) { return map.latLngToContainerPoint(ll); };
                    var half = map.getSize().divideBy(2);
                    var centre = map.project(a.center, a.zoom);
                    return function (ll) { return map.project(ll, a.zoom).subtract(centre).add(half); };
                },

                // Badges follow their anchors. Overlapping badges are pushed
                // apart (static/js/map_layout.js) and a moved badge keeps a
                // leader line back to its anchor. Names sit under the badges.
                // A light pass (mid-drag, mid-zoom) keeps the last decision on
                // which badges and names show and only moves them; a full pass
                // decides afresh for the size and zoom the map has now.
                place: function (light) {
                    // Nothing to place until the first fit gave the map a view
                    // (a hidden compact map has none; Leaflet throws otherwise).
                    if (!this.map || this.fitZoom == null) return;
                    var self = this;
                    var compact = this.variant === 'compact';
                    var isHighlight = function (pk) { return String(pk) === String(self.highlight); };
                    var size = this.map.getSize();
                    var project = this.projector();
                    if (!light || !this._decided) {
                        var inView = Object.keys(this.badges).filter(function (pk) { return !self.visible || self.visible[pk]; });
                        var zoom = this._anim ? this._anim.zoom : this.map.getZoom();
                        var zoomedOut = zoom < this.fitZoom + 1;
                        // Room at this size: how many badges, and how many names,
                        // can sit on the map without burying each other.
                        var badgeRoom = Math.floor(size.x / BADGE_CELL) * Math.floor(size.y / BADGE_CELL);
                        var labelRoom = Math.floor(size.x / LABEL_CELL_W) * Math.floor(size.y / LABEL_CELL_H);
                        // Dense: drop the badges of locations that hold no horses.
                        var dense = inView.length > LABEL_DENSE_COUNT && zoomedOut;
                        // Crowded (compact only): drop the rings of empty locations too.
                        var crowded = compact && inView.length > Math.max(COMPACT_BADGE_MIN, badgeRoom);
                        var shown = inView.filter(function (pk) {
                            if (isHighlight(pk)) return true;
                            var a = self.badges[pk];
                            if (dense && a.dataset.holds === '0') return false;
                            if (crowded && !(Number(a.dataset.count) > 0)) return false;
                            return true;
                        });
                        // Names: when they have room at this size; on the full map
                        // also whenever what is left is few, or once zoomed in a
                        // level. Never far below the fit zoom. The highlighted
                        // location is always named.
                        var showLabels = zoom >= this.fitZoom - LABEL_HIDE_BELOW
                            && (labelRoom >= shown.length || (!compact && (shown.length <= LABEL_DENSE_COUNT || !zoomedOut)));
                        this._decided = { shown: shown, showLabels: showLabels, named: null };
                    }
                    var decided = this._decided;

                    var items = decided.shown.map(function (pk) {
                        var a = self.badges[pk];
                        var parts = a.dataset.anchor.split(',');
                        var pt = project([Number(parts[0]), Number(parts[1])]);
                        return { pk: pk, x: pt.x, y: pt.y, count: Number(a.dataset.count) || 0, fixed: isHighlight(pk) };
                    });
                    // The highlighted location's name always shows, so its
                    // neighbours must keep clear of it: a row of small fixed
                    // obstacles stands in for the label under the badge.
                    var obstacles = [];
                    var hi = items.filter(function (it) { return it.fixed; })[0];
                    var hiLabel = hi && this.labels[hi.pk];
                    if (hiLabel) {
                        var w = this.labelWidth(hiLabel);
                        var cy = hi.y + BADGE_PX / 2 + 2 + LABEL_H / 2;
                        for (var lx = hi.x - w / 2 + LABEL_H / 2; lx <= hi.x + w / 2 - LABEL_H / 2 + 0.01; lx += LABEL_H) {
                            obstacles.push({ x: lx, y: cy, count: 0, fixed: true, r: LABEL_H / 2 + 2 });
                        }
                    }
                    var spread = window.YardwayMapLayout
                        ? YardwayMapLayout.spreadBadges(items.concat(obstacles), { size: BADGE_PX, gap: BADGE_GAP, width: size.x, height: size.y })
                        : items;
                    var placed = {};
                    var layout = window.YardwayMapLayout;
                    var nameBoxes = [];   // names already placed; a name another badge or name would cover stays hidden
                    var named = light && decided.named ? decided.named : null;   // a light pass keeps the last name decisions
                    var newNamed = {};
                    // The highlighted badge first, so its name is never the one that gives way.
                    var order = items.map(function (it, i) { return i; })
                        .sort(function (a, b) { return (items[b].fixed ? 1 : 0) - (items[a].fixed ? 1 : 0); });
                    order.forEach(function (i) {
                        var it = items[i];
                        var a = self.badges[it.pk];
                        var p = spread[i];
                        placed[it.pk] = true;
                        a.hidden = false;
                        // transform, not left/top: a zoom animates this for
                        // every badge and name at once, and left/top relayout
                        // the map on each of those frames. A translate runs
                        // on the compositor. The -50%/-50% that used to sit
                        // in the stylesheet comes along here, because an
                        // element has only one transform.
                        a.style.transform =
                            'translate(' + p.x + 'px,' + p.y + 'px) translate(-50%,-50%)';
                        var dx = p.x - it.x, dy = p.y - it.y;
                        self.leader(it.pk, Math.sqrt(dx * dx + dy * dy) > LEADER_MIN_PX ? { x1: it.x, y1: it.y, x2: p.x, y2: p.y, colour: a.dataset.colour } : null);
                        var label = self.labels[it.pk];
                        if (!label) return;
                        var show;
                        if (named) {
                            show = !!named[it.pk];
                        } else {
                            show = decided.showLabels || it.fixed;
                            if (show) {
                                var box = { x: p.x - self.labelWidth(label) / 2, y: p.y + BADGE_PX / 2 + 2, w: self.labelWidth(label), h: LABEL_H };
                                if (!it.fixed && layout) {
                                    show = !items.some(function (other, j) { return j !== i && layout.circleHitsBox(spread[j].x, spread[j].y, BADGE_PX / 2, box); })
                                        && !nameBoxes.some(function (b) { return layout.boxesOverlap(b, box); });
                                }
                                if (show) nameBoxes.push(box);
                            }
                        }
                        if (show) newNamed[it.pk] = true;
                        label.hidden = !show;
                        label.style.transform =
                            'translate(' + p.x + 'px,' + (p.y + BADGE_PX / 2 + 2) + 'px)'
                            + ' translateX(-50%)';
                    });
                    if (!named) decided.named = newNamed;
                    Object.keys(this.badges).forEach(function (pk) {
                        if (placed[pk]) return;
                        self.badges[pk].hidden = true;
                        if (self.labels[pk]) self.labels[pk].hidden = true;
                        self.leader(pk, null);
                    });
                },

                // A label's rendered width, measured once it can be (a hidden
                // element, or one inside a hidden card, measures 0: try again later).
                labelWidth: function (label) {
                    if (label._w == null) {
                        var wasHidden = label.hidden;
                        label.hidden = false;
                        var w = label.offsetWidth || 0;
                        label.hidden = wasHidden;
                        if (w > 0) label._w = w;
                        return w;
                    }
                    return label._w;
                },

                // The thin line (and dot on the anchor) that ties a moved
                // badge to the spot it stands for; null hides it.
                leader: function (pk, seg) {
                    var svg = this.$refs.leaders;
                    if (!svg) return;
                    var g = this.leaders[pk];
                    if (!seg) {
                        if (g) g.style.display = 'none';
                        return;
                    }
                    var NS = 'http://www.w3.org/2000/svg';
                    if (!g) {
                        g = document.createElementNS(NS, 'g');
                        g.setAttribute('class', 'location-map-leader');
                        g.appendChild(document.createElementNS(NS, 'line'));
                        g.appendChild(document.createElementNS(NS, 'circle'));
                        svg.appendChild(g);
                        this.leaders[pk] = g;
                    }
                    var line = g.firstChild, dot = g.lastChild;
                    line.setAttribute('x1', seg.x1); line.setAttribute('y1', seg.y1);
                    line.setAttribute('x2', seg.x2); line.setAttribute('y2', seg.y2);
                    dot.setAttribute('cx', seg.x1); dot.setAttribute('cy', seg.y1); dot.setAttribute('r', 3);
                    if (seg.colour) { line.style.stroke = seg.colour; dot.style.fill = seg.colour; }
                    g.style.display = '';
                }
            };
        });

        Alpine.data('nearYouCard', function () {
            return {
                data: null,
                site: '',
                highlight: null,
                label: '',
                kind: '',      // 'gps' | 'pinned' | 'last' | ''
                ready: false,

                init: function () {
                    var self = this;
                    if (window.Yardway && Yardway.volatile) { Yardway.volatile(this.$el); }
                    var node = document.getElementById('near-you-card-data');
                    if (!node) return;
                    try { this.data = JSON.parse(node.textContent); } catch (err) { return; }
                    this.decide(window.Yardway && Yardway.position);
                    this._onPosition = function (e) { self.decide(e.detail); };
                    window.addEventListener('yardway:position', this._onPosition);
                },

                destroy: function () {
                    window.removeEventListener('yardway:position', this._onPosition);
                },

                // Two independent questions (plan 6.7): which site, and which
                // location inside it. Each is a first-match ladder.
                decide: function (position) {
                    var d = this.data;
                    var gps = null;
                    if (position && window.YardwayGeo) {
                        gps = YardwayGeo.resolveLadder(position, d.locations, d.sites, { nearRadiusM: d.near_radius_m });
                    }
                    var site = '';
                    if (gps && gps.step === 3) site = gps.site.name;
                    else if (gps && gps.location) site = gps.location.site;
                    else if (d.default_site) site = d.default_site;
                    else if (d.site_names.length === 1) site = d.site_names[0];
                    if (!site || d.site_names.indexOf(site) === -1) {
                        this.ready = false;
                        this.site = '';
                        this.$dispatch('near-you:visible', false);
                        return;
                    }

                    var highlight = null, label = '', kind = '';
                    var onSite = function (pk) {
                        return d.locations.concat(d.unlocated).some(function (l) { return l.pk === pk && l.site === site; });
                    };
                    // Only a GPS match may look like one: the other two say why
                    // they are lit, so a field you opened this morning is not
                    // mistaken for the field you are standing in.
                    if (gps && gps.location && gps.location.site === site) {
                        highlight = gps.location.pk;
                        label = gps.location.name + ' · ' + YardwayGeo.formatDistance(gps.distance);
                        kind = 'gps';
                    } else if (d.pinned && onSite(d.pinned.pk)) {
                        highlight = d.pinned.pk;
                        label = 'Pinned · ' + d.pinned.name;
                        kind = 'pinned';
                    } else {
                        var last = window.Yardway && Yardway.lastLocation && Yardway.lastLocation();
                        if (last && onSite(last.pk)) { highlight = last.pk; label = 'Last opened · ' + last.name; kind = 'last'; }
                    }
                    this.site = site;
                    this.highlight = highlight;
                    this.label = label || site;
                    this.kind = kind;
                    this.ready = true;
                    // The dashboard grid keeps a column for the card only while it shows.
                    this.$dispatch('near-you:visible', true);
                    // After Alpine has shown the chosen site's map, so it has a size to fit.
                    Alpine.nextTick(function () {
                        window.dispatchEvent(new CustomEvent('yardway:map-focus', { detail: { site: site, highlight: highlight } }));
                    });
                },

                mapTabUrl: function () {
                    return this.data.urls.map + '?view=map&site=' + encodeURIComponent(this.site);
                },

                count: function () {
                    var s = this.site;
                    return (this.data.site_counts && this.data.site_counts[s]) || 0;
                },

                // On a touch screen the whole card opens the Map tab. With a
                // fine pointer the map itself is for looking (drag, Ctrl+scroll,
                // +/-), so only the rest of the card — and the Full map button
                // over the map — open it.
                open: function (e) {
                    var fine = !!(window.matchMedia && window.matchMedia('(pointer: fine)').matches);
                    if (fine && e && e.target && e.target.closest && e.target.closest('.location-map')) return;
                    window.location.assign(this.mapTabUrl());
                }
            };
        });
    });
})();

;
/* ── js/locations_mobile.js ── */
/**
 * Locations on a phone — the map page and its three-height sheet.
 *
 * Drives templates/locations/_phone_map.html:
 *   - the sheet's peek / half / full snap heights, by tap or by drag
 *   - the site selector
 *   - filtering and sorting the list, in the browser
 *   - picking a location, from a row, a map badge or the shape itself, and
 *     loading its detail into the sheet over htmx
 *
 * Rows, badges and site items all stay ordinary links or buttons that
 * work without any of this; what is here only saves the page load.
 */
(function (root) {
    'use strict';

    // ── Pure helpers, unit-tested in tests/locations_mobile.test.js ──

    var SNAPS = ['peek', 'half', 'full'];

    /** The next height a tap on the handle asks for. */
    function nextSnap(current) {
        var i = SNAPS.indexOf(current);
        return SNAPS[(i + 1) % SNAPS.length];
    }

    /**
     * Which height a drag landed on.
     *
     * `visible` is the sheet's height in pixels when the finger lifts, and
     * `heights` the pixel height of each snap. The nearest one wins —
     * there is no "put it away" here, because the list is the page's
     * second half and a phone in a gateway should never lose it.
     */
    function snapFromHeight(visible, heights) {
        var best = SNAPS[0];
        var gap = Infinity;
        SNAPS.forEach(function (key) {
            var d = Math.abs(visible - heights[key]);
            if (d < gap) { gap = d; best = key; }
        });
        return best;
    }

    /** Does a row survive the filter? `state` is the location's rest state. */
    function rowPasses(filter, state, count) {
        if (filter === 'occupied') { return count > 0; }
        if (filter === 'rested') { return state === 'rested'; }
        return true;
    }

    /** Sort comparator for two rows, most-horses or longest-rested first. */
    function compareRows(mode, a, b) {
        if (mode === 'rest') {
            if (b.rest !== a.rest) { return b.rest - a.rest; }
        } else if (b.count !== a.count) {
            return b.count - a.count;
        }
        return a.name.localeCompare(b.name);
    }

    var api = {
        SNAPS: SNAPS,
        nextSnap: nextSnap,
        snapFromHeight: snapFromHeight,
        rowPasses: rowPasses,
        compareRows: compareRows
    };
    root.YardwayLocationsMobile = api;
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
        return;
    }

    // ── DOM wiring ──

    function page() { return document.querySelector('[data-phone-map]'); }
    function sheet() { return document.querySelector('[data-loc-sheet]'); }

    var state = { snap: 'peek', pk: null, filter: 'all', sort: 'horses' };

    function mappedBox() { return document.querySelector('[data-loc-mapped]'); }

    function rowsIn(container) {
        return Array.prototype.slice.call(container.querySelectorAll('[data-loc-row]'));
    }

    function readRow(el) {
        return {
            el: el,
            pk: el.getAttribute('data-pk'),
            count: parseInt(el.getAttribute('data-count'), 10) || 0,
            rest: parseInt(el.getAttribute('data-rest'), 10),
            state: el.getAttribute('data-state') || '',
            name: el.getAttribute('data-name') || ''
        };
    }

    function setSnap(key) {
        var panel = sheet();
        if (!panel) { return; }
        state.snap = key;
        panel.classList.remove('is-peek', 'is-half', 'is-full');
        panel.classList.add('is-' + key);
        var hint = document.querySelector('[data-loc-hint]');
        if (hint) { hint.textContent = key === 'full' ? 'Show map' : 'Full list'; }
    }

    // How far the sheet is pushed down right now. offsetTop is where
    // layout put it and the client rect is where the transform left it,
    // so the gap between the two is the translate — whichever snap, or a
    // half-finished animation, set it.
    function pushedDown(panel) {
        var parent = panel.offsetParent;
        if (!parent) { return 0; }
        return panel.getBoundingClientRect().top
            - (parent.getBoundingClientRect().top + panel.offsetTop);
    }

    /** How much of the sheet is showing, in pixels. */
    function showing(panel) {
        return panel.offsetHeight - pushedDown(panel);
    }

    // The three resting heights, read from the stylesheet rather than
    // repeated here where they would drift from it. Measured once: this
    // swaps the class three times, and doing that at the start of every
    // drag made the first frame the most expensive one.
    var snapPx = null;

    function snapHeights() {
        var panel = sheet();
        if (!panel) { return null; }
        if (snapPx) { return snapPx; }
        var was = panel.className;
        var out = {};
        // The snap classes carry a transition, so the sheet's position
        // right after a class swap is still the old one — measured with
        // the transition on, all three snaps come back as wherever the
        // sheet happens to be sitting, and every drag then lands back
        // where it started. Measure with it off.
        panel.style.transition = 'none';
        SNAPS.forEach(function (key) {
            panel.className = was.replace(/is-(peek|half|full)/, 'is-' + key);
            out[key] = showing(panel);
        });
        panel.className = was;
        void panel.offsetHeight;        // settle back before animating again
        panel.style.transition = '';
        snapPx = out;
        return out;
    }

    // The snaps are percentages, so they move with the window.
    window.addEventListener('resize', function () { snapPx = null; });
    window.addEventListener('orientationchange', function () { snapPx = null; });

    // ── Filtering and sorting, in the browser ──

    function applyList() {
        var box = mappedBox();
        if (!box) { return; }
        var rows = rowsIn(box).map(readRow);
        var shown = 0;
        rows.forEach(function (r) {
            var on = rowPasses(state.filter, r.state, r.count);
            r.el.hidden = !on;
            if (on) { shown++; }
        });
        // Appending in order re-sorts in place; hidden rows come along, so
        // clearing a filter never reshuffles what is already on screen.
        rows.sort(function (a, b) { return compareRows(state.sort, a, b); });
        rows.forEach(function (r) { box.appendChild(r.el); });
        // Only a filter that hid everything earns the note. A site with
        // no boundaries at all has none of these rows to begin with, and
        // the block below it says so far better.
        var loose = applyUnmapped();
        // Only a filter that hid everything earns the note. A site with
        // no boundaries at all has none of these rows to begin with, and
        // the block below it says so far better.
        var empty = document.querySelector('[data-loc-empty]');
        if (empty) { empty.hidden = shown > 0 || loose > 0 || rows.length === 0; }
        markRows();
    }

    // The block of locations with no boundary. It keeps its place at the
    // end of the list — sorting it beside the mapped ones would suggest a
    // position it does not have — but the filter and the sort still apply
    // inside it. On a site with no map this block is the whole list, and
    // until November 2026 the two sort buttons did nothing there.
    function applyUnmapped() {
        var box = document.querySelector('[data-loc-unmapped]');
        if (!box) { return 0; }
        var rows = rowsIn(box).map(readRow);
        var shown = 0;
        rows.forEach(function (r) {
            var on = rowPasses(state.filter, r.state, r.count);
            r.el.hidden = !on;
            if (on) { shown++; }
        });
        // The rows come after the block's heading, so an append in order
        // sorts them and leaves the heading first.
        rows.sort(function (a, b) { return compareRows(state.sort, a, b); });
        rows.forEach(function (r) { box.appendChild(r.el); });
        box.hidden = shown === 0;
        var count = box.querySelector('[data-loc-unmapped-count]');
        if (count) { count.textContent = String(shown); }
        return shown;
    }

    function markRows() {
        var p = page();
        if (!p) { return; }
        p.querySelectorAll('[data-loc-row]').forEach(function (el) {
            el.classList.toggle('is-selected', state.pk !== null && el.getAttribute('data-pk') === state.pk);
        });
        p.querySelectorAll('[data-map-badge]').forEach(function (el) {
            el.classList.toggle('is-highlight', state.pk !== null && el.dataset.mapBadge === state.pk);
        });
    }

    // ── Picking a field ──

    function previewUrl(pk) {
        var p = page();
        var template = p ? p.getAttribute('data-preview-url') : '';
        return template ? template.replace('/0/', '/' + pk + '/') : '';
    }

    function select(pk) {
        var p = page();
        var detail = document.getElementById('loc-sheet-detail');
        var list = document.querySelector('[data-loc-list]');
        if (!p || !detail || !pk) { return; }
        var changed = state.pk !== String(pk);
        state.pk = String(pk);
        detail.hidden = false;
        if (list) { list.hidden = true; }
        // The phone's Back button goes back to the list (overlay_back.js).
        if (window.Yardway && Yardway.overlay) {
            Yardway.overlay.opened('location', clearSelection, function () { return state.pk !== null && !!page(); });
        }
        if (changed && window.htmx) {
            // `select` is named because htmx.ajax with no source element
            // falls back to <body>, whose hx-select is #main-content for
            // the boosted navigation — a partial holds none, and the
            // sheet would come back empty.
            htmx.ajax('GET', previewUrl(state.pk), {
                // The leaf that carries hx-push-url="false"; naming the
                // shell instead would hand that override to every link
                // inside it.
                source: p.querySelector('[data-preview-source]') || p,
                target: '#loc-sheet-detail',
                select: '#loc-sheet-content',
                swap: 'innerHTML'
            }).catch(function () { /* said by js/connection.js */ });
        }
        if (state.snap === 'peek') { setSnap('half'); }
        markRows();
        // Aim the map at what was picked, so the sheet and the map agree.
        var site = p.getAttribute('data-site');
        window.dispatchEvent(new CustomEvent('yardway:map-focus', {
            detail: { site: site, highlight: parseInt(state.pk, 10) }
        }));
    }

    function clearSelection() {
        var detail = document.getElementById('loc-sheet-detail');
        var list = document.querySelector('[data-loc-list]');
        state.pk = null;
        if (window.Yardway && Yardway.overlay) { Yardway.overlay.closed('location'); }
        if (detail) { detail.hidden = true; }
        if (list) { list.hidden = false; }
        markRows();
    }

    // ── Site selector ──

    function toggleSites(force) {
        var menu = document.getElementById('loc-site-menu');
        var pill = document.querySelector('[data-loc-action="sites"]');
        if (!menu || !pill) { return; }
        var open = force === undefined ? menu.hidden : force;
        menu.hidden = !open;
        pill.setAttribute('aria-expanded', open ? 'true' : 'false');
        var chevron = pill.querySelector('[data-loc-chevron]');
        if (chevron) { chevron.style.transform = open ? 'rotate(180deg)' : ''; }
    }

    // ── Events ──

    // Capture, not bubble: hx-boost listens on <body>, so a row that is
    // also a link would start navigating before a bubbling handler ran.
    document.addEventListener('click', function (e) {
        var p = page();
        if (!p || !e.target.closest) { return; }
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) { return; }

        // The site selector is teleported into the app bar, so it sits
        // outside the page element: match on the hooks, not on ancestry.
        var action = e.target.closest('[data-loc-action]');
        if (action) {
            var what = action.getAttribute('data-loc-action');
            if (what === 'cycle') { e.preventDefault(); e.stopPropagation(); setSnap(nextSnap(state.snap)); return; }
            if (what === 'sites') { e.preventDefault(); e.stopPropagation(); toggleSites(); return; }
            if (what === 'back') { e.preventDefault(); e.stopPropagation(); clearSelection(); return; }
        }

        var filter = e.target.closest('[data-loc-filter]');
        if (filter && p.contains(filter)) {
            e.preventDefault();
            state.filter = filter.getAttribute('data-loc-filter');
            p.querySelectorAll('[data-loc-filter]').forEach(function (b) {
                b.classList.toggle('is-active', b === filter);
            });
            applyList();
            return;
        }

        var sort = e.target.closest('[data-loc-sort]');
        if (sort && p.contains(sort)) {
            e.preventDefault();
            state.sort = sort.getAttribute('data-loc-sort');
            p.querySelectorAll('[data-loc-sort]').forEach(function (b) {
                b.classList.toggle('is-active', b === sort);
            });
            applyList();
            return;
        }

        var badge = e.target.closest('[data-map-badge]');
        if (badge && p.contains(badge)) {
            e.preventDefault();
            e.stopPropagation();
            select(badge.dataset.mapBadge);
            return;
        }

        var row = e.target.closest('[data-loc-row]');
        if (row && p.contains(row)) {
            e.preventDefault();
            e.stopPropagation();
            select(row.getAttribute('data-pk'));
        }
    }, true);

    // A tap on the map's own shapes, relayed by static/js/location_map.js
    // because Leaflet layers are not in the DOM to delegate from.
    window.addEventListener('yardway:map-pick', function (e) {
        if (!page() || !e.detail) { return; }
        if (e.detail.pk) { select(String(e.detail.pk)); }
        else if (state.pk) { clearSelection(); }
    });

    document.addEventListener('click', function (e) {
        var menu = document.getElementById('loc-site-menu');
        if (!menu || menu.hidden) { return; }
        if (e.target.closest && e.target.closest('.loc-site-slot')) { return; }
        toggleSites(false);
    });

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || !page()) { return; }
        var menu = document.getElementById('loc-site-menu');
        if (menu && !menu.hidden) { toggleSites(false); return; }
        if (state.pk) { clearSelection(); }
    });

    // ── Dragging the sheet ──

    var drag = null;

    function scrollBox() { return document.querySelector('.loc-sheet-scroll'); }

    function dragStart(e) {
        var panel = sheet();
        if (!panel) { return; }
        var box = scrollBox();
        var inList = box && box.contains(e.target);
        // The list scrolls; only a pull from the very top of it is a drag.
        if (inList && box.scrollTop > 0) { return; }
        drag = {
            y: e.touches[0].clientY,
            // Where the sheet is, not where its class says it should be:
            // a finger landing mid-animation picks it up where it looks.
            from: showing(panel),
            height: panel.offsetHeight,
            heights: snapHeights(),
            inList: inList,
            moved: false,
            frame: 0
        };
        panel.classList.add('is-dragging');
    }

    function dragMove(e) {
        var panel = sheet();
        if (!drag || !panel) { return; }
        var delta = drag.y - e.touches[0].clientY;   // up is positive
        // Pulling up inside the list with the sheet already full is the
        // list being read, not the sheet being moved.
        if (drag.inList && state.snap === 'full' && delta > 0) { return; }
        drag.moved = drag.moved || Math.abs(delta) > 4;
        if (!drag.moved) { return; }
        if (e.cancelable) { e.preventDefault(); }
        drag.visible = Math.max(0, drag.from + delta);
        paint(panel);
    }

    // One write per frame: touchmove reports faster than the screen
    // redraws, and a second write in the same frame is work nobody sees.
    function paint(panel) {
        if (!drag || drag.frame) { return; }
        if (!window.requestAnimationFrame) { place(panel); return; }
        drag.frame = requestAnimationFrame(function () {
            if (!drag) { return; }
            drag.frame = 0;
            place(panel);
        });
    }

    function place(panel) {
        panel.style.transform =
            'translateY(' + Math.max(0, drag.height - drag.visible) + 'px)';
    }

    function dragEnd() {
        var panel = sheet();
        if (!drag || !panel) { return; }
        if (drag.frame && window.cancelAnimationFrame) { cancelAnimationFrame(drag.frame); }
        panel.classList.remove('is-dragging');
        panel.style.transform = '';
        var moved = drag.moved;
        var visible = drag.visible;
        var heights = drag.heights;
        drag = null;
        if (!moved || visible === undefined || !heights) { return; }
        setSnap(snapFromHeight(visible, heights));
    }

    // The whole sheet is the handle. A 22 px strip was the only way to
    // move it, which is a hard target for a thumb in a gateway.
    function bindDrag() {
        var panel = sheet();
        if (!panel || panel.dataset.dragBound) { return; }
        panel.dataset.dragBound = '1';
        panel.addEventListener('touchstart', dragStart, { passive: true });
        panel.addEventListener('touchmove', dragMove, { passive: false });
        panel.addEventListener('touchend', dragEnd);
        panel.addEventListener('touchcancel', dragEnd);
    }

    // ── Setting up, and doing it again after a swap ──

    function setUp() {
        if (!page()) { return; }
        // Peek leaves the screen to the map. A site with no boundaries
        // has no map to leave it to, so its list opens at half height
        // instead of under an empty screen.
        var start = page().querySelector('.loc-phone-nomap') ? 'half' : 'peek';
        state.snap = start;
        state.pk = null;
        state.filter = 'all';
        state.sort = 'horses';
        bindDrag();
        setSnap(start);
        applyList();
    }

    document.addEventListener('DOMContentLoaded', setUp);
    document.addEventListener('htmx:afterSwap', function (e) {
        if (e.target && e.target.id === 'main-content') { setUp(); }
    });
    window.Yardway = window.Yardway || {};
    var previousHook = window.Yardway.afterMainSwap;
    window.Yardway.afterMainSwap = function (main, responseText) {
        if (previousHook) { previousHook(main, responseText); }
        setUp();
    };
})(typeof window !== 'undefined' ? window : globalThis);

;
/* ── js/locations_board.js ── */
/**
 * The site board on a wide screen — the rail beside the map.
 *
 * Drives templates/locations/_site_board.html:
 *   - filtering and sorting the rail, in the browser
 *   - hovering a row or a rest bar lights the shape on the map, and a
 *     shape lights its row
 *   - picking a location, from a row, a bar, a badge or the shape, and
 *     loading its detail into the rail over htmx
 *
 * The phone has its own file (locations_mobile.js) because the shapes of
 * the two pages differ; what they share is the payload, the detail
 * partial and the map component, which is where the duplication would
 * actually have cost something.
 */
(function (root) {
    'use strict';

    // ── Pure helpers, unit-tested in tests/locations_board.test.js ──

    /** Does a row survive the text filter? Both sides are already cased. */
    function matchesQuery(name, query) {
        if (!query) { return true; }
        return name.indexOf(query) !== -1;
    }

    /** Sort comparator: most horses, or longest rested, then by name. */
    function compareRows(mode, a, b) {
        if (mode === 'rest') {
            if (b.rest !== a.rest) { return b.rest - a.rest; }
        } else if (b.count !== a.count) {
            return b.count - a.count;
        }
        return a.name.localeCompare(b.name);
    }

    /** The line under "Rest & rotation": what the strip adds up to. */
    function restSummary(states) {
        var ready = 0, recovering = 0, over = 0;
        states.forEach(function (state) {
            if (state === 'rested') { ready++; }
            else if (state === 'recovering') { recovering++; }
            else if (state === 'over') { over++; }
        });
        var parts = [];
        if (ready) { parts.push(ready + ' ready'); }
        if (recovering) { parts.push(recovering + ' recovering'); }
        if (over) { parts.push(over + ' over'); }
        return parts.join(' · ');
    }

    var api = {
        matchesQuery: matchesQuery,
        compareRows: compareRows,
        restSummary: restSummary
    };
    root.YardwayLocationsBoard = api;
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
        return;
    }

    // ── DOM wiring ──

    function board() { return document.querySelector('[data-site-board]'); }

    var state = { pk: null, query: '', sort: 'horses' };

    function readRow(el) {
        return {
            el: el,
            pk: el.getAttribute('data-pk'),
            count: parseInt(el.getAttribute('data-count'), 10) || 0,
            rest: parseInt(el.getAttribute('data-rest'), 10),
            state: el.getAttribute('data-state') || '',
            name: el.getAttribute('data-name') || ''
        };
    }

    function rowsIn(box) {
        return Array.prototype.slice.call(box.querySelectorAll('[data-loc-row]')).map(readRow);
    }

    // ── The rail's list ──

    function applyList() {
        var b = board();
        if (!b) { return; }
        var shown = 0;

        var mapped = b.querySelector('[data-loc-mapped]');
        if (mapped) {
            var rows = rowsIn(mapped);
            rows.forEach(function (r) {
                var on = matchesQuery(r.name.toLowerCase(), state.query);
                r.el.hidden = !on;
                if (on) { shown++; }
            });
            rows.sort(function (a, b2) { return compareRows(state.sort, a, b2); });
            rows.forEach(function (r) { mapped.appendChild(r.el); });
        }

        var loose = b.querySelector('[data-loc-unmapped]');
        if (loose) {
            var seen = 0;
            var looseRows = rowsIn(loose);
            looseRows.forEach(function (r) {
                var on = matchesQuery(r.name.toLowerCase(), state.query);
                r.el.hidden = !on;
                if (on) { seen++; }
            });
            // The block stays at the end, and the sort applies inside it:
            // on a site with no map it is the whole list. The rows come
            // after the block's heading, so an append in order sorts them.
            looseRows.sort(function (a, b2) { return compareRows(state.sort, a, b2); });
            looseRows.forEach(function (r) { loose.appendChild(r.el); });
            loose.hidden = seen === 0;
            var count = loose.querySelector('[data-loc-unmapped-count]');
            if (count) { count.textContent = String(seen); }
            shown += seen;
        }

        var empty = b.querySelector('[data-loc-empty]');
        if (empty) { empty.hidden = shown > 0; }
        markRows();
    }

    function fillSummary() {
        var b = board();
        if (!b) { return; }
        var node = b.querySelector('[data-rest-summary]');
        if (!node) { return; }
        var states = Array.prototype.map.call(
            b.querySelectorAll('[data-loc-row]'),
            function (el) { return el.getAttribute('data-state'); }
        );
        node.textContent = restSummary(states);
    }

    function markRows() {
        var b = board();
        if (!b) { return; }
        b.querySelectorAll('[data-loc-row], [data-loc-bar]').forEach(function (el) {
            el.classList.toggle(
                'is-selected',
                state.pk !== null && el.getAttribute('data-pk') === state.pk
            );
        });
        b.querySelectorAll('[data-map-badge]').forEach(function (el) {
            el.classList.toggle('is-highlight', state.pk !== null && el.dataset.mapBadge === state.pk);
        });
    }

    // ── Hover, both ways ──
    // The rail and the map are two views of one thing, so pointing at a
    // location in either has to answer in the other.

    function hover(pk, on) {
        var b = board();
        if (!b) { return; }
        if (pk) {
            b.querySelectorAll('[data-loc-row][data-pk="' + pk + '"], [data-loc-bar][data-pk="' + pk + '"]')
                .forEach(function (el) { el.classList.toggle('is-hover', on); });
        }
        window.dispatchEvent(new CustomEvent('yardway:map-hover', {
            detail: { site: b.getAttribute('data-site'), pk: on ? pk : null }
        }));
    }

    // ── Picking a location ──

    function previewUrl(pk) {
        var b = board();
        var template = b ? b.getAttribute('data-preview-url') : '';
        return template ? template.replace('/0/', '/' + pk + '/') : '';
    }

    function select(pk) {
        var b = board();
        var detail = document.getElementById('loc-rail-detail');
        var list = b && b.querySelector('[data-rail-list]');
        if (!b || !detail || !pk) { return; }
        var changed = state.pk !== String(pk);
        state.pk = String(pk);
        showSites(false);
        detail.hidden = false;
        if (list) { list.hidden = true; }
        if (changed && window.htmx) {
            // `select` is named because htmx.ajax with no source element
            // falls back to <body>, whose hx-select is #main-content for
            // the boosted navigation — a partial holds none, and the rail
            // would come back empty.
            htmx.ajax('GET', previewUrl(state.pk), {
                // The leaf that carries hx-push-url="false"; naming the
                // shell instead would hand that override to every link
                // inside it.
                source: b.querySelector('[data-preview-source]') || b,
                target: '#loc-rail-detail',
                select: '#loc-sheet-content',
                swap: 'innerHTML'
            }).catch(function () { /* said by js/connection.js */ });
        }
        markRows();
        window.dispatchEvent(new CustomEvent('yardway:map-focus', {
            detail: { site: b.getAttribute('data-site'), highlight: parseInt(state.pk, 10) }
        }));
    }

    // ── Switching site ──
    // The sites take the rail over rather than floating above it: the
    // rail clips its own corners, so a dropdown would be cut in half.

    function showSites(on) {
        var b = board();
        if (!b) { return; }
        var panel = b.querySelector('[data-rail-sites]');
        var list = b.querySelector('[data-rail-list]');
        var detail = document.getElementById('loc-rail-detail');
        var pill = b.querySelector('[data-board-action="sites"]');
        if (!panel) { return; }
        panel.hidden = !on;
        if (list) { list.hidden = on || state.pk !== null; }
        if (detail && on) { detail.hidden = true; }
        if (pill) { pill.setAttribute('aria-expanded', on ? 'true' : 'false'); }
        if (on) {
            var current = panel.querySelector('.loc-site-item.is-current');
            if (current && current.scrollIntoView) { current.scrollIntoView({ block: 'nearest' }); }
        }
    }

    function sitesOpen() {
        var b = board();
        var panel = b && b.querySelector('[data-rail-sites]');
        return !!panel && !panel.hidden;
    }

    function clearSelection() {
        var b = board();
        var detail = document.getElementById('loc-rail-detail');
        var list = b && b.querySelector('[data-rail-list]');
        state.pk = null;
        if (detail) { detail.hidden = true; }
        if (list) { list.hidden = false; }
        markRows();
    }

    // ── Events ──

    // Capture, not bubble: hx-boost listens on <body>, so a row that is
    // also a link would start navigating before a bubbling handler ran.
    document.addEventListener('click', function (e) {
        var b = board();
        if (!b || !e.target.closest) { return; }
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) { return; }

        var site = e.target.closest('[data-board-action]');
        if (site && b.contains(site)) {
            e.preventDefault();
            e.stopPropagation();
            showSites(site.getAttribute('data-board-action') === 'sites');
            return;
        }

        var back = e.target.closest('[data-loc-action="back"]');
        if (back && b.contains(back)) {
            e.preventDefault();
            e.stopPropagation();
            clearSelection();
            return;
        }

        var sort = e.target.closest('[data-loc-sort]');
        if (sort && b.contains(sort)) {
            e.preventDefault();
            state.sort = sort.getAttribute('data-loc-sort');
            b.querySelectorAll('[data-loc-sort]').forEach(function (x) {
                x.classList.toggle('is-active', x === sort);
            });
            applyList();
            return;
        }

        var target = e.target.closest('[data-loc-row], [data-loc-bar], [data-loc-jump], [data-map-badge]');
        if (target && b.contains(target)) {
            e.preventDefault();
            e.stopPropagation();
            select(target.getAttribute('data-pk') || target.dataset.mapBadge);
        }
    }, true);

    document.addEventListener('input', function (e) {
        var b = board();
        if (!b || !e.target.matches || !e.target.matches('[data-rail-filter]')) { return; }
        state.query = e.target.value.trim().toLowerCase();
        applyList();
    });

    ['mouseenter', 'mouseleave'].forEach(function (kind) {
        document.addEventListener(kind, function (e) {
            var b = board();
            if (!b || !e.target.closest) { return; }
            var el = e.target.closest('[data-loc-row], [data-loc-bar], [data-map-badge]');
            if (!el || !b.contains(el)) { return; }
            hover(el.getAttribute('data-pk') || el.dataset.mapBadge, kind === 'mouseenter');
        }, true);
    });

    // A tap on the map's own shapes, relayed by static/js/location_map.js.
    window.addEventListener('yardway:map-pick', function (e) {
        if (!board() || !e.detail) { return; }
        if (e.detail.pk) { select(String(e.detail.pk)); }
        else if (state.pk) { clearSelection(); }
    });

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || !board()) { return; }
        if (sitesOpen()) { showSites(false); return; }
        if (state.pk) { clearSelection(); }
    });

    // ── Setting up, and doing it again after a swap ──

    function setUp() {
        if (!board()) { return; }
        state.pk = null;
        state.query = '';
        state.sort = 'horses';
        showSites(false);
        fillSummary();
        applyList();
    }

    document.addEventListener('DOMContentLoaded', setUp);
    document.addEventListener('htmx:afterSwap', function (e) {
        if (e.target && e.target.id === 'main-content') { setUp(); }
    });
    window.Yardway = window.Yardway || {};
    var previousHook = window.Yardway.afterMainSwap;
    window.Yardway.afterMainSwap = function (main, responseText) {
        if (previousHook) { previousHook(main, responseText); }
        setUp();
    };
})(typeof window !== 'undefined' ? window : globalThis);

;
/* ── js/alpine-collapse.min.js ── */
(()=>{function g(n){n.directive("collapse",e),e.inline=(t,{modifiers:i})=>{i.includes("min")&&(t._x_doShow=()=>{},t._x_doHide=()=>{})};function e(t,{modifiers:i}){let r=l(i,"duration",250)/1e3,h=l(i,"min",0),u=!i.includes("min");t._x_isShown||(t.style.height=`${h}px`),!t._x_isShown&&u&&(t.hidden=!0),t._x_isShown||(t.style.overflow="hidden");let c=(d,s)=>{let o=n.setStyles(d,s);return s.height?()=>{}:o},f={transitionProperty:"height",transitionDuration:`${r}s`,transitionTimingFunction:"cubic-bezier(0.4, 0.0, 0.2, 1)"};t._x_transition={in(d=()=>{},s=()=>{}){u&&(t.hidden=!1),u&&(t.style.display=null);let o=t.getBoundingClientRect().height;t.style.height="auto";let a=t.getBoundingClientRect().height;o===a&&(o=h),n.transition(t,n.setStyles,{during:f,start:{height:o+"px"},end:{height:a+"px"}},()=>t._x_isShown=!0,()=>{Math.abs(t.getBoundingClientRect().height-a)<1&&(t.style.overflow=null)})},out(d=()=>{},s=()=>{}){let o=t.getBoundingClientRect().height;n.transition(t,c,{during:f,start:{height:o+"px"},end:{height:h+"px"}},()=>t.style.overflow="hidden",()=>{t._x_isShown=!1,t.style.height==`${h}px`&&u&&(t.style.display="none",t.hidden=!0)})}}}}function l(n,e,t){if(n.indexOf(e)===-1)return t;let i=n[n.indexOf(e)+1];if(!i)return t;if(e==="duration"){let r=i.match(/([0-9]+)ms/);if(r)return r[1]}if(e==="min"){let r=i.match(/([0-9]+)px/);if(r)return r[1]}return i}document.addEventListener("alpine:init",()=>{window.Alpine.plugin(g)});})();

;
/* ── js/alpine.min.js ── */
(()=>{var nt=!1,it=!1,W=[],ot=-1;function Ut(e){Rn(e)}function Rn(e){W.includes(e)||W.push(e),Mn()}function Wt(e){let t=W.indexOf(e);t!==-1&&t>ot&&W.splice(t,1)}function Mn(){!it&&!nt&&(nt=!0,queueMicrotask(Nn))}function Nn(){nt=!1,it=!0;for(let e=0;e<W.length;e++)W[e](),ot=e;W.length=0,ot=-1,it=!1}var T,N,$,at,st=!0;function Gt(e){st=!1,e(),st=!0}function Jt(e){T=e.reactive,$=e.release,N=t=>e.effect(t,{scheduler:r=>{st?Ut(r):r()}}),at=e.raw}function ct(e){N=e}function Yt(e){let t=()=>{};return[n=>{let i=N(n);return e._x_effects||(e._x_effects=new Set,e._x_runEffects=()=>{e._x_effects.forEach(o=>o())}),e._x_effects.add(i),t=()=>{i!==void 0&&(e._x_effects.delete(i),$(i))},i},()=>{t()}]}function ve(e,t){let r=!0,n,i=N(()=>{let o=e();JSON.stringify(o),r?n=o:queueMicrotask(()=>{t(o,n),n=o}),r=!1});return()=>$(i)}var Xt=[],Zt=[],Qt=[];function er(e){Qt.push(e)}function te(e,t){typeof t=="function"?(e._x_cleanups||(e._x_cleanups=[]),e._x_cleanups.push(t)):(t=e,Zt.push(t))}function Ae(e){Xt.push(e)}function Oe(e,t,r){e._x_attributeCleanups||(e._x_attributeCleanups={}),e._x_attributeCleanups[t]||(e._x_attributeCleanups[t]=[]),e._x_attributeCleanups[t].push(r)}function lt(e,t){e._x_attributeCleanups&&Object.entries(e._x_attributeCleanups).forEach(([r,n])=>{(t===void 0||t.includes(r))&&(n.forEach(i=>i()),delete e._x_attributeCleanups[r])})}function tr(e){for(e._x_effects?.forEach(Wt);e._x_cleanups?.length;)e._x_cleanups.pop()()}var ut=new MutationObserver(mt),ft=!1;function ue(){ut.observe(document,{subtree:!0,childList:!0,attributes:!0,attributeOldValue:!0}),ft=!0}function dt(){kn(),ut.disconnect(),ft=!1}var le=[];function kn(){let e=ut.takeRecords();le.push(()=>e.length>0&&mt(e));let t=le.length;queueMicrotask(()=>{if(le.length===t)for(;le.length>0;)le.shift()()})}function m(e){if(!ft)return e();dt();let t=e();return ue(),t}var pt=!1,Se=[];function rr(){pt=!0}function nr(){pt=!1,mt(Se),Se=[]}function mt(e){if(pt){Se=Se.concat(e);return}let t=[],r=new Set,n=new Map,i=new Map;for(let o=0;o<e.length;o++)if(!e[o].target._x_ignoreMutationObserver&&(e[o].type==="childList"&&(e[o].removedNodes.forEach(s=>{s.nodeType===1&&s._x_marker&&r.add(s)}),e[o].addedNodes.forEach(s=>{if(s.nodeType===1){if(r.has(s)){r.delete(s);return}s._x_marker||t.push(s)}})),e[o].type==="attributes")){let s=e[o].target,a=e[o].attributeName,c=e[o].oldValue,l=()=>{n.has(s)||n.set(s,[]),n.get(s).push({name:a,value:s.getAttribute(a)})},u=()=>{i.has(s)||i.set(s,[]),i.get(s).push(a)};s.hasAttribute(a)&&c===null?l():s.hasAttribute(a)?(u(),l()):u()}i.forEach((o,s)=>{lt(s,o)}),n.forEach((o,s)=>{Xt.forEach(a=>a(s,o))});for(let o of r)t.some(s=>s.contains(o))||Zt.forEach(s=>s(o));for(let o of t)o.isConnected&&Qt.forEach(s=>s(o));t=null,r=null,n=null,i=null}function Ce(e){return z(B(e))}function k(e,t,r){return e._x_dataStack=[t,...B(r||e)],()=>{e._x_dataStack=e._x_dataStack.filter(n=>n!==t)}}function B(e){return e._x_dataStack?e._x_dataStack:typeof ShadowRoot=="function"&&e instanceof ShadowRoot?B(e.host):e.parentNode?B(e.parentNode):[]}function z(e){return new Proxy({objects:e},Dn)}var Dn={ownKeys({objects:e}){return Array.from(new Set(e.flatMap(t=>Object.keys(t))))},has({objects:e},t){return t==Symbol.unscopables?!1:e.some(r=>Object.prototype.hasOwnProperty.call(r,t)||Reflect.has(r,t))},get({objects:e},t,r){return t=="toJSON"?Pn:Reflect.get(e.find(n=>Reflect.has(n,t))||{},t,r)},set({objects:e},t,r,n){let i=e.find(s=>Object.prototype.hasOwnProperty.call(s,t))||e[e.length-1],o=Object.getOwnPropertyDescriptor(i,t);return o?.set&&o?.get?o.set.call(n,r)||!0:Reflect.set(i,t,r)}};function Pn(){return Reflect.ownKeys(this).reduce((t,r)=>(t[r]=Reflect.get(this,r),t),{})}function Te(e){let t=n=>typeof n=="object"&&!Array.isArray(n)&&n!==null,r=(n,i="")=>{Object.entries(Object.getOwnPropertyDescriptors(n)).forEach(([o,{value:s,enumerable:a}])=>{if(a===!1||s===void 0||typeof s=="object"&&s!==null&&s.__v_skip)return;let c=i===""?o:`${i}.${o}`;typeof s=="object"&&s!==null&&s._x_interceptor?n[o]=s.initialize(e,c,o):t(s)&&s!==n&&!(s instanceof Element)&&r(s,c)})};return r(e)}function Re(e,t=()=>{}){let r={initialValue:void 0,_x_interceptor:!0,initialize(n,i,o){return e(this.initialValue,()=>In(n,i),s=>ht(n,i,s),i,o)}};return t(r),n=>{if(typeof n=="object"&&n!==null&&n._x_interceptor){let i=r.initialize.bind(r);r.initialize=(o,s,a)=>{let c=n.initialize(o,s,a);return r.initialValue=c,i(o,s,a)}}else r.initialValue=n;return r}}function In(e,t){return t.split(".").reduce((r,n)=>r[n],e)}function ht(e,t,r){if(typeof t=="string"&&(t=t.split(".")),t.length===1)e[t[0]]=r;else{if(t.length===0)throw error;return e[t[0]]||(e[t[0]]={}),ht(e[t[0]],t.slice(1),r)}}var ir={};function y(e,t){ir[e]=t}function fe(e,t){let r=Ln(t);return Object.entries(ir).forEach(([n,i])=>{Object.defineProperty(e,`$${n}`,{get(){return i(t,r)},enumerable:!1})}),e}function Ln(e){let[t,r]=_t(e),n={interceptor:Re,...t};return te(e,r),n}function or(e,t,r,...n){try{return r(...n)}catch(i){re(i,e,t)}}function re(e,t,r=void 0){e=Object.assign(e??{message:"No error message given."},{el:t,expression:r}),console.warn(`Alpine Expression Error: ${e.message}

${r?'Expression: "'+r+`"

`:""}`,t),setTimeout(()=>{throw e},0)}var Me=!0;function ke(e){let t=Me;Me=!1;let r=e();return Me=t,r}function R(e,t,r={}){let n;return x(e,t)(i=>n=i,r),n}function x(...e){return sr(...e)}var sr=xt;function ar(e){sr=e}function xt(e,t){let r={};fe(r,e);let n=[r,...B(e)],i=typeof t=="function"?$n(n,t):Fn(n,t,e);return or.bind(null,e,t,i)}function $n(e,t){return(r=()=>{},{scope:n={},params:i=[]}={})=>{let o=t.apply(z([n,...e]),i);Ne(r,o)}}var gt={};function jn(e,t){if(gt[e])return gt[e];let r=Object.getPrototypeOf(async function(){}).constructor,n=/^[\n\s]*if.*\(.*\)/.test(e.trim())||/^(let|const)\s/.test(e.trim())?`(async()=>{ ${e} })()`:e,o=(()=>{try{let s=new r(["__self","scope"],`with (scope) { __self.result = ${n} }; __self.finished = true; return __self.result;`);return Object.defineProperty(s,"name",{value:`[Alpine] ${e}`}),s}catch(s){return re(s,t,e),Promise.resolve()}})();return gt[e]=o,o}function Fn(e,t,r){let n=jn(t,r);return(i=()=>{},{scope:o={},params:s=[]}={})=>{n.result=void 0,n.finished=!1;let a=z([o,...e]);if(typeof n=="function"){let c=n(n,a).catch(l=>re(l,r,t));n.finished?(Ne(i,n.result,a,s,r),n.result=void 0):c.then(l=>{Ne(i,l,a,s,r)}).catch(l=>re(l,r,t)).finally(()=>n.result=void 0)}}}function Ne(e,t,r,n,i){if(Me&&typeof t=="function"){let o=t.apply(r,n);o instanceof Promise?o.then(s=>Ne(e,s,r,n)).catch(s=>re(s,i,t)):e(o)}else typeof t=="object"&&t instanceof Promise?t.then(o=>e(o)):e(t)}var wt="x-";function C(e=""){return wt+e}function cr(e){wt=e}var De={};function d(e,t){return De[e]=t,{before(r){if(!De[r]){console.warn(String.raw`Cannot find directive \`${r}\`. \`${e}\` will use the default order of execution`);return}let n=G.indexOf(r);G.splice(n>=0?n:G.indexOf("DEFAULT"),0,e)}}}function lr(e){return Object.keys(De).includes(e)}function pe(e,t,r){if(t=Array.from(t),e._x_virtualDirectives){let o=Object.entries(e._x_virtualDirectives).map(([a,c])=>({name:a,value:c})),s=Et(o);o=o.map(a=>s.find(c=>c.name===a.name)?{name:`x-bind:${a.name}`,value:`"${a.value}"`}:a),t=t.concat(o)}let n={};return t.map(dr((o,s)=>n[o]=s)).filter(mr).map(zn(n,r)).sort(Kn).map(o=>Bn(e,o))}function Et(e){return Array.from(e).map(dr()).filter(t=>!mr(t))}var yt=!1,de=new Map,ur=Symbol();function fr(e){yt=!0;let t=Symbol();ur=t,de.set(t,[]);let r=()=>{for(;de.get(t).length;)de.get(t).shift()();de.delete(t)},n=()=>{yt=!1,r()};e(r),n()}function _t(e){let t=[],r=a=>t.push(a),[n,i]=Yt(e);return t.push(i),[{Alpine:K,effect:n,cleanup:r,evaluateLater:x.bind(x,e),evaluate:R.bind(R,e)},()=>t.forEach(a=>a())]}function Bn(e,t){let r=()=>{},n=De[t.type]||r,[i,o]=_t(e);Oe(e,t.original,o);let s=()=>{e._x_ignore||e._x_ignoreSelf||(n.inline&&n.inline(e,t,i),n=n.bind(n,e,t,i),yt?de.get(ur).push(n):n())};return s.runCleanups=o,s}var Pe=(e,t)=>({name:r,value:n})=>(r.startsWith(e)&&(r=r.replace(e,t)),{name:r,value:n}),Ie=e=>e;function dr(e=()=>{}){return({name:t,value:r})=>{let{name:n,value:i}=pr.reduce((o,s)=>s(o),{name:t,value:r});return n!==t&&e(n,t),{name:n,value:i}}}var pr=[];function ne(e){pr.push(e)}function mr({name:e}){return hr().test(e)}var hr=()=>new RegExp(`^${wt}([^:^.]+)\\b`);function zn(e,t){return({name:r,value:n})=>{let i=r.match(hr()),o=r.match(/:([a-zA-Z0-9\-_:]+)/),s=r.match(/\.[^.\]]+(?=[^\]]*$)/g)||[],a=t||e[r]||r;return{type:i?i[1]:null,value:o?o[1]:null,modifiers:s.map(c=>c.replace(".","")),expression:n,original:a}}}var bt="DEFAULT",G=["ignore","ref","data","id","anchor","bind","init","for","model","modelable","transition","show","if",bt,"teleport"];function Kn(e,t){let r=G.indexOf(e.type)===-1?bt:e.type,n=G.indexOf(t.type)===-1?bt:t.type;return G.indexOf(r)-G.indexOf(n)}function J(e,t,r={}){e.dispatchEvent(new CustomEvent(t,{detail:r,bubbles:!0,composed:!0,cancelable:!0}))}function D(e,t){if(typeof ShadowRoot=="function"&&e instanceof ShadowRoot){Array.from(e.children).forEach(i=>D(i,t));return}let r=!1;if(t(e,()=>r=!0),r)return;let n=e.firstElementChild;for(;n;)D(n,t,!1),n=n.nextElementSibling}function E(e,...t){console.warn(`Alpine Warning: ${e}`,...t)}var _r=!1;function gr(){_r&&E("Alpine has already been initialized on this page. Calling Alpine.start() more than once can cause problems."),_r=!0,document.body||E("Unable to initialize. Trying to load Alpine before `<body>` is available. Did you forget to add `defer` in Alpine's `<script>` tag?"),J(document,"alpine:init"),J(document,"alpine:initializing"),ue(),er(t=>S(t,D)),te(t=>P(t)),Ae((t,r)=>{pe(t,r).forEach(n=>n())});let e=t=>!Y(t.parentElement,!0);Array.from(document.querySelectorAll(br().join(","))).filter(e).forEach(t=>{S(t)}),J(document,"alpine:initialized"),setTimeout(()=>{Vn()})}var vt=[],xr=[];function yr(){return vt.map(e=>e())}function br(){return vt.concat(xr).map(e=>e())}function Le(e){vt.push(e)}function $e(e){xr.push(e)}function Y(e,t=!1){return j(e,r=>{if((t?br():yr()).some(i=>r.matches(i)))return!0})}function j(e,t){if(e){if(t(e))return e;if(e._x_teleportBack&&(e=e._x_teleportBack),!!e.parentElement)return j(e.parentElement,t)}}function wr(e){return yr().some(t=>e.matches(t))}var Er=[];function vr(e){Er.push(e)}var Hn=1;function S(e,t=D,r=()=>{}){j(e,n=>n._x_ignore)||fr(()=>{t(e,(n,i)=>{n._x_marker||(r(n,i),Er.forEach(o=>o(n,i)),pe(n,n.attributes).forEach(o=>o()),n._x_ignore||(n._x_marker=Hn++),n._x_ignore&&i())})})}function P(e,t=D){t(e,r=>{tr(r),lt(r),delete r._x_marker})}function Vn(){[["ui","dialog",["[x-dialog], [x-popover]"]],["anchor","anchor",["[x-anchor]"]],["sort","sort",["[x-sort]"]]].forEach(([t,r,n])=>{lr(r)||n.some(i=>{if(document.querySelector(i))return E(`found "${i}", but missing ${t} plugin`),!0})})}var St=[],At=!1;function ie(e=()=>{}){return queueMicrotask(()=>{At||setTimeout(()=>{je()})}),new Promise(t=>{St.push(()=>{e(),t()})})}function je(){for(At=!1;St.length;)St.shift()()}function Sr(){At=!0}function me(e,t){return Array.isArray(t)?Ar(e,t.join(" ")):typeof t=="object"&&t!==null?qn(e,t):typeof t=="function"?me(e,t()):Ar(e,t)}function Ar(e,t){let r=o=>o.split(" ").filter(Boolean),n=o=>o.split(" ").filter(s=>!e.classList.contains(s)).filter(Boolean),i=o=>(e.classList.add(...o),()=>{e.classList.remove(...o)});return t=t===!0?t="":t||"",i(n(t))}function qn(e,t){let r=a=>a.split(" ").filter(Boolean),n=Object.entries(t).flatMap(([a,c])=>c?r(a):!1).filter(Boolean),i=Object.entries(t).flatMap(([a,c])=>c?!1:r(a)).filter(Boolean),o=[],s=[];return i.forEach(a=>{e.classList.contains(a)&&(e.classList.remove(a),s.push(a))}),n.forEach(a=>{e.classList.contains(a)||(e.classList.add(a),o.push(a))}),()=>{s.forEach(a=>e.classList.add(a)),o.forEach(a=>e.classList.remove(a))}}function X(e,t){return typeof t=="object"&&t!==null?Un(e,t):Wn(e,t)}function Un(e,t){let r={};return Object.entries(t).forEach(([n,i])=>{r[n]=e.style[n],n.startsWith("--")||(n=Gn(n)),e.style.setProperty(n,i)}),setTimeout(()=>{e.style.length===0&&e.removeAttribute("style")}),()=>{X(e,r)}}function Wn(e,t){let r=e.getAttribute("style",t);return e.setAttribute("style",t),()=>{e.setAttribute("style",r||"")}}function Gn(e){return e.replace(/([a-z])([A-Z])/g,"$1-$2").toLowerCase()}function he(e,t=()=>{}){let r=!1;return function(){r?t.apply(this,arguments):(r=!0,e.apply(this,arguments))}}d("transition",(e,{value:t,modifiers:r,expression:n},{evaluate:i})=>{typeof n=="function"&&(n=i(n)),n!==!1&&(!n||typeof n=="boolean"?Yn(e,r,t):Jn(e,n,t))});function Jn(e,t,r){Or(e,me,""),{enter:i=>{e._x_transition.enter.during=i},"enter-start":i=>{e._x_transition.enter.start=i},"enter-end":i=>{e._x_transition.enter.end=i},leave:i=>{e._x_transition.leave.during=i},"leave-start":i=>{e._x_transition.leave.start=i},"leave-end":i=>{e._x_transition.leave.end=i}}[r](t)}function Yn(e,t,r){Or(e,X);let n=!t.includes("in")&&!t.includes("out")&&!r,i=n||t.includes("in")||["enter"].includes(r),o=n||t.includes("out")||["leave"].includes(r);t.includes("in")&&!n&&(t=t.filter((g,b)=>b<t.indexOf("out"))),t.includes("out")&&!n&&(t=t.filter((g,b)=>b>t.indexOf("out")));let s=!t.includes("opacity")&&!t.includes("scale"),a=s||t.includes("opacity"),c=s||t.includes("scale"),l=a?0:1,u=c?_e(t,"scale",95)/100:1,p=_e(t,"delay",0)/1e3,h=_e(t,"origin","center"),w="opacity, transform",F=_e(t,"duration",150)/1e3,Ee=_e(t,"duration",75)/1e3,f="cubic-bezier(0.4, 0.0, 0.2, 1)";i&&(e._x_transition.enter.during={transformOrigin:h,transitionDelay:`${p}s`,transitionProperty:w,transitionDuration:`${F}s`,transitionTimingFunction:f},e._x_transition.enter.start={opacity:l,transform:`scale(${u})`},e._x_transition.enter.end={opacity:1,transform:"scale(1)"}),o&&(e._x_transition.leave.during={transformOrigin:h,transitionDelay:`${p}s`,transitionProperty:w,transitionDuration:`${Ee}s`,transitionTimingFunction:f},e._x_transition.leave.start={opacity:1,transform:"scale(1)"},e._x_transition.leave.end={opacity:l,transform:`scale(${u})`})}function Or(e,t,r={}){e._x_transition||(e._x_transition={enter:{during:r,start:r,end:r},leave:{during:r,start:r,end:r},in(n=()=>{},i=()=>{}){Fe(e,t,{during:this.enter.during,start:this.enter.start,end:this.enter.end},n,i)},out(n=()=>{},i=()=>{}){Fe(e,t,{during:this.leave.during,start:this.leave.start,end:this.leave.end},n,i)}})}window.Element.prototype._x_toggleAndCascadeWithTransitions=function(e,t,r,n){let i=document.visibilityState==="visible"?requestAnimationFrame:setTimeout,o=()=>i(r);if(t){e._x_transition&&(e._x_transition.enter||e._x_transition.leave)?e._x_transition.enter&&(Object.entries(e._x_transition.enter.during).length||Object.entries(e._x_transition.enter.start).length||Object.entries(e._x_transition.enter.end).length)?e._x_transition.in(r):o():e._x_transition?e._x_transition.in(r):o();return}e._x_hidePromise=e._x_transition?new Promise((s,a)=>{e._x_transition.out(()=>{},()=>s(n)),e._x_transitioning&&e._x_transitioning.beforeCancel(()=>a({isFromCancelledTransition:!0}))}):Promise.resolve(n),queueMicrotask(()=>{let s=Cr(e);s?(s._x_hideChildren||(s._x_hideChildren=[]),s._x_hideChildren.push(e)):i(()=>{let a=c=>{let l=Promise.all([c._x_hidePromise,...(c._x_hideChildren||[]).map(a)]).then(([u])=>u?.());return delete c._x_hidePromise,delete c._x_hideChildren,l};a(e).catch(c=>{if(!c.isFromCancelledTransition)throw c})})})};function Cr(e){let t=e.parentNode;if(t)return t._x_hidePromise?t:Cr(t)}function Fe(e,t,{during:r,start:n,end:i}={},o=()=>{},s=()=>{}){if(e._x_transitioning&&e._x_transitioning.cancel(),Object.keys(r).length===0&&Object.keys(n).length===0&&Object.keys(i).length===0){o(),s();return}let a,c,l;Xn(e,{start(){a=t(e,n)},during(){c=t(e,r)},before:o,end(){a(),l=t(e,i)},after:s,cleanup(){c(),l()}})}function Xn(e,t){let r,n,i,o=he(()=>{m(()=>{r=!0,n||t.before(),i||(t.end(),je()),t.after(),e.isConnected&&t.cleanup(),delete e._x_transitioning})});e._x_transitioning={beforeCancels:[],beforeCancel(s){this.beforeCancels.push(s)},cancel:he(function(){for(;this.beforeCancels.length;)this.beforeCancels.shift()();o()}),finish:o},m(()=>{t.start(),t.during()}),Sr(),requestAnimationFrame(()=>{if(r)return;let s=Number(getComputedStyle(e).transitionDuration.replace(/,.*/,"").replace("s",""))*1e3,a=Number(getComputedStyle(e).transitionDelay.replace(/,.*/,"").replace("s",""))*1e3;s===0&&(s=Number(getComputedStyle(e).animationDuration.replace("s",""))*1e3),m(()=>{t.before()}),n=!0,requestAnimationFrame(()=>{r||(m(()=>{t.end()}),je(),setTimeout(e._x_transitioning.finish,s+a),i=!0)})})}function _e(e,t,r){if(e.indexOf(t)===-1)return r;let n=e[e.indexOf(t)+1];if(!n||t==="scale"&&isNaN(n))return r;if(t==="duration"||t==="delay"){let i=n.match(/([0-9]+)ms/);if(i)return i[1]}return t==="origin"&&["top","right","left","center","bottom"].includes(e[e.indexOf(t)+2])?[n,e[e.indexOf(t)+2]].join(" "):n}var I=!1;function A(e,t=()=>{}){return(...r)=>I?t(...r):e(...r)}function Tr(e){return(...t)=>I&&e(...t)}var Rr=[];function H(e){Rr.push(e)}function Mr(e,t){Rr.forEach(r=>r(e,t)),I=!0,kr(()=>{S(t,(r,n)=>{n(r,()=>{})})}),I=!1}var Be=!1;function Nr(e,t){t._x_dataStack||(t._x_dataStack=e._x_dataStack),I=!0,Be=!0,kr(()=>{Zn(t)}),I=!1,Be=!1}function Zn(e){let t=!1;S(e,(n,i)=>{D(n,(o,s)=>{if(t&&wr(o))return s();t=!0,i(o,s)})})}function kr(e){let t=N;ct((r,n)=>{let i=t(r);return $(i),()=>{}}),e(),ct(t)}function ge(e,t,r,n=[]){switch(e._x_bindings||(e._x_bindings=T({})),e._x_bindings[t]=r,t=n.includes("camel")?si(t):t,t){case"value":Qn(e,r);break;case"style":ti(e,r);break;case"class":ei(e,r);break;case"selected":case"checked":ri(e,t,r);break;default:Pr(e,t,r);break}}function Qn(e,t){if(Ot(e))e.attributes.value===void 0&&(e.value=t),window.fromModel&&(typeof t=="boolean"?e.checked=xe(e.value)===t:e.checked=Dr(e.value,t));else if(ze(e))Number.isInteger(t)?e.value=t:!Array.isArray(t)&&typeof t!="boolean"&&![null,void 0].includes(t)?e.value=String(t):Array.isArray(t)?e.checked=t.some(r=>Dr(r,e.value)):e.checked=!!t;else if(e.tagName==="SELECT")oi(e,t);else{if(e.value===t)return;e.value=t===void 0?"":t}}function ei(e,t){e._x_undoAddedClasses&&e._x_undoAddedClasses(),e._x_undoAddedClasses=me(e,t)}function ti(e,t){e._x_undoAddedStyles&&e._x_undoAddedStyles(),e._x_undoAddedStyles=X(e,t)}function ri(e,t,r){Pr(e,t,r),ii(e,t,r)}function Pr(e,t,r){[null,void 0,!1].includes(r)&&ci(t)?e.removeAttribute(t):(Ir(t)&&(r=t),ni(e,t,r))}function ni(e,t,r){e.getAttribute(t)!=r&&e.setAttribute(t,r)}function ii(e,t,r){e[t]!==r&&(e[t]=r)}function oi(e,t){let r=[].concat(t).map(n=>n+"");Array.from(e.options).forEach(n=>{n.selected=r.includes(n.value)})}function si(e){return e.toLowerCase().replace(/-(\w)/g,(t,r)=>r.toUpperCase())}function Dr(e,t){return e==t}function xe(e){return[1,"1","true","on","yes",!0].includes(e)?!0:[0,"0","false","off","no",!1].includes(e)?!1:e?Boolean(e):null}var ai=new Set(["allowfullscreen","async","autofocus","autoplay","checked","controls","default","defer","disabled","formnovalidate","inert","ismap","itemscope","loop","multiple","muted","nomodule","novalidate","open","playsinline","readonly","required","reversed","selected","shadowrootclonable","shadowrootdelegatesfocus","shadowrootserializable"]);function Ir(e){return ai.has(e)}function ci(e){return!["aria-pressed","aria-checked","aria-expanded","aria-selected"].includes(e)}function Lr(e,t,r){return e._x_bindings&&e._x_bindings[t]!==void 0?e._x_bindings[t]:jr(e,t,r)}function $r(e,t,r,n=!0){if(e._x_bindings&&e._x_bindings[t]!==void 0)return e._x_bindings[t];if(e._x_inlineBindings&&e._x_inlineBindings[t]!==void 0){let i=e._x_inlineBindings[t];return i.extract=n,ke(()=>R(e,i.expression))}return jr(e,t,r)}function jr(e,t,r){let n=e.getAttribute(t);return n===null?typeof r=="function"?r():r:n===""?!0:Ir(t)?!![t,"true"].includes(n):n}function ze(e){return e.type==="checkbox"||e.localName==="ui-checkbox"||e.localName==="ui-switch"}function Ot(e){return e.type==="radio"||e.localName==="ui-radio"}function Ke(e,t){var r;return function(){var n=this,i=arguments,o=function(){r=null,e.apply(n,i)};clearTimeout(r),r=setTimeout(o,t)}}function He(e,t){let r;return function(){let n=this,i=arguments;r||(e.apply(n,i),r=!0,setTimeout(()=>r=!1,t))}}function Ve({get:e,set:t},{get:r,set:n}){let i=!0,o,s,a=N(()=>{let c=e(),l=r();if(i)n(Ct(c)),i=!1;else{let u=JSON.stringify(c),p=JSON.stringify(l);u!==o?n(Ct(c)):u!==p&&t(Ct(l))}o=JSON.stringify(e()),s=JSON.stringify(r())});return()=>{$(a)}}function Ct(e){return typeof e=="object"?JSON.parse(JSON.stringify(e)):e}function Fr(e){(Array.isArray(e)?e:[e]).forEach(r=>r(K))}var Z={},Br=!1;function zr(e,t){if(Br||(Z=T(Z),Br=!0),t===void 0)return Z[e];Z[e]=t,Te(Z[e]),typeof t=="object"&&t!==null&&t.hasOwnProperty("init")&&typeof t.init=="function"&&Z[e].init()}function Kr(){return Z}var Hr={};function Vr(e,t){let r=typeof t!="function"?()=>t:t;return e instanceof Element?Tt(e,r()):(Hr[e]=r,()=>{})}function qr(e){return Object.entries(Hr).forEach(([t,r])=>{Object.defineProperty(e,t,{get(){return(...n)=>r(...n)}})}),e}function Tt(e,t,r){let n=[];for(;n.length;)n.pop()();let i=Object.entries(t).map(([s,a])=>({name:s,value:a})),o=Et(i);return i=i.map(s=>o.find(a=>a.name===s.name)?{name:`x-bind:${s.name}`,value:`"${s.value}"`}:s),pe(e,i,r).map(s=>{n.push(s.runCleanups),s()}),()=>{for(;n.length;)n.pop()()}}var Ur={};function Wr(e,t){Ur[e]=t}function Gr(e,t){return Object.entries(Ur).forEach(([r,n])=>{Object.defineProperty(e,r,{get(){return(...i)=>n.bind(t)(...i)},enumerable:!1})}),e}var li={get reactive(){return T},get release(){return $},get effect(){return N},get raw(){return at},version:"3.14.9",flushAndStopDeferringMutations:nr,dontAutoEvaluateFunctions:ke,disableEffectScheduling:Gt,startObservingMutations:ue,stopObservingMutations:dt,setReactivityEngine:Jt,onAttributeRemoved:Oe,onAttributesAdded:Ae,closestDataStack:B,skipDuringClone:A,onlyDuringClone:Tr,addRootSelector:Le,addInitSelector:$e,interceptClone:H,addScopeToNode:k,deferMutations:rr,mapAttributes:ne,evaluateLater:x,interceptInit:vr,setEvaluator:ar,mergeProxies:z,extractProp:$r,findClosest:j,onElRemoved:te,closestRoot:Y,destroyTree:P,interceptor:Re,transition:Fe,setStyles:X,mutateDom:m,directive:d,entangle:Ve,throttle:He,debounce:Ke,evaluate:R,initTree:S,nextTick:ie,prefixed:C,prefix:cr,plugin:Fr,magic:y,store:zr,start:gr,clone:Nr,cloneNode:Mr,bound:Lr,$data:Ce,watch:ve,walk:D,data:Wr,bind:Vr},K=li;function Rt(e,t){let r=Object.create(null),n=e.split(",");for(let i=0;i<n.length;i++)r[n[i]]=!0;return t?i=>!!r[i.toLowerCase()]:i=>!!r[i]}var ui="itemscope,allowfullscreen,formnovalidate,ismap,nomodule,novalidate,readonly";var Ls=Rt(ui+",async,autofocus,autoplay,controls,default,defer,disabled,hidden,loop,open,required,reversed,scoped,seamless,checked,muted,multiple,selected");var Jr=Object.freeze({}),$s=Object.freeze([]);var fi=Object.prototype.hasOwnProperty,ye=(e,t)=>fi.call(e,t),V=Array.isArray,oe=e=>Yr(e)==="[object Map]";var di=e=>typeof e=="string",qe=e=>typeof e=="symbol",be=e=>e!==null&&typeof e=="object";var pi=Object.prototype.toString,Yr=e=>pi.call(e),Mt=e=>Yr(e).slice(8,-1);var Ue=e=>di(e)&&e!=="NaN"&&e[0]!=="-"&&""+parseInt(e,10)===e;var We=e=>{let t=Object.create(null);return r=>t[r]||(t[r]=e(r))},mi=/-(\w)/g,js=We(e=>e.replace(mi,(t,r)=>r?r.toUpperCase():"")),hi=/\B([A-Z])/g,Fs=We(e=>e.replace(hi,"-$1").toLowerCase()),Nt=We(e=>e.charAt(0).toUpperCase()+e.slice(1)),Bs=We(e=>e?`on${Nt(e)}`:""),kt=(e,t)=>e!==t&&(e===e||t===t);var Dt=new WeakMap,we=[],L,Q=Symbol("iterate"),Pt=Symbol("Map key iterate");function _i(e){return e&&e._isEffect===!0}function rn(e,t=Jr){_i(e)&&(e=e.raw);let r=xi(e,t);return t.lazy||r(),r}function nn(e){e.active&&(on(e),e.options.onStop&&e.options.onStop(),e.active=!1)}var gi=0;function xi(e,t){let r=function(){if(!r.active)return e();if(!we.includes(r)){on(r);try{return bi(),we.push(r),L=r,e()}finally{we.pop(),sn(),L=we[we.length-1]}}};return r.id=gi++,r.allowRecurse=!!t.allowRecurse,r._isEffect=!0,r.active=!0,r.raw=e,r.deps=[],r.options=t,r}function on(e){let{deps:t}=e;if(t.length){for(let r=0;r<t.length;r++)t[r].delete(e);t.length=0}}var se=!0,Lt=[];function yi(){Lt.push(se),se=!1}function bi(){Lt.push(se),se=!0}function sn(){let e=Lt.pop();se=e===void 0?!0:e}function M(e,t,r){if(!se||L===void 0)return;let n=Dt.get(e);n||Dt.set(e,n=new Map);let i=n.get(r);i||n.set(r,i=new Set),i.has(L)||(i.add(L),L.deps.push(i),L.options.onTrack&&L.options.onTrack({effect:L,target:e,type:t,key:r}))}function U(e,t,r,n,i,o){let s=Dt.get(e);if(!s)return;let a=new Set,c=u=>{u&&u.forEach(p=>{(p!==L||p.allowRecurse)&&a.add(p)})};if(t==="clear")s.forEach(c);else if(r==="length"&&V(e))s.forEach((u,p)=>{(p==="length"||p>=n)&&c(u)});else switch(r!==void 0&&c(s.get(r)),t){case"add":V(e)?Ue(r)&&c(s.get("length")):(c(s.get(Q)),oe(e)&&c(s.get(Pt)));break;case"delete":V(e)||(c(s.get(Q)),oe(e)&&c(s.get(Pt)));break;case"set":oe(e)&&c(s.get(Q));break}let l=u=>{u.options.onTrigger&&u.options.onTrigger({effect:u,target:e,key:r,type:t,newValue:n,oldValue:i,oldTarget:o}),u.options.scheduler?u.options.scheduler(u):u()};a.forEach(l)}var wi=Rt("__proto__,__v_isRef,__isVue"),an=new Set(Object.getOwnPropertyNames(Symbol).map(e=>Symbol[e]).filter(qe)),Ei=cn();var vi=cn(!0);var Xr=Si();function Si(){let e={};return["includes","indexOf","lastIndexOf"].forEach(t=>{e[t]=function(...r){let n=_(this);for(let o=0,s=this.length;o<s;o++)M(n,"get",o+"");let i=n[t](...r);return i===-1||i===!1?n[t](...r.map(_)):i}}),["push","pop","shift","unshift","splice"].forEach(t=>{e[t]=function(...r){yi();let n=_(this)[t].apply(this,r);return sn(),n}}),e}function cn(e=!1,t=!1){return function(n,i,o){if(i==="__v_isReactive")return!e;if(i==="__v_isReadonly")return e;if(i==="__v_raw"&&o===(e?t?Bi:dn:t?Fi:fn).get(n))return n;let s=V(n);if(!e&&s&&ye(Xr,i))return Reflect.get(Xr,i,o);let a=Reflect.get(n,i,o);return(qe(i)?an.has(i):wi(i))||(e||M(n,"get",i),t)?a:It(a)?!s||!Ue(i)?a.value:a:be(a)?e?pn(a):et(a):a}}var Ai=Oi();function Oi(e=!1){return function(r,n,i,o){let s=r[n];if(!e&&(i=_(i),s=_(s),!V(r)&&It(s)&&!It(i)))return s.value=i,!0;let a=V(r)&&Ue(n)?Number(n)<r.length:ye(r,n),c=Reflect.set(r,n,i,o);return r===_(o)&&(a?kt(i,s)&&U(r,"set",n,i,s):U(r,"add",n,i)),c}}function Ci(e,t){let r=ye(e,t),n=e[t],i=Reflect.deleteProperty(e,t);return i&&r&&U(e,"delete",t,void 0,n),i}function Ti(e,t){let r=Reflect.has(e,t);return(!qe(t)||!an.has(t))&&M(e,"has",t),r}function Ri(e){return M(e,"iterate",V(e)?"length":Q),Reflect.ownKeys(e)}var Mi={get:Ei,set:Ai,deleteProperty:Ci,has:Ti,ownKeys:Ri},Ni={get:vi,set(e,t){return console.warn(`Set operation on key "${String(t)}" failed: target is readonly.`,e),!0},deleteProperty(e,t){return console.warn(`Delete operation on key "${String(t)}" failed: target is readonly.`,e),!0}};var $t=e=>be(e)?et(e):e,jt=e=>be(e)?pn(e):e,Ft=e=>e,Qe=e=>Reflect.getPrototypeOf(e);function Ge(e,t,r=!1,n=!1){e=e.__v_raw;let i=_(e),o=_(t);t!==o&&!r&&M(i,"get",t),!r&&M(i,"get",o);let{has:s}=Qe(i),a=n?Ft:r?jt:$t;if(s.call(i,t))return a(e.get(t));if(s.call(i,o))return a(e.get(o));e!==i&&e.get(t)}function Je(e,t=!1){let r=this.__v_raw,n=_(r),i=_(e);return e!==i&&!t&&M(n,"has",e),!t&&M(n,"has",i),e===i?r.has(e):r.has(e)||r.has(i)}function Ye(e,t=!1){return e=e.__v_raw,!t&&M(_(e),"iterate",Q),Reflect.get(e,"size",e)}function Zr(e){e=_(e);let t=_(this);return Qe(t).has.call(t,e)||(t.add(e),U(t,"add",e,e)),this}function Qr(e,t){t=_(t);let r=_(this),{has:n,get:i}=Qe(r),o=n.call(r,e);o?un(r,n,e):(e=_(e),o=n.call(r,e));let s=i.call(r,e);return r.set(e,t),o?kt(t,s)&&U(r,"set",e,t,s):U(r,"add",e,t),this}function en(e){let t=_(this),{has:r,get:n}=Qe(t),i=r.call(t,e);i?un(t,r,e):(e=_(e),i=r.call(t,e));let o=n?n.call(t,e):void 0,s=t.delete(e);return i&&U(t,"delete",e,void 0,o),s}function tn(){let e=_(this),t=e.size!==0,r=oe(e)?new Map(e):new Set(e),n=e.clear();return t&&U(e,"clear",void 0,void 0,r),n}function Xe(e,t){return function(n,i){let o=this,s=o.__v_raw,a=_(s),c=t?Ft:e?jt:$t;return!e&&M(a,"iterate",Q),s.forEach((l,u)=>n.call(i,c(l),c(u),o))}}function Ze(e,t,r){return function(...n){let i=this.__v_raw,o=_(i),s=oe(o),a=e==="entries"||e===Symbol.iterator&&s,c=e==="keys"&&s,l=i[e](...n),u=r?Ft:t?jt:$t;return!t&&M(o,"iterate",c?Pt:Q),{next(){let{value:p,done:h}=l.next();return h?{value:p,done:h}:{value:a?[u(p[0]),u(p[1])]:u(p),done:h}},[Symbol.iterator](){return this}}}}function q(e){return function(...t){{let r=t[0]?`on key "${t[0]}" `:"";console.warn(`${Nt(e)} operation ${r}failed: target is readonly.`,_(this))}return e==="delete"?!1:this}}function ki(){let e={get(o){return Ge(this,o)},get size(){return Ye(this)},has:Je,add:Zr,set:Qr,delete:en,clear:tn,forEach:Xe(!1,!1)},t={get(o){return Ge(this,o,!1,!0)},get size(){return Ye(this)},has:Je,add:Zr,set:Qr,delete:en,clear:tn,forEach:Xe(!1,!0)},r={get(o){return Ge(this,o,!0)},get size(){return Ye(this,!0)},has(o){return Je.call(this,o,!0)},add:q("add"),set:q("set"),delete:q("delete"),clear:q("clear"),forEach:Xe(!0,!1)},n={get(o){return Ge(this,o,!0,!0)},get size(){return Ye(this,!0)},has(o){return Je.call(this,o,!0)},add:q("add"),set:q("set"),delete:q("delete"),clear:q("clear"),forEach:Xe(!0,!0)};return["keys","values","entries",Symbol.iterator].forEach(o=>{e[o]=Ze(o,!1,!1),r[o]=Ze(o,!0,!1),t[o]=Ze(o,!1,!0),n[o]=Ze(o,!0,!0)}),[e,r,t,n]}var[Di,Pi,Ii,Li]=ki();function ln(e,t){let r=t?e?Li:Ii:e?Pi:Di;return(n,i,o)=>i==="__v_isReactive"?!e:i==="__v_isReadonly"?e:i==="__v_raw"?n:Reflect.get(ye(r,i)&&i in n?r:n,i,o)}var $i={get:ln(!1,!1)};var ji={get:ln(!0,!1)};function un(e,t,r){let n=_(r);if(n!==r&&t.call(e,n)){let i=Mt(e);console.warn(`Reactive ${i} contains both the raw and reactive versions of the same object${i==="Map"?" as keys":""}, which can lead to inconsistencies. Avoid differentiating between the raw and reactive versions of an object and only use the reactive version if possible.`)}}var fn=new WeakMap,Fi=new WeakMap,dn=new WeakMap,Bi=new WeakMap;function zi(e){switch(e){case"Object":case"Array":return 1;case"Map":case"Set":case"WeakMap":case"WeakSet":return 2;default:return 0}}function Ki(e){return e.__v_skip||!Object.isExtensible(e)?0:zi(Mt(e))}function et(e){return e&&e.__v_isReadonly?e:mn(e,!1,Mi,$i,fn)}function pn(e){return mn(e,!0,Ni,ji,dn)}function mn(e,t,r,n,i){if(!be(e))return console.warn(`value cannot be made reactive: ${String(e)}`),e;if(e.__v_raw&&!(t&&e.__v_isReactive))return e;let o=i.get(e);if(o)return o;let s=Ki(e);if(s===0)return e;let a=new Proxy(e,s===2?n:r);return i.set(e,a),a}function _(e){return e&&_(e.__v_raw)||e}function It(e){return Boolean(e&&e.__v_isRef===!0)}y("nextTick",()=>ie);y("dispatch",e=>J.bind(J,e));y("watch",(e,{evaluateLater:t,cleanup:r})=>(n,i)=>{let o=t(n),a=ve(()=>{let c;return o(l=>c=l),c},i);r(a)});y("store",Kr);y("data",e=>Ce(e));y("root",e=>Y(e));y("refs",e=>(e._x_refs_proxy||(e._x_refs_proxy=z(Hi(e))),e._x_refs_proxy));function Hi(e){let t=[];return j(e,r=>{r._x_refs&&t.push(r._x_refs)}),t}var Bt={};function zt(e){return Bt[e]||(Bt[e]=0),++Bt[e]}function hn(e,t){return j(e,r=>{if(r._x_ids&&r._x_ids[t])return!0})}function _n(e,t){e._x_ids||(e._x_ids={}),e._x_ids[t]||(e._x_ids[t]=zt(t))}y("id",(e,{cleanup:t})=>(r,n=null)=>{let i=`${r}${n?`-${n}`:""}`;return Vi(e,i,t,()=>{let o=hn(e,r),s=o?o._x_ids[r]:zt(r);return n?`${r}-${s}-${n}`:`${r}-${s}`})});H((e,t)=>{e._x_id&&(t._x_id=e._x_id)});function Vi(e,t,r,n){if(e._x_id||(e._x_id={}),e._x_id[t])return e._x_id[t];let i=n();return e._x_id[t]=i,r(()=>{delete e._x_id[t]}),i}y("el",e=>e);gn("Focus","focus","focus");gn("Persist","persist","persist");function gn(e,t,r){y(t,n=>E(`You can't use [$${t}] without first installing the "${e}" plugin here: https://alpinejs.dev/plugins/${r}`,n))}d("modelable",(e,{expression:t},{effect:r,evaluateLater:n,cleanup:i})=>{let o=n(t),s=()=>{let u;return o(p=>u=p),u},a=n(`${t} = __placeholder`),c=u=>a(()=>{},{scope:{__placeholder:u}}),l=s();c(l),queueMicrotask(()=>{if(!e._x_model)return;e._x_removeModelListeners.default();let u=e._x_model.get,p=e._x_model.set,h=Ve({get(){return u()},set(w){p(w)}},{get(){return s()},set(w){c(w)}});i(h)})});d("teleport",(e,{modifiers:t,expression:r},{cleanup:n})=>{e.tagName.toLowerCase()!=="template"&&E("x-teleport can only be used on a <template> tag",e);let i=xn(r),o=e.content.cloneNode(!0).firstElementChild;e._x_teleport=o,o._x_teleportBack=e,e.setAttribute("data-teleport-template",!0),o.setAttribute("data-teleport-target",!0),e._x_forwardEvents&&e._x_forwardEvents.forEach(a=>{o.addEventListener(a,c=>{c.stopPropagation(),e.dispatchEvent(new c.constructor(c.type,c))})}),k(o,{},e);let s=(a,c,l)=>{l.includes("prepend")?c.parentNode.insertBefore(a,c):l.includes("append")?c.parentNode.insertBefore(a,c.nextSibling):c.appendChild(a)};m(()=>{s(o,i,t),A(()=>{S(o)})()}),e._x_teleportPutBack=()=>{let a=xn(r);m(()=>{s(e._x_teleport,a,t)})},n(()=>m(()=>{o.remove(),P(o)}))});var qi=document.createElement("div");function xn(e){let t=A(()=>document.querySelector(e),()=>qi)();return t||E(`Cannot find x-teleport element for selector: "${e}"`),t}var yn=()=>{};yn.inline=(e,{modifiers:t},{cleanup:r})=>{t.includes("self")?e._x_ignoreSelf=!0:e._x_ignore=!0,r(()=>{t.includes("self")?delete e._x_ignoreSelf:delete e._x_ignore})};d("ignore",yn);d("effect",A((e,{expression:t},{effect:r})=>{r(x(e,t))}));function ae(e,t,r,n){let i=e,o=c=>n(c),s={},a=(c,l)=>u=>l(c,u);if(r.includes("dot")&&(t=Ui(t)),r.includes("camel")&&(t=Wi(t)),r.includes("passive")&&(s.passive=!0),r.includes("capture")&&(s.capture=!0),r.includes("window")&&(i=window),r.includes("document")&&(i=document),r.includes("debounce")){let c=r[r.indexOf("debounce")+1]||"invalid-wait",l=tt(c.split("ms")[0])?Number(c.split("ms")[0]):250;o=Ke(o,l)}if(r.includes("throttle")){let c=r[r.indexOf("throttle")+1]||"invalid-wait",l=tt(c.split("ms")[0])?Number(c.split("ms")[0]):250;o=He(o,l)}return r.includes("prevent")&&(o=a(o,(c,l)=>{l.preventDefault(),c(l)})),r.includes("stop")&&(o=a(o,(c,l)=>{l.stopPropagation(),c(l)})),r.includes("once")&&(o=a(o,(c,l)=>{c(l),i.removeEventListener(t,o,s)})),(r.includes("away")||r.includes("outside"))&&(i=document,o=a(o,(c,l)=>{e.contains(l.target)||l.target.isConnected!==!1&&(e.offsetWidth<1&&e.offsetHeight<1||e._x_isShown!==!1&&c(l))})),r.includes("self")&&(o=a(o,(c,l)=>{l.target===e&&c(l)})),(Ji(t)||wn(t))&&(o=a(o,(c,l)=>{Yi(l,r)||c(l)})),i.addEventListener(t,o,s),()=>{i.removeEventListener(t,o,s)}}function Ui(e){return e.replace(/-/g,".")}function Wi(e){return e.toLowerCase().replace(/-(\w)/g,(t,r)=>r.toUpperCase())}function tt(e){return!Array.isArray(e)&&!isNaN(e)}function Gi(e){return[" ","_"].includes(e)?e:e.replace(/([a-z])([A-Z])/g,"$1-$2").replace(/[_\s]/,"-").toLowerCase()}function Ji(e){return["keydown","keyup"].includes(e)}function wn(e){return["contextmenu","click","mouse"].some(t=>e.includes(t))}function Yi(e,t){let r=t.filter(o=>!["window","document","prevent","stop","once","capture","self","away","outside","passive"].includes(o));if(r.includes("debounce")){let o=r.indexOf("debounce");r.splice(o,tt((r[o+1]||"invalid-wait").split("ms")[0])?2:1)}if(r.includes("throttle")){let o=r.indexOf("throttle");r.splice(o,tt((r[o+1]||"invalid-wait").split("ms")[0])?2:1)}if(r.length===0||r.length===1&&bn(e.key).includes(r[0]))return!1;let i=["ctrl","shift","alt","meta","cmd","super"].filter(o=>r.includes(o));return r=r.filter(o=>!i.includes(o)),!(i.length>0&&i.filter(s=>((s==="cmd"||s==="super")&&(s="meta"),e[`${s}Key`])).length===i.length&&(wn(e.type)||bn(e.key).includes(r[0])))}function bn(e){if(!e)return[];e=Gi(e);let t={ctrl:"control",slash:"/",space:" ",spacebar:" ",cmd:"meta",esc:"escape",up:"arrow-up",down:"arrow-down",left:"arrow-left",right:"arrow-right",period:".",comma:",",equal:"=",minus:"-",underscore:"_"};return t[e]=e,Object.keys(t).map(r=>{if(t[r]===e)return r}).filter(r=>r)}d("model",(e,{modifiers:t,expression:r},{effect:n,cleanup:i})=>{let o=e;t.includes("parent")&&(o=e.parentNode);let s=x(o,r),a;typeof r=="string"?a=x(o,`${r} = __placeholder`):typeof r=="function"&&typeof r()=="string"?a=x(o,`${r()} = __placeholder`):a=()=>{};let c=()=>{let h;return s(w=>h=w),En(h)?h.get():h},l=h=>{let w;s(F=>w=F),En(w)?w.set(h):a(()=>{},{scope:{__placeholder:h}})};typeof r=="string"&&e.type==="radio"&&m(()=>{e.hasAttribute("name")||e.setAttribute("name",r)});var u=e.tagName.toLowerCase()==="select"||["checkbox","radio"].includes(e.type)||t.includes("lazy")?"change":"input";let p=I?()=>{}:ae(e,u,t,h=>{l(Kt(e,t,h,c()))});if(t.includes("fill")&&([void 0,null,""].includes(c())||ze(e)&&Array.isArray(c())||e.tagName.toLowerCase()==="select"&&e.multiple)&&l(Kt(e,t,{target:e},c())),e._x_removeModelListeners||(e._x_removeModelListeners={}),e._x_removeModelListeners.default=p,i(()=>e._x_removeModelListeners.default()),e.form){let h=ae(e.form,"reset",[],w=>{ie(()=>e._x_model&&e._x_model.set(Kt(e,t,{target:e},c())))});i(()=>h())}e._x_model={get(){return c()},set(h){l(h)}},e._x_forceModelUpdate=h=>{h===void 0&&typeof r=="string"&&r.match(/\./)&&(h=""),window.fromModel=!0,m(()=>ge(e,"value",h)),delete window.fromModel},n(()=>{let h=c();t.includes("unintrusive")&&document.activeElement.isSameNode(e)||e._x_forceModelUpdate(h)})});function Kt(e,t,r,n){return m(()=>{if(r instanceof CustomEvent&&r.detail!==void 0)return r.detail!==null&&r.detail!==void 0?r.detail:r.target.value;if(ze(e))if(Array.isArray(n)){let i=null;return t.includes("number")?i=Ht(r.target.value):t.includes("boolean")?i=xe(r.target.value):i=r.target.value,r.target.checked?n.includes(i)?n:n.concat([i]):n.filter(o=>!Xi(o,i))}else return r.target.checked;else{if(e.tagName.toLowerCase()==="select"&&e.multiple)return t.includes("number")?Array.from(r.target.selectedOptions).map(i=>{let o=i.value||i.text;return Ht(o)}):t.includes("boolean")?Array.from(r.target.selectedOptions).map(i=>{let o=i.value||i.text;return xe(o)}):Array.from(r.target.selectedOptions).map(i=>i.value||i.text);{let i;return Ot(e)?r.target.checked?i=r.target.value:i=n:i=r.target.value,t.includes("number")?Ht(i):t.includes("boolean")?xe(i):t.includes("trim")?i.trim():i}}})}function Ht(e){let t=e?parseFloat(e):null;return Zi(t)?t:e}function Xi(e,t){return e==t}function Zi(e){return!Array.isArray(e)&&!isNaN(e)}function En(e){return e!==null&&typeof e=="object"&&typeof e.get=="function"&&typeof e.set=="function"}d("cloak",e=>queueMicrotask(()=>m(()=>e.removeAttribute(C("cloak")))));$e(()=>`[${C("init")}]`);d("init",A((e,{expression:t},{evaluate:r})=>typeof t=="string"?!!t.trim()&&r(t,{},!1):r(t,{},!1)));d("text",(e,{expression:t},{effect:r,evaluateLater:n})=>{let i=n(t);r(()=>{i(o=>{m(()=>{e.textContent=o})})})});d("html",(e,{expression:t},{effect:r,evaluateLater:n})=>{let i=n(t);r(()=>{i(o=>{m(()=>{e.innerHTML=o,e._x_ignoreSelf=!0,S(e),delete e._x_ignoreSelf})})})});ne(Pe(":",Ie(C("bind:"))));var vn=(e,{value:t,modifiers:r,expression:n,original:i},{effect:o,cleanup:s})=>{if(!t){let c={};qr(c),x(e,n)(u=>{Tt(e,u,i)},{scope:c});return}if(t==="key")return Qi(e,n);if(e._x_inlineBindings&&e._x_inlineBindings[t]&&e._x_inlineBindings[t].extract)return;let a=x(e,n);o(()=>a(c=>{c===void 0&&typeof n=="string"&&n.match(/\./)&&(c=""),m(()=>ge(e,t,c,r))})),s(()=>{e._x_undoAddedClasses&&e._x_undoAddedClasses(),e._x_undoAddedStyles&&e._x_undoAddedStyles()})};vn.inline=(e,{value:t,modifiers:r,expression:n})=>{t&&(e._x_inlineBindings||(e._x_inlineBindings={}),e._x_inlineBindings[t]={expression:n,extract:!1})};d("bind",vn);function Qi(e,t){e._x_keyExpression=t}Le(()=>`[${C("data")}]`);d("data",(e,{expression:t},{cleanup:r})=>{if(eo(e))return;t=t===""?"{}":t;let n={};fe(n,e);let i={};Gr(i,n);let o=R(e,t,{scope:i});(o===void 0||o===!0)&&(o={}),fe(o,e);let s=T(o);Te(s);let a=k(e,s);s.init&&R(e,s.init),r(()=>{s.destroy&&R(e,s.destroy),a()})});H((e,t)=>{e._x_dataStack&&(t._x_dataStack=e._x_dataStack,t.setAttribute("data-has-alpine-state",!0))});function eo(e){return I?Be?!0:e.hasAttribute("data-has-alpine-state"):!1}d("show",(e,{modifiers:t,expression:r},{effect:n})=>{let i=x(e,r);e._x_doHide||(e._x_doHide=()=>{m(()=>{e.style.setProperty("display","none",t.includes("important")?"important":void 0)})}),e._x_doShow||(e._x_doShow=()=>{m(()=>{e.style.length===1&&e.style.display==="none"?e.removeAttribute("style"):e.style.removeProperty("display")})});let o=()=>{e._x_doHide(),e._x_isShown=!1},s=()=>{e._x_doShow(),e._x_isShown=!0},a=()=>setTimeout(s),c=he(p=>p?s():o(),p=>{typeof e._x_toggleAndCascadeWithTransitions=="function"?e._x_toggleAndCascadeWithTransitions(e,p,s,o):p?a():o()}),l,u=!0;n(()=>i(p=>{!u&&p===l||(t.includes("immediate")&&(p?a():o()),c(p),l=p,u=!1)}))});d("for",(e,{expression:t},{effect:r,cleanup:n})=>{let i=ro(t),o=x(e,i.items),s=x(e,e._x_keyExpression||"index");e._x_prevKeys=[],e._x_lookup={},r(()=>to(e,i,o,s)),n(()=>{Object.values(e._x_lookup).forEach(a=>m(()=>{P(a),a.remove()})),delete e._x_prevKeys,delete e._x_lookup})});function to(e,t,r,n){let i=s=>typeof s=="object"&&!Array.isArray(s),o=e;r(s=>{no(s)&&s>=0&&(s=Array.from(Array(s).keys(),f=>f+1)),s===void 0&&(s=[]);let a=e._x_lookup,c=e._x_prevKeys,l=[],u=[];if(i(s))s=Object.entries(s).map(([f,g])=>{let b=Sn(t,g,f,s);n(v=>{u.includes(v)&&E("Duplicate key on x-for",e),u.push(v)},{scope:{index:f,...b}}),l.push(b)});else for(let f=0;f<s.length;f++){let g=Sn(t,s[f],f,s);n(b=>{u.includes(b)&&E("Duplicate key on x-for",e),u.push(b)},{scope:{index:f,...g}}),l.push(g)}let p=[],h=[],w=[],F=[];for(let f=0;f<c.length;f++){let g=c[f];u.indexOf(g)===-1&&w.push(g)}c=c.filter(f=>!w.includes(f));let Ee="template";for(let f=0;f<u.length;f++){let g=u[f],b=c.indexOf(g);if(b===-1)c.splice(f,0,g),p.push([Ee,f]);else if(b!==f){let v=c.splice(f,1)[0],O=c.splice(b-1,1)[0];c.splice(f,0,O),c.splice(b,0,v),h.push([v,O])}else F.push(g);Ee=g}for(let f=0;f<w.length;f++){let g=w[f];g in a&&(m(()=>{P(a[g]),a[g].remove()}),delete a[g])}for(let f=0;f<h.length;f++){let[g,b]=h[f],v=a[g],O=a[b],ee=document.createElement("div");m(()=>{O||E('x-for ":key" is undefined or invalid',o,b,a),O.after(ee),v.after(O),O._x_currentIfEl&&O.after(O._x_currentIfEl),ee.before(v),v._x_currentIfEl&&v.after(v._x_currentIfEl),ee.remove()}),O._x_refreshXForScope(l[u.indexOf(b)])}for(let f=0;f<p.length;f++){let[g,b]=p[f],v=g==="template"?o:a[g];v._x_currentIfEl&&(v=v._x_currentIfEl);let O=l[b],ee=u[b],ce=document.importNode(o.content,!0).firstElementChild,qt=T(O);k(ce,qt,o),ce._x_refreshXForScope=On=>{Object.entries(On).forEach(([Cn,Tn])=>{qt[Cn]=Tn})},m(()=>{v.after(ce),A(()=>S(ce))()}),typeof ee=="object"&&E("x-for key cannot be an object, it must be a string or an integer",o),a[ee]=ce}for(let f=0;f<F.length;f++)a[F[f]]._x_refreshXForScope(l[u.indexOf(F[f])]);o._x_prevKeys=u})}function ro(e){let t=/,([^,\}\]]*)(?:,([^,\}\]]*))?$/,r=/^\s*\(|\)\s*$/g,n=/([\s\S]*?)\s+(?:in|of)\s+([\s\S]*)/,i=e.match(n);if(!i)return;let o={};o.items=i[2].trim();let s=i[1].replace(r,"").trim(),a=s.match(t);return a?(o.item=s.replace(t,"").trim(),o.index=a[1].trim(),a[2]&&(o.collection=a[2].trim())):o.item=s,o}function Sn(e,t,r,n){let i={};return/^\[.*\]$/.test(e.item)&&Array.isArray(t)?e.item.replace("[","").replace("]","").split(",").map(s=>s.trim()).forEach((s,a)=>{i[s]=t[a]}):/^\{.*\}$/.test(e.item)&&!Array.isArray(t)&&typeof t=="object"?e.item.replace("{","").replace("}","").split(",").map(s=>s.trim()).forEach(s=>{i[s]=t[s]}):i[e.item]=t,e.index&&(i[e.index]=r),e.collection&&(i[e.collection]=n),i}function no(e){return!Array.isArray(e)&&!isNaN(e)}function An(){}An.inline=(e,{expression:t},{cleanup:r})=>{let n=Y(e);n._x_refs||(n._x_refs={}),n._x_refs[t]=e,r(()=>delete n._x_refs[t])};d("ref",An);d("if",(e,{expression:t},{effect:r,cleanup:n})=>{e.tagName.toLowerCase()!=="template"&&E("x-if can only be used on a <template> tag",e);let i=x(e,t),o=()=>{if(e._x_currentIfEl)return e._x_currentIfEl;let a=e.content.cloneNode(!0).firstElementChild;return k(a,{},e),m(()=>{e.after(a),A(()=>S(a))()}),e._x_currentIfEl=a,e._x_undoIf=()=>{m(()=>{P(a),a.remove()}),delete e._x_currentIfEl},a},s=()=>{e._x_undoIf&&(e._x_undoIf(),delete e._x_undoIf)};r(()=>i(a=>{a?o():s()})),n(()=>e._x_undoIf&&e._x_undoIf())});d("id",(e,{expression:t},{evaluate:r})=>{r(t).forEach(i=>_n(e,i))});H((e,t)=>{e._x_ids&&(t._x_ids=e._x_ids)});ne(Pe("@",Ie(C("on:"))));d("on",A((e,{value:t,modifiers:r,expression:n},{cleanup:i})=>{let o=n?x(e,n):()=>{};e.tagName.toLowerCase()==="template"&&(e._x_forwardEvents||(e._x_forwardEvents=[]),e._x_forwardEvents.includes(t)||e._x_forwardEvents.push(t));let s=ae(e,t,r,a=>{o(()=>{},{scope:{$event:a},params:[a]})});i(()=>s())}));rt("Collapse","collapse","collapse");rt("Intersect","intersect","intersect");rt("Focus","trap","focus");rt("Mask","mask","mask");function rt(e,t,r){d(t,n=>E(`You can't use [x-${t}] without first installing the "${e}" plugin here: https://alpinejs.dev/plugins/${r}`,n))}K.setEvaluator(xt);K.setReactivityEngine({reactive:et,effect:rn,release:nn,raw:_});var Vt=K;window.Alpine=Vt;queueMicrotask(()=>{Vt.start()});})();

;
