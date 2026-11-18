"""Make, sync and read notifications (notifications.models).

docs/SIDEBAR_AND_NOTIFICATIONS_PLAN.md, WP3.

Who gets a notification: every active user whose role can see the area it
is about (has_feature_access), who has not turned that category off. The
person who did the thing does not get told about it.

Event notifications (movements, money) are made from the code that does
the work, after the database commit, and never let a failure of their own
undo or break that work: an error here is logged and swallowed.

Needs-action notifications are a copy of the dashboard's attention list,
kept in step by ``sync_actions`` (hourly for everyone, and at most every
few minutes for a person who is using the app).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Notification, NotificationPreference

logger = logging.getLogger(__name__)

Category = Notification.Category

# Shown in the profile's Notifications tab, in this order.
CATEGORY_HELP = {
    Category.ACTION: 'Overdue and due-today jobs: vaccinations, farrier, worming, passports, documents, feed stock and more.',
    Category.MOVEMENT: 'A horse arrives, moves to another location, or leaves.',
    Category.MONEY: 'An invoice is paid or becomes overdue, or Xero cannot sync.',
}

# The area a person must be able to see to get each category. Needs-action
# rows are gated item by item by the attention collectors themselves.
CATEGORY_FEATURE = {
    Category.MOVEMENT: 'horses',
    Category.MONEY: 'invoices',
}

SYNC_EVERY_SECONDS = 5 * 60
# More events than this at once (a nightly sweep finding 40 overdue
# invoices) become one notification that counts them.
GROUP_OVER = 5


# ── Preferences ──────────────────────────────────────────────────────────────

def preferences(user):
    """``{category: (in_app, email)}`` with the defaults filled in."""
    stored = {
        p.category: (p.in_app, p.email)
        for p in NotificationPreference.objects.filter(user=user)
    }
    return {c: stored.get(c, (True, False)) for c in Category.values}


def preference_rows(user):
    """The rows of the profile's Notifications tab."""
    prefs = preferences(user)
    return [
        {
            'category': category,
            'label': Category(category).label,
            'help': CATEGORY_HELP[category],
            'in_app': prefs[category][0],
            'email': prefs[category][1],
        }
        for category in Category.values
        if _may_get(user, category)
    ]


def save_preferences(user, data):
    """Save the Notifications tab. ``data`` is the POST: a checkbox named
    ``<category>_in_app`` and ``<category>_email`` per category."""
    for category in Category.values:
        in_app = data.get(f'{category}_in_app') == 'on'
        email = data.get(f'{category}_email') == 'on'
        NotificationPreference.objects.update_or_create(
            user=user, category=category, defaults={'in_app': in_app, 'email': email},
        )
        if not in_app:
            # Turned off: what is there now goes too, and the badge drops.
            Notification.objects.filter(
                recipient=user, category=category, read_at__isnull=True,
            ).update(read_at=timezone.now())


def enabled_categories(user):
    prefs = preferences(user)
    return [c for c in Category.values if prefs[c][0]]


def _may_get(user, category):
    from core.permissions import has_feature_access
    feature = CATEGORY_FEATURE.get(category)
    if feature is None:
        return True
    return has_feature_access(user, feature)


# ── Reading ──────────────────────────────────────────────────────────────────

def visible(user):
    """This person's notifications for the categories they have on."""
    return Notification.objects.filter(recipient=user, category__in=enabled_categories(user))


def unread_count(user):
    return visible(user).filter(read_at__isnull=True, resolved_at__isnull=True).count()


def mark_read(user, ids=None):
    """Mark some (``ids``) or all of this person's notifications read."""
    rows = Notification.objects.filter(recipient=user, read_at__isnull=True)
    if ids is not None:
        rows = rows.filter(pk__in=ids)
    return rows.update(read_at=timezone.now())


# ── Making event notifications ───────────────────────────────────────────────

def _current_actor():
    """The person whose request is running, if any (simple_history keeps
    the request on a thread-local for its own history rows)."""
    try:
        from simple_history.models import HistoricalRecords
        request = getattr(HistoricalRecords.context, 'request', None)
    except Exception:
        return None
    user = getattr(request, 'user', None)
    return user if getattr(user, 'is_authenticated', False) else None


def _short_key(*parts):
    raw = ':'.join(str(p) for p in parts)
    if len(raw) <= 180:
        return raw
    return raw[:120] + ':' + hashlib.sha1(raw.encode()).hexdigest()


def _recipients(category, feature, actor):
    """``[(user, in_app, email)]`` for an event in ``category``."""
    from core.permissions import has_feature_access
    stored = {
        p.user_id: (p.in_app, p.email)
        for p in NotificationPreference.objects.filter(category=category)
    }
    people = []
    for user in get_user_model().objects.filter(is_active=True).order_by('pk'):
        if actor is not None and user.pk == actor.pk:
            continue
        if not has_feature_access(user, feature):
            continue
        in_app, email = stored.get(user.pk, (True, False))
        if in_app or email:
            people.append((user, in_app, email))
    return people


def notify(category, *, kind, title, body='', url='', dedupe_key, feature=None,
           tags='', actor=None):
    """Tell everyone who should know. Safe to call from any code path: it
    never raises. Returns how many notifications it made."""
    try:
        return _notify(category, kind=kind, title=title, body=body, url=url,
                       dedupe_key=dedupe_key, feature=feature or CATEGORY_FEATURE[category],
                       tags=tags or category, actor=actor)
    except Exception:
        logger.exception('Could not make the %s notification %r', category, dedupe_key)
        return 0


def _notify(category, *, kind, title, body, url, dedupe_key, feature, tags, actor):
    made = 0
    emails = []
    for user, in_app, email in _recipients(category, feature, actor):
        if in_app:
            try:
                with transaction.atomic():
                    _, created = Notification.objects.get_or_create(
                        recipient=user, dedupe_key=dedupe_key,
                        defaults={
                            'category': category, 'kind': kind, 'title': title[:200],
                            'body': body[:500], 'url': url[:500], 'tags': tags[:100],
                        },
                    )
            except IntegrityError:
                created = False
            made += int(created)
            if not created:
                continue
        if email and user.email:
            emails.append(user.pk)
    if emails:
        from .tasks import send_notification_email
        payload = {'title': title, 'body': body, 'url': url, 'category': category}
        transaction.on_commit(lambda: send_notification_email.delay(emails, payload))
    return made


def after_commit(func, *args, **kwargs):
    """Run a notify helper once the surrounding transaction commits, so a
    rolled-back change tells nobody. The actor is read now, while the
    request is still known."""
    kwargs.setdefault('actor', _current_actor())
    transaction.on_commit(lambda: func(*args, **kwargs))


# ── Horse movements ──────────────────────────────────────────────────────────

def _names(horses, limit=3):
    names = [h.name for h in horses]
    shown = ', '.join(names[:limit])
    rest = len(names) - limit
    return f'{shown} and {rest} more' if rest > 0 else shown


def horses_moved(event, horses, *, location=None, day=None, actor=None):
    """``event``: 'arrived', 'moved' or 'departed'."""
    from django.urls import reverse

    horses = [h for h in horses if h is not None]
    if not horses:
        return 0
    day = day or timezone.localdate()
    where = ''
    if location is not None:
        where = location.name + (f', {location.site}' if getattr(location, 'site', '') else '')
    verb = {'arrived': 'arrived', 'moved': 'moved', 'departed': 'left the yard'}[event]
    if len(horses) == 1:
        horse = horses[0]
        title = f'{horse.name} {verb}'
        url = reverse('horse_detail', args=[horse.pk])
    else:
        title = f'{len(horses)} horses {verb}'
        url = reverse('horse_list')
    if event == 'arrived':
        body = f'At {where}' if where else ''
    elif event == 'moved':
        body = f'To {where}' if where else ''
    else:
        body = f'From {where}' if where else ''
    if len(horses) > 1:
        body = f'{_names(horses)}' + (f' · {body}' if body else '')
    body += f' · {day.day} {day:%b}'
    ids = '-'.join(str(h.pk) for h in sorted(horses, key=lambda h: h.pk))
    key = _short_key('move', event, ids, getattr(location, 'pk', ''), day.isoformat())
    return notify(Category.MOVEMENT, kind=event, title=title, body=body, url=url,
                  dedupe_key=key, actor=actor)


# ── Money ────────────────────────────────────────────────────────────────────

def invoice_paid(invoice, actor=None):
    from django.urls import reverse
    from core.templatetags.ui_extras import gbp
    owner = getattr(invoice.owner, 'name', '')
    return notify(
        Category.MONEY, kind='invoice_paid',
        title=f'Invoice {invoice.invoice_number} paid',
        body=' · '.join(p for p in (owner, gbp(invoice.total)) if p),
        url=reverse('invoice_detail', args=[invoice.pk]),
        dedupe_key=_short_key('invoice-paid', invoice.pk, (invoice.paid_at or timezone.now()).date().isoformat()),
        actor=actor,
    )


def invoices_overdue(invoices, actor=None):
    from django.urls import reverse
    from core.templatetags.ui_extras import gbp
    invoices = list(invoices)
    if not invoices:
        return 0
    today = timezone.localdate()
    if len(invoices) > GROUP_OVER:
        total = sum((i.balance_due for i in invoices), start=0)
        return notify(
            Category.MONEY, kind='invoice_overdue',
            title=f'{len(invoices)} invoices are now overdue',
            body=f'{gbp(total)} owed',
            url=reverse('aged_debtors'),
            dedupe_key=_short_key('invoices-overdue', today.isoformat()),
            actor=actor,
        )
    made = 0
    for invoice in invoices:
        owner = getattr(invoice.owner, 'name', '')
        due = f'was due {invoice.due_date.day} {invoice.due_date:%b}' if invoice.due_date else ''
        made += notify(
            Category.MONEY, kind='invoice_overdue',
            title=f'Invoice {invoice.invoice_number} is overdue',
            body=' · '.join(p for p in (owner, gbp(invoice.balance_due), due) if p),
            url=reverse('invoice_detail', args=[invoice.pk]),
            dedupe_key=_short_key('invoice-overdue', invoice.pk),
            actor=actor,
        )
    return made


def xero_problem(what, detail='', actor=None):
    """Xero could not sync. One notification per kind of problem per day."""
    from django.urls import reverse
    return notify(
        Category.MONEY, kind='xero_problem',
        title=f'Xero: {what}',
        body=detail,
        url=reverse('app_settings') + '#set-integrations',
        dedupe_key=_short_key('xero', what, timezone.localdate().isoformat()),
        feature='xero',
        actor=actor,
    )


# ── Needs action ─────────────────────────────────────────────────────────────

def _tags_for(row):
    tags = [row.severity]
    for item in row.items:
        tags.append('yard' if item.category == 'documents' else item.category)
    return ' '.join(dict.fromkeys(tags))


def _line(item):
    return {
        'text': item.detail or item.label,
        'kind': item.kind,
        'severity': item.severity,
        'due': item.due_date.isoformat() if item.due_date else '',
        'delta': item.delta,
        'actions': [asdict(a) for a in item.actions],
    }


def _row_fields(row):
    from core.dashboard import dismissals
    if row.kind == 'visit':
        body = f'{row.subtitle} · {len(row.horses)} horses, one visit'
    else:
        parts = [row.subtitle] if row.subtitle else []
        parts += [item.detail or item.label for item in row.items]
        body = ' · '.join(p for p in parts if p)
    data = {
        'lines': [_line(item) for item in row.items] if row.kind != 'visit' else [_line(row.items[0])],
        'row_action': asdict(row.action) if row.action else None,
        'dismiss': [] if row.key == 'invoices-more' else [dismissals.token(i) for i in row.items],
        'horses': len(row.horses),
        'visit': row.kind == 'visit',
    }
    return {
        'kind': row.items[0].kind if row.items else 'action',
        'severity': row.severity,
        'tags': _tags_for(row)[:100],
        'title': row.title[:200],
        'body': body[:500],
        'url': (row.url or '')[:500],
        'due_date': row.due_date,
        'data': data,
    }


def _live_rows(user, today):
    from core.dashboard import attention, dismissals
    items = attention.collect(user, today=today)
    items, _ = dismissals.without_dismissed(items)
    inbox, _ = attention.split(items, today)
    return attention.rows(inbox, user)


def sync_actions(user, *, force=False, today=None):
    """Bring this person's needs-action notifications in step with the
    attention list. New rows are made unread; rows that left the list are
    closed. Runs at most every SYNC_EVERY_SECONDS unless ``force``.
    Returns True when it ran."""
    if not force and not cache.add(f'notif-sync:{user.pk}', 1, SYNC_EVERY_SECONDS):
        return False
    try:
        _sync_actions(user, today or timezone.localdate())
    except Exception:
        logger.exception('Needs-action sync failed for user %s', user.pk)
        return False
    return True


def _sync_actions(user, today):
    now = timezone.now()
    open_rows = {
        n.dedupe_key: n
        for n in Notification.objects.filter(
            recipient=user, category=Category.ACTION, resolved_at__isnull=True,
        )
    }
    in_app, _ = preferences(user)[Category.ACTION]
    live = {}
    if in_app:
        for row in _live_rows(user, today):
            due = row.due_date.isoformat() if row.due_date else ''
            live[_short_key('action', row.key, due)] = row

    for key, row in live.items():
        fields = _row_fields(row)
        existing = open_rows.get(key)
        if existing is None:
            # New, or back after it was closed (a restored dismissal).
            Notification.objects.update_or_create(
                recipient=user, dedupe_key=key,
                defaults={**fields, 'category': Category.ACTION, 'created_at': now,
                          'read_at': None, 'resolved_at': None},
            )
            continue
        changed = [name for name, value in fields.items() if getattr(existing, name) != value]
        if changed:
            for name in changed:
                setattr(existing, name, fields[name])
            existing.save(update_fields=changed)

    gone = [n.pk for key, n in open_rows.items() if key not in live]
    if gone:
        Notification.objects.filter(pk__in=gone).update(resolved_at=now)
        Notification.objects.filter(pk__in=gone, read_at__isnull=True).update(read_at=now)


def sync_everyone():
    """The hourly sync: every active person, whether or not they are in the
    app, so the badge is right when they open it and the morning email
    has what is new."""
    ran = 0
    for user in get_user_model().objects.filter(is_active=True):
        ran += int(sync_actions(user, force=True))
    return ran


def resync_soon(user=None):
    """Forget the throttle, so the next look syncs at once (after a
    dismissal, a restore, or a job done from the panel)."""
    if user is None:
        for pk in get_user_model().objects.filter(is_active=True).values_list('pk', flat=True):
            cache.delete(f'notif-sync:{pk}')
    else:
        cache.delete(f'notif-sync:{user.pk}')


# ── Clean-up ─────────────────────────────────────────────────────────────────

KEEP_READ_DAYS = 90


def purge_old(now=None):
    """Delete notifications read more than KEEP_READ_DAYS ago."""
    from datetime import timedelta
    cutoff = (now or timezone.now()) - timedelta(days=KEEP_READ_DAYS)
    deleted, _ = Notification.objects.filter(read_at__lt=cutoff).delete()
    return deleted
