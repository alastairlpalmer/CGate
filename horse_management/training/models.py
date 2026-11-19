"""Training: work types and the training log for each horse.

Phase 1 of docs/TRAINING_MODULE_PLAN.md. ``Activity`` is the only training
record (principle 5.1): the horse page, the Log and the bulk bar all make
activities, and later the lot board will too. Only Done activities count as
work (5.2).
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from simple_history.models import HistoricalRecords


class WorkTypeQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


class WorkType(models.Model):
    """A kind of work the yard defines, for example "Canter" or "Horse walker"."""

    class Kind(models.TextChoices):
        RIDDEN = 'ridden', 'Ridden'
        NOT_RIDDEN = 'not_ridden', 'Not ridden'
        REST = 'rest', 'Rest'

    class Effort(models.IntegerChoices):
        NONE = 0, 'None'
        EASY = 1, 'Easy'
        STEADY = 2, 'Steady'
        FAST = 3, 'Fast'

    name = models.CharField(max_length=60, unique=True)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.RIDDEN)
    effort = models.PositiveSmallIntegerField(
        choices=Effort.choices, default=Effort.EASY,
        help_text="How hard the work is. A rest work type has no effort.",
    )
    default_minutes = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="The usual length. A record with no minutes uses this.",
    )
    position = models.PositiveSmallIntegerField(default=0, help_text="Order in the lists. Lower comes first.")
    is_active = models.BooleanField(default=True)

    objects = WorkTypeQuerySet.as_manager()

    class Meta:
        ordering = ['position', 'name']
        constraints = [
            models.CheckConstraint(
                condition=(Q(kind='rest') & Q(effort=0)) | (~Q(kind='rest') & Q(effort__gt=0)),
                name='worktype_rest_has_no_effort',
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_rest(self):
        return self.kind == self.Kind.REST


# The effort a single record can set in place of its work type's effort.
EFFORT_OVERRIDE_CHOICES = [
    (WorkType.Effort.EASY, 'Easy'),
    (WorkType.Effort.STEADY, 'Steady'),
    (WorkType.Effort.FAST, 'Fast'),
]


class ActivityQuerySet(models.QuerySet):
    def done(self):
        return self.filter(status=Activity.Status.DONE)

    def not_confirmed(self, today=None):
        """Planned work whose date has passed (principle 5.2, D4)."""
        today = today or timezone.localdate()
        return self.filter(status=Activity.Status.PLANNED, date__lt=today)

    def for_timeline(self, today=None):
        """Facts only: Done and Did not work, dated today or earlier."""
        today = today or timezone.localdate()
        return self.filter(
            status__in=(Activity.Status.DONE, Activity.Status.NOT_DONE), date__lte=today,
        )


class Activity(models.Model):
    """One piece of work by one horse on one date."""

    class Status(models.TextChoices):
        PLANNED = 'planned', 'Planned'
        DONE = 'done', 'Done'
        NOT_DONE = 'not_done', 'Did not work'

    class Reason(models.TextChoices):
        UNSOUND = 'unsound', 'Lame or sore'
        VET = 'vet', 'Vet or treatment'
        FARRIER = 'farrier', 'Shoe off or farrier'
        RESTING = 'resting', 'Resting or box rest'
        WEATHER = 'weather', 'Weather or ground'
        NO_RIDER = 'no_rider', 'No rider'
        OTHER = 'other', 'Other'

    class Source(models.TextChoices):
        BOARD = 'board', 'Lot board'
        HORSE = 'horse', 'One horse'
        BULK = 'bulk', 'Bulk add'

    horse = models.ForeignKey('core.Horse', on_delete=models.CASCADE, related_name='activities')
    date = models.DateField()
    work_type = models.ForeignKey(WorkType, on_delete=models.PROTECT, related_name='activities')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DONE)
    reason = models.CharField(max_length=10, choices=Reason.choices, blank=True)
    minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    effort = models.PositiveSmallIntegerField(
        choices=EFFORT_OVERRIDE_CHOICES, null=True, blank=True,
        help_text="Leave empty to use the work type's usual effort.",
    )
    notes = models.TextField(blank=True)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.HORSE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    updated_at = models.DateTimeField(auto_now=True)
    # Phase 2b: the lot board.
    lot_day = models.ForeignKey(
        'LotDay', null=True, blank=True, on_delete=models.PROTECT, related_name='activities',
    )
    # True until someone changes this horse's work for the day (5.4).
    follows_lot = models.BooleanField(default=False)
    # The lot action that set the status: '' | 'done' | 'cancel'. Reopen
    # changes back only these rows (5.12).
    lot_action = models.CharField(max_length=8, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    # Phase 3: where, who and with what.
    venue = models.ForeignKey('Venue', null=True, blank=True, on_delete=models.PROTECT, related_name='activities')
    rider = models.ForeignKey('Rider', null=True, blank=True, on_delete=models.PROTECT, related_name='activities')
    rider_is_owner = models.BooleanField(default=False, help_text="The horse's owner rode or handled it.")
    gear = models.CharField(max_length=120, blank=True)
    history = HistoricalRecords()

    objects = ActivityQuerySet.as_manager()

    class Meta:
        ordering = ['-date', '-created_at', '-pk']
        verbose_name_plural = 'activities'
        constraints = [
            models.CheckConstraint(
                condition=(Q(status='not_done') & ~Q(reason='')) | (~Q(status='not_done') & Q(reason='')),
                name='activity_reason_only_when_not_done',
            ),
            models.UniqueConstraint(
                fields=['lot_day', 'horse'], condition=Q(lot_day__isnull=False),
                name='activity_one_row_per_horse_per_lot_day',
            ),
            models.CheckConstraint(
                condition=Q(rider__isnull=True) | Q(rider_is_owner=False),
                name='activity_rider_or_owner_not_both',
            ),
        ]
        indexes = [
            models.Index(fields=['horse', '-date'], name='activity_horse_date'),
            models.Index(fields=['date', 'status'], name='activity_date_status'),
        ]

    def __str__(self):
        return f"{self.horse} · {self.work_type} · {self.date:%d %b %Y}"

    @property
    def effective_effort(self):
        return self.effort if self.effort is not None else self.work_type.effort

    @property
    def effort_label(self):
        return WorkType.Effort(self.effective_effort).label if self.effective_effort else ''

    @property
    def is_not_confirmed(self):
        return self.status == self.Status.PLANNED and self.date < timezone.localdate()

    @property
    def status_label(self):
        """The word on the screen (principle 5.13). Past plans say so."""
        if self.is_not_confirmed:
            return 'Not confirmed'
        return self.get_status_display()

    @property
    def rider_label(self):
        """The rider's name, or the owner's name when the owner rode (D1)."""
        if self.rider_is_owner:
            owner = self.horse.current_owner
            return owner.name if owner else 'The owner'
        return self.rider.name if self.rider_id else ''

    @property
    def can_mark_done(self):
        return self.status == self.Status.PLANNED and self.date <= timezone.localdate()


# ─── Phase 2a: lots, training status, the board setting ─────────────

class LotQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


class Lot(models.Model):
    """A standing group of horses that usually work together (D2).

    The default for now: the board for a day starts from these lots. The
    design keeps "new each day" open (principle 5.11): nothing outside
    ``LotMember`` needs a horse to be a standing member.
    """

    name = models.CharField(max_length=60, unique=True)
    position = models.PositiveSmallIntegerField(default=0, help_text="Order in the day. Lower goes first.")
    usual_time = models.TimeField(null=True, blank=True)
    usual_work_type = models.ForeignKey(
        WorkType, on_delete=models.PROTECT, related_name='lots',
        help_text="The work this lot usually does. Each day can change it.",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    objects = LotQuerySet.as_manager()

    class Meta:
        ordering = ['position', 'name']

    def __str__(self):
        return self.name


class LotMember(models.Model):
    """The standing lot of a horse. No history: activities hold the history.

    A foreign key with a unique constraint, not a one-to-one field, so a
    later change to D6 (more than one lot for each horse) only drops the
    constraint. Read it through ``training.services.standing_lot``.
    """

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name='members')
    horse = models.ForeignKey('core.Horse', on_delete=models.CASCADE, related_name='lot_members')
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['position', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['horse'], name='lotmember_one_lot_per_horse'),
        ]

    def __str__(self):
        return f"{self.horse} in {self.lot}"


class HorseTraining(models.Model):
    """A horse's training settings. Made by the first POST that needs it,
    never by a GET. A horse with no row is Not in training."""

    class Status(models.TextChoices):
        IN_WORK = 'in_work', 'In work'
        LIGHT = 'light', 'Light work'
        PRE_TRAINING = 'pre_training', 'Pre-training'
        RESTING = 'resting', 'Resting'
        BOX_REST = 'box_rest', 'Box rest'
        NOT_IN_TRAINING = 'none', 'Not in training'

    # The statuses that go on the board (phase 2b).
    ON_BOARD = (Status.IN_WORK, Status.LIGHT, Status.PRE_TRAINING)

    horse = models.OneToOneField('core.Horse', on_delete=models.CASCADE, related_name='training')
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.NOT_IN_TRAINING)
    status_since = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)
    # Phase 3: what new board rows take (R5). A change for one day does not change these.
    default_rider = models.ForeignKey('Rider', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    default_rider_is_owner = models.BooleanField(default=False)
    default_gear = models.CharField(max_length=120, blank=True)
    # Phase 4 (D1): the exercise days a week the horse's livery includes.
    days_per_week = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text='Exercise days a week, for example from a ridden livery. Leave empty if none.',
    )
    history = HistoricalRecords()

    class Meta:
        verbose_name = 'horse training settings'
        verbose_name_plural = 'horse training settings'

    def __str__(self):
        return f"{self.horse}: {self.get_status_display()}"


class TrainingSettings(models.Model):
    """One row for the yard. Edited on Training, Set up, with Training full.

    Read it with ``TrainingSettings.load()``: it returns the defaults when no
    row exists and never writes (principle 5.3).
    """

    class BoardSource(models.TextChoices):
        LOTS = 'lots', 'From the lots'
        COPY = 'copy', 'Copy the last board'
        EMPTY = 'empty', 'Empty'
        WEEK_PLAN = 'week_plan', 'From the week plan'

    OFFERED_SOURCES = (BoardSource.WEEK_PLAN, BoardSource.LOTS, BoardSource.COPY, BoardSource.EMPTY)

    board_source = models.CharField(
        max_length=10, choices=BoardSource.choices, default=BoardSource.LOTS,
        help_text="How the lot board starts a new day.",
    )
    # Phase 4: "No work for N days" shows from this many days.
    no_work_days = models.PositiveSmallIntegerField(
        default=3, help_text='Show "No work for N days" for a horse in work after this many days.',
    )

    class Meta:
        verbose_name = 'training settings'
        verbose_name_plural = 'training settings'

    def __str__(self):
        return 'Training settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        return cls.objects.filter(pk=1).first() or cls(pk=1)



# ─── Phase 2b: the lot board ─────────────────────────────────────────

class LotDay(models.Model):
    """One lot on one date: its work, time, notes, and whether it is done."""

    class Status(models.TextChoices):
        PLANNED = 'planned', 'Planned'
        DONE = 'done', 'Done'
        CANCELLED = 'cancelled', 'Cancelled'

    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name='days')
    date = models.DateField()
    work_type = models.ForeignKey(WorkType, on_delete=models.PROTECT, related_name='lot_days')
    venue = models.ForeignKey('Venue', null=True, blank=True, on_delete=models.PROTECT, related_name='lot_days')
    start_time = models.TimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PLANNED)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ['date', 'lot__position', 'lot__name']
        constraints = [
            models.UniqueConstraint(fields=['lot', 'date'], name='lotday_one_per_lot_per_date'),
        ]
        indexes = [models.Index(fields=['date'], name='lotday_date')]

    def __str__(self):
        return f"{self.lot} · {self.date:%d %b %Y}"

    @property
    def is_open(self):
        return self.status == self.Status.PLANNED



# ─── Phase 3: venues, riders, the week plan ──────────────────────────

class ActiveQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


class Venue(models.Model):
    """Where the work happens, with its surface. For example "Woodchip gallop"."""

    class Surface(models.TextChoices):
        ALL_WEATHER = 'all_weather', 'All-weather'
        WOODCHIP = 'woodchip', 'Woodchip'
        GRASS = 'grass', 'Grass'
        SAND = 'sand', 'Sand'
        ARENA = 'arena', 'Arena'
        ROAD = 'road', 'Road or track'
        INDOOR = 'indoor', 'Indoor'
        WATER = 'water', 'Pool or water'
        OTHER = 'other', 'Other'

    name = models.CharField(max_length=60, unique=True)
    surface = models.CharField(max_length=12, choices=Surface.choices, default=Surface.OTHER)
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    objects = ActiveQuerySet.as_manager()

    class Meta:
        ordering = ['position', 'name']

    def __str__(self):
        return self.name


class Rider(models.Model):
    """A person who rides or handles horses: a name, and a login if they have one (D5)."""

    name = models.CharField(max_length=80)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='rider',
    )
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    objects = ActiveQuerySet.as_manager()

    class Meta:
        ordering = ['position', 'name']

    def __str__(self):
        return self.name


class LotWeekPlan(models.Model):
    """The usual work of a lot on one day of the week, or no lot that day."""

    WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name='week_plan')
    weekday = models.PositiveSmallIntegerField()   # 0 = Monday
    work_type = models.ForeignKey(WorkType, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    venue = models.ForeignKey(Venue, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    no_lot = models.BooleanField(default=False)

    class Meta:
        ordering = ['lot__position', 'weekday']
        constraints = [
            models.UniqueConstraint(fields=['lot', 'weekday'], name='lotweekplan_one_per_lot_day'),
            models.CheckConstraint(condition=Q(weekday__gte=0, weekday__lte=6), name='lotweekplan_weekday_0_to_6'),
            models.CheckConstraint(condition=Q(no_lot=True) | Q(work_type__isnull=False) | Q(venue__isnull=False),
                                   name='lotweekplan_says_something'),
        ]

    def __str__(self):
        return f"{self.lot} · {self.WEEKDAYS[self.weekday]}"
