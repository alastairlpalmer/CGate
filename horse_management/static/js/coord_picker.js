/* Coordinate picker (templates/locations/_coord_picker.html).
 *
 * One Alpine component for the three ways to enter a latitude/longitude
 * pair: a pasted "lat, lng" string, a Google Maps link (read by the server
 * at /locations/parse-link/, because short links need a cross-origin
 * redirect), and a draggable pin on a Leaflet map with OpenStreetMap
 * tiles. All three write to the form's hidden latitude/longitude inputs.
 *
 * Leaflet is lazy-loaded by the [data-map] loader in base.html; this
 * component asks for it on init and mounts the map on `leaflet:ready`.
 * It works inside the pop-up sheet (the map is sized after the sheet's
 * open transition) and on the full page.
 */
(function () {
    'use strict';

    var PAIR_RE = /^\s*([-+]?\d+(?:\.\d+)?)\s*(?:,\s*|\s+)([-+]?\d+(?:\.\d+)?)\s*$/;

    function round6(n) { return Math.round(n * 1e6) / 1e6; }

    document.addEventListener('alpine:init', function () {
        Alpine.data('coordPicker', function (centreLat, centreLng, centreZoom, parseUrl) {
            return {
                lat: '',
                lng: '',
                linkNote: '',
                map: null,
                marker: null,
                _ro: null,

                init: function () {
                    var self = this;
                    var form = this.$el.closest('form');
                    this.latInput = form ? form.querySelector('input[name="latitude"]') : null;
                    this.lngInput = form ? form.querySelector('input[name="longitude"]') : null;
                    this.lat = (this.latInput && this.latInput.value) || '';
                    this.lng = (this.lngInput && this.lngInput.value) || '';
                    if (window.Yardway && Yardway.ensureLeaflet) { Yardway.ensureLeaflet(); }
                    if (window.L) {
                        this.$nextTick(function () { self.mount(); });
                    } else {
                        window.addEventListener('leaflet:ready', function () { self.mount(); }, { once: true });
                    }
                },

                destroy: function () {
                    if (this._ro) { this._ro.disconnect(); }
                    if (this.map) { this.map.remove(); this.map = null; this.marker = null; }
                },

                has: function () { return this.lat !== '' && this.lng !== ''; },

                display: function () { return this.has() ? this.lat + ', ' + this.lng : 'Not set'; },

                // Write the pair everywhere: the hidden inputs (the form),
                // the pin (the map) and the text box (the operator).
                set: function (lat, lng, fromMap) {
                    this.lat = lat === '' ? '' : String(round6(Number(lat)));
                    this.lng = lng === '' ? '' : String(round6(Number(lng)));
                    if (this.latInput) { this.latInput.value = this.lat; }
                    if (this.lngInput) { this.lngInput.value = this.lng; }
                    if (this.$refs.text) { this.$refs.text.value = this.has() ? this.lat + ', ' + this.lng : ''; }
                    // The sheet tracks edits through input events on real fields.
                    if (this.latInput) { this.latInput.dispatchEvent(new Event('input', { bubbles: true })); }
                    if (!fromMap) { this.placePin(); }
                },

                clear: function () {
                    this.set('', '');
                    if (this.$refs.link) { this.$refs.link.value = ''; }
                    this.linkNote = '';
                },

                applyText: function (value) {
                    var m = PAIR_RE.exec(value || '');
                    if (!m) {
                        if (!(value || '').trim()) { this.set('', ''); }
                        return;  // the server explains a bad pair on save
                    }
                    this.set(m[1], m[2]);
                    if (this.map && this.has()) { this.map.setView([Number(this.lat), Number(this.lng)], Math.max(this.map.getZoom(), 15)); }
                },

                applyLink: function (value) {
                    var self = this;
                    value = (value || '').trim();
                    this.linkNote = '';
                    if (!value || !parseUrl) { return; }
                    this.linkNote = 'Reading the link…';
                    fetch(parseUrl + '?link=' + encodeURIComponent(value), {
                        credentials: 'same-origin',
                        headers: { 'Accept': 'application/json' }
                    }).then(function (r) { return r.json(); }).then(function (data) {
                        if (data && data.ok) {
                            self.set(data.latitude, data.longitude);
                            self.linkNote = 'Found ' + self.lat + ', ' + self.lng + ' in the link.';
                            // The pair is now in the form; keep the link out of
                            // the save so the server does not fetch it twice.
                            if (self.$refs.link) { self.$refs.link.value = ''; }
                            if (self.map) { self.map.setView([Number(self.lat), Number(self.lng)], Math.max(self.map.getZoom(), 15)); }
                        } else {
                            self.linkNote = (data && data.error) || 'That link could not be read.';
                        }
                    }).catch(function () {
                        self.linkNote = 'That link could not be read. It will be tried again on save.';
                    });
                },

                mount: function () {
                    var self = this;
                    var el = this.$refs.map;
                    if (this.map || !el || !window.L || !document.body.contains(el)) { return; }
                    var start = this.has() ? [Number(this.lat), Number(this.lng)] : [centreLat, centreLng];
                    var zoom = this.has() ? 16 : centreZoom;
                    this.map = L.map(el, { zoomControl: true, attributionControl: false, tap: false }).setView(start, zoom);
                    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
                        maxZoom: 19,
                        attribution: '&copy; OpenStreetMap contributors'
                    }).addTo(this.map);
                    this.map.on('click', function (e) { self.set(e.latlng.lat, e.latlng.lng, true); self.placePin(); });
                    this.placePin();
                    // Inside the pop-up sheet the container has no size until
                    // the open transition ends; without this the tiles render
                    // at zero width.
                    setTimeout(function () { if (self.map) { self.map.invalidateSize(); } }, 250);
                    if (window.ResizeObserver) {
                        this._ro = new ResizeObserver(function () { if (self.map) { self.map.invalidateSize(); } });
                        this._ro.observe(el);
                    }
                },

                placePin: function () {
                    var self = this;
                    if (!this.map) { return; }
                    if (!this.has()) {
                        if (this.marker) { this.map.removeLayer(this.marker); this.marker = null; }
                        return;
                    }
                    var pos = [Number(this.lat), Number(this.lng)];
                    if (this.marker) {
                        this.marker.setLatLng(pos);
                        return;
                    }
                    this.marker = L.marker(pos, { draggable: true, keyboard: false }).addTo(this.map);
                    this.marker.on('dragend', function () {
                        var p = self.marker.getLatLng();
                        self.set(p.lat, p.lng, true);
                    });
                }
            };
        });
    });
})();
