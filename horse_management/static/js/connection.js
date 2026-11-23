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
                : { text: 'Not saved: no signal. Your entries are still here. Save again when the signal is back.', retry: false };
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

    var api = {
        timeLimit: timeLimit,
        startedByPerson: startedByPerson,
        failureMessage: failureMessage,
        safeToRefresh: safeToRefresh,
        awayLongEnough: awayLongEnough,
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
        el.className = kind === 'warning' ? 'toast-warning' : 'toast-error';
        el.setAttribute('data-request-error', '');
        el.setAttribute('role', 'alert');
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
        if (isSheet(detail.target)) { return; }     // popup.js says it in the sheet
        var config = detail.requestConfig || {};
        var event = config.triggeringEvent;
        var elt = detail.elt;
        var spec = elt && elt.getAttribute ? elt.getAttribute('hx-trigger') : '';
        if (!startedByPerson(event ? event.type : '', spec)) { return; }
        var status = detail.xhr ? detail.xhr.status : 0;
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
    doc.addEventListener('htmx:sendError', function (e) { onFailure('send', e); });
    doc.addEventListener('htmx:timeout', function (e) { onFailure('timeout', e); });
    doc.addEventListener('htmx:responseError', function (e) { onFailure('response', e); });

    // The next request that works takes the message away.
    doc.addEventListener('htmx:afterRequest', function (e) {
        if (e.detail && e.detail.successful) { clearNotice(); }
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
    root.addEventListener('offline', showSignal);
    root.addEventListener('online', function () { showSignal(); clearNotice(); });
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
