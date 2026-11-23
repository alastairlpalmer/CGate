"""Template context: feature switches that every template can read."""

from django.conf import settings


def location_maps(request):
    """``location_maps_enabled`` + the chip's radius, for every template.

    The flag gates everything user-visible about maps (the chip, the Map
    tab, the Near you card). The coordinate picker on the edit form is not
    gated, so data can be entered before the feature is switched on.
    """
    return {
        'location_maps_enabled': settings.LOCATION_MAPS_ENABLED,
        'location_near_radius_m': settings.LOCATION_NEAR_RADIUS_M,
    }


def barns(request):
    """``barns_enabled``: whether to offer barns and stables."""
    return {'barns_enabled': settings.BARNS_ENABLED}


def training(request):
    """``training_enabled``: whether to show Training in the menus."""
    return {'training_enabled': settings.TRAINING_ENABLED}


def page_frame(request):
    """``slim_page``: make the page without its frame.

    True for a tap on a link or a form that htmx sends itself (hx-boost):
    htmx then uses only the title, the messages and ``#main-content`` of
    the answer, and the browser already has the rest (base.html). A page
    load, a Back that htmx fetches again, and a part of a page all get the
    whole page, as before.
    """
    return {'slim_page': request.headers.get('HX-Boosted') == 'true'}


def google_sign_in(request):
    """``google_sign_in_enabled``: whether to show the Google buttons."""
    return {'google_sign_in_enabled': settings.GOOGLE_SIGN_IN_ENABLED}
