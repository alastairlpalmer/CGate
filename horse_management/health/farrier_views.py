"""Views for the farrier page (docs/FARRIER_PAGE_PLAN.md, section 5).

Step 2 builds the gate and the scoped pages in their first, read-only
form. Recording work (step 3), Finish visit (step 4) and editing prices
(step 5) come next. Every view here is wrapped in ``provider_page`` and
reads data only through the scoped helpers in ``health.farrier``.
"""

from django.shortcuts import render

from . import farrier
from .farrier import provider_page


@provider_page
def today(request):
    """Today's visit: the farrier's main screen."""
    provider = request.provider
    return render(request, 'farrier/today.html', {
        'provider': provider,
        'visit': farrier.open_visit(provider),
        'prices': farrier.price_list(provider),
    })


@provider_page
def visit_list(request):
    """My visits: every visit this farrier has recorded."""
    provider = request.provider
    return render(request, 'farrier/visit_list.html', {
        'provider': provider,
        'visits': farrier.visits(provider).prefetch_related('farrier_visits'),
    })


@provider_page
def visit_detail(request, pk):
    """One visit, its lines and its total."""
    provider = request.provider
    visit = farrier.get_visit(provider, pk)
    return render(request, 'farrier/visit_detail.html', {
        'provider': provider,
        'visit': visit,
        'lines': visit.farrier_visits.select_related('horse').order_by('horse__name'),
    })


@provider_page
def price_list(request):
    """My prices: this farrier's own price list."""
    provider = request.provider
    return render(request, 'farrier/price_list.html', {
        'provider': provider,
        'prices': farrier.price_list(provider, active_only=False),
    })
