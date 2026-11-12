"""Dismissing Needs action items from the home dashboard.

An Administrator can dismiss an item for the whole yard. It leaves the
dashboard (the inbox, the headline count and the Next 14 days strip) and
nothing else: the Health and Invoices pages still list it, and reminder
emails still go out. A dismissed item with a date shows again when that
date changes; one with no date stays away until it is restored.
"""

from __future__ import annotations

from ..mfa import is_administrator

SEPARATOR = '|'


def can_dismiss(user) -> bool:
    return bool(getattr(user, 'is_authenticated', False)) and is_administrator(user)


def token(item) -> str:
    """What the dismiss form posts for one item: its key and its due date."""
    due = item.due_date.isoformat() if item.due_date else ''
    return f'{item.key}{SEPARATOR}{due}'


def _parse(value):
    key, _, due = str(value or '').rpartition(SEPARATOR)
    key = key.strip()
    due = due.strip()
    if not key or len(key) > 120 or len(due) > 10:
        return None
    return key, due


def _dismissed():
    from ..models import AttentionDismissal
    return set(AttentionDismissal.objects.values_list('key', 'due'))


def without_dismissed(items):
    """``(items still showing, how many were left out)``."""
    dismissed = _dismissed()
    if not dismissed:
        return items, 0
    kept = [
        item for item in items
        if (item.key, item.due_date.isoformat() if item.due_date else '') not in dismissed
    ]
    return kept, len(items) - len(kept)


def dismiss(tokens, user) -> int:
    """Dismiss each ``key|due`` in ``tokens``. Returns how many were new."""
    from ..models import AttentionDismissal
    made = 0
    for value in tokens:
        parsed = _parse(value)
        if parsed is None:
            continue
        key, due = parsed
        _, created = AttentionDismissal.objects.get_or_create(
            key=key, due=due, defaults={'dismissed_by': user},
        )
        made += int(created)
    return made


def restore_all() -> int:
    """Bring every dismissed item back. Returns how many rows went."""
    from ..models import AttentionDismissal
    deleted, _ = AttentionDismissal.objects.all().delete()
    return deleted
