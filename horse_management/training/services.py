"""Status changes for training records (docs/TRAINING_MODULE_PLAN.md, 5.12).

Each function locks the rows it changes, changes only rows that are still
in the status it changes from, and saves them with history. A double tap,
or two people at once, give the same result as one tap.
"""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone
from simple_history.utils import bulk_update_with_history

from .models import Activity

CHANGED_FIELDS = ['status', 'reason', 'updated_by', 'updated_at']


@dataclass
class MarkResult:
    changed: int = 0
    future: int = 0     # Planned, but dated after today: not changed
    other: int = 0      # Not Planned: not changed

    @property
    def skipped(self):
        return self.future + self.other


def _ids(activities):
    return [a.pk if isinstance(a, Activity) else int(a) for a in activities]


def _save(rows, user):
    if not rows:
        return
    now = timezone.now()
    for row in rows:
        row.updated_by = user
        row.updated_at = now
    bulk_update_with_history(rows, Activity, CHANGED_FIELDS, default_user=user)


@transaction.atomic
def mark_done(activities, *, user, today=None):
    """Planned → Done, for rows dated today or earlier."""
    today = today or timezone.localdate()
    ids = _ids(activities)
    rows = list(Activity.objects.select_for_update().filter(pk__in=ids))
    result = MarkResult()
    to_change = []
    for row in rows:
        if row.status != Activity.Status.PLANNED:
            result.other += 1
        elif row.date > today:
            result.future += 1
        else:
            row.status = Activity.Status.DONE
            row.reason = ''
            to_change.append(row)
    _save(to_change, user)
    result.changed = len(to_change)
    return result


@transaction.atomic
def mark_not_done(activities, reason, *, user):
    """Planned → Did not work, with a reason. Any date: a storm can be forecast."""
    if reason not in Activity.Reason.values:
        raise ValueError(f"Unknown reason: {reason!r}")
    ids = _ids(activities)
    rows = list(Activity.objects.select_for_update().filter(pk__in=ids))
    result = MarkResult()
    to_change = []
    for row in rows:
        if row.status != Activity.Status.PLANNED:
            result.other += 1
        else:
            row.status = Activity.Status.NOT_DONE
            row.reason = reason
            to_change.append(row)
    _save(to_change, user)
    result.changed = len(to_change)
    return result
