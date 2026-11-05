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


def _display(model, field_name, value, cache=None) -> str:
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
        # ``cache`` holds names already looked up for this page.
        key = (f.related_model, value)
        if cache is not None and key in cache:
            return cache[key]
        related = f.related_model._default_manager.filter(pk=value).first()
        text = str(related) if related is not None else f'#{value}'
        if cache is not None:
            cache[key] = text
        return text
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


_UNSET = object()


def _changes(record, prev=_UNSET, cache=None) -> list[Change]:
    if record.history_type != '~':
        return []
    if prev is _UNSET:
        prev = record.prev_record
    if prev is None:
        return []
    model = record.instance_type
    # Every tracked field, not only form-editable ones: a void (voided_at,
    # void_reason) is set by code and is exactly what the trail must show.
    # (simple-history's diff_against compares editable fields only.)
    changes = []
    for f in record.tracked_fields:
        if f.name in _NOISE_FIELDS:
            continue
        old, new = getattr(prev, f.attname), getattr(record, f.attname)
        if old != new:
            changes.append(Change(
                _field_label(model, f.name),
                _display(model, f.name, old, cache), _display(model, f.name, new, cache),
            ))
    return changes


def _history_entry(record, category, label, prev=_UNSET, obj=None, cache=None) -> Entry | None:
    """``prev``, ``obj`` and ``cache`` come from ``_build`` for a whole
    page at once; without them each record looks them up itself."""
    if record.history_change_reason == BASELINE_REASON:
        return None
    if prev is _UNSET:
        prev = record.prev_record
    changes = _changes(record, prev, cache)
    if record.history_type == '~' and not changes and prev is not None:
        # Saved with nothing visible changed (an excluded field moved).
        return None
    user = record.history_user
    return Entry(
        when=record.history_date,
        who=_label(user) or 'System',
        what=f'{_VERBS.get(record.history_type, "Changed")} {label.lower()}',
        subject=_subject(record, obj),
        category=category,
        changes=changes,
    )


def _subject(record, obj=None) -> str:
    # ``history_object`` is the row as it was, built from the history copy
    # alone. ``instance`` also read the live row for excluded fields: one
    # query per line, for fields the name never uses.
    try:
        return str(obj if obj is not None else record.history_object)
    except Exception:
        return f'#{record.id}'


def _with_prev(history):
    """Annotate each history row with the id of the copy before it."""
    from django.db.models import OuterRef, Subquery

    return history.annotate(audit_prev_id=Subquery(
        history.model._default_manager.filter(
            id=OuterRef('id'), history_date__lt=OuterRef('history_date'),
        ).order_by('-history_date').values('history_id')[:1]
    ))


def _build(candidates, limit):
    """Entries for (record, category, label) candidates, newest first.

    Previous copies and the rows named in the subject are loaded per
    model in one query each, instead of up to four queries a line.
    Returns (entries, used) where ``used`` counts the candidates read.
    """
    from collections import defaultdict

    from django.db.models import prefetch_related_objects

    by_model = defaultdict(list)
    for record, _cat, _label in candidates:
        by_model[record.instance_type].append(record)
    prevs = {}
    objs = {}
    for model, records in by_model.items():
        prev_ids = [r.audit_prev_id for r in records if getattr(r, 'audit_prev_id', None)]
        if prev_ids:
            for p in model.history.filter(history_id__in=prev_ids):
                prevs[(model, p.history_id)] = p
        built = [(r, r.history_object) for r in records]
        foreign = [
            f.name for f in model._meta.concrete_fields
            if f.many_to_one and not f.name.startswith('history')
        ]
        if foreign:
            try:
                prefetch_related_objects([o for _r, o in built], *foreign)
            except Exception:
                # A row named in a deleted copy may be gone: str() then
                # loads (or fails) per line, as before.
                logger.debug('audit prefetch skipped for %s', model, exc_info=True)
        for r, o in built:
            objs[(model, r.history_id)] = o

    cache = {}
    entries = []
    used = 0
    for record, cat, label in candidates:
        if len(entries) == limit:
            break
        used += 1
        prev_id = getattr(record, 'audit_prev_id', _UNSET)
        model = record.instance_type
        # Keyed by model too: each history table numbers its rows from 1.
        prev = _UNSET if prev_id is _UNSET else prevs.get((model, prev_id))
        entry = _history_entry(record, cat, label, prev, objs.get((model, record.history_id)), cache)
        if entry is not None:
            entries.append(entry)
    return entries, used


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

    # Gather the newest rows of every source first, then build lines only
    # for the page: building every fetched row (51 a source) cost about
    # four queries a line for lines that were then thrown away.
    candidates = []  # (when, kind, item, category, label)
    if category in ('', 'account'):
        events = AuditEvent.objects.all()
        if before is not None:
            events = events.filter(created_at__lt=before)
        candidates += [(e.created_at, 'event', e, 'account', '') for e in events[:limit + 1]]
    for cat, label, path in TRACKED_MODELS:
        if category not in ('', cat):
            continue
        history = apps.get_model(path).history.exclude(
            history_change_reason=BASELINE_REASON,
        )
        if before is not None:
            history = history.filter(history_date__lt=before)
        history = _with_prev(history).select_related('history_user')
        candidates += [
            (rec.history_date, 'history', rec, cat, label)
            for rec in history.order_by('-history_date')[:limit + 1]
        ]
    candidates.sort(key=lambda c: c[0], reverse=True)

    entries: list[Entry] = []
    i = 0
    while len(entries) < limit and i < len(candidates):
        # Build the next run of history rows together, events one by one.
        if candidates[i][1] == 'event':
            entries.append(_event_entry(candidates[i][2]))
            i += 1
            continue
        j = i
        while j < len(candidates) and candidates[j][1] == 'history':
            j += 1
        built, used = _build(
            [(c[2], c[3], c[4]) for c in candidates[i:j]], limit - len(entries),
        )
        entries += built
        i += used
    more = i < len(candidates)
    return entries, (entries[-1].when if more and entries else None)


def history_of(history_qs, label, limit=50) -> list[Entry]:
    """Entries for a queryset of historical records, newest first."""
    records = _with_prev(history_qs).select_related('history_user').order_by('-history_date')[:limit]
    entries, _used = _build([(r, '', label) for r in records], limit)
    return entries


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
