"""
Views for core app.
"""

import logging
import time
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connection, transaction
from django.http import JsonResponse
from django.core.exceptions import ValidationError
from django.db.models import Count, Exists, OuterRef, Prefetch, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from billing.models import ExtraCharge
from health.models import (
    BreedingRecord,
    FarrierVisit,
    MedicalCondition,
    Vaccination,
    VetVisit,
    WormEggCount,
    WormingTreatment,
)

from .forms import (
    ArrivalForm, DepartureForm, HorseForm, LocationForm, MoveHorseForm,
    OwnerForm, OwnershipShareFormSet, PlacementForm, SingleArrivalForm,
)
from .models import Horse, Invoice, Location, Owner, OwnershipShare, Placement, RateType


def health_check(request):
    """Lightweight DB ping. No auth required. Used by Vercel cron to keep Supabase awake."""
    start = time.monotonic()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    db_ms = (time.monotonic() - start) * 1000
    return JsonResponse({
        "status": "ok",
        "db_ping_ms": round(db_ms, 1),
    })


logger = logging.getLogger(__name__)


@login_required
def app_settings(request):
    """Unified settings page for integrations, providers, business config."""
    from billing.models import ServiceProvider
    from health.models import VaccinationType
    from xero_integration.models import XeroConnection

    from .forms import BusinessSettingsForm
    from .models import BusinessSettings, RateType

    business = BusinessSettings.get_settings()
    if request.method == 'POST' and 'save_business' in request.POST:
        biz_form = BusinessSettingsForm(request.POST, instance=business)
        if biz_form.is_valid():
            biz_form.save()
            messages.success(request, "Business settings saved.")
            return redirect('app_settings')
    else:
        biz_form = BusinessSettingsForm(instance=business)

    return render(request, 'settings.html', {
        'xero_connection': XeroConnection.get_connection(),
        'providers': ServiceProvider.objects.filter(is_active=True).order_by('name'),
        'biz_form': biz_form,
        'rate_types': RateType.objects.all(),
        'vaccination_types': VaccinationType.objects.all(),
    })


@login_required
def rate_type_create(request):
    """Create a new rate type."""
    from .forms import RateTypeForm
    if request.method == 'POST':
        form = RateTypeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Rate type added.")
            return redirect('app_settings')
    else:
        form = RateTypeForm()
    return render(request, 'settings/rate_type_form.html', {'form': form})


@login_required
def rate_type_update(request, pk):
    """Edit a rate type."""
    from .forms import RateTypeForm
    from .models import RateType
    rate = get_object_or_404(RateType, pk=pk)
    if request.method == 'POST':
        form = RateTypeForm(request.POST, instance=rate)
        if form.is_valid():
            form.save()
            messages.success(request, "Rate type updated.")
            return redirect('app_settings')
    else:
        form = RateTypeForm(instance=rate)
    return render(request, 'settings/rate_type_form.html', {'form': form, 'object': rate})


@login_required
def dashboard(request):
    """Main dashboard view."""
    try:
        return _dashboard_inner(request)
    except Exception:
        logger.exception("Dashboard error")
        return render(request, 'error.html', {
            'error_title': 'Dashboard Error',
            'error_message': 'An unexpected error occurred loading the dashboard. Please try again or contact support.',
        }, status=500)


def _dashboard_inner(request):
    """Dashboard queries (health alerts loaded via HTMX)."""
    today = timezone.now().date()
    thirty_days = today + timedelta(days=30)
    two_weeks = today + timedelta(days=14)

    # Horse counts
    total_horses = Horse.objects.filter(is_active=True).count()
    horses_by_location = Location.objects.annotate(
        horse_count=Count(
            'placements',
            filter=Q(placements__end_date__isnull=True)
        )
    ).filter(horse_count__gt=0).order_by('-horse_count')

    # Owner summary — fall back to placement-based count if OwnershipShare
    # table hasn't been created yet (migration 0006).
    try:
        owners_with_horses = Owner.objects.annotate(
            horse_count=Count(
                'ownership_shares',
                filter=Q(ownership_shares__horse__is_active=True),
                distinct=True,
            )
        ).filter(horse_count__gt=0).order_by('-horse_count')[:10]
        # Force evaluation to trigger any DB error now
        list(owners_with_horses)
    except Exception:
        owners_with_horses = Owner.objects.annotate(
            horse_count=Count(
                'placements',
                filter=Q(placements__end_date__isnull=True),
                distinct=True,
            )
        ).filter(horse_count__gt=0).order_by('-horse_count')[:10]

    # Vaccinations due soon
    vaccinations_due = Vaccination.objects.filter(
        next_due_date__lte=thirty_days,
        next_due_date__gte=today,
        horse__is_active=True
    ).select_related('horse', 'vaccination_type').order_by('next_due_date')[:10]

    # Farrier due soon
    farrier_due = FarrierVisit.objects.filter(
        next_due_date__lte=two_weeks,
        next_due_date__gte=today,
        horse__is_active=True
    ).select_related('horse').order_by('next_due_date')[:10]

    # Outstanding invoices
    outstanding_invoices = Invoice.objects.filter(
        status__in=[Invoice.Status.SENT, Invoice.Status.OVERDUE]
    ).select_related('owner').order_by('due_date')[:10]

    # Unbilled charges
    unbilled_charges = ExtraCharge.objects.filter(
        invoiced=False
    ).select_related('horse', 'owner').order_by('-date')[:10]

    unbilled_total = ExtraCharge.objects.filter(invoiced=False).aggregate(
        total=Sum('amount')
    )['total'] or 0

    context = {
        'total_horses': total_horses,
        'horses_by_location': horses_by_location,
        'owners_with_horses': owners_with_horses,
        'vaccinations_due': vaccinations_due,
        'farrier_due': farrier_due,
        'outstanding_invoices': outstanding_invoices,
        'unbilled_charges': unbilled_charges,
        'unbilled_total': unbilled_total,
    }

    return render(request, 'dashboard.html', context)


@login_required
def dashboard_health_alerts(request):
    """HTMX partial: health alerts loaded after initial dashboard render."""
    today = timezone.now().date()
    thirty_days = today + timedelta(days=30)

    ehv_due = BreedingRecord.objects.filter(
        status='confirmed',
        mare__is_active=True,
    ).select_related('mare')[:10]

    high_egg_counts = WormEggCount.objects.filter(
        count__gt=200,
        horse__is_active=True,
    ).select_related('horse').order_by('-date')[:10]

    vet_follow_ups = VetVisit.objects.filter(
        follow_up_date__gte=today,
        follow_up_date__lte=thirty_days,
        horse__is_active=True,
    ).select_related('horse', 'vet').order_by('follow_up_date')[:10]

    context = {
        'ehv_due': ehv_due,
        'high_egg_counts': high_egg_counts,
        'vet_follow_ups': vet_follow_ups,
    }

    return render(request, 'partials/dashboard_health_alerts.html', context)


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


# Horse Views
class HorseListView(LoginRequiredMixin, ListView):
    model = Horse
    template_name = 'horses/horse_list.html'
    context_object_name = 'horses'
    paginate_by = 25

    def get_queryset(self):
        active_placements = Prefetch(
            'placements',
            queryset=Placement.objects.filter(
                end_date__isnull=True
            ).select_related('owner', 'location'),
            to_attr='active_placements',
        )
        queryset = Horse.objects.filter(is_active=True).prefetch_related(
            active_placements
        )

        # Search filter
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(notes__icontains=search)
            )

        # Location filter
        location = self.request.GET.get('location')
        if location:
            queryset = queryset.filter(
                Exists(Placement.objects.filter(
                    horse=OuterRef('pk'),
                    location_id=location,
                    end_date__isnull=True,
                ))
            )

        # Owner filter
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
        context['locations'] = Location.objects.values('pk', 'name')
        context['owners'] = Owner.objects.values('pk', 'name')
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
        ).all()[:5]
        context['farrier_visits'] = horse.farrier_visits.all()[:5]
        context['extra_charges'] = horse.extra_charges.select_related(
            'owner'
        ).all()[:10]
        context['ownership_shares'] = horse.ownership_shares.select_related('owner').all()
        # New sections
        context['worming_treatments'] = horse.worming_treatments.all()[:10]
        context['egg_counts'] = horse.worm_egg_counts.all()[:10]
        context['medical_conditions'] = horse.medical_conditions.all()
        context['vet_visits'] = horse.vet_visits.select_related('vet').all()[:10]
        # Breeding (mare only)
        if horse.is_mare:
            context['breeding_records'] = horse.breeding_records.select_related('foal').all()
            context['active_pregnancy'] = horse.breeding_records.filter(
                status__in=['covered', 'confirmed']
            ).first()
        # Foals via dam FK
        context['foals'] = Horse.objects.filter(dam=horse) if horse.is_mare else []
        return context


class HorseCreateView(LoginRequiredMixin, CreateView):
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


class HorseUpdateView(LoginRequiredMixin, UpdateView):
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


@login_required
def horse_move(request, pk):
    """Move a horse to a new location."""
    horse = get_object_or_404(Horse, pk=pk)
    current_placement = horse.current_placement

    if request.method == 'POST':
        form = MoveHorseForm(request.POST)
        if form.is_valid():
            move_date = form.cleaned_data['move_date']

            new_owner = form.cleaned_data['new_owner']
            new_rate_type = form.cleaned_data['new_rate_type']

            if not new_owner:
                new_owner = horse.primary_owner
            if not new_owner and current_placement:
                new_owner = current_placement.owner
            if not new_rate_type and current_placement:
                new_rate_type = current_placement.rate_type

            if not new_owner or not new_rate_type:
                messages.error(request, "Owner and rate type are required when the horse has no current placement.")
                return render(request, 'horses/horse_move.html', {
                    'horse': horse, 'form': form, 'current_placement': current_placement
                })

            # Validate move date isn't before current placement start
            if current_placement and move_date <= current_placement.start_date:
                messages.error(
                    request,
                    f"Move date must be after the current placement start date "
                    f"({current_placement.start_date})."
                )
                return render(request, 'horses/horse_move.html', {
                    'horse': horse, 'form': form, 'current_placement': current_placement
                })

            new_placement = Placement(
                horse=horse,
                owner=new_owner,
                location=form.cleaned_data['new_location'],
                rate_type=new_rate_type,
                start_date=move_date,
                notes=form.cleaned_data['notes']
            )
            try:
                new_placement.full_clean()
            except ValidationError as e:
                messages.error(request, str(e))
                return render(request, 'horses/horse_move.html', {
                    'horse': horse, 'form': form, 'current_placement': current_placement
                })

            with transaction.atomic():
                # End current placement and create new one atomically
                if current_placement:
                    current_placement.end_date = move_date - timedelta(days=1)
                    current_placement.save()
                new_placement.save()

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


# Owner Views
class OwnerListView(LoginRequiredMixin, ListView):
    model = Owner
    template_name = 'owners/owner_list.html'
    context_object_name = 'owners'

    def get_queryset(self):
        return Owner.objects.annotate(
            horse_count=Count(
                'ownership_shares',
                filter=Q(ownership_shares__horse__is_active=True),
                distinct=True,
            )
        ).order_by('name')


class OwnerDetailView(LoginRequiredMixin, DetailView):
    model = Owner
    template_name = 'owners/owner_detail.html'
    context_object_name = 'owner'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Optimized: prefetch active placements with location to avoid N+1
        active_placements = Prefetch(
            'placements',
            queryset=Placement.objects.filter(
                end_date__isnull=True
            ).select_related('location'),
            to_attr='active_placements',
        )
        # Get horses via ownership shares, annotate with share %
        shares = OwnershipShare.objects.filter(owner=self.object).select_related('horse')
        share_map = {s.horse_id: s.share_percentage for s in shares}

        horses = Horse.objects.filter(
            ownership_shares__owner=self.object,
            is_active=True,
        ).distinct().prefetch_related(active_placements)

        # Attach share_pct to each horse for template use
        for horse in horses:
            horse.share_pct = share_map.get(horse.pk)

        context['horses'] = horses
        context['invoices'] = self.object.invoices.all()[:10]
        context['extra_charges'] = self.object.extra_charges.filter(
            invoiced=False
        ).select_related('horse')
        return context


class OwnerCreateView(LoginRequiredMixin, CreateView):
    model = Owner
    form_class = OwnerForm
    template_name = 'owners/owner_form.html'
    success_url = reverse_lazy('owner_list')


class OwnerUpdateView(LoginRequiredMixin, UpdateView):
    model = Owner
    form_class = OwnerForm
    template_name = 'owners/owner_form.html'

    def get_success_url(self):
        return reverse_lazy('owner_detail', kwargs={'pk': self.object.pk})


# Location Views
class LocationListView(LoginRequiredMixin, ListView):
    model = Location
    template_name = 'locations/location_list.html'
    context_object_name = 'locations'

    def get_queryset(self):
        return Location.objects.annotate(
            horse_count=Count(
                'placements',
                filter=Q(placements__end_date__isnull=True)
            )
        ).order_by('site', 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['current_tab'] = self.request.GET.get('tab', 'locations')

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
            context['all_locations'] = Location.objects.all()
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

        return context


class LocationCreateView(LoginRequiredMixin, CreateView):
    model = Location
    form_class = LocationForm
    template_name = 'locations/location_form.html'
    success_url = reverse_lazy('location_list')


class LocationUpdateView(LoginRequiredMixin, UpdateView):
    model = Location
    form_class = LocationForm
    template_name = 'locations/location_form.html'

    def get_success_url(self):
        return reverse_lazy('location_detail', kwargs={'pk': self.object.pk})


# Placement Views
class PlacementListView(LoginRequiredMixin, ListView):
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
        context['locations'] = Location.objects.all()
        context['owners'] = Owner.objects.all()
        return context


class PlacementCreateView(LoginRequiredMixin, CreateView):
    model = Placement
    form_class = PlacementForm
    template_name = 'placements/placement_form.html'

    def get_success_url(self):
        return reverse_lazy('location_list') + '?tab=history'


class PlacementUpdateView(LoginRequiredMixin, UpdateView):
    model = Placement
    form_class = PlacementForm
    template_name = 'placements/placement_form.html'

    def get_success_url(self):
        return reverse_lazy('location_list') + '?tab=history'


# ── Arrival & Departure Views ──

@login_required
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
        form = ArrivalForm(request.POST)
        form.fields['horses'].queryset = available_horses
        if form.is_valid():
            horses = form.cleaned_data['horses']
            owner = form.cleaned_data['owner']
            rate_type = form.cleaned_data['rate_type']
            arrival_date = form.cleaned_data['arrival_date']
            notes = form.cleaned_data['notes']

            created = 0
            errors = []
            with transaction.atomic():
                for horse in horses:
                    placement = Placement(
                        horse=horse,
                        owner=owner,
                        location=location,
                        rate_type=rate_type,
                        start_date=arrival_date,
                        notes=notes,
                    )
                    try:
                        placement.full_clean()
                        placement.save()
                        created += 1
                    except ValidationError as e:
                        errors.append(f"{horse.name}: {e}")

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


@login_required
def log_departure(request, pk):
    """Log departure of selected horses from a location (POST only)."""
    location = get_object_or_404(Location, pk=pk)

    if request.method == 'POST':
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

        departed = 0
        with transaction.atomic():
            placements = Placement.objects.filter(
                horse_id__in=horse_ids,
                location=location,
                end_date__isnull=True,
            )
            for placement in placements:
                if departure_date < placement.start_date:
                    messages.error(
                        request,
                        f"{placement.horse.name}: departure date cannot be before arrival ({placement.start_date})."
                    )
                    continue
                placement.end_date = departure_date
                if notes:
                    placement.notes = (placement.notes or '') + f"\nDeparted: {notes}" if placement.notes else notes
                placement.save()
                departed += 1

        if departed:
            messages.success(
                request,
                f"{departed} horse{'s' if departed != 1 else ''} departed from {location.name}."
            )
        return redirect('location_detail', pk=location.pk)

    return redirect('location_detail', pk=location.pk)


@login_required
def horse_arrive(request, pk):
    """Log a single horse arriving at a location (from Horse Detail)."""
    horse = get_object_or_404(Horse, pk=pk)

    if request.method == 'POST':
        form = SingleArrivalForm(request.POST)
        if form.is_valid():
            placement = Placement(
                horse=horse,
                owner=form.cleaned_data['owner'],
                location=form.cleaned_data['location'],
                rate_type=form.cleaned_data['rate_type'],
                start_date=form.cleaned_data['arrival_date'],
                notes=form.cleaned_data['notes'],
            )
            try:
                placement.full_clean()
                placement.save()
                messages.success(request, f"{horse.name} arrived at {placement.location.name}.")
                return redirect('horse_detail', pk=horse.pk)
            except ValidationError as e:
                messages.error(request, str(e))
    else:
        initial = {'arrival_date': timezone.now().date()}
        # Pre-fill owner from horse's primary owner
        primary_owner = horse.primary_owner
        if primary_owner:
            initial['owner'] = primary_owner.pk
        form = SingleArrivalForm(initial=initial)

    return render(request, 'horses/horse_arrive.html', {
        'horse': horse,
        'form': form,
    })


@login_required
def horse_depart(request, pk):
    """Log a single horse departing (from Horse Detail, POST only)."""
    horse = get_object_or_404(Horse, pk=pk)
    current_placement = horse.current_placement

    if request.method == 'POST' and current_placement:
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

        if departure_date < current_placement.start_date:
            messages.error(
                request,
                f"Departure date cannot be before arrival ({current_placement.start_date})."
            )
            return redirect('horse_detail', pk=horse.pk)

        current_placement.end_date = departure_date
        current_placement.save()
        messages.success(request, f"{horse.name} departed from {current_placement.location.name}.")

    return redirect('horse_detail', pk=horse.pk)


@login_required
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

