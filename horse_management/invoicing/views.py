"""
Views for invoicing app.
"""

import io
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin

from core.mixins import StaffRequiredMixin, staff_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import DetailView, ListView, UpdateView

from core.models import Owner
from invoicing.models import Invoice

from .forms import InvoiceCreateForm, InvoiceUpdateForm, MonthlyInvoiceForm, PaymentForm
from .pdf import generate_invoice_pdf
from .services import DuplicateInvoiceError, InvoiceService
from .utils import group_line_items_by_horse, write_xero_csv

logger = logging.getLogger(__name__)


class InvoiceListView(LoginRequiredMixin, ListView):
    model = Invoice
    template_name = 'invoicing/invoice_list.html'
    context_object_name = 'invoices'
    paginate_by = 25

    def get_queryset(self):
        from decimal import Decimal

        from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
        from django.db.models.functions import Coalesce

        queryset = Invoice.objects.select_related('owner', 'xero_sync').annotate(
            balance=ExpressionWrapper(
                F('total') - Coalesce(
                    Sum('payments__amount'), Value(Decimal('0.00'))
                ),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )

        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)

        owner = self.request.GET.get('owner')
        if owner:
            queryset = queryset.filter(owner_id=owner)

        search = self.request.GET.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(invoice_number__icontains=search)
                | Q(owner__name__icontains=search)
            )

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['owners'] = Owner.objects.only('pk', 'name')
        context['status_choices'] = Invoice.Status.choices
        return context


class InvoiceDetailView(LoginRequiredMixin, DetailView):
    model = Invoice
    template_name = 'invoicing/invoice_detail.html'
    context_object_name = 'invoice'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        line_items = self.object.line_items.select_related(
            'horse', 'placement', 'charge'
        ).order_by('line_type', 'description')
        context['line_items'] = line_items
        context['horse_groups'] = group_line_items_by_horse(line_items)
        context['payments'] = self.object.payments.all()
        context['amount_paid'] = self.object.amount_paid
        context['balance_due'] = self.object.balance_due
        return context


class InvoiceUpdateView(StaffRequiredMixin, UpdateView):
    model = Invoice
    form_class = InvoiceUpdateForm
    template_name = 'invoicing/invoice_form.html'

    def form_valid(self, form):
        was_cancelled = (
            form.initial.get('status') == Invoice.Status.CANCELLED
        )
        response = super().form_valid(form)
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
        return response

    def get_success_url(self):
        return reverse_lazy('invoice_detail', kwargs={'pk': self.object.pk})


@staff_required
def invoice_create(request):
    """Create a new invoice."""
    initial = {}

    # Pre-fill owner if provided
    owner_id = request.GET.get('owner')
    if owner_id:
        initial['owner'] = owner_id

    # Default to last month
    today = timezone.now().date()
    first_of_month = today.replace(day=1)
    last_month_end = first_of_month - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    initial['period_start'] = last_month_start
    initial['period_end'] = last_month_end

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
                invoice = InvoiceService.create_invoice(
                    owner, period_start, period_end, notes
                )
            except DuplicateInvoiceError as e:
                messages.error(request, str(e))
                return render(request, 'invoicing/invoice_create.html', {
                    'form': form, 'preview': None,
                })

            messages.success(request, f"Invoice {invoice.invoice_number} created successfully.")
            return redirect('invoice_detail', pk=invoice.pk)
    else:
        form = InvoiceCreateForm(initial=initial)

    # Show preview if owner and dates are provided
    preview = None
    if owner_id and initial.get('period_start') and initial.get('period_end'):
        try:
            owner = Owner.objects.get(pk=owner_id)
            preview = InvoiceService.calculate_invoice_preview(
                owner,
                initial['period_start'],
                initial['period_end']
            )
        except Owner.DoesNotExist:
            pass

    return render(request, 'invoicing/invoice_create.html', {
        'form': form,
        'preview': preview,
    })


@login_required
def invoice_preview(request):
    """AJAX preview of invoice charges."""
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

    preview = InvoiceService.calculate_invoice_preview(owner, start, end)

    return render(request, 'invoicing/partials/preview.html', {
        'preview': preview,
    })


@login_required
def invoice_pdf(request, pk):
    """Download invoice as PDF."""
    invoice = get_object_or_404(Invoice, pk=pk)
    pdf_file = generate_invoice_pdf(invoice)

    response = HttpResponse(pdf_file.read(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{invoice.invoice_number}.pdf"'

    return response


@staff_required
def invoice_send(request, pk):
    """Send invoice via email."""
    if request.method != 'POST':
        return redirect('invoice_detail', pk=pk)

    invoice = get_object_or_404(Invoice, pk=pk)

    if invoice.status not in [Invoice.Status.DRAFT, Invoice.Status.SENT]:
        messages.error(request, "This invoice cannot be sent.")
        return redirect('invoice_detail', pk=pk)

    if not invoice.owner.email:
        messages.error(request, "Owner doesn't have an email address.")
        return redirect('invoice_detail', pk=pk)

    # Import here to avoid circular imports
    from notifications.emails import send_invoice_email

    success = send_invoice_email(invoice)

    if success:
        invoice.mark_as_sent()
        messages.success(request, f"Invoice sent to {invoice.owner.email}")
    else:
        messages.error(request, "Failed to send invoice. Check email configuration.")

    return redirect('invoice_detail', pk=pk)


@staff_required
def invoice_mark_paid(request, pk):
    """Mark invoice as paid."""
    if request.method != 'POST':
        return redirect('invoice_detail', pk=pk)

    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.status not in [Invoice.Status.SENT, Invoice.Status.OVERDUE]:
        messages.error(request, "Only sent or overdue invoices can be marked as paid.")
        return redirect('invoice_detail', pk=pk)
    invoice.mark_as_paid(reference='Marked as paid')
    messages.success(request, f"Invoice {invoice.invoice_number} marked as paid.")
    return redirect('invoice_detail', pk=pk)


@staff_required
def payment_create(request, pk):
    """Record a payment (possibly partial) against an invoice."""
    invoice = get_object_or_404(Invoice, pk=pk)

    if invoice.status == Invoice.Status.CANCELLED:
        messages.error(request, "Payments cannot be recorded against a cancelled invoice.")
        return redirect('invoice_detail', pk=pk)
    if invoice.balance_due <= 0:
        messages.info(request, "This invoice is already fully paid.")
        return redirect('invoice_detail', pk=pk)

    if request.method == 'POST':
        form = PaymentForm(request.POST, invoice=invoice)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.invoice = invoice
            payment.save()
            invoice.refresh_payment_status()
            if invoice.balance_due <= 0:
                messages.success(
                    request,
                    f"Payment of £{payment.amount:.2f} recorded — "
                    f"invoice {invoice.invoice_number} is now fully paid."
                )
            else:
                messages.success(
                    request,
                    f"Payment of £{payment.amount:.2f} recorded — "
                    f"£{invoice.balance_due:.2f} still outstanding."
                )
            return redirect('invoice_detail', pk=pk)
    else:
        form = PaymentForm(
            invoice=invoice,
            initial={
                'date': timezone.now().date(),
                'amount': invoice.balance_due,
            },
        )

    return render(request, 'invoicing/payment_form.html', {
        'form': form,
        'invoice': invoice,
    })


@staff_required
def payment_delete(request, pk):
    """Delete a mistakenly recorded payment and re-derive the invoice status."""
    from invoicing.models import Payment

    payment = get_object_or_404(Payment.objects.select_related('invoice'), pk=pk)
    invoice = payment.invoice

    if request.method != 'POST':
        return redirect('invoice_detail', pk=invoice.pk)

    amount = payment.amount
    payment.delete()
    invoice.refresh_payment_status()
    messages.success(
        request,
        f"Payment of £{amount:.2f} removed — "
        f"£{invoice.balance_due:.2f} now outstanding."
    )
    return redirect('invoice_detail', pk=invoice.pk)


@staff_required
def invoice_bulk_action(request):
    """Send or mark-paid a selection of invoices in one action.

    Ineligible invoices in the selection are skipped and reported rather
    than failing the whole batch, so "select all → send" after a monthly
    generation run just works.
    """
    if request.method != 'POST':
        return redirect('invoice_list')

    action = request.POST.get('action')
    ids = request.POST.getlist('invoice_ids')

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
        from notifications.emails import send_invoice_email

        sent = failed = skipped = 0
        no_email = []
        for invoice in invoices:
            if invoice.status != Invoice.Status.DRAFT:
                skipped += 1
                continue
            if not invoice.owner.email:
                no_email.append(invoice.invoice_number)
                continue
            if send_invoice_email(invoice):
                invoice.mark_as_sent()
                sent += 1
            else:
                failed += 1
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
                "already sent, paid or cancelled."
            )
    elif action == 'mark_paid':
        paid = skipped = 0
        for invoice in invoices:
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
    elif action == 'push_xero':
        from xero_integration.client import XeroAPIError, XeroTokenExpiredError
        from xero_integration.models import XeroConnection, XeroInvoiceSync
        from xero_integration.services import (
            XeroNotConnectedError,
            push_invoice_to_xero,
        )

        if not XeroConnection.get_connection().is_connected:
            messages.error(
                request, "Xero is not connected — connect it from Settings first."
            )
            return redirect(next_url)

        pushed = skipped = failed = 0
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
            try:
                push_invoice_to_xero(invoice)
                pushed += 1
            except (XeroNotConnectedError, XeroTokenExpiredError) as exc:
                # Connection-level failure — every remaining push would fail too.
                messages.error(request, f"Xero push stopped: {exc}")
                break
            except XeroAPIError as exc:
                failed += 1
                logger.warning(
                    "Xero push failed for %s: %s", invoice.invoice_number, exc
                )
        if pushed:
            messages.success(
                request,
                f"Pushed {pushed} invoice{'s' if pushed != 1 else ''} to Xero."
            )
        if failed:
            messages.error(
                request,
                f"{failed} invoice{'s' if failed != 1 else ''} failed to push — "
                "see the Xero status on each invoice for details."
            )
        if skipped:
            messages.info(
                request,
                f"Skipped {skipped} invoice{'s' if skipped != 1 else ''} "
                "already in Xero or cancelled."
            )
    else:
        messages.error(request, "Unknown bulk action.")

    return redirect(next_url)


@staff_required
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


@login_required
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
    write_xero_csv(invoice, output)

    response = HttpResponse(output.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{invoice.invoice_number}.csv"'
    return response


@login_required
def invoice_export_csv(request):
    """Bulk export invoices as Xero-compatible CSV.

    Cancelled invoices are never exported (importing them into Xero would
    raise receivables that were voided here); drafts are excluded unless
    explicitly requested via ?status=draft.
    """
    queryset = Invoice.objects.select_related('owner').exclude(
        status=Invoice.Status.CANCELLED
    ).order_by('-created_at')

    status = request.GET.get('status')
    if status and status != Invoice.Status.CANCELLED:
        queryset = queryset.filter(status=status)
    else:
        queryset = queryset.exclude(status=Invoice.Status.DRAFT)

    owner = request.GET.get('owner')
    if owner:
        queryset = queryset.filter(owner_id=owner)

    search = request.GET.get('search', '').strip()
    if search:
        queryset = queryset.filter(
            Q(invoice_number__icontains=search)
            | Q(owner__name__icontains=search)
        )

    date_from = request.GET.get('date_from')
    if date_from:
        from datetime import datetime
        try:
            queryset = queryset.filter(period_start__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            pass

    date_to = request.GET.get('date_to')
    if date_to:
        from datetime import datetime
        try:
            queryset = queryset.filter(period_end__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            pass

    output = io.StringIO()
    write_xero_csv(list(queryset), output)

    today = timezone.now().strftime('%Y-%m-%d')
    response = HttpResponse(output.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="invoices-export-{today}.csv"'
    return response
