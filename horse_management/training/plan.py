"""The Plan tab: the week by lot (docs/TRAINING_BOARD_REDESIGN_PLAN.md, 5.1).

One column for each day, Monday to Sunday; one cell for each lot. A day
with a board shows what is on it. A day with no board shows what a new
board would start with, so the trainer sees the week before it is made.
Reading it never writes (module plan, 5.3).
"""

from datetime import timedelta

from django.db.models import Count, Q

from .models import Activity, DayNote, Lot, LotDay, LotWeekPlan, TrainingSettings


def week_days(day):
    monday = day - timedelta(days=day.weekday())
    return [monday + timedelta(days=i) for i in range(7)]


def _lot_day_cell(lot, lot_day, counts, today):
    total = sum(counts.values())
    done = counts.get(Activity.Status.DONE, 0)
    if lot_day.status == LotDay.Status.CANCELLED:
        state, tone = 'Cancelled', 'info'
    elif lot_day.status == LotDay.Status.DONE:
        state, tone = 'Done', 'done'
    elif lot_day.date < today:
        state, tone = 'Not confirmed', 'warn'
    elif done:
        state, tone = f'{done} of {total} done', 'part'
    else:
        state, tone = 'Planned', 'planned'
    return {
        'lot': lot, 'lot_day': lot_day, 'work': lot_day.work_type.name,
        'venue': lot_day.venue.name if lot_day.venue_id else '',
        'total': total, 'state': state, 'tone': tone, 'notes': lot_day.notes,
    }


def _usual_cell(lot, plan, use_week_plan):
    """What a new board would give this lot: the week plan when the yard
    starts boards from it, or the lot's usual work."""
    if use_week_plan and plan is not None and plan.no_lot:
        return {'lot': lot, 'usual': True, 'no_lot': True, 'work': 'No lot', 'venue': ''}
    work = plan.work_type if use_week_plan and plan is not None and plan.work_type_id else lot.usual_work_type
    venue = plan.venue.name if use_week_plan and plan is not None and plan.venue_id else ''
    return {'lot': lot, 'usual': True, 'work': work.name, 'venue': venue, 'total': lot.size}


def _day_state(lot_days, day, today):
    if not lot_days:
        return ('No board', 'none') if day < today else ('Not started', 'none')
    open_days = [ld for ld in lot_days if ld.status == LotDay.Status.PLANNED]
    if not open_days:
        return 'Done', 'done'
    if day < today:
        return 'Not confirmed', 'warn'
    closed = len(lot_days) - len(open_days)
    if closed:
        return f'{closed} of {len(lot_days)} lots done', 'part'
    return 'Planned', 'planned'


def build(day, *, today):
    """The week of ``day``: ``{'days', 'lots', 'columns', 'previous', 'next'}``.
    Six queries, whatever the size of the yard."""
    days = week_days(day)
    lot_days = list(
        LotDay.objects.filter(date__range=(days[0], days[-1])).select_related('lot', 'work_type', 'venue')
    )
    counts = {}
    for row in (Activity.objects.filter(lot_day__in=[ld.pk for ld in lot_days])
                .values('lot_day_id', 'status').annotate(n=Count('id'))):
        counts.setdefault(row['lot_day_id'], {})[row['status']] = row['n']
    on_week = {ld.lot_id for ld in lot_days}
    lots = list(
        Lot.objects.filter(Q(is_active=True) | Q(pk__in=on_week)).select_related('usual_work_type')
        .annotate(size=Count('members', filter=Q(members__horse__is_active=True)))
        .order_by('position', 'name')
    )
    plans = {
        (p.lot_id, p.weekday): p
        for p in LotWeekPlan.objects.filter(lot__in=lots).select_related('work_type', 'venue')
    }
    notes = dict(DayNote.objects.filter(date__range=(days[0], days[-1])).values_list('date', 'text'))
    use_week_plan = TrainingSettings.load().board_source == TrainingSettings.BoardSource.WEEK_PLAN
    by_key = {(ld.lot_id, ld.date): ld for ld in lot_days}
    columns = []
    for d in days:
        day_lot_days = [ld for ld in lot_days if ld.date == d]
        cells = []
        for lot in lots:
            lot_day = by_key.get((lot.pk, d))
            if lot_day is not None:
                cells.append(_lot_day_cell(lot, lot_day, counts.get(lot_day.pk, {}), today))
            elif day_lot_days or not lot.is_active:
                # The board started without this lot, or the lot is archived.
                cells.append({'lot': lot, 'off': True, 'work': 'Not on this board', 'venue': ''})
            else:
                cells.append(_usual_cell(lot, plans.get((lot.pk, d.weekday())), use_week_plan))
        state, tone = _day_state(day_lot_days, d, today)
        # A day the week plan gives no lot to has nothing to start.
        no_lots = not day_lot_days and bool(cells) and all(cell.get('no_lot') for cell in cells)
        if no_lots and d >= today:
            state = 'No lots'
        columns.append({
            'date': d, 'is_today': d == today, 'is_past': d < today, 'started': bool(day_lot_days),
            'can_start': not day_lot_days and d >= today and not no_lots,
            'no_lots': no_lots,
            'state': state, 'tone': tone, 'cells': cells, 'note': notes.get(d, ''),
        })
    return {
        'days': days, 'lots': lots, 'columns': columns,
        'previous': days[0] - timedelta(days=7), 'next': days[0] + timedelta(days=7),
        'is_this_week': days[0] <= today <= days[-1],
    }
