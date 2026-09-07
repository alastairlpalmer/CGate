"""
Forms for billing app.
"""

import json

from django import forms

from core.forms import owners_for_picker
from core.images import heic_to_jpeg

from .models import ExtraCharge, FeedOut, FeedStock, ServiceItem, ServiceProvider, YardCost

# ─── Services directory picker ────────────────────────────────────────
#
# Every record form that can bill an owner gets a "Service" select as its
# first field. Each option carries the item's price and the record fields
# it fills (data-price / data-fill); static/js/service_picker.js copies
# them into the form on change, locks the cost behind an "Override price"
# button, and unlocks everything for "Other". The server repeats the fill
# in clean() so a form posted without JavaScript still prices correctly.

SERVICE_OTHER = 'other'


class ServicePickerSelect(forms.Select):
    """Select whose options carry the service item's price and fill values."""

    def __init__(self, attrs=None, choices=()):
        super().__init__(attrs, choices)
        self.items_by_pk = {}
        self.fill = ()

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        item = self.items_by_pk.get(str(value))
        if item is not None:
            option['attrs']['data-price'] = str(item.price)
            option['attrs']['data-fill'] = json.dumps(_js_fill_values(item, self.fill))
        return option


def _js_fill_values(item, fill):
    """{form_field: value} for the browser — model instances become pks."""
    values = {}
    for form_field, attr in fill:
        value = item.fill_value(attr)
        if value is None:
            continue
        values[form_field] = getattr(value, 'pk', value)
    return values


class ServicePickerField(forms.ChoiceField):
    """Choose a priced service (or "Other") — cleans to a ServiceItem or None.

    Built per form instance so the option list always reflects the current
    directory. ``category`` limits the list (a str, or a tuple to offer
    several categories as option groups; None offers every active item).
    """

    def __init__(self, *, category=None, fill=(), **kwargs):
        kwargs.setdefault('required', False)
        kwargs.setdefault('label', 'Service')
        widget = ServicePickerSelect(attrs={'class': 'form-select', 'data-service-picker': ''})
        super().__init__(choices=(), widget=widget, **kwargs)
        self.widget.fill = tuple(fill)
        self.load_items(category)

    def load_items(self, category):
        queryset = ServiceItem.objects.filter(is_active=True)
        if isinstance(category, str):
            queryset = queryset.filter(category=category)
        elif category:
            queryset = queryset.filter(category__in=category)
        items = list(queryset)
        self.widget.items_by_pk = {str(item.pk): item for item in items}

        def option(item):
            return (str(item.pk), f"{item.name} — {item.price_label}")

        choices = [('', 'Choose a service…')]
        if isinstance(category, str) or not items:
            choices += [option(item) for item in items]
        else:
            labels = dict(ServiceItem.Category.choices)
            groups = {}
            for item in items:
                groups.setdefault(item.category, []).append(option(item))
            choices += [(labels.get(cat, cat), opts) for cat, opts in groups.items()]
        choices.append((SERVICE_OTHER, 'Other (enter details manually)'))
        self.choices = choices

    def clean(self, value):
        value = super().clean(value)
        if not value or value == SERVICE_OTHER:
            return None
        return self.widget.items_by_pk[str(value)]


class ServicePickerMixin:
    """Add the services picker to a record form and apply the chosen item.

    Subclasses set ``service_category`` (which items to offer),
    ``service_fill`` — ``((form_field, item_attr), …)`` copied onto the
    record when the form field is blank — and ``service_cost_field`` (the
    money field the price goes into, ``cost`` by default).

    Only blank fields are filled: a chosen service never overwrites a
    price or detail the user typed, which is what makes "override" work.
    """

    service_category = None
    service_fill = ()
    service_cost_field = 'cost'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        picker = ServicePickerField(category=self.service_category, fill=self.service_fill)
        cost_field = self.fields.get(self.service_cost_field)
        if cost_field is not None:
            cost_field.widget.attrs.setdefault('data-service-cost', '')
        # The picker leads: choose the service, then only touch what differs.
        self.fields = {'service_item': picker, **self.fields}

    def clean(self):
        cleaned_data = super().clean()
        item = cleaned_data.get('service_item')
        if item is None:
            return cleaned_data
        cost_name = self.service_cost_field
        if cost_name in self.fields:
            raw_cost = (self.data.get(self.add_prefix(cost_name)) or '').strip()
            if not raw_cost:
                cleaned_data[cost_name] = item.price
                self.errors.pop(cost_name, None)
        for form_field, attr in self.service_fill:
            if form_field not in self.fields:
                continue
            value = item.fill_value(attr)
            if value is None:
                continue
            if form_field in self.errors:
                # A required field left blank because the service was meant
                # to supply it (no JavaScript): supply it now.
                if (self.data.get(self.add_prefix(form_field)) or '').strip():
                    continue
                self.errors.pop(form_field, None)
                cleaned_data[form_field] = value
            elif not cleaned_data.get(form_field):
                cleaned_data[form_field] = value
        return cleaned_data


class ServiceItemForm(forms.ModelForm):
    class Meta:
        model = ServiceItem
        fields = ['name', 'category', 'price', 'farrier_work', 'vaccination_type', 'sort_order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Wormer - Equimax'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal', 'min': '0'}),
            'farrier_work': forms.Select(attrs={'class': 'form-select'}),
            'vaccination_type': forms.Select(attrs={'class': 'form-select'}),
            'sort_order': forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric', 'min': '0'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }
        labels = {'price': 'Price (£)', 'sort_order': 'Sort order'}
        help_texts = {'sort_order': 'Lower numbers list first within the category.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from health.models import VaccinationType
        self.fields['vaccination_type'].queryset = VaccinationType.objects.filter(is_active=True)
        self.fields['vaccination_type'].empty_label = '— not set —'

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get('category')
        # The extra fields only mean something for their own category.
        if category != ServiceItem.Category.FARRIER:
            cleaned_data['farrier_work'] = ''
        if category != ServiceItem.Category.VACCINATION:
            cleaned_data['vaccination_type'] = None
        return cleaned_data


class ExtraChargeForm(ServicePickerMixin, forms.ModelForm):
    service_fill = (('description', 'name'), ('charge_type', 'charge_type'))
    service_cost_field = 'amount'

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
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'description': forms.TextInput(attrs={'class': 'form-input'}),
            'amount': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
            'split_by_ownership': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'receipt_image': forms.FileInput(attrs={'class': 'form-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Archived owners leave every picker; a charge already billed to
        # one keeps that owner in its own list so it can still be edited.
        self.fields['owner'].queryset = owners_for_picker(self.instance.owner_id)

    def clean_receipt_image(self):
        return heic_to_jpeg(self.cleaned_data.get('receipt_image'))


class BulkChargeForm(ServicePickerMixin, forms.ModelForm):
    """One charge per selected horse (transport, worm test, anything priced).

    Rendered by the bulk-actions sheet; health.views.bulk_health_apply bills
    each horse's current owner, so there is no horse or owner field here.
    """

    service_fill = (('description', 'name'), ('charge_type', 'charge_type'))
    service_cost_field = 'amount'

    class Meta:
        model = ExtraCharge
        fields = ['date', 'charge_type', 'description', 'amount', 'service_provider', 'notes']
        labels = {'amount': 'Amount per horse (£)'}
        help_texts = {'amount': 'Charged in full to each selected horse’s owner.'}
        widgets = {
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'charge_type': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.TextInput(attrs={'class': 'form-input'}),
            'amount': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal', 'min': '0'}),
            'service_provider': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['amount'].required = False

    def clean_amount(self):
        # Blank means "use the service price" (filled in by the mixin); with
        # no service chosen a charge still needs a real amount.
        return self.cleaned_data.get('amount')

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('amount') is None and 'amount' not in self.errors:
            self.add_error('amount', 'Enter an amount, or choose a service with a set price.')
        return cleaned_data


class YardCostForm(forms.ModelForm):
    class Meta:
        model = YardCost
        fields = [
            'category', 'date', 'supplier', 'description',
            'amount', 'vat_amount', 'is_recurring', 'recurrence_interval', 'recurrence_end_date',
            'receipt_image', 'notes',
        ]
        widgets = {
            'category': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'supplier': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Local Hay Merchant'}),
            'description': forms.TextInput(attrs={'class': 'form-input'}),
            'amount': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
            'vat_amount': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
            'is_recurring': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'recurrence_interval': forms.Select(attrs={'class': 'form-select'}),
            'recurrence_end_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'receipt_image': forms.FileInput(attrs={'class': 'form-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def clean_receipt_image(self):
        return heic_to_jpeg(self.cleaned_data.get('receipt_image'))


class FeedOutForm(forms.ModelForm):
    class Meta:
        model = FeedOut
        fields = ['date', 'feed_type', 'quantity_numeric', 'unit', 'quantity', 'total_cost', 'is_recharged', 'notes']
        widgets = {
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'feed_type': forms.Select(attrs={'class': 'form-select'}),
            'quantity_numeric': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal', 'placeholder': 'e.g. 3'}),
            'unit': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Description (e.g. 2 round bales)'}),
            'total_cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal', 'placeholder': 'Auto-calculated if left blank'}),
            # x-model keeps the template's recharge panel in step with the box
            # itself, not only with a click on its label.
            'is_recharged': forms.CheckboxInput(attrs={'class': 'form-checkbox', 'x-model': 'isRecharged'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['total_cost'].required = False


class FeedStockForm(forms.ModelForm):
    class Meta:
        model = FeedStock
        fields = ['site', 'feed_type', 'date', 'quantity', 'unit', 'entry_type', 'supplier', 'cost', 'notes']
        widgets = {
            'site': forms.Select(attrs={'class': 'form-select'}),
            'feed_type': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
            'unit': forms.Select(attrs={'class': 'form-select'}),
            'entry_type': forms.Select(attrs={'class': 'form-select'}),
            'supplier': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Local Hay Merchant'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Location
        # Sites that still have a field in use — nothing new is booked
        # against a site whose fields are all archived.
        sites = Location.objects.active().values_list(
            'site', flat=True
        ).distinct().order_by('site')
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
            'phone': forms.TextInput(attrs={'class': 'form-input', 'type': 'tel', 'autocomplete': 'tel'}),
            'email': forms.EmailInput(attrs={'class': 'form-input'}),
            'address': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }
