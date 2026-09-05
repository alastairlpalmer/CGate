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
    var COMPACT_LOCATIONS = 4;
    var FIT_PADDING = [36, 36];
    var LABEL_HIDE_BELOW = 1.25;   // zoom levels under the fit zoom at which names hide
    var STROKE_WEIGHT = 2;

    function haversine(a, b) {
        return window.YardwayGeo ? YardwayGeo.haversineMetres(a[0], a[1], b[0], b[1]) : Infinity;
    }

    document.addEventListener('alpine:init', function () {
        Alpine.data('locationMap', function (opts) {
            opts = opts || {};
            return {
                variant: opts.variant || 'full',
                highlight: opts.highlight || null,
                payload: null,
                map: null,
                group: null,
                layers: {},       // pk → Leaflet layer
                bounds: {},       // pk → LatLngBounds (known without a map view)
                badges: {},       // pk → <a>
                labels: {},       // pk → <span>
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
                },

                destroy: function () {
                    window.removeEventListener('yardway:map-focus', this._onFocus);
                    if (this._ro) { this._ro.disconnect(); }
                    if (this.map) { this.map.remove(); this.map = null; }
                },

                mount: function () {
                    var self = this;
                    var el = this.$refs.map;
                    if (this.map || !el || !window.L || !document.body.contains(el)) return;
                    var full = this.variant === 'full';
                    this.map = L.map(el, {
                        attributionControl: false,
                        zoomControl: full,
                        dragging: full,
                        scrollWheelZoom: full,
                        touchZoom: full,
                        doubleClickZoom: full,
                        boxZoom: false,
                        keyboard: full,
                        tap: false,
                        zoomSnap: 0.25,
                        maxZoom: 21,
                        minZoom: 3
                    });
                    this.group = L.featureGroup().addTo(this.map);
                    this.draw();
                    this.map.on('move zoom viewreset resize', function () { self.place(); });
                    if (window.ResizeObserver) {
                        this._ro = new ResizeObserver(function () { if (self.map) { self.map.invalidateSize(); } });
                        this._ro.observe(el);
                    }
                    this.focus(this.highlight);
                },

                // The rendering rule, once.
                draw: function () {
                    var self = this;
                    var full = this.variant === 'full';
                    this.payload.locations.forEach(function (loc) {
                        var style = { color: loc.colour, weight: STROKE_WEIGHT, fillColor: loc.colour, fillOpacity: 0.12, opacity: 0.9 };
                        var layer = null;
                        if (loc.boundary) {
                            layer = L.geoJSON(loc.boundary, { style: style, interactive: full });
                        } else if (loc.lat != null && loc.lng != null) {
                            layer = L.circle([loc.lat, loc.lng], Object.assign({ radius: loc.radius_m || 25, interactive: full }, style));
                        }
                        if (!layer) return;   // no point: draw nothing, not even a placeholder
                        if (full) {
                            layer.on('click', function () { window.location.assign(loc.urls.detail); });
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
                    var self = this;
                    Object.keys(this.badges).forEach(function (pk) {
                        self.badges[pk].classList.toggle('is-highlight', String(pk) === String(self.highlight));
                    });
                    this.visible = null;
                    if (this.variant === 'compact' && this.highlight) {
                        var target = this.payload.locations.filter(function (l) { return l.pk === self.highlight && l.anchor; })[0];
                        if (target) {
                            var nearest = this.payload.locations.filter(function (l) { return l.anchor; })
                                .sort(function (a, b) { return haversine(target.anchor, a.anchor) - haversine(target.anchor, b.anchor); })
                                .slice(0, COMPACT_LOCATIONS);
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
                    if (bounds.isValid()) {
                        this.map.fitBounds(bounds, { padding: FIT_PADDING, animate: false });
                        this.fitZoom = this.map.getZoom();
                    }
                    this.place();
                },

                // Badges and names follow their anchors; overlapping badges
                // step aside (the lower count moves, perpendicular to the line).
                place: function () {
                    if (!this.map) return;
                    var self = this;
                    var placed = [];
                    var showLabels = this.variant === 'full' && this.fitZoom != null && this.map.getZoom() >= this.fitZoom - LABEL_HIDE_BELOW;
                    Object.keys(this.badges).forEach(function (pk) {
                        var a = self.badges[pk];
                        var show = !self.visible || self.visible[pk];
                        var label = self.labels[pk];
                        if (!show) {
                            a.hidden = true;
                            if (label) label.hidden = true;
                            return;
                        }
                        a.hidden = false;
                        var parts = a.dataset.anchor.split(',');
                        var pt = self.map.latLngToContainerPoint([Number(parts[0]), Number(parts[1])]);
                        var x = pt.x, y = pt.y;
                        var count = Number(a.dataset.count) || 0;
                        placed.forEach(function (other) {
                            var dx = x - other.x, dy = y - other.y;
                            var d = Math.sqrt(dx * dx + dy * dy);
                            if (d >= BADGE_PX || d === 0 && placed.length === 0) return;
                            // Move the lighter badge sideways, off the line between them.
                            if (count <= other.count) {
                                var nx = d === 0 ? 0 : -dy / d, ny = d === 0 ? 1 : dx / d;
                                x = other.x + nx * BADGE_PX + dx * 0.5;
                                y = other.y + ny * BADGE_PX + dy * 0.5;
                            }
                        });
                        a.style.left = x + 'px';
                        a.style.top = y + 'px';
                        placed.push({ x: x, y: y, count: count });
                        if (label) {
                            label.hidden = !showLabels;
                            label.style.left = x + 'px';
                            label.style.top = (y + BADGE_PX / 2 + 2) + 'px';
                        }
                    });
                }
            };
        });

        Alpine.data('nearYouCard', function () {
            return {
                data: null,
                site: '',
                highlight: null,
                label: '',
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
                    if (!site || d.site_names.indexOf(site) === -1) { this.ready = false; this.site = ''; return; }

                    var highlight = null, label = '';
                    var onSite = function (pk) {
                        return d.locations.concat(d.unlocated).some(function (l) { return l.pk === pk && l.site === site; });
                    };
                    if (gps && gps.location && gps.location.site === site) {
                        highlight = gps.location.pk;
                        label = gps.location.name + ' · ' + YardwayGeo.formatDistance(gps.distance);
                    } else if (d.pinned && onSite(d.pinned.pk)) {
                        highlight = d.pinned.pk;
                        label = d.pinned.name;
                    } else {
                        var last = window.Yardway && Yardway.lastLocation && Yardway.lastLocation();
                        if (last && onSite(last.pk)) { highlight = last.pk; label = last.name; }
                    }
                    this.site = site;
                    this.highlight = highlight;
                    this.label = label || site;
                    this.ready = true;
                    window.dispatchEvent(new CustomEvent('yardway:map-focus', { detail: { site: site, highlight: highlight } }));
                },

                mapTabUrl: function () {
                    return this.data.urls.map + '?tab=map&site=' + encodeURIComponent(this.site);
                },

                count: function () {
                    var s = this.site;
                    return (this.data.site_counts && this.data.site_counts[s]) || 0;
                },

                open: function () { window.location.assign(this.mapTabUrl()); }
            };
        });
    });
})();
