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

    var api = { spreadBadges: spreadBadges };
    root.YardwayMapLayout = api;
    if (typeof module !== 'undefined' && module.exports) { module.exports = api; }
})(typeof window !== 'undefined' ? window : globalThis);
