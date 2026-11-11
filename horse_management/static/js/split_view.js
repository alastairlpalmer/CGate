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
        });
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
        if (!container) { state.pk = null; return; }
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
        markRows();
        updateChrome();
    }

    document.addEventListener('DOMContentLoaded', restore);
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
    });
})(typeof window !== 'undefined' ? window : globalThis);
