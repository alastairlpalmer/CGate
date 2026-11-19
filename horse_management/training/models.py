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
    def can_mark_done(self):
        return self.status == self.Status.PLANNED and self.date <= timezone.localdate()
