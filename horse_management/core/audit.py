"""The audit trail: who changed what, and who did what to which account.

Two sources, read together:

* **Record history.** django-simple-history keeps a copy of a row each
  time it is saved or deleted, with the user who did it. Tracked:
  invoices, payments, owners, roles, role assignments and business
  settings (``TRACKED_MODELS``).
* **Account events.** ``AuditEvent`` rows for actions that are not a row
  changing: sign-in, two-step sign-in, lockouts, sessions, passwords.

``record`` writes an event. ``timeline`` and ``history_for`` read both
sources for the audit log page and the history panels.

Not tracked: changes made with ``QuerySet.update()`` (the overdue sweep,
reminder flags), which bypass ``save()``. Those are system bookkeeping
done by scheduled tasks, not by people.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from django.apps import apps

logger = logging.getLogger('audit')

# history_change_reason on the starting copy of each row
# (start_audit_trail). It is a baseline to compare with, not an action.
BASELINE_REASON = 'audit-trail-start'

# Fields never worth showing as a change.
_NOISE_FIELDS = {'created_at', 'updated_at', 'assigned_at'}


def _label(user) -> str:
    if user is None or not getattr(user, 'is_authenticated', False):
        return ''
    return user.email or user.get_username()


def _ip(request) -> str | None:
    if request is None:
        return None
    return request.META.get('REMOTE_ADDR') or None


def record(action, *, request=None, actor=None, target=None, detail='') -> None:
    """Write one account event, and the same line to the log.

    ``actor`` defaults to the signed-in user of ``request``. A failure to
    write is logged and swallowed: the action the user asked for has
    already happened, and refusing it now would not undo it.
    """
    from .models import AuditEvent

    if actor is None and request is not None:
        actor = getattr(request, 'user', None)
    if actor is not None and not getattr(actor, 'is_authenticated', False):
        actor = None
    try:
        AuditEvent.objects.create(
            action=action,
            actor=actor,
            actor_label=_label(actor),
            target_user=target,
            target_label=_label(target) if target is not None else '',
            detail=detail[:500],
            ip_address=_ip(request),
        )
    except Exception:
        logger.exception('Could not write audit event %s', action)
        return
    logger.info(
        'audit %s actor=%s target=%s %s',
        action,
        getattr(actor, 'pk', None),
        getattr(target, 'pk', None),
        detail,
    )


# ── Reading ──────────────────────────────────────────────────────────────────

# (category key, label, model path). The category is the filter on the
# audit log page.
TRACKED_MODELS = [
    ('invoices', 'Invoice', 'invoicing.Invoice'),
    ('payments', 'Payment', 'invoicing.Payment'),
    ('owners', 'Owner', 'core.Owner'),
    ('access', 'Role', 'core.Role'),
    ('access', 'Role assignment', 'core.UserRole'),
    ('settings', 'Business settings', 'core.BusinessSettings'),
]

CATEGORIES = [
    ('', 'Everything'),
    ('account', 'Sign-in and accounts'),
    ('invoices', 'Invoices'),
    ('payments', 'Payments'),
    ('owners', 'Owners'),
    ('access', 'Roles'),
    ('settings', 'Business settings'),
]

_VERBS = {'+': 'Created', '~': 'Changed', '-': 'Deleted'}


@dataclass
class Change:
    field: str
    old: str
    new: str


@dataclass
class Entry:
    """One line of the audit trail, from either source."""

    when: datetime
    who: str
    what: str
    subject: str
    category: str
    changes: list[Change] = field(default_factory=list)
    detail: str = ''
    url: str = ''


def _display(model, field_name, value) -> str:
    if value is None or value == '':
        return '—'
    try:
        f = model._meta.get_field(field_name)
    except Exception:
        return str(value)
    if f.choices:
        return str(dict(f.flatchoices).get(value, value))
    if f.is_relation:
        # Name the related row (a role, an owner) rather than its id. It
        # may since have been deleted, in which case the id is all we have.
        related = f.related_model._default_manager.filter(pk=value).first()
        return str(related) if related is not None else f'#{value}'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, dict):
        return ', '.join(f'{k}: {v}' for k, v in sorted(value.items())) or '—'
    text = str(value)
    return text if len(text) <= 120 else text[:117] + '…'


def _field_label(model, field_name) -> str:
    try:
        return str(model._meta.get_field(field_name).verbose_name).capitalize()
    except Exception:
        return field_name


def _changes(record) -> list[Change]:
    if record.history_type != '~':
        return []
    prev = record.prev_record
    if prev is None:
        return []
    model = record.instance_type
    delta = record.diff_against(prev)
    return [
        Change(_field_label(model, c.field), _display(model, c.field, c.old), _display(model, c.field, c.new))
        for c in delta.changes
        if c.field not in _NOISE_FIELDS
    ]


def _history_entry(record, category, label) -> Entry | None:
    if record.history_change_reason == BASELINE_REASON:
        return None
    changes = _changes(record)
    if record.history_type == '~' and not changes and record.prev_record is not None:
        # Saved with nothing visible changed (an excluded field moved).
        return None
    user = record.history_user
    return Entry(
        when=record.history_date,
        who=_label(user) or 'System',
        what=f'{_VERBS.get(record.history_type, "Changed")} {label.lower()}',
        subject=str(record.instance) if record.history_type != '-' else _deleted_label(record),
        category=category,
        changes=changes,
    )


def _deleted_label(record) -> str:
    try:
        return str(record.instance)
    except Exception:
        return f'#{record.id}'


def _event_entry(event) -> Entry:
    return Entry(
        when=event.created_at,
        who=event.actor_label or 'System',
        what=event.get_action_display(),
        subject=event.target_label,
        category='account',
        detail=event.detail,
    )


def timeline(category='', before=None, limit=50) -> tuple[list[Entry], datetime | None]:
    """The newest ``limit`` entries older than ``before``, all sources merged.

    Returns the entries and the cursor for the next page (``None`` when
    there is no more). Each source is asked for ``limit`` rows at most,
    so a page costs the same however long the trail grows.
    """
    from .models import AuditEvent

    entries: list[Entry] = []
    if category in ('', 'account'):
        events = AuditEvent.objects.all()
        if before is not None:
            events = events.filter(created_at__lt=before)
        entries += [_event_entry(e) for e in events[:limit + 1]]
    for cat, label, path in TRACKED_MODELS:
        if category not in ('', cat):
            continue
        history = apps.get_model(path).history.select_related('history_user').exclude(
            history_change_reason=BASELINE_REASON,
        )
        if before is not None:
            history = history.filter(history_date__lt=before)
        for rec in history.order_by('-history_date')[:limit + 1]:
            entry = _history_entry(rec, cat, label)
            if entry is not None:
                entries.append(entry)
    entries.sort(key=lambda e: e.when, reverse=True)
    page = entries[:limit]
    more = len(entries) > limit
    return page, (page[-1].when if more and page else None)


def history_of(history_qs, label, limit=50) -> list[Entry]:
    """Entries for a queryset of historical records, newest first."""
    records = history_qs.select_related('history_user').order_by('-history_date')[:limit]
    return [e for e in (_history_entry(r, '', label) for r in records) if e is not None]


def history_for(instance, limit=50) -> list[Entry]:
    """Change history of one tracked record, newest first."""
    label = next(
        (lbl for _, lbl, path in TRACKED_MODELS if apps.get_model(path) is type(instance)),
        type(instance)._meta.verbose_name,
    )
    return history_of(instance.history.all(), label, limit)


def merge(*entry_lists, limit=50) -> list[Entry]:
    entries = [e for entries in entry_lists for e in entries]
    entries.sort(key=lambda e: e.when, reverse=True)
    return entries[:limit]


def events_for_user(user, limit=50) -> list[Entry]:
    """Account events where ``user`` was the one acted on."""
    from .models import AuditEvent

    events = AuditEvent.objects.filter(target_user=user)[:limit]
    return [_event_entry(e) for e in events]
