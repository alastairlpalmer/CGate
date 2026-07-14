"""
Placement views — CRUD and list.
"""

from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from ..forms import PlacementForm
from ..permissions import LEVEL_VIEW, FeatureAccessMixin, feature_required
from ..models import Location, Owner, Placement


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


class PlacementCreateView(FeatureAccessMixin, CreateView):
    feature = 'locations'
    model = Placement
    form_class = PlacementForm
    template_name = 'placements/placement_form.html'

    def get_success_url(self):
        return reverse_lazy('location_list') + '?tab=history'


class PlacementUpdateView(FeatureAccessMixin, UpdateView):
    feature = 'locations'
    model = Placement
    form_class = PlacementForm
    template_name = 'placements/placement_form.html'

    def get_success_url(self):
        return reverse_lazy('location_list') + '?tab=history'
