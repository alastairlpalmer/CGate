"""The public marketing pages, for visitors who are not signed in.

``MARKETING_SITE_ENABLED`` turns the site on. While it is off, ``/``
works as it did before (the dashboard, or the sign-in page for a
visitor) and the other marketing pages are "not found". This lets the
site be built in steps and turned on when the words are ready.
"""

from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET


def site_enabled():
    return bool(getattr(settings, 'MARKETING_SITE_ENABLED', False))


def _wants_the_app(request):
    """True when ``/`` must be the dashboard (or its sign-in redirect).

    Only a plain page load by a visitor gets the Home page. A request
    from inside the app (htmx, a background poll), a form post, or the
    installed phone app (its start address is ``/?source=app``) keeps
    the old behaviour: a signed-out user goes to the sign-in page.
    """
    return (
        request.user.is_authenticated
        or not site_enabled()
        or request.method not in ('GET', 'HEAD')
        or request.headers.get('HX-Request')
        or request.headers.get('X-Background')
        or request.GET.get('source') == 'app'
    )


def root(request):
    """``/``: the dashboard for a signed-in user, Home for a visitor."""
    if _wants_the_app(request):
        from core.views import dashboard

        response = dashboard(request)
    else:
        response = render(request, 'marketing/home.html', {'page': 'home'})
    # The same address gives two pages, chosen by the session cookie.
    patch_vary_headers(response, ('Cookie',))
    return response


def _page(template, name):
    @require_GET
    def view(request):
        if not site_enabled():
            raise Http404
        return render(request, template, {'page': name})
    view.__name__ = name
    return view


features = _page('marketing/features.html', 'features')
pricing = _page('marketing/pricing.html', 'pricing')
about = _page('marketing/about.html', 'about')
contact = _page('marketing/contact.html', 'contact')
