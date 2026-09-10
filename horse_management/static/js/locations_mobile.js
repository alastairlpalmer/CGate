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

    function snapHeights() {
        var panel = sheet();
        if (!panel) { return null; }
        // Read the heights the stylesheet actually resolved, rather than
        // repeating them here where they would drift from the CSS.
        var was = panel.className;
        var out = {};
        SNAPS.forEach(function (key) {
            panel.className = was.replace(/is-(peek|half|full)/, 'is-' + key);
            out[key] = panel.offsetHeight;
        });
        panel.className = was;
        return out;
    }

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
    // position it does not have — but the filter still applies.
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
        if (changed && window.htmx) {
            // `select` is named because htmx.ajax with no source element
            // falls back to <body>, whose hx-select is #main-content for
            // the boosted navigation — a partial holds none, and the
            // sheet would come back empty.
            htmx.ajax('GET', previewUrl(state.pk), {
                source: p,
                target: '#loc-sheet-detail',
                select: '#loc-sheet-content',
                swap: 'innerHTML'
            });
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

    function dragStart(e) {
        var panel = sheet();
        if (!panel) { return; }
        drag = { y: e.touches[0].clientY, from: panel.offsetHeight, heights: snapHeights(), moved: false };
    }

    function dragMove(e) {
        var panel = sheet();
        if (!drag || !panel) { return; }
        var delta = drag.y - e.touches[0].clientY;   // up is positive
        drag.moved = drag.moved || Math.abs(delta) > 4;
        if (!drag.moved) { return; }
        if (e.cancelable) { e.preventDefault(); }
        drag.visible = Math.max(0, drag.from + delta);
        panel.style.height = drag.visible + 'px';
    }

    function dragEnd() {
        var panel = sheet();
        if (!drag || !panel) { return; }
        panel.style.height = '';
        var moved = drag.moved;
        var visible = drag.visible;
        var heights = drag.heights;
        drag = null;
        if (!moved || visible === undefined || !heights) { return; }
        setSnap(snapFromHeight(visible, heights));
    }

    function bindDrag() {
        var handle = document.querySelector('.loc-sheet-handle');
        if (!handle || handle.dataset.dragBound) { return; }
        handle.dataset.dragBound = '1';
        handle.addEventListener('touchstart', dragStart, { passive: true });
        handle.addEventListener('touchmove', dragMove, { passive: false });
        handle.addEventListener('touchend', dragEnd);
        handle.addEventListener('touchcancel', dragEnd);
    }

    // ── Setting up, and doing it again after a swap ──

    function setUp() {
        if (!page()) { return; }
        state.snap = 'peek';
        state.pk = null;
        state.filter = 'all';
        state.sort = 'horses';
        bindDrag();
        setSnap('peek');
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
