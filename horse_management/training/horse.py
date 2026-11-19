"""Training rows for the horse page and the list's preview pane.

One place decides who sees them: none when Training is switched off or the
role has Training hidden (docs/TRAINING_MODULE_PLAN.md, 5.7). The horse page
shows to every role with Horses access, so this check cannot be left to the
template alone.
"""

from datetime import timedelta

from django.utils import timezone

from core.permissions import LEVEL_FULL, LEVEL_VIEW, has_feature_access

from .access import training_on
from .models import Activity, HorseTraining, LotMember

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


def training_card(user, horse):
    """The horse page's Training card, or None when the role cannot see it."""
    if not can_see_training(user):
        return None
    today = timezone.localdate()
    rows = horse.activities.select_related('work_type')
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
        'coming_up': list(rows.filter(date__gt=today).order_by('date', 'pk')[:COMING_UP]),
        'recent': list(
            rows.filter(date__lte=today, date__gt=today - timedelta(days=RECENT_DAYS))
            .order_by('-date', '-created_at')
        ),
        'not_confirmed': rows.filter(status=Activity.Status.PLANNED, date__lt=today).count(),
        'can_change': has_feature_access(user, 'training', LEVEL_FULL),
        'reasons': Activity.Reason.choices,
        'days': RECENT_DAYS,
    }
