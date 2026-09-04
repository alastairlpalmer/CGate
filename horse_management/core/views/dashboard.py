"""Home dashboard views.

The page is six zones (see ``core.dashboard_widgets``). The first response
carries the headline, the Needs action inbox, the Next 14 days strip, the
Yard board and the In foal block; Money and What changed load afterwards
through their own endpoints so the page paints fast. The data comes from
``core.dashboard``; these views only assemble it.
"""

import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from ..dashboard import activity as activity_data
from ..dashboard import attention, board, breeding
from ..dashboard import money as money_data
from ..dashboard import upcoming as upcoming_data
from ..models import DashboardPreference, Horse, Location, Owner
from ..permissions import feature_required
from ..search import is_fuzzy_match

logger = logging.getLogger(__name__)


def _greeting():
    """Time-of-day greeting for the dashboard subtitle."""
    hour = timezone.localtime().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def _headline(things, site, sites):
    """The page title is the yard's state, not a greeting."""
    if things == 0:
        if site:
            return f"All clear at {site}"
        return "All clear across the yard" if len(sites) > 1 else "All clear on the yard"
    if things == 1:
        return "1 thing needs doing"
    return f"{things} things need doing"


def _site_context(request, pref):
    """Resolve the site switch.

    ``?site=<name>`` saves the choice on the user's preference (blank means
    all sites); a name that no longer exists falls back to all. Returns
    ``(site, site_names)``.
    """
    names = board.site_names()
    if 'site' in request.GET:
        chosen = request.GET.get('site', '').strip()
        if chosen not in names:
            chosen = ''
        if chosen != pref.site:
            pref.site = chosen
            pref.save(update_fields=['site', 'updated_at'])
    site = pref.site if pref.site in names else ''
    return site, names


def _empty_context(request):
    """Safe zero-context used when the dashboard view errors out."""
    return {
        'greeting': _greeting(),
        'today': timezone.localdate(),
        'headline': 'Dashboard',
        'error': True,
        'site': '',
        'sites': [],
        'visible': set(),
        'rows': [],
        'summary': {'things': 0, 'overdue': 0, 'today': 0, 'upcoming': 0, 'money': 0, 'health': 0, 'yard': 0},
        'upcoming': None,
        'sites_overview': [],
        'in_foal': [],
        'departure_ids': [],
    }


@feature_required('dashboard')
def dashboard(request):
    """Main dashboard view."""
    try:
        return _dashboard_inner(request)
    except Exception:
        if settings.DEBUG:
            raise
        logger.exception("Dashboard error")
        return render(request, 'dashboard.html', _empty_context(request))


def _dashboard_inner(request):
    today = timezone.localdate()
    pref = DashboardPreference.get_for(request.user)
    visible = pref.visible_keys()
    site, sites = _site_context(request, pref)

    items = []
    if visible & {'attention', 'upcoming', 'yard_board'}:
        items = attention.collect(request.user, site=site, today=today)
    inbox_items, upcoming_items = attention.split(items, today)

    rows = attention.rows(inbox_items, request.user) if 'attention' in visible else []
    summary = attention.summary(rows, upcoming_items, today)

    upcoming = None
    if 'upcoming' in visible:
        upcoming = upcoming_data.build(items, request.user, today)

    sites_overview = []
    if 'yard_board' in visible:
        flagged = {(item.site, item.location) for item in inbox_items if item.location}
        sites_overview = board.sites_overview(site=site, today=today, flagged=flagged)

    in_foal = []
    if 'in_foal' in visible:
        in_foal = breeding.in_foal(request.user, site=site, today=today)

    departure_ids = [
        str(item.horse_id) for item in inbox_items
        if item.kind == 'departure' and item.horse_id is not None
    ]

    context = {
        'greeting': _greeting(),
        'today': today,
        'headline': _headline(summary['things'], site, sites),
        'site': site,
        'sites': sites,
        'visible': visible,
        'rows': rows,
        'summary': summary,
        'upcoming': upcoming,
        'sites_overview': sites_overview,
        'in_foal': in_foal,
        'departure_ids': departure_ids,
        'horse_count': sum(band['horses'] for band in sites_overview) if sites_overview else None,
    }
    return render(request, 'dashboard.html', context)


@feature_required('dashboard')
def dashboard_money(request):
    """HTMX partial: the month's billing position, loaded after first paint."""
    pref = DashboardPreference.get_for(request.user)
    if 'money' not in pref.visible_keys():
        return HttpResponse('')
    today = timezone.localdate()
    return render(request, 'partials/dashboard/money.html', {
        'money': money_data.snapshot(request.user, today=today),
        'today': today,
    })


@feature_required('dashboard')
def dashboard_activity(request):
    """HTMX partial: what changed, loaded after first paint."""
    pref = DashboardPreference.get_for(request.user)
    if 'activity' not in pref.visible_keys():
        return HttpResponse('')
    today = timezone.localdate()
    return render(request, 'partials/dashboard/activity.html', {
        'days': activity_data.recent(request.user, site=pref.site, today=today),
        'site': pref.site,
    })


# ── Quick find ──────────────────────────────────────────────────────────────

QUICK_FIND_MIN_CHARS = 2
QUICK_FIND_PER_GROUP = 4


def _find(queryset, query, fields, columns, key=None):
    """Rows of ``queryset`` matching ``query``, best effort then typo-tolerant.

    The database answers the common case: someone typing "bel" wants Bella,
    and SQL can find that without waking Python. Only when that comes up
    short do we scan every row with difflib — the path that catches
    "alihnter" for ALIHUNTER, and the one that costs real time once a yard
    has thousands of records.
    """
    def rows(qs):
        return [dict(zip(columns, values)) for values in qs.values_list(*columns)]

    exact = Q()
    for field in fields:
        exact |= Q(**{f'{field}__icontains': query})
    found = rows(queryset.filter(exact)[:QUICK_FIND_PER_GROUP * 3])

    # Fuzzy only rescues a query the database could not answer at all.
    # Falling back whenever there were merely "few" matches meant the scan
    # ran on nearly every keystroke, since most searches have one or two
    # hits — and a row containing what you typed beats one that resembles
    # it anyway.
    if not found:
        found = [
            row for row in rows(queryset)
            if any(is_fuzzy_match(query, row[field]) for field in fields)
        ]

    if key:
        found.sort(key=key)
    return found[:QUICK_FIND_PER_GROUP]


@login_required
def quick_find(request):
    """HTMX partial: typo-tolerant search across horses, owners and locations.

    Runs on every keystroke from the app bar, so the database does the
    obvious matching first and difflib only picks up what it missed — see
    ``_find``.

    Logging in is the only gate. It used to need the dashboard feature,
    from when this was the dashboard's own search — which left a role with
    the dashboard hidden looking at a search box in the app bar that
    silently did nothing. Each group below is gated on its own area, so
    nothing leaks either way.
    """
    query = request.GET.get('q', '').strip()
    if len(query) < QUICK_FIND_MIN_CHARS:
        return HttpResponse('')

    from ..permissions import has_feature_access

    # Include departed horses (labelled) — searching by name for a horse
    # that left last month should still find its record. Groups the user's
    # role can't view are skipped entirely so hidden areas don't leak here.
    horses = []
    if has_feature_access(request.user, 'horses'):
        horses = _find(
            Horse.objects.all(), query, ('name',),
            ('pk', 'name', 'is_active'),
            key=lambda h: not h['is_active'],  # active horses first
        )

    owners = []
    if has_feature_access(request.user, 'owners'):
        owners = _find(Owner.objects.all(), query, ('name',), ('pk', 'name'))

    locations = []
    if has_feature_access(request.user, 'locations'):
        locations = _find(
            Location.objects.active(), query, ('name', 'site'),
            ('pk', 'name', 'site'),
        )

    return render(request, 'partials/dashboard/quick_find_results.html', {
        'query': query,
        'horses': horses,
        'owners': owners,
        'locations': locations,
        'has_results': bool(horses or owners or locations),
    })
