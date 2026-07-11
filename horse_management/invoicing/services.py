"""
Invoice calculation and generation services.

Supports fractional ownership: charges are split by OwnershipShare percentages.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import (
    BusinessSettings,
    Horse,
    Owner,
    OwnershipShare,
    Placement,
)
from invoicing.models import Invoice, InvoiceLineItem
from .utils import format_date_short, format_date_short_year, group_preview_charges_by_horse


class DuplicateInvoiceError(Exception):
    """Raised when an invoice would overlap with an existing one."""
    pass


class InvoiceService:
    """Service for generating and managing invoices."""

    @staticmethod
    def check_for_overlapping_invoices(owner, period_start, period_end):
        """Check if an invoice already exists for this owner overlapping the given period.

        Returns the overlapping invoice if found, None otherwise.
        """
        return Invoice.objects.filter(
            owner=owner,
            period_start__lte=period_end,
            period_end__gte=period_start,
        ).exclude(
            status=Invoice.Status.CANCELLED,
        ).first()

    @staticmethod
    def _build_livery_charge(placement, period_start, period_end,
                             *, share_percentage, amount=None):
        """Build a single livery charge dict for a placement, or None.

        ``amount`` is this owner's already-reconciled share of the charge; if
        omitted the owner is billed the full charge (single-owner case).
        Returns None when the placement has no billable days in the period.
        """
        days = placement.get_days_in_period(period_start, period_end)
        if days <= 0:
            return None

        full_amount = placement.calculate_charge(period_start, period_end)
        owner_amount = full_amount if amount is None else amount
        eff_start, eff_end = placement.get_effective_dates_in_period(
            period_start, period_end
        )

        rate_str = f"£{placement.daily_rate:g}"
        date_from = format_date_short(eff_start)
        date_to = format_date_short_year(eff_end)

        share_note = ""
        if share_percentage < Decimal('100'):
            share_note = f" ({share_percentage:g}% share)"

        description = (
            f"{placement.rate_type.name} {rate_str} per day "
            f"- {days} days ({date_from} to {date_to}){share_note}"
        )
        return {
            'horse': placement.horse,
            'placement': placement,
            'description': description,
            'days': days,
            'daily_rate': placement.daily_rate,
            'full_amount': full_amount,
            'amount': owner_amount,
            'share_percentage': share_percentage,
            'line_type': 'livery',
        }

    @staticmethod
    def _co_owned_horse_ids():
        """IDs of horses with genuine fractional co-ownership (2+ shareholders)."""
        return set(
            OwnershipShare.objects
            .values('horse')
            .annotate(n=models.Count('id'))
            .filter(n__gte=2)
            .values_list('horse', flat=True)
        )

    @staticmethod
    def _effective_share_percentage(share, all_shares):
        """Owner's billable percentage for a co-owned horse.

        Normally the share's own percentage. If the horse's shares total less
        than 100% (a data gap — see QA #4), the unallocated remainder is billed
        to the primary contact (largest share if none is flagged) so the full
        charge is always invoiced rather than silently lost.
        """
        pct = share.share_percentage
        total = sum(s.share_percentage for s in all_shares)
        if total < Decimal('100'):
            primary = next(
                (s for s in all_shares if s.is_primary_contact), None
            ) or max(all_shares, key=lambda s: s.share_percentage)
            if share.pk == primary.pk:
                pct += Decimal('100') - total
        return pct

    @staticmethod
    def _reconciled_amount(full_amount, owner_id, all_shares):
        """This owner's monetary share of ``full_amount`` for a co-owned item.

        Non-primary owners get their share rounded to the penny; the primary
        contact gets the residual (full charge minus everyone else's rounded
        amounts). This absorbs both the penny rounding remainder (QA #5) and any
        sub-100% shortfall (QA #4) so the splits always sum exactly to the full
        charge — nothing is lost or double-counted.
        """
        primary = next(
            (s for s in all_shares if s.is_primary_contact), None
        ) or max(all_shares, key=lambda s: s.share_percentage)

        def rounded(s):
            return (full_amount * (s.share_percentage / Decimal('100'))).quantize(
                Decimal('0.01')
            )

        if owner_id == primary.owner_id:
            others = sum(
                (rounded(s) for s in all_shares if s.owner_id != primary.owner_id),
                Decimal('0.00'),
            )
            return full_amount - others
        share = next(s for s in all_shares if s.owner_id == owner_id)
        return rounded(share)

    @classmethod
    def calculate_livery_charges(cls, owner, period_start, period_end):
        """Calculate livery charges for an owner, per placement.

        Two ownership models coexist:

        * **Co-owned** horses (2+ OwnershipShare rows) are split by share
          percentage. Any shortfall below 100% is billed to the primary
          contact so nothing is lost (QA #4).
        * **Single-owner** horses (0 or 1 share) are billed 100% to each
          placement's own ``owner``. This bills placements created outside the
          "new arrival" flow that have no share (QA #2) and, crucially, follows
          ownership changes over time — when a horse is moved to a new owner,
          the pre-move placement is billed to the old owner and the post-move
          placement to the new owner (QA #3).
        """
        charges = []
        co_owned_ids = cls._co_owned_horse_ids()

        def overlapping(qs):
            return qs.filter(
                start_date__lte=period_end,
            ).exclude(
                end_date__lt=period_start
            ).select_related('horse', 'location', 'rate_type')

        # --- Co-owned horses this owner has a share in: split by share % ---
        owner_shares = OwnershipShare.objects.filter(
            owner=owner
        ).select_related('horse')
        for share in owner_shares:
            if share.horse_id not in co_owned_ids:
                continue  # single/partial share handled as single-owner below
            all_shares = list(share.horse.ownership_shares.all())
            pct = cls._effective_share_percentage(share, all_shares)
            for placement in overlapping(
                Placement.objects.filter(horse_id=share.horse_id)
            ):
                full = placement.calculate_charge(period_start, period_end)
                charge = cls._build_livery_charge(
                    placement, period_start, period_end,
                    share_percentage=pct,
                    amount=cls._reconciled_amount(full, owner.id, all_shares),
                )
                if charge:
                    charges.append(charge)

        # --- Single-owner horses: bill each placement's owner at 100% ---
        for placement in overlapping(
            Placement.objects.filter(owner=owner).exclude(horse_id__in=co_owned_ids)
        ):
            charge = cls._build_livery_charge(
                placement, period_start, period_end,
                share_percentage=Decimal('100.00'),
            )
            if charge:
                charges.append(charge)

        return charges

    @classmethod
    def get_unbilled_charges(cls, owner, period_end):
        """Get extra charges for this owner, handling ownership splits.

        Two cases:
        - split_by_ownership=False: charge goes 100% to the specified owner
        - split_by_ownership=True: charge is split among co-owners by share %,
          with the rounding remainder / sub-100% shortfall billed to the
          primary contact so the splits sum exactly to the charge (QA #4/#5)

        A split charge on a horse with NO OwnershipShare rows has no shares to
        split across, so it falls back to Case 1 and bills 100% to the
        charge's owner — otherwise it would never be billed at all.
        """
        charges = []

        # Case 1: Direct charges (no split) — bill to specified owner.
        # Includes split-flagged charges on horses without any ownership
        # shares, which would otherwise match neither case.
        shareless_split_ids = list(
            ExtraCharge.objects.filter(
                owner=owner,
                invoiced=False,
                date__lte=period_end,
                split_by_ownership=True,
            )
            .exclude(horse__ownership_shares__isnull=False)
            .values_list('id', flat=True)
        )
        direct_charges = ExtraCharge.objects.filter(
            models.Q(
                owner=owner,
                invoiced=False,
                date__lte=period_end,
                split_by_ownership=False,
            )
            | models.Q(id__in=shareless_split_ids)
        ).select_related('horse', 'service_provider')

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
        horse_share_map = {s.horse_id: s for s in owner_shares}

        if horse_share_map:
            # A split charge stays invoiced=False until every co-owner is
            # billed, so exclude charges this owner already has a line item
            # for on a live (non-cancelled) invoice — otherwise the same
            # share would be billed to them again.
            # charge__isnull excludes livery lines: their NULL charge_id would
            # poison the NOT IN subquery and exclude every split charge.
            already_billed_here = InvoiceLineItem.objects.filter(
                invoice__owner=owner,
                charge__isnull=False,
            ).exclude(
                invoice__status=Invoice.Status.CANCELLED,
            ).values('charge_id')
            split_charges = ExtraCharge.objects.filter(
                horse_id__in=horse_share_map.keys(),
                invoiced=False,
                date__lte=period_end,
                split_by_ownership=True,
            ).exclude(
                id__in=already_billed_here,
            ).select_related('horse', 'service_provider')

            # All shares per involved horse, so the split can reconcile to 100%.
            all_shares_by_horse = {}
            for s in OwnershipShare.objects.filter(horse_id__in=horse_share_map.keys()):
                all_shares_by_horse.setdefault(s.horse_id, []).append(s)

            for charge in split_charges:
                share = horse_share_map[charge.horse_id]
                all_shares = all_shares_by_horse.get(charge.horse_id, [share])
                owner_amount = cls._reconciled_amount(charge.amount, owner.id, all_shares)
                pct = cls._effective_share_percentage(share, all_shares)

                share_note = ""
                if pct < Decimal('100'):
                    share_note = f" ({pct:g}% share)"

                charges.append({
                    'horse': charge.horse,
                    'charge': charge,
                    'description': f"{charge.get_charge_type_display()} - {charge.description}{share_note}",
                    'date': charge.date,
                    'days': 1,
                    'daily_rate': charge.amount,
                    'full_amount': charge.amount,
                    'amount': owner_amount,
                    'share_percentage': pct,
                    'line_type': charge.charge_type,
                })

        return charges

    @classmethod
    def calculate_invoice_preview(cls, owner, period_start, period_end):
        """Calculate a preview of invoice charges without creating anything."""
        livery_charges = cls.calculate_livery_charges(owner, period_start, period_end)
        extra_charges = cls.get_unbilled_charges(owner, period_end)

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
    def create_invoice(cls, owner, period_start, period_end, notes=''):
        """Create an invoice for an owner."""
        # Serialise concurrent invoice creation per owner: the overlap check
        # below is check-then-act, so without this lock two simultaneous
        # "generate" clicks can both pass it and double-bill the period.
        owner = Owner.objects.select_for_update().get(pk=owner.pk)
        existing = cls.check_for_overlapping_invoices(owner, period_start, period_end)
        if existing:
            raise DuplicateInvoiceError(
                f"{owner.name} already has invoice {existing.invoice_number} "
                f"covering {existing.period_start} to {existing.period_end} "
                f"which overlaps with this period."
            )

        settings = BusinessSettings.get_settings()

        # Create the invoice
        invoice = Invoice.objects.create(
            owner=owner,
            invoice_number=settings.get_next_invoice_number(),
            period_start=period_start,
            period_end=period_end,
            payment_terms_days=settings.default_payment_terms,
            due_date=period_end + timedelta(days=settings.default_payment_terms),
            notes=notes,
        )

        # Add livery line items
        livery_charges = cls.calculate_livery_charges(owner, period_start, period_end)
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
        extra_charges = cls.get_unbilled_charges(owner, period_end)
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
        # on live invoices — a cancelled invoice doesn't bill anyone.
        already_invoiced = set(
            InvoiceLineItem.objects.filter(
                charge=extra_charge
            ).exclude(
                invoice__status=Invoice.Status.CANCELLED
            ).values_list('invoice__owner_id', flat=True)
        )
        # Include the current owner (their line item was just created)
        already_invoiced.add(current_owner.id)

        if all_owner_ids.issubset(already_invoiced):
            extra_charge.mark_as_invoiced(invoice)

    @staticmethod
    def get_owners_for_billing(period_start, period_end):
        """Get all owners who should receive invoices for a period.

        A superset mirroring calculate_livery_charges; generate_monthly_invoices
        filters out anyone whose preview total is zero. Returns owners who have:
        - OwnershipShares on horses with placements overlapping the period
          (co-owners), OR
        - an overlapping placement they own directly (single-owner horses,
          including ownership changes via a move), OR
        - direct (non-split) unbilled extra charges.
        """
        overlapping_placements = Placement.objects.filter(
            start_date__lte=period_end,
        ).exclude(
            end_date__lt=period_start
        )

        horses_with_placements = Horse.objects.filter(
            pk__in=overlapping_placements.values('horse')
        )

        owners_via_shares = Owner.objects.filter(
            ownership_shares__horse__in=horses_with_placements
        ).distinct()

        # Owners named directly on an overlapping placement (covers single-owner
        # horses and both sides of an ownership change).
        owners_via_placements = Owner.objects.filter(
            placements__in=overlapping_placements
        ).distinct()

        # Owners with direct (non-split) unbilled charges, plus split charges
        # on share-less horses (billed 100% to the charge owner — see
        # get_unbilled_charges).
        owners_via_charges = Owner.objects.filter(
            models.Q(extra_charges__split_by_ownership=False)
            | models.Q(extra_charges__horse__ownership_shares__isnull=True),
            extra_charges__invoiced=False,
            extra_charges__date__lte=period_end,
        ).distinct()

        return (
            owners_via_shares
            | owners_via_placements
            | owners_via_charges
        ).distinct()

    @staticmethod
    def generate_monthly_invoices(year, month):
        """Generate invoices for all owners for a given month.

        Includes both direct placement owners and fractional owners.
        """
        from calendar import monthrange

        # Calculate period
        first_day = date(year, month, 1)
        last_day = date(year, month, monthrange(year, month)[1])

        # Get all owners who should be billed (via ownership shares)
        owners = InvoiceService.get_owners_for_billing(first_day, last_day)

        invoices = []
        skipped = []
        for owner in owners:
            existing = InvoiceService.check_for_overlapping_invoices(
                owner, first_day, last_day
            )
            if existing:
                skipped.append(owner)
                continue

            # Preview charges first to avoid consuming an invoice number for zero totals
            preview = InvoiceService.calculate_invoice_preview(owner, first_day, last_day)
            if preview['total'] <= 0:
                continue

            invoice = InvoiceService.create_invoice(owner, first_day, last_day)
            invoices.append(invoice)

        return invoices, skipped
