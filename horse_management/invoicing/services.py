"""
Invoice calculation and generation services.

Supports fractional ownership: charges are split by OwnershipShare percentages.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import (
    BusinessSettings,
    Horse,
    Owner,
    OwnershipShare,
    Placement,
)
from invoicing.models import Invoice, InvoiceLineItem, InvoiceRun
from .utils import format_date_short, format_date_short_year, group_preview_charges_by_horse


class DuplicateInvoiceError(Exception):
    """Raised when an invoice would overlap with an existing one."""
    pass


class InvoiceService:
    """Service for generating and managing invoices."""

    @staticmethod
    def check_for_overlapping_invoices(owner, period_start, period_end, horse=None):
        """Check if an invoice already exists for this owner overlapping the given period.

        When ``horse`` is None (concatenated/ON mode), any existing non-cancelled
        invoice for the owner in the period blocks creation.

        When ``horse`` is given (per-horse/OFF mode), an existing invoice blocks
        creation if it either (a) targets the same horse, or (b) has no horse set
        (meaning a concatenated invoice already covers all of the owner's horses
        for that period).
        """
        qs = Invoice.objects.filter(
            owner=owner,
            period_start__lte=period_end,
            period_end__gte=period_start,
        ).exclude(
            status=Invoice.Status.CANCELLED,
        )
        if horse is not None:
            qs = qs.filter(Q(horse=horse) | Q(horse__isnull=True))
        return qs.first()

    @staticmethod
    def calculate_livery_charges(owner, period_start, period_end, horse=None):
        """Calculate livery charges for an owner based on ownership shares.

        If ``horse`` is provided, restrict to charges for that single horse.
        """
        charges = []

        shares = OwnershipShare.objects.filter(
            owner=owner
        ).select_related('horse')
        if horse is not None:
            shares = shares.filter(horse=horse)

        for share in shares:
            # Find placements for this horse overlapping the period
            placements = Placement.objects.filter(
                horse=share.horse,
                start_date__lte=period_end,
            ).exclude(
                end_date__lt=period_start
            ).select_related('horse', 'location', 'rate_type')

            for placement in placements:
                days = placement.get_days_in_period(period_start, period_end)
                if days > 0:
                    full_amount = placement.calculate_charge(period_start, period_end)
                    owner_amount = (full_amount * share.share_fraction).quantize(Decimal('0.01'))
                    eff_start, eff_end = placement.get_effective_dates_in_period(
                        period_start, period_end
                    )

                    rate_str = f"£{placement.daily_rate:g}"
                    date_from = format_date_short(eff_start)
                    date_to = format_date_short_year(eff_end)

                    share_note = ""
                    if share.share_percentage < Decimal('100'):
                        share_note = f" ({share.share_percentage:g}% share)"

                    description = (
                        f"{placement.rate_type.name} {rate_str} per day "
                        f"- {days} days ({date_from} to {date_to}){share_note}"
                    )
                    charges.append({
                        'horse': placement.horse,
                        'placement': placement,
                        'description': description,
                        'days': days,
                        'daily_rate': placement.daily_rate,
                        'full_amount': full_amount,
                        'amount': owner_amount,
                        'share_percentage': share.share_percentage,
                        'line_type': 'livery',
                    })

        return charges

    @staticmethod
    def get_unbilled_charges(owner, period_end, horse=None):
        """Get extra charges for this owner, handling ownership splits.

        Two cases:
        - split_by_ownership=False: charge goes 100% to the specified owner
        - split_by_ownership=True: charge is split among co-owners by share %

        If ``horse`` is provided, restrict to charges for that single horse.
        """
        charges = []

        # Case 1: Direct charges (no split) — bill to specified owner
        direct_charges = ExtraCharge.objects.filter(
            owner=owner,
            invoiced=False,
            date__lte=period_end,
            split_by_ownership=False,
        ).select_related('horse', 'service_provider')
        if horse is not None:
            direct_charges = direct_charges.filter(horse=horse)

        for charge in direct_charges:
            charges.append({
                'horse': charge.horse,
                'charge': charge,
                'description': f"{charge.get_charge_type_display()} - {charge.description}",
                'date': charge.date,
                'days': 1,
                'daily_rate': charge.amount,
                'full_amount': charge.amount,
                'amount': charge.amount,
                'share_percentage': Decimal('100.00'),
                'line_type': charge.charge_type,
            })

        # Case 2: Split charges — find charges on horses this owner has shares in
        owner_shares = OwnershipShare.objects.filter(owner=owner).select_related('horse')
        if horse is not None:
            owner_shares = owner_shares.filter(horse=horse)
        horse_share_map = {s.horse_id: s for s in owner_shares}

        if horse_share_map:
            split_charges = ExtraCharge.objects.filter(
                horse_id__in=horse_share_map.keys(),
                invoiced=False,
                date__lte=period_end,
                split_by_ownership=True,
            ).select_related('horse', 'service_provider')

            for charge in split_charges:
                share = horse_share_map[charge.horse_id]
                owner_amount = (charge.amount * share.share_fraction).quantize(Decimal('0.01'))

                share_note = ""
                if share.share_percentage < Decimal('100'):
                    share_note = f" ({share.share_percentage:g}% share)"

                charges.append({
                    'horse': charge.horse,
                    'charge': charge,
                    'description': f"{charge.get_charge_type_display()} - {charge.description}{share_note}",
                    'date': charge.date,
                    'days': 1,
                    'daily_rate': charge.amount,
                    'full_amount': charge.amount,
                    'amount': owner_amount,
                    'share_percentage': share.share_percentage,
                    'line_type': charge.charge_type,
                })

        return charges

    @classmethod
    def calculate_invoice_preview(cls, owner, period_start, period_end, horse=None):
        """Calculate a preview of invoice charges without creating anything."""
        livery_charges = cls.calculate_livery_charges(owner, period_start, period_end, horse=horse)
        extra_charges = cls.get_unbilled_charges(owner, period_end, horse=horse)

        all_charges = livery_charges + extra_charges
        subtotal = sum(c['amount'] for c in all_charges)
        horse_groups = group_preview_charges_by_horse(all_charges)

        return {
            'livery_charges': livery_charges,
            'extra_charges': extra_charges,
            'all_charges': all_charges,
            'horse_groups': horse_groups,
            'subtotal': subtotal,
            'total': subtotal,  # No tax for now
        }

    @classmethod
    @transaction.atomic
    def create_invoice(cls, owner, period_start, period_end, notes='', run=None, horse=None):
        """Create an invoice for an owner.

        If ``run`` and ``horse`` are both provided, the invoice is a per-horse
        sub-invoice with a compound number like "0090/0001". Otherwise it's a
        concatenated owner-level invoice using the standard serial number.
        """
        existing = cls.check_for_overlapping_invoices(
            owner, period_start, period_end, horse=horse
        )
        if existing:
            scope = f" for {horse.name}" if horse is not None else ""
            raise DuplicateInvoiceError(
                f"{owner.name}{scope} already has invoice {existing.invoice_number} "
                f"covering {existing.period_start} to {existing.period_end} "
                f"which overlaps with this period."
            )

        settings = BusinessSettings.get_settings()

        if run is not None and horse is not None:
            sub_no = run.allocate_sub_number()
            invoice_number = f"{run.display_number}/{sub_no:04d}"
        else:
            invoice_number = settings.get_next_invoice_number()

        # Create the invoice
        invoice = Invoice.objects.create(
            owner=owner,
            run=run,
            horse=horse,
            invoice_number=invoice_number,
            period_start=period_start,
            period_end=period_end,
            payment_terms_days=settings.default_payment_terms,
            due_date=period_end + timedelta(days=settings.default_payment_terms),
            notes=notes,
        )

        # Add livery line items
        livery_charges = cls.calculate_livery_charges(
            owner, period_start, period_end, horse=horse
        )
        for charge in livery_charges:
            InvoiceLineItem.objects.create(
                invoice=invoice,
                horse=charge['horse'],
                placement=charge['placement'],
                line_type=InvoiceLineItem.LineType.LIVERY,
                description=charge['description'],
                quantity=Decimal(str(charge['days'])),
                unit_price=charge['daily_rate'],
                line_total=charge['amount'],
                share_percentage=charge['share_percentage'],
            )

        # Add extra charge line items
        extra_charges = cls.get_unbilled_charges(owner, period_end, horse=horse)
        for charge in extra_charges:
            line_type_map = {
                'vet': InvoiceLineItem.LineType.VET,
                'farrier': InvoiceLineItem.LineType.FARRIER,
                'vaccination': InvoiceLineItem.LineType.VACCINATION,
                'feed': InvoiceLineItem.LineType.FEED,
                'medication': InvoiceLineItem.LineType.OTHER,
                'transport': InvoiceLineItem.LineType.OTHER,
                'equipment': InvoiceLineItem.LineType.OTHER,
                'dentist': InvoiceLineItem.LineType.OTHER,
                'physio': InvoiceLineItem.LineType.OTHER,
            }
            line_type = line_type_map.get(
                charge['line_type'],
                InvoiceLineItem.LineType.OTHER
            )

            InvoiceLineItem.objects.create(
                invoice=invoice,
                horse=charge['horse'],
                charge=charge['charge'],
                line_type=line_type,
                description=charge['description'],
                quantity=Decimal('1'),
                unit_price=charge['amount'],
                line_total=charge['amount'],
                share_percentage=charge['share_percentage'],
            )

            # Mark split charges as invoiced only when all co-owners have been billed
            extra_charge = charge['charge']
            if extra_charge.split_by_ownership:
                cls._maybe_mark_split_charge_invoiced(extra_charge, invoice, owner)
            else:
                extra_charge.mark_as_invoiced(invoice)

        # Recalculate totals
        invoice.recalculate_totals()

        return invoice

    @staticmethod
    def _maybe_mark_split_charge_invoiced(extra_charge, invoice, current_owner):
        """Mark a split charge as invoiced once all co-owners have been billed for it."""
        all_shares = OwnershipShare.objects.filter(horse=extra_charge.horse)
        all_owner_ids = set(s.owner_id for s in all_shares)

        # Find which owners already have invoice line items for this charge
        already_invoiced = set(
            InvoiceLineItem.objects.filter(
                charge=extra_charge
            ).values_list('invoice__owner_id', flat=True)
        )
        # Include the current owner (their line item was just created)
        already_invoiced.add(current_owner.id)

        if all_owner_ids.issubset(already_invoiced):
            extra_charge.mark_as_invoiced(invoice)

    @staticmethod
    def get_owners_for_billing(period_start, period_end):
        """Get all owners who should receive invoices for a period.

        Returns owners who have:
        - OwnershipShares on horses with placements overlapping the period, OR
        - Direct (non-split) unbilled extra charges
        """
        # Horses with overlapping placements
        horses_with_placements = Horse.objects.filter(
            placements__start_date__lte=period_end,
        ).exclude(
            placements__end_date__lt=period_start
        ).distinct()

        owners_via_shares = Owner.objects.filter(
            ownership_shares__horse__in=horses_with_placements
        ).distinct()

        # Owners with direct (non-split) unbilled charges
        owners_via_charges = Owner.objects.filter(
            extra_charges__invoiced=False,
            extra_charges__date__lte=period_end,
            extra_charges__split_by_ownership=False,
        ).distinct()

        return (owners_via_shares | owners_via_charges).distinct()

    @staticmethod
    def get_horses_to_bill_for_owner(owner, period_start, period_end):
        """Return horses (alphabetical) for which this owner has billable
        activity (livery or extra charges) in the given period.
        """
        # Horses owned via shares with an overlapping placement
        shared_horses = Horse.objects.filter(
            ownership_shares__owner=owner,
            placements__start_date__lte=period_end,
        ).exclude(
            placements__end_date__lt=period_start,
        )

        # Horses with direct (non-split) unbilled charges owed by this owner
        direct_charge_horses = Horse.objects.filter(
            extra_charges__owner=owner,
            extra_charges__invoiced=False,
            extra_charges__date__lte=period_end,
            extra_charges__split_by_ownership=False,
        )

        # Horses with split extra charges where this owner has a share
        split_charge_horses = Horse.objects.filter(
            ownership_shares__owner=owner,
            extra_charges__invoiced=False,
            extra_charges__date__lte=period_end,
            extra_charges__split_by_ownership=True,
        )

        combined = (
            shared_horses | direct_charge_horses | split_charge_horses
        ).distinct().order_by('name')
        return combined

    @classmethod
    def generate_monthly_invoices(cls, year, month, concatenate=True, created_by=None):
        """Generate invoices for all owners for a given month.

        Creates an ``InvoiceRun`` for the batch. When ``concatenate`` is True
        (default, the historical behaviour), each owner gets a single invoice
        with all their horses as line items. When False, each owner gets one
        invoice per horse with compound ``{run}/{sub}`` numbering.

        Returns (run, invoices, skipped_owners).
        """
        from calendar import monthrange

        # Calculate period
        first_day = date(year, month, 1)
        last_day = date(year, month, monthrange(year, month)[1])

        settings = BusinessSettings.get_settings()
        run = InvoiceRun.objects.create(
            run_number=settings.get_next_run_number(),
            period_start=first_day,
            period_end=last_day,
            concatenate_invoices=concatenate,
            created_by=created_by,
        )

        owners = cls.get_owners_for_billing(first_day, last_day)

        invoices = []
        skipped = []

        for owner in owners:
            if concatenate:
                invoice = cls._try_create_concatenated(owner, run, first_day, last_day, skipped)
                if invoice is not None:
                    invoices.append(invoice)
            else:
                sub_invoices = cls._try_create_per_horse(owner, run, first_day, last_day, skipped)
                invoices.extend(sub_invoices)

        return run, invoices, skipped

    @classmethod
    def _try_create_concatenated(cls, owner, run, period_start, period_end, skipped):
        """ON path: one invoice per owner."""
        existing = cls.check_for_overlapping_invoices(owner, period_start, period_end)
        if existing:
            skipped.append(owner)
            return None

        preview = cls.calculate_invoice_preview(owner, period_start, period_end)
        if preview['total'] <= 0:
            return None

        return cls.create_invoice(owner, period_start, period_end, run=run)

    @classmethod
    def _try_create_per_horse(cls, owner, run, period_start, period_end, skipped):
        """OFF path: one invoice per horse."""
        horses = cls.get_horses_to_bill_for_owner(owner, period_start, period_end)
        created = []
        skipped_any = False

        for horse in horses:
            existing = cls.check_for_overlapping_invoices(
                owner, period_start, period_end, horse=horse
            )
            if existing:
                skipped_any = True
                continue

            preview = cls.calculate_invoice_preview(
                owner, period_start, period_end, horse=horse
            )
            if preview['total'] <= 0:
                continue

            invoice = cls.create_invoice(
                owner, period_start, period_end, run=run, horse=horse
            )
            created.append(invoice)

        if skipped_any and not created:
            # Record the owner as skipped when none of their horses produced
            # a new invoice (consistent with ON-mode behaviour).
            skipped.append(owner)

        return created
