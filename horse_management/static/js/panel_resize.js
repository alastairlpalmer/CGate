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
