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
    // edge, kept inside the window, and above the button when it does not
    // fit below. `rect` is the button's getBoundingClientRect().
    function placeFixed(rect, width, height, viewWidth, viewHeight) {
        var GAP = 6, EDGE = 8;
        var left = Math.min(Math.max(rect.right - width, EDGE), viewWidth - width - EDGE);
        left = Math.max(left, EDGE);
        var top = rect.bottom + GAP;
        if (top + height > viewHeight - EDGE && rect.top - GAP - height >= EDGE) {
            top = rect.top - GAP - height;
        }
        return { top: Math.round(top), left: Math.round(left) };
    }

    function register(Alpine) {
        Alpine.data('moreMenu', function () {
            return {
                open: false,
                init: function () {
                    var self = this;
                    // Follow the button. A phone hides its address bar as the
                    // page scrolls, which is a resize, so closing on every
                    // move would shut the menu under the reader's thumb.
                    // Close only once the button has left the window.
                    this._onMove = function () {
                        if (!self.open) return;
                        var r = self.$refs.trigger.getBoundingClientRect();
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
                    var panel = this.$refs.panel, trigger = this.$refs.trigger;
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
                        window.innerWidth, bottom);
                    panel.style.position = 'fixed';
                    panel.style.top = pos.top + 'px';
                    panel.style.left = pos.left + 'px';
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
                    this._host = this.$root.closest('td, li');
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
                    if (returnFocus && this.$refs.trigger) this.$refs.trigger.focus();
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
                        var list = this.items();
                        var i = nextIndex(list.indexOf(document.activeElement), list.length, e.key);
                        if (i >= 0) list[i].focus();
                    }
                }
            };
        });
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { nextIndex: nextIndex, placeFixed: placeFixed };
    }
    if (typeof document !== 'undefined') {
        document.addEventListener('alpine:init', function () { register(window.Alpine); });
    }
})();
