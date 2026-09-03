"""Helpers for views that can render inside the shared pop-up sheet
(templates/includes/_popup_sheet.html, static/js/popup.js)."""

from django.http import HttpResponse


def is_popup_request(request):
    """True when the pop-up sheet asked for this view.

    Boosted navigations also send ``HX-Request: true``, so the target is
    the discriminator: the sheet always loads into ``#popup-body``.
    """
    htmx = getattr(request, 'htmx', None)
    return bool(htmx) and htmx.target == 'popup-body'


def popup_saved_response():
    """The answer to a successful save from the sheet: nothing to swap, and
    a trigger that closes the sheet and refreshes the page beneath it.
    Django messages queued before this show on that refresh."""
    return HttpResponse(status=204, headers={'HX-Trigger': 'popup:saved'})
