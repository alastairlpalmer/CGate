"""A horse's training profile (docs/TRAINING_BOARD_REDESIGN_PLAN.md, 5.3):
the status, the workload facts, the last 8 weeks as a calendar, the work
that is planned, and every record and note, newest first.

The facts are the ones the board and the horse page show (``training.load``).
Reading the page never writes (module plan, 5.3).
"""

from datetime import timedelta

from django.core.paginator import Paginator
from django.db.models import Count

from . import load
from .horse import status_changes
from .models import Activity, HorseTraining, LotMember, TrainingNote, TrainingSettings

WEEKS = 8
PAGE_SIZE = 30
COMING_UP = 7
ONLY = ('notes', 'missed', 'health')


def _ago(days):
    if days is None:
        return 'None yet'
    if days == 0:
        return 'Today'
    if days == 1:
        return 'Yesterday'
    return f'{days} days ago'


def facts(rows, today):
    """Four figures for the facts card. Facts only (module plan, 5.9)."""
    return [
        {'value': load.days_worked_7(rows, today), 'label': 'Days worked', 'sub': 'last 7 days'},
        {'value': load.fast_sessions_7(rows, today), 'label': 'Fast work', 'sub': 'last 7 days'},
        {'value': _ago(load.days_since_work(rows, today)), 'label': 'Last work', 'sub': ''},
        {'value': _ago(load.days_since_fast(rows, today)), 'label': 'Last fast work', 'sub': ''},
    ]


def calendar(rows, today, *, weeks=WEEKS):
    """One row for each week, Monday to Sunday, the newest week first. Each
    square is the horse page's square (``load.day_cell``)."""
    by_day = {}
    for row in rows:
        by_day.setdefault(row.date, []).append(row)
    monday = today - timedelta(days=today.weekday())
    out = []
    for back in range(weeks):
        start = monday - timedelta(days=7 * back)
        cells = []
        for offset in range(7):
            d = start + timedelta(days=offset)
            cell = load.day_cell(d, by_day.get(d, []), today)
            cell['is_today'] = d == today
            cells.append(cell)
        out.append({'start': start, 'cells': cells})
    return out


# The kinds of entry in "Work and notes". The number is the order inside
# one moment, and is never shown.
WORK, NOTE, STATUS, CONDITION, VET = range(5)
KIND_NAMES = {WORK: 'work', NOTE: 'note', STATUS: 'status', CONDITION: 'condition', VET: 'vet'}


def _history(horse, today, only, page, show_health):
    """Records dated today or earlier, notes, training status changes and,
    for a role that can see Health, conditions and vet visits: one list,
    newest first. Only the keys of every row are read; the page's rows are
    read in full.

    ``only``: 'notes' is work with a note and the notes; 'missed' is the
    days the horse did not work; 'health' is those days with the status
    changes, the conditions and the vet visits: what happened to the horse.
    """
    from django.utils import timezone

    from health.models import MedicalCondition, VetVisit

    activities = horse.activities.filter(date__lte=today)
    notes = horse.training_notes.all()
    if only == 'notes':
        activities = activities.exclude(notes='')
    elif only in ('missed', 'health'):
        activities = activities.filter(status=Activity.Status.NOT_DONE)
        notes = notes.none()
    keys = [(d, created, WORK, pk) for pk, d, created in activities.values_list('pk', 'date', 'created_at')]
    keys += [(d, created, NOTE, pk) for pk, d, created in notes.values_list('pk', 'date', 'created_at')]
    changes = {}
    if only in ('', 'health'):
        changes = dict(enumerate(status_changes(horse)))
        keys += [(change.date, change.stamp, STATUS, index) for index, change in changes.items()]
        if show_health:
            for pk, diagnosed, created in MedicalCondition.objects.filter(horse=horse).values_list(
                    'pk', 'diagnosed_date', 'created_at'):
                keys.append((diagnosed or timezone.localtime(created).date(), created, CONDITION, pk))
            keys += [(d, created, VET, pk) for pk, d, created in
                     VetVisit.objects.filter(horse=horse).values_list('pk', 'date', 'created_at')]
    keys.sort(reverse=True)
    page_obj = Paginator(keys, PAGE_SIZE).get_page(page)

    def ids(kind):
        return [pk for _, _, k, pk in page_obj.object_list if k == kind]

    rows = {
        WORK: Activity.objects.select_related('work_type', 'rider', 'venue').in_bulk(ids(WORK)),
        NOTE: TrainingNote.objects.select_related('created_by').in_bulk(ids(NOTE)),
        STATUS: changes,
        CONDITION: MedicalCondition.objects.in_bulk(ids(CONDITION)) if ids(CONDITION) else {},
        VET: VetVisit.objects.select_related('vet').in_bulk(ids(VET)) if ids(VET) else {},
    }
    entries = []
    for day, _, kind, pk in page_obj.object_list:
        obj = rows[kind].get(pk)
        if obj is not None:
            entries.append({'kind': KIND_NAMES[kind], 'obj': obj, 'date': day})
    return page_obj, entries


def build(horse, *, today, only='', page=1, show_health=False):
    only = only if only in ONLY else ''
    status_row = HorseTraining.objects.filter(horse=horse).select_related('default_rider').first()
    member = LotMember.objects.filter(horse=horse).select_related('lot').first()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    # One read for the facts (60 days back) and the calendar (to Sunday).
    window = max(load.WINDOW_DAYS, WEEKS * 7) + (sunday - today).days
    rows = load.rows_for([horse.pk], sunday, window=window)[horse.pk]
    on_board = bool(status_row and status_row.status in HorseTraining.ON_BOARD)
    since = today - timedelta(days=27)
    rated = dict(
        horse.activities.filter(status=Activity.Status.DONE, rating__isnull=False, date__gte=since, date__lte=today)
        .values_list('rating').annotate(n=Count('id'))
    )
    page_obj, entries = _history(horse, today, only, page, show_health)
    default_rider = ''
    if status_row:
        if status_row.default_rider_id:
            default_rider = status_row.default_rider.name
        elif status_row.default_rider_is_owner:
            default_rider = 'The owner'
    return {
        'status': status_row.get_status_display() if status_row else HorseTraining.Status.NOT_IN_TRAINING.label,
        'status_value': status_row.status if status_row else HorseTraining.Status.NOT_IN_TRAINING,
        'status_since': status_row.status_since if status_row else None,
        'status_notes': status_row.notes if status_row else '',
        'review_on': status_row.review_on if status_row else None,
        'lot': member.lot if member else None,
        'default_rider': default_rider,
        'default_gear': status_row.default_gear if status_row else '',
        'facts': facts(rows, today),
        'chips': load.chips(
            rows, today, threshold=TrainingSettings.load().no_work_days,
            days_per_week=status_row.days_per_week if status_row else None,
            on_board=on_board, since=status_row.status_since if status_row else None,
        ),
        'calendar': calendar(rows, today),
        'rated': [
            (label, rated.get(value, 0)) for value, label in Activity.Rating.choices
        ] if rated else [],
        'coming_up': list(
            horse.activities.filter(date__gt=today).select_related('work_type', 'rider', 'lot_day__lot')
            .order_by('date', 'pk')[:COMING_UP]
        ),
        'not_confirmed': horse.activities.not_confirmed(today).count(),
        'page_obj': page_obj,
        'is_paginated': page_obj.has_other_pages(),
        'entries': entries,
        'only': only,
    }
