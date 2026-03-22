"""
Health and care tracking models.
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.db import models
from django.utils import timezone


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
    vet_name = models.CharField(max_length=200, blank=True)
    batch_number = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
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
        super().save(*args, **kwargs)

    @property
    def is_due_soon(self):
        """Check if vaccination is due within reminder period."""
        from django.utils import timezone
        days_until = (self.next_due_date - timezone.now().date()).days
        return 0 <= days_until <= self.vaccination_type.reminder_days_before

    @property
    def is_overdue(self):
        """Check if vaccination is overdue."""
        if not self.next_due_date:
            return False
        from django.utils import timezone
        return timezone.now().date() > self.next_due_date


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
        super().save(*args, **kwargs)

    @property
    def is_due_soon(self):
        """Check if farrier visit is due within 2 weeks."""
        from django.utils import timezone
        if not self.next_due_date:
            return False
        days_until = (self.next_due_date - timezone.now().date()).days
        return 0 <= days_until <= 14

    @property
    def is_overdue(self):
        """Check if farrier visit is overdue."""
        from django.utils import timezone
        if not self.next_due_date:
            return False
        return timezone.now().date() > self.next_due_date


class WormingTreatment(models.Model):
    """Worming treatment record for a horse."""

    horse = models.ForeignKey(
        'core.Horse', on_delete=models.CASCADE, related_name='worming_treatments'
    )
    date = models.DateField()
    product_name = models.CharField(max_length=200, help_text="Brand name of wormer")
    active_ingredient = models.CharField(max_length=200, blank=True)
    dose = models.CharField(max_length=100, blank=True)
    administered_by = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.horse.name} - {self.product_name} ({self.date})"


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
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['horse', 'date'], name='egg_horse_date'),
        ]

    def __str__(self):
        return f"{self.horse.name} - {self.count} EPG ({self.date})"

    @property
    def is_high(self):
        return self.count > 200


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
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.COVERED
    )
    ehv_reminders_sent = models.CharField(
        max_length=20, blank=True,
        help_text="Comma-separated list of EHV reminder months already sent (e.g. 5,7)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date_covered']

    def __str__(self):
        return f"{self.mare.name} x {self.stallion_name} ({self.date_covered})"

    def save(self, *args, **kwargs):
        if not self.date_foal_due and self.date_covered:
            self.date_foal_due = self.date_covered + timedelta(days=340)
        super().save(*args, **kwargs)

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
