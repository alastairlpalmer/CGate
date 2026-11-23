/* A PDF goes to the phone's share sheet (docs/MOBILE_APP_PLAN.md, item D6).
 *
 * A link to a PDF, in an installed app on an iPhone, opens the PDF in the
 * app's own window: there is no Back, and the person must close the app.
 * And what someone does with an invoice on a phone is send it. So on a
 * phone or a tablet that can share a file, a link with
 *
 *   data-share-file="INV00012.pdf"  data-share-title="Invoice INV00012"
 *
 * downloads the file and gives it to the share sheet (Mail, Messages,
 * WhatsApp, Save to Files, Print). The page stays where it is.
 *
 * Everywhere else the link works as it did: a computer downloads the file,
 * and so does a phone that cannot share a file. When anything fails, the
 * link is followed in the old way, so a fault here never hides a PDF.
 *
 * A browser lets a page open the share sheet only for a short time after a
 * tap. When the download takes longer, a message says the file is ready,
 * with a Share button: that tap is a new one.
 */
(function (root) {
    'use strict';

    var PDF = 'application/pdf';

    // Can this device give a PDF to its share sheet? `touch`: a finger,
    // not a mouse. A computer can often share too, but it has a Downloads
    // folder, which is what the office wants.
    function canShare(nav, touch, FileClass) {
        if (!touch || !nav || typeof nav.share !== 'function' || typeof nav.canShare !== 'function') return false;
        try {
            return !!nav.canShare({ files: [new FileClass([''], 'a.pdf', { type: PDF })] });
        } catch (err) {
            return false;
        }
    }

    // What to do when the share sheet did not take the file:
    //   'done'  the person closed the sheet: nothing more to do
    //   'ask'   the browser wants a new tap
    //   'open'  anything else: follow the link in the old way
    function afterShareError(error) {
        var name = error && error.name;
        if (name === 'AbortError') return 'done';
        if (name === 'NotAllowedError') return 'ask';
        return 'open';
    }

    // Is this answer the PDF, and not the sign-in page or an error page?
    function isPdf(response) {
        var type = (response.headers && response.headers.get('Content-Type')) || '';
        return !!response.ok && !response.redirected && type.toLowerCase().indexOf(PDF) === 0;
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { canShare: canShare, afterShareError: afterShareError, isPdf: isPdf };
        return;
    }

    var doc = root.document;

    function touch() {
        return !!(root.matchMedia && root.matchMedia('(pointer: coarse)').matches);
    }

    function busy(link, on) {
        if (on) link.setAttribute('aria-busy', 'true'); else link.removeAttribute('aria-busy');
        link.classList.toggle('is-loading', on);
    }

    function share(data, name) {
        return root.navigator.share(data).catch(function (error) {
            var next = afterShareError(error);
            if (next === 'done') return;
            if (next === 'ask' && root.Yardway && root.Yardway.notice) {
                root.Yardway.notice('info', name + ' is ready.', {
                    label: 'Share',
                    run: function () { root.navigator.share(data).catch(function () { /* closed, or refused again */ }); }
                });
                return;
            }
            throw error;
        });
    }

    doc.addEventListener('click', function (event) {
        var link = event.target && event.target.closest ? event.target.closest('a[data-share-file]') : null;
        if (!link || event.defaultPrevented) return;
        if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if (!canShare(root.navigator, touch(), root.File)) return;
        event.preventDefault();
        if (link.getAttribute('aria-busy') === 'true') return;  // a second tap while the file comes
        var name = link.getAttribute('data-share-file');
        busy(link, true);
        root.fetch(link.href, { credentials: 'same-origin' }).then(function (response) {
            if (!isPdf(response)) throw new Error('not the PDF');
            return response.blob();
        }).then(function (blob) {
            var file = new root.File([blob], name, { type: PDF });
            return share({ files: [file], title: link.getAttribute('data-share-title') || name }, name);
        }).then(function () {
            busy(link, false);
        }, function () {
            busy(link, false);
            root.location.href = link.href;  // the old way
        });
    });
})(typeof window !== 'undefined' ? window : this);
