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
