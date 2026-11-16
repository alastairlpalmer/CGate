"""The practice's bills, and what goes back to the yard (docs/C_VET_PLAN.md, section 7e).

When the vet taps Finish visit, ``close_out`` does three things:

1. **Makes the bill.** One bill to the yard (the practice's client), or,
   for a yard set to "bill each owner", one bill for each owner. A bill
   is a copy and never changes. Work that arrives after it goes on a
   further bill for the same visit.
2. **Writes the yard's records**, as the yard's staff would by hand: a
   vaccination record for each vaccination, a vet visit record with the
   other items and the recheck date. When the yard is billed, each
   record carries its cost, and C-gate's own code makes the charge that
   the yard's next invoices bill to each owner.
3. **Tells the yard:** the bill as a cost in the yard's books, and by email.

This module is the one place where C-vet writes to the yard's records.
It writes nothing clinical: item names, amounts and dates only.

Nothing goes to the yard in test mode. A test bill is made, with a
number of its own, so that the path can be tried; no record, charge,
cost or email is.
"""

import logging
from collections import defaultdict
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from . import access
from .models import Bill, BillLine, ClinicalNote, NoteLine, Practice, PracticeYardLink, Visit

logger = logging.getLogger(__name__)

PENNY = Decimal('0.01')


# ── Money ────────────────────────────────────────────────────────────────

def vat_in(gross, rate):
    """The VAT inside an amount that includes it."""
    rate = Decimal(rate)
    return (Decimal(gross) * rate / (Decimal('100') + rate)).quantize(PENNY, ROUND_HALF_UP)


def split_amount(total, payers):
    """Split an amount between the owners on record, by their shares.

    ``[(payer, amount)]``. The owners on record pay all of it, in
    proportion: shares of 60% and 20% pay three quarters and one quarter
    (decision 18). The parts add up to the total exactly; the odd pence
    go to the largest remainders. The same rule as the phone's
    ``splitByShare`` (static/cvet/app_core.js).
    """
    total = Decimal(total)
    shares = [(payer, Decimal(str(payer.get('share', '0')) or '0')) for payer in payers or []]
    share_sum = sum((share for _, share in shares), Decimal('0'))
    if share_sum <= 0:
        return []
    parts = []
    for payer, share in shares:
        exact = total * share / share_sum
        floor = exact.quantize(PENNY, ROUND_FLOOR)
        parts.append([payer, floor, exact - floor])
    left = total - sum((amount for _, amount, _ in parts), Decimal('0'))
    for part in sorted(parts, key=lambda item: item[2], reverse=True):
        if left <= 0:
            break
        part[1] += PENNY
        left -= PENNY
    return [(payer, amount) for payer, amount, _ in parts]


# ── 1. The bill ──────────────────────────────────────────────────────────

def _next_number(practice, is_test):
    """The next bill number of the practice. Call with the practice locked."""
    field = 'next_test_bill_number' if is_test else 'next_bill_number'
    number = getattr(practice, field)
    setattr(practice, field, number + 1)
    practice.save(update_fields=[field])
    prefix = practice.bill_prefix or 'CV'
    return f'{"TEST-" if is_test else ""}{prefix}-{number:04d}'


def _unbilled_lines(visit):
    return list(
        NoteLine.objects.filter(note__visit=visit, bill_lines__isnull=True)
        .select_related('note').order_by('note__horse_name', 'note_id', 'pk')
    )


def _parts_by_addressee(visit, lines):
    """{(owner id or None, name): [(line, share %, amount)]}: who each line is billed to."""
    by_addressee = defaultdict(list)
    yard_key = (None, visit.yard_name or 'The yard')
    for line in lines:
        payers = line.note.payers if visit.bill_to == PracticeYardLink.BillTo.OWNERS else []
        parts = split_amount(line.total_inc_vat, payers)
        if not parts:
            # The yard is billed; or owners are, and this horse has none
            # on record. The yard asked for the visit, so the yard gets it.
            by_addressee[yard_key].append((line, Decimal('100.00'), line.total_inc_vat))
            continue
        share_sum = sum((Decimal(str(payer['share'])) for payer, _ in parts), Decimal('0'))
        for payer, amount in parts:
            share = (Decimal(str(payer['share'])) * 100 / share_sum).quantize(PENNY, ROUND_HALF_UP)
            by_addressee[(payer.get('owner_id'), payer['name'])].append((line, share, amount))
    return by_addressee


def bill_visit(visit):
    """Make the bill, or bills, for the visit's items that are on no bill yet.

    Safe to call again: the second call finds nothing unbilled and makes
    nothing. Returns the new bills.
    """
    from core.models import Owner

    with transaction.atomic():
        # One at a time for a practice: its bill numbers have no gaps.
        practice = Practice.objects.select_for_update().get(pk=visit.practice_id)
        lines = _unbilled_lines(visit)
        if not lines:
            return []
        bills = []
        for (owner_id, name), parts in _parts_by_addressee(visit, lines).items():
            total = sum((amount for _, _, amount in parts), Decimal('0'))
            # VAT is worked out on the total at each rate, once, so the
            # bill, its printed page and its CSV all show the same figure.
            bands = defaultdict(lambda: Decimal('0'))
            for line, _, amount in parts:
                bands[line.vat_rate] += amount
            vat = sum((vat_in(gross, rate) for rate, gross in bands.items()), Decimal('0'))
            bill = Bill.objects.create(
                practice=practice, visit=visit, number=_next_number(practice, visit.is_test),
                bill_to=PracticeYardLink.BillTo.OWNERS if owner_id else PracticeYardLink.BillTo.YARD,
                to_name=name,
                to_owner=Owner.objects.filter(pk=owner_id).first() if owner_id else None,
                date=visit.date, total_inc_vat=total, total_vat=vat, is_test=visit.is_test,
            )
            BillLine.objects.bulk_create([
                BillLine(
                    bill=bill, note_line=line, horse_name=line.note.horse_name,
                    description=line.description, quantity=line.quantity,
                    unit_price_inc_vat=line.unit_price_inc_vat, vat_rate=line.vat_rate,
                    share=share, amount_inc_vat=amount,
                )
                for line, share, amount in parts
            ])
            bills.append(bill)
        return bills


# ── 2. The yard's records ────────────────────────────────────────────────

def _yard_amount(gross, rate, yard_charges_vat):
    """What the yard puts on an owner's invoice for an item of the vet's bill.

    A yard that is not VAT-registered passes on what it pays: the whole
    amount. A VAT-registered yard takes the VAT back from HMRC and its
    own invoices add VAT again, so it passes on the amount without VAT.
    """
    gross = Decimal(gross)
    return gross - vat_in(gross, rate) if yard_charges_vat else gross


def send_note_to_yard(note, link, bill_numbers=''):
    """Write one note's dates and costs into the yard's records, once.

    Returns the problems the yard must know about, as a list of strings.
    """
    from core.models import BusinessSettings
    from health.models import Vaccination, VetVisit
    from health.views import sync_record_charge

    problems = []
    with transaction.atomic():
        note = ClinicalNote.objects.select_for_update().select_related('horse', 'visit').get(pk=note.pk)
        if note.yard_sent_at is not None or note.is_test:
            return problems
        lines = list(note.lines.select_related('price_item__vaccination_type'))
        total = sum((line.total_inc_vat for line in lines), Decimal('0'))
        made = {}
        if note.horse is None:
            if total:
                problems.append(
                    f'{note.horse_name}: £{total:.2f} is on the bill, but the horse is not in '
                    f'C-gate now. Nothing was added to an owner\'s charges.'
                )
        else:
            with_charges = note.visit.bill_to == PracticeYardLink.BillTo.YARD and link.sends_charges
            business = BusinessSettings.objects.only('vat_rate').first()
            yard_charges_vat = bool(business and business.vat_rate > 0)
            source = f'From C-vet: {note.practice.name}' + (f', bill {bill_numbers}' if bill_numbers else '')

            def cost_of(line_set):
                if not with_charges:
                    return Decimal('0.00')
                return sum(
                    (_yard_amount(line.total_inc_vat, line.vat_rate, yard_charges_vat) for line in line_set),
                    Decimal('0.00'),
                )

            records = []
            other = []
            for line in lines:
                vaccination_type = line.price_item.vaccination_type if line.price_item else None
                if vaccination_type is not None and link.sends_vaccination_dates:
                    records.append(Vaccination.objects.create(
                        horse=note.horse, vaccination_type=vaccination_type, date_given=note.seen_on,
                        vet=link.service_provider, cost=cost_of([line]), notes=source,
                    ))
                else:
                    other.append(line)
            made['vaccinations'] = [record.pk for record in records]

            follow_up = note.follow_up_date if link.sends_follow_up_dates else None
            if (other and with_charges) or follow_up:
                # The reason is the item names: it is the text of the
                # yard's charge and of the line on the owner's invoice.
                # Never the note's own text.
                reason = '; '.join(
                    (f'{line.quantity.normalize():f} x ' if line.quantity != 1 else '') + line.description
                    for line in other
                ) if with_charges else ''
                visit_record = VetVisit.objects.create(
                    horse=note.horse, date=note.seen_on, vet=link.service_provider,
                    reason=(reason or 'Vet visit')[:500], follow_up_date=follow_up,
                    cost=cost_of(other), notes=source,
                )
                records.append(visit_record)
                made['vet_visit'] = visit_record.pk

            for record in records:
                if sync_record_charge(record) == 'no_owner':
                    problems.append(
                        f'{note.horse.name}: £{record.cost:.2f} was recorded, but the horse has no '
                        f'owner in C-gate, so no charge was made. Give the horse an owner and save '
                        f'the record again.'
                    )
        note.yard_sent_at = timezone.now()
        note.yard_records = made
        # No history row: nothing of the clinical record changed.
        ClinicalNote.objects.filter(pk=note.pk).update(yard_sent_at=note.yard_sent_at, yard_records=made)
    return problems


def _record_yard_cost(bill, link):
    """The bill as a cost in the yard's books: what the yard owes the practice."""
    from billing.models import YardCost

    if bill.yard_cost_id or bill.bill_to != PracticeYardLink.BillTo.YARD or not link.sends_charges:
        return
    horses = bill.lines.values('horse_name').distinct().count()
    cost = YardCost.objects.create(
        category=YardCost.CostCategory.PROFESSIONAL, date=bill.date, supplier=bill.practice.name,
        description=f'Vet bill {bill.number}: {horses} horse{"s" if horses != 1 else ""}'[:500],
        amount=bill.total_ex_vat, vat_amount=bill.total_vat,
        notes="From C-vet. Each horse's part is in Charges and in its health records.",
    )
    bill.yard_cost = cost
    bill.save(update_fields=['yard_cost'])


# ── All of it, when a visit is finished ──────────────────────────────────

def close_out(visit):
    """Bill a finished visit and send the yard what it needs. Safe to call again.

    Called when the vet finishes the visit, and again for each note that
    arrives after that. Returns the new bills.
    """
    if visit.status != Visit.Status.FINISHED:
        return []
    bills = bill_visit(visit)
    if visit.is_test:
        return bills

    link = access.open_link(visit.practice)
    if link is None:
        # The link to the yard ended. The bill stands; nothing more goes
        # into the yard's records.
        return bills

    numbers = ', '.join(bill.number for bill in visit.bills.order_by('pk'))
    problems = []
    for note in visit.notes.filter(yard_sent_at__isnull=True, is_test=False):
        problems += send_note_to_yard(note, link, numbers)
    for bill in bills:
        _record_yard_cost(bill, link)
        if bill.bill_to == PracticeYardLink.BillTo.YARD:
            # After the transaction, so a failed email undoes nothing.
            transaction.on_commit(lambda pk=bill.pk, found=tuple(problems): email_bill_to_yard(pk, found))
    return bills


def email_bill_to_yard(bill_pk, problems=()):
    """Send the yard its bill. True if it went."""
    from django.template.loader import render_to_string

    from core.models import BusinessSettings
    from notifications.mail import message

    bill = Bill.objects.select_related('practice', 'visit').get(pk=bill_pk)
    business = BusinessSettings.objects.first()
    if not business or not business.email:
        return False
    subject = f'Vet bill {bill.number} from {bill.practice.name}: £{bill.total_inc_vat:,.2f}'
    html = render_to_string('cvet/email/bill_to_yard.html', dict(bill_context(bill), problems=problems))
    try:
        message(subject, html, [business.email]).send()
    except Exception:
        logger.exception('Failed to send bill %s to the yard', bill.number)
        return False
    Bill.objects.filter(pk=bill.pk).update(emailed_at=timezone.now())
    return True


def bill_context(bill):
    """What a bill shows: on the printed page, in the email, in the CSV."""
    by_horse = defaultdict(list)
    bands = defaultdict(lambda: Decimal('0'))
    for line in bill.lines.all():
        by_horse[line.horse_name].append(line)
        bands[line.vat_rate] += line.amount_inc_vat
    return {
        'bill': bill,
        'practice': bill.practice,
        'horses': [
            {'name': name, 'lines': lines, 'total': sum((line.amount_inc_vat for line in lines), Decimal('0'))}
            for name, lines in sorted(by_horse.items())
        ],
        'vat_bands': [
            {'rate': rate, 'gross': gross, 'vat': vat_in(gross, rate), 'net': gross - vat_in(gross, rate)}
            for rate, gross in sorted(bands.items())
        ],
        'test_mode': bill.is_test,
        'to_owners': bill.bill_to == PracticeYardLink.BillTo.OWNERS,
    }
