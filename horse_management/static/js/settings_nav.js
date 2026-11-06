/* Settings sections (templates/settings.html).
 *
 * The settings page holds every group (Business, Users and roles, Yard
 * setup, Billing, Your preferences, Data). On a wide screen a menu on the
 * left shows one group at a time; on a phone the same menu is a row of
 * chips above it. Without JavaScript every group shows, as before.
 *
 * Which group opens:
 *   1. the one named in the address (#billing), or the one that holds the
 *      named element (#services lives in Billing), so old links still work;
 *   2. else the last one open in this browser tab, so a form that sends you
 *      back to the settings page (edit a rate, add a user) returns you to
 *      where you were;
 *   3. else the first.
 */
(function () {
    'use strict';

    var STORE_KEY = 'cgate:settings-section';

    // `ids`: the groups on the page, in order. `hashOwner`: the group that
    // holds the element the hash names (or ''). `stored`: the last group
    // open in this tab (or '').
    function pickSection(ids, hash, hashOwner, stored) {
        if (!ids.length) return '';
        var name = (hash || '').replace(/^#/, '');
        if (name && ids.indexOf(name) !== -1) return name;
        if (hashOwner && ids.indexOf(hashOwner) !== -1) return hashOwner;
        if (stored && ids.indexOf(stored) !== -1) return stored;
        return ids[0];
    }

    function readStore() {
        try { return window.sessionStorage.getItem(STORE_KEY) || ''; } catch (err) { return ''; }
    }

    function writeStore(id) {
        try { window.sessionStorage.setItem(STORE_KEY, id); } catch (err) { /* private mode */ }
    }

    function register(Alpine) {
        Alpine.data('settingsSections', function () {
            return {
                current: '',
                ids: [],
                init: function () {
                    var self = this;
                    this.ids = Array.prototype.map.call(
                        this.$root.querySelectorAll('section[data-settings-section]'),
                        function (el) { return el.id; });
                    this.fromHash(true);
                    window.addEventListener('hashchange', function () { self.fromHash(false); });
                },
                fromHash: function (first) {
                    var hash = window.location.hash;
                    var owner = '';
                    if (hash.length > 1) {
                        var target = document.getElementById(hash.slice(1));
                        var section = target && target.closest('section[data-settings-section]');
                        owner = section ? section.id : '';
                    }
                    this.current = pickSection(this.ids, hash, owner, first ? readStore() : '');
                    writeStore(this.current);
                    // An element inside the group (#services): scroll to it once shown.
                    if (owner && hash.slice(1) !== owner) {
                        this.$nextTick(function () {
                            var el = document.getElementById(hash.slice(1));
                            if (el) el.scrollIntoView({ block: 'start' });
                        });
                    }
                },
                show: function (id) {
                    this.current = id;
                    writeStore(id);
                    if (window.history && window.history.replaceState) {
                        window.history.replaceState(null, '', '#' + id);
                    }
                    var top = this.$refs.panels;
                    if (top && top.getBoundingClientRect().top < 0) top.scrollIntoView({ block: 'start' });
                }
            };
        });
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { pickSection: pickSection };
    }
    if (typeof document !== 'undefined') {
        document.addEventListener('alpine:init', function () { register(window.Alpine); });
    }
})();
