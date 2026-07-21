"""
Location views — CRUD, detail tabs, arrival/departure logging.
"""

import calendar
from datetime import date, timedelta
from itertools import groupby

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Exists, Min, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from ..forms import ArrivalForm, LocationForm, LocationUsageForm
from ..permissions import LEVEL_VIEW, FeatureAccessMixin, feature_required
from ..models import Horse, Location, LocationUsagePeriod, Owner, Placement

# Chart/legend colour per usage type — reuses the established design palette.
USAGE_COLORS = {
    Location.Usage.HORSES: '#1B3A2D',   # forest
    Location.Usage.MIXED: '#A0522D',    # saddle
    Location.Usage.RESTED: '#6B8F71',   # sage
    Location.Usage.HAY: '#2E86AB',      # info-blue
    Location.Usage.OTHER: '#B8CBB9',    # sage-200
}


def usage_days_for_period(location, period_start, period_end):
    """Compute usage day-counts and timeline segments for a location in a period.

    Returns (totals, segments) where ``totals`` maps each usage value to its
    inclusive day count within the period, and ``segments`` is a date-ordered
    list of dicts (usage, label, start, end, days) for the timeline view.
    """
    periods = location.usage_periods.filter(
        start_date__lte=period_end,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=period_start)
    ).order_by('start_date')

    totals = {choice.value: 0 for choice in Location.Usage}
    segments = []
    for p in periods:
        days = p.get_days_in_period(period_start, period_end)
        if days <= 0:
            continue
        totals[p.usage] = totals.get(p.usage, 0) + days
        eff_start, eff_end = p.get_effective_dates_in_period(period_start, period_end)
        segments.append({
            'usage': p.usage,
            'label': p.get_usage_display(),
            'start': eff_start.isoformat(),
            'end': eff_end.isoformat(),
            'days': days,
            'source': p.source,
        })
    return totals, segments


def usage_days_for_year(location, year):
    """Backwards-compatible wrapper computing usage for a whole calendar year."""
    return usage_days_for_period(location, date(year, 1, 1), date(year, 12, 31))


def _months_ago(d, n):
    """Return the date ``n`` whole months before ``d`` (clamping the day).

    Dependency-free month arithmetic (avoids requiring python-dateutil).
    """
    month_index = (d.year * 12 + (d.month - 1)) - n
    year, month = divmod(month_index, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def _usage_year_choices(earliest_year):
    """Year selector range from the earliest recorded period to this year."""
    this_year = timezone.localdate().year
    if not earliest_year or earliest_year > this_year:
        earliest_year = this_year
    return list(range(this_year, earliest_year - 1, -1))


def _resolve_usage_window(request):
    """Resolve the selected Usage analytics window from query params.

    Returns a dict: range ('3mo'|'6mo'|'year'), year, start, end (dates),
    label, days (inclusive day count), is_year.
    """
    today = timezone.localdate()
    range_key = request.GET.get('range', 'year')
    if range_key not in ('3mo', '6mo', 'year'):
        range_key = 'year'

    if range_key in ('3mo', '6mo'):
        months = 3 if range_key == '3mo' else 6
        end = today
        start = _months_ago(today, months) + timedelta(days=1)
        label = f"last {months} months"
        return {
            'range': range_key, 'year': today.year,
            'start': start, 'end': end, 'label': label,
            'days': (end - start).days + 1, 'is_year': False,
        }

    try:
        year = int(request.GET.get('year', today.year))
    except (TypeError, ValueError):
        year = today.year
    start, end = date(year, 1, 1), date(year, 12, 31)
    return {
        'range': 'year', 'year': year,
        'start': start, 'end': end, 'label': str(year),
        'days': 366 if calendar.isleap(year) else 365, 'is_year': True,
    }


class LocationListView(FeatureAccessMixin, ListView):
    feature = 'locations'
    access_level = LEVEL_VIEW
    model = Location
    template_name = 'locations/location_list.html'
    context_object_name = 'locations'

    def get_queryset(self):
        queryset = Location.objects.annotate(
            horse_count=Count(
                'placements__horse',
                filter=Q(
                    placements__end_date__isnull=True,
                    placements__horse__is_active=True,
                ),
                distinct=True,
            )
        )
        if self.request.GET.get('tab', 'locations') != 'history':
            search = self.request.GET.get('search', '').strip()
            if search:
                queryset = queryset.filter(
                    Q(name__icontains=search) | Q(site__icontains=search)
                )
        return queryset.order_by('site', 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['current_tab'] = self.request.GET.get('tab', 'locations')

        if context['current_tab'] not in ('history', 'usage'):
            # Group locations by site for card display
            grouped = []
            for site, locs in groupby(context['locations'], key=lambda l: l.site):
                site_locs = list(locs)
                site_horse_count = sum(l.horse_count for l in site_locs)
                grouped.append((site, site_locs, site_horse_count))
                # Prime the cached properties from the annotation so the
                # template's availability ring doesn't run one COUNT query
                # per card — and so the ring and the n/capacity number can't
                # disagree (the property counted stranded inactive horses,
                # the annotation doesn't).
                for loc in site_locs:
                    loc.__dict__['current_horse_count'] = loc.horse_count
                    loc.__dict__['availability'] = (
                        loc.capacity - loc.horse_count
                        if loc.capacity is not None else None
                    )
            context['grouped_locations'] = grouped

        # Usage analytics overview tab
        if context['current_tab'] == 'usage':
            window = _resolve_usage_window(self.request)

            usage_meta = [
                {'value': v, 'label': label, 'color': USAGE_COLORS.get(v, '#6B8F71')}
                for v, label in Location.Usage.choices
            ]
            label_for = {v: label for v, label in Location.Usage.choices}
            overview = []
            for site, locs in groupby(context['locations'], key=lambda l: l.site):
                rows = []
                for loc in locs:
                    totals, _ = usage_days_for_period(loc, window['start'], window['end'])
                    total = sum(totals.values())
                    # Compact, mobile-friendly: only the non-zero usages, each
                    # with its share of the field's tracked days for the bar.
                    segments = [
                        {
                            'value': v,
                            'label': label_for[v],
                            'color': USAGE_COLORS.get(v, '#6B8F71'),
                            'days': totals[v],
                            'pct': round(totals[v] / total * 100, 1) if total else 0,
                        }
                        for v in totals if totals[v]
                    ]
                    rows.append({
                        'location': loc,
                        'segments': segments,
                        'total': total,
                    })
                overview.append((site, rows))

            earliest = LocationUsagePeriod.objects.aggregate(
                first=Min('start_date')
            )['first']
            context['usage_range'] = window['range']
            context['usage_year'] = window['year']
            context['usage_period_label'] = window['label']
            context['usage_meta'] = usage_meta
            context['usage_overview'] = overview
            context['usage_year_choices'] = _usage_year_choices(
                earliest.year if earliest else None
            )

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
            context['owners'] = Owner.objects.only('pk', 'name')

        return context


class LocationDetailView(FeatureAccessMixin, DetailView):
    feature = 'locations'
    access_level = LEVEL_VIEW
    model = Location
    template_name = 'locations/location_detail.html'
    context_object_name = 'location'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['current_tab'] = self.request.GET.get('tab', 'current')
        context['today'] = timezone.localdate()

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

        # Usage analytics tab data
        if context['current_tab'] == 'usage':
            window = _resolve_usage_window(self.request)

            totals, segments = usage_days_for_period(
                self.object, window['start'], window['end']
            )

            usage_labels = dict(Location.Usage.choices)
            summary = [
                {
                    'value': value,
                    'label': usage_labels[value],
                    'color': USAGE_COLORS.get(value, '#6B8F71'),
                    'days': days,
                    'pct': round(days / window['days'] * 100, 1) if days else 0,
                }
                for value, days in totals.items()
            ]

            context['usage_range'] = window['range']
            context['usage_year'] = window['year']
            context['usage_period_label'] = window['label']
            context['usage_summary'] = summary
            context['usage_total_days'] = sum(totals.values())
            context['usage_chart_data'] = {
                'labels': [row['label'] for row in summary],
                'days': [row['days'] for row in summary],
                'colors': [row['color'] for row in summary],
                'segments': [
                    {**seg, 'color': USAGE_COLORS.get(seg['usage'], '#6B8F71')}
                    for seg in segments
                ],
            }
            earliest = self.object.usage_periods.aggregate(
                first=Min('start_date')
            )['first']
            year_choices = _usage_year_choices(
                earliest.year if earliest else None
            )
            context['usage_year_choices'] = year_choices

            # Multi-year comparison only makes sense in Year mode: up to the 5
            # most recent recorded years, chronological. Compute each year's
            # totals once, then shape a per-year table and a stacked-bar dataset.
            if window['is_year']:
                compare_years = sorted(year_choices)[-5:]
                year_totals = {cy: usage_days_for_year(self.object, cy)[0] for cy in compare_years}
                context['usage_compare_rows'] = [
                    {
                        'year': cy,
                        'days': [year_totals[cy].get(value, 0) for value, _ in Location.Usage.choices],
                        'total': sum(year_totals[cy].values()),
                    }
                    for cy in compare_years
                ]
                context['usage_compare_data'] = {
                    'years': compare_years,
                    'datasets': [
                        {
                            'label': label,
                            'color': USAGE_COLORS.get(value, '#6B8F71'),
                            'days': [year_totals[cy].get(value, 0) for cy in compare_years],
                        }
                        for value, label in Location.Usage.choices
                    ],
                }
            context['usage_periods'] = self.object.usage_periods.order_by(
                '-start_date'
            )[:50]
            context['usage_form'] = LocationUsageForm(initial={
                'usage': self.object.usage,
                'change_date': context['today'],
            })

        return context


class LocationCreateView(FeatureAccessMixin, CreateView):
    feature = 'locations'
    model = Location
    form_class = LocationForm
    template_name = 'locations/location_form.html'
    success_url = reverse_lazy('location_list')


class LocationUpdateView(FeatureAccessMixin, UpdateView):
    feature = 'locations'
    model = Location
    form_class = LocationForm
    template_name = 'locations/location_form.html'

    def get_success_url(self):
        return reverse_lazy('location_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        from ..services import LocationUsageService

        # Detect a usage change against the stored value before saving.
        old_usage = Location.objects.filter(pk=self.object.pk).values_list(
            'usage', flat=True
        ).first()
        new_usage = form.cleaned_data.get('usage')

        # Let the form persist the other fields, but keep the existing usage
        # so the service stays the single writer of usage + history.
        if old_usage is not None and new_usage != old_usage:
            form.instance.usage = old_usage
        response = super().form_valid(form)

        if old_usage is not None and new_usage != old_usage:
            try:
                LocationUsageService.set_usage(
                    self.object,
                    usage=new_usage,
                    change_date=timezone.localdate(),
                    source=LocationUsagePeriod.Source.MANUAL,
                    notes='Changed via location edit form.',
                )
            except ValidationError as e:
                # e.g. the current usage period also started today (horses
                # arrived onto an empty field this morning). The other
                # fields saved fine — surface why the usage didn't change
                # instead of 500ing on a half-applied edit.
                messages.warning(
                    self.request,
                    "Location saved, but the usage wasn't changed: "
                    + '; '.join(e.messages),
                )
        return response


@feature_required('locations')
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
        form = ArrivalForm(initial={'arrival_date': timezone.localdate()})
        form.fields['horses'].queryset = available_horses

    return render(request, 'locations/location_arrive.html', {
        'location': location,
        'form': form,
    })


@feature_required('locations')
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


@feature_required('locations')
def set_location_usage(request, pk):
    """Record a manual change to a field's usage, optionally backdated."""
    location = get_object_or_404(Location, pk=pk)

    if request.method == 'POST':
        from ..services import LocationUsageService

        form = LocationUsageForm(request.POST)
        if form.is_valid():
            try:
                period = LocationUsageService.set_usage(
                    location,
                    usage=form.cleaned_data['usage'],
                    change_date=form.cleaned_data['change_date'],
                    source=LocationUsagePeriod.Source.MANUAL,
                    notes=form.cleaned_data.get('notes', ''),
                )
            except ValidationError as e:
                messages.error(request, '; '.join(e.messages))
            else:
                if period is None:
                    messages.info(
                        request,
                        f"{location.name} is already set to "
                        f"{location.get_usage_display()}."
                    )
                else:
                    messages.success(
                        request,
                        f"{location.name} usage set to {period.get_usage_display()} "
                        f"from {period.start_date:%-d %b %Y}."
                    )
        else:
            for errors in form.errors.values():
                for err in errors:
                    messages.error(request, err)

    return redirect(f"{reverse_lazy('location_detail', kwargs={'pk': location.pk})}?tab=usage")
