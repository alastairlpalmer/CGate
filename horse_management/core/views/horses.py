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
from ..permissions import (
    LEVEL_HIDDEN,
    LEVEL_ORDER,
    LEVEL_VIEW,
    FeatureAccessMixin,
    access_map,
    feature_required,
)
from ..models import Horse, Location, Owner, OwnershipShare, Placement
from .placements import movement_history
from .locations import (
    USAGE_COLORS,
    resolve_usage_window,
    usage_days_for_locations,
)
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
# "all" is a flat list of every horse on the property; the other axes put
# the same horses into group cards. Each axis has its own sort menu, so an
# option that says nothing there (sort by owner while grouped by owner) is
# never offered.
#
# The grouped axes build from the *entity* side — every location, site or
# owner — and hang horses off them, rather than bucketing the horse list.
# Grouping horses cannot show an empty location, and an empty location is
# exactly what you look for when you move a horse.
GROUP_BY_CHOICES = ('all', 'location', 'owner')
GROUPED_AXES = ('location', 'owner')

# What one group on the page *is*. The Location axis prints either a card
# per location or one table per site, so its group kind is 'location' or
# 'site'; the other axes print groups of their own kind. Everything keyed
# by kind (sorts, empty groups, land use) reads the kind, not the axis.
GROUP_KINDS = ('all', 'site', 'location', 'owner')

# How a grouped axis lays its groups out. The first option is the default.
#   location: a card per location under a site heading, or one table per
#             site with the location as a column (the old Site axis).
#   owner:    one table with an owner row above each owner's horses, or a
#             card per owner.
LAYOUT_OPTIONS = {
    'location': (('location', 'Locations'), ('site', 'Sites')),
    'owner': (('table', 'Table'), ('cards', 'Cards')),
}
# Old links and bookmarks said ?group_by=site. They mean the Location axis
# in its site layout.
LEGACY_AXIS_ALIASES = {'site': ('location', 'site')}

# Axes that need more than the 'horses' feature to make sense. A role can
# see horses without seeing owners, so an axis its role hides is not
# offered and its URL falls back to All.
AXIS_FEATURE = {
    'location': 'locations',
    'owner': 'owners',
}

# Which group kinds show empty groups unless the URL says otherwise. Empty
# locations answer "where can this horse go"; a roll of owners with no
# horses is just noise.
SHOW_EMPTY_DEFAULT = {'site': True, 'location': True, 'owner': False}

# Which group kinds open collapsed. Locations and owners make long pages,
# so they start as a list of headers to scan; a site table is the point of
# picking that layout, so it opens. Per-group state lives in the browser.
COLLAPSED_DEFAULT = {'site': False, 'location': True, 'owner': True}

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
    'site': ('name', '-name', 'age', '-age', 'sex', 'owner', 'location',
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
# Location groups run inside a site heading, so their labels say so.
GROUP_SORT_LABEL_OVERRIDES = {
    'location': {
        'name': 'Name A–Z, in site',
        '-name': 'Name Z–A, in site',
        '-count': 'Fullest first, empty last',
        'count': 'Emptiest first, in site',
    },
}
GROUP_SORT_OPTIONS = ('name', '-name', '-count', 'count')

# Locations print under a heading per site, and the useful thing to see
# first inside a site is where the horses are — which drops empty ground
# to the bottom of each site by itself.
GROUP_SORT_DEFAULT = {'location': '-count'}

# Group headers that carry a capacity ring and a usage badge.
AXES_WITH_CAPACITY = ('location',)

# Axes whose groups are land, so a land-use strip says something.
# It would say nothing under Owner or All.
AXES_WITH_USAGE = ('location', 'site')


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


def _usage_strip(totals):
    """Land-use day counts as bar segments, widest share first.

    Only the usages with days in the window: a strip of five zero-width
    slivers reads as noise on a browse row.
    """
    tracked = sum(totals.values())
    if not tracked:
        return []
    labels = dict(Location.Usage.choices)
    segments = [
        {
            'value': value,
            'label': labels[value],
            'color': USAGE_COLORS.get(value, '#6A8990'),
            'days': days,
            'pct': round(days / tracked * 100, 1),
        }
        for value, days in totals.items() if days
    ]
    segments.sort(key=lambda s: -s['days'])
    return segments


def _location_group(location, horses):
    """A group card for one location, carrying its capacity and land use.

    Capacity and usage hang off the location, not off anything standing
    in it — which is why an empty or rested field can still be a card.
    """
    count = len(horses)
    return {
        'kind': 'location',
        'name': location.name,
        'pk': location.pk,
        'location_ids': [location.pk],
        'site': location.site,
        'count': count,
        'horses': horses,
        'capacity': location.capacity,
        'availability': (
            location.capacity - count if location.capacity is not None
            else None
        ),
        'usage': location.usage,
        'usage_display': location.get_usage_display(),
    }


def _sort_groups(groups, group_sort, site_major=False):
    """Order the group cards. They arrive in name order already.

    ``site_major`` keeps every order inside its site, because the location
    axis prints a heading per site — an order that crossed those headings
    would scatter each site's locations down the page.
    """
    def site(group):
        if not site_major:
            return ''
        # A group with no site ("No Location") sorts after every real one.
        return (group.get('site') or '\uffff').lower()

    if group_sort == '-count':
        return sorted(groups, key=lambda g: (site(g), -g['count'], g['name'].lower()))
    if group_sort == 'count':
        return sorted(groups, key=lambda g: (site(g), g['count'], g['name'].lower()))
    if group_sort == '-name':
        if site_major:
            return sorted(
                groups, key=lambda g: (site(g), _reverse_key(g['name'].lower())),
            )
        return sorted(groups, key=lambda g: g['name'].lower(), reverse=True)
    if site_major:
        return sorted(groups, key=lambda g: (site(g), g['name'].lower()))
    return groups


class _reverse_key:
    """Sort one field descending inside an otherwise ascending key."""

    __slots__ = ('value',)

    def __init__(self, value):
        self.value = value

    def __lt__(self, other):
        return other.value < self.value

    def __eq__(self, other):
        return other.value == self.value


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
    def shows_movements(self):
        """Whether the Movements tab is what the URL asked for.

        The log reads placements — horse, owner and location together —
        so it needs the locations feature, the same as the land axes.
        """
        if self.request.GET.get('tab') != 'movements':
            return False
        access = access_map(self.request.user)
        level = access.get('locations', LEVEL_HIDDEN)
        return LEVEL_ORDER[level] >= LEVEL_ORDER[LEVEL_VIEW]

    @property
    def available_axes(self):
        """The group-by axes this role may use.

        Horses, owners and locations are separate features. A role can see
        horses without seeing owners, so the axes that read another
        feature's records are offered only when that feature is visible.
        """
        access = access_map(self.request.user)
        axes = ['all']
        for axis in GROUPED_AXES:
            level = access.get(AXIS_FEATURE[axis], LEVEL_HIDDEN)
            if LEVEL_ORDER[level] >= LEVEL_ORDER[LEVEL_VIEW]:
                axes.append(axis)
        return axes

    @property
    def group_by(self):
        value = self.request.GET.get('group_by', 'all')
        value, _ = LEGACY_AXIS_ALIASES.get(value, (value, None))
        # An axis the role cannot see falls back to All rather than
        # erroring — a stale bookmark should still show the horses.
        return value if value in self.available_axes else 'all'

    @property
    def layout(self):
        """How the grouped axis prints its groups; None on the All axis."""
        options = LAYOUT_OPTIONS.get(self.group_by)
        if not options:
            return None
        keys = [key for key, _ in options]
        value = self.request.GET.get('layout')
        if value not in keys:
            raw_axis = self.request.GET.get('group_by')
            _, value = LEGACY_AXIS_ALIASES.get(raw_axis, (None, None))
        return value if value in keys else keys[0]

    @property
    def group_kind(self):
        """What each group on the page is: 'all', 'site', 'location' or
        'owner'. The Location axis in its site layout prints sites."""
        if self.group_by == 'location' and self.layout == 'site':
            return 'site'
        return self.group_by

    @property
    def groups_collapsed(self):
        return COLLAPSED_DEFAULT.get(self.group_kind, False)

    @property
    def shows_usage(self):
        """Land-use only reads on the axes whose groups are land."""
        return self.group_kind in AXES_WITH_USAGE

    @property
    def usage_window(self):
        # Last 3 months by default here, not the calendar year the
        # analytics tab opens on: on a page you are in all day the useful
        # question is "has this location had a rest recently".
        return resolve_usage_window(self.request, default='3mo')

    @property
    def show_empty(self):
        """Whether groups with no horses are listed.

        On by default for locations and sites: an empty field is the
        answer to "where can this horse go", and grouping horses can
        never produce one.
        """
        raw = self.request.GET.get('show_empty')
        if raw in ('0', '1'):
            return raw == '1'
        return SHOW_EMPTY_DEFAULT.get(self.group_kind, False)

    @property
    def sort_context(self):
        """Which sort menu applies: the grouping, or the flat-list tab."""
        if self.is_searching:
            return 'search'
        if self.status == 'departed':
            return 'departed'
        return self.group_kind

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
        default = GROUP_SORT_DEFAULT.get(self.sort_context, DEFAULT_SORT)
        value = self.request.GET.get('gsort', default)
        return value if value in GROUP_SORT_OPTIONS else default

    def get_paginate_by(self, queryset):
        # The Movements tab shows a placement log, not horses.
        if self.shows_movements:
            return None
        # Only paginate the departed tab (current tab shows all, grouped).
        # Search results are never paginated: the search branch renders a
        # flat list with no pager, so paginating silently capped a departed
        # -tab search at its first 25 matches.
        if self.status == 'departed' and not self.is_searching:
            return 25
        return None

    def get_queryset(self):
        if self.shows_movements:
            # Nothing on that tab reads the horse list, so don't build it.
            return Horse.objects.none()

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
        # Non-numeric ids raise ValueError at query time — treat as unset.
        location = self.request.GET.get('location')
        if location and location.isdigit():
            queryset = queryset.filter(
                Exists(Placement.objects.filter(
                    horse=OuterRef('pk'),
                    location_id=location,
                    **placement_filter,
                ))
            )

        owner = self.request.GET.get('owner')
        if owner and owner.isdigit():
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
        context['shows_movements'] = self.shows_movements
        context['movements_available'] = (
            'location' in self.available_axes
        )
        if self.shows_movements:
            # Same log, same filters as the Locations page's tab — one
            # query function serves both (placements.movement_history).
            placements, movement_status = movement_history(self.request)
            context['placements'] = placements
            context['current_status'] = movement_status
            context['movement_statuses'] = (
                ('active', 'Current'), ('ended', 'Ended'), ('all', 'All'),
            )
        context['group_by'] = self.group_by
        context['group_kind'] = self.group_kind
        context['layout'] = self.layout
        context['layout_options'] = [
            {'key': key, 'label': label, 'active': key == self.layout}
            for key, label in LAYOUT_OPTIONS.get(self.group_by, ())
        ]
        context['groups_collapsed'] = self.groups_collapsed
        context['available_axes'] = self.available_axes
        context['show_empty'] = self.show_empty
        context['show_empty_default'] = SHOW_EMPTY_DEFAULT.get(
            self.group_kind, False
        )
        context['axis_has_capacity'] = self.group_kind in AXES_WITH_CAPACITY
        context['shows_usage'] = self.shows_usage
        if self.shows_usage:
            window = self.usage_window
            context['usage_range'] = window['range']
            context['usage_year'] = window['year']
            context['usage_period_label'] = window['label']
            context['usage_ranges'] = (
                ('3mo', '3 mo'), ('6mo', '6 mo'), ('year', str(window['year'])),
            )
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
        ] if self.group_by in GROUPED_AXES else None
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
        if self.shows_movements:
            pass
        elif self.status == 'current' and not self.is_searching:
            horses = _sort_horses(horses, self.sort)
            group_kind = self.group_kind

            if group_kind == 'all':
                # One flat card holding every horse on the property.
                context['grouped_horses'] = [{
                    'kind': 'all',
                    'name': 'All Horses',
                    'pk': None,
                    'count': len(horses),
                    'horses': horses,
                }]
            else:
                groups = self._build_groups(horses, group_kind)
                if self.shows_usage:
                    self._attach_usage(groups)
                context['grouped_horses'] = groups
        elif self.is_searching:
            # Search results are one flat, unpaginated list.
            context['horses'] = _sort_horses(horses, self.sort)

        return context

    def _build_groups(self, horses, group_kind):
        """Group the horses by location, site or owner.

        Built from the entity side: every location (or site, or owner) is
        a candidate group, and the horses hang off it. Bucketing the horse
        list instead would silently drop every empty location — the one
        thing you most want to see when moving a horse.
        """
        if group_kind == 'owner':
            return self._owner_groups(horses)
        if group_kind == 'site':
            return self._site_groups(horses)
        return self._location_groups(horses)

    def _attach_usage(self, groups):
        """Hang a land-use strip on each land group, in one query.

        Every location on the page is fetched together — see
        ``usage_days_for_locations``. Doing it per group is the regression
        that AxisQueryCountTests exists to catch.
        """
        window = self.usage_window
        wanted = {pk for group in groups for pk in group['location_ids']}
        usage = usage_days_for_locations(
            wanted, window['start'], window['end'],
        )
        for group in groups:
            totals = {}
            for pk in group['location_ids']:
                for value, days in usage[pk][0].items():
                    totals[value] = totals.get(value, 0) + days
            group['usage_strip'] = _usage_strip(totals)
            group['usage_days'] = sum(totals.values())

    def _location_groups(self, horses):
        by_location = {}
        unplaced = []
        for horse in horses:
            placement = _current_placement(horse)
            location = placement.location if placement else None
            if location is None:
                unplaced.append(horse)
            else:
                by_location.setdefault(location.pk, []).append(horse)

        groups = []
        if self.show_empty:
            # Archived fields stay out: they keep their history but are
            # hidden from lists and pickers.
            spine = Location.objects.active().order_by('site', 'name')
        else:
            spine = Location.objects.filter(
                pk__in=by_location
            ).order_by('site', 'name')

        for location in spine:
            groups.append(
                _location_group(location, by_location.pop(location.pk, []))
            )

        # A horse on an archived field still has to appear somewhere.
        for location in Location.objects.filter(pk__in=by_location):
            groups.append(_location_group(location, by_location[location.pk]))
        groups.sort(key=lambda g: ((g['site'] or '').lower(),
                                   g['name'].lower()))

        if unplaced:
            groups.append({
                'kind': 'location', 'name': 'No Location', 'pk': None,
                'location_ids': [], 'site': '',
                'count': len(unplaced), 'horses': unplaced,
                'capacity': None, 'availability': None,
                'usage': '', 'usage_display': '',
            })
        return _sort_groups(groups, self.group_sort, site_major=True)

    def _site_groups(self, horses):
        by_site = {}
        for horse in horses:
            placement = _current_placement(horse)
            location = placement.location if placement else None
            site = location.site if location else ''
            by_site.setdefault(site, []).append(horse)

        # One pass over the active locations gives both the sites that
        # exist and the locations each one owns — a site's land use is the
        # sum of its fields'.
        locations_by_site = {}
        for pk, site in Location.objects.active().values_list('pk', 'site'):
            locations_by_site.setdefault(site, []).append(pk)

        names = set(by_site)
        if self.show_empty:
            names |= set(locations_by_site)
        names.discard('')

        groups = [
            {
                'kind': 'site', 'name': site, 'pk': None, 'site': '',
                'location_ids': locations_by_site.get(site, []),
                'count': len(by_site.get(site, [])),
                'horses': by_site.get(site, []),
            }
            for site in sorted(names, key=str.lower)
        ]
        if by_site.get(''):
            groups.append({
                'kind': 'site', 'name': 'No Site', 'pk': None, 'site': '',
                'location_ids': [],
                'count': len(by_site['']), 'horses': by_site[''],
            })
        return _sort_groups(groups, self.group_sort)

    def _owner_groups(self, horses):
        by_owner = {}
        unowned = []
        for horse in horses:
            owner = horse.resolved_owner
            if owner is None:
                unowned.append(horse)
            else:
                by_owner.setdefault(owner.pk, []).append(horse)

        if self.show_empty:
            spine = Owner.objects.order_by('name')
        else:
            spine = Owner.objects.filter(pk__in=by_owner).order_by('name')

        groups = [
            {
                'kind': 'owner', 'name': owner.name, 'pk': owner.pk,
                'site': '', 'count': len(by_owner.get(owner.pk, [])),
                'horses': by_owner.get(owner.pk, []),
            }
            for owner in spine
        ]
        if unowned:
            groups.append({
                'kind': 'owner', 'name': 'No Owner', 'pk': None, 'site': '',
                'count': len(unowned), 'horses': unowned,
            })
        return _sort_groups(groups, self.group_sort)


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
    """Log a single horse arriving at a location (from Horse Detail).

    Serves the pop-up sheet too (HX-Target: popup-body): the form partial
    alone, 204 + ``popup:saved`` on success, and the partial with any
    service error inline otherwise.
    """
    from ..services import PlacementService

    horse = get_object_or_404(Horse, pk=pk)
    in_popup = is_popup_request(request)

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
                messages.success(request, format_html(
                    '{} arrived at {}. <a href="{}?category=arrival" class="underline font-semibold">Add photos</a>',
                    horse.name,
                    placement.location.name,
                    reverse('horse_photo_add', args=[horse.pk]),
                ))
                _flash_superseded_trim(request, horse, placement)
                if in_popup:
                    return popup_saved_response()
                return redirect('horse_list')
    else:
        initial = {'arrival_date': timezone.localdate()}
        primary_owner = horse.primary_owner
        if primary_owner:
            initial['owner'] = primary_owner.pk
        form = SingleArrivalForm(initial=initial)

    template = 'horses/partials/arrive_form.html' if in_popup else 'horses/horse_arrive.html'
    return render(request, template, {
        'horse': horse,
        'form': form,
        'in_popup': in_popup,
        'today': timezone.localdate(),
    })


@feature_required('horses')
def horse_depart(request, pk):
    """Log a single horse departing (from Horse Detail, POST only)."""
    from ..services import PlacementService

    horse = get_object_or_404(Horse, pk=pk)

    if request.method == 'POST':
        if not horse.current_placement:
            # A double-submit or a colleague's bulk departure closed it
            # first. Say so — a silent redirect looks like a dropped request
            # and invites another click.
            messages.error(
                request,
                f"{horse.name} has no current placement to depart from.",
            )
            return redirect('horse_detail', pk=horse.pk)
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
                f"{horse.name} is still placed in a location — use Log Departure instead.",
            )
    if request.headers.get('HX-Request'):
        # 204 + popup:saved: nothing is swapped, and popup.js re-fetches
        # #main-content so the queued message shows and the widget
        # re-renders from the database. Swapping an empty body used to
        # delete the row even when the action was refused.
        return HttpResponse(status=204, headers={'HX-Trigger': 'popup:saved'})
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
        # 204 + popup:saved: nothing is swapped, and popup.js re-fetches
        # #main-content so the queued message shows and the widget
        # re-renders from the database. Swapping an empty body used to
        # delete the row even when the action was refused.
        return HttpResponse(status=204, headers={'HX-Trigger': 'popup:saved'})
    return redirect('dashboard')


@feature_required('horses')
def confirm_departures_bulk(request):
    """Confirm multiple horses as departed in one action (HTMX endpoint)."""
    from ..services import PlacementService

    if request.method == 'POST':
        horse_ids = [
            i for i in request.POST.getlist('horse_ids') if i.isdigit()
        ]
        if horse_ids:
            count = PlacementService.confirm_departures_bulk(horse_ids)
            skipped = len(set(horse_ids)) - count
            msg = f"{count} horse{'s' if count != 1 else ''} confirmed as departed."
            if skipped > 0:
                msg += f" {skipped} skipped (already departed or still placed in a location)."
            messages.success(request, msg)
    if request.headers.get('HX-Request'):
        # 204 + popup:saved: nothing is swapped, and popup.js re-fetches
        # #main-content so the queued message shows and the widget
        # re-renders from the database. Swapping an empty body used to
        # delete the row even when the action was refused.
        return HttpResponse(status=204, headers={'HX-Trigger': 'popup:saved'})
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
