"""
Horse views — CRUD, move, arrive, depart, ownership.
"""

from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import (
    Case,
    Count,
    Exists,
    ExpressionWrapper,
    F,
    IntegerField,
    Max,
    OuterRef,
    Prefetch,
    Q,
    Value,
    When,
)
from django.db.models.functions import ExtractYear
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
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
    QuickHorseForm,
    MoveHorseForm,
    OwnershipShareFormSet,
    SingleArrivalForm,
)
from ..permissions import LEVEL_VIEW, FeatureAccessMixin, feature_required
from ..models import Horse, Location, Owner, OwnershipShare, Placement
from ..search import fuzzy_horse_ids
from ._popup import is_popup_request, popup_saved_response


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


# ── Horse list: grouping and sorting ─────────────────────────────────────
# "all" is a flat list of every horse on the property; "location" and
# "owner" put the same horses into group cards. Each grouping has its own
# sort menu, so an option that says nothing in that view (sort by owner
# while grouped by owner) is never offered.
GROUP_BY_CHOICES = ('all', 'location', 'owner')
DEFAULT_SORT = 'name'

HORSE_SORT_LABELS = {
    'name': 'Name A–Z',
    '-name': 'Name Z–A',
    'age': 'Age: youngest first',
    '-age': 'Age: oldest first',
    'sex': 'Gender',
    'owner': 'Owner A–Z',
    'location': 'Location A–Z',
    '-arrived': 'Recently arrived',
    '-departed': 'Departed: newest first',
    'departed': 'Departed: earliest first',
}

# Keyed by sort context: the grouping on the Active tab, or the tab name
# when that tab shows one flat list.
HORSE_SORT_OPTIONS = {
    'all': ('name', '-name', 'age', '-age', 'sex', 'owner', 'location',
            '-arrived'),
    'location': ('name', '-name', 'age', '-age', 'sex', 'owner', '-arrived'),
    'owner': ('name', '-name', 'age', '-age', 'sex', 'location', '-arrived'),
    'departed': ('name', '-name', 'age', '-age', 'sex', '-departed',
                 'departed'),
    'search': ('name', '-name', 'age', '-age', 'sex', 'owner', 'location'),
}

GROUP_SORT_LABELS = {
    'name': 'Name A–Z',
    '-name': 'Name Z–A',
    '-count': 'Most horses first',
    'count': 'Fewest horses first',
}
# Location groups sort on site first, so say so rather than "Name A–Z".
GROUP_SORT_LABEL_OVERRIDES = {
    'location': {'name': 'Site, then name', '-name': 'Site, then name Z–A'},
}
GROUP_SORT_OPTIONS = ('name', '-name', '-count', 'count')


def _age_sort_expression():
    """Age in whole years, for database-side ordering.

    Mirrors Horse.calculated_age: from date_of_birth when it is set, else
    the stored age field. Horses with neither stay NULL, and the callers
    order them last.
    """
    today = timezone.localdate()
    had_birthday = Case(
        When(
            Q(date_of_birth__month__lt=today.month) |
            Q(date_of_birth__month=today.month,
              date_of_birth__day__lte=today.day),
            then=Value(0),
        ),
        default=Value(1),
        output_field=IntegerField(),
    )
    from_dob = ExpressionWrapper(
        Value(today.year) - ExtractYear('date_of_birth') - had_birthday,
        output_field=IntegerField(),
    )
    return Case(
        When(date_of_birth__isnull=False, then=from_dob),
        default=F('age'),
        output_field=IntegerField(),
    )


def _current_placement(horse):
    """The open placement if there is one, else the most recent."""
    placements = (
        getattr(horse, 'active_placements', None)
        or getattr(horse, 'last_placements', None)
        or []
    )
    return placements[0] if placements else None


def _sort_horses(horses, sort):
    """Order a materialised list of horses by one of HORSE_SORT_LABELS.

    Rows with nothing to sort on (no age, no owner, no location) go to the
    bottom in both directions, and ties fall back to name A–Z.
    """
    reverse = sort.startswith('-')
    field = sort.lstrip('-')

    # Name order first: sorted() is stable, so it becomes the tie-break.
    horses = sorted(horses, key=lambda h: h.name.lower())
    if field == 'name':
        return list(reversed(horses)) if reverse else horses

    def rank(is_missing):
        # Descending flips the ranks, so pick the one that still leaves
        # the unknown rows at the bottom.
        if reverse:
            return 0 if is_missing else 1
        return 1 if is_missing else 0

    def key(horse):
        if field == 'age':
            age = horse.calculated_age
            return (rank(age is None), age or 0)
        if field == 'sex':
            sex = horse.get_sex_display() or ''
            return (rank(not sex), sex.lower())
        if field == 'owner':
            owner = getattr(horse, 'resolved_owner', None)
            return (rank(owner is None), owner.name.lower() if owner else '')
        if field == 'location':
            placement = _current_placement(horse)
            location = placement.location if placement else None
            return (rank(location is None),
                    location.name.lower() if location else '')
        if field == 'arrived':
            placement = _current_placement(horse)
            start = placement.start_date if placement else None
            return (rank(start is None), start or date.min)
        return (0, horse.name.lower())

    return sorted(horses, key=key, reverse=reverse)


def _sort_groups(groups, group_sort):
    """Order the group cards. They arrive in name order already."""
    if group_sort == '-count':
        return sorted(groups, key=lambda g: (-g['count'], g['name'].lower()))
    if group_sort == 'count':
        return sorted(groups, key=lambda g: (g['count'], g['name'].lower()))
    if group_sort == '-name':
        # Location groups carry a site, and it leads the default order —
        # so it must lead the reverse of that order too.
        return sorted(
            groups,
            key=lambda g: (g.get('site') or '', g['name'].lower()),
            reverse=True,
        )
    return groups


class HorseListView(FeatureAccessMixin, ListView):
    feature = 'horses'
    access_level = LEVEL_VIEW
    model = Horse
    template_name = 'horses/horse_list.html'
    context_object_name = 'horses'

    @property
    def status(self):
        return self.request.GET.get('status', 'current')

    @property
    def is_searching(self):
        return bool(self.request.GET.get('search'))

    @property
    def group_by(self):
        value = self.request.GET.get('group_by', 'all')
        return value if value in GROUP_BY_CHOICES else 'all'

    @property
    def sort_context(self):
        """Which sort menu applies: the grouping, or the flat-list tab."""
        if self.is_searching:
            return 'search'
        if self.status == 'departed':
            return 'departed'
        return self.group_by

    @property
    def sort(self):
        """The horse sort key, dropped back to the default if it is not
        offered here — the menu differs per grouping, and stale keys
        survive in the URL when the grouping changes."""
        value = self.request.GET.get('sort', DEFAULT_SORT)
        if value in HORSE_SORT_OPTIONS[self.sort_context]:
            return value
        return DEFAULT_SORT

    @property
    def group_sort(self):
        value = self.request.GET.get('gsort', DEFAULT_SORT)
        return value if value in GROUP_SORT_OPTIONS else DEFAULT_SORT

    def get_paginate_by(self, queryset):
        # Only paginate the departed tab (current tab shows all, grouped).
        # Search results are never paginated: the search branch renders a
        # flat list with no pager, so paginating silently capped a departed
        # -tab search at its first 25 matches.
        if self.status == 'departed' and not self.is_searching:
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
            match = (
                Q(name__icontains=search) |
                Q(notes__icontains=search) |
                Q(placements__owner__name__icontains=search) |
                Q(placements__location__name__icontains=search)
            )
            # Typo tolerance: "alihnter" should still find ALIHUNTER.
            fuzzy_ids = fuzzy_horse_ids(search)
            if fuzzy_ids:
                match |= Q(pk__in=fuzzy_ids)
            queryset = queryset.filter(match).distinct()
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

        # Advanced filters (location/owner dropdowns). Departed horses have
        # no open placement by definition, so the departed tab matches on
        # placement history — requiring end_date__isnull=True there returned
        # nothing by construction.
        placement_filter = (
            {} if self.status == 'departed' else {'end_date__isnull': True}
        )
        location = self.request.GET.get('location')
        if location:
            queryset = queryset.filter(
                Exists(Placement.objects.filter(
                    horse=OuterRef('pk'),
                    location_id=location,
                    **placement_filter,
                ))
            )

        owner = self.request.GET.get('owner')
        if owner:
            queryset = queryset.filter(
                Exists(Placement.objects.filter(
                    horse=OuterRef('pk'),
                    owner_id=owner,
                    **placement_filter,
                ))
            )

        if self.status == 'departed' and not self.is_searching:
            return self._order_departed(queryset)
        return queryset.order_by('name')

    def _order_departed(self, queryset):
        """Order the departed tab in the database.

        That tab is paginated, so a Python sort would only order the 25
        rows of the current page. The other tabs render every row, and
        sort in get_context_data on resolved owner/location values that
        SQL cannot see.
        """
        sort = self.sort
        if sort in ('age', '-age'):
            queryset = queryset.annotate(sort_age=_age_sort_expression())
            field = F('sort_age')
        elif sort in ('departed', '-departed'):
            queryset = queryset.annotate(
                sort_departed=Max('placements__end_date'),
            )
            field = F('sort_departed')
        elif sort == 'sex':
            # Blank sex sorts last, to match the flat-list sort.
            return queryset.annotate(
                sort_sex=Case(
                    When(sex='', then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                ),
            ).order_by('sort_sex', 'sex', 'name')
        elif sort == '-name':
            return queryset.order_by('-name')
        else:
            return queryset.order_by('name')

        direction = (
            field.desc(nulls_last=True) if sort.startswith('-')
            else field.asc(nulls_last=True)
        )
        return queryset.order_by(direction, 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status'] = self.status
        context['group_by'] = self.group_by
        context['sort'] = self.sort
        context['group_sort'] = self.group_sort
        context['active_sort_label'] = HORSE_SORT_LABELS[self.sort]
        context['sort_options'] = [
            {
                'key': key,
                'label': HORSE_SORT_LABELS[key],
                'active': key == self.sort,
            }
            for key in HORSE_SORT_OPTIONS[self.sort_context]
        ]
        # Only the grouped views order groups as well as horses.
        group_labels = dict(GROUP_SORT_LABELS)
        group_labels.update(
            GROUP_SORT_LABEL_OVERRIDES.get(self.sort_context, {}),
        )
        context['group_sort_options'] = [
            {
                'key': key,
                'label': group_labels[key],
                'active': key == self.group_sort,
            }
            for key in GROUP_SORT_OPTIONS
        ] if self.sort_context in ('location', 'owner') else None
        # Location.objects.active() hides archived fields (from main).
        context['locations'] = Location.objects.active().order_by('site', 'name')
        context['owners'] = Owner.objects.values('pk', 'name').order_by('name')
        context['is_searching'] = self.is_searching
        # Single query for both counts. The departed test must be
        # NOT EXISTS(open placement) — a negated multi-valued Q inside an
        # aggregate filter compiles per-joined-row, which counted every
        # horse that had ever moved fields as departed.
        has_open_placement = Exists(Placement.objects.filter(
            horse=OuterRef('pk'), end_date__isnull=True,
        ))
        counts = Horse.objects.annotate(
            has_open_placement=has_open_placement,
        ).aggregate(
            total_current=Count(
                'pk', filter=Q(is_active=True, has_open_placement=True),
            ),
            total_departed=Count(
                'pk', filter=Q(is_active=False) | Q(has_open_placement=False),
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
            horses = _sort_horses(horses, self.sort)

            if group_by == 'all':
                # One flat card holding every horse on the property.
                context['grouped_horses'] = [{
                    'name': 'All Horses',
                    'pk': None,
                    'count': len(horses),
                    'horses': horses,
                }]
            else:
                # Bucket into a dict rather than itertools.groupby: the
                # horses are already in the requested sort order, and
                # groupby would need them re-sorted by the group key.
                buckets = {}
                for h in horses:
                    if group_by == 'owner':
                        owner = h.resolved_owner
                        key = (
                            owner.name if owner else 'No Owner',
                            owner.pk if owner else 0,
                            '',
                        )
                    else:
                        placement = _current_placement(h)
                        location = placement.location if placement else None
                        key = (
                            location.name if location else 'No Location',
                            location.pk if location else 0,
                            location.site if location else '',
                        )
                    buckets.setdefault(key, []).append(h)

                # Location groups stay ordered by site then name, as
                # before; owner groups by name.
                def group_order(item):
                    name, pk, site = item
                    return (site, name) if group_by == 'location' else (name,)

                grouped = [
                    {
                        'site': site,
                        'name': name,
                        'pk': pk,
                        'count': len(members),
                        'horses': members,
                    }
                    for (name, pk, site), members in sorted(
                        buckets.items(), key=lambda kv: group_order(kv[0]),
                    )
                ]
                context['grouped_horses'] = _sort_groups(
                    grouped, self.group_sort,
                )
        elif self.is_searching:
            # Search results are one flat, unpaginated list.
            context['horses'] = _sort_horses(horses, self.sort)

        return context


class HorseDetailView(FeatureAccessMixin, DetailView):
    feature = 'horses'
    access_level = LEVEL_VIEW
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
        context['today'] = timezone.localdate()
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
        context['photos'] = horse.photos.all()[:12]
        context['photo_count'] = horse.photos.count()
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


class HorseCreateView(FeatureAccessMixin, CreateView):
    feature = 'horses'
    model = Horse
    form_class = HorseForm
    template_name = 'horses/horse_form.html'

    def get_success_url(self):
        # Land on the new horse's page — that's where the next step
        # ("Log Arrival", quick actions) lives, not back on the list.
        return reverse_lazy('horse_detail', kwargs={'pk': self.object.pk})

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


class HorseUpdateView(FeatureAccessMixin, UpdateView):
    feature = 'horses'
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


@feature_required('horses')
def horse_quick_edit(request, pk):
    """The day-to-day horse fields, for the pop-up sheet only.

    Triggers keep their href on the full Edit page, so without JavaScript
    (or on a direct visit) this simply redirects there. Inside the sheet
    (HX-Target: popup-body) it renders QuickHorseForm, re-renders it with
    errors, and answers a valid save with 204 + ``popup:saved``.
    """
    horse = get_object_or_404(Horse, pk=pk)
    if not is_popup_request(request):
        return redirect('horse_update', pk=horse.pk)

    if request.method == 'POST':
        form = QuickHorseForm(request.POST, instance=horse)
        if form.is_valid():
            form.save()
            messages.success(request, f"Horse '{horse.name}' updated successfully.")
            return popup_saved_response()
    else:
        form = QuickHorseForm(instance=horse)

    return render(request, 'horses/partials/quick_edit_form.html', {
        'horse': horse,
        'form': form,
    })


def _flash_superseded_trim(request, horse, placement):
    """Tell the user when logging a return shortened the previous stay —
    the trim changes recorded (potentially invoiced) dates, so it must
    never happen silently."""
    trimmed = getattr(placement, 'superseded_trim', None)
    if trimmed is not None:
        messages.info(
            request,
            f"{horse.name}'s previous stay at {trimmed.location.name} now "
            f"ends {trimmed.end_date:%d %b %Y} (was recorded as "
            f"{trimmed.superseded_from:%d %b %Y}) so it doesn't overlap "
            f"this return."
        )


def _safe_next_url(request):
    """A same-origin ``next`` from the query string or POST body, else ''."""
    candidate = request.POST.get('next') or request.GET.get('next') or ''
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return ''


@feature_required('horses')
def horse_move(request, pk):
    """Move a horse to a new location.

    One form partial serves two shells: the full page, and — when the
    shared pop-up sheet asks for it (HX-Target: popup-body) — the form on
    its own. A successful pop-up save answers 204 + ``HX-Trigger:
    popup:saved`` so the sheet closes and refreshes the page it was opened
    from (the success toast rides along on that refresh); the full page
    redirects to a same-origin ``next`` or the horse list. Service errors
    (move date before the current stay, a clashing update) render inline
    on the form in both shells.
    """
    from ..services import PlacementService

    horse = get_object_or_404(Horse, pk=pk)
    current_placement = horse.current_placement
    in_popup = is_popup_request(request)
    next_url = _safe_next_url(request)

    if request.method == 'POST':
        form = MoveHorseForm(request.POST)
        if form.is_valid():
            try:
                new_placement = PlacementService.move_horse(
                    horse,
                    new_location=form.cleaned_data['new_location'],
                    move_date=form.cleaned_data['move_date'],
                    new_owner=form.cleaned_data['new_owner'],
                    new_rate_type=form.cleaned_data['new_rate_type'],
                    expected_departure=form.cleaned_data.get('expected_departure'),
                    notes=form.cleaned_data['notes'],
                )
            except ValidationError as e:
                form.add_error(None, e)
            except IntegrityError:
                form.add_error(
                    None,
                    "That change clashed with another update to the same "
                    "horse (it may already be placed) — refresh and check "
                    "the current placement before retrying.",
                )
            else:
                messages.success(request, f"{horse.name} moved successfully.")
                _flash_superseded_trim(request, horse, new_placement)
                if in_popup:
                    return popup_saved_response()
                return redirect(next_url or 'horse_list')
    else:
        form = MoveHorseForm(initial={
            'move_date': timezone.localdate()
        })

    template = 'horses/partials/move_form.html' if in_popup else 'horses/horse_move.html'
    return render(request, template, {
        'horse': horse,
        'form': form,
        'current_placement': current_placement,
        'in_popup': in_popup,
        'next_url': next_url,
        'today': timezone.localdate(),
    })


@feature_required('horses')
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
            messages.success(request, format_html(
                '{} created and arrived at {}. <a href="{}?category=arrival" class="underline font-semibold">Add photos</a>',
                horse.name,
                placement.location.name,
                reverse('horse_photo_add', args=[horse.pk]),
            ))
            return redirect('horse_detail', pk=horse.pk)
    else:
        initial = {'arrival_date': timezone.localdate()}
        location_id = request.GET.get('location')
        if location_id:
            initial['location'] = location_id
        form = NewArrivalForm(initial=initial)

    return render(request, 'horses/horse_new_arrival.html', {'form': form})


@feature_required('horses')
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
                messages.success(request, format_html(
                    '{} arrived at {}. <a href="{}?category=arrival" class="underline font-semibold">Add photos</a>',
                    horse.name,
                    placement.location.name,
                    reverse('horse_photo_add', args=[horse.pk]),
                ))
                _flash_superseded_trim(request, horse, placement)
                return redirect('horse_list')
            except ValidationError as e:
                messages.error(request, '; '.join(e.messages))
            except IntegrityError:
                messages.error(
                    request,
                    "That change clashed with another update to the same "
                    "horse (it may already be placed) — refresh and check "
                    "the current placement before retrying.",
                )
    else:
        initial = {'arrival_date': timezone.localdate()}
        primary_owner = horse.primary_owner
        if primary_owner:
            initial['owner'] = primary_owner.pk
        form = SingleArrivalForm(initial=initial)

    return render(request, 'horses/horse_arrive.html', {
        'horse': horse,
        'form': form,
    })


@feature_required('horses')
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
            if placement.end_date is None:
                messages.success(
                    request,
                    f"{horse.name} scheduled to depart {placement.location.name} "
                    f"on {departure_date.strftime('%d %b %Y')} — log the departure "
                    f"on the day to close the placement.",
                )
            else:
                messages.success(request, f"{horse.name} departed from {placement.location.name}.")
        except ValidationError as e:
            messages.error(request, str(e))
        except IntegrityError:
            messages.error(
                request,
                "That change clashed with another update to the same horse "
                "— refresh and check the current placement before retrying.",
            )

    return redirect('horse_detail', pk=horse.pk)


@feature_required('horses')
def horse_reactivate(request, pk):
    """Repair action: mark a horse active again while its placement is open.

    Used by the stranded-record banner on the horse page — the horse is
    flagged departed but the placement below is correct, so flipping the
    flag is all that's needed (no new placement, no date changes).
    """
    from ..services import PlacementService

    horse = get_object_or_404(Horse, pk=pk)
    if request.method == 'POST':
        if horse.current_placement:
            PlacementService.reactivate(horse)
            messages.success(
                request,
                f"{horse.name} is active again at {horse.current_placement.location.name}.",
            )
        else:
            messages.error(
                request,
                f"{horse.name} has no current placement — use Log Arrival instead.",
            )
    return redirect('horse_detail', pk=horse.pk)


@feature_required('horses')
def confirm_departure(request, pk):
    """Confirm a horse has departed and deactivate it (HTMX endpoint)."""
    from ..services import PlacementService

    horse = get_object_or_404(Horse, pk=pk)
    if request.method == 'POST':
        if PlacementService.confirm_departure(horse):
            messages.success(request, f"{horse.name} confirmed as departed.")
        else:
            messages.warning(
                request,
                f"{horse.name} is still placed in a field — use Log Departure instead.",
            )
    if request.headers.get('HX-Request'):
        return HttpResponse('')
    return redirect('dashboard')


@feature_required('horses')
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


@feature_required('horses')
def confirm_departures_bulk(request):
    """Confirm multiple horses as departed in one action (HTMX endpoint)."""
    from ..services import PlacementService

    if request.method == 'POST':
        horse_ids = request.POST.getlist('horse_ids')
        if horse_ids:
            count = PlacementService.confirm_departures_bulk(horse_ids)
            skipped = len(set(horse_ids)) - count
            msg = f"{count} horse{'s' if count != 1 else ''} confirmed as departed."
            if skipped > 0:
                msg += f" {skipped} skipped (already departed or still placed in a field)."
            messages.success(request, msg)
    if request.headers.get('HX-Request'):
        return HttpResponse('')
    return redirect('dashboard')


@feature_required('horses')
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
