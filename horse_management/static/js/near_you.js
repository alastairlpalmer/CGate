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
    // markup is still the server's at that point) and is reset to that
    // markup just before each snapshot.
    window.Yardway.volatile = function (el) {
        if (el && el._x_serverHtml == null) { el._x_serverHtml = el.innerHTML; }
    };
    document.addEventListener('DOMContentLoaded', function () {
        document.body.addEventListener('htmx:beforeHistorySave', function () {
            document.querySelectorAll('[x-data]').forEach(function (el) {
                if (el._x_serverHtml != null) { el.innerHTML = el._x_serverHtml; }
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
                            if (status.state === 'granted') self.locate();
                            else if (status.state === 'prompt') self.askable = !granted ? true : (self.locate(), false);
                            // 'denied': render nothing, do not ask again.
                        }).catch(function () { self.askable = !granted; if (granted) self.locate(); });
                    } else {
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
