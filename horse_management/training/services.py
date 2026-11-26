"""Status changes for training records (docs/TRAINING_MODULE_PLAN.md, 5.12).

Each function locks the rows it changes, changes only rows that are still
in the status it changes from, and saves them with history. A double tap,
or two people at once, give the same result as one tap.
"""

from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from simple_history.utils import bulk_update_with_history

from . import charges
from .models import Activity, HorseTraining, Lot, LotMember

CHANGED_FIELDS = ['status', 'reason', 'lot_action', 'updated_by', 'updated_at']


@dataclass
class MarkResult:
    changed: int = 0
    future: int = 0     # Planned, but dated after today: not changed
    other: int = 0      # Not Planned: not changed
    charges: dict = field(default_factory=dict)     # phase 5: training.charges.sync counts

    @property
    def skipped(self):
        return self.future + self.other


def _ids(activities):
    return [a.pk if isinstance(a, Activity) else int(a) for a in activities]


def _save(rows, user, fields=CHANGED_FIELDS):
    if not rows:
        return
    now = timezone.now()
    for row in rows:
        row.updated_by = user
        row.updated_at = now
    bulk_update_with_history(rows, Activity, fields, default_user=user)


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
            row.lot_action = ''     # the person's own change, not the lot's
            to_change.append(row)
    _save(to_change, user)
    result.changed = len(to_change)
    result.charges = charges.sync(to_change)    # phase 5
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
            row.lot_action = ''
            to_change.append(row)
    _save(to_change, user)
    result.changed = len(to_change)
    return result


@transaction.atomic
def mark_planned(activities, *, user):
    """Did not work → Planned: the Undo on one horse (5.12)."""
    ids = _ids(activities)
    rows = list(Activity.objects.select_for_update().filter(pk__in=ids))
    result = MarkResult()
    to_change = []
    for row in rows:
        if row.status != Activity.Status.NOT_DONE:
            result.other += 1
        else:
            row.status = Activity.Status.PLANNED
            row.reason = ''
            row.lot_action = ''
            to_change.append(row)
    _save(to_change, user)
    result.changed = len(to_change)
    return result


@transaction.atomic
def unmark_done(activities, *, user):
    """Done → Planned for one horse: the Undo of a single **Done** (board
    redesign, B1). Its unbilled charge goes; a record whose charge is on a
    live invoice is refused (5.12). The rating goes with the status.
    Returns ``(MarkResult, billed)``: ``billed`` is what
    ``charges.refuse_if_billed`` found, or None.
    """
    ids = _ids(activities)
    rows = list(
        Activity.objects.select_for_update(of=('self',))
        .select_related('horse', 'extra_charge').filter(pk__in=ids)
    )
    result = MarkResult()
    done = [row for row in rows if row.status == Activity.Status.DONE]
    result.other = len(rows) - len(done)
    billed = charges.refuse_if_billed(done)
    if billed:
        return result, billed
    for row in done:
        row.status = Activity.Status.PLANNED
        row.reason = ''
        row.lot_action = ''
        row.rating = None
    _save(done, user, fields=CHANGED_FIELDS + ['rating'])
    result.changed = len(done)
    charges.sync(done)
    return result, None


def set_day_note(day, text, *, user):
    """The note for the whole yard on ``day``. Empty text deletes it."""
    from .models import DayNote
    text = (text or '').strip()
    if not text:
        DayNote.objects.filter(date=day).delete()
        return None
    note, _ = DayNote.objects.update_or_create(date=day, defaults={'text': text, 'updated_by': user})
    return note


# ─── Phase 2a: lots and training status ─────────────────────────────
#
# A horse is in one standing lot at most (D6). Membership has no history:
# activities hold the history. Nothing here runs on a GET (principle 5.3).


def standing_lot(horse):
    """The horse's standing lot, or None. The only way code reads it (5.11)."""
    member = LotMember.objects.filter(horse=horse).select_related('lot').first()
    return member.lot if member else None


def training_status(horse):
    """The status value; a horse with no row is Not in training."""
    row = HorseTraining.objects.filter(horse=horse).only('status').first()
    return row.status if row else HorseTraining.Status.NOT_IN_TRAINING


def _next_position(lot):
    top = lot.members.aggregate(top=Max('position'))['top']
    return 0 if top is None else top + 1


@dataclass
class AddResult:
    added: list = field(default_factory=list)          # horses now in the lot
    already: list = field(default_factory=list)        # (horse, the lot it is in)
    left: list = field(default_factory=list)           # horses no longer on the yard
    started_work: list = field(default_factory=list)   # Not in training -> In work


_KEEP = object()


def set_status(horse, status, day=None, notes=None, *, since=None, review_on=_KEEP):
    """Set the horse's training status. Makes the row if needed.

    ``day`` is the date of the change: the status starts then when it is
    new. ``since`` corrects the start date of the current status. Saved with
    history. Phase 2b adds the board part: Planned work on boards (7.4).
    ``review_on`` (dashboard plan 4.4): the date to check a horse on rest.
    A status that goes on the board clears it.
    """
    if status not in HorseTraining.Status.values:
        raise ValueError(f"Unknown status: {status!r}")
    day = day or timezone.localdate()
    on_board = status in HorseTraining.ON_BOARD
    row, created = HorseTraining.objects.get_or_create(horse=horse, defaults={
        'status': status, 'status_since': since or day, 'notes': notes or '',
        'review_on': None if on_board or review_on is _KEEP else review_on,
    })
    if created:
        return row
    before = (row.status, row.status_since, row.notes, row.review_on)
    if row.status != status:
        row.status = status
        row.status_since = day
    if since is not None:
        row.status_since = since
    if notes is not None:
        row.notes = notes
    if review_on is not _KEEP:
        row.review_on = review_on
    if on_board:
        row.review_on = None
    if (row.status, row.status_since, row.notes, row.review_on) != before:
        row.save()   # one history row for each real change
    return row


def add_to_lot(lot, horses, *, auto_status=True, day=None):
    """Add horses to a standing lot, at the end, in the order given.

    A horse already in a lot stays where it is; the result names it. A horse
    that is Not in training moves to In work (7.4), unless ``auto_status``
    is off because the person chose a status in the same step.
    """
    result = AddResult()
    for horse in horses:
        if not horse.is_active:
            result.left.append(horse)
            continue
        current = LotMember.objects.filter(horse=horse).select_related('lot').first()
        if current is not None:
            result.already.append((horse, current.lot))
            continue
        try:
            with transaction.atomic():
                LotMember.objects.create(lot=lot, horse=horse, position=_next_position(lot))
        except IntegrityError:
            # Someone else put it in a lot at the same moment.
            current = LotMember.objects.filter(horse=horse).select_related('lot').first()
            result.already.append((horse, current.lot if current else lot))
            continue
        result.added.append(horse)
        if auto_status and training_status(horse) == HorseTraining.Status.NOT_IN_TRAINING:
            set_status(horse, HorseTraining.Status.IN_WORK, day)
            result.started_work.append(horse)
    return result


def remove_from_lot(horse):
    """Take the horse out of its standing lot. Returns the lot it left, or None."""
    member = LotMember.objects.filter(horse=horse).select_related('lot').first()
    if member is None:
        return None
    member.delete()
    return member.lot


@transaction.atomic
def move_to_lot(horse, lot, *, auto_status=True, day=None):
    """Put the horse in ``lot``, out of any other. Returns the lot it left."""
    member = LotMember.objects.select_for_update(of=('self',)).filter(horse=horse).select_related('lot').first()
    if member is None:
        add_to_lot(lot, [horse], auto_status=auto_status, day=day)
        return None
    if member.lot_id == lot.pk:
        return None
    old = member.lot
    member.lot = lot
    member.position = _next_position(lot)
    member.save(update_fields=['lot', 'position'])
    return old


@transaction.atomic
def move_member(member, direction):
    """Swap the member with its neighbour, 'up' or 'down'. False at an end."""
    members = list(LotMember.objects.select_for_update().filter(lot_id=member.lot_id).order_by('position', 'pk'))
    index = next((i for i, m in enumerate(members) if m.pk == member.pk), None)
    if index is None:
        return False
    other = index - 1 if direction == 'up' else index + 1
    if other < 0 or other >= len(members):
        return False
    members[index], members[other] = members[other], members[index]
    for position, m in enumerate(members):
        m.position = position
    LotMember.objects.bulk_update(members, ['position'])
    return True


def archive_lot(lot):
    """Archive a lot. Refused while it has members (7.3): its horses would be
    hidden from the picker for ever. Returns an error message, or ''."""
    count = lot.members.count()
    if count:
        return f"Move or remove the {count} horse{'s' if count != 1 else ''} in {lot.name} first."
    lot.is_active = False
    lot.save(update_fields=['is_active'])
    return ''


def set_defaults(horse, *, rider=None, rider_is_owner=False, gear=''):
    """The horse's usual rider and gear for new board rows (R5). Makes the
    status row if needed (Not in training), and saves only a real change."""
    row, created = HorseTraining.objects.get_or_create(horse=horse, defaults={
        'default_rider': rider, 'default_rider_is_owner': rider_is_owner and rider is None, 'default_gear': gear,
    })
    if created:
        return row
    before = (row.default_rider_id, row.default_rider_is_owner, row.default_gear)
    row.default_rider = rider
    row.default_rider_is_owner = rider_is_owner and rider is None
    row.default_gear = gear
    if (row.default_rider_id, row.default_rider_is_owner, row.default_gear) != before:
        row.save()
    return row


def set_days_per_week(horse, days):
    """The exercise days a week the horse's livery includes (D1), or None."""
    row, created = HorseTraining.objects.get_or_create(horse=horse, defaults={'days_per_week': days})
    if not created and row.days_per_week != days:
        row.days_per_week = days
        row.save()
    return row
