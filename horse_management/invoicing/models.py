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
    last_overdue_reminder_at = models.DateTimeField(null=True, blank=True)

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

    @property
    def amount_paid(self):
        """Sum of recorded payments against this invoice."""
        return (
            self.payments.aggregate(total=models.Sum('amount'))['total']
            or Decimal('0.00')
        )

    @property
    def balance_due(self):
        """What's still owed after recorded payments."""
        return self.total - self.amount_paid

    def mark_as_paid(self, method='other', reference=''):
        """Mark invoice as paid, recording a balancing payment.

        The balancing payment keeps the ledger complete when staff use the
        one-click "Mark as Paid" instead of recording each payment — without
        it, amount_paid would disagree with the PAID status.
        """
        balance = self.balance_due
        if balance > 0:
            Payment.objects.create(
                invoice=self,
                date=timezone.now().date(),
                amount=balance,
                method=method,
                reference=reference,
            )
        self.status = self.Status.PAID
        self.paid_at = timezone.now()
        self.save(update_fields=['status', 'paid_at'])

    def refresh_payment_status(self):
        """Sync status with the payment ledger after a payment is added,
        edited, or deleted.

        - Fully paid → PAID.
        - A payment against a DRAFT promotes it to SENT (it has clearly been
          issued, e.g. hand-delivered to a cash-paying owner).
        - A PAID invoice whose payments no longer cover the total (payment
          deleted) reverts to SENT/OVERDUE by due date.
        """
        if self.status == self.Status.CANCELLED:
            return
        if self.total > 0 and self.balance_due <= 0:
            if self.status != self.Status.PAID:
                self.status = self.Status.PAID
                self.paid_at = self.paid_at or timezone.now()
                self.save(update_fields=['status', 'paid_at'])
        elif self.status == self.Status.PAID:
            self.status = (
                self.Status.OVERDUE
                if self.due_date and timezone.now().date() > self.due_date
                else self.Status.SENT
            )
            self.paid_at = None
            self.save(update_fields=['status', 'paid_at'])
        elif self.status == self.Status.DRAFT and self.amount_paid > 0:
            self.mark_as_sent()

    def release_extra_charges(self):
        """Un-invoice extra charges tied to this invoice so a replacement
        invoice can bill them again.

        Must be called when the invoice is cancelled — otherwise the charges
        stay ``invoiced=True`` pointing at a dead invoice and silently drop
        out of any replacement. Covers both charges whose ``invoice`` FK is
        this invoice and split charges with a line item here but an FK set by
        a co-owner's invoice. Re-billing co-owners already billed on live
        invoices is prevented in ``InvoiceService.get_unbilled_charges``.

        Returns the number of charges released.
        """
        from billing.models import ExtraCharge

        charge_ids = set(
            self.line_items.exclude(charge=None).values_list('charge_id', flat=True)
        )
        charge_ids |= set(self.extra_charges.values_list('id', flat=True))
        released = ExtraCharge.objects.filter(
            id__in=charge_ids, invoiced=True
        ).update(invoiced=False, invoice=None)
        return released

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


class Payment(models.Model):
    """A payment (possibly partial) recorded against an invoice."""

    class Method(models.TextChoices):
        BANK_TRANSFER = 'bank_transfer', 'Bank Transfer'
        CASH = 'cash', 'Cash'
        CARD = 'card', 'Card'
        CHEQUE = 'cheque', 'Cheque'
        XERO = 'xero', 'Paid in Xero'
        OTHER = 'other', 'Other'

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='payments'
    )
    date = models.DateField()
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    method = models.CharField(
        max_length=20,
        choices=Method.choices,
        default=Method.BANK_TRANSFER,
    )
    reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="e.g. bank reference or cheque number"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"£{self.amount} against {self.invoice.invoice_number} on {self.date}"
