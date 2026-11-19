"""Training items for the dashboard's Needs action list (plan 9.5, item 5).

core.dashboard.attention calls these through its COLLECTORS, so the
dashboard, the notification centre and the reminder switches treat them
like every other kind. Each returns nothing while Training is switched off.
"""

from datetime import timedelta

from django.db.models import Count, Q
from django.urls import reverse

from . import load
from .access import training_on
from .models import Activity, HorseTraining, Lot, LotDay, LotWeekPlan, TrainingSettings

NOT_CONFIRMED_DAYS = 14


def collect(ctx):
    """All training items, for one entry in core's COLLECTORS. A yard with
    no horse in work pays one query and gets nothing."""
    if not training_on():
        return []
    # Every item needs a horse in work (it goes on a board, misses a
    # confirmation, or has no work) or a horse on rest with a review due.
    # A horse on rest can have a review date that is due (dashboard plan 4.4).
    if not HorseTraining.objects.filter(
        Q(status__in=HorseTraining.ON_BOARD) | Q(status__in=HorseTraining.ON_REST, review_on__lte=ctx.today)
    ).exists():
        return []
    return board_not_started(ctx) + not_confirmed(ctx) + no_work(ctx) + review_due(ctx)


def board_not_started(ctx):
    """Today has no board, but lots with horses would go out today."""
    from core.dashboard.attention import AttentionItem
    today = ctx.today
    if LotDay.objects.filter(date=today).exists():
        return []
    going = list(
        Lot.objects.active().annotate(n=Count('members')).filter(n__gt=0)
        .exclude(pk__in=LotWeekPlan.objects.filter(weekday=today.weekday(), no_lot=True).values('lot_id'))
    )
    if not going:
        return []
    return [AttentionItem(
        kind='training_board', severity='due',
        title="Today's lot board is not started",
        detail=f"{len(going)} lot{'s' if len(going) != 1 else ''} with horses",
        url=reverse('training_board'), due_date=today, delta=0,
        key=f'training-board-{today.isoformat()}',
    )]


def not_confirmed(ctx):
    """Lot days of the last 14 days that still hold Planned work (D4)."""
    from core.dashboard.attention import AttentionItem
    today = ctx.today
    days = (
        LotDay.objects.filter(date__lt=today, date__gte=today - timedelta(days=NOT_CONFIRMED_DAYS))
        .annotate(planned=Count('activities', filter=Q(activities__status=Activity.Status.PLANNED)))
        .filter(planned__gt=0).select_related('lot').order_by('date', 'lot__position')
    )
    items = []
    for lot_day in days:
        items.append(AttentionItem(
            kind='training_unconfirmed', severity='overdue',
            title=f"{lot_day.lot.name}, {lot_day.date:%a} {lot_day.date.day} {lot_day.date:%b}",
            detail=f"Not confirmed · {lot_day.planned} horse{'s' if lot_day.planned != 1 else ''} still planned",
            url=reverse('training_board_day', args=[lot_day.date.isoformat()]) + f'#lot-day-{lot_day.pk}',
            due_date=lot_day.date, delta=(lot_day.date - today).days,
            key=f'training-unconfirmed-{lot_day.pk}-{lot_day.planned}',
        ))
    return items


def no_work(ctx):
    """Horses in work with no work for the yard's number of days."""
    from core.dashboard.attention import AttentionItem
    today = ctx.today
    rows = list(
        HorseTraining.objects.filter(status__in=HorseTraining.ON_BOARD, horse__is_active=True)
        .select_related('horse')
    )
    if not rows:
        return []
    threshold = TrainingSettings.load().no_work_days
    by_horse = load.rows_for([r.horse_id for r in rows], today)
    items = []
    for row in rows:
        horse_rows = by_horse.get(row.horse_id, [])
        last = load.last_work_date(horse_rows, today)
        start = last or row.status_since
        idle = (today - start).days
        if idle < threshold or load.unconfirmed_days(horse_rows, today):
            continue   # a gap with Planned work is the "not confirmed" item
        location, site = ctx.placements.get(row.horse_id, ('', ''))
        items.append(AttentionItem(
            kind='training_no_work', severity='info',
            title=row.horse.name,
            detail=f'No work for {idle} days',
            url=reverse('horse_detail', args=[row.horse_id]) + '#training',
            horse=row.horse, horse_id=row.horse_id, location=location, site=site,
            key=f'training-nowork-{row.horse_id}-{start.isoformat()}',
        ))
    return items


def review_due(ctx):
    """Horses on rest whose review date is today or past (dashboard plan 4.4)."""
    from core.dashboard.attention import AttentionItem
    today = ctx.today
    rows = (
        HorseTraining.objects.filter(status__in=HorseTraining.ON_REST, horse__is_active=True, review_on__lte=today)
        .select_related('horse').order_by('review_on', 'horse__name')
    )
    items = []
    for row in rows:
        location, site = ctx.placements.get(row.horse_id, ('', ''))
        items.append(AttentionItem(
            kind='training_review', severity='overdue' if row.review_on < today else 'due',
            title=row.horse.name,
            detail=f"{row.get_status_display()} · review {'today' if row.review_on == today else f'from {row.review_on:%a} {row.review_on.day} {row.review_on:%b}'}",
            url=reverse('horse_detail', args=[row.horse_id]) + '#training',
            horse=row.horse, horse_id=row.horse_id, location=location, site=site,
            due_date=row.review_on, delta=(row.review_on - today).days,
            key=f'training-review-{row.horse_id}-{row.review_on.isoformat()}',
        ))
    return items
