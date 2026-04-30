"""
Core models for horse management system.
"""

from datetime import date
from decimal import Decimal
from functools import cached_property

from django.core.exceptions import ValidationError as DjangoValidationError
from django.conf import settings
from django.core.validators import FileExtensionValidator, MinValueValidator, MaxValueValidator
from django.db import models, transaction
from django.db.models import F


def validate_file_size(value):
    """Reject uploads larger than 5MB."""
    if value.size > 5 * 1024 * 1024:
        raise DjangoValidationError("File size must be under 5MB.")


class Owner(models.Model):
    """Horse owner with contact information."""

    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    account_code = models.CharField(
        max_length=20,
        blank=True,
        help_text="Account code for accounting systems (e.g. Xero)"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @cached_property
    def active_horses(self):
        """Get horses currently placed with this owner."""
        return Horse.objects.filter(
            placements__owner=self,
            placements__end_date__isnull=True
        ).distinct()

    @cached_property
    def active_horse_count(self):
        return self.active_horses.count()

    @cached_property
    def active_horses_via_shares(self):
        """Get horses this owner has ownership shares in (active horses only)."""
        return Horse.objects.filter(
            ownership_shares__owner=self,
            is_active=True
        ).distinct()

    @cached_property
    def owned_horse_count(self):
        return self.active_horses_via_shares.count()


class Location(models.Model):
    """Physical location where horses are kept."""

    class Usage(models.TextChoices):
        HORSES = 'horses', 'Horses'
        MIXED = 'mixed', 'Mixed Grazing'
        RESTED = 'rested', 'Rested'
        HAY = 'hay', 'Hay'
        OTHER = 'other', 'Other'

    name = models.CharField(max_length=200)
    site = models.CharField(
        max_length=100,
        help_text="Main site name (e.g., Colgate, Somerford, California Farm)"
    )
    usage = models.CharField(
        max_length=20, choices=Usage.choices, default=Usage.HORSES,
    )
    description = models.TextField(blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['site', 'name']

    def __str__(self):
        return f"{self.site} — {self.name}"

    @cached_property
    def current_horses(self):
        """Get horses currently at this location."""
        return Horse.objects.filter(
            placements__location=self,
            placements__end_date__isnull=True
        ).distinct()

    @cached_property
    def current_horse_count(self):
        return self.current_horses.count()

    @cached_property
    def availability(self):
        """Return available spaces if capacity is set."""
        if self.capacity is not None:
            return self.capacity - self.current_horse_count
        return None


class Horse(models.Model):
    """Individual horse record."""

    class Sex(models.TextChoices):
        MARE = 'mare', 'Mare'
        GELDING = 'gelding', 'Gelding'
        STALLION = 'stallion', 'Stallion'
        COLT = 'colt', 'Colt'
        FILLY = 'filly', 'Filly'

    class Color(models.TextChoices):
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

    name = models.CharField(max_length=200)
    date_of_birth = models.DateField(null=True, blank=True, help_text="Date of birth")
    age = models.PositiveIntegerField(null=True, blank=True, help_text="Age in years (used if DOB unknown)")
    color = models.CharField(max_length=20, choices=Color.choices, blank=True)
    sex = models.CharField(max_length=20, choices=Sex.choices, blank=True)
    breeding = models.TextField(blank=True, help_text="Sire/dam information (free text)")
    dam = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='offspring_as_dam', help_text="Dam (mother) if she is in the system"
    )
    dam_name = models.CharField(max_length=200, blank=True, help_text="Dam (mother) name")
    sire_name = models.CharField(max_length=200, blank=True, help_text="Stallion name")
    photo = models.ImageField(
        upload_to='horses/', blank=True, null=True,
        validators=[
            FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'webp']),
            validate_file_size,
        ],
    )
    notes = models.TextField(blank=True, help_text="Special notes (e.g., first winter, lame, needs rug)")
    passport_number = models.CharField(max_length=100, blank=True)
    has_passport = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True, help_text="False if horse has left permanently")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def calculated_age(self):
        """Return age from DOB if set, else fall back to age field."""
        if self.date_of_birth:
            today = date.today()
            return today.year - self.date_of_birth.year - (
                (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
            )
        return self.age

    @property
    def is_mare(self):
        return self.sex == self.Sex.MARE

    @cached_property
    def foals(self):
        """Return offspring where this horse is the dam."""
        return Horse.objects.filter(dam=self)

    @cached_property
    def current_placement(self):
        """Get the current active placement.

        Iterates `self.placements.all()` so a `prefetch_related('placements')`
        on the queryset is honoured (otherwise this issues a fresh query per
        horse on list pages).
        """
        for placement in self.placements.all():
            if placement.end_date is None:
                return placement
        return None

    @cached_property
    def current_location(self):
        """Get the current location."""
        placement = self.current_placement
        return placement.location if placement else None

    @cached_property
    def current_owner(self):
        """Get the current owner -- prefer OwnershipShare, fall back to Placement."""
        primary = self.primary_owner
        if primary:
            return primary
        placement = self.current_placement
        return placement.owner if placement else None

    @cached_property
    def current_owners(self):
        """Get all current fractional owners with their share percentages.

        Returns a list of (owner, share_percentage) tuples.
        Falls back to placement owner at 100% if no ownership records exist.
        """
        shares = list(
            self.ownership_shares.select_related('owner').all()
        )
        if shares:
            return [(s.owner, s.share_percentage) for s in shares]
        # Fallback to placement owner
        if self.current_owner:
            return [(self.current_owner, Decimal('100.00'))]
        return []

    @cached_property
    def has_fractional_ownership(self):
        """Check if this horse has explicit ownership share records."""
        return self.ownership_shares.exists()

    @cached_property
    def primary_owner(self):
        """Get the primary contact owner, falling back to largest shareholder.

        Iterates `self.ownership_shares.all()` so a
        `prefetch_related('ownership_shares__owner')` is honoured. Without
        prefetching this issues a single query (vs the previous two).
        """
        shares = list(self.ownership_shares.all())
        if not shares:
            return None
        primary = next((s for s in shares if s.is_primary_contact), None)
        if primary is None:
            primary = max(shares, key=lambda s: s.share_percentage)
        return primary.owner

    @cached_property
    def owners(self):
        """Get all current owners via OwnershipShare."""
        return Owner.objects.filter(
            ownership_shares__horse=self
        ).distinct()

    @cached_property
    def has_multiple_owners(self):
        return self.ownership_shares.count() > 1


class RateType(models.Model):
    """Rate configuration for different livery types."""

    name = models.CharField(max_length=100)
    daily_rate = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['daily_rate']

    def __str__(self):
        return f"{self.name} (£{self.daily_rate}/day)"


class Placement(models.Model):
    """Tracks where a horse is located and who owns it."""

    horse = models.ForeignKey(
        Horse,
        on_delete=models.CASCADE,
        related_name='placements'
    )
    owner = models.ForeignKey(
        Owner,
        on_delete=models.PROTECT,
        related_name='placements'
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='placements'
    )
    rate_type = models.ForeignKey(
        RateType,
        on_delete=models.PROTECT,
        related_name='placements'
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    expected_departure = models.DateField(
        null=True, blank=True,
        help_text="Anticipated departure date (for forecasting)"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date']
        indexes = [
            models.Index(fields=['horse', 'end_date'], name='placement_horse_enddate'),
            models.Index(fields=['horse', 'location', 'end_date'], name='placement_horse_loc_end'),
            models.Index(fields=['horse', 'owner', 'end_date'], name='placement_horse_owner_end'),
            models.Index(fields=['end_date'], name='placement_enddate_solo'),
        ]
        constraints = [
            # Prevent a horse from having more than one open-ended placement
            models.UniqueConstraint(
                fields=['horse'],
                condition=models.Q(end_date__isnull=True),
                name='unique_active_placement_per_horse',
            ),
        ]

    def __str__(self):
        status = "current" if self.is_current else f"ended {self.end_date}"
        return f"{self.horse.name} at {self.location.name} ({status})"

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if not self.horse_id or not self.start_date:
            return

        if self.end_date and self.end_date < self.start_date:
            raise ValidationError(
                "Placement end date cannot be before start date."
            )

        # Find overlapping placements for the same horse
        overlapping = Placement.objects.filter(horse=self.horse)
        if self.pk:
            overlapping = overlapping.exclude(pk=self.pk)

        # A placement overlaps if it starts before this one ends
        # and ends after this one starts (or is still open)
        if self.end_date:
            # This placement has a defined range
            overlapping = overlapping.filter(
                start_date__lte=self.end_date
            ).exclude(
                end_date__isnull=False, end_date__lt=self.start_date
            )
        else:
            # This placement is open-ended — overlaps with anything
            # that hasn't ended before this one starts
            overlapping = overlapping.exclude(
                end_date__isnull=False, end_date__lt=self.start_date
            )

        if overlapping.exists():
            conflict = overlapping.first()
            end = conflict.end_date or "present"
            raise ValidationError(
                f"{self.horse.name} already has a placement from "
                f"{conflict.start_date} to {end} that overlaps with these dates."
            )

    def save(self, *args, **kwargs):
        # Always validate overlaps, even when clean() isn't called
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_current(self):
        return self.end_date is None

    @property
    def daily_rate(self):
        return self.rate_type.daily_rate

    def get_effective_dates_in_period(self, period_start, period_end):
        """Return (effective_start, effective_end) tuple for a billing period."""
        effective_start = max(self.start_date, period_start)
        effective_end = min(self.end_date or period_end, period_end)
        return (effective_start, effective_end)

    def get_days_in_period(self, period_start, period_end):
        """Calculate billable days within a billing period."""
        effective_start, effective_end = self.get_effective_dates_in_period(period_start, period_end)

        if effective_start > effective_end:
            return 0

        return (effective_end - effective_start).days + 1

    def calculate_charge(self, period_start, period_end):
        """Calculate the charge for this placement in a billing period."""
        days = self.get_days_in_period(period_start, period_end)
        return days * self.daily_rate


class HorseOwnership(models.Model):
    """Tracks fractional ownership of a horse by multiple owners.

    This allows horses to be owned by multiple owners with different
    percentage shares. Invoice charges are split according to these
    ownership percentages.
    """

    horse = models.ForeignKey(
        Horse,
        on_delete=models.CASCADE,
        related_name='ownerships'
    )
    owner = models.ForeignKey(
        Owner,
        on_delete=models.PROTECT,
        related_name='horse_ownerships'
    )
    share_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal('0.01')),
            MaxValueValidator(Decimal('100.00'))
        ],
        help_text="Ownership percentage (0.01 to 100.00)"
    )
    effective_from = models.DateField()
    effective_to = models.DateField(
        null=True,
        blank=True,
        help_text="Leave blank if ownership is ongoing"
    )
    is_billing_contact = models.BooleanField(
        default=False,
        help_text="Primary contact for billing communications about this horse"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-effective_from', 'owner__name']
        unique_together = [('horse', 'owner', 'effective_from')]
        verbose_name = "Horse Ownership"
        verbose_name_plural = "Horse Ownerships"

    def __str__(self):
        return f"{self.horse.name} - {self.owner.name} ({self.share_percentage}%)"

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()

        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValidationError("Effective end date cannot be before start date.")

    @property
    def is_current(self):
        """Check if this ownership is currently active."""
        today = date.today()
        if self.effective_from > today:
            return False
        if self.effective_to and self.effective_to < today:
            return False
        return True

    @classmethod
    def get_ownership_shares(cls, horse, as_of_date=None):
        """Get all active ownership shares for a horse on a given date.

        Returns a list of (owner, share_percentage) tuples.
        If no ownership records exist, returns empty list.
        """
        if as_of_date is None:
            as_of_date = date.today()

        ownerships = cls.objects.filter(
            horse=horse,
            effective_from__lte=as_of_date,
        ).filter(
            models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=as_of_date)
        ).select_related('owner')

        return [(o.owner, o.share_percentage) for o in ownerships]

    @classmethod
    def get_ownership_for_period(cls, horse, period_start, period_end):
        """Get ownership shares that overlap with a billing period.

        Returns a list of dicts with owner, percentage, and effective dates.
        """
        ownerships = cls.objects.filter(
            horse=horse,
            effective_from__lte=period_end,
        ).filter(
            models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=period_start)
        ).select_related('owner')

        result = []
        for ownership in ownerships:
            eff_start = max(ownership.effective_from, period_start)
            eff_end = min(ownership.effective_to or period_end, period_end)
            result.append({
                'owner': ownership.owner,
                'percentage': ownership.share_percentage,
                'effective_start': eff_start,
                'effective_end': eff_end,
            })
        return result


class OwnershipShare(models.Model):
    """Fractional ownership of a horse. Shares for a horse must total <= 100%."""

    horse = models.ForeignKey(
        Horse,
        on_delete=models.CASCADE,
        related_name='ownership_shares'
    )
    owner = models.ForeignKey(
        Owner,
        on_delete=models.PROTECT,
        related_name='ownership_shares'
    )
    share_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal('0.01')),
            MaxValueValidator(Decimal('100.00')),
        ],
        help_text="Ownership percentage (e.g. 50.00 for 50%)"
    )
    is_primary_contact = models.BooleanField(
        default=False,
        help_text="Primary contact for this horse"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-share_percentage']
        unique_together = [('horse', 'owner')]
        indexes = [
            models.Index(fields=['horse', 'owner'], name='ownership_horse_owner'),
        ]

    def __str__(self):
        return f"{self.owner.name} owns {self.share_percentage}% of {self.horse.name}"

    @property
    def share_fraction(self):
        """Return share as a decimal fraction (e.g. 0.50 for 50%)."""
        return self.share_percentage / Decimal('100')

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if not self.horse_id:
            return
        existing = OwnershipShare.objects.filter(horse=self.horse)
        if self.pk:
            existing = existing.exclude(pk=self.pk)
        total = sum(s.share_percentage for s in existing) + (self.share_percentage or Decimal('0'))
        if total > Decimal('100.00'):
            raise ValidationError(
                f"Total ownership for {self.horse.name} would be {total}%, "
                f"which exceeds 100%."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class BusinessSettings(models.Model):
    """Singleton model for business configuration."""

    business_name = models.CharField(max_length=200, default="Horse Livery")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    vat_registration = models.CharField(
        max_length=50,
        blank=True,
        default="N/A",
        help_text="VAT registration number, or N/A if not registered"
    )
    logo = models.ImageField(
        upload_to='business/', blank=True, null=True,
        validators=[
            FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'webp']),
            validate_file_size,
        ],
    )
    bank_details = models.TextField(blank=True, help_text="Bank details for payment")
    card_payment_url = models.URLField(
        blank=True,
        help_text="URL for online card payment (e.g. SumUp link)"
    )
    default_payment_terms = models.PositiveIntegerField(
        default=30,
        help_text="Default payment terms in days"
    )
    invoice_prefix = models.CharField(max_length=10, default="INV")
    next_invoice_number = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Business Settings"
        verbose_name_plural = "Business Settings"

    def __str__(self):
        return self.business_name

    def save(self, *args, **kwargs):
        # Ensure only one instance exists
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        """Get or create the singleton settings instance."""
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

    def get_next_invoice_number(self):
        """Get and atomically increment the next invoice number.

        Uses select_for_update inside a transaction so that two concurrent
        invoice creations cannot read the same value and emit duplicate
        invoice numbers.
        """
        with transaction.atomic():
            locked = BusinessSettings.objects.select_for_update().get(pk=self.pk)
            number = locked.next_invoice_number
            locked.next_invoice_number = number + 1
            locked.save(update_fields=['next_invoice_number'])
        self.next_invoice_number = number + 1
        return f"{self.invoice_prefix}{number:05d}"


class DashboardPreference(models.Model):
    """Per-user home dashboard layout (widget visibility + order)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='dashboard_preference',
    )
    # {"kpi_total_horses": {"visible": True, "order": 0}, ...}
    layout = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"DashboardPreference for {self.user}"

    @classmethod
    def get_for(cls, user):
        """Get-or-create the user's preference row (mirrors BusinessSettings.get_settings)."""
        obj, _ = cls.objects.get_or_create(user=user)
        return obj

    def resolved_layout(self):
        """Merge stored layout over DEFAULT_LAYOUT.

        New widgets added to the registry appear visible at the end of their
        group's order. Stored keys no longer in the registry are ignored.
        """
        from .dashboard_widgets import DEFAULT_LAYOUT, WIDGETS_BY_KEY
        resolved = {}
        for key, default in DEFAULT_LAYOUT.items():
            stored = self.layout.get(key) if isinstance(self.layout, dict) else None
            if isinstance(stored, dict):
                resolved[key] = {
                    "visible": bool(stored.get("visible", default["visible"])),
                    "order": int(stored.get("order", default["order"])),
                }
            else:
                resolved[key] = dict(default)
        # Drop any stale keys (defensive — DEFAULT_LAYOUT is already the filter).
        return {k: v for k, v in resolved.items() if k in WIDGETS_BY_KEY}

    def visible_ordered_keys_by_group(self):
        """Return {group: [key, ...]} filtered to visible keys, sorted by order."""
        from .dashboard_widgets import GROUPS, WIDGETS_BY_KEY
        layout = self.resolved_layout()
        grouped = {g: [] for g in GROUPS}
        ordered = sorted(layout.items(), key=lambda kv: kv[1]["order"])
        for key, meta in ordered:
            if not meta["visible"]:
                continue
            group = WIDGETS_BY_KEY[key]["group"]
            grouped[group].append(key)
        return grouped


# Invoice and InvoiceLineItem have been moved to invoicing.models.
# Re-exported here for backward compatibility with existing imports.
from invoicing.models import Invoice, InvoiceLineItem  # noqa: F401
