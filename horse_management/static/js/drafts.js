/**
 * Keep what was typed when the sign-in ends during a form (pre-launch
 * review: the save went to the sign-in page and the typing was lost).
 *
 * 1. On submit of a form that posts, a copy of its typed fields goes in
 *    this tab's sessionStorage. Never a password, a file or a hidden
 *    field, and never a form that has a password box.
 * 2. The sign-in page marks a copy made in the last two minutes as
 *    "interrupted": that save was sent to sign in, not saved.
 * 3. After sign-in, the next page offers Restore for an interrupted copy:
 *    it fills the same form on the page, or opens the pop-up sheet the
 *    form was in and fills it there. Any other page load deletes a copy
 *    that was not interrupted (the save went through).
 *
 * The pure parts are exported for static/js/tests/drafts.test.js.
 */
(function (root) {
    'use strict';

    var KEY = 'cgate-form-draft';
    var INTERRUPT_MS = 2 * 60 * 1000;     // submit to sign-in page
    var OFFER_MS = 2 * 60 * 60 * 1000;    // sign-in to the offer
    var SKIP_TYPES = { password: 1, file: 1, hidden: 1, submit: 1, button: 1, reset: 1, image: 1 };

    function pathOf(url, base) {
        try {
            var u = new URL(url, base);
            return u.pathname + u.search;
        } catch (err) { return ''; }
    }

    /** The fields worth keeping: [[name, value]] in page order. */
    function collect(elements) {
        var fields = [];
        for (var i = 0; i < elements.length; i++) {
            var el = elements[i];
            var type = (el.type || '').toLowerCase();
            if (!el.name || el.disabled || SKIP_TYPES[type]) { continue; }
            if (el.name === 'csrfmiddlewaretoken' || el.name === 'form_token') { continue; }
            if (type === 'checkbox' || type === 'radio') {
                fields.push([el.name, el.checked ? (el.value || 'on') : null]);
            } else if (el.tagName === 'SELECT' && el.multiple) {
                var chosen = [];
                for (var j = 0; j < el.options.length; j++) {
                    if (el.options[j].selected) { chosen.push(el.options[j].value); }
                }
                fields.push([el.name, chosen]);
            } else {
                fields.push([el.name, el.value]);
            }
        }
        return fields;
    }

    /** May a copy of this form be kept? */
    function keepable(elements, action) {
        if (/\/accounts\/(login|logout|password|reset|two-step|confirm)/.test(action || '')) { return false; }
        for (var i = 0; i < elements.length; i++) {
            if ((elements[i].type || '').toLowerCase() === 'password') { return false; }
        }
        return true;
    }

    /** What a page load does with a stored copy: 'offer', 'keep' or 'drop'. */
    function decide(draft, now, onSignInPage) {
        if (!draft || !draft.time) { return 'drop'; }
        var age = now - draft.time;
        if (onSignInPage) { return age <= INTERRUPT_MS ? 'interrupt' : 'drop'; }
        if (draft.interrupted) { return age <= OFFER_MS ? 'offer' : 'drop'; }
        return 'drop';
    }

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

    var api = { collect: collect, keepable: keepable, decide: decide, fill: fill, pathOf: pathOf,
                INTERRUPT_MS: INTERRUPT_MS, OFFER_MS: OFFER_MS };
    if (typeof module !== 'undefined' && module.exports) { module.exports = api; }
    if (!root.document) { return; }

    var doc = root.document;

    function read() {
        try { return JSON.parse(root.sessionStorage.getItem(KEY) || 'null'); } catch (err) { return null; }
    }
    function write(draft) {
        try {
            if (draft) { root.sessionStorage.setItem(KEY, JSON.stringify(draft)); }
            else { root.sessionStorage.removeItem(KEY); }
        } catch (err) { /* private mode: nothing kept */ }
    }

    // 1. Keep a copy on submit.
    doc.addEventListener('submit', function (e) {
        var form = e.target;
        if (!form || !form.elements || (form.method || '').toLowerCase() !== 'post') { return; }
        if (form.hasAttribute('data-no-draft')) { return; }
        var action = pathOf(form.getAttribute('hx-post') || form.getAttribute('action') || root.location.href, root.location.href);
        if (!keepable(form.elements, action)) { return; }
        var store = root.Alpine && root.Alpine.store && root.Alpine.store('popup');
        var inSheet = !!form.closest('#popup-body');
        write({
            page: root.location.pathname + root.location.search,
            action: action,
            title: inSheet && store ? store.title : '',
            fields: collect(form.elements),
            time: Date.now(),
            interrupted: false
        });
    }, true);

    // A save that worked: its copy is not needed.
    doc.addEventListener('htmx:afterRequest', function (e) {
        var d = e.detail || {};
        var verb = ((d.requestConfig && d.requestConfig.verb) || '').toLowerCase();
        if (!d.successful || verb !== 'post') { return; }
        var draft = read();
        if (draft && !draft.interrupted) { write(null); }
    });

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

    // 2 and 3. On each page load.
    function onLoad() {
        var draft = read();
        if (!draft) { return; }
        var onSignIn = /\/accounts\/login\/?$/.test(root.location.pathname);
        var what = decide(draft, Date.now(), onSignIn);
        if (what === 'interrupt') { draft.interrupted = true; write(draft); return; }
        if (what === 'offer') { offer(draft); return; }
        write(null);
    }
    if (doc.readyState === 'loading') { doc.addEventListener('DOMContentLoaded', onLoad); } else { onLoad(); }
})(typeof window !== 'undefined' ? window : globalThis);
