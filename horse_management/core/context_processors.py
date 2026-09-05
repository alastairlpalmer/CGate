"""Template context for the location mapping feature."""

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
