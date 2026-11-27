from .views import site_enabled


def marketing(request):
    """``marketing_site``: True when the public pages are on (the sign-in page links back to Home)."""
    return {'marketing_site': site_enabled()}
