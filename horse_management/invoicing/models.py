"""
Invoice models — moved from core app.

Uses db_table to keep original table names, avoiding database migration changes.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Invoice(models.Model):
    """Invoice for an owner covering a billing period."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SENT = 'sent', 'Sent'
        PAID = 'paid', 'Paid'
        OVERDUE = 'overdue', 'Overdue'
        CANCELLED = 'cancelled', 'Cancelled'

    owner = models.ForeignKey(
        'core.Owner',
        on_delete=models.PROTECT,
        related_name='invoices'
    )
    invoice_number = models.CharField(max_length=50, unique=True)
    period_start = models.DateField()
    period_end = models.DateField()
    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00')
    )
    total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00')
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT
    )
    payment_terms_days = models.PositiveIntegerField(default=30)
    due_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'core_invoice'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['owner', 'status'], name='invoice_owner_status'),
        ]

    def __str__(self):
        return f"{self.invoice_number} - {self.owner.name}"

    def save(self, *args, **kwargs):
        # Auto-calculate due date if not set
        if not self.due_date:
            self.due_date = self.period_end + timedelta(days=self.payment_terms_days)
        super().save(*args, **kwargs)

    def recalculate_totals(self):
        """Recalculate invoice totals from line items."""
        self.subtotal = sum(item.line_total for item in self.line_items.all())
        self.total = self.subtotal  # No tax for now
        self.save(update_fields=['subtotal', 'total'])

    def mark_as_sent(self):
        """Mark invoice as sent."""
        self.status = self.Status.SENT
        self.sent_at = timezone.now()
        self.save(update_fields=['status', 'sent_at'])

    def mark_as_paid(self):
        """Mark invoice as paid."""
        self.status = self.Status.PAID
        self.paid_at = timezone.now()
        self.save(update_fields=['status', 'paid_at'])

    @property
    def is_overdue(self):
        """Check if invoice is overdue."""
        if self.status in [self.Status.PAID, self.Status.CANCELLED]:
            return False
        if not self.due_date:
            return False
        return timezone.now().date() > self.due_date


class InvoiceLineItem(models.Model):
    """Individual line item on an invoice."""

    class LineType(models.TextChoices):
        LIVERY = 'livery', 'Livery'
        VET = 'vet', 'Veterinary'
        FARRIER = 'farrier', 'Farrier'
        VACCINATION = 'vaccination', 'Vaccination'
        FEED = 'feed', 'Feed'
        OTHER = 'other', 'Other'

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='line_items'
    )
    horse = models.ForeignKey(
        'core.Horse',
        on_delete=models.PROTECT,
        related_name='invoice_items',
        null=True,
        blank=True
    )
    placement = models.ForeignKey(
        'core.Placement',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoice_items'
    )
    charge = models.ForeignKey(
        'billing.ExtraCharge',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoice_items'
    )
    line_type = models.CharField(
        max_length=20,
        choices=LineType.choices,
        default=LineType.LIVERY
    )
    description = models.CharField(max_length=500)
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('1.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    line_total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    share_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('100.00'),
        help_text="Ownership share % at time of invoicing"
    )

    class Meta:
        db_table = 'core_invoicelineitem'
        ordering = ['line_type', 'description']

    def __str__(self):
        return f"{self.description}: £{self.line_total}"

    def save(self, *args, **kwargs):
        # Auto-calculate line total unless explicitly provided
        if self.line_total is None:
            self.line_total = (self.quantity * self.unit_price).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)
