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
    var BADGE_GAP = 4;             // clear space kept between two badges
    var LEADER_MIN_PX = 6;         // a badge pushed further than this off its anchor gets a leader line
    var LABEL_H = 20;              // a name label's height (text-xs, leading-4, py-0.5)
    var COMPACT_LOCATIONS = 4;       // the compact crop around a highlight, on a phone
    var COMPACT_LOCATIONS_WIDE = 8;  // ... and on a map at least COMPACT_WIDE_PX wide
    var COMPACT_WIDE_PX = 480;
    // How much fits depends on the map's size. One badge per BADGE_CELL
    // square keeps counts readable; one name per LABEL_CELL_W × LABEL_CELL_H
    // keeps names apart. The compact card is a glance at where the horses
    // are: past its room (never fewer than COMPACT_BADGE_MIN) it drops the
    // rings of empty locations, their shapes staying drawn.
    var BADGE_CELL = 110;
    var LABEL_CELL_W = 170, LABEL_CELL_H = 90;
    var COMPACT_BADGE_MIN = 6;
    var FIT_PADDING = [36, 36];
    var LABEL_HIDE_BELOW = 1.25;   // zoom levels under the fit zoom at which names hide
    // A dense site (more badges than this) shows names only once zoomed in
    // a level, and until then only the badges of locations that hold
    // horses: at the fit zoom ponds, woods and rested fields would bury
    // the capacity rings, which are what the map is for. Their shapes stay
    // drawn and tappable.
    var LABEL_DENSE_COUNT = 12;
    var STROKE_WEIGHT = 2.5;
    // The base map behind the shapes: OpenStreetMap, muted by CSS so the
    // river, woods, tracks and buildings read as context under the parcels
    // (the same tiles the coordinate picker uses). Off by default on the
    // dashboard card, on by default on the Map tab; the last choice on each
    // is remembered on this device.
    var TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
    var TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
    var TILE_KEY = 'yardway.mapTiles.';
    var FILL_PLAIN = 0.2;
    var FILL_OVER_TILES = 0.12;

    function haversine(a, b) {
        return window.YardwayGeo ? YardwayGeo.haversineMetres(a[0], a[1], b[0], b[1]) : Infinity;
    }

    document.addEventListener('alpine:init', function () {
        Alpine.data('locationMap', function (opts) {
            opts = opts || {};
            return {
                variant: opts.variant || 'full',
                highlight: opts.highlight || null,
                tiles: false,     // the base map is showing
                payload: null,
                map: null,
                group: null,
                layers: {},       // pk → Leaflet layer
                bounds: {},       // pk → LatLngBounds (known without a map view)
                badges: {},       // pk → <a>
                labels: {},       // pk → <span>
                leaders: {},      // pk → <g> (line + dot back to the anchor)
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
                    clearTimeout(this._bgTimer);
                    window.removeEventListener('yardway:map-focus', this._onFocus);
                    if (this._ro) { this._ro.disconnect(); }
                    if (this.map) { this.map.remove(); this.map = null; }
                    this._tileLayer = null;
                    this._attribution = null;
                },

                // ── The base map ──
                tilesWanted: function () {
                    var fallback = this.variant === 'full';
                    try {
                        var saved = localStorage.getItem(TILE_KEY + this.variant);
                        if (saved === '1') return true;
                        if (saved === '0') return false;
                    } catch (err) { /* private mode */ }
                    return fallback;
                },

                toggleTiles: function () {
                    this.setTiles(!this.tiles);
                    try { localStorage.setItem(TILE_KEY + this.variant, this.tiles ? '1' : '0'); } catch (err) { /* ignore */ }
                },

                setTiles: function (on) {
                    var self = this;
                    this.tiles = !!on;
                    if (!this.map) return;
                    if (this.tiles) {
                        if (!this._tileLayer) {
                            this._tileLayer = L.tileLayer(TILE_URL, { maxNativeZoom: 19, maxZoom: 21, attribution: TILE_ATTRIBUTION });
                        }
                        this._tileLayer.addTo(this.map);
                        if (!this._attribution) this._attribution = L.control.attribution({ prefix: false });
                        this._attribution.addTo(this.map);
                    } else {
                        if (this._tileLayer) this.map.removeLayer(this._tileLayer);
                        if (this._attribution) this._attribution.remove();
                    }
                    // Lighter fills over the map so what is under a parcel shows.
                    var fill = this.tiles ? FILL_OVER_TILES : FILL_PLAIN;
                    Object.keys(this.layers).forEach(function (pk) {
                        var layer = self.layers[pk];
                        if (layer && layer.setStyle) layer.setStyle({ fillOpacity: fill });
                    });
                },

                mount: function () {
                    var self = this;
                    var el = this.$refs.map;
                    if (this.map || !el || !window.L || !document.body.contains(el)) return;
                    var full = this.variant === 'full';
                    // Two ways in. With a mouse or trackpad (a fine pointer)
                    // both maps are for looking: drag to move, +/- and a double
                    // click to zoom, the wheel to zoom — on the Map tab as it
                    // comes, on the dashboard card with Ctrl (⌘) held, so a
                    // scroll down the dashboard never sticks on the map (a
                    // trackpad pinch arrives as a Ctrl wheel, so it just
                    // works). On a touch screen one finger scrolls the page;
                    // on the Map tab two fingers move and zoom the map, and
                    // the dashboard card opens the Map tab on a tap.
                    var coarse = !!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);
                    var fine = !coarse;
                    this.map = L.map(el, {
                        attributionControl: false,
                        zoomControl: full || fine,
                        dragging: fine,
                        scrollWheelZoom: fine,
                        touchZoom: full,
                        doubleClickZoom: full || fine,
                        boxZoom: false,
                        keyboard: full,
                        tap: false,
                        zoomSnap: 0.25,
                        maxZoom: 21,
                        minZoom: 3
                    });
                    this.setTiles(this.tilesWanted());
                    if (fine && !full) {
                        // Plain wheel: the page scrolls and a hint says how to
                        // zoom. Leaflet's own wheel listener sits on this element
                        // in the bubble phase; stopping the event here, in the
                        // capture phase, keeps it from zooming.
                        var mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '\u2318' : 'Ctrl';
                        el.addEventListener('wheel', function (e) {
                            if (e.ctrlKey || e.metaKey) return;
                            e.stopImmediatePropagation();
                            self.hint('Hold ' + mod + ' and scroll to zoom the map');
                        }, { capture: true, passive: true });
                    }
                    if (full && coarse) {
                        el.addEventListener('touchstart', function (e) {
                            if (e.touches.length === 1) self.hint('Use two fingers to move the map');
                        }, { passive: true });
                    }
                    if (full) {
                        // A tap outside every shape opens the Locations list. It
                        // waits a beat so a double click still zooms instead;
                        // shape and badge taps never reach here (stopped below,
                        // and badges sit outside Leaflet's container).
                        this.map.on('click', function () {
                            clearTimeout(self._bgTimer);
                            self._bgTimer = setTimeout(function () {
                                if (self.payload.urls && self.payload.urls.list) window.location.assign(self.payload.urls.list);
                            }, 300);
                        });
                        this.map.on('dblclick', function () { clearTimeout(self._bgTimer); });
                    }
                    if (this.variant === 'phone') {
                        // Tapping bare ground puts the field detail away,
                        // rather than leaving the page as the full map does.
                        this.map.on('click', function () {
                            window.dispatchEvent(new CustomEvent('yardway:map-pick', {
                                detail: { site: self.$el.dataset.site, pk: null }
                            }));
                        });
                    }
                    this.group = L.featureGroup().addTo(this.map);
                    this.draw();
                    // Badges are DOM outside Leaflet's panes, so they must be
                    // moved by hand. During a drag every frame is a light pass
                    // (same badges and names, new positions). An animated zoom
                    // fires zoomanim once, with where it will end: the badges
                    // are sent there at once and CSS carries them along Leaflet's
                    // own curve, so they arrive with the shapes instead of
                    // jumping after them. zoomend and moveend decide afresh what
                    // fits; a resize does too.
                    this.map.on('zoomanim', function (e) {
                        self._anim = e;
                        self.$el.classList.add('is-zooming');
                        self.place(true);
                    });
                    this.map.on('zoomend moveend', function () {
                        self._anim = null;
                        self.$el.classList.remove('is-zooming');
                        self.place();
                    });
                    this.map.on('move zoom viewreset', function () { if (!self._anim) self.place(true); });
                    this.map.on('resize', function () { self.place(); });
                    if (window.ResizeObserver) {
                        // A compact map inside a hidden card (the Near you card shows
                        // one site at a time) has no size to fit to; fit once it appears.
                        this._ro = new ResizeObserver(function () {
                            if (!self.map) return;
                            self.map.invalidateSize();
                            if (self._needsFit && el.clientWidth > 0) { self.focus(self.highlight); }
                        });
                        this._ro.observe(el);
                    }
                    this.focus(this.highlight);
                },

                // A short notice over the map (how to move it on a phone).
                hint: function (text) {
                    var self = this;
                    var node = this.$refs.hint;
                    if (!node) return;
                    node.textContent = text;
                    node.hidden = false;
                    clearTimeout(this._hintTimer);
                    this._hintTimer = setTimeout(function () { node.hidden = true; }, 1600);
                },

                // The rendering rule, once.
                draw: function () {
                    var self = this;
                    var full = this.variant === 'full';
                    // The phone map picks rather than navigates: its tap
                    // opens the field in the sheet beside it, and the
                    // sheet is what static/js/locations_mobile.js drives.
                    var picks = this.variant === 'phone';
                    var interactive = full || picks;
                    this.payload.locations.forEach(function (loc) {
                        // On the phone the colour answers the rotation —
                        // grazing, over, rested, recovering — not capacity
                        // alone, so the map and the list say the same thing.
                        var colour = (picks && loc.rest_colour) ? loc.rest_colour : loc.colour;
                        var style = { color: colour, weight: STROKE_WEIGHT, fillColor: colour, fillOpacity: self.tiles ? FILL_OVER_TILES : FILL_PLAIN, opacity: 1 };
                        var layer = null;
                        if (loc.boundary) {
                            layer = L.geoJSON(loc.boundary, { style: style, interactive: interactive });
                        } else if (loc.lat != null && loc.lng != null) {
                            layer = L.circle([loc.lat, loc.lng], Object.assign({ radius: loc.radius_m || 25, interactive: interactive }, style));
                        }
                        if (!layer) return;   // no point: draw nothing, not even a placeholder
                        if (interactive) {
                            layer.on('click', function (e) {
                                L.DomEvent.stopPropagation(e);   // not a background tap
                                if (picks) {
                                    // Leaflet layers are not in the page's
                                    // DOM, so there is nothing to delegate
                                    // from: the sheet is told by event.
                                    window.dispatchEvent(new CustomEvent('yardway:map-pick', {
                                        detail: { site: self.$el.dataset.site, pk: loc.pk }
                                    }));
                                    return;
                                }
                                window.location.assign(loc.urls.detail);
                            });
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
                    this._decided = null;
                    var self = this;
                    Object.keys(this.badges).forEach(function (pk) {
                        self.badges[pk].classList.toggle('is-highlight', String(pk) === String(self.highlight));
                    });
                    this.visible = null;
                    var el = this.$refs.map;
                    if (this.variant === 'compact' && this.highlight) {
                        var target = this.payload.locations.filter(function (l) { return l.pk === self.highlight && l.anchor; })[0];
                        if (target) {
                            var crop = el && el.clientWidth >= COMPACT_WIDE_PX ? COMPACT_LOCATIONS_WIDE : COMPACT_LOCATIONS;
                            var nearest = this.payload.locations.filter(function (l) { return l.anchor; })
                                .sort(function (a, b) { return haversine(target.anchor, a.anchor) - haversine(target.anchor, b.anchor); })
                                .slice(0, crop);
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
                    // fitBounds on a 0×0 container (display:none) computes a nonsense
                    // zoom; remember to fit when the ResizeObserver sees a size.
                    this._needsFit = !el || el.clientWidth === 0 || el.clientHeight === 0;
                    if (bounds.isValid() && !this._needsFit) {
                        this.map.invalidateSize();
                        this.map.fitBounds(bounds, Object.assign(
                            { animate: false }, this.fitPadding(el)
                        ));
                        this.fitZoom = this.map.getZoom();
                    }
                    this.place();
                },

                // How much of the container the shapes must keep clear.
                // On the phone the sheet lies over the bottom of the map,
                // so a plain inset would fit the yard into ground nobody
                // can see. The sheet's real edge is measured rather than
                // repeated as a number here.
                fitPadding: function (el) {
                    if (this.variant !== 'phone' || !el) { return { padding: FIT_PADDING }; }
                    var box = el.getBoundingClientRect();
                    var sheet = document.querySelector('[data-loc-sheet]');
                    var bottom = sheet ? box.bottom - sheet.getBoundingClientRect().top : 0;
                    var top = FIT_PADDING[1];
                    bottom = Math.max(FIT_PADDING[1], bottom + 12);
                    // A sheet pulled up to full would leave no band at all.
                    if (top + bottom > box.height * 0.7) { return { padding: FIT_PADDING }; }
                    return {
                        paddingTopLeft: [FIT_PADDING[0], Math.round(top)],
                        paddingBottomRight: [FIT_PADDING[0], Math.round(bottom)]
                    };
                },

                // Where a lat/lng lands in the container: now, or — during an
                // animated zoom — where it will land when the zoom ends.
                projector: function () {
                    var map = this.map, a = this._anim;
                    if (!a) return function (ll) { return map.latLngToContainerPoint(ll); };
                    var half = map.getSize().divideBy(2);
                    var centre = map.project(a.center, a.zoom);
                    return function (ll) { return map.project(ll, a.zoom).subtract(centre).add(half); };
                },

                // Badges follow their anchors. Overlapping badges are pushed
                // apart (static/js/map_layout.js) and a moved badge keeps a
                // leader line back to its anchor. Names sit under the badges.
                // A light pass (mid-drag, mid-zoom) keeps the last decision on
                // which badges and names show and only moves them; a full pass
                // decides afresh for the size and zoom the map has now.
                place: function (light) {
                    // Nothing to place until the first fit gave the map a view
                    // (a hidden compact map has none; Leaflet throws otherwise).
                    if (!this.map || this.fitZoom == null) return;
                    var self = this;
                    var compact = this.variant === 'compact';
                    var isHighlight = function (pk) { return String(pk) === String(self.highlight); };
                    var size = this.map.getSize();
                    var project = this.projector();
                    if (!light || !this._decided) {
                        var inView = Object.keys(this.badges).filter(function (pk) { return !self.visible || self.visible[pk]; });
                        var zoom = this._anim ? this._anim.zoom : this.map.getZoom();
                        var zoomedOut = zoom < this.fitZoom + 1;
                        // Room at this size: how many badges, and how many names,
                        // can sit on the map without burying each other.
                        var badgeRoom = Math.floor(size.x / BADGE_CELL) * Math.floor(size.y / BADGE_CELL);
                        var labelRoom = Math.floor(size.x / LABEL_CELL_W) * Math.floor(size.y / LABEL_CELL_H);
                        // Dense: drop the badges of locations that hold no horses.
                        var dense = inView.length > LABEL_DENSE_COUNT && zoomedOut;
                        // Crowded (compact only): drop the rings of empty locations too.
                        var crowded = compact && inView.length > Math.max(COMPACT_BADGE_MIN, badgeRoom);
                        var shown = inView.filter(function (pk) {
                            if (isHighlight(pk)) return true;
                            var a = self.badges[pk];
                            if (dense && a.dataset.holds === '0') return false;
                            if (crowded && !(Number(a.dataset.count) > 0)) return false;
                            return true;
                        });
                        // Names: when they have room at this size; on the full map
                        // also whenever what is left is few, or once zoomed in a
                        // level. Never far below the fit zoom. The highlighted
                        // location is always named.
                        var showLabels = zoom >= this.fitZoom - LABEL_HIDE_BELOW
                            && (labelRoom >= shown.length || (!compact && (shown.length <= LABEL_DENSE_COUNT || !zoomedOut)));
                        this._decided = { shown: shown, showLabels: showLabels, named: null };
                    }
                    var decided = this._decided;

                    var items = decided.shown.map(function (pk) {
                        var a = self.badges[pk];
                        var parts = a.dataset.anchor.split(',');
                        var pt = project([Number(parts[0]), Number(parts[1])]);
                        return { pk: pk, x: pt.x, y: pt.y, count: Number(a.dataset.count) || 0, fixed: isHighlight(pk) };
                    });
                    // The highlighted location's name always shows, so its
                    // neighbours must keep clear of it: a row of small fixed
                    // obstacles stands in for the label under the badge.
                    var obstacles = [];
                    var hi = items.filter(function (it) { return it.fixed; })[0];
                    var hiLabel = hi && this.labels[hi.pk];
                    if (hiLabel) {
                        var w = this.labelWidth(hiLabel);
                        var cy = hi.y + BADGE_PX / 2 + 2 + LABEL_H / 2;
                        for (var lx = hi.x - w / 2 + LABEL_H / 2; lx <= hi.x + w / 2 - LABEL_H / 2 + 0.01; lx += LABEL_H) {
                            obstacles.push({ x: lx, y: cy, count: 0, fixed: true, r: LABEL_H / 2 + 2 });
                        }
                    }
                    var spread = window.YardwayMapLayout
                        ? YardwayMapLayout.spreadBadges(items.concat(obstacles), { size: BADGE_PX, gap: BADGE_GAP, width: size.x, height: size.y })
                        : items;
                    var placed = {};
                    var layout = window.YardwayMapLayout;
                    var nameBoxes = [];   // names already placed; a name another badge or name would cover stays hidden
                    var named = light && decided.named ? decided.named : null;   // a light pass keeps the last name decisions
                    var newNamed = {};
                    // The highlighted badge first, so its name is never the one that gives way.
                    var order = items.map(function (it, i) { return i; })
                        .sort(function (a, b) { return (items[b].fixed ? 1 : 0) - (items[a].fixed ? 1 : 0); });
                    order.forEach(function (i) {
                        var it = items[i];
                        var a = self.badges[it.pk];
                        var p = spread[i];
                        placed[it.pk] = true;
                        a.hidden = false;
                        a.style.left = p.x + 'px';
                        a.style.top = p.y + 'px';
                        var dx = p.x - it.x, dy = p.y - it.y;
                        self.leader(it.pk, Math.sqrt(dx * dx + dy * dy) > LEADER_MIN_PX ? { x1: it.x, y1: it.y, x2: p.x, y2: p.y, colour: a.dataset.colour } : null);
                        var label = self.labels[it.pk];
                        if (!label) return;
                        var show;
                        if (named) {
                            show = !!named[it.pk];
                        } else {
                            show = decided.showLabels || it.fixed;
                            if (show) {
                                var box = { x: p.x - self.labelWidth(label) / 2, y: p.y + BADGE_PX / 2 + 2, w: self.labelWidth(label), h: LABEL_H };
                                if (!it.fixed && layout) {
                                    show = !items.some(function (other, j) { return j !== i && layout.circleHitsBox(spread[j].x, spread[j].y, BADGE_PX / 2, box); })
                                        && !nameBoxes.some(function (b) { return layout.boxesOverlap(b, box); });
                                }
                                if (show) nameBoxes.push(box);
                            }
                        }
                        if (show) newNamed[it.pk] = true;
                        label.hidden = !show;
                        label.style.left = p.x + 'px';
                        label.style.top = (p.y + BADGE_PX / 2 + 2) + 'px';
                    });
                    if (!named) decided.named = newNamed;
                    Object.keys(this.badges).forEach(function (pk) {
                        if (placed[pk]) return;
                        self.badges[pk].hidden = true;
                        if (self.labels[pk]) self.labels[pk].hidden = true;
                        self.leader(pk, null);
                    });
                },

                // A label's rendered width, measured once it can be (a hidden
                // element, or one inside a hidden card, measures 0: try again later).
                labelWidth: function (label) {
                    if (label._w == null) {
                        var wasHidden = label.hidden;
                        label.hidden = false;
                        var w = label.offsetWidth || 0;
                        label.hidden = wasHidden;
                        if (w > 0) label._w = w;
                        return w;
                    }
                    return label._w;
                },

                // The thin line (and dot on the anchor) that ties a moved
                // badge to the spot it stands for; null hides it.
                leader: function (pk, seg) {
                    var svg = this.$refs.leaders;
                    if (!svg) return;
                    var g = this.leaders[pk];
                    if (!seg) {
                        if (g) g.style.display = 'none';
                        return;
                    }
                    var NS = 'http://www.w3.org/2000/svg';
                    if (!g) {
                        g = document.createElementNS(NS, 'g');
                        g.setAttribute('class', 'location-map-leader');
                        g.appendChild(document.createElementNS(NS, 'line'));
                        g.appendChild(document.createElementNS(NS, 'circle'));
                        svg.appendChild(g);
                        this.leaders[pk] = g;
                    }
                    var line = g.firstChild, dot = g.lastChild;
                    line.setAttribute('x1', seg.x1); line.setAttribute('y1', seg.y1);
                    line.setAttribute('x2', seg.x2); line.setAttribute('y2', seg.y2);
                    dot.setAttribute('cx', seg.x1); dot.setAttribute('cy', seg.y1); dot.setAttribute('r', 3);
                    if (seg.colour) { line.style.stroke = seg.colour; dot.style.fill = seg.colour; }
                    g.style.display = '';
                }
            };
        });

        Alpine.data('nearYouCard', function () {
            return {
                data: null,
                site: '',
                highlight: null,
                label: '',
                kind: '',      // 'gps' | 'pinned' | 'last' | ''
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
                    if (!site || d.site_names.indexOf(site) === -1) {
                        this.ready = false;
                        this.site = '';
                        this.$dispatch('near-you:visible', false);
                        return;
                    }

                    var highlight = null, label = '', kind = '';
                    var onSite = function (pk) {
                        return d.locations.concat(d.unlocated).some(function (l) { return l.pk === pk && l.site === site; });
                    };
                    // Only a GPS match may look like one: the other two say why
                    // they are lit, so a field you opened this morning is not
                    // mistaken for the field you are standing in.
                    if (gps && gps.location && gps.location.site === site) {
                        highlight = gps.location.pk;
                        label = gps.location.name + ' · ' + YardwayGeo.formatDistance(gps.distance);
                        kind = 'gps';
                    } else if (d.pinned && onSite(d.pinned.pk)) {
                        highlight = d.pinned.pk;
                        label = 'Pinned · ' + d.pinned.name;
                        kind = 'pinned';
                    } else {
                        var last = window.Yardway && Yardway.lastLocation && Yardway.lastLocation();
                        if (last && onSite(last.pk)) { highlight = last.pk; label = 'Last opened · ' + last.name; kind = 'last'; }
                    }
                    this.site = site;
                    this.highlight = highlight;
                    this.label = label || site;
                    this.kind = kind;
                    this.ready = true;
                    // The dashboard grid keeps a column for the card only while it shows.
                    this.$dispatch('near-you:visible', true);
                    // After Alpine has shown the chosen site's map, so it has a size to fit.
                    Alpine.nextTick(function () {
                        window.dispatchEvent(new CustomEvent('yardway:map-focus', { detail: { site: site, highlight: highlight } }));
                    });
                },

                mapTabUrl: function () {
                    return this.data.urls.map + '?tab=map&site=' + encodeURIComponent(this.site);
                },

                count: function () {
                    var s = this.site;
                    return (this.data.site_counts && this.data.site_counts[s]) || 0;
                },

                // On a touch screen the whole card opens the Map tab. With a
                // fine pointer the map itself is for looking (drag, Ctrl+scroll,
                // +/-), so only the rest of the card — and the Full map button
                // over the map — open it.
                open: function (e) {
                    var fine = !!(window.matchMedia && window.matchMedia('(pointer: fine)').matches);
                    if (fine && e && e.target && e.target.closest && e.target.closest('.location-map')) return;
                    window.location.assign(this.mapTabUrl());
                }
            };
        });
    });
})();
