"""Training rows for the horse page and the list's preview pane.

One place decides who sees them: none when Training is switched off or the
role has Training hidden (docs/TRAINING_MODULE_PLAN.md, 5.7). The horse page
shows to every role with Horses access, so this check cannot be left to the
template alone.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from core.permissions import LEVEL_FULL, LEVEL_VIEW, has_feature_access

from . import load
from .access import training_on
from .models import Activity, HorseTraining, LotMember, TrainingSettings

RECENT_DAYS = 14
COMING_UP = 5


def can_see_training(user):
    return training_on() and has_feature_access(user, 'training', LEVEL_VIEW)


def timeline_activities(user, horse, limit):
    """Done and Did not work, dated today or earlier, newest first."""
    if not can_see_training(user):
        return []
    return list(
        horse.activities.for_timeline().select_related('work_type').order_by('-date', '-created_at')[:limit]
    )


@dataclass
class StatusChange:
    """One change of a horse's training status, for a timeline."""
    date: object            # the day the status started
    status: str
    label: str
    notes: str = ''
    review_on: object = None
    stamp: object = None    # when it was saved: the order inside one day
    is_rest: bool = False   # Resting or Box rest


def status_changes(horse):
    """The horse's training status changes, oldest first, from the history
    of its settings row. Saves that keep the status (a new note, a corrected
    date, a review date) fold into the change they belong to, and show its
    last values. A first status of Not in training is not a change."""
    rows = (
        HorseTraining.history.filter(horse_id=horse.pk).exclude(history_type='-')
        .order_by('history_date', 'history_id')
        .values('status', 'status_since', 'notes', 'review_on', 'history_date')
    )
    changes = []
    for row in rows:
        if changes and changes[-1].status == row['status']:
            last = changes[-1]
            last.date, last.notes, last.review_on = row['status_since'], row['notes'], row['review_on']
            continue
        changes.append(StatusChange(
            date=row['status_since'], status=row['status'],
            label=HorseTraining.Status(row['status']).label, notes=row['notes'],
            review_on=row['review_on'], stamp=row['history_date'],
            is_rest=row['status'] in HorseTraining.ON_REST,
        ))
    if changes and changes[0].status == HorseTraining.Status.NOT_IN_TRAINING:
        changes = changes[1:]
    # The same status for the same day, saved two times (a change that was
    # undone and made again, or a date typed over another), is one entry:
    # the later one.
    seen, kept = set(), []
    for change in reversed(changes):
        if (change.status, change.date) not in seen:
            seen.add((change.status, change.date))
            kept.append(change)
    return kept[::-1]


def timeline_status_changes(user, horse, limit=None):
    """Status changes for the horse page's Timeline and the preview pane,
    newest first; none when the role cannot see training."""
    if not can_see_training(user):
        return []
    changes = status_changes(horse)[::-1]
    return changes[:limit] if limit else changes


def training_actions(user):
    """What the horse page needs when it does not show the Training card.

    The page still asks one thing of it: may this role record and change
    training (the Record card, the Timeline's Edit link). ``None`` when the
    role cannot see training, as ``training_card``. It runs no query; the
    card runs eight.
    """
    if not can_see_training(user):
        return None
    return {'can_change': has_feature_access(user, 'training', LEVEL_FULL)}


def training_card(user, horse):
    """The horse page's Training card, or None when the role cannot see it."""
    if not can_see_training(user):
        return None
    today = timezone.localdate()
    # Rows show the rider and the venue: join them, not one query a row.
    rows = horse.activities.select_related('work_type', 'rider', 'venue')
    # Read only: a horse with no row is Not in training (5.3).
    status_row = HorseTraining.objects.filter(horse=horse).first()
    member = LotMember.objects.filter(horse=horse).select_related('lot', 'lot__usual_work_type').first()
    return {
        'status': status_row.get_status_display() if status_row else HorseTraining.Status.NOT_IN_TRAINING.label,
        'status_value': status_row.status if status_row else HorseTraining.Status.NOT_IN_TRAINING,
        'status_since': status_row.status_since if status_row else None,
        'status_notes': status_row.notes if status_row else '',
        'lot': member.lot if member else None,
        'today_row': rows.filter(date=today, lot_day__isnull=False).select_related('lot_day__lot').first(),
        **_load_facts(horse, today, status_row),
        'coming_up': list(rows.filter(date__gt=today).order_by('date', 'pk')[:COMING_UP]),
        'recent': list(
            rows.filter(date__lte=today, date__gt=today - timedelta(days=RECENT_DAYS))
            .order_by('-date', '-created_at')
        ),
        'not_confirmed': rows.filter(status=Activity.Status.PLANNED, date__lt=today).count(),
        # Board redesign, C3: the newest note about how the horse is going.
        'latest_note': horse.training_notes.select_related('created_by').first(),
        'can_change': has_feature_access(user, 'training', LEVEL_FULL),
        'reasons': Activity.Reason.choices,
        'days': RECENT_DAYS,
    }


def _load_facts(horse, today, status_row):
    """The workload chips and the 28-day strip (plan 9.5)."""
    rows = load.rows_for([horse.pk], today)[horse.pk]
    on_board = bool(status_row and status_row.status in HorseTraining.ON_BOARD)
    return {
        'chips': load.chips(
            rows, today, threshold=TrainingSettings.load().no_work_days,
            days_per_week=status_row.days_per_week if status_row else None,
            on_board=on_board, since=status_row.status_since if status_row else None,
        ),
        'strip': load.strip(rows, today),
        'days_per_week': status_row.days_per_week if status_row else None,
    }
