"""The practice's bills, and what goes back to the yard (docs/C_VET_PLAN.md, section 7e).

When the vet taps Finish visit, ``close_out`` does two things:

1. **Writes the yard's records**, as the yard's staff would by hand: a
   vaccination record for each vaccination, a vet visit record with the
   other items and the recheck date. When the yard is billed, each
   record carries its cost, and C-gate's own code makes the charge that
   the yard's next invoices bill to each owner. The yard manager can
   change that charge in C-gate, to add the yard's margin.
2. **Makes the bill**, if the practice bills after each visit: one bill
   to the client, or, for a C-gate yard set to "bill each owner", one
   bill for each owner.

A visit for a client that is not the C-gate yard does step 2 only:
nothing of it is written to the yard's records (``Visit.at_cgate_yard``).

A practice that bills once a month gets its bill from ``bill_month_end``
(a scheduled task, on the 1st) or from ``bill_now`` (the button on the
Bills page). Either way a bill is a copy and never changes: work that
arrives after it goes on a further bill.

Each new bill to the yard is also recorded as a cost in the yard's books
and sent to the yard by email.

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
YARD = PracticeYardLink.BillTo.YARD
OWNERS = PracticeYardLink.BillTo.OWNERS


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


def yard_amount(gross, rate, yard_charges_vat):
    """What an item of the vet's bill costs the yard: the owner's charge starts here.

    A yard that is not VAT-registered passes on what it pays: the whole
    amount. A VAT-registered yard takes the VAT back from HMRC and its
    own invoices add VAT again, so it passes on the amount without VAT
    (decision 23).

    No margin is added here. What the yard adds when it invoices an owner
    is the yard manager's decision, made in C-gate on the charge
    (decision 26). C-vet passes the vet's amount on as it is.
    """
    gross = Decimal(gross)
    return gross - vat_in(gross, rate) if yard_charges_vat else gross


# ── The bill ─────────────────────────────────────────────────────────────

def _next_number(practice, is_test):
    """The next bill number of the practice. Call with the practice locked."""
    field = 'next_test_bill_number' if is_test else 'next_bill_number'
    number = getattr(practice, field)
    setattr(practice, field, number + 1)
    practice.save(update_fields=[field])
    prefix = practice.bill_prefix or 'CV'
    return f'{"TEST-" if is_test else ""}{prefix}-{number:04d}'


def unbilled_lines(practice, *, visit=None, before=None):
    """The items of the practice's finished visits that are on no bill yet.

    ``visit``: of that visit only. ``before``: of visits before that day.
    """
    lines = NoteLine.objects.filter(
        practice=practice, bill_lines__isnull=True, note__visit__status=Visit.Status.FINISHED,
    )
    if visit is not None:
        lines = lines.filter(note__visit=visit)
    if before is not None:
        lines = lines.filter(note__visit__date__lt=before)
    return lines.select_related('note__visit__practice_client').order_by('note__visit__date', 'note__horse_name', 'note_id', 'pk')


def _parts_by_addressee(lines):
    """Who each line is billed to.

    ``{(is test, bill to, owner id or None, name, client pk or None): [(line, share %, amount)]}``.
    One entry is one bill: each client has its own.
    """
    by_addressee = defaultdict(list)
    for line in lines:
        visit = line.note.visit
        payers = line.note.payers if visit.bill_to == OWNERS else []
        parts = split_amount(line.total_inc_vat, payers)
        if not parts:
            # The yard is billed; or owners are, and this horse has none
            # on record. The yard asked for the visit, so the yard gets it.
            key = (visit.is_test, YARD, None, visit.yard_name or 'The yard', visit.practice_client_id)
            by_addressee[key].append((line, Decimal('100.00'), line.total_inc_vat))
            continue
        share_sum = sum((Decimal(str(payer['share'])) for payer, _ in parts), Decimal('0'))
        for payer, amount in parts:
            share = (Decimal(str(payer['share'])) * 100 / share_sum).quantize(PENNY, ROUND_HALF_UP)
            key = (visit.is_test, OWNERS, payer.get('owner_id'), payer['name'], visit.practice_client_id)
            by_addressee[key].append((line, share, amount))
    return by_addressee


def make_bills(practice, *, visit=None, before=None):
    """Bill the practice's unbilled items. Returns the new bills.

    With ``visit``: that visit's items, on a bill for the visit. Without:
    the items of every finished visit (before ``before``, if given), on a
    bill for the period they cover.

    Safe to call again: the second call finds nothing unbilled and makes
    nothing.
    """
    from core.models import Owner

    with transaction.atomic():
        # One at a time for a practice: its bill numbers have no gaps.
        practice = Practice.objects.select_for_update().get(pk=practice.pk)
        lines = list(unbilled_lines(practice, visit=visit, before=before))
        if not lines:
            return []
        today = timezone.localdate()
        bills = []
        for (is_test, bill_to, owner_id, name, client_pk), parts in _parts_by_addressee(lines).items():
            total = sum((amount for _, _, amount in parts), Decimal('0'))
            # VAT is worked out on the total at each rate, once, so the
            # bill, its printed page and its CSV all show the same figure.
            bands = defaultdict(lambda: Decimal('0'))
            for line, _, amount in parts:
                bands[line.vat_rate] += amount
            vat = sum((vat_in(gross, rate) for rate, gross in bands.items()), Decimal('0'))
            dates = [line.note.visit.date for line, _, _ in parts]
            bill = Bill.objects.create(
                practice=practice, number=_next_number(practice, is_test),
                visit=visit, date=visit.date if visit else today,
                period_start=None if visit else min(dates), period_end=None if visit else max(dates),
                bill_to=bill_to, to_name=name, practice_client_id=client_pk,
                to_owner=Owner.objects.filter(pk=owner_id).first() if owner_id else None,
                total_inc_vat=total, total_vat=vat, is_test=is_test,
            )
            BillLine.objects.bulk_create([
                BillLine(
                    bill=bill, note_line=line, date=line.note.visit.date,
                    horse_name=line.note.horse_name, description=line.description,
                    quantity=line.quantity, unit_price_inc_vat=line.unit_price_inc_vat,
                    vat_rate=line.vat_rate, share=share, amount_inc_vat=amount,
                )
                for line, share, amount in parts
            ])
            bills.append(bill)
        return bills


# ── The yard's records ───────────────────────────────────────────────────

def send_note_to_yard(note, link):
    """Write one note's dates and costs into the yard's records, once.

    Returns the problems the yard must know about, as a list of strings.
    """
    from core.models import BusinessSettings
    from health.models import Vaccination, VetVisit
    from health.views import sync_record_charge

    problems = []
    with transaction.atomic():
        note = (
            ClinicalNote.objects.select_for_update()
            .select_related('horse', 'visit', 'practice').get(pk=note.pk)
        )
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
            with_charges = note.visit.bill_to == YARD and link.sends_charges
            business = BusinessSettings.objects.only('vat_rate').first()
            yard_charges_vat = bool(business and business.vat_rate > 0)
            source = f'From C-vet: {note.practice.name}'

            def cost_of(line_set):
                if not with_charges:
                    return Decimal('0.00')
                return sum((
                    yard_amount(line.total_inc_vat, line.vat_rate, yard_charges_vat) for line in line_set
                ), Decimal('0.00'))

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
        # No history row: nothing of the clinical record changed.
        ClinicalNote.objects.filter(pk=note.pk).update(yard_sent_at=timezone.now(), yard_records=made)
    return problems


def _record_yard_cost(bill, link):
    """The bill as a cost in the yard's books: what the yard owes the practice."""
    from billing.models import YardCost

    if bill.yard_cost_id or bill.bill_to != YARD or not link.sends_charges:
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


def _tell_yard(bills, link, problems=()):
    """Each new real bill to the yard: a cost in its books, and an email."""
    for bill in bills:
        if bill.is_test or bill.bill_to != YARD:
            continue
        if bill.practice_client is not None and not bill.practice_client.is_yard:
            # A client of the practice that is not the C-gate yard:
            # nothing of its bill enters the yard's books or its inbox.
            continue
        _record_yard_cost(bill, link)
        # After the transaction, so a failed email undoes nothing.
        transaction.on_commit(lambda pk=bill.pk, found=tuple(problems): email_bill_to_yard(pk, found))


# ── When each thing happens ──────────────────────────────────────────────

def close_out(visit):
    """A finished visit: the yard's records, and the bill if it is due now.

    Called when the vet finishes the visit, and again for each note that
    arrives after that. Safe to call again. Returns the new bills.
    """
    if visit.status != Visit.Status.FINISHED:
        return []
    link = access.open_link(visit.practice)

    problems = []
    # Only a visit at the C-gate yard writes to the yard's records.
    if link is not None and not visit.is_test and visit.at_cgate_yard:
        for note in visit.notes.filter(yard_sent_at__isnull=True, is_test=False):
            problems += send_note_to_yard(note, link)
    # If the link to the yard has ended, nothing more goes into the
    # yard's records; the work is still billed.

    bill_now_ = link is None or link.bill_when == PracticeYardLink.BillWhen.VISIT
    bills = make_bills(visit.practice, visit=visit) if bill_now_ else []
    if link is not None:
        _tell_yard(bills, link, problems)
    return bills


def bill_now(practice, *, before=None):
    """Bill everything of the practice that is finished and not billed yet.

    The button on the Bills page, and the monthly run. Returns the new bills.
    """
    bills = make_bills(practice, before=before)
    link = access.open_link(practice)
    if link is not None:
        _tell_yard(bills, link)
    return bills


def bill_month_end(today=None):
    """The monthly run: bill each practice that bills once a month.

    Covers every finished visit before the first day of this month. Run
    on the 1st; safe on any later day, and safe to run twice.
    """
    first_of_month = (today or timezone.localdate()).replace(day=1)
    bills = []
    for link in PracticeYardLink.objects.filter(
        bill_when=PracticeYardLink.BillWhen.MONTH, practice__is_active=True,
    ).select_related('practice'):
        bills += bill_now(link.practice, before=first_of_month)
    return bills


def unbilled_summary(practice, is_test):
    """(number of visits, total) of finished work that is on no bill yet."""
    lines = [line for line in unbilled_lines(practice) if line.note.visit.is_test == is_test]
    visits = {line.note.visit_id for line in lines}
    return len(visits), sum((line.total_inc_vat for line in lines), Decimal('0.00'))


# ── Telling the yard ─────────────────────────────────────────────────────

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
        'to_owners': bill.bill_to == OWNERS,
        # A monthly bill: each line shows the day of its visit.
        'monthly': bill.visit_id is None,
    }
