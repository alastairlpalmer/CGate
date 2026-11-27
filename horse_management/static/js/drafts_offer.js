/**
 * Keep what was typed when the sign-in ends during a form (pre-launch
 * review: the save went to the sign-in page and the typing was lost).
 *
 * js/drafts.js (every page): on submit of a form that posts, a copy of
 * its typed fields goes in this tab's sessionStorage. Never a password,
 * a file or a hidden field, and never a form that has a password box.
 * The sign-in page marks a copy made in the last two minutes as
 * interrupted. A save that works, or any other page load, drops a copy
 * that was not interrupted.
 *
 * This file: the Restore banner. It fills the same form on the page, or
 * opens the pop-up sheet the form was in and fills it there. Loaded by drafts.js only when a
 * save was interrupted by the end of the sign-in, so other pages do not
 * carry it (the size limit in core/tests/test_phone_speed.py).
 */
(function (root) {
    'use strict';

    var shared = root.cgateDraft;
    if (!shared) { return; }
    var doc = root.document;
    var read = shared.read;
    var write = shared.write;
    var pathOf = shared.pathOf;
    var SKIP_TYPES = shared.skip;

    /** Fill ``elements`` (a form's) from ``fields``. Returns how many were set. */
    function fill(form, fields) {
        var seen = {};
        var set = 0;
        fields.forEach(function (pair) {
            var name = pair[0];
            var value = pair[1];
            var all = form.querySelectorAll('[name="' + name.replace(/"/g, '\\"') + '"]');
            var index = seen[name] || 0;
            seen[name] = index + 1;
            var el = all[index] || null;
            if (!el) { return; }
            var type = (el.type || '').toLowerCase();
            if (SKIP_TYPES[type]) { return; }
            if (type === 'checkbox' || type === 'radio') {
                el.checked = value !== null;
            } else if (el.tagName === 'SELECT' && el.multiple && Array.isArray(value)) {
                for (var j = 0; j < el.options.length; j++) {
                    el.options[j].selected = value.indexOf(el.options[j].value) !== -1;
                }
            } else {
                el.value = value;
            }
            el.dispatchEvent(new root.Event('input', { bubbles: true }));
            el.dispatchEvent(new root.Event('change', { bubbles: true }));
            set += 1;
        });
        return set;
    }

    function offer(draft) {
        var restore = function () {
            write(null);
            var forms = doc.querySelectorAll('form');
            for (var i = 0; i < forms.length; i++) {
                var f = forms[i];
                var act = pathOf(f.getAttribute('hx-post') || f.getAttribute('action') || root.location.href, root.location.href);
                if (act === draft.action) { fill(f, draft.fields); return; }
            }
            if (!draft.title || !root.htmx) { return; }
            // The form was in the pop-up sheet: open it again, then fill it.
            var link = doc.createElement('a');
            link.setAttribute('hx-get', draft.action);
            link.setAttribute('hx-target', '#popup-body');
            link.setAttribute('hx-select', 'unset');
            link.setAttribute('hx-swap', 'innerHTML');
            link.setAttribute('hx-push-url', 'false');
            link.setAttribute('data-popup-title', draft.title);
            link.hidden = true;
            doc.body.appendChild(link);
            root.htmx.process(link);
            var onSwap = function (ev) {
                var body = doc.getElementById('popup-body');
                if (!body || ev.detail.target !== body) { return; }
                doc.body.removeEventListener('htmx:afterSwap', onSwap);
                var form = body.querySelector('form');
                if (form) { fill(form, draft.fields); }
                link.remove();
            };
            doc.body.addEventListener('htmx:afterSwap', onSwap);
            root.htmx.trigger(link, 'click');
        };
        // Its own banner: a message in the shared box goes at the next
        // request that works, and a page makes some at once.
        var place = doc.getElementById('main-content') || doc.body;
        var banner = doc.createElement('div');
        banner.className = 'alert-warning flex-wrap';
        banner.setAttribute('role', 'alert');
        banner.setAttribute('data-draft-offer', '');
        var text = doc.createElement('p');
        text.className = 'flex-1 min-w-0';
        text.textContent = 'You were signed out before your last save went through, so it was not saved. Your typing is kept on this device.';
        var yes = doc.createElement('button');
        yes.type = 'button';
        yes.className = 'btn-primary btn-xs';
        yes.textContent = 'Restore what you typed';
        var no = doc.createElement('button');
        no.type = 'button';
        no.className = 'btn-ghost btn-xs';
        no.textContent = 'Discard';
        yes.addEventListener('click', function () { banner.remove(); restore(); });
        no.addEventListener('click', function () { banner.remove(); write(null); });
        banner.appendChild(text);
        banner.appendChild(yes);
        banner.appendChild(no);
        place.insertBefore(banner, place.firstChild);
    }

    offer(shared.draft);
})(window);
