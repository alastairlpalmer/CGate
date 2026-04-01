"""
Forms for core app.
"""

from django import forms

from .models import BusinessSettings, Horse, Location, Owner, OwnershipShare, Placement, RateType


def get_grouped_location_choices():
    """Build location choices grouped by site for <optgroup> rendering."""
    locations = Location.objects.order_by('site', 'name')
    choices = [('', '---------')]
    current_site = None
    group = []
    for loc in locations:
        if loc.site != current_site:
            if current_site is not None:
                choices.append((current_site, group))
            current_site = loc.site
            group = []
        group.append((loc.pk, loc.name))
    if current_site is not None:
        choices.append((current_site, group))
    return choices


class OwnerForm(forms.ModelForm):
    class Meta:
        model = Owner
        fields = ['name', 'email', 'phone', 'address', 'account_code', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'email': forms.EmailInput(attrs={'class': 'form-input'}),
            'phone': forms.TextInput(attrs={'class': 'form-input'}),
            'address': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
            'account_code': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Xero account code'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
        }


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ['name', 'site', 'usage', 'description', 'capacity']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'site': forms.TextInput(attrs={'class': 'form-input'}),
            'usage': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
            'capacity': forms.NumberInput(attrs={'class': 'form-input'}),
        }


class HorseForm(forms.ModelForm):
    class Meta:
        model = Horse
        fields = [
            'name', 'date_of_birth', 'age', 'sex', 'color',
            'dam', 'sire_name', 'breeding', 'photo',
            'notes', 'passport_number', 'has_passport', 'is_active'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'date_of_birth': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'age': forms.NumberInput(attrs={'class': 'form-input'}),
            'sex': forms.Select(attrs={'class': 'form-select'}),
            'color': forms.Select(attrs={'class': 'form-select'}),
            'dam': forms.Select(attrs={'class': 'form-select'}),
            'sire_name': forms.TextInput(attrs={'class': 'form-input'}),
            'breeding': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'photo': forms.ClearableFileInput(attrs={'class': 'form-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
            'passport_number': forms.TextInput(attrs={'class': 'form-input'}),
            'has_passport': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }


class PlacementForm(forms.ModelForm):
    class Meta:
        model = Placement
        fields = ['horse', 'owner', 'location', 'rate_type', 'start_date', 'end_date', 'notes']
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'owner': forms.Select(attrs={'class': 'form-select'}),
            'location': forms.Select(attrs={'class': 'form-select'}),
            'rate_type': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['location'].choices = get_grouped_location_choices()

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        if start_date and end_date and end_date < start_date:
            raise forms.ValidationError("End date cannot be before start date.")
        return cleaned_data

    def validate_unique(self):
        super().validate_unique()
        try:
            self.instance.clean()
        except forms.ValidationError:
            raise
        except Exception as e:
            raise forms.ValidationError(str(e))


class MoveHorseForm(forms.Form):
    """Form for moving a horse to a new location."""
    new_location = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['new_location'].choices = get_grouped_location_choices()

    new_owner = forms.ModelChoiceField(
        queryset=Owner.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Leave empty to keep current owner"
    )
    new_rate_type = forms.ModelChoiceField(
        queryset=RateType.objects.filter(is_active=True),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Leave empty to keep current rate"
    )
    move_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-input', 'type': 'date'})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2})
    )


class ArrivalForm(forms.Form):
    """Form for logging horse arrivals at a location (supports multiple horses)."""
    horses = forms.ModelMultipleChoiceField(
        queryset=Horse.objects.filter(is_active=True),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-checkbox rounded'}),
        help_text="Select horses to arrive at this location"
    )
    owner = forms.ModelChoiceField(
        queryset=Owner.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    rate_type = forms.ModelChoiceField(
        queryset=RateType.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    arrival_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-input', 'type': 'date'})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2})
    )


class SingleArrivalForm(forms.Form):
    """Form for logging a single horse arrival (from Horse Detail page)."""
    location = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['location'].choices = get_grouped_location_choices()

    owner = forms.ModelChoiceField(
        queryset=Owner.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    rate_type = forms.ModelChoiceField(
        queryset=RateType.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    arrival_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-input', 'type': 'date'})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2})
    )


class DepartureForm(forms.Form):
    """Form for logging horse departures."""
    departure_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-input', 'type': 'date'})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2})
    )


class OwnershipShareForm(forms.ModelForm):
    class Meta:
        model = OwnershipShare
        fields = ['owner', 'share_percentage', 'is_primary_contact', 'notes']
        widgets = {
            'owner': forms.Select(attrs={'class': 'form-select'}),
            'share_percentage': forms.NumberInput(attrs={
                'class': 'form-input', 'step': '0.01', 'min': '0.01', 'max': '100',
            }),
            'is_primary_contact': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 1}),
        }


OwnershipShareFormSet = forms.inlineformset_factory(
    Horse,
    OwnershipShare,
    form=OwnershipShareForm,
    extra=1,
    can_delete=True,
)


class RateTypeForm(forms.ModelForm):
    class Meta:
        model = RateType
        fields = ['name', 'daily_rate', 'description', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'daily_rate': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01'}),
            'description': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }


class BusinessSettingsForm(forms.ModelForm):
    class Meta:
        model = BusinessSettings
        fields = [
            'business_name', 'address', 'phone', 'email', 'website',
            'vat_registration', 'bank_details', 'card_payment_url',
            'default_payment_terms', 'invoice_prefix',
        ]
        widgets = {
            'business_name': forms.TextInput(attrs={'class': 'form-input'}),
            'address': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'phone': forms.TextInput(attrs={'class': 'form-input'}),
            'email': forms.EmailInput(attrs={'class': 'form-input'}),
            'website': forms.URLInput(attrs={'class': 'form-input'}),
            'vat_registration': forms.TextInput(attrs={'class': 'form-input'}),
            'bank_details': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
            'card_payment_url': forms.URLInput(attrs={'class': 'form-input'}),
            'default_payment_terms': forms.NumberInput(attrs={'class': 'form-input'}),
            'invoice_prefix': forms.TextInput(attrs={'class': 'form-input', 'maxlength': 10}),
        }
