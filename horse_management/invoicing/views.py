"""
Views for invoicing app.
"""

import io
import logging
from datetime import timedelta

from django.contrib import messages

from core.downloads import attachment_header
from core.views._popup import is_popup_request, popup_saved_response
from core.permissions import (
    LEVEL_FULL,
    LEVEL_VIEW,
    FeatureAccessMixin,
    feature_required,
    has_feature_access,
)
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView, UpdateView

from core import audit
from core.views._undo import undo_tag
from core.models import Owner
from invoicing.models import LIVE_PAYMENTS, Invoice, InvoiceLineItem, Payment

from .forms import InvoiceCreateForm, InvoiceUpdateForm, MonthlyInvoiceForm, PaymentForm
from .pdf import generate_invoice_pdf, generate_owner_statement_pdf
from .services import (
    DuplicateInvoiceError,
    InvoiceService,
    NothingToInvoiceError,
    StatementService,
)
from .utils import UnsupportedVatRateError, group_line_items_by_horse, write_xero_csv
from core.templatetags.ui_extras import gbp

logger = logging.getLogger(__name__)


class InvoiceListView(FeatureAccessMixin, ListView):
    feature = 'invoices'
    access_level = LEVEL_VIEW
    model = Invoice
    template_name = 'invoicing/invoice_list.html'
    context_object_name = 'invoices'
    paginate_by = 25

    def get_queryset(self):
        from decimal import Decimal

        from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
        from django.db.models.functions import Coalesce

        queryset = Invoice.objects.select_related('owner', 'horse', 'xero_sync').annotate(
            balance=ExpressionWrapper(
                F('total') - Coalesce(
                    Sum('payments__amount', filter=LIVE_PAYMENTS), Value(Decimal('0.00'))
                ),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )

        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)

        owner = self.request.GET.get('owner')
        if owner and owner.isdigit():
            queryset = queryset.filter(owner_id=owner)

        search = self.request.GET.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(invoice_number__icontains=search)
                | Q(owner__name__icontains=search)
            )

        # Period filter — same semantics as invoice_export_csv, so the list
        # and the "Export CSV" of that list always contain the same invoices.
        queryset = _apply_period_filter(queryset, self.request)

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        from decimal import Decimal

        from django.db.models import Count, Sum

        context = super().get_context_data(**kwargs)
        context['owners'] = Owner.objects.only('pk', 'name')
        context['status_choices'] = Invoice.Status.choices

        # Totals over the WHOLE filtered set (not just this page): what was
        # invoiced, and what of it is still unpaid (part-payments deducted).
        filtered = self.object_list
        live = filtered.exclude(status=Invoice.Status.CANCELLED)
        summary = live.aggregate(count=Count('id'), invoiced=Sum('total'))
        open_invoices = live.exclude(status=Invoice.Status.PAID)
        outstanding = (
            (open_invoices.aggregate(t=Sum('total'))['t'] or Decimal('0.00'))
            - (
                Payment.objects.filter(invoice__in=open_invoices.values('pk'))
                .aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
            )
        )
        context['summary_count'] = summary['count'] or 0
        context['summary_invoiced'] = summary['invoiced'] or Decimal('0.00')
        context['summary_outstanding'] = outstanding
        return context


def _apply_period_filter(queryset, request):
    """Filter invoices to a billing-period date range from GET params.

    Shared by the list view and the CSV export so what you see is what you
    export. Invalid dates are ignored rather than erroring.
    """
    from datetime import datetime

    date_from = request.GET.get('date_from')
    if date_from:
        try:
            queryset = queryset.filter(
                period_start__gte=datetime.strptime(date_from, '%Y-%m-%d').date()
            )
        except ValueError:
            pass

    date_to = request.GET.get('date_to')
    if date_to:
        try:
            queryset = queryset.filter(
                period_end__lte=datetime.strptime(date_to, '%Y-%m-%d').date()
            )
        except ValueError:
            pass

    return queryset


class InvoiceDetailView(FeatureAccessMixin, DetailView):
    feature = 'invoices'
    access_level = LEVEL_VIEW
    model = Invoice
    template_name = 'invoicing/invoice_detail.html'
    context_object_name = 'invoice'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_invoice_parts(self.object))
        context['voided_payments'] = Payment.all_objects.filter(
            invoice=self.object, voided_at__isnull=False,
        ).select_related('voided_by')
        # Payments included, so a payment that was deleted still shows here.
        context['history_entries'] = audit.merge(
            audit.history_for(self.object),
            audit.history_of(Payment.history.filter(invoice_id=self.object.pk), 'Payment'),
        )
        return context


def _invoice_parts(invoice):
    """The lines, payments and balance of one invoice, for the invoice
    page and for the invoice list's pane."""
    line_items = invoice.line_items.select_related(
        'horse', 'placement', 'charge'
    ).order_by('line_type', 'description')
    return {
        'line_items': line_items,
        'horse_groups': group_line_items_by_horse(line_items),
        'payments': invoice.payments.all(),
        'amount_paid': invoice.amount_paid,
        'balance_due': invoice.balance_due,
    }


@feature_required('invoices', LEVEL_VIEW)
def invoice_pane(request, pk):
    """The invoice list's preview pane, and its sheet on a phone.

    Three views of one invoice: the invoice itself, the owner it bills
    (the same record the owner list's pane shows) and the PDF as the owner
    gets it. A direct visit (or no JavaScript) goes to the invoice's page
    instead, so every row on the list stays an ordinary link.
    """
    htmx = getattr(request, 'htmx', None)
    if not htmx or htmx.target != 'invoice-preview-body':
        return redirect('invoice_detail', pk=pk)

    invoice = get_object_or_404(
        Invoice.objects.select_related('owner', 'horse', 'xero_sync'), pk=pk,
    )
    context = {'invoice': invoice, 'list_url': _list_url(request)}
    context.update(_invoice_parts(invoice))
    # The Owner tab is the owner's record, so it follows access to Owners.
    # Without it the tab still says who the invoice bills: that is on the
    # invoice itself.
    context['can_see_owner'] = has_feature_access(request.user, 'owners', LEVEL_VIEW)
    if context['can_see_owner']:
        from core.views.owners import owner_preview_context

        owner_context = owner_preview_context(request.user, invoice.owner)
        owner_context.pop('owner')
        # The invoice's own keys win: `invoices` and `payments` here are
        # the owner's recent invoices, under names of their own.
        context['owner_invoices'] = owner_context.pop('invoices')
        context.update(owner_context)
    return render(request, 'invoicing/partials/pane.html', context)


def _list_url(request):
    """The list the pane was opened from, filters and page included, so an
    action taken in the pane comes back to the same rows.

    htmx sends the address bar's URL with every request. Only a URL on
    this site is trusted; anything else falls back to the plain list.
    """
    from urllib.parse import urlsplit

    current = request.headers.get('HX-Current-URL', '')
    if current and url_has_allowed_host_and_scheme(
        current, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        parts = urlsplit(current)
        if parts.path == reverse('invoice_list'):
            return parts.path + (f'?{parts.query}' if parts.query else '')
    return reverse('invoice_list')


class InvoiceUpdateView(FeatureAccessMixin, UpdateView):
    feature = 'invoices'
    model = Invoice
    form_class = InvoiceUpdateForm
    template_name = 'invoicing/invoice_form.html'

    def form_valid(self, form):
        was_cancelled = (
            form.initial.get('status') == Invoice.Status.CANCELLED
        )
        was_paid = form.initial.get('status') == Invoice.Status.PAID
        response = super().form_valid(form)
        # Choosing "Paid" on the edit form used to write the status only:
        # amount_paid stayed 0, the statement still showed the full balance,
        # aged debtors dropped it, and with no payment to delete there was
        # no way back. Route it through mark_as_paid like the button does.
        if self.object.status == Invoice.Status.PAID and not was_paid:
            self.object.mark_as_paid(reference='Set to Paid on invoice edit')
        # Cancelling must free the invoice's extra charges, or they stay
        # invoiced=True against a dead invoice and drop out of any
        # replacement invoice.
        if self.object.status == Invoice.Status.CANCELLED and not was_cancelled:
            released = self.object.release_extra_charges()
            if released:
                messages.info(
                    self.request,
                    f"{released} extra charge{'s' if released != 1 else ''} "
                    "released for re-billing on a future invoice."
                )
            # Cancelling locally does not touch Xero, and cancelled invoices
            # drop out of the nightly status sweep — warn so the accountant
            # voids the Xero copy before it gets approved and chased there.
            try:
                sync = self.object.xero_sync
            except ObjectDoesNotExist:
                sync = None
            if sync and sync.xero_invoice_id:
                messages.warning(
                    self.request,
                    "This invoice was pushed to Xero "
                    f"({sync.xero_invoice_number or sync.xero_invoice_id}). "
                    "Cancelling here does not void it in Xero — void it "
                    "there too, or it may still be approved and chased.",
                )
        return response

    def get_success_url(self):
        return reverse_lazy('invoice_detail', kwargs={'pk': self.object.pk})


@feature_required('invoices')
def invoice_create(request):
    """Create a new invoice."""
    initial = {}

    # Pre-fill owner if provided
    owner_id = request.GET.get('owner')
    if owner_id:
        initial['owner'] = owner_id

    # Default to last month
    today = timezone.localdate()
    first_of_month = today.replace(day=1)
    last_month_end = first_of_month - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    initial['period_start'] = last_month_start
    initial['period_end'] = last_month_end
    if owner_id:
        try:
            owner = Owner.objects.get(pk=owner_id)
        except (Owner.DoesNotExist, ValueError):
            pass
        else:
            initial['period_start'], initial['period_end'] = _suggested_period(
                owner, last_month_start, last_month_end
            )

    if request.method == 'POST':
        form = InvoiceCreateForm(request.POST)
        if form.is_valid():
            owner = form.cleaned_data['owner']
            period_start = form.cleaned_data['period_start']
            period_end = form.cleaned_data['period_end']
            notes = form.cleaned_data['notes']

            # Don't create an empty invoice (and burn an invoice number) when
            # there is nothing to bill — mirrors monthly generation (QA #7).
            preview = InvoiceService.calculate_invoice_preview(
                owner, period_start, period_end
            )
            if preview['total'] <= 0:
                messages.error(
                    request,
                    f"{owner.name} has nothing to bill for "
                    f"{period_start:%d/%m/%Y} – {period_end:%d/%m/%Y}.",
                )
                return render(request, 'invoicing/invoice_create.html', {
                    'form': form, 'preview': preview,
                })

            try:
                invoices = InvoiceService.create_invoices(
                    owner, period_start, period_end, notes
                )
            except (DuplicateInvoiceError, NothingToInvoiceError) as e:
                messages.error(request, str(e))
                return render(request, 'invoicing/invoice_create.html', {
                    'form': form, 'preview': None,
                })

            if len(invoices) == 1:
                msg = f"Invoice {invoices[0].invoice_number} created successfully."
            else:
                msg = (
                    f"{len(invoices)} invoices created, one per horse: "
                    + ", ".join(
                        f"{inv.invoice_number} ({inv.horse.name})" for inv in invoices
                    )
                    + "."
                )
            if form.start_moved_from:
                msg += (
                    f" {'It starts' if len(invoices) == 1 else 'They start'} on "
                    f"{period_start:%d/%m/%Y}: the days before that are already invoiced."
                )
            messages.success(request, msg)
            if len(invoices) == 1:
                return redirect('invoice_detail', pk=invoices[0].pk)
            return redirect(f"{reverse('invoice_list')}?owner={owner.pk}")
    else:
        form = InvoiceCreateForm(initial=initial)

    # Show preview if owner and dates are provided
    preview = None
    if owner_id and initial.get('period_start') and initial.get('period_end'):
        try:
            owner = Owner.objects.get(pk=owner_id)
            preview = InvoiceService.preview_unbilled(
                owner,
                initial['period_start'],
                initial['period_end']
            )
        except (Owner.DoesNotExist, ValueError):
            pass

    return render(request, 'invoicing/invoice_create.html', {
        'form': form,
        'preview': preview,
    })


@feature_required('invoices', LEVEL_VIEW)
def invoice_preview(request):
    """AJAX preview of invoice charges."""
    # A pushed-history / bookmarked / reloaded full-page hit would render a
    # bare unstyled fragment — send those to the create form instead.
    if request.headers.get('HX-Request') != 'true':
        url = reverse('invoice_create')
        owner_id = request.GET.get('owner')
        if owner_id:
            url = f"{url}?owner={owner_id}"
        return redirect(url)

    owner_id = request.GET.get('owner')
    period_start = request.GET.get('period_start')
    period_end = request.GET.get('period_end')

    if not all([owner_id, period_start, period_end]):
        return HttpResponse("Missing parameters", status=400)

    try:
        owner = Owner.objects.get(pk=owner_id)
        from datetime import datetime
        start = datetime.strptime(period_start, '%Y-%m-%d').date()
        end = datetime.strptime(period_end, '%Y-%m-%d').date()
    except (Owner.DoesNotExist, ValueError):
        return HttpResponse("Invalid parameters", status=400)

    # A new owner was picked: start the period the day after their last
    # invoice, so nobody has to look that date up. The new date inputs go
    # back as out-of-band swaps next to the preview.
    oob_fields = []
    if request.headers.get('HX-Trigger-Name') == 'owner':
        new_start, new_end = _suggested_period(owner, start, end)
        if (new_start, new_end) != (start, end):
            start, end = new_start, new_end
            form = InvoiceCreateForm(
                initial={'period_start': start, 'period_end': end}
            )
            oob_fields = [
                form[name].as_widget(attrs={'hx-swap-oob': 'true'})
                for name in ('period_start', 'period_end')
            ]

    preview = InvoiceService.preview_unbilled(owner, start, end)

    return render(request, 'invoicing/partials/preview_response.html', {
        'preview': preview,
        'oob_fields': oob_fields,
    })


def _suggested_period(owner, period_start, period_end):
    """Default create-form period for an owner who already has invoices.

    Starts the day after their last live invoice. If that is after the
    given end, the period runs to today instead. Owners with no invoice
    (or invoiced past today) keep the given period.
    """
    last = InvoiceService.last_invoiced_date(owner)
    if last is None:
        return period_start, period_end
    start = last + timedelta(days=1)
    if start <= period_end:
        return start, period_end
    today = timezone.localdate()
    if start <= today:
        return start, today
    return period_start, period_end


@feature_required('invoices', LEVEL_VIEW)
def invoice_pdf(request, pk):
    """The invoice as a PDF: a download, or with ``?inline=1`` the same
    file for the browser to show (the PDF tab of the invoice list's pane).
    """
    invoice = get_object_or_404(Invoice, pk=pk)
    pdf_file = generate_invoice_pdf(invoice)
    inline = request.GET.get('inline') == '1'

    response = HttpResponse(pdf_file.read(), content_type='application/pdf')
    response['Content-Disposition'] = attachment_header(
        f'{invoice.invoice_number}.pdf', inline=inline,
    )
    if inline:
        # The pane shows the PDF in a frame on this site. Everything else
        # the app serves stays unframeable (X_FRAME_OPTIONS = 'DENY').
        response['X-Frame-Options'] = 'SAMEORIGIN'
        # The same allowance in the CSP header (django-csp reads these off
        # the response): once for the enforced policy, once for report-only.
        framed = {'frame-ancestors': ["'self'"]}
        response._csp_replace = framed
        response._csp_replace_ro = framed
        # A draft changes; never show an old copy of one.
        response['Cache-Control'] = 'private, no-store'
    return response


@feature_required('invoices')
def invoice_send(request, pk):
    """Send invoice via email."""
    if request.method != 'POST':
        return redirect('invoice_detail', pk=pk)

    invoice = get_object_or_404(Invoice, pk=pk)
    # The invoice list's pane passes next, so a send from the pane comes
    # back to the list; anything else lands on the invoice's page.
    from core.views._next import safe_next

    back = safe_next(request, reverse('invoice_detail', kwargs={'pk': pk}))

    if invoice.status not in [Invoice.Status.DRAFT, Invoice.Status.SENT]:
        messages.error(request, "This invoice cannot be sent.")
        return redirect(back)

    if not invoice.owner.email:
        messages.error(request, "Owner doesn't have an email address.")
        return redirect(back)

    # Claim the invoice first, as the bulk send does: a double-click, or a
    # click while a bulk send of it is queued, used to email the owner twice.
    from notifications.emails import send_invoice_email

    from .tasks import claim_invoice_for_sending, send_invoice_email_task

    if invoice.status == Invoice.Status.DRAFT:
        if not claim_invoice_for_sending(invoice.pk):
            messages.info(request, "This invoice is already being sent.")
            return redirect(back)
        outcome = send_invoice_email_task(invoice.pk)
        if outcome == 'sent':
            messages.success(request, f"Invoice sent to {invoice.owner.email}")
        else:
            invoice.refresh_from_db(fields=['send_error'])
            messages.error(
                request,
                invoice.send_error or "Failed to send invoice. Check email configuration.",
            )
        return redirect(back)

    # A re-send of an invoice that was sent before. A second click just
    # after the first send finished is a double-click, not a re-send.
    if invoice.sent_at and timezone.now() - invoice.sent_at < RESEND_GAP:
        messages.info(request, "This invoice was emailed less than a minute ago.")
        return redirect(back)
    if not claim_invoice_for_sending(invoice.pk, statuses=[Invoice.Status.SENT]):
        messages.info(request, "This invoice is already being sent.")
        return redirect(back)
    try:
        success = send_invoice_email(invoice)
    finally:
        Invoice.objects.filter(pk=invoice.pk).update(send_queued_at=None)

    if success:
        invoice.mark_as_sent()
        messages.success(request, f"Invoice sent to {invoice.owner.email}")
    else:
        messages.error(request, "Failed to send invoice. Check email configuration.")

    return redirect(back)


# The shortest time between two emails of the same invoice from "Send".
RESEND_GAP = timedelta(minutes=1)


@feature_required('invoices')
def invoice_mark_paid(request, pk):
    """Mark invoice as paid."""
    if request.method != 'POST':
        return redirect('invoice_detail', pk=pk)

    # Callers embedded elsewhere (e.g. the dashboard widget) pass next so the
    # user stays where they were; unsafe/missing values fall back to detail.
    next_url = request.POST.get('next') or ''
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse('invoice_detail', kwargs={'pk': pk})

    # Lock the row: a double-click used to pass the status check twice and
    # record two balancing payments (amount_paid = 2x total).
    with transaction.atomic():
        invoice = get_object_or_404(Invoice.objects.select_for_update(), pk=pk)
        if invoice.status not in [Invoice.Status.SENT, Invoice.Status.OVERDUE]:
            messages.error(request, "Only sent or overdue invoices can be marked as paid.")
            return redirect(next_url)
        invoice.mark_as_paid(reference='Marked as paid')
    messages.success(request, f"Invoice {invoice.invoice_number} marked as paid.")
    return redirect(next_url)


@feature_required('invoices')
def invoice_delete(request, pk):
    """Delete a draft invoice that was never issued.

    The month-end task creates drafts for every owner, so a draft that is
    wrong (period, missing horse, owner set up late) needs removing before
    the correct invoice can be created for the same period. Cancelling
    also frees the period, but leaves a dead stub in the list; a draft
    nobody has seen can simply go. Its extra charges are released so the
    replacement invoice picks them up. Anything past draft is refused —
    see ``Invoice.deletion_blocker``.
    """
    if request.method != 'POST':
        return redirect('invoice_detail', pk=pk)

    invoice = get_object_or_404(Invoice.objects.select_related('owner'), pk=pk)

    blocker = invoice.deletion_blocker
    if blocker:
        messages.error(request, blocker)
        return redirect('invoice_detail', pk=pk)

    number = invoice.invoice_number
    owner_name = invoice.owner.name
    with transaction.atomic():
        released = invoice.release_extra_charges()
        invoice.delete()

    msg = f"Draft {number} for {owner_name} deleted."
    if released:
        msg += (
            f" {released} extra charge{'s' if released != 1 else ''} "
            "released for re-billing."
        )
    messages.success(request, msg)
    return redirect('invoice_list')


@feature_required('invoices', LEVEL_VIEW)
def aged_debtors(request):
    """Aged debtors: who owes what, bucketed by how overdue it is."""
    rows, totals = StatementService.aged_debtors()
    return render(request, 'invoicing/aged_debtors.html', {
        'rows': rows,
        'totals': totals,
        'buckets': StatementService.BUCKETS,
    })


@feature_required('invoices', LEVEL_VIEW)
def owner_statement(request, owner_pk):
    """Statement of account for one owner."""
    owner = get_object_or_404(Owner, pk=owner_pk)
    statement = StatementService.build_owner_statement(owner)
    return render(request, 'invoicing/owner_statement.html', {
        'statement': statement,
        'owner': owner,
    })


@feature_required('invoices', LEVEL_VIEW)
def owner_statement_pdf(request, owner_pk):
    """Download an owner's statement of account as PDF."""
    owner = get_object_or_404(Owner, pk=owner_pk)
    statement = StatementService.build_owner_statement(owner)
    pdf_file = generate_owner_statement_pdf(owner, statement)

    response = HttpResponse(pdf_file.read(), content_type='application/pdf')
    filename = f"Statement - {owner.name} - {timezone.localdate():%Y-%m-%d}.pdf"
    response['Content-Disposition'] = attachment_header(filename)
    return response


@feature_required('invoices')
def owner_statement_email(request, owner_pk):
    """Email an owner their statement of account with PDF attached."""
    if request.method != 'POST':
        return redirect('owner_statement', owner_pk=owner_pk)

    owner = get_object_or_404(Owner, pk=owner_pk)
    if not owner.email:
        messages.error(request, f"{owner.name} has no email address on file.")
        return redirect('owner_statement', owner_pk=owner_pk)

    from notifications.emails import send_owner_statement
    from notifications.mail import can_email

    if not can_email(owner):
        # A bounced address: "check email configuration" sent staff to the
        # wrong place.
        messages.error(
            request,
            f"Emails to {owner.email} are not arriving (the address bounced). "
            "Check the address on the owner page, then send again.",
        )
        return redirect('owner_statement', owner_pk=owner_pk)

    if send_owner_statement(owner):
        messages.success(request, f"Statement emailed to {owner.email}.")
    else:
        messages.error(request, "Failed to send statement. Check email configuration.")
    return redirect('owner_statement', owner_pk=owner_pk)


@feature_required('invoices')
def payment_create(request, pk):
    """Record a payment (possibly partial) against an invoice."""
    invoice = get_object_or_404(Invoice, pk=pk)
    in_popup = is_popup_request(request)

    if invoice.status == Invoice.Status.CANCELLED:
        messages.error(request, "Payments cannot be recorded against a cancelled invoice.")
        return popup_saved_response() if in_popup else redirect('invoice_detail', pk=pk)
    if invoice.balance_due <= 0:
        messages.info(request, "This invoice is already fully paid.")
        return popup_saved_response() if in_popup else redirect('invoice_detail', pk=pk)

    if request.method == 'POST':
        # Validate and save under a row lock so two concurrent submissions
        # cannot both pass the balance check and overpay the invoice.
        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
            form = PaymentForm(request.POST, invoice=invoice)
            if form.is_valid():
                payment = form.save(commit=False)
                payment.invoice = invoice
                payment.save()
                invoice.refresh_payment_status()
        if form.is_valid():
            if invoice.balance_due <= 0:
                messages.success(
                    request,
                    f"Payment of {gbp(payment.amount)} recorded — "
                    f"invoice {invoice.invoice_number} is now fully paid."
                )
            else:
                messages.success(
                    request,
                    f"Payment of {gbp(payment.amount)} recorded — "
                    f"{gbp(invoice.balance_due)} still outstanding."
                )
            if in_popup:
                return popup_saved_response()
            return redirect('invoice_detail', pk=pk)
    else:
        form = PaymentForm(
            invoice=invoice,
            initial={
                'date': timezone.localdate(),
                'amount': invoice.balance_due,
            },
        )

    template = 'includes/popup_form.html' if in_popup else 'invoicing/payment_form.html'
    return render(request, template, {
        'form': form,
        'invoice': invoice,
        'in_popup': in_popup,
        'popup_submit_label': 'Record Payment',
    })


@feature_required('invoices')
def credit_note_create(request, pk):
    """Issue a credit note against a sent invoice (invoicing.services)."""
    from .forms import CreditNoteForm
    from .services import CreditNoteRefused, issue_credit_note

    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method == 'POST':
        form = CreditNoteForm(request.POST, invoice=invoice)
        if form.is_valid():
            try:
                credit = issue_credit_note(
                    invoice, form.cleaned_data['amount'], form.cleaned_data['reason'],
                    form.cleaned_data['date'], request.user,
                )
            except CreditNoteRefused as exc:
                form.add_error(None, str(exc))
            else:
                invoice.refresh_from_db()
                messages.success(
                    request,
                    f"Credit note {credit.credit_note_number} for {gbp(credit.amount)} issued. "
                    f"{gbp(invoice.balance_due)} now outstanding. Enter it in Xero too.",
                )
                return redirect('invoice_detail', pk=invoice.pk)
    else:
        form = CreditNoteForm(invoice=invoice, initial={'date': timezone.localdate()})
    return render(request, 'invoicing/credit_note_form.html', {'form': form, 'invoice': invoice})


@feature_required('invoices', LEVEL_VIEW)
def credit_note_pdf(request, pk):
    from .pdf import generate_credit_note_pdf

    credit = get_object_or_404(
        Payment.all_objects.select_related('invoice__owner'),
        pk=pk, method=Payment.Method.CREDIT_NOTE,
    )
    response = HttpResponse(generate_credit_note_pdf(credit).read(), content_type='application/pdf')
    response['Content-Disposition'] = attachment_header(f'{credit.credit_note_number}.pdf')
    return response


@feature_required('invoices')
def payment_void(request, pk):
    """Void a payment entered by mistake. It stays on the invoice, struck
    through, with who voided it and why; the balance no longer counts it."""
    payment = get_object_or_404(Payment.objects.select_related('invoice'), pk=pk)
    invoice = payment.invoice

    if request.method != 'POST':
        return redirect('invoice_detail', pk=invoice.pk)

    reason = (request.POST.get('reason') or '').strip()
    if not reason:
        messages.error(request, "Say why you are voiding this payment, for example \"entered twice\".")
        return redirect('invoice_detail', pk=invoice.pk)

    payment.void(request.user, reason)
    invoice.refresh_from_db()
    messages.success(
        request,
        f"Payment of {gbp(payment.amount)} voided. {gbp(invoice.balance_due)} now outstanding.",
        extra_tags=undo_tag(reverse('payment_unvoid', args=[payment.pk])),
    )
    return redirect('invoice_detail', pk=invoice.pk)


@feature_required('invoices')
@require_POST
def payment_unvoid(request, pk):
    payment = get_object_or_404(
        Payment.all_objects.select_related('invoice').filter(voided_at__isnull=False), pk=pk,
    )
    invoice = payment.invoice
    if payment.amount > invoice.balance_due:
        messages.error(
            request,
            f"Restoring this {gbp(payment.amount)} payment would pay more than the "
            f"{gbp(invoice.balance_due)} outstanding. Void the other payment first.",
        )
        return redirect('invoice_detail', pk=invoice.pk)
    payment.unvoid()
    messages.success(request, f"Payment of {gbp(payment.amount)} restored.")
    return redirect('invoice_detail', pk=invoice.pk)


@feature_required('invoices')
def invoice_bulk_action(request):
    """Send, mark-paid, push or delete a selection of invoices in one action.

    Ineligible invoices in the selection are skipped and reported rather
    than failing the whole batch, so "select all → send" after a monthly
    generation run just works.
    """
    if request.method != 'POST':
        return redirect('invoice_list')

    action = request.POST.get('action')
    ids = request.POST.getlist('invoice_ids')

    # Pushing to Xero is gated by the Xero feature on the single-invoice
    # endpoint (xero_push_invoice); the bulk path must enforce the same
    # gate or an invoices-only role could reach the external system.
    if action == 'push_xero' and not has_feature_access(request.user, 'xero', LEVEL_FULL):
        raise PermissionDenied

    next_url = request.POST.get('next') or ''
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse('invoice_list')

    invoices = list(Invoice.objects.filter(pk__in=ids).select_related('owner'))
    if not invoices:
        messages.error(request, "No invoices selected.")
        return redirect(next_url)

    if action == 'send':
        from .tasks import claim_invoice_for_sending, send_invoice_email_task

        # Each invoice is claimed (send_queued_at) and handed to a Celery
        # task, so the request returns at once and a double-submit cannot
        # email an owner twice. Without a worker (Vercel) the task runs
        # inline here, which is the old behaviour.
        async_send = getattr(settings, 'INVOICE_SEND_ASYNC', False)
        sent = failed = skipped = queued = 0
        no_email = []
        for invoice in invoices:
            if invoice.status != Invoice.Status.DRAFT:
                skipped += 1
                continue
            if not invoice.owner.email:
                no_email.append(invoice.invoice_number)
                continue
            if not claim_invoice_for_sending(invoice.pk):
                skipped += 1  # already queued by another submit
                continue
            if async_send:
                # The claim above is already committed (no atomic block
                # around this view), so the worker will see it.
                send_invoice_email_task.delay(invoice.pk)
                queued += 1
                continue
            outcome = send_invoice_email_task(invoice.pk)
            if outcome == 'sent':
                sent += 1
            elif outcome == 'failed':
                failed += 1
            else:
                skipped += 1
        if queued:
            messages.success(
                request,
                f"Sending {queued} invoice{'s' if queued != 1 else ''} in the "
                "background — each shows as Sent once its email has gone. An "
                "invoice that stays Draft with a send error needs attention.",
            )
        if sent:
            messages.success(request, f"Sent {sent} invoice{'s' if sent != 1 else ''}.")
        if no_email:
            messages.warning(
                request,
                f"Not sent (owner has no email address): {', '.join(no_email)}."
            )
        if failed:
            messages.error(
                request,
                f"{failed} invoice{'s' if failed != 1 else ''} failed to send. "
                "Check email configuration."
            )
        if skipped:
            messages.info(
                request,
                f"Skipped {skipped} invoice{'s' if skipped != 1 else ''} "
                "already sent, paid, cancelled or already being sent."
            )
    elif action == 'mark_paid':
        paid = skipped = 0
        for invoice in invoices:
            # Locked and read again, as the single "Mark as paid" does: two
            # overlapping submits each recorded a balancing payment.
            with transaction.atomic():
                invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
                if invoice.status in (Invoice.Status.SENT, Invoice.Status.OVERDUE):
                    invoice.mark_as_paid(reference='Bulk marked as paid')
                    paid += 1
                else:
                    skipped += 1
        if paid:
            messages.success(
                request,
                f"Marked {paid} invoice{'s' if paid != 1 else ''} as paid."
            )
        if skipped:
            messages.info(
                request,
                f"Skipped {skipped} invoice{'s' if skipped != 1 else ''} "
                "not in a payable state (only sent/overdue can be marked paid)."
            )
    elif action == 'delete':
        # Same rule as the single delete (invoice_delete): only a draft that
        # was never issued goes. The rest stay and are reported, so a
        # "select all → delete" cannot remove an issued invoice.
        deleted = 0
        blocked = []
        for invoice in invoices:
            if invoice.deletion_blocker:
                blocked.append(invoice.invoice_number)
                continue
            with transaction.atomic():
                invoice.release_extra_charges()
                invoice.delete()
            deleted += 1
        if deleted:
            messages.success(
                request,
                f"Deleted {deleted} draft invoice{'s' if deleted != 1 else ''}. "
                "Their extra charges are back to unbilled.",
            )
        if blocked:
            messages.info(
                request,
                f"Not deleted (already sent, paid in part, or in Xero): "
                f"{', '.join(blocked)}. Cancel these instead.",
            )
    elif action == 'push_xero':
        from xero_integration.models import XeroConnection, XeroInvoiceSync
        from xero_integration.tasks import push_invoices_to_xero

        if not XeroConnection.get_connection().is_connected:
            messages.error(
                request, "Xero is not connected — connect it from Settings first."
            )
            return redirect(next_url)

        to_push = []
        skipped = 0
        for invoice in invoices:
            if invoice.status == Invoice.Status.CANCELLED:
                skipped += 1
                continue
            try:
                already = invoice.xero_sync.sync_status in (
                    XeroInvoiceSync.SyncStatus.PUSHED,
                    XeroInvoiceSync.SyncStatus.PAID_IN_XERO,
                )
            except XeroInvoiceSync.DoesNotExist:
                already = False
            if already:
                skipped += 1
                continue
            to_push.append(invoice.pk)

        if to_push:
            count = len(to_push)
            plural = 's' if count != 1 else ''
            # Paced to stay inside Xero's rate limit, so a large selection
            # takes minutes: a worker does it, not this request. Without a
            # worker (Vercel) it runs here, as before.
            if getattr(settings, 'INVOICE_SEND_ASYNC', False):
                push_invoices_to_xero.delay(to_push)
                messages.success(
                    request,
                    f"Pushing {count} invoice{plural} to Xero in the background. "
                    "This takes about 3 seconds for each invoice. Each invoice "
                    "shows \"In Xero\" when it is done, or \"Retry Xero\" if it failed.",
                )
            else:
                summary = push_invoices_to_xero(to_push)
                messages.info(request, summary)
        if skipped:
            messages.info(
                request,
                f"Skipped {skipped} invoice{'s' if skipped != 1 else ''} "
                "already in Xero or cancelled."
            )
    else:
        messages.error(request, "Unknown bulk action.")

    return redirect(next_url)


@feature_required('invoices')
def invoice_generate_monthly(request):
    """Generate invoices for all owners for a month."""
    if request.method == 'POST':
        form = MonthlyInvoiceForm(request.POST)
        if form.is_valid():
            year = form.cleaned_data['year']
            month = int(form.cleaned_data['month'])

            invoices, skipped = InvoiceService.generate_monthly_invoices(year, month)

            msg = f"Generated {len(invoices)} invoice{'s' if len(invoices) != 1 else ''}."
            if skipped:
                names = ', '.join(o.name for o in skipped)
                msg += f" Skipped {len(skipped)} (already invoiced): {names}."
            messages.success(request, msg)
            return redirect('invoice_list')
    else:
        form = MonthlyInvoiceForm()

    return render(request, 'invoicing/invoice_generate.html', {
        'form': form,
    })


@feature_required('invoices', LEVEL_VIEW)
def invoice_csv(request, pk):
    """Download a single invoice as Xero-compatible CSV."""
    invoice = get_object_or_404(Invoice, pk=pk)

    if invoice.status == Invoice.Status.CANCELLED:
        messages.error(
            request,
            "Cancelled invoices cannot be exported — importing one into Xero "
            "would raise a receivable that was voided here.",
        )
        return redirect('invoice_detail', pk=pk)

    output = io.StringIO()
    try:
        write_xero_csv(invoice, output)
    except UnsupportedVatRateError as exc:
        messages.error(request, str(exc))
        return redirect('invoice_detail', pk=pk)

    # The byte-order mark tells Excel the file is UTF-8, so "Siobhán" does
    # not open as "SiobhÃ¡n". Xero's import accepts it (Excel's own
    # "CSV UTF-8" format writes one).
    response = HttpResponse('\ufeff' + output.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = attachment_header(f'{invoice.invoice_number}.csv')
    return response


@feature_required('invoices', LEVEL_VIEW)
def invoice_export_csv(request):
    """Bulk export invoices as Xero-compatible CSV.

    Cancelled invoices are never exported (importing them into Xero would
    raise receivables that were voided here); drafts are excluded unless
    explicitly requested via ?status=draft.
    """
    queryset = Invoice.objects.select_related('owner', 'horse').exclude(
        status=Invoice.Status.CANCELLED
    ).order_by('-created_at')

    status = request.GET.get('status')
    if status and status != Invoice.Status.CANCELLED:
        queryset = queryset.filter(status=status)
    else:
        queryset = queryset.exclude(status=Invoice.Status.DRAFT)

    owner = request.GET.get('owner')
    if owner and owner.isdigit():
        queryset = queryset.filter(owner_id=owner)

    search = request.GET.get('search', '').strip()
    if search:
        queryset = queryset.filter(
            Q(invoice_number__icontains=search)
            | Q(owner__name__icontains=search)
        )

    queryset = _apply_period_filter(queryset, request)

    output = io.StringIO()
    queryset = queryset.prefetch_related(Prefetch(
        'line_items',
        queryset=InvoiceLineItem.objects.select_related('horse', 'charge').order_by('line_type', 'description'),
    ))
    try:
        write_xero_csv(list(queryset), output)
    except UnsupportedVatRateError as exc:
        # The whole export is refused, not just the offending invoice: a
        # file that is quietly missing rows is worse than one that is not
        # produced, because nothing downstream would say so.
        messages.error(request, f"{exc} Nothing was exported.")
        back = reverse('invoice_list')
        filters = request.GET.urlencode()
        return redirect(f"{back}?{filters}" if filters else back)

    today = timezone.now().strftime('%Y-%m-%d')
    # The byte-order mark tells Excel the file is UTF-8, so "Siobhán" does
    # not open as "SiobhÃ¡n". Xero's import accepts it (Excel's own
    # "CSV UTF-8" format writes one).
    response = HttpResponse('\ufeff' + output.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = attachment_header(f'invoices-export-{today}.csv')
    return response


@feature_required('finances')
def reconciliation(request):
    """Monthly invoiced, received and outstanding figures for one year, with
    the invoice-number gaps explained (invoicing/reconciliation.py)."""
    from core.csv_export import csv_response
    from core.models import BusinessSettings

    from .reconciliation import monthly_report, number_gaps

    this_year = timezone.localdate().year
    try:
        year = int(request.GET.get('year', this_year))
    except ValueError:
        year = this_year
    year = max(2000, min(year, this_year))
    report = monthly_report(year)

    if request.GET.get('format') == 'csv':
        rows = [
            [f'{m.month:%b %Y}', m.invoice_count, m.net, m.vat, m.total, m.received, m.credited, m.outstanding]
            for m in report.months
        ]
        t = report.totals
        rows.append(['Total', t.invoice_count, t.net, t.vat, t.total, t.received, t.credited, t.outstanding])
        return csv_response(
            f'reconciliation-{year}',
            ['Month', 'Invoices', 'Net', 'VAT', 'Total invoiced', 'Received', 'Credited', 'Outstanding at month end'],
            rows,
        )

    prefix = BusinessSettings.get_settings().invoice_prefix
    return render(request, 'invoicing/reconciliation.html', {
        'report': report,
        'year': year,
        'years': list(range(this_year, max(this_year - 7, 1999), -1)),
        'gaps': number_gaps(prefix),
        'prefix': prefix,
    })
