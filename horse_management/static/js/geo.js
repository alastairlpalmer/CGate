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
