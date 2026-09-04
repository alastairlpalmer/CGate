/* Photo lightbox (templates/includes/_photo_lightbox.html).
 *
 * Any element with `data-lightbox-src` opens the full-screen viewer:
 *
 *   data-lightbox-src      full-resolution image URL (required)
 *   data-lightbox-title    heading, e.g. the horse name
 *   data-lightbox-caption  small line under the photo, e.g. age / colour / sex
 *   data-lightbox-href     optional link out of the viewer, e.g. the horse page
 *   data-lightbox-group    name of the set to step through (swipe, arrow keys,
 *                          arrow buttons). Triggers with the same name, in
 *                          page order, make one gallery; a trigger with no
 *                          group stands alone.
 *
 * The listeners run in the capture phase and stop the event, so a trigger
 * inside a boosted <a> (a horse-list row) shows the photo instead of
 * navigating. Without Alpine the event is left alone and the link works as
 * before, so the viewer is an enhancement and never a dead end.
 */
(function () {
    'use strict';

    var SWIPE_MIN_PX = 45;      // shorter drags are a tap or a stray finger
    var SWIPE_MAX_MS = 800;     // slower drags are a hold, not a swipe

    function photoFrom(el) {
        return {
            src: el.getAttribute('data-lightbox-src'),
            title: el.getAttribute('data-lightbox-title') || '',
            caption: el.getAttribute('data-lightbox-caption') || '',
            href: el.getAttribute('data-lightbox-href') || ''
        };
    }

    // The photos to step through: the trigger's group, in page order. A
    // trigger with no group (or the only one in its group) shows on its own.
    // Hidden triggers are dropped — the horse list holds every horse twice,
    // once in the phone cards and once in the desktop table, and only the
    // set on screen should be stepped through.
    function galleryFor(el) {
        var group = el.getAttribute('data-lightbox-group');
        var alone = { photos: [photoFrom(el)], index: 0 };
        if (!group) { return alone; }
        var sel = '[data-lightbox-src][data-lightbox-group="' +
                  group.replace(/"/g, '\\"') + '"]';
        var els = Array.prototype.slice.call(document.querySelectorAll(sel))
            .filter(function (node) { return node.getClientRects().length > 0; });
        var index = els.indexOf(el);
        if (index < 0) { return alone; }
        return { photos: els.map(photoFrom), index: index };
    }

    document.addEventListener('alpine:init', function () {
        Alpine.store('lightbox', {
            open: false,
            photos: [],
            index: 0,
            src: '',
            title: '',
            caption: '',
            href: '',
            loading: true,
            failed: false,
            opener: null,   // the trigger element, for focus return
            touch: null,    // where the finger currently down went down
            swiped: 0,      // time of the last swipe, to swallow its click

            show: function (gallery, opener) {
                this.photos = gallery.photos;
                this.opener = opener || null;
                this.open = true;
                this.at(gallery.index);
                document.documentElement.classList.add('overflow-hidden');
                // The panel is display:none until Alpine applies x-show
                Alpine.nextTick(function () {
                    var panel = document.getElementById('lightbox-panel');
                    if (panel) { panel.focus({ preventScroll: true }); }
                });
            },

            at: function (i) {
                var photo = this.photos[i];
                if (!photo) { return; }
                this.index = i;
                this.src = photo.src;
                this.title = photo.title;
                this.caption = photo.caption;
                this.href = photo.href;
                this.loading = true;
                this.failed = false;
                this.preload();
            },

            // The photo either side is fetched now, so a swipe shows it at once.
            preload: function () {
                var self = this;
                [this.index - 1, this.index + 1].forEach(function (i) {
                    var photo = self.photos[self.wrap(i)];
                    if (photo) { (new Image()).src = photo.src; }
                });
            },

            wrap: function (i) {
                var n = this.photos.length;
                return n ? ((i % n) + n) % n : 0;
            },

            step: function (by) {
                if (this.photos.length < 2) { return; }
                this.at(this.wrap(this.index + by));
            },

            next: function () { this.step(1); },
            prev: function () { this.step(-1); },

            swipeBegin: function (e) {
                if (!e.touches || e.touches.length !== 1) { this.touch = null; return; }
                this.touch = { x: e.touches[0].clientX, y: e.touches[0].clientY,
                               at: Date.now() };
            },

            swipeEnd: function (e) {
                var start = this.touch;
                this.touch = null;
                if (!start || !e.changedTouches || !e.changedTouches.length) { return; }
                var dx = e.changedTouches[0].clientX - start.x;
                var dy = e.changedTouches[0].clientY - start.y;
                // A swipe is long, quick, and more sideways than up or down.
                if (Math.abs(dx) < SWIPE_MIN_PX ||
                    Math.abs(dx) < Math.abs(dy) * 1.5 ||
                    Date.now() - start.at > SWIPE_MAX_MS) { return; }
                this.swiped = Date.now();
                this.step(dx < 0 ? 1 : -1);
            },

            // A tap on the photo closes the viewer. The tail of a swipe can
            // also arrive as a click, which must not close it.
            tap: function () {
                if (Date.now() - this.swiped < 500) { return; }
                this.close();
            },

            close: function () {
                if (!this.open) { return; }
                this.open = false;
                var opener = this.opener;
                this.opener = null;
                this.touch = null;
                // The pop-up sheet locks scrolling too — leave its lock alone.
                var popup = window.Alpine ? Alpine.store('popup') : null;
                if (!popup || !popup.open) {
                    document.documentElement.classList.remove('overflow-hidden');
                }
                if (opener && document.body.contains(opener)) {
                    opener.focus({ preventScroll: true });
                }
            }
        });
    });

    function triggerFor(node) {
        return node && node.closest ? node.closest('[data-lightbox-src]') : null;
    }

    // Returns false when the viewer cannot open (Alpine not started yet), so
    // the caller leaves the event alone and the underlying link still works.
    function openViewer(el) {
        if (!window.Alpine || !Alpine.store('lightbox')) { return false; }
        Alpine.store('lightbox').show(galleryFor(el), el);
        return true;
    }

    document.addEventListener('click', function (e) {
        // Let people open the photo in a new tab or window as usual.
        if (e.button > 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) { return; }
        var el = triggerFor(e.target);
        if (!el || !openViewer(el)) { return; }
        e.preventDefault();
        e.stopPropagation();
    }, true);

    document.addEventListener('keydown', function (e) {
        var store = window.Alpine ? Alpine.store('lightbox') : null;
        if (store && store.open &&
            (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
            store.step(e.key === 'ArrowRight' ? 1 : -1);
            e.preventDefault();
            return;
        }
        if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') { return; }
        var el = triggerFor(e.target);
        if (!el || el !== e.target || !openViewer(el)) { return; }
        e.preventDefault();
        e.stopPropagation();
    }, true);
})();
