"""
Billing and extra charges models.
"""

from decimal import Decimal

from django.core.validators import FileExtensionValidator, MinValueValidator

from core.models import validate_file_size
from django.db import models


class ServiceProvider(models.Model):
    """Service providers (vets, farriers, etc.)."""

    class ProviderType(models.TextChoices):
        VET = 'vet', 'Veterinarian'
        FARRIER = 'farrier', 'Farrier'
        DENTIST = 'dentist', 'Equine Dentist'
        PHYSIO = 'physio', 'Physiotherapist'
        SADDLER = 'saddler', 'Saddler'
        OTHER = 'other', 'Other'

    name = models.CharField(max_length=200)
    provider_type = models.CharField(
        max_length=20,
        choices=ProviderType.choices,
        default=ProviderType.OTHER
    )
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['provider_type', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_provider_type_display()})"


class ExtraCharge(models.Model):
    """Extra charges for services beyond standard livery."""

    class ChargeType(models.TextChoices):
        VET = 'vet', 'Veterinary'
        FARRIER = 'farrier', 'Farrier'
        VACCINATION = 'vaccination', 'Vaccination'
        FEED = 'feed', 'Feed/Hay'
        MEDICATION = 'medication', 'Medication'
        TRANSPORT = 'transport', 'Transport'
        EQUIPMENT = 'equipment', 'Equipment'
        DENTIST = 'dentist', 'Dentist'
        PHYSIO = 'physio', 'Physiotherapy'
        OTHER = 'other', 'Other'

    horse = models.ForeignKey(
        'core.Horse',
        on_delete=models.CASCADE,
        related_name='extra_charges'
    )
    owner = models.ForeignKey(
        'core.Owner',
        on_delete=models.PROTECT,
        related_name='extra_charges',
        help_text="Who pays for this charge"
    )
    service_provider = models.ForeignKey(
        ServiceProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='charges'
    )
    charge_type = models.CharField(
        max_length=20,
        choices=ChargeType.choices,
        default=ChargeType.OTHER
    )
    date = models.DateField()
    description = models.CharField(max_length=500)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    invoiced = models.BooleanField(default=False)
    invoice = models.ForeignKey(
        'core.Invoice',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='extra_charges'
    )
    receipt_image = models.ImageField(
        upload_to='receipts/%Y/%m/',
        blank=True, null=True,
        validators=[
            FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'pdf']),
            validate_file_size,
        ],
    )
    feed_out = models.ForeignKey(
        'FeedOut',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='extra_charges',
    )
    split_by_ownership = models.BooleanField(
        default=True,
        help_text="Split this charge among owners by their ownership %. "
                  "If unchecked, bill 100% to the specified owner."
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['invoiced', 'date'], name='charge_invoiced_date'),
            models.Index(fields=['horse', 'invoiced'], name='charge_horse_invoiced'),
        ]

    def __str__(self):
        return f"{self.horse.name} - {self.get_charge_type_display()}: £{self.amount} ({self.date})"

    def mark_as_invoiced(self, invoice):
        """Mark this charge as invoiced."""
        self.invoiced = True
        self.invoice = invoice
        self.save(update_fields=['invoiced', 'invoice'])


class YardCost(models.Model):
    """Yard-level costs not tied to a specific horse."""

    class CostCategory(models.TextChoices):
        HAY = 'hay', 'Hay'
        STRAW = 'straw', 'Straw/Bedding'
        FEED = 'feed', 'Feed'
        SUPPLEMENTS = 'supplements', 'Supplements'
        STAFF = 'staff', 'Staff/Wages'
        RENT = 'rent', 'Rent/Lease'
        FUEL = 'fuel', 'Fuel'
        UTILITIES = 'utilities', 'Utilities'
        REPAIRS = 'repairs', 'Repairs/Maintenance'
        INSURANCE = 'insurance', 'Insurance'
        EQUIPMENT = 'equipment', 'Equipment'
        PROFESSIONAL = 'professional', 'Professional Services'
        OTHER = 'other', 'Other'

    class RecurrenceInterval(models.TextChoices):
        WEEKLY = 'weekly', 'Weekly'
        MONTHLY = 'monthly', 'Monthly'
        QUARTERLY = 'quarterly', 'Quarterly'
        ANNUAL = 'annual', 'Annual'

    category = models.CharField(max_length=20, choices=CostCategory.choices)
    date = models.DateField()
    supplier = models.CharField(max_length=200, blank=True)
    description = models.CharField(max_length=500)
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    vat_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    is_recurring = models.BooleanField(default=False)
    recurrence_interval = models.CharField(
        max_length=20, choices=RecurrenceInterval.choices, blank=True
    )
    receipt_image = models.ImageField(
        upload_to='receipts/yard/%Y/%m/', blank=True, null=True,
        validators=[
            FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'pdf']),
            validate_file_size,
        ],
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['date', 'category'], name='yardcost_date_category'),
            models.Index(fields=['is_recurring'], name='yardcost_recurring'),
        ]

    def __str__(self):
        return f"{self.get_category_display()} - {self.description}: £{self.amount} ({self.date})"

    @property
    def total_with_vat(self):
        return self.amount + self.vat_amount


class FeedType(models.TextChoices):
    """Shared feed type choices used by FeedOut and FeedStock."""
    HAY = 'hay', 'Hay'
    HAYLAGE = 'haylage', 'Haylage'
    HARD_FEED = 'hard_feed', 'Hard Feed'
    SUPPLEMENTS = 'supplements', 'Supplements'
    OTHER = 'other', 'Other'


class FeedUnit(models.TextChoices):
    """Shared unit choices for feed quantities."""
    SMALL_BALES = 'small_bales', 'Small Bales'
    LARGE_BALES = 'large_bales', 'Large Bales'
    ROUND_BALES = 'round_bales', 'Round Bales'
    LARGE_SQUARE = 'large_square', 'Large Square Bales'
    BAGS = 'bags', 'Bags'
    KG = 'kg', 'Kilograms'
    TONNES = 'tonnes', 'Tonnes'


class FeedOut(models.Model):
    """Record of feed delivered to a field/location."""

    location = models.ForeignKey(
        'core.Location',
        on_delete=models.CASCADE,
        related_name='feed_outs',
    )
    date = models.DateField()
    feed_type = models.CharField(max_length=20, choices=FeedType.choices)
    quantity = models.CharField(
        max_length=100, blank=True,
        help_text="e.g. 2 bales, 10kg, half a round bale"
    )
    quantity_numeric = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Numeric quantity for stock tracking",
    )
    unit = models.CharField(
        max_length=20, choices=FeedUnit.choices, blank=True,
    )
    total_cost = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    notes = models.TextField(blank=True)
    is_recharged = models.BooleanField(
        default=False,
        help_text="Recharge this cost to horse owners in the field"
    )
    yard_cost = models.ForeignKey(
        YardCost,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='feed_outs',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['location', 'date'], name='feedout_location_date'),
        ]

    def __str__(self):
        return f"{self.get_feed_type_display()} - {self.location.name} ({self.date})"


class FeedStock(models.Model):
    """Records feed deliveries and adjustments for stock tracking."""

    class EntryType(models.TextChoices):
        DELIVERY = 'delivery', 'Delivery'
        ADJUSTMENT = 'adjustment', 'Adjustment'
        WASTE = 'waste', 'Waste'

    site = models.CharField(
        max_length=100, blank=True,
        help_text="Which site is this stock stored at (e.g. Colgate, Somerford)"
    )
    feed_type = models.CharField(max_length=20, choices=FeedType.choices)
    date = models.DateField()
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=20, choices=FeedUnit.choices)
    entry_type = models.CharField(
        max_length=20, choices=EntryType.choices, default=EntryType.DELIVERY,
    )
    supplier = models.CharField(max_length=200, blank=True)
    cost = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['feed_type', 'date'], name='feedstock_type_date'),
        ]

    def __str__(self):
        return f"{self.get_entry_type_display()}: {self.quantity} {self.get_unit_display()} {self.get_feed_type_display()} ({self.date})"
