"""The farrier page: who may open it, and the only queries it may run.

docs/FARRIER_PAGE_PLAN.md, sections 4 and 8. Two rules live here so the
views cannot get them wrong:

* **The gate.** ``provider_page`` lets a view run only for a signed-in
  user with Full on ``farrier_portal`` whose login is linked to an active
  ``ServiceProvider``. The provider is put on ``request.provider``. A user
  with the access but no linked provider sees a page that says so.
* **The scope.** Every query takes that provider. No view reads a
  provider id from the URL or a form, so one farrier can never reach
  another farrier's visits or prices: an id that is not theirs is a 404.
"""

from functools import wraps

from django.shortcuts import get_object_or_404, render

from billing.models import ServiceItem, ServiceProvider
from core.permissions import LEVEL_FULL, feature_required

from .models import ProviderVisit


def provider_page(view_func):
    """Gate a farrier page view; see the module docstring."""

    @wraps(view_func)
    @feature_required('farrier_portal', LEVEL_FULL)
    def _wrapped(request, *args, **kwargs):
        provider = ServiceProvider.for_user(request.user)
        if provider is None:
            return render(request, 'farrier/no_provider.html', status=403)
        request.provider = provider
        return view_func(request, *args, **kwargs)

    return _wrapped


# --- Queries, always scoped to one provider -------------------------------

def visits(provider):
    """This provider's visits, newest first."""
    return ProviderVisit.objects.filter(provider=provider)


def open_visit(provider):
    """This provider's open visit, or None."""
    return visits(provider).filter(status=ProviderVisit.Status.OPEN).first()


def get_visit(provider, pk):
    """One of this provider's visits, or 404 (also for another's id)."""
    return get_object_or_404(ProviderVisit, pk=pk, provider=provider)


def price_list(provider, active_only=True):
    """This provider's own prices, in display order."""
    items = ServiceItem.objects.for_provider(provider)
    if active_only:
        items = items.filter(is_active=True)
    return items.order_by('sort_order', 'name')


def get_price(provider, pk):
    """One of this provider's prices, or 404 (also for the yard's items)."""
    return get_object_or_404(ServiceItem, pk=pk, provider=provider)
