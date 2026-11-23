/* The notification centre drawer (base.html, notifications/centre_views.py).
 *
 * Alpine store `notif`: open/close the drawer. Opening loads the panel
 * (/notifications/panel/) into #notif-panel; closing asks both badges to
 * refresh, so a notification read in the drawer drops off the count.
 * A save in the pop-up sheet (popup:saved) reloads an open panel with a
 * fresh sync: a vaccination recorded from the drawer closes its row.
 */
(function () {
    'use strict';

    function panel() { return document.getElementById('notif-panel'); }

    function load(fresh, tab) {
        var el = panel();
        if (!el || !window.htmx) { return; }
        var query = [];
        if (fresh) { query.push('fresh=1'); }
        if (tab) { query.push('tab=' + encodeURIComponent(tab)); }
        var url = el.getAttribute('data-src') + (query.length ? '?' + query.join('&') : '');
        window.htmx.ajax('GET', url, { source: el, target: el, swap: 'innerHTML' }).catch(function () { /* said by js/connection.js */ });
    }

    document.addEventListener('alpine:init', function () {
        Alpine.store('notif', {
            open: false,
            _returnTo: null,

            toggle: function () {
                if (this.open) { this.close(); } else { this.show(); }
            },

            // ``tab``: open on that tab ('new', 'action' or 'all').
            show: function (tab) {
                var self = this;
                this._returnTo = document.activeElement;
                this.open = true;
                // The phone's Back button closes the drawer (overlay_back.js).
                if (window.Yardway && Yardway.overlay) {
                    Yardway.overlay.opened('notif', function () { self.close(); }, function () { return self.open; });
                }
                load(false, tab);
                Alpine.nextTick(function () {
                    var drawer = document.getElementById('notif-drawer');
                    if (drawer) { drawer.focus({ preventScroll: true }); }
                });
            },

            close: function () {
                if (!this.open) { return; }
                this.open = false;
                // Before the badges ask for their counts: a request in
                // flight makes the entry wait (overlay_back.js, rule 1).
                if (window.Yardway && Yardway.overlay) { Yardway.overlay.closed('notif'); }
                if (window.htmx) { window.htmx.trigger(document.body, 'notif:changed'); }
                var back = this._returnTo;
                this._returnTo = null;
                if (back && document.body.contains(back) && back.focus) {
                    back.focus({ preventScroll: true });
                }
            },
        });
    });

    document.addEventListener('popup:saved', function () {
        if (window.Alpine && Alpine.store('notif') && Alpine.store('notif').open) { load(true); }
    });
    // A boosted page change closes the drawer: its rows point at the old page.
    document.addEventListener('htmx:beforeHistorySave', function () {
        if (window.Alpine && Alpine.store('notif')) { Alpine.store('notif').close(); }
    });
}());
