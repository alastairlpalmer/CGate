/* "More" menu (templates/includes/_more_button.html).
 *
 * One page shows one main button. The other actions go in a More menu:
 *
 *   <div class="more-menu" x-data="moreMenu" @click.outside="close(false)"
 *        @keydown.escape="close(true)">
 *     {% include 'includes/_more_button.html' with label="More actions" %}
 *     <div class="more-menu-panel" x-ref="panel" x-show="open" x-cloak
 *          role="menu" @keydown="onPanelKey($event)" @click="close(false)">
 *       <a role="menuitem" class="more-menu-item" href="…">Edit</a>
 *       <div class="more-menu-sep" role="separator"></div>
 *       <form …><button role="menuitem" class="more-menu-item is-danger">Delete</button></form>
 *     </div>
 *   </div>
 *
 * Once Alpine runs, the panel is placed against the window: under the
 * button, or above it when there is no room below, and never past an edge
 * of the window. So a card that clips its content (overflow hidden or
 * auto) cannot cut it off, and a button that wraps to the left on a phone
 * does not push it off the screen. It follows the button as the page
 * scrolls, and closes when the button leaves the window. While it is
 * open, the row or cell that holds it (the closest td or li) gets
 * `has-open-menu`, which lifts it over the rows after it.
 *
 * Keyboard: Enter, Space or Down on the button opens the menu and puts focus
 * on the first item. Up and Down move, Home and End jump, Escape closes and
 * puts focus back on the button, Tab closes and moves on. A mouse click
 * opens the menu without moving focus.
 */
(function () {
    'use strict';

    // The item to focus after a key press, or -1 when there are no items.
    // `current` is -1 when focus is not on an item yet.
    function nextIndex(current, count, key) {
        if (!count) return -1;
        if (key === 'Home') return 0;
        if (key === 'End') return count - 1;
        if (key === 'ArrowDown') return current < 0 ? 0 : (current + 1) % count;
        if (key === 'ArrowUp') return current < 0 ? count - 1 : (current - 1 + count) % count;
        return current;
    }

    // Where a fixed panel goes: its right edge under the button's right
    // edge (its left edge under the button's left edge with `alignLeft`,
    // for a button at the start of a row), kept inside the window, and above the button when it does not
    // fit below. `rect` is the button's getBoundingClientRect().
    function placeFixed(rect, width, height, viewWidth, viewHeight, alignLeft) {
        var GAP = 6, EDGE = 8;
        var start = alignLeft ? rect.left : rect.right - width;
        var left = Math.min(Math.max(start, EDGE), viewWidth - width - EDGE);
        left = Math.max(left, EDGE);
        var top = rect.bottom + GAP;
        if (top + height > viewHeight - EDGE && rect.top - GAP - height >= EDGE) {
            top = rect.top - GAP - height;
        }
        return { top: Math.round(top), left: Math.round(left) };
    }

    // The box that position: fixed is measured from: the window, unless an
    // ancestor has a transform, filter or perspective.
    function fixedOrigin(el) {
        for (var node = el.parentElement; node && node !== document.body; node = node.parentElement) {
            var cs = window.getComputedStyle(node);
            if ((cs.transform && cs.transform !== 'none') ||
                (cs.filter && cs.filter !== 'none') ||
                (cs.perspective && cs.perspective !== 'none')) {
                var r = node.getBoundingClientRect();
                return { top: r.top, left: r.left };
            }
        }
        return { top: 0, left: 0 };
    }

    // The row an item of a shared menu acts on is named by a placeholder
    // in its address: /horses/0/move/ becomes /horses/52/move/.
    function rowUrl(template, pk) {
        return String(template).replace('/0/', '/' + pk + '/');
    }

    function rowTitle(template, name) {
        return String(template).replace('{name}', name);
    }

    function menuData() {
            return {
                open: false,
                // Named here so that each menu has its own. Alpine puts a
                // field that a component did not name on the outermost
                // x-data of the page (the body): every menu then shared
                // one `_onMove`, and destroy() removed another menu's
                // listener and left its own.
                _onMove: null,
                _host: null,
                // The button the panel is placed against.
                trigger: function () { return this.$refs.trigger; },
                init: function () {
                    var self = this;
                    // Follow the button. A phone hides its address bar as the
                    // page scrolls, which is a resize, so closing on every
                    // move would shut the menu under the reader's thumb.
                    // Close only once the button has left the window.
                    this._onMove = function () {
                        if (!self.open) return;
                        var button = self.trigger();
                        if (!button || !button.isConnected) { self.close(false); return; }
                        var r = button.getBoundingClientRect();
                        if (r.bottom < 0 || r.top > window.innerHeight) self.close(false);
                        else self.place();
                    };
                    window.addEventListener('scroll', this._onMove, true);
                    window.addEventListener('resize', this._onMove);
                },
                destroy: function () {
                    if (!this._onMove) return;
                    window.removeEventListener('scroll', this._onMove, true);
                    window.removeEventListener('resize', this._onMove);
                },
                place: function () {
                    var panel = this.$refs.panel, trigger = this.trigger();
                    if (!panel || !trigger) return;
                    // On a phone the tab bar covers the foot of the window,
                    // so the room below ends at its top edge.
                    var bottom = window.innerHeight;
                    var bar = document.getElementById('mobile-tab-bar');
                    if (bar && bar.getClientRects().length) {
                        bottom = Math.min(bottom, bar.getBoundingClientRect().top);
                    }
                    var pos = placeFixed(trigger.getBoundingClientRect(),
                        panel.offsetWidth, panel.offsetHeight,
                        window.innerWidth, bottom, panel.classList.contains('is-left'));
                    // Inside a transformed parent (the horse sheet on a
                    // phone slides on a transform) "fixed" is measured from
                    // that parent, not the window, so take its corner off.
                    var origin = fixedOrigin(panel);
                    panel.style.position = 'fixed';
                    panel.style.top = (pos.top - origin.top) + 'px';
                    panel.style.left = (pos.left - origin.left) + 'px';
                    panel.style.right = 'auto';
                    panel.style.marginTop = '0';
                },
                items: function () {
                    var panel = this.$refs.panel;
                    if (!panel) return [];
                    // Leave out items hidden at this width (md:hidden and the
                    // like): they cannot take focus.
                    return Array.prototype.slice.call(
                        panel.querySelectorAll('[role="menuitem"]:not([disabled])'))
                        .filter(function (el) { return el.getClientRects().length > 0; });
                },
                show: function (focusFirst) {
                    this.open = true;
                    var button = this.trigger();
                    this._host = button ? button.closest('td, li') : null;
                    if (this._host) this._host.classList.add('has-open-menu');
                    var self = this;
                    this.$nextTick(function () {
                        self.place();
                        if (focusFirst) {
                            var list = self.items();
                            if (list.length) list[0].focus();
                        }
                    });
                },
                close: function (returnFocus) {
                    if (!this.open) return;
                    this.open = false;
                    if (this._host) this._host.classList.remove('has-open-menu');
                    var button = this.trigger();
                    if (returnFocus && button && button.isConnected) button.focus();
                },
                toggle: function () {
                    if (this.open) this.close(false); else this.show(false);
                },
                onTriggerKey: function (e) {
                    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        this.show(true);
                    }
                },
                onPanelKey: function (e) {
                    if (e.key === 'Escape') {
                        e.preventDefault();
                        e.stopPropagation();
                        this.close(true);
                        return;
                    }
                    if (e.key === 'Tab') {
                        this.close(false);
                        return;
                    }
                    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].indexOf(e.key) !== -1) {
                        e.preventDefault();
                        // The key is the menu's. On the horse list the page
                        // also reads the arrows, to go to the next horse.
                        e.stopPropagation();
                        var list = this.items();
                        var i = nextIndex(list.indexOf(document.activeElement), list.length, e.key);
                        if (i >= 0) list[i].focus();
                    }
                }
            };
    }

    // One menu for all the rows of a list (templates/horses/_row_menu.html).
    // A list of 75 horses had 75 menus: 75 Alpine components and 300 links
    // for htmx to set up, to show one menu at a time. Now a row has only
    // its button, `data-row-menu="<pk>" data-name="<name>"`, and this one
    // panel is filled for the row when its button is used.
    function rowMenuData() {
        var menu = menuData();
        var baseInit = menu.init, baseDestroy = menu.destroy, baseClose = menu.close;
        menu._trigger = null;
        menu._onClick = null;
        menu._onKey = null;
        menu.trigger = function () { return this._trigger; };
        menu.init = function () {
            baseInit.call(this);
            var self = this;
            this._onClick = function (e) {
                var button = e.target.closest ? e.target.closest('[data-row-menu]') : null;
                if (button) { self.openFor(button, false); return; }
                // A click on an item closes the panel itself (@click).
                if (self.open && !self.$refs.panel.contains(e.target)) self.close(false);
            };
            // On the way down (capture), so the key is taken before the
            // page's own key handlers see it.
            this._onKey = function (e) {
                var button = e.target.closest ? e.target.closest('[data-row-menu]') : null;
                if (button && (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ')) {
                    e.preventDefault();
                    e.stopPropagation();
                    self.openFor(button, true);
                } else if (e.key === 'Escape' && self.open && !self.$refs.panel.contains(e.target)) {
                    e.stopPropagation();
                    self.close(true);
                }
            };
            document.addEventListener('click', this._onClick);
            document.addEventListener('keydown', this._onKey, true);
        };
        menu.destroy = function () {
            baseDestroy.call(this);
            document.removeEventListener('click', this._onClick);
            document.removeEventListener('keydown', this._onKey, true);
        };
        menu.openFor = function (button, fromKey) {
            var same = this.open && this._trigger === button;
            if (same && !fromKey) { this.close(false); return; }
            if (this.open && !same) this.close(false);
            this._trigger = button;
            this.fill(button);
            button.setAttribute('aria-expanded', 'true');
            this.show(fromKey);
        };
        menu.close = function (returnFocus) {
            if (this._trigger) this._trigger.setAttribute('aria-expanded', 'false');
            baseClose.call(this, returnFocus);
        };
        menu.fill = function (button) {
            var panel = this.$refs.panel;
            var pk = button.getAttribute('data-row-menu');
            var name = button.getAttribute('data-name') || '';
            var row = button.closest('tr, li');
            var page = row ? row.querySelector('[data-horse-link]') : null;
            panel.setAttribute('aria-label', 'More actions for ' + name);
            Array.prototype.forEach.call(panel.querySelectorAll('[role="menuitem"]'), function (item) {
                var d = item.dataset;
                if (d.rowHref) item.setAttribute('href', rowUrl(d.rowHref, pk));
                if (d.rowGet) item.setAttribute('hx-get', rowUrl(d.rowGet, pk));
                if (d.rowTitle) item.setAttribute('data-popup-title', rowTitle(d.rowTitle, name));
                // The row's own link knows which list to go back to.
                if (d.rowPage !== undefined && page) item.setAttribute('href', page.getAttribute('href'));
            });
            // htmx reads hx-get when it sets a link up, so set them up again.
            if (typeof window !== 'undefined' && window.htmx) window.htmx.process(panel);
        };
        return menu;
    }

    function register(Alpine) {
        Alpine.data('moreMenu', menuData);
        Alpine.data('rowMenu', rowMenuData);
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            nextIndex: nextIndex, placeFixed: placeFixed, rowUrl: rowUrl, rowTitle: rowTitle,
            menuData: menuData, rowMenuData: rowMenuData,
        };
    }
    if (typeof document !== 'undefined') {
        document.addEventListener('alpine:init', function () { register(window.Alpine); });
    }
})();
