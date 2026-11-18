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

    function load(fresh) {
        var el = panel();
        if (!el || !window.htmx) { return; }
        var url = el.getAttribute('data-src') + (fresh ? '?fresh=1' : '');
        window.htmx.ajax('GET', url, { source: el, target: el, swap: 'innerHTML' });
    }

    document.addEventListener('alpine:init', function () {
        Alpine.store('notif', {
            open: false,
            _returnTo: null,

            toggle: function () {
                if (this.open) { this.close(); } else { this.show(); }
            },

            show: function () {
                this._returnTo = document.activeElement;
                this.open = true;
                load(false);
                Alpine.nextTick(function () {
                    var drawer = document.getElementById('notif-drawer');
                    if (drawer) { drawer.focus({ preventScroll: true }); }
                });
            },

            close: function () {
                if (!this.open) { return; }
                this.open = false;
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
