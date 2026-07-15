"""
Placement views — CRUD and list.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import CreateView, ListView, UpdateView

from ..forms import PlacementForm
from ..permissions import LEVEL_VIEW, FeatureAccessMixin, feature_required
from ..models import Location, Owner, Placement


def _safe_next_url(request):
    """Validated ?next= target so edits can return to the page they came
    from (e.g. a horse's timeline) without becoming an open redirect."""
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return None


class PlacementListView(FeatureAccessMixin, ListView):
    feature = 'locations'
    access_level = LEVEL_VIEW
    model = Placement
    template_name = 'placements/placement_list.html'
    context_object_name = 'placements'
    paginate_by = 50

    def get_queryset(self):
        queryset = Placement.objects.select_related(
            'horse', 'owner', 'location', 'rate_type'
        )

        # Status filter
        status = self.request.GET.get('status', 'active')
        if status == 'active':
            queryset = queryset.filter(end_date__isnull=True)
        elif status == 'ended':
            queryset = queryset.filter(end_date__isnull=False)
        # 'all' = no end_date filter

        # Location filter
        location = self.request.GET.get('location')
        if location:
            queryset = queryset.filter(location_id=location)

        # Owner filter
        owner = self.request.GET.get('owner')
        if owner:
            queryset = queryset.filter(owner_id=owner)

        return queryset.order_by('-start_date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['current_status'] = self.request.GET.get('status', 'active')
        context['locations'] = Location.objects.order_by('site', 'name')
        context['owners'] = Owner.objects.all()
        return context


class _PlacementFormViewMixin:
    """Shared next-URL handling: the validated target is used for both the
    post-save redirect and the template's Cancel link, so an off-site or
    javascript: ?next= can never end up in a rendered href."""

    def get_success_url(self):
        return _safe_next_url(self.request) or (
            reverse('location_list') + '?tab=history'
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['back_url'] = (
            _safe_next_url(self.request) or reverse('placement_list')
        )
        return context


class PlacementCreateView(_PlacementFormViewMixin, FeatureAccessMixin, CreateView):
    feature = 'locations'
    model = Placement
    form_class = PlacementForm
    template_name = 'placements/placement_form.html'


class PlacementUpdateView(_PlacementFormViewMixin, FeatureAccessMixin, UpdateView):
    feature = 'locations'
    model = Placement
    form_class = PlacementForm
    template_name = 'placements/placement_form.html'


@feature_required('locations')
def placement_delete(request, pk):
    """Delete a placement outright (POST only).

    For rows that should never have existed — e.g. a stay created by a
    mis-click — where editing the dates can't express "this never happened".
    Removing the stay also removes its days from billing.
    """
    placement = get_object_or_404(
        Placement.objects.select_related('horse', 'location'), pk=pk
    )
    horse = placement.horse
    if request.method == 'POST':
        location_name = placement.location.name
        placement.delete()
        messages.success(
            request,
            f"Placement of {horse.name} at {location_name} "
            f"({placement.start_date} – {placement.end_date or 'present'}) deleted."
        )
    return redirect(
        _safe_next_url(request) or reverse('horse_detail', args=[horse.pk])
    )
