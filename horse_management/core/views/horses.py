"""
Horse views — CRUD, move, arrive, depart, ownership.
"""

import re
from datetime import date, timedelta
from pathlib import PurePosixPath

from django.conf import settings
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
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from health.models import (
    BreedingRecord,
    FarrierVisit,
    MedicalCondition,
    Vaccination,
    VetVisit,
    WormEggCount,
    WormingTreatment,
    current_farrier_visits,
    current_vaccinations,
    current_worming_treatments,
)
from training import horse as training_horse

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
    has_feature_access,
)
from ..models import Horse, HorseIdentifier, Location, Owner, OwnershipShare, Placement
from .placements import movement_history
from .locations import (
    USAGE_COLORS,
    resolve_usage_window,
    usage_days_for_locations,
)
from ..search import fuzzy_horse_ids
from ._next import safe_next
from ._popup import is_popup_request, popup_saved_response
from ._undo import group_move_undo_url, undo_tag


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
# How many horse rows a page renders before it stops and says so.
#
# The active tab lists every horse, and every horse is rendered twice: a
# card for phones and a table row from md up. That is about 7.7 kB of
# HTML each, so a hundred-horse yard was shipping the better part of a
# megabyte to a phone in a gateway, and a four-hundred-horse yard three
# of them. The cap bounds the page; the horses past it are a count and a
# link that asks for them.
#
# Sixty is chosen to sit above a normal yard: most lists never reach it
# and nothing about them changes.
ROW_CAP = 60

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
    'usage': 'Use case',
}
# Location groups run inside a site heading, so their labels say so.
GROUP_SORT_LABEL_OVERRIDES = {
    'location': {
        'name': 'Name A–Z, in site',
        '-name': 'Name Z–A, in site',
        '-count': 'Fullest first, empty last',
        'count': 'Emptiest first, in site',
        'usage': 'Use case, in site',
    },
}
GROUP_SORT_OPTIONS = ('name', '-name', '-count', 'count')
# Only locations carry a use case, so only their groups can order by it.
GROUP_SORT_OPTIONS_BY_AXIS = {
    'location': GROUP_SORT_OPTIONS + ('usage',),
}

# The use-case filter reads on the location axis alone: its groups are
# the locations themselves, so "show me the mixed grazing" is a question
# about which groups appear. In a site table or under Owner a location's
# use case is one input among several, not the thing the group is.
AXES_WITH_USE_FILTER = ('location',)

# Order the use cases print in — the order they are declared in, which
# puts the horse ground first and the odd cases last.
USAGE_ORDER = {
    value: index for index, (value, _label) in enumerate(Location.Usage.choices)
}

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

    Mirrors Horse.calculated_age (core/identity.age_on): one rule for all
    horses, a year older on 1 January, or on 1 August for a horse foaled
    in the southern hemisphere. A year-only or estimated date counts from
    its own year. Horses with no date stay NULL (a stored age is turned
    into a date on save), and the callers order them last.
    """
    from ..identity import SOUTHERN_HEMISPHERE, season_year

    today = timezone.localdate()
    southern = Q(country_of_birth__in=SOUTHERN_HEMISPHERE)
    exact = Q(dob_precision=Horse.DobPrecision.EXACT)
    foaled_season = Case(
        # Southern, exact date before August: the season began the year before.
        When(southern & exact & Q(date_of_birth__month__lt=8),
             then=ExtractYear('date_of_birth') - Value(1)),
        default=ExtractYear('date_of_birth'),
        output_field=IntegerField(),
    )
    this_season = Case(
        When(southern, then=Value(season_year(today, southern=True))),
        default=Value(today.year),
        output_field=IntegerField(),
    )
    return Case(
        When(date_of_birth__isnull=False,
             then=ExpressionWrapper(this_season - foaled_season, output_field=IntegerField())),
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


def _location_group(location, horses, by_stable=False):
    """A group card for one location, carrying its capacity and land use.

    Capacity and usage hang off the location, not off anything standing
    in it — which is why an empty or rested field can still be a card.

    ``by_stable`` puts a barn's horses in stable order, the ones with no
    stable last: it is the order you meet them in walking the barn. Only
    asked for when the reader has not chosen a sort of their own.
    """
    if by_stable and location.is_barn:
        horses = sorted(horses, key=lambda horse: (
            getattr(horse, 'stable_position', None) is None,
            getattr(horse, 'stable_position', None) or 0,
        ))
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
        # A barn has no land use, so its card has no land-use strip.
        'is_barn': location.is_barn,
        # What the ring shows. More than ``count`` when stabled horses go
        # out here by day (set by the caller, which knows).
        'ring_count': count,
        'turnout_count': 0,
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

    if group_sort == 'usage':
        # A group with no use case ("No Location") sorts after every real one.
        return sorted(groups, key=lambda g: (
            site(g),
            USAGE_ORDER.get(g.get('usage'), len(USAGE_ORDER)),
            g['name'].lower(),
        ))
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
    def show_all_rows(self):
        """Whether the page renders every horse rather than the first
        ROW_CAP of them."""
        return self.request.GET.get('all') == '1'

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
    def group_sort_options(self):
        return GROUP_SORT_OPTIONS_BY_AXIS.get(
            self.sort_context, GROUP_SORT_OPTIONS,
        )

    @property
    def group_sort(self):
        default = GROUP_SORT_DEFAULT.get(self.sort_context, DEFAULT_SORT)
        value = self.request.GET.get('gsort', default)
        return value if value in self.group_sort_options else default

    @property
    def shows_use_filter(self):
        return self.group_kind in AXES_WITH_USE_FILTER

    @property
    def use_filter(self):
        """The use cases the location groups are limited to.

        ``?use=horses&use=mixed`` — one value per tick in the menu. Empty
        means no filter, which is the default. Unknown values are dropped
        rather than erroring, so a stale bookmark still shows the horses.
        """
        if not self.shows_use_filter:
            return []
        wanted = set(self.request.GET.getlist('use'))
        return [value for value in USAGE_ORDER if value in wanted]

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
                Q(registered_name__icontains=search) |
                Q(notes__icontains=search) |
                Q(placements__owner__name__icontains=search) |
                Q(placements__location__name__icontains=search)
            )
            # A number read off a chip scanner or a passport finds its horse.
            compact = re.sub(r'[\s\-]', '', search)
            if len(compact) >= 4 and any(ch.isdigit() for ch in compact):
                match |= (Q(microchip__icontains=compact) | Q(ueln__icontains=compact)
                          | Q(passport_number__icontains=compact))
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
        if settings.BARNS_ENABLED:
            # Each row can print its stable; subqueries, so no query a row.
            from ..stables import annotate_stable
            from ..turnout import annotate_turnout
            queryset = annotate_turnout(annotate_stable(queryset))
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
        # The toolbar rail: All, Location, Owner and (with the locations
        # feature) Movements. The rail stays on the Movements view so the
        # way back to the horse list is one click, not a hunt through a
        # menu. On that view the Movements option is the lit one; group_by
        # still reads 'all' there, so the rail cannot key off it alone.
        context['rail_active'] = (
            'movements' if self.shows_movements else self.group_by
        )
        # The Show block (Active / Departed / Movements) belongs on the
        # views that pick which list you see: All, Departed, a search, and
        # the log itself. A grouped axis only shapes the Active list, so
        # its menu holds the grouping controls alone.
        context['shows_switcher'] = (
            self.shows_movements
            or self.status == 'departed'
            or self.is_searching
            or self.group_by == 'all'
        )
        context['group_kind'] = self.group_kind
        # A list filtered to one location is that location, as far as the
        # dashboard's "Back to …" chip is concerned (plan 5.5).
        location_filter = self.request.GET.get('location', '')
        context['remember_location'] = (
            Location.objects.active().filter(pk=location_filter).values('pk', 'name').first()
            if location_filter.isdigit() else None
        )
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
            for key in self.group_sort_options
        ] if self.group_by in GROUPED_AXES else None
        context['shows_use_filter'] = self.shows_use_filter
        context['use_filter'] = self.use_filter
        # One tick per use case. Each row's href carries the set that
        # clicking it would leave behind: this value added or removed.
        # The querystring tag writes a list as repeated ``use=`` params
        # and drops the key for None.
        context['use_filter_options'] = [
            {
                'key': value,
                'label': label,
                'active': value in self.use_filter,
                'values': [
                    v for v in USAGE_ORDER
                    if (v in self.use_filter) != (v == value)
                ] or None,
            }
            for value, label in Location.Usage.choices
        ] if self.shows_use_filter else None
        # Location.objects.active() hides archived fields (from main).
        context['locations'] = Location.objects.active().order_by('site', 'name')
        context['owners'] = Owner.objects.active().values('pk', 'name').order_by('name')
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

        # Health warnings beside each name, from the dashboard's own rules,
        # and the "Needs action" filter (UX plan H1).
        if self.status == 'current' and not self.shows_movements:
            flags = _health_flags(self.request.user, horses)
            for h in horses:
                h.health_flag = flags.get(h.pk)
            context['needs_action_count'] = sum(1 for h in horses if h.health_flag)
            context['needs_action_only'] = self.request.GET.get('needs') == '1'
            if context['needs_action_only']:
                horses = [h for h in horses if h.health_flag]

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
            context.update(self._cap_rows(context['grouped_horses']))
        elif self.is_searching:
            # Search results are one flat, unpaginated list.
            context['horses'] = _sort_horses(horses, self.sort)
            context.update(self._cap_flat(context, 'horses'))

        if not self.shows_movements:
            # Rows link to the horse page with ?from=<this query>, and the
            # rows' order goes in the session for its previous / next.
            context['list_query'] = self.request.GET.urlencode()
            if 'grouped_horses' in context:
                shown = [h for group in context['grouped_horses'] for h in group['horses']]
            else:
                shown = list(context['horses'])
            _remember_list_order(self.request, shown)

        return context

    def _cap_rows(self, groups):
        """Stop the page growing without limit.

        Groups keep their order, their headings and their counts; it is
        the horses inside them that stop once the cap is reached. An
        empty group is not a row and is never trimmed — showing the space
        that is free is the whole point of the land axes.
        """
        total = sum(len(group['horses']) for group in groups)
        if self.show_all_rows or total <= ROW_CAP:
            return {'rows_total': total, 'rows_shown': total, 'rows_capped': False}
        shown = 0
        for group in groups:
            room = max(0, ROW_CAP - shown)
            kept = group['horses'][:room]
            group['hidden_here'] = len(group['horses']) - len(kept)
            group['horses'] = kept
            shown += len(kept)
        return {'rows_total': total, 'rows_shown': shown, 'rows_capped': True}

    def _cap_flat(self, context, key):
        """The same ceiling for the flat lists: search, and the log."""
        rows = context[key]
        total = len(rows)
        if self.show_all_rows or total <= ROW_CAP:
            return {'rows_total': total, 'rows_shown': total, 'rows_capped': False}
        context[key] = rows[:ROW_CAP]
        return {'rows_total': total, 'rows_shown': ROW_CAP, 'rows_capped': True}

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

        # The use-case filter limits which locations print, and with them
        # the horses standing on the locations it leaves out.
        use_filter = self.use_filter
        if use_filter:
            spine = spine.filter(usage__in=use_filter)

        # A list filtered to one location (the map badge, the location
        # card's "View horses") is that location's list: the other groups
        # would all be empty, and every empty location on every site
        # printed under a filter reads as the filter not working.
        location_filter = self.request.GET.get('location', '')
        if location_filter.isdigit():
            spine = spine.filter(pk=location_filter)

        # Stable order inside a barn, unless a sort was asked for.
        by_stable = 'sort' not in self.request.GET
        # Stabled horses that go out on a location by day are listed under
        # their barn; the location's own card says how many, so its ring
        # (which counts them) and the rows beneath it agree.
        out_by_day = {}
        if settings.BARNS_ENABLED:
            from ..turnout import turnout_counts
            out_by_day = turnout_counts()

        def with_turnout(group, location):
            out = out_by_day.get(location.pk, 0)
            if out:
                group['turnout_count'] = out
                group['ring_count'] = group['count'] + out
                if group['capacity'] is not None:
                    group['availability'] = group['capacity'] - group['ring_count']
            return group

        for location in spine:
            groups.append(with_turnout(
                _location_group(location, by_location.pop(location.pk, []), by_stable),
                location,
            ))

        # A horse on an archived field still has to appear somewhere.
        leftover = Location.objects.filter(pk__in=by_location)
        if use_filter:
            leftover = leftover.filter(usage__in=use_filter)
        for location in leftover:
            groups.append(_location_group(location, by_location[location.pk], by_stable))
        groups.sort(key=lambda g: ((g['site'] or '').lower(),
                                   g['name'].lower()))

        # A horse with no location has no use case, so it has no place
        # in a list limited to one.
        if unplaced and not use_filter:
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
            spine = Owner.objects.active().order_by('name')
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


def _stable_stays(horse, limit):
    """The horse's recent stable stays for its timeline; none with barns off."""
    if not settings.BARNS_ENABLED:
        return []
    return list(
        horse.stable_stays.select_related('stable', 'stable__location')
        .order_by('-start_date', '-pk')[:limit]
    )


def build_horse_timeline(
    *, placements, vaccinations, farrier_visits, worming_treatments,
    egg_counts, vet_visits, breeding_records=(), farrier_notes=(),
    stable_stays=(), activities=(), conditions=(), status_changes=(),
):
    """One list of dated events, newest first.

    The horse page and the list's preview pane both print a timeline. They
    build it from the same records, so it is assembled once here — two
    copies of this drift the moment a record type is added.
    """
    timeline = []
    for p in placements:
        timeline.append({'type': 'placement', 'date': p.start_date, 'obj': p})
    for v in vaccinations:
        timeline.append({'type': 'vaccination', 'date': v.date_given, 'obj': v})
    for f in farrier_visits:
        timeline.append({'type': 'farrier', 'date': f.date, 'obj': f})
    for w in worming_treatments:
        timeline.append({'type': 'worming', 'date': w.date, 'obj': w})
    for ec in egg_counts:
        timeline.append({'type': 'egg_count', 'date': ec.date, 'obj': ec})
    for v in vet_visits:
        timeline.append({'type': 'vet_visit', 'date': v.date, 'obj': v})
    for br in breeding_records:
        timeline.append({'type': 'breeding', 'date': br.date_covered, 'obj': br})
    for note in farrier_notes:
        timeline.append({'type': 'farrier_note', 'date': note.date, 'obj': note})
    # A horse going into a stable is its own row, never folded into the
    # placement's: it changes on its own, with no move and no new rate.
    for stay in stable_stays:
        timeline.append({'type': 'stable', 'date': stay.start_date, 'obj': stay})
    # Training: Done and Did not work only, already filtered and capped by
    # training.horse.timeline_activities (it checks the role and the switch).
    for activity in activities:
        timeline.append({'type': 'training', 'date': activity.date, 'obj': activity})
    # An injury is a condition, and often a change to Box rest or Resting.
    # Both are dated events, so both are on the timeline (training board
    # redesign plan, 9.5). A condition with no diagnosis date shows on the
    # day it was recorded. The status changes come from
    # training.horse.timeline_status_changes (it checks the role and the switch).
    for condition in conditions:
        day = condition.diagnosed_date or timezone.localtime(condition.created_at).date()
        timeline.append({'type': 'condition', 'date': day, 'obj': condition})
    for change in status_changes:
        timeline.append({'type': 'training_status', 'date': change.date, 'obj': change})
    timeline.sort(key=lambda e: e['date'], reverse=True)
    # Only the newest record of a kind can be overdue: an old booster's next
    # due date is in the past for ever once the horse has had the next one.
    # 'attention' is what the timeline colours rust; nothing else is.
    seen = set()
    for event in timeline:
        obj = event['obj']
        event['attention'] = False
        if event['type'] not in ('vaccination', 'farrier', 'worming'):
            continue
        key = (event['type'], getattr(obj, 'vaccination_type_id', None))
        if key in seen:
            continue
        seen.add(key)
        event['attention'] = obj.is_overdue
    return timeline


# The horse list's rows in the order shown, kept in the session for the
# horse page's back link and previous / next (UX review E1). The list links
# to a horse with ?from=<the list's query>, and the horse page steps
# through the same rows while that query matches.
LIST_ORDER_KEY = 'horse_list_order'
LIST_ORDER_LIMIT = 500


def _remember_list_order(request, horses):
    ids = list(dict.fromkeys(h.pk for h in horses))[:LIST_ORDER_LIMIT]
    request.session[LIST_ORDER_KEY] = {'query': request.GET.urlencode(), 'ids': ids}


def list_from(request):
    """The query of the list the horse page was opened from, tidied, or ''."""
    from django.http import QueryDict

    raw = request.GET.get('from', '')
    return QueryDict(raw).urlencode() if raw else ''


def list_neighbours(request, horse, query):
    """``(previous, next, (place, total))`` inside the list the user came
    from, or None when the session does not hold that list (opened in
    another browser, or the list changed since)."""
    order = request.session.get(LIST_ORDER_KEY) or {}
    ids = order.get('ids') or []
    if not query or order.get('query') != query or horse.pk not in ids:
        return None
    i = ids.index(horse.pk)
    near_ids = [pk for pk in (ids[i - 1] if i else None, ids[i + 1] if i + 1 < len(ids) else None) if pk]
    near = {h.pk: h for h in Horse.objects.filter(pk__in=near_ids).only('pk', 'name')}
    previous = near.get(ids[i - 1]) if i else None
    following = near.get(ids[i + 1]) if i + 1 < len(ids) else None
    return previous, following, (i + 1, len(ids))


def horse_neighbours(horse):
    """``(previous, next)`` in the horse list's default order, name A to Z.

    A yard works through its horses one after another: this lets the horse
    page step to the next one without a trip back to the list. Horses on
    the yard step through horses on the yard, departed through departed.
    Either end is None.
    """
    from django.db.models import Q as _Q

    same = Horse.objects.filter(is_active=horse.is_active).only('pk', 'name')
    previous = same.filter(
        _Q(name__lt=horse.name) | _Q(name=horse.name, pk__lt=horse.pk)
    ).order_by('-name', '-pk').first()
    following = same.filter(
        _Q(name__gt=horse.name) | _Q(name=horse.name, pk__gt=horse.pk)
    ).order_by('name', 'pk').first()
    return previous, following


# Timeline filter pills: (key, label). A record of a kind not listed here
# filters with the kind in TIMELINE_FILTER_OF (a stable stay is a placement).
TIMELINE_FILTERS = (
    ('placement', 'Placements'), ('vaccination', 'Vaccinations'), ('farrier', 'Farrier'),
    ('vet_visit', 'Vet visits'), ('condition', 'Conditions'), ('breeding', 'Breeding'), ('worming', 'Worming'),
    ('training', 'Training'),
)
TIMELINE_FILTER_OF = {
    'stable': 'placement', 'egg_count': 'worming', 'farrier_note': 'farrier', 'training_status': 'training',
}


# How many Timeline entries show before "Show older" (UX review A6).
TIMELINE_PAGE = 10
# The Charges card shows this many, newest first, then "See all records".
CHARGES_ON_PAGE = 5


# What a screen reader hears for each kind: "Edit vet visit record, 3 Mar
# 2026", not "Edit vetvisit record" (UX review C10).
TIMELINE_LABELS = {
    'placement': 'placement', 'stable': 'stable', 'vaccination': 'vaccination',
    'farrier': 'farrier visit', 'farrier_note': 'farrier note', 'vet_visit': 'vet visit',
    'breeding': 'breeding', 'worming': 'worming', 'egg_count': 'egg count', 'training': 'training',
    'condition': 'condition', 'training_status': 'training status',
}


def timeline_filters(events):
    """Give each event its filter key, its readable label and its place in
    the list (``pos`` among all, ``kpos`` among its kind); return the pills
    for the kinds the horse has, each with its count, and whether anything
    is overdue."""
    counts = {}
    for pos, event in enumerate(events):
        key = TIMELINE_FILTER_OF.get(event['type'], event['type'])
        event['filter'] = key
        event['label'] = TIMELINE_LABELS.get(event['type'], event['type'].replace('_', ' '))
        event['pos'] = pos
        event['kpos'] = counts.get(key, 0)
        counts[key] = counts.get(key, 0) + 1
    pills = [(key, label, counts[key]) for key, label in TIMELINE_FILTERS if counts.get(key)]
    return pills, any(e.get('attention') for e in events)


def _timeline_capped(user, horse, context):
    """The kinds the Timeline shows only the newest of: a line at the end
    says so, with a link to the full list where there is one."""
    health = has_feature_access(user, 'health', LEVEL_VIEW)

    def link(name):
        return f"{reverse(name)}?horse={horse.pk}" if health and horse.is_active else ''

    caps = (
        ('placement', context['placements'], 10, 'Showing the last 10 placements.', ''),
        ('vaccination', context['vaccinations'], 10, 'Showing the last 10 vaccinations.',
         link('vaccination_list')),
        ('farrier', context['farrier_visits'], 10, 'Showing the last 10 farrier visits.', ''),
        ('vet_visit', context['vet_visits'], 10, 'Showing the last 10 vet visits.', link('vet_visit_list')),
        ('worming', context['worming_treatments'], 10, 'Showing the last 10 wormings.', link('worming_list')),
    )
    return [{'filter': key, 'text': text, 'url': url}
            for key, rows, cap, text, url in caps if len(rows) >= cap]


class HorseDetailView(FeatureAccessMixin, DetailView):
    feature = 'horses'
    access_level = LEVEL_VIEW
    model = Horse
    template_name = 'horses/horse_detail.html'
    context_object_name = 'horse'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        horse = self.object
        # Back and previous / next stay inside the list the user came from
        # (UX review E1); without one, the whole list, name A to Z.
        query = list_from(self.request)
        context['list_query'] = query
        in_list = list_neighbours(self.request, horse, query)
        if in_list:
            context['previous_horse'], context['next_horse'], context['list_place'] = in_list
        else:
            context['previous_horse'], context['next_horse'] = horse_neighbours(horse)
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
        # Charges card (UX review B7): the latest few, for roles that see money.
        if has_feature_access(self.request.user, 'charges', LEVEL_VIEW):
            context['extra_charges'] = list(
                horse.extra_charges.order_by('-date', '-pk')[:CHARGES_ON_PAGE])
            context['extra_charge_count'] = horse.extra_charges.count()
        context['ownership_shares'] = horse.ownership_shares.select_related('owner').all()
        context['photos'] = list(horse.photos.all()[:12])
        context['photo_count'] = horse.photos.count()
        # The photo the profile picture was made from gets a "Profile" mark
        # and no "Use as profile picture" (UX review B8).
        source = _profile_source(horse.photo.name if horse.photo else '', context['photos'])
        context['profile_photo_pk'] = source.pk if source else None
        # Any other horse with the same chip or UELN: a warning in the
        # banner's facts. The rest of the identity is in the pop-up
        # (horse_identity, "All details").
        context['identity_duplicates'] = horse.identity_duplicates()
        # Pedigree card and the "by … out of …" line (plan §8.3).
        from pedigree.display import diagram_context, record_for_page
        context['pedigree'] = diagram_context(record_for_page(horse), page_horse=horse)
        # New sections
        context['worming_treatments'] = horse.worming_treatments.all()[:10]
        context['egg_counts'] = horse.worm_egg_counts.all()[:10]
        # Open conditions first; resolved ones fold away (UX review B6).
        conditions = list(horse.medical_conditions.all())
        context['medical_conditions'] = sorted(
            (c for c in conditions if c.status != 'resolved'), key=lambda c: c.status != 'active')
        context['resolved_conditions'] = [c for c in conditions if c.status == 'resolved']
        context['vet_visits'] = horse.vet_visits.select_related('vet').all()[:10]
        # Breeding (mare only) — single query, filter active in Python. A
        # role without Breeding access sees none of it, timeline included.
        can_breeding = has_feature_access(self.request.user, 'breeding', LEVEL_VIEW)
        if horse.is_mare and can_breeding:
            breeding_records = list(
                horse.breeding_records.select_related('foal')
                .prefetch_related('coverings', 'scans', 'coverings__vet', 'scans__vet').all()
            )
            context['breeding_records'] = breeding_records
            # Every record but the open one, newest season first: the open
            # one has its own card above.
            context['breeding_history'] = [
                br for br in breeding_records if br.status not in ('covered', 'confirmed')
            ]
            context['active_pregnancy'] = next(
                (br for br in breeding_records if br.status in ('covered', 'confirmed')), None
            )
            context['foals'] = list(
                Horse.objects.filter(dam=horse)
                .prefetch_related('birth_record')
                .order_by('-date_of_birth', 'name')
            )
        else:
            context['foals'] = []

        # The foal's own record: its birth record, milestones and notes.
        foal_record = (
            horse.birth_record.select_related('mare', 'foal')
            .prefetch_related('foal_notes').first()
        ) if can_breeding else None
        context['foal_record'] = foal_record
        if foal_record is not None:
            from health.forms import FoalNoteForm
            from health.services import foal_milestones
            context['foal_milestones'] = foal_milestones(foal_record)
            context['foal_notes'] = list(foal_record.foal_notes.all())
            context['foal_note_form'] = FoalNoteForm(initial={'date': timezone.localdate()})

        # Weight and condition (health/measurements.py): only once there is
        # one; the Record card's Weight tile adds the first.
        context['measurements'] = None
        if has_feature_access(self.request.user, 'health', LEVEL_VIEW):
            from health.measurements import summary
            context['measurements'] = summary(horse)

        # Training and breeding are not in the Timeline: a role that may see
        # them has their own cards on this page (Training, In foal, Breeding
        # history), so the Timeline does not show them a second time (UX
        # review A3). A role that may not see them gets none of them anyway.
        # The Training card shows only once the horse has a training record
        # (done or did not work, today or earlier): the rows the Timeline
        # would list. A horse that has never trained gets no empty 28-day
        # strip; Record training stays in the Record card (21 Nov 2026).
        # Ask that first: the card costs eight queries, and most horses in
        # a livery yard have no training record (phone plan, item B6).
        context['show_training_card'] = bool(
            training_horse.timeline_activities(self.request.user, horse, 1)
        )
        context['training_card'] = (
            training_horse.training_card(self.request.user, horse)
            if context['show_training_card']
            else training_horse.training_actions(self.request.user)
        )
        context['timeline_events'] = build_horse_timeline(
            placements=context['placements'],
            vaccinations=context['vaccinations'],
            farrier_visits=context['farrier_visits'],
            worming_treatments=context['worming_treatments'],
            egg_counts=context['egg_counts'],
            vet_visits=context['vet_visits'],
            breeding_records=[],
            farrier_notes=list(horse.farrier_notes.select_related('provider')[:20]),
            stable_stays=_stable_stays(horse, 20),
            activities=[],
            conditions=conditions,
            status_changes=training_horse.timeline_status_changes(self.request.user, horse, 20),
        )
        context['timeline_filters'], context['timeline_overdue'] = timeline_filters(
            context['timeline_events'])
        context['timeline_page'] = TIMELINE_PAGE
        context['timeline_capped'] = _timeline_capped(self.request.user, horse, context)
        # Due (UX review A1): the same items as the dashboard and the list's
        # "Needs action" dot, for this horse. Only horses on the yard are due.
        due_flag = _health_flags(self.request.user).get(horse.pk) if horse.is_active else None
        for need in (due_flag or {}).get('items', []):
            # One main (rust) button on the page: the header's (UX review B2).
            if need['action'] is not None:
                need['action'].style = 'ghost'
        context['due_flag'] = due_flag
        # A departed horse says when and where it left (UX review A5).
        context['departure'] = None if horse.is_active else (
            horse.placements.filter(end_date__isnull=False).select_related('location')
            .order_by('-end_date', '-pk').first())
        context['placement_empty'] = _placement_empty(horse, context['departure'])
        # History (UX review E2): what changed on the horse, its extra
        # numbers and its pedigree record, who changed it and when. The
        # owner and invoice pages show the same panel.
        from pedigree.models import PedigreeHorse

        from .. import audit
        context['history_entries'] = audit.merge(
            audit.history_for(horse),
            audit.history_of(HorseIdentifier.history.filter(horse_id=horse.pk), 'Horse number or link'),
            audit.history_of(PedigreeHorse.history.filter(horse_id=horse.pk), 'Pedigree record'),
        )

        return context


def _placement_empty(horse, departure):
    """The title and line of the Placement card when the horse has no stay
    open (UX review A5, F8)."""
    from django.utils.dateformat import format as date_format

    if horse.is_active:
        return {'title': 'No current placement', 'body': f'{horse.name} is not at a location now.'}
    if departure is None:
        return {'title': 'Departed', 'body': ''}
    return {'title': f'Departed {date_format(departure.end_date, "j M Y")}',
            'body': f'Left {departure.location.name}.'}


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
        try:
            with transaction.atomic():
                self.object = form.save()
                ownership_formset.instance = self.object
                ownership_formset.save()
                # The open stay is billed to the Ownership card's primary owner,
                # as on the ownership screen (manage_ownership_shares).
                from ..services import PlacementService
                PlacementService.sync_placement_owner(self.object)
        except ValidationError as error:
            ownership_formset.report(error)
            return self.render_to_response(
                self.get_context_data(form=form, ownership_formset=ownership_formset)
            )
        _warn_if_incomplete_ownership(self.request, ownership_formset)
        messages.success(self.request, f"Horse '{self.object.name}' created successfully.")
        _warn_identity(self.request, self.object)
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
        try:
            with transaction.atomic():
                self.object = form.save()
                ownership_formset.instance = self.object
                ownership_formset.save()
                # The open stay is billed to the Ownership card's primary owner,
                # as on the ownership screen (manage_ownership_shares).
                from ..services import PlacementService
                PlacementService.sync_placement_owner(self.object)
        except ValidationError as error:
            ownership_formset.report(error)
            return self.render_to_response(
                self.get_context_data(form=form, ownership_formset=ownership_formset)
            )
        _warn_if_incomplete_ownership(self.request, ownership_formset)
        messages.success(self.request, f"Horse '{self.object.name}' updated successfully.")
        _warn_identity(self.request, self.object)
        return redirect(self.get_success_url())


def _warn_identity(request, horse):
    """Say what looks wrong with the numbers, after the save.

    A chip that is not 15 digits, or a chip or UELN that is also on
    another horse. These never block a save: old records can hold
    mistakes, and a returning horse can have two records.
    """
    for warning in horse.identity_warnings():
        messages.warning(request, warning)


@feature_required('horses')
def horse_identity_check(request):
    """The line under the microchip or UELN box while it is typed (UX
    review D1): the number in groups, whether its length is the usual
    one, and any other horse that has it. htmx GET from HorseForm; the
    ``horse`` being edited is left out of the "also on" check."""
    from ..identity import (
        UELN_LENGTH, format_ueln, group_digits, is_standard_microchip, microchip_hint,
        normalise_microchip, normalise_ueln,
    )

    if 'microchip' in request.GET:
        field, value = 'microchip', normalise_microchip(request.GET['microchip'][:60])
    elif 'ueln' in request.GET:
        field, value = 'ueln', normalise_ueln(request.GET['ueln'][:30])
    else:
        return HttpResponse('')
    if not value:
        return HttpResponse('')
    if field == 'microchip':
        ok = is_standard_microchip(value)
        shown = group_digits(value) if ok else value
        note = microchip_hint(value) if ok else ''
    else:
        ok = len(value) == UELN_LENGTH
        shown = format_ueln(value)
        note = ''
    others = Horse.objects.filter(**{field: value})
    exclude = request.GET.get('horse', '')
    if exclude.isdigit():
        others = others.exclude(pk=exclude)
    return render(request, 'horses/partials/identity_check.html', {
        'field': field, 'value': value, 'shown': shown, 'ok': ok, 'note': note,
        'length': len(value), 'others': list(others.order_by('-is_active', 'name')[:3]),
    })


@feature_required('horses')
def horse_scan_check(request, pk):
    """Scan to check: compare a chip scan with the stored number.

    Pop-up sheet only (a direct visit goes to the horse page). A match
    stamps the record "ID checked". A different number is shown beside
    the stored one; staff choose to save the scanned number (the old one
    is kept as an "Old microchip") or to cancel. With no number on record,
    the scan can be saved as the microchip.
    """
    from ..forms import ScanCheckForm
    from ..identity import group_digits, is_standard_microchip

    horse = get_object_or_404(Horse, pk=pk)
    if not is_popup_request(request):
        return redirect('horse_detail', pk=horse.pk)

    def shown(chip):
        return group_digits(chip) if is_standard_microchip(chip) else chip

    context = {'horse': horse, 'stored': horse.microchip, 'stored_shown': shown(horse.microchip)}
    if request.method != 'POST':
        context['form'] = ScanCheckForm()
        return render(request, 'horses/partials/scan_check.html', context)

    action = request.POST.get('action', 'check')
    if action == 'save' and request.POST.get('last_scanned'):
        # Save the number shown as "Scanned", not what is in the box to
        # scan again (that box is for Check again).
        form = ScanCheckForm({'scanned': request.POST['last_scanned']})
    else:
        form = ScanCheckForm(request.POST)
    context['form'] = form
    if not form.is_valid():
        return render(request, 'horses/partials/scan_check.html', context)

    scanned = form.cleaned_data['scanned']
    stored = horse.microchip

    if scanned == stored:
        horse.mark_identity_checked(request.user)
        horse.save(update_fields=['identity_checked_at', 'identity_checked_by', 'updated_at'])
        messages.success(request, f"The chip matches the record for {horse.name}. Marked as ID checked.")
        return popup_saved_response()

    if action == 'save':
        with transaction.atomic():
            if stored:
                HorseIdentifier.objects.create(
                    horse=horse, kind=HorseIdentifier.Kind.OLD_MICROCHIP, value=stored,
                    notes=f'Replaced by a scan on {timezone.localdate():%d %b %Y}.',
                )
            horse.microchip = scanned
            horse.mark_identity_checked(request.user)
            horse.save(update_fields=['microchip', 'identity_checked_at', 'identity_checked_by', 'updated_at'])
        messages.success(request, f"Saved the scanned microchip for {horse.name}. Marked as ID checked.")
        _warn_identity(request, horse)
        return popup_saved_response()

    context.update({'scanned': scanned, 'scanned_shown': shown(scanned), 'mismatch': True,
                    'form': ScanCheckForm()})
    return render(request, 'horses/partials/scan_check.html', context)


@feature_required('horses', LEVEL_VIEW)
def horse_identity(request, pk):
    """The horse's full identity: "All details" under the banner's facts
    opens it in the pop-up sheet (24 Nov 2026); a direct visit gets a page."""
    horse = get_object_or_404(Horse, pk=pk)
    context = {
        'horse': horse,
        'identifiers': list(horse.identifiers.all()),
        'identity_duplicates': horse.identity_duplicates(),
        'identity_checked_by': horse.identity_checked_by if horse.identity_checked_by_id else None,
    }
    template = ('horses/partials/identity_details.html' if is_popup_request(request)
                else 'horses/identity_page.html')
    return render(request, template, context)


@feature_required('horses', LEVEL_VIEW)
def horse_record_actions(request, pk):
    """Every Record tile in the pop-up sheet: the phone row's "All" (UX
    review A7). A direct visit goes to the horse page's Record card."""
    horse = get_object_or_404(Horse, pk=pk)
    if not is_popup_request(request):
        return redirect(reverse('horse_detail', args=[horse.pk]) + '#quick-actions')
    active_pregnancy = None
    if horse.is_mare and has_feature_access(request.user, 'breeding', LEVEL_VIEW):
        active_pregnancy = horse.breeding_records.filter(status__in=('covered', 'confirmed')).first()
    return render(request, 'horses/partials/record_actions.html', {
        'horse': horse, 'active_pregnancy': active_pregnancy,
        # The sheet only asks "may this role record training?".
        'training_card': training_horse.training_actions(request.user),
    })


def _identifier_page(request, horse, form, ident=None):
    template = ('horses/partials/identifier_form.html' if is_popup_request(request)
                else 'horses/identifier_form.html')
    return render(request, template, {
        'horse': horse, 'form': form, 'ident': ident,
        'issuer_suggestions': HorseIdentifier.ISSUER_SUGGESTIONS,
    })


@feature_required('horses')
def horse_identifier_add(request, pk):
    """Add a stud-book number, a Racing Post link, an old chip, …"""
    from ..forms import HorseIdentifierForm

    horse = get_object_or_404(Horse, pk=pk)
    ident = HorseIdentifier(horse=horse)
    if request.method == 'POST':
        form = HorseIdentifierForm(request.POST, instance=ident, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, f"Added {ident.label} to {horse.name}.")
            if is_popup_request(request):
                return popup_saved_response()
            return redirect('horse_detail', pk=horse.pk)
    else:
        form = HorseIdentifierForm(instance=ident, user=request.user)
    return _identifier_page(request, horse, form)


@feature_required('horses')
def horse_identifier_edit(request, pk, ident_pk):
    from ..forms import HorseIdentifierForm

    horse = get_object_or_404(Horse, pk=pk)
    ident = get_object_or_404(HorseIdentifier, pk=ident_pk, horse=horse)
    if request.method == 'POST':
        form = HorseIdentifierForm(request.POST, instance=ident, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, f"Updated {ident.label} for {horse.name}.")
            if is_popup_request(request):
                return popup_saved_response()
            return redirect('horse_detail', pk=horse.pk)
    else:
        form = HorseIdentifierForm(instance=ident, user=request.user)
    return _identifier_page(request, horse, form, ident)


@feature_required('horses')
@require_POST
def horse_identifier_delete(request, pk, ident_pk):
    """Remove a number at once, with Undo on the message (UX review B4)."""
    horse = get_object_or_404(Horse, pk=pk)
    ident = get_object_or_404(HorseIdentifier, pk=ident_pk, horse=horse)
    label = ident.label
    ident.delete()
    messages.success(
        request, f"Removed {label} from {horse.name}.",
        extra_tags=undo_tag(reverse('horse_identifier_restore', args=[horse.pk, ident_pk])),
    )
    return redirect('horse_detail', pk=horse.pk)


@feature_required('horses')
@require_POST
def horse_identifier_restore(request, pk, ident_pk):
    """Undo a remove. The history table keeps the removed row, so it comes
    back as it was: same id, same "checked" stamp, same added date."""
    horse = get_object_or_404(Horse, pk=pk)
    back = safe_next(request, reverse('horse_detail', args=[horse.pk]))
    if HorseIdentifier.objects.filter(pk=ident_pk).exists():
        return redirect(back)  # a second click on Undo: it is back already
    last = (HorseIdentifier.history.filter(id=ident_pk, horse_id=horse.pk)
            .order_by('-history_date', '-history_id').first())
    if last is None or last.history_type != '-':
        raise Http404
    ident = last.instance
    ident._change_reason = 'Undo remove'
    ident.save(force_insert=True)
    # auto_now_add stamped the restore time: put back when it was added.
    HorseIdentifier.objects.filter(pk=ident.pk).update(created_at=last.created_at)
    messages.success(request, f"{ident.label} is back on {horse.name}.")
    return redirect(back)


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
            _warn_identity(request, horse)
            return popup_saved_response()
    else:
        form = QuickHorseForm(instance=horse)

    return render(request, 'horses/partials/quick_edit_form.html', {
        'horse': horse,
        'form': form,
    })


@feature_required('horses', LEVEL_VIEW)
def horse_quick_view(request, pk):
    """A one-glance profile card for the pop-up sheet.

    The app bar search opens this first: photo, where the horse is, who
    owns it, colour, age, and the day-to-day actions (Move, Edit, Photos,
    Add record). The full profile is one tap away in the footer. Triggers
    keep their href on the horse page, so without JavaScript (or on a
    direct visit) this simply redirects there.
    """
    horse = get_object_or_404(Horse, pk=pk)
    if not is_popup_request(request):
        return redirect('horse_detail', pk=horse.pk)

    placement = (
        horse.placements.filter(end_date__isnull=True)
        .select_related('location', 'owner')
        .first()
    )
    return render(request, 'horses/partials/quick_view.html', {
        'horse': horse,
        'current_placement': placement,
        'owner': horse.current_owner,
    })


def horse_credential_rows(horse):
    """The Credentials block: identity papers, then what is next due.

    Vaccination rows are read from the horse's own records rather than a
    fixed Flu/Tetanus pair, so a yard running its own programme gets its
    own labels. Only the latest record per type carries a meaningful due
    date — an old booster is due forever — so each is filtered down to it.
    """
    today = timezone.localdate()

    def due_row(label, due, kind):
        return {
            'label': label,
            'value': due.strftime('%d %b %Y') if due else 'Not recorded',
            'tone': 'muted' if not due else ('alert' if due < today else 'plain'),
            # For preview_due(): the date itself and the kind of reminder.
            'due': due,
            'kind': kind,
        }

    rows = [
        {
            'label': 'Passport',
            'value': (horse.passport_number or 'On record') if horse.has_passport else 'No passport',
            'tone': 'plain' if horse.has_passport else 'alert',
        },
        {
            'label': 'Microchip',
            'value': horse.microchip_display or 'Not recorded',
            'tone': 'plain' if horse.microchip else 'muted',
        },
    ]
    if horse.ueln:
        rows.append({'label': 'UELN', 'value': horse.ueln_display, 'tone': 'plain'})

    for vaccination in current_vaccinations(
        horse.vaccinations.select_related('vaccination_type')
    ):
        rows.append(due_row(vaccination.vaccination_type.name, vaccination.next_due_date, 'vaccination'))

    farrier = current_farrier_visits(horse.farrier_visits.all()).first()
    rows.append(due_row('Farrier', farrier.next_due_date if farrier else None, 'farrier'))

    worming = current_worming_treatments(horse.worming_treatments.all()).first()
    rows.append(due_row('Worming', worming.next_due_date if worming else None, 'worming'))

    return rows


def preview_due(rows, user, today=None):
    """The preview's line under the name: the first thing that is overdue
    or due in the next fortnight, and how many more there are.

    ``{'label', 'due_date', 'severity': 'overdue' | 'due', 'more'}`` or
    None. It reads the rows of ``horse_credential_rows``, which the
    preview has loaded already: the pane opens once for each horse as
    someone goes down the list, so the line must not cost a query of its
    own. (The list's dot and the horse page use ``_health_flags``, which
    reads every horse.) A kind of reminder the yard switched off in
    Settings is left out, as it is there.
    """
    from ..reminders import muted_collectors

    if not has_feature_access(user, 'health', LEVEL_VIEW):
        return None
    today = today or timezone.localdate()
    horizon = today + timedelta(days=HEALTH_FLAG_DAYS)
    due = [row for row in rows if row.get('due') and row['due'] <= horizon]
    if not due:
        return None
    muted = muted_collectors()
    due = sorted((row for row in due if row['kind'] not in muted), key=lambda row: row['due'])
    if not due:
        return None
    first = due[0]
    return {
        'label': first['label'],
        'due_date': first['due'],
        'severity': 'overdue' if first['due'] < today else 'due',
        'more': len(due) - 1,
    }


@feature_required('horses', LEVEL_VIEW)
def horse_preview(request, pk):
    """The horse list's preview pane, and its sheet on a phone.

    The same partial serves both: the pane is the sheet laid on its side.
    A direct visit (or no JavaScript) goes to the full page instead, so
    every row on the list stays an ordinary link.
    """
    htmx = getattr(request, 'htmx', None)
    if not htmx or htmx.target != 'horse-preview-body':
        return redirect('horse_detail', pk=pk)

    horse = get_object_or_404(
        Horse.objects.prefetch_related('ownership_shares__owner'), pk=pk
    )
    placement = (
        horse.placements.filter(end_date__isnull=True)
        .select_related('location', 'owner', 'rate_type')
        .first()
    )
    last_placement = None
    if placement is None:
        last_placement = (
            horse.placements.select_related('location').order_by('-start_date').first()
        )

    placements = list(
        horse.placements.select_related('owner', 'location', 'rate_type')[:8]
    )
    vaccinations = list(horse.vaccinations.select_related('vaccination_type')[:8])
    farrier_visits = list(horse.farrier_visits.select_related('service_provider')[:8])
    worming_treatments = list(horse.worming_treatments.all()[:8])
    egg_counts = list(horse.worm_egg_counts.all()[:8])
    vet_visits = list(horse.vet_visits.select_related('vet')[:8])
    breeding_records = (
        list(horse.breeding_records.all()[:8])
        if horse.is_mare and has_feature_access(request.user, 'breeding', LEVEL_VIEW) else []
    )

    timeline = build_horse_timeline(
        placements=placements,
        vaccinations=vaccinations,
        farrier_visits=farrier_visits,
        worming_treatments=worming_treatments,
        egg_counts=egg_counts,
        vet_visits=vet_visits,
        breeding_records=breeding_records,
        farrier_notes=list(horse.farrier_notes.select_related('provider')[:8]),
        stable_stays=_stable_stays(horse, 8),
        # 5, not 8: daily training must not push the vaccinations out of 12.
        activities=training_horse.timeline_activities(request.user, horse, 5),
    )

    credential_rows = horse_credential_rows(horse)
    return render(request, 'horses/partials/preview.html', {
        'horse': horse,
        'gallery': _preview_gallery(horse),
        'current_placement': placement,
        'last_placement': last_placement,
        'owner': horse.current_owner,
        'ownership_shares': list(horse.ownership_shares.all()),
        'credential_rows': credential_rows,
        'documents': list(horse.documents.all()[:6]),
        'timeline_events': timeline[:12],
        'has_training': training_horse.can_see_training(request.user),
        'today': timezone.localdate(),
        # What is due, in words, under the name.
        'due_line': preview_due(credential_rows, request.user) if horse.is_active else None,
    })


PREVIEW_GALLERY_LIMIT = 10


def _preview_gallery(horse):
    """The photos the preview pane shows: the profile photo first, then the
    horse's other photos, newest first, up to PREVIEW_GALLERY_LIMIT.

    Each entry has `src` (the upload itself, already capped at 2560px on
    upload, for the large photo and the full-screen viewer), `thumb` (the
    small square rendition for the strip, or the upload when there is none)
    and `title` / `caption` / `href` for the full-screen viewer, which takes
    the list as is (static/js/lightbox.js).
    """
    items = []
    if horse.photo:
        items.append({
            'src': horse.photo.url,
            'thumb': (horse.photo_thumb or horse.photo).url,
            'title': horse.name,
            'caption': 'Profile photo',
            'href': '',
        })
    # "Use as profile picture" copies a log photo onto Horse.photo, so the
    # same picture is in both places. Show it once, as the profile photo:
    # a horse with one photo is "1 photo", not "1 of 2".
    log = list(horse.photos.all()[:PREVIEW_GALLERY_LIMIT + 1])
    source = _profile_source(horse.photo.name, log) if horse.photo else None
    if source is not None:
        log = [photo for photo in log if photo.pk != source.pk]
    for photo in log[:PREVIEW_GALLERY_LIMIT - len(items)]:
        caption = photo.get_category_display()
        if photo.caption:
            caption = f'{caption}: {photo.caption}'
        items.append({
            'src': photo.image.url,
            'thumb': (photo.thumb or photo.image).url,
            'title': horse.name,
            'caption': caption,
            'href': '',
        })
    return items


# Django keeps an upload's name and, when the name is taken, adds "_" and
# seven random letters or digits (Storage.get_available_name).
_TAKEN_NAME_SUFFIX = re.compile(r'_[A-Za-z0-9]{7}$')
# The name core.views.photos._use_as_profile gives the copy it makes.
_PROFILE_FROM = re.compile(r'^profile-from-(\d+)(?:_[A-Za-z0-9]{7})?$')


def _profile_source(profile_name, log):
    """The photo in ``log`` (HorsePhoto list) that the profile photo
    ``profile_name`` was copied from by "Use as profile picture", or None.

    Copies made now are named ``profile-from-<pk>``. Older copies kept the
    log photo's own name, sometimes with Django's "_xxxxxxx" suffix, so
    they are matched by name: an exact match first, then without the
    suffix. Phones name every upload "image.jpg", so a name that fits more
    than one photo matches none: a duplicate left in is better than a photo
    hidden by mistake. Reads no files.
    """
    if not profile_name or not log:
        return None
    profile = PurePosixPath(profile_name)
    marked = _PROFILE_FROM.match(profile.stem)
    if marked:
        pk = int(marked.group(1))
        return next((photo for photo in log if photo.pk == pk), None)
    same_type = [
        photo for photo in log
        if PurePosixPath(photo.image.name).suffix.lower() == profile.suffix.lower()
    ]
    for stem in (profile.stem, _TAKEN_NAME_SUFFIX.sub('', profile.stem)):
        found = [photo for photo in same_type if PurePosixPath(photo.image.name).stem == stem]
        if len(found) == 1:
            return found[0]
        if found:
            return None
    return None


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


def _warn_wrong_rate(request, horse, placement):
    """Say so when a horse has just been left on a rate that belongs to
    the other type of location (core/rates.py). Never blocks the save: the
    person may mean it. The dashboard keeps showing it until it is right.
    """
    from ..rates import mismatch_text, rate_mismatch

    if rate_mismatch(placement.rate_type, placement.location):
        text = mismatch_text(placement.rate_type, placement.location)
        messages.warning(request, f"{horse.name}: {text[0].lower()}{text[1:]}. Check the rate.")


def _assign_chosen_stable(horse, form, day):
    """Put the horse in the stable picked on a move or arrival form.

    Called inside the transaction that wrote the placement. Returns a
    sentence for the success message ("" when no stable was picked).
    Raises ValidationError — a taken stable, a date that cannot be — for
    the caller to show on the form.
    """
    stable = form.cleaned_data.get('stable')
    if stable is None:
        return ''
    from ..stables import StableService

    StableService.assign(
        Horse.objects.get(pk=horse.pk), stable, day,
        share=bool(form.cleaned_data.get('share_stable')),
    )
    return f" It is in stable {stable.label}."


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
    next_url = safe_next(request, '')

    if request.method == 'POST':
        form = MoveHorseForm(request.POST, horse=horse)
        if form.is_valid():
            try:
                # One transaction: a stable that turns out to be taken
                # undoes the move rather than leaving it half done.
                with transaction.atomic():
                    new_placement = PlacementService.move_horse(
                        horse,
                        new_location=form.cleaned_data['new_location'],
                        move_date=form.cleaned_data['move_date'],
                        new_owner=form.cleaned_data['new_owner'],
                        new_rate_type=form.cleaned_data['new_rate_type'],
                        expected_departure=form.cleaned_data.get('expected_departure'),
                        notes=form.cleaned_data['notes'],
                    )
                    stable_note = _assign_chosen_stable(horse, form, form.cleaned_data['move_date'])
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
                rate_note = ''
                if current_placement and new_placement.rate_type_id != current_placement.rate_type_id:
                    rate_note = f" Rate changed to {new_placement.rate_type.name}."
                # Undo puts the horse back, as it does for a group
                # (core.views.locations.group_move_undo): a horse that
                # arrives in the wrong location today cannot be moved
                # again until tomorrow.
                undo = group_move_undo_url(request.user, [new_placement])
                messages.success(
                    request, f"{horse.name} moved successfully.{stable_note}{rate_note}",
                    extra_tags=undo_tag(undo) if undo else '')
                _warn_wrong_rate(request, horse, new_placement)
                _flash_superseded_trim(request, horse, new_placement)
                if in_popup:
                    return popup_saved_response()
                return redirect(next_url or 'horse_list')
    else:
        today = timezone.localdate()
        form = MoveHorseForm(horse=horse, initial={
            'move_date': today,
            # A move inside the yard does not change the day the horse
            # leaves it. Before, the box was empty and the date was lost
            # unless the person typed it again.
            'expected_departure': PlacementService.kept_departure(current_placement, today),
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
    """Create a new horse and place it at a location in one step.

    A returning horse keeps its old record (core/returning.py): the panel
    under the chip box offers "Bring it back", which logs the arrival on
    the old record with the placement typed here. A chip that is already
    on a horse stops the save once, so a second record is never made by
    accident; "Make a new record anyway" goes on.
    """
    from ..forms import CoOwnerFormSet, NewArrivalForm
    from ..returning import find_returning
    from ..services import PlacementService

    returning = None
    # Other owners, when the horse is split (Dev board, 21 Nov 2026).
    # A post without the rows' count (an older page, a script) has none.
    co_data = request.POST if request.method == 'POST' and 'co-TOTAL_FORMS' in request.POST else None
    co_owners = CoOwnerFormSet(co_data, prefix='co')
    if request.method == 'POST':
        data = request.POST
        back = None
        if request.POST.get('bring_back', '').isdigit():
            back = get_object_or_404(Horse, pk=request.POST['bring_back'])
            data = data.copy()
            if not data.get('name', '').strip():
                # Only the chip was scanned: the old record has the name.
                data['name'] = back.name
            if not data.get('owner') and back.primary_owner:
                data['owner'] = back.primary_owner.pk  # as Log Arrival does
        form = NewArrivalForm(data)
        if form.is_valid() and back is not None:
            done = _bring_back(request, form, back)
            if done is not None:
                return done
        elif form.is_valid() and _co_owner_shares(form, co_owners) is not None:
            returning = find_returning(microchip=form.cleaned_data.get('microchip'))
            if returning.chip_matches and not request.POST.get('new_record'):
                return render(request, 'horses/horse_new_arrival.html', {
                    'form': form, 'co_owners': co_owners, 'returning': returning, 'stopped': True,
                })
            returning = None
            shares = _co_owner_shares(form, co_owners)
            chose_stable = form.cleaned_data.get('stable') is not None
            try:
                # One transaction when a stable was picked: if it turns
                # out to be taken, the horse is not left created with no
                # explanation of where its stable went.
                with transaction.atomic():
                    horse, placement = PlacementService.create_new_arrival(
                        name=form.cleaned_data['name'],
                        sex=form.cleaned_data.get('sex') or '',
                        color=form.cleaned_data.get('color') or '',
                        date_of_birth=form.cleaned_data.get('date_of_birth'),
                        sire_name=form.cleaned_data.get('sire_name') or '',
                        passport_number=form.cleaned_data.get('passport_number') or '',
                        has_passport=form.cleaned_data.get('has_passport', False),
                        microchip=form.cleaned_data.get('microchip') or '',
                        owner=form.cleaned_data['owner'],
                        location=form.cleaned_data['location'],
                        rate_type=form.cleaned_data['rate_type'],
                        arrival_date=form.cleaned_data['arrival_date'],
                        expected_departure=form.cleaned_data.get('expected_departure'),
                        notes=form.cleaned_data.get('notes', ''),
                        shares=shares,
                    )
                    _assign_chosen_stable(horse, form, form.cleaned_data['arrival_date'])
            except ValidationError as e:
                if not chose_stable:
                    # As before this form had a stable on it.
                    raise
                form.add_error(None, e)
            else:
                messages.success(request, format_html(
                    '{} created and arrived at {}. <a href="{}?category=arrival" class="underline font-semibold">Add photos</a>',
                    horse.name,
                    placement.location.name,
                    reverse('horse_photo_add', args=[horse.pk]),
                ))
                _warn_wrong_rate(request, horse, placement)
                _warn_identity(request, horse)
                return redirect('horse_detail', pk=horse.pk)
    else:
        initial = {'arrival_date': timezone.localdate()}
        location_id = request.GET.get('location')
        if location_id:
            initial['location'] = location_id
        # "Arrive a horse here" on an empty stable fills both in.
        stable_id = request.GET.get('stable')
        if stable_id and stable_id.isdigit():
            initial['stable'] = stable_id
        form = NewArrivalForm(initial=initial)

    if request.method == 'POST':
        # The form came back with an error: keep the panel showing what
        # the chip and name found, as it did before the save.
        returning = _returning_from(request.POST)
    return render(request, 'horses/horse_new_arrival.html', {
        'form': form, 'co_owners': co_owners, 'returning': returning,
    })


def _co_owner_shares(form, co_owners):
    """The ownership rows for a new arrival: the main owner and any other
    owners with their shares, or None when the other owners have an error
    (the formset holds it)."""
    if co_owners.is_bound and not co_owners.is_valid():
        return None
    return co_owners.shares(form.cleaned_data['owner'], form.cleaned_data.get('owner_share'))


def _returning_from(params):
    """find_returning for the raw chip, name and foaling date in a GET or
    POST (the boxes may not be valid yet)."""
    from ..returning import find_returning

    dob = params.get('date_of_birth', '')
    try:
        dob = date.fromisoformat(dob) if dob else None
    except ValueError:
        dob = None
    return find_returning(microchip=params.get('microchip', '')[:60],
                          name=params.get('name', '')[:200], date_of_birth=dob)


def _bring_back(request, form, horse):
    """Log the arrival on the old record of a returning horse. Answers the
    redirect to its page, or None when the form shows an error instead."""
    from ..identity import normalise_microchip
    from ..services import PlacementService

    if horse.placements.filter(end_date__isnull=True).exists():
        form.add_error(None, f"{horse.name} is on the yard now. Use Move on its page to change where it is.")
        return None
    data = form.cleaned_data
    chip = normalise_microchip(data.get('microchip') or '')
    try:
        with transaction.atomic():
            placement = PlacementService.arrive_horse(
                horse,
                owner=data['owner'],
                location=data['location'],
                rate_type=data['rate_type'],
                arrival_date=data['arrival_date'],
                expected_departure=data.get('expected_departure'),
                notes=data.get('notes', ''),
            )
            _assign_chosen_stable(horse, form, data['arrival_date'])
            if chip and not horse.microchip:
                # Matched by name, and the chip was scanned: keep it.
                horse.microchip = chip
                horse._change_reason = 'Chip scanned when the horse came back'
                horse.save(update_fields=['microchip'])
    except ValidationError as e:
        form.add_error(None, e)
        return None
    except IntegrityError:
        form.add_error(None, "That change clashed with another update to the same horse. "
                             "Open its page and check the current placement.")
        return None
    messages.success(request, format_html(
        '{} is back at {}, on the old record with all its history. '
        '<a href="{}?category=arrival" class="underline font-semibold">Add photos</a>',
        horse.name, placement.location.name, reverse('horse_photo_add', args=[horse.pk]),
    ))
    _warn_wrong_rate(request, horse, placement)
    _warn_identity(request, horse)
    return redirect('horse_detail', pk=horse.pk)


@feature_required('horses')
def new_arrival_check(request):
    """The "Was this horse here before?" panel on New Arrival, while the
    chip, name and foaling date are typed (htmx, GET). Nothing found: an
    empty answer, so the panel's box closes (CSS :empty)."""
    returning = _returning_from(request.GET)
    if not returning.found:
        return HttpResponse('')
    return render(request, 'horses/partials/returning_panel.html', {'returning': returning})


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
                with transaction.atomic():
                    placement = PlacementService.arrive_horse(
                        horse,
                        owner=form.cleaned_data['owner'],
                        location=form.cleaned_data['location'],
                        rate_type=form.cleaned_data['rate_type'],
                        arrival_date=form.cleaned_data['arrival_date'],
                        expected_departure=form.cleaned_data.get('expected_departure'),
                        notes=form.cleaned_data['notes'],
                    )
                    _assign_chosen_stable(horse, form, form.cleaned_data['arrival_date'])
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
                _warn_wrong_rate(request, horse, placement)
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
    """Log a single horse departing (POST only).

    Answers a same-origin ``next`` so a departure logged from the horse
    list's preview pane comes back to the list, not to the horse page.
    """
    from ..services import PlacementService

    horse = get_object_or_404(Horse, pk=pk)
    back = safe_next(request, '') or reverse('horse_detail', kwargs={'pk': horse.pk})

    if request.method == 'POST':
        if not horse.current_placement:
            # A double-submit or a colleague's bulk departure closed it
            # first. Say so — a silent redirect looks like a dropped request
            # and invites another click.
            messages.error(
                request,
                f"{horse.name} has no current placement to depart from.",
            )
            return redirect(back)
        departure_date_str = request.POST.get('departure_date')
        if not departure_date_str:
            messages.error(request, "Departure date is required.")
            return redirect(back)

        from datetime import date
        try:
            departure_date = date.fromisoformat(departure_date_str)
        except ValueError:
            messages.error(request, "Invalid date format.")
            return redirect(back)

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
                # Undo re-opens the stay (UX review B4): a slip of the
                # finger costs one tap, not a new arrival.
                messages.success(
                    request, f"{horse.name} departed from {placement.location.name}.",
                    extra_tags=undo_tag(reverse('cancel_departure', args=[horse.pk])),
                )
        except ValidationError as e:
            # e.messages, not str(e): that printed "['...']".
            messages.error(request, ' '.join(e.messages))
        except IntegrityError:
            messages.error(
                request,
                "That change clashed with another update to the same horse "
                "— refresh and check the current placement before retrying.",
            )

    return redirect(back)


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
                f"{horse.name} has no current placement. Use Log arrival instead.",
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
    # The Undo on a "departed" message sends the page it was on.
    return redirect(safe_next(request, reverse('dashboard')))


@feature_required('horses')
@require_POST
def expected_departure_left(request, pk):
    """"Yes, it left" on a passed expected departure: the stay closes on
    the expected date and the horse is marked departed."""
    from django.utils.dateformat import format as date_format

    from ..services import PlacementService

    placement = get_object_or_404(Placement.objects.select_related('horse', 'location'), pk=pk)
    horse = placement.horse
    if placement.end_date is None and placement.expected_departure and placement.expected_departure <= timezone.localdate():
        try:
            PlacementService.depart_horse(horse, placement.expected_departure)
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        else:
            messages.success(request, format_html(
                '{} left {} on {}. <a href="{}" class="underline font-semibold">Open {}</a>',
                horse.name, placement.location.name, date_format(placement.expected_departure, 'j M Y'),
                reverse('horse_detail', args=[horse.pk]), horse.name))
    return _departure_answer(request)


@feature_required('horses')
@require_POST
def expected_departure_stays(request, pk):
    """"Still here" on a passed expected departure: the date is cleared,
    so the question stops. Change date sets a new one instead."""
    placement = get_object_or_404(Placement.objects.select_related('horse'), pk=pk)
    if placement.end_date is None and placement.expected_departure:
        placement.expected_departure = None
        placement.save()
        messages.success(request, f"{placement.horse.name} is still here. The expected departure date is cleared.")
    return _departure_answer(request)


def _departure_answer(request):
    if request.headers.get('HX-Request'):
        # As confirm_departure: nothing is swapped, and the page refreshes.
        return HttpResponse(status=204, headers={'HX-Trigger': 'popup:saved'})
    return redirect(safe_next(request, reverse('dashboard')))


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
    """Manage fractional ownership shares for a horse.

    The one place ownership is edited. The open stay follows the primary
    share on save, so livery is billed to whoever the Ownership card names.
    """
    from ..services import PlacementService

    horse = get_object_or_404(Horse, pk=pk)

    if request.method == 'POST':
        formset = OwnershipShareFormSet(request.POST, instance=horse)
        saved = False
        if formset.is_valid():
            try:
                with transaction.atomic():
                    formset.save()
                    billed_to = PlacementService.sync_placement_owner(horse)
                saved = True
            except ValidationError as error:
                formset.report(error)
        if saved:
            _warn_if_incomplete_ownership(request, formset)
            note = f"Ownership shares for {horse.name} updated."
            if billed_to is not None:
                note += f" The current stay is now billed to {billed_to.name}."
            messages.success(request, note)
            return redirect('horse_detail', pk=horse.pk)
    else:
        formset = OwnershipShareFormSet(instance=horse)

    return render(request, 'horses/horse_ownership.html', {
        'horse': horse,
        'formset': formset,
    })


# Every health kind the dashboard collects for a horse. A high egg count
# has no due date (it needs worming now), so it counts as due.
HEALTH_FLAG_KINDS = frozenset({'vaccination', 'farrier', 'worming', 'vet', 'egg_count'})
HEALTH_FLAG_DAYS = 14


def _health_flags(user, horses=()):
    """``{horse pk: flag}`` for horses with a health item overdue or due in
    the next fortnight, from the same collectors as the dashboard, so the
    list and the dashboard never disagree. A flag is ``{'severity':
    'overdue' | 'due', 'text': 'Vaccination overdue, …', 'items': [...]}``;
    overdue wins when a horse has both. ``items`` (earliest due first)
    feeds the Needs column of the "Needs action" view: ``detail``,
    ``due_date``, ``severity`` and the record ``action``. A fixed handful of queries, whatever the size of
    the list.
    """
    from ..dashboard import attention

    if not has_feature_access(user, 'health', LEVEL_VIEW):
        return {}
    # The list has each horse's open placement loaded already.
    placements = {}
    for h in horses:
        open_stays = getattr(h, 'active_placements', None) or []
        if open_stays and open_stays[0].location_id:
            loc = open_stays[0].location
            placements[h.pk] = (loc.name, loc.site)
    flags = {}
    # A kind the yard switched off in Settings puts no dot on a horse.
    from ..reminders import muted_collectors
    for item in attention.collect(
        user, kinds=set(HEALTH_FLAG_KINDS) - muted_collectors(),
        horizon_days=HEALTH_FLAG_DAYS,
        placements=placements,
    ):
        if item.horse_id is None:
            continue
        severity = item.severity
        if item.kind == 'egg_count':
            severity, line = 'due', 'High egg count'
        elif severity in ('overdue', 'due'):
            what = item.detail.split(' · ', 1)[0] if item.detail else item.kind
            line = f"{what} {'overdue' if severity == 'overdue' else 'due soon'}"
        else:
            continue
        flag = flags.setdefault(
            item.horse_id, {'severity': severity, 'lines': [], 'items': []})
        if severity == 'overdue':
            flag['severity'] = 'overdue'
        if line not in flag['lines']:
            flag['lines'].append(line)
        # What the Needs column shows: the dashboard's own wording ("Farrier ·
        # Full set"), when it was due, and its record button.
        flag['items'].append({
            'severity': severity,
            'detail': item.detail or line,
            'due_date': item.due_date,
            'action': item.actions[0] if item.actions else None,
        })
    for flag in flags.values():
        flag['text'] = '; '.join(flag['lines'])
        flag['items'].sort(key=lambda entry: entry['due_date'] or date.max)
    return flags

