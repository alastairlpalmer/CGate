"""The lot board (docs/TRAINING_MODULE_PLAN.md, phase 2b).

Every board rule is here, in functions that tests can call without a
request. The rules:

- A page that shows the board never writes it (5.3). ``start_board`` is a
  POST, and a second start of the same date makes nothing.
- A change to a lot's work changes only the rows that still follow the lot
  (5.4).
- A lot action changes only rows still in the status it changes from, and
  marks them with ``lot_action`` so that Reopen changes back only those
  (5.12). Rows are saved with history.
- The board checks ``Horse.is_active`` itself; nothing hooks into the
  departure code (5.5).
"""

from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

from .models import Activity, HorseTraining, Lot, LotDay, LotMember, TrainingSettings

ROW_FIELDS = ['status', 'reason', 'lot_action', 'work_type', 'updated_by', 'updated_at']
SOURCES = ('lots', 'copy', 'empty')


class BoardError(Exception):
    """A refusal to show to the person, in plain words."""


# ─── Reading ─────────────────────────────────────────────────────────

def board_days(day):
    return list(
        LotDay.objects.filter(date=day)
        .select_related('lot', 'work_type', 'confirmed_by')
        .order_by('lot__position', 'lot__name')
    )


def last_board_before(day):
    return LotDay.objects.filter(date__lt=day).aggregate(last=Max('date'))['last']


def last_not_confirmed_before(today=None):
    """The latest past date with Planned rows on its board (D4)."""
    today = today or timezone.localdate()
    return (
        Activity.objects.filter(lot_day__isnull=False, status=Activity.Status.PLANNED, date__lt=today)
        .aggregate(last=Max('date'))['last']
    )


def on_board_horse_ids():
    """Horses that go on a board: active, with a status in ON_BOARD."""
    return set(
        HorseTraining.objects.filter(status__in=HorseTraining.ON_BOARD, horse__is_active=True)
        .values_list('horse_id', flat=True)
    )


def horses_on_board(day):
    return set(
        Activity.objects.filter(date=day, lot_day__isnull=False).values_list('horse_id', flat=True)
    )


def missing_members(lot_day, *, today=None, on_board=None, taken=None):
    """Standing members that could go on this lot day but are on no lot day
    of its date (the hint in 8.2). Past boards never change, so none."""
    today = today or timezone.localdate()
    if lot_day.date < today or not lot_day.is_open:
        return []
    on_board = on_board_horse_ids() if on_board is None else on_board
    taken = horses_on_board(lot_day.date) if taken is None else taken
    members = LotMember.objects.filter(lot_id=lot_day.lot_id).select_related('horse').order_by('position', 'pk')
    return [m.horse for m in members if m.horse_id in on_board and m.horse_id not in taken]


def not_on_board_summary(day):
    """Standing members on the yard that are not on this date's board,
    counted by training status: "2 resting, 1 box rest"."""
    taken = horses_on_board(day)
    counts = {}
    members = (
        LotMember.objects.filter(lot__is_active=True, horse__is_active=True)
        .exclude(horse_id__in=taken).select_related('horse__training')
    )
    for member in members:
        row = getattr(member.horse, 'training', None)   # None: no row, Not in training
        label = row.get_status_display() if row else HorseTraining.Status.NOT_IN_TRAINING.label
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items())


# ─── Starting a board ────────────────────────────────────────────────

@dataclass
class StartResult:
    lot_days: list
    source: str
    created: bool
    note: str = ''


def _row(lot_day, horse_id, position, user, **values):
    return Activity(
        horse_id=horse_id, date=lot_day.date, lot_day=lot_day, position=position,
        work_type=values.pop('work_type', None) or lot_day.work_type,
        status=values.pop('status', Activity.Status.PLANNED),
        follows_lot=values.pop('follows_lot', True),
        source=Activity.Source.BOARD, created_by=user, updated_by=user, **values,
    )


def _rows_from_members(lot_day, on_board, taken, user):
    rows = []
    members = LotMember.objects.filter(lot_id=lot_day.lot_id).order_by('position', 'pk')
    for member in members:
        if member.horse_id in on_board and member.horse_id not in taken:
            rows.append(_row(lot_day, member.horse_id, len(rows), user))
            taken.add(member.horse_id)
    return rows


def _rows_from_copy(lot_days, copy_from, on_board, user):
    """Copy the last board: lot days' work, time and notes, each horse's own
    change and the lot it was in. Every copied row is Planned: the status of
    the old day is not copied, so a day cancelled for weather copies whole."""
    by_lot = {ld.lot_id: ld for ld in lot_days}
    rows, taken, copied_lots = [], set(), set()
    sources = (
        LotDay.objects.filter(date=copy_from).select_related('work_type')
        .prefetch_related('activities')
    )
    for source in sources:
        target = by_lot.get(source.lot_id)
        if target is None:          # the lot is archived now
            continue
        copied_lots.add(source.lot_id)
        target.work_type = source.work_type
        target.start_time = source.start_time
        target.notes = source.notes
        target.save(update_fields=['work_type', 'start_time', 'notes', 'updated_at'])
        position = 0
        for old in sorted(source.activities.all(), key=lambda a: (a.position, a.pk)):
            if old.horse_id not in on_board or (target.pk, old.horse_id) in taken:
                continue
            own = not old.follows_lot
            rows.append(_row(
                target, old.horse_id, position, user,
                work_type=old.work_type if own else target.work_type,
                follows_lot=not own,
                minutes=old.minutes if own else None,
                effort=old.effort if own else None,
            ))
            taken.add((target.pk, old.horse_id))
            position += 1
    # A lot made since the last board starts from its standing members.
    placed = {horse_id for _, horse_id in taken}
    for lot_day in lot_days:
        if lot_day.lot_id not in copied_lots:
            rows += _rows_from_members(lot_day, on_board, placed, user)
    return rows


@transaction.atomic
def start_board(day, *, user, source=None):
    """Start the board for ``day``: a lot day for every active lot, and the
    Planned rows the source gives. A date that has a board keeps it."""
    existing = board_days(day)
    if existing:
        return StartResult(existing, source or '', created=False)
    source = source if source in SOURCES else TrainingSettings.load().board_source
    if source not in SOURCES:
        source = 'lots'
    note = ''
    copy_from = None
    if source == 'copy':
        copy_from = last_board_before(day)
        if copy_from is None:
            source = 'lots'
            note = 'There is no earlier board to copy, so the board started from the lots.'
    lots = list(Lot.objects.active().select_related('usual_work_type'))
    try:
        with transaction.atomic():
            lot_days = [
                LotDay.objects.create(
                    lot=lot, date=day, work_type=lot.usual_work_type,
                    start_time=lot.usual_time, created_by=user,
                )
                for lot in lots
            ]
    except IntegrityError:
        # Someone started the same date a moment before: show theirs.
        return StartResult(board_days(day), source, created=False)

    on_board = on_board_horse_ids()
    rows = []
    if source == 'lots':
        taken = set()
        for lot_day in lot_days:
            rows += _rows_from_members(lot_day, on_board, taken, user)
    elif source == 'copy':
        rows = _rows_from_copy(lot_days, copy_from, on_board, user)
    if rows:
        bulk_create_with_history(rows, Activity, default_user=user)
    return StartResult(board_days(day), source, created=True, note=note)


@transaction.atomic
def add_lot_to_board(lot, day, *, user):
    """A lot with no lot day on a started board (made after it started)."""
    lot_day, created = LotDay.objects.get_or_create(
        lot=lot, date=day,
        defaults={'work_type': lot.usual_work_type, 'start_time': lot.usual_time, 'created_by': user},
    )
    if created:
        rows = _rows_from_members(lot_day, on_board_horse_ids(), horses_on_board(day), user)
        if rows:
            bulk_create_with_history(rows, Activity, default_user=user)
    return lot_day


# ─── Changing a lot day ──────────────────────────────────────────────

def _lock(lot_day):
    return LotDay.objects.select_for_update().get(pk=lot_day.pk)


def _save_rows(rows, user):
    if not rows:
        return
    now = timezone.now()
    for row in rows:
        row.updated_by = user
        row.updated_at = now
    bulk_update_with_history(rows, Activity, ROW_FIELDS, default_user=user)


@transaction.atomic
def set_lot_work(lot_day, *, work_type, start_time, notes, user):
    """Change the lot's work, time and notes for the day. The work goes to
    the Planned rows that still follow the lot; a horse's own change stays."""
    lot_day = _lock(lot_day)
    if not lot_day.is_open:
        raise BoardError(f"{lot_day.lot.name} is {lot_day.get_status_display().lower()}. Reopen it first.")
    lot_day.work_type = work_type
    lot_day.start_time = start_time
    lot_day.notes = notes
    lot_day.save()
    rows = list(lot_day.activities.select_for_update().filter(
        follows_lot=True, status=Activity.Status.PLANNED,
    ))
    for row in rows:
        row.work_type = work_type
    _save_rows(rows, user)
    return len(rows)


@dataclass
class MarkLotResult:
    changed: int = 0
    left_yard: list = field(default_factory=list)   # horse names, rows left Planned


@transaction.atomic
def mark_lot_done(lot_day, *, user, today=None):
    """Planned → Done for every horse still on the yard. Did not work stays."""
    today = today or timezone.localdate()
    lot_day = _lock(lot_day)
    if lot_day.date > today:
        raise BoardError("Work cannot be Done on a date after today.")
    if lot_day.status == LotDay.Status.CANCELLED:
        raise BoardError(f"{lot_day.lot.name} is cancelled. Reopen it first.")
    result = MarkLotResult()
    rows = list(lot_day.activities.select_for_update().select_related('horse').filter(status=Activity.Status.PLANNED))
    to_change = []
    for row in rows:
        if not row.horse.is_active:
            result.left_yard.append(row.horse.name)
            continue
        row.status = Activity.Status.DONE
        row.reason = ''
        row.lot_action = 'done'
        to_change.append(row)
    _save_rows(to_change, user)
    result.changed = len(to_change)
    lot_day.status = LotDay.Status.DONE
    lot_day.confirmed_by = user
    lot_day.confirmed_at = timezone.now()
    lot_day.save()
    return result


@transaction.atomic
def cancel_lot(lot_day, reason, *, user):
    """Planned → Did not work with one reason. Done stays Done. Any date."""
    if reason not in Activity.Reason.values:
        raise BoardError("Choose why the lot did not go out.")
    lot_day = _lock(lot_day)
    if lot_day.status == LotDay.Status.DONE:
        raise BoardError(f"{lot_day.lot.name} is done. Reopen it first.")
    rows = list(lot_day.activities.select_for_update().filter(status=Activity.Status.PLANNED))
    for row in rows:
        row.status = Activity.Status.NOT_DONE
        row.reason = reason
        row.lot_action = 'cancel'
    _save_rows(rows, user)
    lot_day.status = LotDay.Status.CANCELLED
    lot_day.confirmed_by = user
    lot_day.confirmed_at = timezone.now()
    lot_day.save()
    return len(rows)


@transaction.atomic
def reopen_lot(lot_day, *, user):
    """Change back only the rows a lot action set. No time limit (5.12)."""
    lot_day = _lock(lot_day)
    rows = list(lot_day.activities.select_for_update().exclude(lot_action=''))
    for row in rows:
        row.status = Activity.Status.PLANNED
        row.reason = ''
        row.lot_action = ''
    _save_rows(rows, user)
    lot_day.status = LotDay.Status.PLANNED
    lot_day.confirmed_by = None
    lot_day.confirmed_at = None
    lot_day.save()
    return len(rows)


# ─── One horse on the board ──────────────────────────────────────────

@dataclass
class AddResult:
    added: list = field(default_factory=list)     # horse names
    already: list = field(default_factory=list)   # horse names on this lot day
    left: list = field(default_factory=list)      # horse names not on the yard


def _next_position(lot_day):
    top = lot_day.activities.aggregate(top=Max('position'))['top']
    return 0 if top is None else top + 1


@transaction.atomic
def add_horses(lot_day, horses, *, user, today=None):
    """Add horses to this lot day only. The standing lot does not change.
    A horse added to a lot that is done is Done with it."""
    today = today or timezone.localdate()
    lot_day = _lock(lot_day)
    if lot_day.status == LotDay.Status.CANCELLED:
        raise BoardError(f"{lot_day.lot.name} is cancelled. Reopen it first.")
    done = lot_day.status == LotDay.Status.DONE and lot_day.date <= today
    result = AddResult()
    present = set(lot_day.activities.values_list('horse_id', flat=True))
    for horse in horses:
        if not horse.is_active:
            result.left.append(horse.name)
            continue
        if horse.pk in present:
            result.already.append(horse.name)
            continue
        row = _row(
            lot_day, horse.pk, _next_position(lot_day), user,
            status=Activity.Status.DONE if done else Activity.Status.PLANNED,
            lot_action='done' if done else '',
        )
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError:
            result.already.append(horse.name)
            continue
        present.add(horse.pk)
        result.added.append(horse.name)
    return result


@transaction.atomic
def move_horse(activity, to_lot_day, *, user):
    """Move one row to another lot day of the same date."""
    activity = Activity.objects.select_for_update().select_related('horse', 'lot_day__lot').get(pk=activity.pk)
    if to_lot_day.date != activity.date:
        raise BoardError("A horse can move only to a lot on the same date.")
    if activity.lot_day_id == to_lot_day.pk:
        return activity
    if not to_lot_day.is_open:
        raise BoardError(f"{to_lot_day.lot.name} is {to_lot_day.get_status_display().lower()}. Reopen it first.")
    if to_lot_day.activities.filter(horse_id=activity.horse_id).exists():
        raise BoardError(f"{activity.horse.name} is already in {to_lot_day.lot.name} on this date.")
    activity.lot_day = to_lot_day
    activity.position = _next_position(to_lot_day)
    if activity.status == Activity.Status.PLANNED:
        activity.follows_lot = True
        activity.work_type = to_lot_day.work_type
    activity.lot_action = ''
    activity.updated_by = user
    activity.save()
    return activity


def apply_row_edit(activity, changed):
    """The rules for a board row changed in the record form (5.12):
    a new date takes it off the lot day, new work stops it following the
    lot, and a new status is the person's own, not the lot's."""
    if activity.lot_day_id and 'date' in changed:
        activity.lot_day = None
        activity.follows_lot = False
        activity.position = 0
    if activity.lot_day_id and 'work_type' in changed:
        activity.follows_lot = False
    if 'status' in changed:
        activity.lot_action = ''
    return activity


# ─── Effects of the standing data on started boards ──────────────────

@dataclass
class StatusBoardResult:
    not_done: int = 0
    removed: int = 0
    added_to: object = None   # the lot day it was added to


@transaction.atomic
def apply_status_to_boards(horse, status, day, *, user, today=None):
    """Make the started boards follow a training status change (7.4).

    Off the board (Resting, Box rest, Not in training): the Planned row on
    ``day`` becomes Did not work, "Resting or box rest" (the true record);
    Planned board rows after ``day`` are deleted (they are plans).
    Back on the board: the horse goes on today's board in its standing lot,
    if that board has started and the horse is not on it.
    """
    today = today or timezone.localdate()
    result = StatusBoardResult()
    planned = Activity.objects.select_for_update().filter(
        horse=horse, lot_day__isnull=False, status=Activity.Status.PLANNED,
    )
    if status not in HorseTraining.ON_BOARD:
        rows = list(planned.filter(date=day))
        for row in rows:
            row.status = Activity.Status.NOT_DONE
            row.reason = Activity.Reason.RESTING
            row.lot_action = ''
        _save_rows(rows, user)
        result.not_done = len(rows)
        later = planned.filter(date__gt=day)
        result.removed = later.count()
        for row in later:
            row.delete()
        return result
    member = LotMember.objects.filter(horse=horse).first()
    if member is None or not horse.is_active:
        return result
    lot_day = LotDay.objects.filter(lot_id=member.lot_id, date=today, status=LotDay.Status.PLANNED).first()
    if lot_day and horse.pk not in horses_on_board(today):
        add_horses(lot_day, [horse], user=user, today=today)
        result.added_to = lot_day
    return result


def boards_started_from(day):
    """True when a board for ``day`` or a later date has started."""
    return LotDay.objects.filter(date__gte=day).exists()


@transaction.atomic
def remove_from_started_boards(horse, lot, *, today=None):
    """Planned rows of this horse in this lot, today and later, are deleted."""
    today = today or timezone.localdate()
    rows = Activity.objects.filter(
        horse=horse, lot_day__lot=lot, lot_day__date__gte=today, status=Activity.Status.PLANNED,
    )
    count = rows.count()
    for row in rows:
        row.delete()
    return count
