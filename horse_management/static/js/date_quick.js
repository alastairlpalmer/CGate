/* Yesterday, Today and Tomorrow under each date box (Dev board, 21 Nov
 * 2026: "each date field should have a button for yesterday, today,
 * tomorrow").
 *
 * Runs on every htmx:load (the first page, a boosted page swap, a pop-up
 * sheet), so every <input type="date"> gets the three buttons once. A
 * button puts that day in the box and fires input and change, so Alpine
 * and htmx see the new value. The button for the day in the box is
 * pressed (aria-pressed). A day outside the box's min or max is disabled.
 *
 * Left out:
 *   - a box with data-date-quick="off", or inside an element with it
 *   - a box in a GET form (list filters: a range, not one day)
 *   - a horse's date of birth and an expiry date (date_of_birth,
 *     expiry_date): never yesterday or tomorrow
 *   - a disabled or read-only box
 *
 * "Today" is the device's date, as the date box itself uses.
 */
(function () {
    'use strict';

    var DAYS = [['Yesterday', -1], ['Today', 0], ['Tomorrow', 1]];
    // A horse's date of birth and a document's expiry are never
    // yesterday or tomorrow. A foal's date of birth often is: it keeps them.
    var SKIP_NAME = /(^|[-_])(date_of_birth|expiry_date)$/i;

    function pad(n) { return (n < 10 ? '0' : '') + n; }

    // The ISO date `offset` days from `now` (a Date), in local time.
    function isoDay(now, offset) {
        var d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + offset, 12);
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
    }

    // True when a box should get no buttons. `box` needs only name,
    // disabled, readOnly, dataset, closest() and form.
    function skip(box) {
        if (!box || box.disabled || box.readOnly) return true;
        if (box.dataset && (box.dataset.dateQuick === 'off' || box.dataset.dateQuickDone)) return true;
        if (box.closest && box.closest('[data-date-quick="off"]')) return true;
        if (box.form && String(box.form.getAttribute ? box.form.getAttribute('method') || 'get' : box.form.method).toLowerCase() === 'get') return true;
        if (SKIP_NAME.test(box.name || '')) return true;
        return false;
    }

    // Is the ISO day inside the box's min and max? (ISO dates sort as text.)
    function allowed(day, min, max) {
        return !(min && day < min) && !(max && day > max);
    }

    function labelFor(box) {
        var label = box.id && document.querySelector('label[for="' + box.id + '"]');
        var text = label ? label.textContent.replace(/[*\s]+$/, '').trim() : '';
        return text || 'date';
    }

    function refresh(box, bar) {
        var buttons = bar.querySelectorAll('button');
        for (var i = 0; i < buttons.length; i++) {
            var b = buttons[i];
            var day = isoDay(new Date(), parseInt(b.dataset.offset, 10));
            b.disabled = !allowed(day, box.min, box.max);
            b.setAttribute('aria-pressed', box.value === day ? 'true' : 'false');
        }
    }

    function enhance(box) {
        if (skip(box)) return;
        box.dataset.dateQuickDone = '1';
        var bar = document.createElement('div');
        bar.className = 'date-quick';
        bar.setAttribute('role', 'group');
        bar.setAttribute('aria-label', 'Quick dates for ' + labelFor(box));
        DAYS.forEach(function (pair) {
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'date-quick-btn';
            b.textContent = pair[0];
            b.dataset.offset = String(pair[1]);
            b.addEventListener('click', function () {
                box.value = isoDay(new Date(), pair[1]);
                box.dispatchEvent(new Event('input', { bubbles: true }));
                box.dispatchEvent(new Event('change', { bubbles: true }));
                refresh(box, bar);
            });
            bar.appendChild(b);
        });
        box.insertAdjacentElement('afterend', bar);
        box.addEventListener('input', function () { refresh(box, bar); });
        box.addEventListener('change', function () { refresh(box, bar); });
        refresh(box, bar);
    }

    function enhanceAll(root) {
        var scope = root && root.querySelectorAll ? root : document;
        var boxes = scope.querySelectorAll('input[type="date"]');
        for (var i = 0; i < boxes.length; i++) enhance(boxes[i]);
        if (scope.matches && scope.matches('input[type="date"]')) enhance(scope);
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { isoDay: isoDay, skip: skip, allowed: allowed };
    }
    if (typeof document !== 'undefined') {
        document.addEventListener('htmx:load', function (e) { enhanceAll(e.target); });
        document.addEventListener('DOMContentLoaded', function () { enhanceAll(document); });
    }
})();
