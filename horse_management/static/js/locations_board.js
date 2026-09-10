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
            rowsIn(loose).forEach(function (r) {
                var on = matchesQuery(r.name.toLowerCase(), state.query);
                r.el.hidden = !on;
                if (on) { seen++; }
            });
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
                source: b,
                target: '#loc-rail-detail',
                select: '#loc-sheet-content',
                swap: 'innerHTML'
            });
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
