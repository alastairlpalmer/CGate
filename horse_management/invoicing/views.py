"""
Views for invoicing app.
"""

import io
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin

from core.mixins import StaffRequiredMixin, staff_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import DetailView, ListView, UpdateView

from core.models import Owner
from invoicing.models import Invoice

from .forms import InvoiceCreateForm, InvoiceUpdateForm, MonthlyInvoiceForm
from .pdf import generate_invoice_pdf
from .services import DuplicateInvoiceError, InvoiceService
from .utils import group_line_items_by_horse, write_xero_csv


class InvoiceListView(LoginRequiredMixin, ListView):
    model = Invoice
    template_name = 'invoicing/invoice_list.html'
    context_object_name = 'invoices'
    paginate_by = 25

    def get_queryset(self):
        queryset = Invoice.objects.select_related('owner', 'xero_sync')

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
    invoice.mark_as_paid()
    messages.success(request, f"Invoice {invoice.invoice_number} marked as paid.")
    return redirect('invoice_detail', pk=pk)


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
