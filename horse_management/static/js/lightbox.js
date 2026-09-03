/* Photo lightbox (templates/includes/_photo_lightbox.html).
 *
 * Any element with `data-lightbox-src` opens the full-screen viewer:
 *
 *   data-lightbox-src      full-resolution image URL (required)
 *   data-lightbox-title    heading, e.g. the horse name
 *   data-lightbox-caption  small line under the photo, e.g. age / colour / sex
 *   data-lightbox-href     optional link out of the viewer, e.g. the horse page
 *
 * The listeners run in the capture phase and stop the event, so a trigger
 * inside a boosted <a> (a horse-list row) shows the photo instead of
 * navigating. Without Alpine the event is left alone and the link works as
 * before, so the viewer is an enhancement and never a dead end.
 */
(function () {
    'use strict';

    document.addEventListener('alpine:init', function () {
        Alpine.store('lightbox', {
            open: false,
            src: '',
            title: '',
            caption: '',
            href: '',
            loading: true,
            failed: false,
            opener: null,   // the trigger element, for focus return

            show: function (photo) {
                this.src = photo.src || '';
                this.title = photo.title || '';
                this.caption = photo.caption || '';
                this.href = photo.href || '';
                this.opener = photo.opener || null;
                this.loading = true;
                this.failed = false;
                this.open = true;
                document.documentElement.classList.add('overflow-hidden');
                // The panel is display:none until Alpine applies x-show
                Alpine.nextTick(function () {
                    var panel = document.getElementById('lightbox-panel');
                    if (panel) { panel.focus({ preventScroll: true }); }
                });
            },

            close: function () {
                if (!this.open) { return; }
                this.open = false;
                var opener = this.opener;
                this.opener = null;
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
        Alpine.store('lightbox').show({
            src: el.getAttribute('data-lightbox-src'),
            title: el.getAttribute('data-lightbox-title') || '',
            caption: el.getAttribute('data-lightbox-caption') || '',
            href: el.getAttribute('data-lightbox-href') || '',
            opener: el
        });
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
        if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') { return; }
        var el = triggerFor(e.target);
        if (!el || el !== e.target || !openViewer(el)) { return; }
        e.preventDefault();
        e.stopPropagation();
    }, true);
})();
