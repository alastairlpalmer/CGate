// Previous and next horse with the ← and → keys on the horse page
// (docs/HORSE_PAGE_UX_REVIEW.md E1), as ↑ and ↓ step through the list's
// preview pane (split_view.js). The links are the stepper's rel="prev" and
// rel="next" in templates/horses/horse_detail.html; a click on them goes
// through htmx's boost like a tap does.
(function () {
    'use strict';

    // The key means "step" only when nothing else on the page wants it: not
    // while typing, not inside a menu, a sheet or a scrolling diagram, and
    // not with a modifier (Alt + ← is the browser's Back).
    function stepFor(e) {
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) { return null; }
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') { return null; }
        var t = e.target;
        if (t && t.closest) {
            if (t.closest('input, textarea, select, [contenteditable="true"], [role="menu"], [role="dialog"], .ped-scroll')) {
                return null;
            }
        }
        return e.key === 'ArrowLeft' ? 'prev' : 'next';
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { stepFor: stepFor };
    }
    if (typeof document === 'undefined') { return; }

    document.addEventListener('keydown', function (e) {
        var stepper = document.querySelector('[data-testid="horse-stepper"]');
        if (!stepper) { return; }
        var popup = window.Alpine && Alpine.store && Alpine.store('popup');
        if (popup && popup.open) { return; }
        var rel = stepFor(e);
        if (!rel) { return; }
        var link = stepper.querySelector('a[rel="' + rel + '"]');
        if (!link) { return; }
        e.preventDefault();
        link.click();
    });
})();
