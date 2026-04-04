"""
Forms for billing app.
"""

from django import forms

from .models import ExtraCharge, FeedOut, FeedStock, ServiceProvider, YardCost


class ExtraChargeForm(forms.ModelForm):
    class Meta:
        model = ExtraCharge
        fields = [
            'horse', 'owner', 'service_provider', 'charge_type',
            'date', 'description', 'amount', 'split_by_ownership',
            'receipt_image', 'notes'
        ]
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'owner': forms.Select(attrs={'class': 'form-select'}),
            'service_provider': forms.Select(attrs={'class': 'form-select'}),
            'charge_type': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'description': forms.TextInput(attrs={'class': 'form-input'}),
            'amount': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01'}),
            'split_by_ownership': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'receipt_image': forms.FileInput(attrs={'class': 'form-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class YardCostForm(forms.ModelForm):
    class Meta:
        model = YardCost
        fields = [
            'category', 'date', 'supplier', 'description',
            'amount', 'vat_amount', 'is_recurring', 'recurrence_interval',
            'receipt_image', 'notes',
        ]
        widgets = {
            'category': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'supplier': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Local Hay Merchant'}),
            'description': forms.TextInput(attrs={'class': 'form-input'}),
            'amount': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01'}),
            'vat_amount': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01'}),
            'is_recurring': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'recurrence_interval': forms.Select(attrs={'class': 'form-select'}),
            'receipt_image': forms.FileInput(attrs={'class': 'form-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class FeedOutForm(forms.ModelForm):
    class Meta:
        model = FeedOut
        fields = ['date', 'feed_type', 'quantity_numeric', 'unit', 'quantity', 'total_cost', 'is_recharged', 'notes']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'feed_type': forms.Select(attrs={'class': 'form-select'}),
            'quantity_numeric': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'placeholder': 'e.g. 3'}),
            'unit': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Description (e.g. 2 round bales)'}),
            'total_cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01'}),
            'is_recharged': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class FeedStockForm(forms.ModelForm):
    class Meta:
        model = FeedStock
        fields = ['site', 'feed_type', 'date', 'quantity', 'unit', 'entry_type', 'supplier', 'cost', 'notes']
        widgets = {
            'site': forms.Select(attrs={'class': 'form-select'}),
            'feed_type': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01'}),
            'unit': forms.Select(attrs={'class': 'form-select'}),
            'entry_type': forms.Select(attrs={'class': 'form-select'}),
            'supplier': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Local Hay Merchant'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Location
        sites = Location.objects.values_list('site', flat=True).distinct().order_by('site')
        self.fields['site'].widget = forms.Select(
            attrs={'class': 'form-select'},
            choices=[('', '---------')] + [(s, s) for s in sites],
        )


class ServiceProviderForm(forms.ModelForm):
    class Meta:
        model = ServiceProvider
        fields = ['name', 'provider_type', 'phone', 'email', 'address', 'notes', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'provider_type': forms.Select(attrs={'class': 'form-select'}),
            'phone': forms.TextInput(attrs={'class': 'form-input'}),
            'email': forms.EmailInput(attrs={'class': 'form-input'}),
            'address': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }
