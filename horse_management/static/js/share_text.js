/* The day's lots go to the team as text (docs/TRAINING_BOARD_REDESIGN_PLAN.md, 5.5).
 *
 * A yard sends its lots as a message. So a button with
 *
 *   data-share-text="#share-board"  data-share-title="Lots, Wed 25 Nov"
 *
 * takes the text from that element (a hidden <textarea>, which the server
 * filled) and gives it to the phone's share sheet: WhatsApp, Messages, Mail.
 * A computer has no share sheet that helps, so there the text is copied and
 * a message says so. data-share-copy always copies.
 *
 * The listener is on the document, so it is added one time and works after
 * each page swap.
 */
(function (root) {
    'use strict';

    // What to do with the text: 'share' on a device with a finger and a
    // share sheet, 'copy' everywhere else and when the button asks for it.
    function plan(nav, touch, copyOnly) {
        if (copyOnly) return 'copy';
        if (touch && nav && typeof nav.share === 'function') return 'share';
        return 'copy';
    }

    // The share sheet did not take the text: 'done' when the person closed
    // it, 'copy' for anything else, so the text is never lost.
    function afterShareError(error) {
        return error && error.name === 'AbortError' ? 'done' : 'copy';
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { plan: plan, afterShareError: afterShareError };
        return;
    }

    var doc = root.document;

    function touch() {
        return !!(root.matchMedia && root.matchMedia('(pointer: coarse)').matches);
    }

    function say(kind, text) {
        if (root.Yardway && root.Yardway.notice) root.Yardway.notice(kind, text);
    }

    // The old way to copy, for a browser with no clipboard API or a page
    // that is not on https.
    function copyOldWay(text) {
        var box = doc.createElement('textarea');
        box.value = text;
        box.setAttribute('readonly', '');
        box.style.position = 'fixed';
        box.style.opacity = '0';
        doc.body.appendChild(box);
        box.select();
        var ok = false;
        try { ok = doc.execCommand('copy'); } catch (err) { ok = false; }
        box.remove();
        return ok ? Promise.resolve() : Promise.reject(new Error('copy'));
    }

    function copy(text) {
        var clip = root.navigator && root.navigator.clipboard;
        var done = clip && clip.writeText
            ? clip.writeText(text).catch(function () { return copyOldWay(text); })
            : copyOldWay(text);
        return done.then(function () {
            say('info', 'Copied. Paste it in a message to the team.');
        }, function () {
            say('error', 'The text was not copied. Use Print in its place.');
        });
    }

    doc.addEventListener('click', function (event) {
        var button = event.target.closest && event.target.closest('[data-share-text]');
        if (!button) return;
        var source = doc.querySelector(button.getAttribute('data-share-text'));
        if (!source) return;
        event.preventDefault();
        var text = source.value || source.textContent || '';
        var title = button.getAttribute('data-share-title') || '';
        if (plan(root.navigator, touch(), button.hasAttribute('data-share-copy')) === 'copy') {
            copy(text);
            return;
        }
        root.navigator.share({ title: title, text: text }).catch(function (error) {
            if (afterShareError(error) === 'copy') copy(text);
        });
    });
})(typeof window !== 'undefined' ? window : this);
