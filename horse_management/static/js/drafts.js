// Keeps typed fields when the sign-in ends during a save. See js/drafts_offer.js.
(function (root) {
    'use strict';

    var KEY = 'cgate-form-draft';
    var INTERRUPT_MS = 2 * 60 * 1000;
    var OFFER_MS = 2 * 60 * 60 * 1000;
    var SKIP_TYPES = { password: 1, file: 1, hidden: 1, submit: 1, button: 1, reset: 1, image: 1 };

    function pathOf(url, base) {
        try {
            var u = new URL(url, base);
            return u.pathname + u.search;
        } catch (err) { return ''; }
    }

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

    function keepable(elements, action) {
        if (/\/accounts\/(login|logout|password|reset|two-step|confirm)/.test(action || '')) { return false; }
        for (var i = 0; i < elements.length; i++) {
            if ((elements[i].type || '').toLowerCase() === 'password') { return false; }
        }
        return true;
    }

    function decide(draft, now, onSignInPage) {
        if (!draft || !draft.time) { return 'drop'; }
        var age = now - draft.time;
        if (onSignInPage) { return age <= INTERRUPT_MS ? 'interrupt' : 'drop'; }
        if (draft.interrupted) { return age <= OFFER_MS ? 'offer' : 'drop'; }
        return 'drop';
    }

    var api = { collect: collect, keepable: keepable, decide: decide, pathOf: pathOf,
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

    doc.addEventListener('htmx:afterRequest', function (e) {
        var d = e.detail || {};
        var verb = ((d.requestConfig && d.requestConfig.verb) || '').toLowerCase();
        if (!d.successful || verb !== 'post') { return; }
        var draft = read();
        if (draft && !draft.interrupted) { write(null); }
    });

    function offer(draft) {
        root.cgateDraft = { draft: draft, read: read, write: write, pathOf: pathOf, skip: SKIP_TYPES };
        var src = (doc.querySelector('script[src*="js/drafts.js"]') || {}).src || '';
        var tag = doc.createElement('script');
        tag.src = src.replace('js/drafts.js', 'js/drafts_offer.js');
        doc.head.appendChild(tag);
    }

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
