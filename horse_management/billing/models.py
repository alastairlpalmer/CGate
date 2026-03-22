"""
Billing and extra charges models.
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
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
        blank=True,
        null=True
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
