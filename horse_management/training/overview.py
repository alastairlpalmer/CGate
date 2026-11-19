"""The Training Overview (docs/TRAINING_DASHBOARD_PLAN.md, phase A).

One function, ``build``, reads the data for the page once and returns the
blocks: the figures, Today, Needs attention and Not in work. Each block
answers one question in section 2 of the plan. The page states facts; it
gives no advice and claims no risk (training plan 5.9). No price shows (T4).

A large yard (more than LARGE horses on the board) gets the lot filter and
folded blocks (plan 4.3). There is no setting for this.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Count, Q
from django.urls import reverse

from . import board, load
from .models import Activity, HorseTraining, Lot, LotDay, LotMember, TrainingSettings

LARGE = 25              # horses on the board before blocks fold (4.3)
ATTENTION_ROWS = 8      # Needs attention rows before they group by kind
REST_NO_REVIEW_DAYS = 28
VET_AHEAD_DAYS = 7
VET_BEHIND_DAYS = 30


@dataclass
class Entry:
    name: str
    badge: str
    url: str
    detail: str = ''


@dataclass
class Group:
    kind: str
    label: str
    tone: str
    entries: list = field(default_factory=list)


def _day_word(day, today):
    if day == today:
        return 'today'
    if day == today + timedelta(days=1):
        return 'tomorrow'
    if today < day <= today + timedelta(days=6):
        return f'{day:%a}'
    return f'{day:%a} {day.day} {day:%b}'


def _plural(n, word, many=None):
    return f"{n} {word if n == 1 else (many or word + 's')}"


# ─── Not in work (4.4) ───────────────────────────────────────────────

def rest_badge(row, today, follow_up=None):
    """The action badge for a horse on rest, as ``(order, text)``, or None.

    Lower order is more urgent. ``follow_up`` is the vet follow-up date, or
    None (also None when the user cannot see Health).
    """
    if row.review_on and row.review_on < today:
        late = (today - row.review_on).days
        return 0, f"Review overdue {_plural(late, 'day')}"
    if row.review_on == today:
        return 1, 'Review due today'
    if follow_up:
        if follow_up < today:
            return 2, 'Vet follow-up overdue'
        return 2, f'Vet follow-up {_day_word(follow_up, today)}'
    if not row.review_on:
        days = (today - row.status_since).days
        if row.status == HorseTraining.Status.BOX_REST or days >= REST_NO_REVIEW_DAYS:
            return 3, 'No review date'
    return None


def _health_facts(horse_ids, today):
    """Active conditions and the next vet follow-up for each horse."""
    from health.models import MedicalCondition, VetVisit
    conditions = {}
    for horse_id, name in (
        MedicalCondition.objects.filter(
            horse_id__in=horse_ids,
            status__in=[MedicalCondition.Status.ACTIVE, MedicalCondition.Status.MONITORING],
        ).order_by('-diagnosed_date', 'pk').values_list('horse_id', 'name')
    ):
        conditions.setdefault(horse_id, []).append(name)
    follow_ups = {}
    for horse_id, day in (
        VetVisit.objects.filter(
            horse_id__in=horse_ids,
            follow_up_date__gte=today - timedelta(days=VET_BEHIND_DAYS),
            follow_up_date__lte=today + timedelta(days=VET_AHEAD_DAYS),
        ).order_by('follow_up_date').values_list('horse_id', 'follow_up_date')
    ):
        follow_ups.setdefault(horse_id, day)
    return conditions, follow_ups


def not_in_work(today, *, scope=None, show_health=False, large=False):
    """Horses on Resting or Box rest (T6): the ones with an action first."""
    qs = HorseTraining.objects.filter(status__in=HorseTraining.ON_REST, horse__is_active=True)
    if scope is not None:
        qs = qs.filter(horse_id__in=scope)
    rows = list(qs.select_related('horse').order_by('horse__name'))
    ids = [r.horse_id for r in rows]
    conditions, follow_ups = _health_facts(ids, today) if (show_health and ids) else ({}, {})
    action, others = [], {}
    for row in rows:
        row.days = (today - row.status_since).days
        row.conditions = conditions.get(row.horse_id, [])
        row.badge = rest_badge(row, today, follow_ups.get(row.horse_id))
        if row.review_on and row.review_on > today:
            row.review_word = f'Review {_day_word(row.review_on, today)}'
        if row.badge:
            action.append(row)
        else:
            others.setdefault(row.get_status_display(), []).append(row)
    action.sort(key=lambda r: (r.badge[0], -r.days))
    return {
        'count': len(rows),
        'action': action,
        # A small yard shows every horse; a large one folds the rest (4.3).
        'others': sorted(others.items()),
        'fold': large,
    }


# ─── Needs attention ─────────────────────────────────────────────────

def _behind(rows, today, days_per_week):
    """True when the days left this week cannot reach the livery's days."""
    worked = load.week_days_worked(rows, today)
    needed = days_per_week - worked
    if needed <= 0:
        return False
    worked_today = today in {r.date for r in rows if load.is_work(r)}
    days_left = 6 - today.weekday() + (0 if worked_today else 1)
    return needed > days_left


def needs_attention(today, *, statuses, history, lot=None, rest=None):
    """The attention groups, in a fixed order. Horse facts use load.py, so
    the Overview, the board and the horse page say the same thing."""
    from .attention import NOT_CONFIRMED_DAYS
    groups = []
    unconfirmed = Group('unconfirmed', 'Not confirmed', 'warn')
    days = (
        LotDay.objects.filter(date__lt=today, date__gte=today - timedelta(days=NOT_CONFIRMED_DAYS))
        .annotate(planned=Count('activities', filter=Q(activities__status=Activity.Status.PLANNED)))
        .filter(planned__gt=0).select_related('lot').order_by('date', 'lot__position')
    )
    if lot is not None:
        days = days.filter(lot=lot)
    for lot_day in days:
        unconfirmed.entries.append(Entry(
            f"{lot_day.lot.name}, {lot_day.date:%a} {lot_day.date.day} {lot_day.date:%b}",
            'Not confirmed',
            reverse('training_board_day', args=[lot_day.date.isoformat()]) + f'#lot-day-{lot_day.pk}',
            f"{_plural(lot_day.planned, 'horse')} still planned",
        ))
    groups.append(unconfirmed)

    review = Group('review', 'Review due', 'warn')
    for row in (rest or []):
        if row.badge and row.badge[0] <= 1:
            review.entries.append(Entry(row.horse.name, row.badge[1], _horse_url(row.horse_id), row.get_status_display()))
    groups.append(review)

    threshold = TrainingSettings.load().no_work_days
    no_work = Group('no_work', f'No work {threshold}+ days', 'warn')
    fast = Group('fast', 'Fast work 2+ days running', 'info')
    first = Group('first', f'First work after {load.FIRST_WORK_GAP}+ days', 'info')
    behind = Group('behind', 'Behind on exercise days', 'neutral')
    for status in statuses:
        rows = history.get(status.horse_id, [])
        url = _horse_url(status.horse_id)
        name = status.horse.name
        gap = load.first_work_gap(rows, today)
        if gap:
            first.entries.append(Entry(name, f'First work in {gap} days', url))
        else:
            last = load.last_work_date(rows, today)
            start = last or status.status_since
            idle = (today - start).days if start else 0
            if idle >= threshold and not load.unconfirmed_days(rows, today):
                no_work.entries.append(Entry(name, f'No work for {idle} days', url))
        running = load.fast_days_running(rows, today)
        if running >= 2:
            fast.entries.append(Entry(name, f'Fast work {running} days running', url))
        if status.days_per_week and _behind(rows, today, status.days_per_week):
            worked = load.week_days_worked(rows, today)
            behind.entries.append(Entry(name, f'{worked} of {status.days_per_week} days this week', url))
    groups += [no_work, fast, first, behind]
    groups = [g for g in groups if g.entries]
    total = sum(len(g.entries) for g in groups)
    return {
        'groups': groups,
        'total': total,
        'grouped': total > ATTENTION_ROWS,
        'unconfirmed_lots': len(unconfirmed.entries),
    }


def _horse_url(horse_id):
    return reverse('horse_detail', args=[horse_id]) + '#training'


# ─── Today ───────────────────────────────────────────────────────────

def today_block(today, lot_days):
    rows = list(
        Activity.objects.filter(lot_day__in=lot_days)
        .select_related('horse', 'rider').order_by('position', 'pk')
    )
    by_day = {}
    for row in rows:
        by_day.setdefault(row.lot_day_id, []).append(row)
    lot_names = {ld.pk: ld.lot for ld in lot_days}
    for lot_day in lot_days:
        day_rows = by_day.get(lot_day.pk, [])
        lot_day.total = len(day_rows)
        lot_day.done = sum(1 for r in day_rows if r.status == Activity.Status.DONE)
        lot_day.not_done = sum(1 for r in day_rows if r.status == Activity.Status.NOT_DONE)
        lot_day.planned = lot_day.total - lot_day.done - lot_day.not_done
        lot_day.percent = round(100 * (lot_day.done + lot_day.not_done) / lot_day.total) if lot_day.total else 0
        names = []
        for r in day_rows:
            name = 'The owner' if r.rider_is_owner else (r.rider.name if r.rider_id else '')
            if name and name not in names:
                names.append(name)
        lot_day.rider_names = names[:3]
        lot_day.more_riders = max(0, len(names) - 3)
    not_going = [
        {'name': r.horse.name, 'reason': r.get_reason_display(), 'url': _horse_url(r.horse_id)}
        for r in rows if r.status == Activity.Status.NOT_DONE
    ]
    riders = {}
    for r in rows:
        name = 'The owner' if r.rider_is_owner else (r.rider.name if r.rider_id else '')
        if name and r.status != Activity.Status.NOT_DONE:
            riders.setdefault(name, []).append(r.horse.name)
    rider_list = sorted(riders.items(), key=lambda item: (-len(item[1]), item[0]))
    open_days = [ld for ld in lot_days if ld.is_open]
    timed = [ld for ld in open_days if ld.start_time]
    return {
        'lot_days': lot_days,
        'not_going': not_going,
        'done_count': sum(1 for ld in lot_days if ld.status == LotDay.Status.DONE),
        'next': timed[0] if timed else (open_days[0] if open_days else None),
        'riders': rider_list,
        'lot_of_horse': {r.horse_id: lot_names.get(r.lot_day_id) for r in rows},
    }


# ─── This week (phase B) ────────────────────────────────────────────

WEEK_OPEN = 10   # horses shown when a large yard opens a lot


def week_block(today, statuses, history, *, lot_of_horse, large, lot=None):
    """Horses in work, grouped by today's lot, else their standing lot.

    A large yard gets one summary row for each lot (plan 4.3): for each day,
    horses worked of horses in the lot. Today and later days are grey (M6).
    """
    standing = {
        m.horse_id: m.lot for m in
        LotMember.objects.filter(horse_id__in=[s.horse_id for s in statuses]).select_related('lot')
    }
    monday = today - timedelta(days=today.weekday())
    days = [monday + timedelta(days=i) for i in range(7)]
    groups = {}
    for status in statuses:
        rows = history.get(status.horse_id, [])
        group_lot = lot_of_horse.get(status.horse_id) or standing.get(status.horse_id)
        horse = {
            'id': status.horse_id, 'name': status.horse.name,
            'cells': load.week(rows, today),
            'days': load.week_days_worked(rows, today),
            'fast': load.week_fast(rows, today),
        }
        groups.setdefault(group_lot, []).append(horse)
    ordered = sorted(groups.items(), key=lambda item: (
        item[0] is None, item[0].position if item[0] else 0, item[0].name if item[0] else '',
    ))
    out = []
    for group_lot, horses in ordered:
        n = len(horses)
        summary = []
        for i, d in enumerate(days):
            if d > today:
                summary.append({'text': '', 'tone': 'later'})
                continue
            worked = sum(1 for h in horses if h['cells'][i]['tone'] in ('easy', 'steady', 'fast'))
            if d == today:
                tone = 'today'
            elif worked == n:
                tone = 'full'
            elif n - worked >= 3:
                tone = 'low'
            else:
                tone = 'part'
            summary.append({'text': f'{worked}/{n}', 'tone': tone,
                            'label': f'{d:%a}: {worked} of {n} horses worked'})
        out.append({
            'lot': group_lot,
            'name': group_lot.name if group_lot else 'Not in a lot',
            'horses': horses,
            'shown': horses if (not large or lot is not None) else horses[:WEEK_OPEN],
            'more': 0 if (not large or lot is not None) else max(0, n - WEEK_OPEN),
            'summary': summary,
            'fast': sum(h['fast'] for h in horses),
        })
    return {'days': days, 'groups': out, 'summary_rows': large and lot is None}


def tomorrow_block(today, *, lot=None):
    tomorrow = today + timedelta(days=1)
    qs = (
        LotDay.objects.filter(date=tomorrow).select_related('lot', 'work_type')
        .annotate(n=Count('activities')).order_by('lot__position', 'lot__name')
    )
    if lot is not None:
        qs = qs.filter(lot=lot)
    lot_days = list(qs)
    return {'date': tomorrow, 'lot_days': lot_days, 'horses': sum(ld.n for ld in lot_days)}


# ─── The page ────────────────────────────────────────────────────────

def _scope(lot, today):
    """Horses that belong to a lot: its standing members and today's rows."""
    ids = set(LotMember.objects.filter(lot=lot).values_list('horse_id', flat=True))
    ids |= set(Activity.objects.filter(lot_day__lot=lot, lot_day__date=today).values_list('horse_id', flat=True))
    return ids


def build(today, *, lot=None, show_health=False):
    on_board_counts = {}
    on_board_qs = HorseTraining.objects.filter(status__in=HorseTraining.ON_BOARD, horse__is_active=True)
    for status in on_board_qs.values_list('status', flat=True):
        on_board_counts[status] = on_board_counts.get(status, 0) + 1
    on_board_total = sum(on_board_counts.values())
    large = on_board_total > LARGE

    scope = None
    if lot is not None:
        scope = _scope(lot, today)
        on_board_counts = {}
        for status in on_board_qs.filter(horse_id__in=scope).values_list('status', flat=True):
            on_board_counts[status] = on_board_counts.get(status, 0) + 1

    lot_days = board.board_days(today)
    if lot is not None:
        lot_days = [ld for ld in lot_days if ld.lot_id == lot.pk]
    today_data = today_block(today, lot_days)
    rest = not_in_work(today, scope=scope, show_health=show_health, large=large)

    # One read of the horses in work and their records, for Needs attention
    # and This week. The rows reach Sunday, so the week shows planned work.
    status_qs = on_board_qs if scope is None else on_board_qs.filter(horse_id__in=scope)
    statuses = list(status_qs.select_related('horse').order_by('horse__name'))
    sunday = today + timedelta(days=6 - today.weekday())
    history = load.rows_for([st.horse_id for st in statuses], sunday) if statuses else {}
    attention = needs_attention(today, statuses=statuses, history=history, lot=lot, rest=rest['action'])
    week = week_block(
        today, statuses, history, lot_of_horse=today_data['lot_of_horse'], large=large, lot=lot,
    )

    labels = dict(HorseTraining.Status.choices)
    in_work_parts = [
        f"{on_board_counts[s]} {labels[s].lower()}" for s in HorseTraining.ON_BOARD if on_board_counts.get(s)
    ]
    return {
        'large': large,
        'lots': list(Lot.objects.active()) if (large or lot is not None) else [],
        'lot': lot,
        'today_data': today_data,
        'in_work_total': sum(on_board_counts.values()),
        'in_work_parts': in_work_parts,
        'rest': rest,
        'attention': attention,
        'week': week,
        'tomorrow': tomorrow_block(today, lot=lot),
        'riders_folded': large,
    }
