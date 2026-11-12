"""Home dashboard views.

The page is six zones (see ``core.dashboard_widgets``). The first response
carries the headline, the Needs action inbox, the Next 14 days strip, the
Yard board and the In foal block; Money and What changed load afterwards
through their own endpoints so the page paints fast. The data comes from
``core.dashboard``; these views only assemble it.
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..dashboard import activity as activity_data
from ..dashboard import attention, board, breeding, dismissals
from ..dashboard import money as money_data
from ..dashboard import upcoming as upcoming_data
from ..models import DashboardPreference, Horse, Location, Owner, SiteSettings
from ..permissions import feature_required, has_feature_access
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


def near_you_payload(request):
    """The data the nearest-location chip needs, or None when it is off.

    Every active location with a point, every site centre, and the URLs
    the chip links to. Emitted with ``json_script``; the browser does the
    distance maths (static/js/geo.js), so there is no endpoint and no
    fetch. Two queries, only with the feature on and Locations visible.
    """
    if not settings.LOCATION_MAPS_ENABLED:
        return None
    if not has_feature_access(request.user, 'locations'):
        return None
    active = Location.objects.active()
    locations = [
        {'pk': pk, 'name': name, 'site': site, 'lat': float(lat), 'lng': float(lng)}
        for pk, name, site, lat, lng in active.filter(latitude__isnull=False)
        .order_by('site', 'name').values_list('pk', 'name', 'site', 'latitude', 'longitude')
    ]
    counts = {}
    all_pks = []
    for pk, site in active.values_list('pk', 'site'):
        all_pks.append(pk)
        counts[site] = counts.get(site, 0) + 1
    sites = [
        {
            'name': row.site,
            'lat': float(row.latitude) if row.latitude is not None else None,
            'lng': float(row.longitude) if row.longitude is not None else None,
            'radius_m': row.radius_m,
            'count': counts.get(row.site, 0),
        }
        for row in SiteSettings.objects.exclude(site='').order_by('site')
        if row.site in counts
    ]
    return {
        'locations': locations,
        'sites': sites,
        'all_pks': all_pks,
        'near_radius_m': settings.LOCATION_NEAR_RADIUS_M,
        'urls': {'horses': reverse('horse_list'), 'dashboard': reverse('dashboard')},
    }


def near_you_card(request, pref, sites, payload):
    """Everything the Near you card needs, or None when it cannot show.

    The card is one slot filled by two independent ladders (plan 6.7)
    that the browser runs, because the first rung of each is GPS. The
    server settles what it can: the dashboard's chosen site as the
    default (failing that the yard's ``DEFAULT_MAP_SITE``, for someone
    GPS does not place on a site), the only site when there is one, the pinned location, and
    a map payload per site (``map_locations`` — the same shaping as the
    Map tab). With no site to fall back on and no GPS the card hides.
    """
    if payload is None:
        return None
    if not sites:
        return None
    default_site = pref.site if pref.site in sites else board.default_map_site(sites)
    pinned = pref.pinned_location
    if pinned is not None and (pinned.is_archived or pinned.site not in sites):
        pinned = None
    maps = {name: data for name, data in board.map_locations_by_site().items() if name in sites}
    return {
        'locations': payload['locations'],
        'sites': payload['sites'],
        'near_radius_m': payload['near_radius_m'],
        'site_names': sites,
        'default_site': default_site,
        'site_counts': {name: maps[name]['total'] for name in sites},
        'unlocated': [
            {'pk': loc['pk'], 'site': name}
            for name in sites for loc in maps[name]['locations'] if not loc['kind']
        ],
        'pinned': {'pk': pinned.pk, 'name': pinned.name, 'site': pinned.site} if pinned else None,
        # True when the site ladder settles without GPS (a chosen site, or one
        # site): the dashboard then lays out around the card from first paint
        # instead of waiting for the browser to say whether it shows.
        'shows_without_gps': bool(default_site) or len(sites) == 1,
        'urls': {'map': reverse('location_list')},
        'maps': maps,
    }


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
    # What an Administrator dismissed leaves the dashboard, for everyone.
    items, dismissed_count = dismissals.without_dismissed(items)
    inbox_items, upcoming_items = attention.split(items, today)

    rows = attention.rows(inbox_items, request.user) if 'attention' in visible else []
    summary = attention.summary(rows, upcoming_items, today)

    upcoming = None
    if 'upcoming' in visible:
        upcoming = upcoming_data.build(items, request.user, today)

    sites_overview = []
    if 'yard_board' in visible:
        flagged = {}
        for item in inbox_items:
            if item.location:
                what = item.detail.split(' · ', 1)[0] if item.detail else item.kind
                flagged.setdefault((item.site, item.location), []).append(
                    f'{item.title}: {what[:1].lower()}{what[1:]}' if item.title else what)
        sites_overview = board.sites_overview(site=site, today=today, flagged=flagged)

    in_foal = []
    if 'in_foal' in visible:
        in_foal = breeding.in_foal(request.user, site=site, today=today)

    departure_ids = [
        str(item.horse_id) for item in inbox_items
        if item.kind == 'departure' and item.horse_id is not None
    ]

    near_you = near_you_payload(request)
    near_you_card_data = None
    near_you_maps = {}
    if 'near_you' in visible:
        near_you_card_data = near_you_card(request, pref, sites, near_you)
        if near_you_card_data:
            # The maps are rendered by the compact partials, each with its
            # own json_script; keep them out of the card's JSON or every
            # parcel is sent twice.
            near_you_maps = near_you_card_data.pop('maps')

    context = {
        'greeting': _greeting(),
        'today': today,
        'headline': _headline(summary['things'], site, sites),
        'site': site,
        'sites': sites,
        'visible': visible,
        'rows': rows,
        'can_dismiss': dismissals.can_dismiss(request.user),
        'dismissed_count': dismissed_count,
        'summary': summary,
        'upcoming': upcoming,
        'sites_overview': sites_overview,
        'in_foal': in_foal,
        'departure_ids': departure_ids,
        'horse_count': sum(band['horses'] for band in sites_overview) if sites_overview else None,
        'near_you': near_you,
        'near_you_card': near_you_card_data,
        'near_you_maps': near_you_maps,
    }
    return render(request, 'dashboard.html', context)


@login_required
@require_POST
def attention_dismiss(request):
    """Dismiss Needs action items from the dashboard. Administrators only."""
    if not dismissals.can_dismiss(request.user):
        raise PermissionDenied
    made = dismissals.dismiss(request.POST.getlist('item'), request.user)
    if made:
        messages.success(
            request,
            "Dismissed. It no longer shows on the dashboard for anyone."
            if made == 1 else
            f"Dismissed {made} items. They no longer show on the dashboard for anyone.",
        )
    return redirect('dashboard')


@login_required
@require_POST
def attention_restore(request):
    """Bring every dismissed item back. Administrators only."""
    if not dismissals.can_dismiss(request.user):
        raise PermissionDenied
    if dismissals.restore_all():
        messages.success(request, "Dismissed items are back on the dashboard.")
    return redirect('dashboard')


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
    days = activity_data.recent(request.user, site=pref.site, today=today)
    return render(request, 'partials/dashboard/activity.html', {
        'days': days,
        'folded_count': _fold_activity(days),
        'site': pref.site,
    })


ACTIVITY_SHOWN = 8


def _fold_activity(days, shown=ACTIVITY_SHOWN):
    """Mark every event after the first ``shown`` as ``folded``, and a day
    as folded when all its events are. Returns how many are folded.

    The card shows the newest changes and a "Show all" button for the rest,
    so a busy week cannot make it far taller than the cards beside it
    (UX plan D4).
    """
    seen = 0
    for day in days:
        for event in day['events']:
            event.folded = seen >= shown
            seen += 1
        day['folded'] = all(event.folded for event in day['events'])
    return max(seen - shown, 0)


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
    """HTMX partial: typo-tolerant search across horses, owners and locations,
    plus invoices by number and health records by what they are about.

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
        # Archived owners last, and labelled: they used to come first and
        # push active owners out of the four places.
        owners = _find(
            Owner.objects.order_by('is_archived', 'name'), query, ('name',),
            ('pk', 'name', 'is_archived'),
            key=lambda o: o['is_archived'],
        )

    locations = []
    if has_feature_access(request.user, 'locations'):
        locations = _find(
            Location.objects.active(), query, ('name', 'site'),
            ('pk', 'name', 'site'),
        )

    has_digit = any(ch.isdigit() for ch in query)

    # A horse by the number on its passport or microchip: the vet reads a
    # chip and the yard needs to know whose it is. Four characters with a
    # digit, so a name search does not pay for it.
    if has_feature_access(request.user, 'horses') and has_digit and len(query) >= 4:
        seen = {h['pk'] for h in horses}
        compact = query.replace(' ', '')
        by_number = Horse.objects.filter(
            Q(microchip__icontains=compact) | Q(passport_number__icontains=compact)
        ).order_by('-is_active', 'name').values('pk', 'name', 'is_active', 'microchip')
        for row in by_number[:QUICK_FIND_PER_GROUP]:
            if row['pk'] in seen or len(horses) >= QUICK_FIND_PER_GROUP:
                continue
            row['matched'] = 'Microchip' if compact.lower() in (row.pop('microchip') or '').lower() else 'Passport'
            horses.append(row)

    # A horse by where it is stabled: "top barn 7" is how a yard says it.
    # The last word is the stable, the words before it the barn.
    if (
        settings.BARNS_ENABLED and ' ' in query
        and has_feature_access(request.user, 'horses')
        and has_feature_access(request.user, 'locations')
    ):
        from ..models import StableStay
        barn_words, _space, label = query.rpartition(' ')
        barn_words = barn_words.strip()
        if barn_words.lower().endswith(' stable'):
            barn_words = barn_words[:-len(' stable')].strip()
        if len(barn_words) >= 2 and label:
            seen = {h['pk'] for h in horses}
            stays = StableStay.objects.filter(
                end_date__isnull=True, horse__is_active=True,
                stable__label__iexact=label,
                stable__location__name__icontains=barn_words,
                stable__location__is_archived=False,
            ).select_related('horse', 'stable__location').order_by('horse__name')
            for stay in stays[:QUICK_FIND_PER_GROUP]:
                if stay.horse_id in seen or len(horses) >= QUICK_FIND_PER_GROUP:
                    continue
                horses.append({
                    'pk': stay.horse_id, 'name': stay.horse.name, 'is_active': True,
                    'matched': f'{stay.stable.location.name} · {stay.stable.label}',
                })

    # An invoice by its number. Numbers only: an owner's name already
    # finds the owner, whose page lists their invoices.
    invoices = []
    if has_digit and has_feature_access(request.user, 'invoices'):
        from invoicing.models import Invoice
        invoices = list(
            Invoice.objects.filter(invoice_number__icontains=query.replace(' ', ''))
            .order_by('-period_end', '-pk')
            .values('pk', 'invoice_number', 'owner__name', 'status')[:QUICK_FIND_PER_GROUP]
        )
        labels = dict(Invoice._meta.get_field('status').choices)
        for row in invoices:
            row['status_label'] = labels.get(row['status'], row['status'])

    # Health records by what they are about: a condition's name, or the
    # reason for a vet visit. Each opens the horse it belongs to.
    records = []
    if len(query) >= 3 and has_feature_access(request.user, 'health'):
        from health.models import MedicalCondition, VetVisit
        for row in (
            MedicalCondition.objects.filter(name__icontains=query, horse__is_active=True)
            .order_by('status', 'horse__name')
            .values('horse_id', 'horse__name', 'name')[:QUICK_FIND_PER_GROUP]
        ):
            records.append({'horse_pk': row['horse_id'], 'horse': row['horse__name'], 'what': row['name'], 'kind': 'Condition'})
        if len(records) < QUICK_FIND_PER_GROUP:
            for row in (
                VetVisit.objects.filter(reason__icontains=query, horse__is_active=True)
                .order_by('-date')
                .values('horse_id', 'horse__name', 'reason', 'date')[:QUICK_FIND_PER_GROUP - len(records)]
            ):
                records.append({
                    'horse_pk': row['horse_id'], 'horse': row['horse__name'], 'what': row['reason'],
                    'kind': 'Vet visit', 'date': row['date'],
                })

    return render(request, 'partials/dashboard/quick_find_results.html', {
        'query': query,
        'horses': horses,
        'owners': owners,
        'locations': locations,
        'invoices': invoices,
        'records': records,
        'has_results': bool(horses or owners or locations or invoices or records),
    })
