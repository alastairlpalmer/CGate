"""
Horse views — CRUD, move, arrive, depart, ownership.
"""

from datetime import timedelta
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from health.models import (
    BreedingRecord,
    FarrierVisit,
    MedicalCondition,
    Vaccination,
    VetVisit,
    WormEggCount,
    WormingTreatment,
)

from ..forms import (
    ArrivalForm,
    HorseForm,
    MoveHorseForm,
    OwnershipShareFormSet,
    SingleArrivalForm,
)
from ..mixins import StaffRequiredMixin, staff_required
from ..models import Horse, Location, Owner, OwnershipShare, Placement


def _warn_if_incomplete_ownership(request, formset):
    """Flash a warning if saved ownership shares total less than 100%."""
    total = sum(
        f.cleaned_data.get('share_percentage', 0) or 0
        for f in formset
        if f.cleaned_data and not f.cleaned_data.get('DELETE', False)
    )
    if 0 < total < 100:
        messages.warning(
            request,
            f"Total ownership is {total}% (less than 100%). "
            "This horse has unallocated ownership."
        )


class HorseListView(LoginRequiredMixin, ListView):
    model = Horse
    template_name = 'horses/horse_list.html'
    context_object_name = 'horses'

    @property
    def status(self):
        return self.request.GET.get('status', 'current')

    @property
    def is_searching(self):
        return bool(self.request.GET.get('search'))

    def get_paginate_by(self, queryset):
        # Only paginate departed tab (current tab shows all, grouped)
        if self.status == 'departed':
            return 25
        return None

    def get_queryset(self):
        active_placements = Prefetch(
            'placements',
            queryset=Placement.objects.filter(
                end_date__isnull=True
            ).select_related('owner', 'location'),
            to_attr='active_placements',
        )
        last_placements = Prefetch(
            'placements',
            queryset=Placement.objects.select_related(
                'owner', 'location'
            ).order_by('-end_date'),
            to_attr='last_placements',
        )

        search = self.request.GET.get('search', '').strip()

        # Search searches ALL horses (active + inactive)
        ownership_shares_prefetch = Prefetch(
            'ownership_shares',
            queryset=OwnershipShare.objects.select_related('owner'),
        )

        if search:
            queryset = Horse.objects.all().prefetch_related(
                active_placements, last_placements, ownership_shares_prefetch
            )
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(notes__icontains=search) |
                Q(placements__owner__name__icontains=search) |
                Q(placements__location__name__icontains=search)
            ).distinct()
        elif self.status == 'departed':
            # Departed: inactive OR active with no current placement (limbo)
            queryset = Horse.objects.filter(
                Q(is_active=False) |
                ~Q(placements__end_date__isnull=True)
            ).distinct().prefetch_related(
                last_placements, ownership_shares_prefetch,
            )
        else:
            # Current: active AND has an active placement
            queryset = Horse.objects.filter(
                is_active=True,
                placements__end_date__isnull=True,
            ).distinct().prefetch_related(
                active_placements, ownership_shares_prefetch,
            )

        # Advanced filters (location/owner dropdowns)
        location = self.request.GET.get('location')
        if location:
            queryset = queryset.filter(
                Exists(Placement.objects.filter(
                    horse=OuterRef('pk'),
                    location_id=location,
                    end_date__isnull=True,
                ))
            )

        owner = self.request.GET.get('owner')
        if owner:
            queryset = queryset.filter(
                Exists(Placement.objects.filter(
                    horse=OuterRef('pk'),
                    owner_id=owner,
                    end_date__isnull=True,
                ))
            )

        return queryset.order_by('name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status'] = self.status
        context['group_by'] = self.request.GET.get('group_by', 'location')
        context['locations'] = Location.objects.order_by('site', 'name')
        context['owners'] = Owner.objects.values('pk', 'name').order_by('name')
        context['is_searching'] = self.is_searching
        # Single query for both counts
        counts = Horse.objects.aggregate(
            total_current=Count(
                'pk', filter=Q(is_active=True, placements__end_date__isnull=True),
                distinct=True,
            ),
            total_departed=Count(
                'pk', filter=Q(is_active=False) | ~Q(placements__end_date__isnull=True),
                distinct=True,
            ),
        )
        context['total_current'] = counts['total_current']
        context['total_departed'] = counts['total_departed']

        # Helper: resolve owner - prefer OwnershipShare (canonical), fall back to placement
        def _get_owner(h):
            # 1. OwnershipShare is the canonical ownership record
            shares = getattr(h, 'ownership_shares_list', None)
            if shares is None:
                shares = list(h.ownership_shares.all())
                h.ownership_shares_list = shares
            if shares:
                primary = next((s for s in shares if s.is_primary_contact), None)
                return (primary or shares[0]).owner
            # 2. Fall back to active placement owner
            ap = getattr(h, 'active_placements', [])
            if ap and ap[0].owner:
                return ap[0].owner
            # 3. Fall back to last placement owner
            lp = getattr(h, 'last_placements', [])
            if lp and lp[0].owner:
                return lp[0].owner
            return None

        # Attach resolved owner to all horses for template use
        horses = list(context['horses'])
        for h in horses:
            h.resolved_owner = _get_owner(h)

        # Build grouped data for current tab (not when searching or departed)
        if self.status == 'current' and not self.is_searching:
            group_by = context['group_by']

            if group_by == 'owner':
                def key_fn(h):
                    o = h.resolved_owner
                    return (o.name if o else 'No Owner', o.pk if o else 0)
                horses.sort(key=lambda h: key_fn(h)[0])
                grouped = []
                for (name, pk), group in groupby(horses, key=key_fn):
                    group_list = list(group)
                    grouped.append({
                        'name': name,
                        'pk': pk,
                        'count': len(group_list),
                        'horses': group_list,
                    })
                context['grouped_horses'] = grouped
            else:
                # Group by location (default)
                def key_fn(h):
                    p = h.active_placements[0] if h.active_placements else None
                    return (
                        p.location.site if p and p.location else '',
                        p.location.name if p and p.location else 'No Location',
                        p.location.pk if p and p.location else 0,
                    )
                horses.sort(key=lambda h: key_fn(h))
                grouped = []
                for (site, loc_name, pk), group in groupby(horses, key=key_fn):
                    group_list = list(group)
                    grouped.append({
                        'site': site,
                        'name': loc_name,
                        'pk': pk,
                        'count': len(group_list),
                        'horses': group_list,
                    })
                context['grouped_horses'] = grouped

        return context


class HorseDetailView(LoginRequiredMixin, DetailView):
    model = Horse
    template_name = 'horses/horse_detail.html'
    context_object_name = 'horse'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        horse = self.object
        # Prefetch current placement once to avoid repeated DB hits in template
        context['current_placement'] = horse.placements.filter(
            end_date__isnull=True
        ).select_related('owner', 'location', 'rate_type').first()
        context['today'] = timezone.now().date()
        context['placements'] = horse.placements.select_related(
            'owner', 'location', 'rate_type'
        ).all()[:10]
        context['vaccinations'] = horse.vaccinations.select_related(
            'vaccination_type'
        ).all()[:10]
        context['farrier_visits'] = horse.farrier_visits.select_related(
            'service_provider'
        ).all()[:10]
        context['extra_charges'] = horse.extra_charges.select_related(
            'owner'
        ).all()[:10]
        context['ownership_shares'] = horse.ownership_shares.select_related('owner').all()
        # New sections
        context['worming_treatments'] = horse.worming_treatments.all()[:10]
        context['egg_counts'] = horse.worm_egg_counts.all()[:10]
        context['medical_conditions'] = horse.medical_conditions.all()
        context['vet_visits'] = horse.vet_visits.select_related('vet').all()[:10]
        # Breeding (mare only) — single query, filter active in Python
        if horse.is_mare:
            breeding_records = list(horse.breeding_records.select_related('foal').all())
            context['breeding_records'] = breeding_records
            context['active_pregnancy'] = next(
                (br for br in breeding_records if br.status in ('covered', 'confirmed')), None
            )
            context['foals'] = Horse.objects.filter(dam=horse).only(
                'pk', 'name', 'date_of_birth', 'sex', 'color'
            )
        else:
            context['foals'] = []

        # Build unified timeline
        timeline = []
        for p in context['placements']:
            timeline.append({'type': 'placement', 'date': p.start_date, 'obj': p})
        for v in context['vaccinations']:
            timeline.append({'type': 'vaccination', 'date': v.date_given, 'obj': v})
        for f in context['farrier_visits']:
            timeline.append({'type': 'farrier', 'date': f.date, 'obj': f})
        for w in context['worming_treatments']:
            timeline.append({'type': 'worming', 'date': w.date, 'obj': w})
        for ec in context['egg_counts']:
            timeline.append({'type': 'egg_count', 'date': ec.date, 'obj': ec})
        for v in context['vet_visits']:
            timeline.append({'type': 'vet_visit', 'date': v.date, 'obj': v})
        if horse.is_mare:
            for br in context.get('breeding_records', []):
                timeline.append({'type': 'breeding', 'date': br.date_covered, 'obj': br})
        timeline.sort(key=lambda e: e['date'], reverse=True)
        context['timeline_events'] = timeline

        return context


class HorseCreateView(StaffRequiredMixin, CreateView):
    model = Horse
    form_class = HorseForm
    template_name = 'horses/horse_form.html'
    success_url = reverse_lazy('horse_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if 'ownership_formset' not in context:
            if self.request.POST:
                context['ownership_formset'] = OwnershipShareFormSet(self.request.POST)
            else:
                context['ownership_formset'] = OwnershipShareFormSet()
        return context

    def form_valid(self, form):
        ownership_formset = OwnershipShareFormSet(self.request.POST)
        if not ownership_formset.is_valid():
            return self.render_to_response(
                self.get_context_data(form=form, ownership_formset=ownership_formset)
            )
        with transaction.atomic():
            self.object = form.save()
            ownership_formset.instance = self.object
            ownership_formset.save()
        _warn_if_incomplete_ownership(self.request, ownership_formset)
        messages.success(self.request, f"Horse '{self.object.name}' created successfully.")
        return redirect(self.get_success_url())


class HorseUpdateView(StaffRequiredMixin, UpdateView):
    model = Horse
    form_class = HorseForm
    template_name = 'horses/horse_form.html'

    def get_success_url(self):
        return reverse_lazy('horse_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if 'ownership_formset' not in context:
            if self.request.POST:
                context['ownership_formset'] = OwnershipShareFormSet(
                    self.request.POST, instance=self.object
                )
            else:
                context['ownership_formset'] = OwnershipShareFormSet(instance=self.object)
        return context

    def form_valid(self, form):
        ownership_formset = OwnershipShareFormSet(
            self.request.POST, instance=self.object
        )
        if not ownership_formset.is_valid():
            return self.render_to_response(
                self.get_context_data(form=form, ownership_formset=ownership_formset)
            )
        with transaction.atomic():
            self.object = form.save()
            ownership_formset.instance = self.object
            ownership_formset.save()
        _warn_if_incomplete_ownership(self.request, ownership_formset)
        messages.success(self.request, f"Horse '{self.object.name}' updated successfully.")
        return redirect(self.get_success_url())


@staff_required
def horse_move(request, pk):
    """Move a horse to a new location."""
    from ..services import PlacementService

    horse = get_object_or_404(Horse, pk=pk)
    current_placement = horse.current_placement

    if request.method == 'POST':
        form = MoveHorseForm(request.POST)
        if form.is_valid():
            try:
                PlacementService.move_horse(
                    horse,
                    new_location=form.cleaned_data['new_location'],
                    move_date=form.cleaned_data['move_date'],
                    new_owner=form.cleaned_data['new_owner'],
                    new_rate_type=form.cleaned_data['new_rate_type'],
                    expected_departure=form.cleaned_data.get('expected_departure'),
                    notes=form.cleaned_data['notes'],
                )
            except ValidationError as e:
                messages.error(request, str(e))
                return render(request, 'horses/horse_move.html', {
                    'horse': horse, 'form': form, 'current_placement': current_placement
                })

            messages.success(request, f"{horse.name} moved successfully.")
            return redirect('horse_detail', pk=horse.pk)
    else:
        form = MoveHorseForm(initial={
            'move_date': timezone.now().date()
        })

    return render(request, 'horses/horse_move.html', {
        'horse': horse,
        'form': form,
        'current_placement': current_placement
    })


@staff_required
def new_arrival(request):
    """Create a new horse and place it at a location in one step."""
    from ..forms import NewArrivalForm
    from ..services import PlacementService

    if request.method == 'POST':
        form = NewArrivalForm(request.POST)
        if form.is_valid():
            horse, placement = PlacementService.create_new_arrival(
                name=form.cleaned_data['name'],
                sex=form.cleaned_data.get('sex') or '',
                color=form.cleaned_data.get('color') or '',
                date_of_birth=form.cleaned_data.get('date_of_birth'),
                sire_name=form.cleaned_data.get('sire_name') or '',
                passport_number=form.cleaned_data.get('passport_number') or '',
                has_passport=form.cleaned_data.get('has_passport', False),
                owner=form.cleaned_data['owner'],
                location=form.cleaned_data['location'],
                rate_type=form.cleaned_data['rate_type'],
                arrival_date=form.cleaned_data['arrival_date'],
                expected_departure=form.cleaned_data.get('expected_departure'),
                notes=form.cleaned_data.get('notes', ''),
            )
            messages.success(request, f"{horse.name} created and arrived at {placement.location.name}.")
            return redirect('horse_detail', pk=horse.pk)
    else:
        initial = {'arrival_date': timezone.now().date()}
        location_id = request.GET.get('location')
        if location_id:
            initial['location'] = location_id
        form = NewArrivalForm(initial=initial)

    return render(request, 'horses/horse_new_arrival.html', {'form': form})


@staff_required
def horse_arrive(request, pk):
    """Log a single horse arriving at a location (from Horse Detail)."""
    from ..services import PlacementService

    horse = get_object_or_404(Horse, pk=pk)

    if request.method == 'POST':
        form = SingleArrivalForm(request.POST)
        if form.is_valid():
            try:
                placement = PlacementService.arrive_horse(
                    horse,
                    owner=form.cleaned_data['owner'],
                    location=form.cleaned_data['location'],
                    rate_type=form.cleaned_data['rate_type'],
                    arrival_date=form.cleaned_data['arrival_date'],
                    expected_departure=form.cleaned_data.get('expected_departure'),
                    notes=form.cleaned_data['notes'],
                )
                messages.success(request, f"{horse.name} arrived at {placement.location.name}.")
                return redirect('horse_detail', pk=horse.pk)
            except ValidationError as e:
                messages.error(request, str(e))
    else:
        initial = {'arrival_date': timezone.now().date()}
        primary_owner = horse.primary_owner
        if primary_owner:
            initial['owner'] = primary_owner.pk
        form = SingleArrivalForm(initial=initial)

    return render(request, 'horses/horse_arrive.html', {
        'horse': horse,
        'form': form,
    })


@staff_required
def horse_depart(request, pk):
    """Log a single horse departing (from Horse Detail, POST only)."""
    from ..services import PlacementService

    horse = get_object_or_404(Horse, pk=pk)

    if request.method == 'POST' and horse.current_placement:
        departure_date_str = request.POST.get('departure_date')
        if not departure_date_str:
            messages.error(request, "Departure date is required.")
            return redirect('horse_detail', pk=horse.pk)

        from datetime import date
        try:
            departure_date = date.fromisoformat(departure_date_str)
        except ValueError:
            messages.error(request, "Invalid date format.")
            return redirect('horse_detail', pk=horse.pk)

        try:
            placement = PlacementService.depart_horse(horse, departure_date)
            messages.success(request, f"{horse.name} departed from {placement.location.name}.")
        except ValidationError as e:
            messages.error(request, str(e))

    return redirect('horse_detail', pk=horse.pk)


@staff_required
def confirm_departure(request, pk):
    """Confirm a horse has departed and deactivate it (HTMX endpoint)."""
    from ..services import PlacementService

    horse = get_object_or_404(Horse, pk=pk)
    if request.method == 'POST':
        PlacementService.confirm_departure(horse)
        messages.success(request, f"{horse.name} confirmed as departed.")
    if request.headers.get('HX-Request'):
        return HttpResponse('')
    return redirect('dashboard')


@staff_required
def cancel_departure(request, pk):
    """Undo a pending departure - clear placement end_date (HTMX endpoint)."""
    from ..services import PlacementService

    horse = get_object_or_404(Horse, pk=pk)
    if request.method == 'POST':
        if PlacementService.cancel_departure(horse):
            messages.success(request, f"{horse.name} departure cancelled.")
    if request.headers.get('HX-Request'):
        return HttpResponse('')
    return redirect('dashboard')


@staff_required
def confirm_departures_bulk(request):
    """Confirm multiple horses as departed in one action (HTMX endpoint)."""
    from ..services import PlacementService

    if request.method == 'POST':
        horse_ids = request.POST.getlist('horse_ids')
        if horse_ids:
            count = PlacementService.confirm_departures_bulk(horse_ids)
            messages.success(request, f"{count} horse{'s' if count != 1 else ''} confirmed as departed.")
    if request.headers.get('HX-Request'):
        return HttpResponse('')
    return redirect('dashboard')


@staff_required
def manage_ownership_shares(request, pk):
    """Manage fractional ownership shares for a horse."""
    horse = get_object_or_404(Horse, pk=pk)

    if request.method == 'POST':
        formset = OwnershipShareFormSet(request.POST, instance=horse)
        if formset.is_valid():
            with transaction.atomic():
                formset.save()
            _warn_if_incomplete_ownership(request, formset)
            messages.success(request, f"Ownership shares for {horse.name} updated.")
            return redirect('horse_detail', pk=horse.pk)
    else:
        formset = OwnershipShareFormSet(instance=horse)

    return render(request, 'horses/horse_ownership.html', {
        'horse': horse,
        'formset': formset,
    })
