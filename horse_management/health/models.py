"""
Health and care tracking models.
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.db import models
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone


def exclude_superseded(queryset, *, date_field, group_fields):
    """Keep only each group's most recent record.

    Due/overdue logic must consider only the latest record per horse (and
    per vaccination type) — an annual booster's year-old predecessor has a
    next_due_date in the past *forever*, so without this every historical
    record shows as permanently overdue the day after a horse is re-treated.
    Ties on the date fall back to highest pk (the later entry).
    """
    model = queryset.model
    newer = model.objects.filter(
        **{field: OuterRef(field) for field in group_fields}
    ).filter(
        Q(**{f'{date_field}__gt': OuterRef(date_field)})
        | Q(**{date_field: OuterRef(date_field), 'pk__gt': OuterRef('pk')})
    )
    return queryset.annotate(
        _superseded=Exists(newer),
    ).filter(_superseded=False)


def current_vaccinations(queryset):
    """Latest vaccination per (horse, type) — the only records whose
    next_due_date is meaningful for due/overdue purposes."""
    return exclude_superseded(
        queryset, date_field='date_given',
        group_fields=('horse', 'vaccination_type'),
    )


def current_farrier_visits(queryset):
    """Latest farrier visit per horse."""
    return exclude_superseded(
        queryset, date_field='date', group_fields=('horse',),
    )


def current_worming_treatments(queryset):
    """Latest worming treatment per horse."""
    return exclude_superseded(
        queryset, date_field='date', group_fields=('horse',),
    )


class VaccinationType(models.Model):
    """Types of vaccinations with their schedules."""

    name = models.CharField(max_length=100)
    interval_months = models.PositiveIntegerField(
        default=12,
        help_text="Months between vaccinations"
    )
    reminder_days_before = models.PositiveIntegerField(
        default=30,
        help_text="Days before due date to send reminder"
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} (every {self.interval_months} months)"


class Vaccination(models.Model):
    """Individual vaccination record for a horse."""

    horse = models.ForeignKey(
        'core.Horse',
        on_delete=models.CASCADE,
        related_name='vaccinations'
    )
    vaccination_type = models.ForeignKey(
        VaccinationType,
        on_delete=models.PROTECT,
        related_name='vaccinations'
    )
    date_given = models.DateField()
    next_due_date = models.DateField(null=True, blank=True)
    vet = models.ForeignKey(
        'billing.ServiceProvider',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vaccinations',
        limit_choices_to={'provider_type': 'vet'},
    )
    batch_number = models.CharField(max_length=100, blank=True)
    cost = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Leave 0 if not billable; a cost creates a charge for the owner",
    )
    notes = models.TextField(blank=True)
    extra_charge = models.OneToOneField(
        'billing.ExtraCharge',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vaccination'
    )
    reminder_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date_given']
        indexes = [
            models.Index(fields=['horse', 'next_due_date'], name='vax_horse_nextdue'),
            models.Index(fields=['next_due_date'], name='vax_nextdue'),
        ]

    def __str__(self):
        return f"{self.horse.name} - {self.vaccination_type.name} ({self.date_given})"

    @staticmethod
    def _add_months(start_date, months):
        """Add calendar months to a date, clamping to last day of target month."""
        month = start_date.month - 1 + months
        year = start_date.year + month // 12
        month = month % 12 + 1
        day = min(start_date.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def save(self, *args, **kwargs):
        # Auto-calculate next due date if not set
        if not self.next_due_date:
            months = self.vaccination_type.interval_months
            self.next_due_date = self._add_months(self.date_given, months)
        # Re-arm the reminder when the due date moves (same rule as
        # Document expiry) — a pushed-out due date used to keep
        # reminder_sent=True and pass silently with no email, ever.
        if self.pk and self.reminder_sent:
            old_due = type(self).objects.filter(pk=self.pk).values_list(
                'next_due_date', flat=True
            ).first()
            if old_due != self.next_due_date:
                self.reminder_sent = False
        super().save(*args, **kwargs)

    @property
    def is_due_soon(self):
        """Check if vaccination is due within reminder period."""
        from django.utils import timezone
        if not self.next_due_date:
            return False
        days_until = (self.next_due_date - timezone.localdate()).days
        return 0 <= days_until <= self.vaccination_type.reminder_days_before

    @property
    def is_overdue(self):
        """Check if vaccination is overdue."""
        if not self.next_due_date:
            return False
        from django.utils import timezone
        return timezone.localdate() > self.next_due_date


class FarrierVisit(models.Model):
    """Farrier visit record."""

    class WorkType(models.TextChoices):
        TRIM = 'trim', 'Trim Only'
        FRONT_SHOES = 'front_shoes', 'Front Shoes'
        FULL_SET = 'full_set', 'Full Set'
        REMEDIAL = 'remedial', 'Remedial Work'
        REMOVE = 'remove', 'Shoe Removal'

    horse = models.ForeignKey(
        'core.Horse',
        on_delete=models.CASCADE,
        related_name='farrier_visits'
    )
    date = models.DateField()
    service_provider = models.ForeignKey(
        'billing.ServiceProvider',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='farrier_visits'
    )
    work_done = models.CharField(
        max_length=20,
        choices=WorkType.choices,
        default=WorkType.TRIM
    )
    next_due_date = models.DateField(null=True, blank=True)
    cost = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00')
    )
    notes = models.TextField(blank=True)
    extra_charge = models.OneToOneField(
        'billing.ExtraCharge',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='farrier_visit'
    )
    reminder_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['horse', 'next_due_date'], name='farrier_horse_nextdue'),
            models.Index(fields=['next_due_date'], name='farrier_nextdue'),
        ]

    def __str__(self):
        return f"{self.horse.name} - {self.get_work_done_display()} ({self.date})"

    def save(self, *args, **kwargs):
        # Auto-calculate next due date (typically 6-8 weeks)
        if not self.next_due_date:
            self.next_due_date = self.date + timedelta(weeks=6)
        # Re-arm the reminder when the due date moves — see Vaccination.save.
        if self.pk and self.reminder_sent:
            old_due = type(self).objects.filter(pk=self.pk).values_list(
                'next_due_date', flat=True
            ).first()
            if old_due != self.next_due_date:
                self.reminder_sent = False
        super().save(*args, **kwargs)

    @property
    def is_due_soon(self):
        """Check if farrier visit is due within 2 weeks."""
        from django.utils import timezone
        if not self.next_due_date:
            return False
        days_until = (self.next_due_date - timezone.localdate()).days
        return 0 <= days_until <= 14

    @property
    def is_overdue(self):
        """Check if farrier visit is overdue."""
        from django.utils import timezone
        if not self.next_due_date:
            return False
        return timezone.localdate() > self.next_due_date


class WormingTreatment(models.Model):
    """Worming treatment record for a horse."""

    horse = models.ForeignKey(
        'core.Horse', on_delete=models.CASCADE, related_name='worming_treatments'
    )
    date = models.DateField()
    next_due_date = models.DateField(
        null=True, blank=True,
        help_text="Left blank, this is set to 13 weeks after the dose",
    )
    product_name = models.CharField(max_length=200, help_text="Brand name of wormer")
    active_ingredient = models.CharField(max_length=200, blank=True)
    dose = models.CharField(max_length=100, blank=True)
    administered_by = models.CharField(max_length=200, blank=True)
    cost = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Leave 0 if not billable; a cost creates a charge for the owner",
    )
    notes = models.TextField(blank=True)
    extra_charge = models.OneToOneField(
        'billing.ExtraCharge',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='worming_treatment'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['horse', 'next_due_date'], name='worming_horse_nextdue'),
            models.Index(fields=['next_due_date'], name='worming_nextdue'),
        ]

    def __str__(self):
        return f"{self.horse.name} - {self.product_name} ({self.date})"

    def save(self, *args, **kwargs):
        # A 13-week interval is the common yard routine, and it is only a
        # starting point: the field is editable, so a vet's own programme
        # (or an egg count) overrides it.
        if not self.next_due_date:
            self.next_due_date = self.date + timedelta(weeks=13)
        super().save(*args, **kwargs)

    @property
    def is_due_soon(self):
        """Due within a fortnight."""
        if not self.next_due_date:
            return False
        days_until = (self.next_due_date - timezone.localdate()).days
        return 0 <= days_until <= 14

    @property
    def is_overdue(self):
        if not self.next_due_date:
            return False
        return timezone.localdate() > self.next_due_date


class WormEggCount(models.Model):
    """Worm egg count (faecal/saliva test) result."""

    class SampleType(models.TextChoices):
        FEC = 'fec', 'Faecal Egg Count (FEC)'
        SALIVA = 'saliva', 'Saliva Test'
        OTHER = 'other', 'Other'

    horse = models.ForeignKey(
        'core.Horse', on_delete=models.CASCADE, related_name='worm_egg_counts'
    )
    date = models.DateField()
    count = models.PositiveIntegerField(help_text="Eggs per gram (EPG)")
    lab_name = models.CharField(max_length=200, blank=True)
    sample_type = models.CharField(
        max_length=20, choices=SampleType.choices, default=SampleType.FEC
    )
    cost = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Leave 0 if not billable; a cost creates a charge for the owner",
    )
    notes = models.TextField(blank=True)
    extra_charge = models.OneToOneField(
        'billing.ExtraCharge',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='worm_egg_count'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # The egg-count key, in eggs per gram (EPG): the upper bound (exclusive)
    # of each band, its code, its label and the badge class the lists use.
    # low < 200, mild < 500, moderate < 1000, high from 1000 up.
    LEVELS = (
        (200, 'low', 'Low', 'badge-success'),
        (500, 'mild', 'Mild', 'badge-warning'),
        (1000, 'moderate', 'Moderate', 'badge-warning'),
        (None, 'high', 'High', 'badge-danger'),
    )
    LEVEL_KEY = "Key (EPG): low < 200 · mild 200–499 · moderate 500–999 · high 1000+"

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['horse', 'date'], name='egg_horse_date'),
        ]

    def __str__(self):
        return f"{self.horse.name} - {self.count} EPG ({self.date})"

    def _level_row(self):
        for upper, code, label, badge in self.LEVELS:
            if upper is None or self.count < upper:
                return code, label, badge
        return self.LEVELS[-1][1:]

    @property
    def level(self):
        """'low' | 'mild' | 'moderate' | 'high' per the egg-count key."""
        return self._level_row()[0]

    @property
    def level_label(self):
        return self._level_row()[1]

    @property
    def level_badge_class(self):
        return self._level_row()[2]

    @property
    def is_high(self):
        """Above the treatment threshold (200 EPG) — anything but 'low'."""
        return self.count >= 200


class MedicalCondition(models.Model):
    """Ongoing medical condition for a horse."""

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        RESOLVED = 'resolved', 'Resolved'
        MONITORING = 'monitoring', 'Monitoring'

    horse = models.ForeignKey(
        'core.Horse', on_delete=models.CASCADE, related_name='medical_conditions'
    )
    name = models.CharField(max_length=200, help_text="e.g. Laminitis, Sweet Itch")
    diagnosed_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.horse.name} - {self.name} ({self.get_status_display()})"


class VetVisit(models.Model):
    """Vet visit record for a horse."""

    horse = models.ForeignKey(
        'core.Horse', on_delete=models.CASCADE, related_name='vet_visits'
    )
    date = models.DateField()
    vet = models.ForeignKey(
        'billing.ServiceProvider', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='vet_visits'
    )
    reason = models.CharField(max_length=500)
    diagnosis = models.TextField(blank=True)
    treatment = models.TextField(blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    cost = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    extra_charge = models.OneToOneField(
        'billing.ExtraCharge', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='vet_visit'
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['horse', 'follow_up_date'], name='vet_horse_followup'),
        ]

    def __str__(self):
        return f"{self.horse.name} - {self.reason} ({self.date})"


class BreedingRecord(models.Model):
    """Breeding and foaling record for a mare."""

    class Status(models.TextChoices):
        COVERED = 'covered', 'Covered'
        CONFIRMED = 'confirmed', 'Confirmed In-Foal'
        BORN = 'born', 'Born'
        LOST = 'lost', 'Lost'
        BARREN = 'barren', 'Barren'

    class FoalSex(models.TextChoices):
        COLT = 'colt', 'Colt'
        FILLY = 'filly', 'Filly'

    class FoalColour(models.TextChoices):
        BAY = 'bay', 'Bay'
        CHESTNUT = 'chestnut', 'Chestnut'
        GREY = 'grey', 'Grey'
        BLACK = 'black', 'Black'
        BROWN = 'brown', 'Brown'
        PALOMINO = 'palomino', 'Palomino'
        SKEWBALD = 'skewbald', 'Skewbald'
        PIEBALD = 'piebald', 'Piebald'
        ROAN = 'roan', 'Roan'
        DUN = 'dun', 'Dun'
        CREAM = 'cream', 'Cream'
        OTHER = 'other', 'Other'

    mare = models.ForeignKey(
        'core.Horse', on_delete=models.CASCADE, related_name='breeding_records',
        limit_choices_to={'sex': 'mare'}
    )
    stallion_name = models.CharField(max_length=200)
    date_covered = models.DateField()
    date_scanned_14_days = models.DateField(null=True, blank=True, help_text="In-foal scan")
    date_scanned_heartbeat = models.DateField(null=True, blank=True)
    date_foal_due = models.DateField(null=True, blank=True)
    foal = models.ForeignKey(
        'core.Horse', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='birth_record'
    )
    foal_dob = models.DateField(null=True, blank=True)
    foal_sex = models.CharField(max_length=20, choices=FoalSex.choices, blank=True)
    foal_colour = models.CharField(max_length=20, choices=FoalColour.choices, blank=True)
    foal_microchip = models.CharField(max_length=100, blank=True)
    foaling_notes = models.TextField(blank=True)
    weaned_date = models.DateField(
        null=True, blank=True, help_text="When the foal was weaned (split from the mare)",
    )
    weaning_notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.COVERED
    )
    ehv_reminders_sent = models.CharField(
        max_length=20, blank=True,
        help_text="Comma-separated list of EHV reminder months already sent (e.g. 5,7)"
    )
    due_date_is_manual = models.BooleanField(
        default=False,
        help_text="True when the foal due date was typed in rather than worked "
                  "out as 340 days from the last covering.",
    )
    breeding_reminders_sent = models.CharField(
        max_length=200, blank=True,
        help_text="Comma-separated keys of owner reminders already sent "
                  "(scan14, heartbeat, foal30, foal7)",
    )
    foaling_watch = models.JSONField(
        default=dict, blank=True,
        help_text='Foaling-watch checklist: {"items": {key: true}, "notes": ""}',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date_covered']

    def __str__(self):
        return f"{self.mare.name} x {self.stallion_name} ({self.date_covered})"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loaded_date_covered = self.date_covered

    def save(self, *args, **kwargs):
        if self.date_covered and not self.due_date_is_manual:
            self.date_foal_due = self.date_covered + timedelta(days=self.GESTATION_DAYS)
        elif not self.date_foal_due and self.date_covered:
            self.date_foal_due = self.date_covered + timedelta(days=self.GESTATION_DAYS)
        super().save(*args, **kwargs)
        self._sync_coverings_from_date()
        self._loaded_date_covered = self.date_covered

    def _sync_coverings_from_date(self):
        """Keep the covering history and ``date_covered`` in step.

        A record created straight from the form (one date) gets its first
        Covering row; editing that date on a record with a single covering
        moves the row. Records with several coverings are driven the other
        way round: their ``date_covered`` follows the latest Covering.
        """
        if getattr(self, '_syncing_coverings', False) or not self.date_covered:
            return
        coverings = list(self.coverings.all())
        if not coverings:
            Covering(record=self, date=self.date_covered).save(_skip_sync=True)
        elif (len(coverings) == 1
              and self._loaded_date_covered != self.date_covered
              and coverings[0].date != self.date_covered):
            coverings[0].date = self.date_covered
            coverings[0].save(update_fields=['date'], _skip_sync=True)

    def sync_from_coverings(self):
        """Recompute ``date_covered`` (and the due date unless typed in)
        from the covering rows. Called by Covering.save/delete."""
        latest = self.coverings.order_by('-date').values_list('date', flat=True).first()
        if latest is None:
            return
        changed = []
        if self.date_covered != latest:
            self.date_covered = latest
            changed.append('date_covered')
        if not self.due_date_is_manual:
            due = latest + timedelta(days=self.GESTATION_DAYS)
            if self.date_foal_due != due:
                self.date_foal_due = due
                changed.append('date_foal_due')
        if changed:
            self._syncing_coverings = True
            try:
                super().save(update_fields=changed + ['updated_at'])
            finally:
                self._syncing_coverings = False
            self._loaded_date_covered = self.date_covered

    @property
    def ehv_vaccination_dates(self):
        """Calculate EHV 1,4 vaccination dates at months 5, 7, 9 from covering."""
        if not self.date_covered:
            return {}
        return {
            5: Vaccination._add_months(self.date_covered, 5),
            7: Vaccination._add_months(self.date_covered, 7),
            9: Vaccination._add_months(self.date_covered, 9),
        }

    @property
    def sent_ehv_months(self):
        if not self.ehv_reminders_sent:
            return set()
        return {int(m) for m in self.ehv_reminders_sent.split(',') if m.strip()}

    # ── Operational helpers (used by the breeding list and the mare page) ──

    GESTATION_DAYS = 340
    ACTIVE_STATUSES = ('covered', 'confirmed')
    HEARTBEAT_SCAN_BY_DAY = 60

    @property
    def season(self):
        """The breeding season (year of the first covering)."""
        first = self.first_covering_date
        return first.year if first else (self.date_covered.year if self.date_covered else None)

    @property
    def first_covering_date(self):
        dates = [c.date for c in self.coverings.all()]
        return min(dates) if dates else self.date_covered

    @property
    def covering_count(self):
        return self.coverings.count()

    @property
    def sent_reminder_keys(self):
        if not self.breeding_reminders_sent:
            return set()
        return {k.strip() for k in self.breeding_reminders_sent.split(',') if k.strip()}

    @property
    def can_add_covering(self):
        """A covering can be added while the mare is not confirmed in foal
        and has not foaled: first covers, repeat covers in the same cycle,
        and re-covers after a negative scan."""
        return self.status in ('covered', 'barren')

    # ── Foal helpers (the foal's own page and the mare's Foals table) ──

    @property
    def foal_days_old(self):
        if not self.foal_dob:
            return None
        return (timezone.localdate() - self.foal_dob).days

    @property
    def foal_age_label(self):
        days = self.foal_days_old
        if days is None or days < 0:
            return ''
        if days < 14:
            return f'{days} day{"s" if days != 1 else ""} old'
        if days < 90:
            weeks = days // 7
            return f'{weeks} week{"s" if weeks != 1 else ""} old'
        months = days // 30
        if days < 365:
            return f'{months} month{"s" if months != 1 else ""} old'
        years = days // 365
        return f'{years} year{"s" if years != 1 else ""} old'

    @property
    def is_weaned(self):
        return self.weaned_date is not None

    @property
    def can_wean(self):
        return self.status == self.Status.BORN and self.foal_dob is not None and not self.is_weaned

    @property
    def weaning_due(self):
        """A guide date: six months from birth."""
        if not self.foal_dob:
            return None
        return Vaccination._add_months(self.foal_dob, 6)

    # Foaling-watch checklist, in the order the yard works through it.
    FOALING_WATCH_ITEMS = (
        ('foaling_box', 'Moved to foaling box / paddock'),
        ('udder', 'Udder filling or waxing seen'),
        ('milk', 'Milk calcium test started'),
        ('vet', 'Vet on call confirmed'),
        ('kit', 'Foaling kit and camera ready'),
        ('owner', 'Owner informed of the plan'),
    )
    FOALING_WATCH_DAYS = 21

    @property
    def watch_items(self):
        """[(key, label, done)] for the foaling-watch checklist."""
        done = (self.foaling_watch or {}).get('items', {})
        return [(key, label, bool(done.get(key))) for key, label in self.FOALING_WATCH_ITEMS]

    @property
    def watch_done_count(self):
        return sum(1 for _, _, done in self.watch_items if done)

    @property
    def watch_notes(self):
        return (self.foaling_watch or {}).get('notes', '')

    @property
    def on_foaling_watch(self):
        """Within three weeks of the due date, or past it, and still carrying."""
        days = self.days_to_foal_due
        return days is not None and days <= self.FOALING_WATCH_DAYS

    @property
    def scan_14_result(self):
        return self._latest_scan_result(PregnancyScan.ScanType.DAY_14)

    @property
    def scan_heartbeat_result(self):
        return self._latest_scan_result(PregnancyScan.ScanType.HEARTBEAT)

    def _latest_scan_result(self, scan_type):
        scans = [s for s in self.scans.all() if s.scan_type == scan_type]
        if not scans:
            return ''
        return max(scans, key=lambda s: (s.date, s.pk)).result

    @property
    def is_active_pregnancy(self):
        """Covered or confirmed: the mare is (or may be) carrying."""
        return self.status in self.ACTIVE_STATUSES

    @property
    def can_record_foaling(self):
        """Foaling can be recorded while the pregnancy is still open."""
        return self.is_active_pregnancy and self.foal_id is None

    @property
    def next_scan(self):
        """Which scan is still to come on an open pregnancy: '14-day',
        'heartbeat', or None when both are in (or the record is closed)."""
        if not self.is_active_pregnancy:
            return None
        if self.status == self.Status.COVERED and not self.date_scanned_14_days:
            return '14-day'
        # A confirmed record without a heartbeat scan wants one early on; a
        # reconciled older record well past that point is left alone.
        day = self.day_of_gestation
        if not self.date_scanned_heartbeat and day is not None and day <= self.HEARTBEAT_SCAN_BY_DAY:
            return 'heartbeat'
        return None

    @property
    def day_of_gestation(self):
        """Days since covering (today), or None when not applicable."""
        if not self.date_covered or not self.is_active_pregnancy:
            return None
        return (timezone.localdate() - self.date_covered).days

    @property
    def gestation_progress(self):
        """0-100 percent of a 340-day gestation, clamped."""
        day = self.day_of_gestation
        if day is None:
            return 0
        return max(0, min(100, round(day * 100 / self.GESTATION_DAYS)))

    @property
    def days_to_foal_due(self):
        """Signed days until the due date (negative = past)."""
        if not self.date_foal_due or not self.is_active_pregnancy:
            return None
        return (self.date_foal_due - timezone.localdate()).days

    @property
    def is_due_soon(self):
        days = self.days_to_foal_due
        return days is not None and 0 <= days <= 14

    @property
    def is_past_due(self):
        days = self.days_to_foal_due
        return days is not None and days < 0

    @property
    def next_ehv(self):
        """The next EHV-1,4 dose still to come: {'month', 'date', 'sent'}.

        Only for open pregnancies. ``sent`` says whether the owner reminder
        for that month has already gone out.
        """
        if not self.is_active_pregnancy:
            return None
        today = timezone.localdate()
        sent = self.sent_ehv_months
        for month, ehv_date in sorted(self.ehv_vaccination_dates.items()):
            if ehv_date >= today:
                return {'month': month, 'date': ehv_date, 'sent': month in sent}
        return None

class Covering(models.Model):
    """One covering (or insemination) of a mare within a breeding record.

    A mare is usually covered two or three times in a cycle, and covered
    again after a negative scan. The record's ``date_covered`` follows the
    latest covering, and the foal due date follows it unless typed in.
    """

    class Method(models.TextChoices):
        NATURAL = 'natural', 'Natural cover'
        AI_FRESH = 'ai_fresh', 'AI — fresh'
        AI_CHILLED = 'ai_chilled', 'AI — chilled'
        AI_FROZEN = 'ai_frozen', 'AI — frozen'
        UNKNOWN = '', 'Not recorded'

    record = models.ForeignKey(
        BreedingRecord, on_delete=models.CASCADE, related_name='coverings',
    )
    date = models.DateField()
    method = models.CharField(max_length=20, choices=Method.choices, blank=True)
    stallion_name = models.CharField(
        max_length=200, blank=True,
        help_text="Leave blank when it is the record's stallion",
    )
    vet = models.ForeignKey(
        'billing.ServiceProvider', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='coverings',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'pk']

    def __str__(self):
        return f"{self.record.mare.name} covered {self.date}"

    @property
    def stallion(self):
        return self.stallion_name or self.record.stallion_name

    def save(self, *args, _skip_sync=False, **kwargs):
        super().save(*args, **kwargs)
        if not _skip_sync:
            self.record.sync_from_coverings()

    def delete(self, *args, **kwargs):
        record = self.record
        super().delete(*args, **kwargs)
        record.sync_from_coverings()


class FoalNote(models.Model):
    """A dated note on a foal: how it is doing, what was done, what to
    watch. Kept on the breeding record so the foal's story stays with its
    birth even before it has a horse record of its own."""

    record = models.ForeignKey(
        BreedingRecord, on_delete=models.CASCADE, related_name='foal_notes',
    )
    date = models.DateField()
    note = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-pk']

    def __str__(self):
        return f"{self.record.mare.name}'s foal, {self.date}: {self.note[:40]}"


class PregnancyScan(models.Model):
    """One pregnancy scan on a breeding record, with its result.

    The record keeps ``date_scanned_14_days`` / ``date_scanned_heartbeat``
    as the latest positive dates (the EHV schedule and older screens use
    them); this is the full history, negatives included, so a season with
    a re-cover reads as one story.
    """

    class ScanType(models.TextChoices):
        DAY_14 = '14_day', '14-day scan'
        HEARTBEAT = 'heartbeat', 'Heartbeat scan'
        SEX = 'sex', 'Sexing scan'
        OTHER = 'other', 'Other scan'

    class Result(models.TextChoices):
        IN_FOAL = 'in_foal', 'In foal'
        NOT_IN_FOAL = 'not_in_foal', 'Not in foal'
        TWINS = 'twins', 'Twins'
        INCONCLUSIVE = 'inconclusive', 'Inconclusive'

    record = models.ForeignKey(
        BreedingRecord, on_delete=models.CASCADE, related_name='scans',
    )
    date = models.DateField()
    scan_type = models.CharField(max_length=20, choices=ScanType.choices)
    result = models.CharField(max_length=20, choices=Result.choices)
    vet = models.ForeignKey(
        'billing.ServiceProvider', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pregnancy_scans',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'pk']

    def __str__(self):
        return f"{self.record.mare.name} {self.get_scan_type_display()} {self.date}: {self.get_result_display()}"

    @property
    def positive(self):
        return self.result in (self.Result.IN_FOAL, self.Result.TWINS)

