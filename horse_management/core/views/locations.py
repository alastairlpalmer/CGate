"""
Location views — CRUD, detail tabs, arrival/departure logging.
"""

from itertools import groupby

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from ..forms import ArrivalForm, LocationForm
from ..mixins import StaffRequiredMixin, staff_required
from ..models import Horse, Location, Owner, Placement


class LocationListView(LoginRequiredMixin, ListView):
    model = Location
    template_name = 'locations/location_list.html'
    context_object_name = 'locations'

    def get_queryset(self):
        return Location.objects.annotate(
            horse_count=Count(
                'placements__horse',
                filter=Q(
                    placements__end_date__isnull=True,
                    placements__horse__is_active=True,
                ),
                distinct=True,
            )
        ).order_by('site', 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['current_tab'] = self.request.GET.get('tab', 'locations')

        if context['current_tab'] != 'history':
            # Group locations by site for card display
            grouped = []
            for site, locs in groupby(context['locations'], key=lambda l: l.site):
                site_locs = list(locs)
                site_horse_count = sum(l.horse_count for l in site_locs)
                grouped.append((site, site_locs, site_horse_count))
            context['grouped_locations'] = grouped

        # Movement History tab data
        if context['current_tab'] == 'history':
            placements = Placement.objects.select_related(
                'horse', 'owner', 'location', 'rate_type'
            )
            status = self.request.GET.get('status', 'active')
            if status == 'active':
                placements = placements.filter(end_date__isnull=True)
            elif status == 'ended':
                placements = placements.filter(end_date__isnull=False)
            location_filter = self.request.GET.get('location')
            if location_filter:
                placements = placements.filter(location_id=location_filter)
            owner_filter = self.request.GET.get('owner')
            if owner_filter:
                placements = placements.filter(owner_id=owner_filter)
            context['placements'] = placements.order_by('-start_date')[:50]
            context['current_status'] = status
            context['all_locations'] = Location.objects.order_by('site', 'name')
            context['owners'] = Owner.objects.all()

        return context


class LocationDetailView(LoginRequiredMixin, DetailView):
    model = Location
    template_name = 'locations/location_detail.html'
    context_object_name = 'location'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['current_tab'] = self.request.GET.get('tab', 'current')
        context['today'] = timezone.now().date()

        # Current horses (always needed for the info card counts)
        active_placements = Prefetch(
            'placements',
            queryset=Placement.objects.filter(
                end_date__isnull=True
            ).select_related('owner'),
            to_attr='active_placements',
        )
        context['horses'] = Horse.objects.filter(
            placements__location=self.object,
            placements__end_date__isnull=True
        ).distinct().prefetch_related(active_placements)

        # History tab data
        if context['current_tab'] == 'history':
            history = Placement.objects.filter(
                location=self.object
            ).select_related('horse', 'owner', 'rate_type')
            status = self.request.GET.get('status', 'all')
            if status == 'active':
                history = history.filter(end_date__isnull=True)
            elif status == 'ended':
                history = history.filter(end_date__isnull=False)
            context['history_placements'] = history.order_by('-start_date')[:50]
            context['current_status'] = status

        # Feed history tab data
        if context['current_tab'] == 'feed':
            from billing.models import FeedOut
            context['feed_outs'] = FeedOut.objects.filter(
                location=self.object
            ).select_related('yard_cost').order_by('-date')[:50]

        return context


class LocationCreateView(StaffRequiredMixin, CreateView):
    model = Location
    form_class = LocationForm
    template_name = 'locations/location_form.html'
    success_url = reverse_lazy('location_list')


class LocationUpdateView(StaffRequiredMixin, UpdateView):
    model = Location
    form_class = LocationForm
    template_name = 'locations/location_form.html'

    def get_success_url(self):
        return reverse_lazy('location_detail', kwargs={'pk': self.object.pk})


@staff_required
def log_arrival(request, pk):
    """Log one or more horses arriving at a location."""
    location = get_object_or_404(Location, pk=pk)

    # Horses without an active placement (available to arrive)
    horses_with_active = Placement.objects.filter(
        horse=OuterRef('pk'), end_date__isnull=True
    )
    available_horses = Horse.objects.filter(
        is_active=True
    ).exclude(
        Exists(horses_with_active)
    ).order_by('name')

    if request.method == 'POST':
        from ..services import PlacementService

        form = ArrivalForm(request.POST)
        form.fields['horses'].queryset = available_horses
        if form.is_valid():
            created, errors = PlacementService.bulk_arrive(
                form.cleaned_data['horses'],
                owner=form.cleaned_data['owner'],
                location=location,
                rate_type=form.cleaned_data['rate_type'],
                arrival_date=form.cleaned_data['arrival_date'],
                expected_departure=form.cleaned_data.get('expected_departure'),
                notes=form.cleaned_data['notes'],
            )
            if created:
                messages.success(
                    request,
                    f"{created} horse{'s' if created != 1 else ''} arrived at {location.name}."
                )
            for err in errors:
                messages.error(request, err)
            return redirect('location_detail', pk=location.pk)
    else:
        form = ArrivalForm(initial={'arrival_date': timezone.now().date()})
        form.fields['horses'].queryset = available_horses

    return render(request, 'locations/location_arrive.html', {
        'location': location,
        'form': form,
    })


@staff_required
def log_departure(request, pk):
    """Log departure of selected horses from a location (POST only)."""
    location = get_object_or_404(Location, pk=pk)

    if request.method == 'POST':
        from ..services import PlacementService

        horse_ids = request.POST.getlist('horse_ids')
        departure_date_str = request.POST.get('departure_date')
        notes = request.POST.get('notes', '')

        if not horse_ids:
            messages.error(request, "No horses selected.")
            return redirect('location_detail', pk=location.pk)

        if not departure_date_str:
            messages.error(request, "Departure date is required.")
            return redirect('location_detail', pk=location.pk)

        from datetime import date
        try:
            departure_date = date.fromisoformat(departure_date_str)
        except ValueError:
            messages.error(request, "Invalid date format.")
            return redirect('location_detail', pk=location.pk)

        departed, depart_errors = PlacementService.bulk_depart(
            horse_ids, location, departure_date, notes
        )
        for err in depart_errors:
            messages.error(request, err)
        if departed:
            messages.success(
                request,
                f"{departed} horse{'s' if departed != 1 else ''} departed from {location.name}."
            )
        return redirect('location_detail', pk=location.pk)

    return redirect('location_detail', pk=location.pk)
