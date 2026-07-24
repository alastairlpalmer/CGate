"""
Forms for invoicing app.
"""

from django import forms
from django.urls import reverse_lazy
from django.utils import timezone

from core.models import Owner
from invoicing.models import Invoice, Payment

# Refresh the live preview panel whenever the owner or period changes — the
# invoice_preview endpoint reads owner/period_start/period_end from the
# included form fields.
#
# The boosted <body> sets hx-select="#main-content", hx-swap="outerHTML" and
# hx-push-url="true", and htmx inherits all three onto descendant requests.
# Left inherited, the preview response (a partial with no #main-content)
# selects to nothing, the empty outerHTML swap deletes #preview-content, and
# the address bar is rewritten to /invoicing/preview/?...&csrfmiddlewaretoken=…
# — from there "Create Invoice" leaves the user on a blank page. Each must be
# overridden locally.
PREVIEW_HTMX_ATTRS = {
    'hx-get': reverse_lazy('invoice_preview'),
    'hx-trigger': 'change',
    'hx-target': '#preview-content',
    'hx-include': 'closest form',
    'hx-select': 'unset',
    'hx-swap': 'innerHTML',
    'hx-push-url': 'false',
    # The CSRF token has no business in a GET query string (URL bar,
    # history, server logs).
    'hx-params': 'not csrfmiddlewaretoken',
}


class InvoiceCreateForm(forms.Form):
    """Form for creating a new invoice."""

    owner = forms.ModelChoiceField(
        queryset=Owner.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select', **PREVIEW_HTMX_ATTRS})
    )
    period_start = forms.DateField(
        widget=forms.DateInput(
            format='%Y-%m-%d',
            attrs={'class': 'form-input', 'type': 'date', **PREVIEW_HTMX_ATTRS},
        )
    )
    period_end = forms.DateField(
        widget=forms.DateInput(
            format='%Y-%m-%d',
            attrs={'class': 'form-input', 'type': 'date', **PREVIEW_HTMX_ATTRS},
        )
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3})
    )

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get('period_start')
        end = cleaned_data.get('period_end')
        owner = cleaned_data.get('owner')

        if start and end and start > end:
            raise forms.ValidationError("Period start must be before period end.")

        if owner and start and end:
            from .services import InvoiceService
            existing = InvoiceService.check_for_overlapping_invoices(owner, start, end)
            if existing:
                raise forms.ValidationError(
                    f"{owner.name} already has invoice {existing.invoice_number} "
                    f"covering {existing.period_start} to {existing.period_end} "
                    f"which overlaps with this period."
                )

        return cleaned_data


class InvoiceUpdateForm(forms.ModelForm):
    """Form for updating invoice details."""

    # Valid status transitions
    ALLOWED_TRANSITIONS = {
        'draft': {'draft', 'sent', 'cancelled'},
        'sent': {'sent', 'paid', 'overdue', 'cancelled'},
        'overdue': {'overdue', 'paid', 'cancelled'},
        'paid': {'paid'},
        'cancelled': {'cancelled'},
    }

    class Meta:
        model = Invoice
        fields = ['status', 'payment_terms_days', 'due_date', 'notes']
        widgets = {
            'status': forms.Select(attrs={'class': 'form-select'}),
            'payment_terms_days': forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
            'due_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
        }

    def clean_status(self):
        new_status = self.cleaned_data['status']
        if self.instance and self.instance.pk:
            current_status = self.instance.status
            allowed = self.ALLOWED_TRANSITIONS.get(current_status, set())
            if new_status not in allowed:
                raise forms.ValidationError(
                    f"Cannot change status from '{self.instance.get_status_display()}' "
                    f"to '{dict(Invoice.Status.choices).get(new_status, new_status)}'."
                )
        return new_status


class PaymentForm(forms.ModelForm):
    """Form for recording a payment against an invoice."""

    class Meta:
        model = Payment
        fields = ['date', 'amount', 'method', 'reference', 'notes']
        widgets = {
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'amount': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
            'method': forms.Select(attrs={'class': 'form-select'}),
            'reference': forms.TextInput(attrs={'class': 'form-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if self.invoice is not None and amount > self.invoice.balance_due:
            raise forms.ValidationError(
                f"Amount exceeds the outstanding balance "
                f"(£{self.invoice.balance_due:.2f}). Overpayments/credits "
                "are not supported yet — record up to the balance."
            )
        return amount


class MonthlyInvoiceForm(forms.Form):
    """Form for generating monthly invoices."""

    MONTH_CHOICES = [
        (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
        (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
        (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December'),
    ]

    year = forms.IntegerField(
        min_value=2020,
        max_value=2100,
        widget=forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
    )
    month = forms.ChoiceField(
        choices=MONTH_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        now = timezone.now()
        self.fields['year'].initial = now.year
        self.fields['month'].initial = now.month
