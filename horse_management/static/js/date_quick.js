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
 *
 * A date that is weeks or months away (the next due date of a
 * vaccination) gets its own buttons in place of the three days
 * (docs/MOBILE_APP_PLAN.md, C8). The box names them:
 *
 *   data-date-quick="+6m +12m"       "+6 months", "+12 months"
 *   data-date-quick="+6w +8w"        "+6 weeks", "+8 weeks"
 *   data-date-quick-from="date_given"
 *
 * They count from the date in the box named by data-date-quick-from (the
 * date the vaccination was given), or from today when that box is empty
 * or not named.
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

    var UNITS = { d: ['day', 'days'], w: ['week', 'weeks'], m: ['month', 'months'] };

    // "+6m +12m" → [{ amount: 6, unit: 'm', label: '+6 months' }, …].
    // Anything that is not such a list gives [].
    function parseSpec(spec) {
        var out = [];
        String(spec || '').split(/[\s,]+/).forEach(function (token) {
            var m = /^\+(\d{1,3})([dwm])$/.exec(token);
            if (!m) return;
            var amount = parseInt(m[1], 10);
            out.push({ amount: amount, unit: m[2], label: '+' + amount + ' ' + UNITS[m[2]][amount === 1 ? 0 : 1] });
        });
        return out;
    }

    // The ISO date `amount` days, weeks or months after the ISO date
    // `from`. A month that is too short gives its last day: 31 August
    // + 6 months is 28 February.
    function shift(from, amount, unit) {
        var parts = String(from).split('-');
        var y = parseInt(parts[0], 10), mo = parseInt(parts[1], 10) - 1, d = parseInt(parts[2], 10);
        var out;
        if (unit === 'm') {
            var last = new Date(y, mo + amount + 1, 0, 12).getDate();
            out = new Date(y, mo + amount, Math.min(d, last), 12);
        } else {
            out = new Date(y, mo, d + amount * (unit === 'w' ? 7 : 1), 12);
        }
        return out.getFullYear() + '-' + pad(out.getMonth() + 1) + '-' + pad(out.getDate());
    }

    // The box the buttons count from, or null.
    function fromBox(box) {
        var name = box.dataset ? box.dataset.dateQuickFrom : '';
        if (!name || !box.form || !box.form.querySelector) return null;
        return box.form.querySelector('input[name$="' + name + '"]');
    }

    // The day a button stands for, today.
    function dayOf(box, b) {
        if (b.dataset.unit) {
            var from = fromBox(box);
            var start = from && /^\d{4}-\d{2}-\d{2}$/.test(from.value) ? from.value : isoDay(new Date(), 0);
            return shift(start, parseInt(b.dataset.amount, 10), b.dataset.unit);
        }
        return isoDay(new Date(), parseInt(b.dataset.offset, 10));
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
            var day = dayOf(box, b);
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
        var later = parseSpec(box.dataset.dateQuick);
        var choices = later.length
            ? later.map(function (c) { return { label: c.label, set: { amount: String(c.amount), unit: c.unit } }; })
            : DAYS.map(function (pair) { return { label: pair[0], set: { offset: String(pair[1]) } }; });
        choices.forEach(function (choice) {
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'date-quick-btn';
            b.textContent = choice.label;
            Object.keys(choice.set).forEach(function (key) { b.dataset[key] = choice.set[key]; });
            b.addEventListener('click', function () {
                box.value = dayOf(box, b);
                box.dispatchEvent(new Event('input', { bubbles: true }));
                box.dispatchEvent(new Event('change', { bubbles: true }));
                refresh(box, bar);
            });
            bar.appendChild(b);
        });
        box.insertAdjacentElement('afterend', bar);
        // "+6 months" counts from another box: when that date changes,
        // the button that is pressed can change too.
        var from = later.length ? fromBox(box) : null;
        if (from) {
            from.addEventListener('input', function () { refresh(box, bar); });
            from.addEventListener('change', function () { refresh(box, bar); });
        }
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
        module.exports = { isoDay: isoDay, skip: skip, allowed: allowed, parseSpec: parseSpec, shift: shift };
    }
    if (typeof document !== 'undefined') {
        document.addEventListener('htmx:load', function (e) { enhanceAll(e.target); });
        document.addEventListener('DOMContentLoaded', function () { enhanceAll(document); });
    }
})();
