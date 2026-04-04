"""
Forms for invoicing app.
"""

from django import forms
from django.utils import timezone

from core.models import Invoice, Owner


class InvoiceCreateForm(forms.Form):
    """Form for creating a new invoice."""

    owner = forms.ModelChoiceField(
        queryset=Owner.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    period_start = forms.DateField(
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'})
    )
    period_end = forms.DateField(
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'})
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
            'payment_terms_days': forms.NumberInput(attrs={'class': 'form-input'}),
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
        widget=forms.NumberInput(attrs={'class': 'form-input'}),
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
