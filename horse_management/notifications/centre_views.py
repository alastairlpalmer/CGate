"""The notification centre's views: the panel, the badge, read and open.

docs/SIDEBAR_AND_NOTIFICATIONS_PLAN.md, WP3. The panel is a drawer in
base.html that loads /notifications/panel/ into itself; the badge sits on
the sidebar's Notifications item and the phone's bell.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import centre
from .models import Notification

TABS = ('new', 'action', 'all')
CHIPS = ('all', 'overdue', 'health', 'yard', 'money', 'movement')
PANEL_LIMIT = 100


def _panel_context(request):
    from core.dashboard import dismissals
    from core.models import AttentionDismissal

    user = request.user
    tab = request.GET.get('tab') or request.POST.get('tab') or 'new'
    tab = tab if tab in TABS else 'new'
    chip = request.GET.get('chip') or request.POST.get('chip') or 'all'
    chip = chip if chip in CHIPS else 'all'

    base = centre.visible(user)
    unread = base.filter(read_at__isnull=True, resolved_at__isnull=True)
    open_actions = base.filter(category=Notification.Category.ACTION, resolved_at__isnull=True)
    counts = {'new': unread.count(), 'action': open_actions.count()}

    rows = {'new': unread, 'action': open_actions, 'all': base}[tab]
    if tab == 'action':
        # Most urgent first, as the dashboard list was.
        rows = rows.order_by('due_date', 'title')
    if chip != 'all':
        rows = rows.filter(tags__contains=chip)
    rows = list(rows[:PANEL_LIMIT])

    can_dismiss = dismissals.can_dismiss(user)
    # Horses that left and wait to be confirmed: one button for all of
    # them, as the dashboard's Needs action card had.
    departure_ids = []
    if tab == 'action':
        from core.permissions import has_feature_access
        if has_feature_access(user, 'horses', 'full'):
            departure_ids = [
                str(n.data.get('horse_id')) for n in rows
                if n.kind == 'departure' and n.resolved_at is None and n.data.get('horse_id')
            ]
    from django.utils import timezone
    return {
        'today': timezone.localdate(),
        'tab': tab,
        'chip': chip,
        'chips': [c for c in CHIPS if c != 'movement' or 'movement' in centre.enabled_categories(user)],
        'counts': counts,
        'notifications': rows,
        'can_dismiss': can_dismiss,
        'departure_ids': departure_ids,
        'dismissed_count': AttentionDismissal.objects.count() if can_dismiss else 0,
    }


def _panel(request, *, changed=False):
    response = render(request, 'notifications/_panel.html', _panel_context(request))
    if changed:
        response['HX-Trigger'] = 'notif:changed'
    return response


@login_required
def panel(request):
    centre.sync_actions(request.user, force=request.GET.get('fresh') == '1')
    return _panel(request)


@login_required
def badge(request):
    centre.sync_actions(request.user)
    return render(request, 'notifications/_badge.html', {
        'unread': centre.unread_count(request.user),
    })


@login_required
@require_POST
def read(request):
    """Mark one (``id``) or every notification read."""
    ids = request.POST.getlist('id')
    centre.mark_read(request.user, [int(i) for i in ids if i.isdigit()] if ids else None)
    return _panel(request, changed=True)


@login_required
def open_notification(request, pk):
    """Mark it read, then go where it points."""
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if notification.read_at is None:
        centre.mark_read(request.user, [notification.pk])
    url = notification.url
    if not url or not url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        url = '/'
    return redirect(url)


@login_required
@require_POST
def dismiss(request):
    """Dismiss a needs-action row for the whole yard (Administrators only),
    the same as the dashboard's Dismiss did."""
    from core.dashboard import dismissals
    if not dismissals.can_dismiss(request.user):
        raise PermissionDenied
    if dismissals.dismiss(request.POST.getlist('item'), request.user):
        centre.resync_soon()
        centre.sync_actions(request.user, force=True)
    return _panel(request, changed=True)


@login_required
@require_POST
def restore(request):
    from core.dashboard import dismissals
    if not dismissals.can_dismiss(request.user):
        raise PermissionDenied
    if dismissals.restore_all():
        centre.resync_soon()
        centre.sync_actions(request.user, force=True)
    return _panel(request, changed=True)

