"""C-vet for the practice office (docs/C_VET_PLAN.md, section 7j).

Pages, with a signal, behind the same gate as the app (``cvet.access``):

* ``bill_paid`` marks a bill as paid, or as not paid.
* ``bill_email`` sends a bill to a client of the practice's own.
* ``statement`` is one client's bills and what is still to pay.
* ``reports`` is the work of a period: by client, by vet, by item.
* ``price_file`` takes the price list from a CSV file, and gives it as one.

The list of bills itself is ``cvet.views.bill_list``. Every read goes
through ``request.practice``: no address here carries a practice id.

Nothing here writes to the yard's records, and nothing here reads the
text of a clinical note. The reports count items and money only.
"""

import csv
import io
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.csv_export import csv_response

from . import access, billing
from .access import practice_page
from .models import PriceItem

logger = logging.getLogger(__name__)

PRICE_FILE_MAX_BYTES = 200 * 1024
PRICE_FILE_MAX_ROWS = 1000
PRICE_MAX = Decimal('99999.99')
VAT_RATE_MAX = Decimal('99.99')


def _bill_or_404(request, pk):
    bill = (
        access.bills_for(request.practice).select_related('practice', 'visit', 'practice_client')
        .filter(pk=pk).first()
    )
    if bill is None:
        raise Http404
    return bill


def _back(request, fallback='cvet:bill_list'):
    """Where a button sends the person back to: the page it was on, if it is one of ours."""
    target = request.POST.get('next', '')
    return redirect(target) if target.startswith('/c-vet/') and '//' not in target else redirect(fallback)


# ── Paid, and sent ───────────────────────────────────────────────────────

@practice_page
@require_POST
def bill_paid(request, pk):
    """Mark a bill as paid on a day, or as not paid again.

    A note for the practice's own list. It makes no entry anywhere else:
    the yard's books hold what the yard owes from its own records.
    """
    bill = _bill_or_404(request, pk)
    if request.POST.get('paid') == 'no':
        bill.paid_on = None
    else:
        bill.paid_on = parse_date(request.POST.get('paid_on', '')) or timezone.localdate()
    bill.save(update_fields=['paid_on'])
    return _back(request)


def can_email(bill):
    """True for a bill that goes to a client of the practice's own with an email address.

    The C-gate yard gets its bill by itself when it is made
    (``billing.email_bill_to_yard``). A bill to one owner of a horse at
    the yard has no address here: the practice does not hold it.
    """
    client = bill.practice_client
    return bool(client and not client.is_yard and client.email and not bill.is_test)


@practice_page
@require_POST
def bill_email(request, pk):
    """Send a bill to its client. Replies go to the practice's own address."""
    from notifications.mail import message

    bill = _bill_or_404(request, pk)
    if not can_email(bill):
        raise Http404
    client = bill.practice_client
    subject = f'Invoice {bill.number} from {bill.practice.name}: £{bill.total_inc_vat:,.2f}'
    html = render_to_string('cvet/email/bill_to_client.html', billing.bill_context(bill))
    mail = message(subject, html, [client.email])
    if bill.practice.email:
        mail.reply_to = [bill.practice.email]
    try:
        mail.send()
    except Exception:
        logger.exception('Failed to send bill %s to its client', bill.number)
        return _sent(request, 'no')
    bill.emailed_at = timezone.now()
    bill.emailed_to = client.email
    bill.save(update_fields=['emailed_at', 'emailed_to'])
    return _sent(request, 'yes')


def _sent(request, answer):
    response = redirect('cvet:bill_detail', pk=request.resolver_match.kwargs['pk'])
    response['Location'] += f'?sent={answer}'
    return response


# ── A client's statement ─────────────────────────────────────────────────

@practice_page
@require_GET
def statement(request, client_id):
    """One client: its bills, what was paid, and what is still to pay. To print."""
    client = access.clients_for(request.practice).filter(client_id=client_id).first()
    if client is None:
        raise Http404
    bills = list(
        access.bills_for(request.practice)
        .filter(practice_client=client, is_test=settings.CVET_TEST_MODE).order_by('date', 'pk')
    )
    total = sum((bill.total_inc_vat for bill in bills), Decimal('0.00'))
    paid = sum((bill.total_inc_vat for bill in bills if bill.paid_on), Decimal('0.00'))
    return render(request, 'cvet/statement.html', {
        'practice': request.practice, 'client': client, 'bills': bills, 'today': timezone.localdate(),
        'total': total, 'paid': paid, 'to_pay': total - paid, 'test_mode': settings.CVET_TEST_MODE,
    })


# ── Reports ──────────────────────────────────────────────────────────────

def _period(request):
    """(first day, last day) from the address. This month when it gives none."""
    today = timezone.localdate()
    start = parse_date(request.GET.get('from', '')) or today.replace(day=1)
    end = parse_date(request.GET.get('to', '')) or today
    return (start, end) if start <= end else (end, start)


def work_in(practice, start, end):
    """The work of a period, counted three ways. Items and money only.

    From the items on the notes of visits in the period: finished or
    not, billed or not. That is the work done, which is what a report
    of work is for; the Bills page says what was billed.
    """
    lines = (
        access.lines_for(practice)
        .filter(note__is_test=settings.CVET_TEST_MODE, note__visit__date__gte=start, note__visit__date__lte=end)
        .select_related('note__visit')
    )
    clients = defaultdict(lambda: {'visits': set(), 'horses': set(), 'total': Decimal('0.00')})
    vets = defaultdict(lambda: {'visits': set(), 'horses': set(), 'total': Decimal('0.00')})
    items = defaultdict(lambda: {'quantity': Decimal('0'), 'total': Decimal('0.00')})
    total = Decimal('0.00')
    for line in lines:
        note = line.note
        amount = line.total_inc_vat
        total += amount
        for table, key in ((clients, note.visit.yard_name or 'No client'), (vets, note.author_name or 'Not known')):
            row = table[key]
            row['visits'].add(note.visit_id)
            row['horses'].add((note.visit_id, note.horse_name))
            row['total'] += amount
        items[line.description]['quantity'] += line.quantity
        items[line.description]['total'] += amount

    def people(table):
        return sorted((
            {'name': name, 'visits': len(row['visits']), 'horses': len(row['horses']), 'total': row['total']}
            for name, row in table.items()
        ), key=lambda row: (-row['total'], row['name']))

    return {
        'clients': people(clients),
        'vets': people(vets),
        'items': sorted((
            {'name': name, 'quantity': row['quantity'], 'total': row['total']} for name, row in items.items()
        ), key=lambda row: (-row['total'], row['name'])),
        'total': total,
    }


@practice_page
@require_GET
def reports(request):
    start, end = _period(request)
    work = work_in(request.practice, start, end)
    which = request.GET.get('csv', '')
    if which in ('clients', 'vets'):
        return csv_response(
            f'c-vet-work-by-{which[:-1]}-{start}-to-{end}',
            ['Client' if which == 'clients' else 'Vet', 'Visits', 'Horses seen', 'Total inc VAT'],
            [[row['name'], row['visits'], row['horses'], row['total']] for row in work[which]],
        )
    if which == 'items':
        return csv_response(
            f'c-vet-work-by-item-{start}-to-{end}', ['Item', 'Quantity', 'Total inc VAT'],
            [[row['name'], row['quantity'], row['total']] for row in work['items']],
        )
    today = timezone.localdate()
    last_month_end = today.replace(day=1) - timedelta(days=1)
    return render(request, 'cvet/reports.html', dict(
        work, section='reports', practice=request.practice, start=start, end=end,
        test_mode=settings.CVET_TEST_MODE,
        # Ready-made periods, one tap each.
        periods=[
            ('This month', today.replace(day=1), today),
            ('Last month', last_month_end.replace(day=1), last_month_end),
            ('This year', date(today.year, 1, 1), today),
        ],
    ))


# ── The price list as a file ─────────────────────────────────────────────

def _amount(text, maximum):
    """A plain amount with at most 2 decimal places, or None. As ``views._decimal``."""
    from .views import _decimal

    return _decimal(str(text or '').replace('£', '').replace(',', ''), maximum)


def read_price_file(data):
    """(rows, problems) from the bytes of a CSV file.

    A row: name, category, price, vat. The first line names the columns;
    ``name`` and ``price`` must be there. Nothing is changed here.
    """
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return [], ['The file is not text. Save it as CSV (UTF-8) and try again.']
    reader = csv.DictReader(io.StringIO(text))
    columns = {(name or '').strip().lower(): name for name in (reader.fieldnames or [])}
    if 'name' not in columns or 'price' not in columns:
        return [], ['The first line must name the columns. "name" and "price" must be there; "category" and "vat" can be.']
    rows, problems, seen = [], [], set()
    for number, raw in enumerate(reader, 2):
        if number - 1 > PRICE_FILE_MAX_ROWS:
            problems.append(f'The file has more than {PRICE_FILE_MAX_ROWS} prices.')
            break
        def cell(column, raw=raw):
            return (raw.get(columns.get(column, '')) or '').strip()
        name = cell('name')
        if not name and not cell('price'):
            continue  # an empty line
        price = _amount(cell('price'), PRICE_MAX)
        vat = _amount(cell('vat') or '20', VAT_RATE_MAX)
        if not name or len(name) > 100:
            problems.append(f'Line {number}: the name is empty or longer than 100 characters.')
        elif name.lower() in seen:
            problems.append(f'Line {number}: "{name}" is in the file twice.')
        elif price is None:
            problems.append(f'Line {number}: the price of "{name}" is not a number such as 65 or 65.50.')
        elif vat is None:
            problems.append(f'Line {number}: the VAT rate of "{name}" is not a number such as 20.')
        elif len(cell('category')) > 50:
            problems.append(f'Line {number}: the category of "{name}" is longer than 50 characters.')
        else:
            seen.add(name.lower())
            rows.append({'name': name, 'category': cell('category'), 'price_inc_vat': price, 'vat_rate': vat})
    if not rows and not problems:
        problems.append('The file has no prices.')
    return rows, problems


@practice_page
@require_http_methods(['GET', 'POST'])
def price_file(request):
    """Take the price list from a CSV file, or give it as one.

    A price in the file with the name of a price on the list changes it.
    A new name adds a price. A price that is not in the file stays as it
    is. One wrong line and nothing is changed: the page lists each one.
    """
    practice = request.practice
    if request.method == 'GET' and request.GET.get('download'):
        return csv_response('c-vet-prices', ['name', 'category', 'price', 'vat'], [
            [item.name, item.category, item.price_inc_vat, item.vat_rate]
            for item in access.prices_for(practice).filter(is_active=True).order_by('category', 'name')
        ])
    context = {'section': 'prices', 'practice': practice, 'test_mode': settings.CVET_TEST_MODE}
    if request.method == 'POST':
        upload = request.FILES.get('file')
        if upload is None:
            context['problems'] = ['Choose a file first.']
        elif upload.size > PRICE_FILE_MAX_BYTES:
            context['problems'] = ['The file is larger than 200 KB.']
        else:
            rows, problems = read_price_file(upload.read())
            if problems:
                context['problems'] = problems
            else:
                existing = {item.name.lower(): item for item in access.prices_for(practice)}
                added = changed = 0
                for row in rows:
                    item = existing.get(row['name'].lower())
                    if item is None:
                        PriceItem.objects.create(practice=practice, **row)
                        added += 1
                    elif (item.category, item.price_inc_vat, item.vat_rate, item.is_active) != (
                        row['category'], row['price_inc_vat'], row['vat_rate'], True,
                    ):
                        item.category, item.price_inc_vat, item.vat_rate = (
                            row['category'], row['price_inc_vat'], row['vat_rate'],
                        )
                        item.is_active = True
                        item.save()
                        changed += 1
                context['done'] = {'added': added, 'changed': changed, 'same': len(rows) - added - changed}
    context['count'] = access.prices_for(practice).filter(is_active=True).count()
    return render(request, 'cvet/price_file.html', context)


# ── People ───────────────────────────────────────────────────────────────

@practice_page
@require_http_methods(['GET', 'POST'])
def people(request):
    """Who can read the practice's records, and who asks to.

    A login the yard put in the practice (or that opened C-vet with the
    Vet role) waits here until a member approves it. The yard gives the
    role; only the practice lets a login read its notes.
    """
    from django.contrib import messages

    practice = request.practice
    if request.method == 'POST':
        pk = request.POST.get('member', '')
        if request.POST.get('action') == 'approve':
            member = access.approve(practice, pk, request.user)
            if member is not None:
                messages.success(request, f'{_name(member.user)} can now read and write the practice\'s records.')
        elif request.POST.get('action') == 'decline':
            member = access.decline(practice, pk)
            if member is not None:
                messages.success(request, f'{_name(member.user)} is not let in.')
        return redirect('cvet:people')
    members = (
        practice.members.filter(is_active=True, approved_at__isnull=False)
        .select_related('user', 'approved_by').order_by('user__first_name', 'user__email')
    )
    return render(request, 'cvet/people.html', {
        'practice': practice,
        'section': 'people',
        'waiting': access.waiting_for(practice),
        'members': members,
    })


def _name(user):
    return user.get_full_name() or user.email or user.get_username()
