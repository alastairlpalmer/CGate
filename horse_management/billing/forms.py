"""
Forms for billing app.
"""

from django import forms

from .models import ExtraCharge, ServiceProvider, YardCost


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
