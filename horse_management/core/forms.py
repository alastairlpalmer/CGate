"""
Forms for core app.
"""

from decimal import Decimal

from django import forms
from django.db.models import Q

from .images import heic_to_jpeg
from .models import BusinessSettings, Document, Horse, HorsePhoto, Location, Owner, OwnershipShare, Placement, RateType


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
            'name': forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'name'}),
            'email': forms.EmailInput(attrs={'class': 'form-input', 'autocomplete': 'email'}),
            'phone': forms.TextInput(attrs={'class': 'form-input', 'type': 'tel', 'autocomplete': 'tel'}),
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
            'capacity': forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
        }


class LocationUsageForm(forms.Form):
    """Log a usage change for a field, optionally backdated to an effective date."""

    usage = forms.ChoiceField(
        choices=Location.Usage.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    change_date = forms.DateField(
        label="Effective date",
        input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(
            format='%Y-%m-%d',
            attrs={'class': 'form-input', 'type': 'date'},
        ),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
    )


class HorseForm(forms.ModelForm):
    # Override date_of_birth to disable localization so HTML5 date input gets YYYY-MM-DD
    date_of_birth = forms.DateField(
        required=False,
        input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
    )

    class Meta:
        model = Horse
        fields = [
            'name', 'date_of_birth', 'age', 'sex', 'color',
            'dam_name', 'sire_name', 'breeding', 'photo',
            'notes', 'passport_number', 'has_passport', 'is_active'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'age': forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
            'sex': forms.Select(attrs={'class': 'form-select'}),
            'color': forms.Select(attrs={'class': 'form-select'}),
            'dam_name': forms.TextInput(attrs={'class': 'form-input'}),
            'sire_name': forms.TextInput(attrs={'class': 'form-input'}),
            'breeding': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'photo': forms.ClearableFileInput(attrs={'class': 'form-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
            'passport_number': forms.TextInput(attrs={'class': 'form-input'}),
            'has_passport': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }

    def clean_photo(self):
        return heic_to_jpeg(self.cleaned_data.get('photo'))

    def clean(self):
        cleaned_data = super().clean()
        # Unticking Active while the horse still occupies a field would strand
        # the record: it shows as Departed in lists and search, yet the record
        # page only offers Move/Depart (no Log Arrival) because the placement
        # is still open. Departures must go through the Depart flow, which
        # closes the placement too. Horses already in the stranded state are
        # left editable so other fields can still be corrected.
        if self.instance.pk and not cleaned_data.get('is_active'):
            was_active = Horse.objects.filter(
                pk=self.instance.pk, is_active=True
            ).exists()
            has_open_placement = self.instance.placements.filter(
                end_date__isnull=True
            ).exists()
            if was_active and has_open_placement:
                self.add_error(
                    'is_active',
                    "This horse still has an open placement. Use the Depart "
                    "button on the horse's page to log the departure instead "
                    "of unticking Active.",
                )
        return cleaned_data


class PlacementForm(forms.ModelForm):
    class Meta:
        model = Placement
        fields = ['horse', 'owner', 'location', 'rate_type', 'start_date', 'end_date', 'expected_departure', 'notes']
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'owner': forms.Select(attrs={'class': 'form-select'}),
            'location': forms.Select(attrs={'class': 'form-select'}),
            'rate_type': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'end_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'expected_departure': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
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

    # Note: no validate_unique override. Django's ModelForm._post_clean
    # already runs Placement.clean() (overlap validation) via
    # instance.full_clean and records failures as form errors; re-raising
    # here instead turned any overlapping dates into a 500.


class MoveHorseForm(forms.Form):
    """Form for moving a horse to a new location."""
    new_location = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['new_location'].choices = get_grouped_location_choices()
        self.fields['new_owner'].queryset = Owner.objects.all()
        self.fields['new_rate_type'].queryset = RateType.objects.filter(is_active=True)

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
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'})
    )
    expected_departure = forms.DateField(
        required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
        help_text="When do you expect this horse to leave?"
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
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'})
    )
    expected_departure = forms.DateField(
        required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
        help_text="When are these horses expected to leave?"
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['owner'].queryset = Owner.objects.all()
        self.fields['rate_type'].queryset = RateType.objects.filter(is_active=True)


class SingleArrivalForm(forms.Form):
    """Form for logging a single horse arrival (from Horse Detail page)."""
    location = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['location'].choices = get_grouped_location_choices()
        self.fields['owner'].queryset = Owner.objects.all()
        self.fields['rate_type'].queryset = RateType.objects.filter(is_active=True)

    owner = forms.ModelChoiceField(
        queryset=Owner.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    rate_type = forms.ModelChoiceField(
        queryset=RateType.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    arrival_date = forms.DateField(
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'})
    )
    expected_departure = forms.DateField(
        required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
        help_text="When do you expect this horse to leave?"
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2})
    )


class NewArrivalForm(forms.Form):
    """Combined form: create a new horse and place it in one step."""
    # Horse fields
    name = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Horse name'})
    )
    sex = forms.ChoiceField(
        choices=[('', '---------')],
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    color = forms.ChoiceField(
        choices=[('', '---------')],
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'})
    )
    sire_name = forms.CharField(
        max_length=200, required=False,
        widget=forms.TextInput(attrs={'class': 'form-input'})
    )
    passport_number = forms.CharField(
        max_length=50, required=False,
        widget=forms.TextInput(attrs={'class': 'form-input'})
    )
    has_passport = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'})
    )

    # Owner
    owner = forms.ModelChoiceField(
        queryset=Owner.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    # Placement fields
    location = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    rate_type = forms.ModelChoiceField(
        queryset=RateType.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    arrival_date = forms.DateField(
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'})
    )
    expected_departure = forms.DateField(
        required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
        help_text="When is this horse expected to leave?"
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sex'].choices = [('', '---------')] + list(Horse._meta.get_field('sex').choices)
        self.fields['color'].choices = [('', '---------')] + list(Horse._meta.get_field('color').choices)
        self.fields['owner'].queryset = Owner.objects.all()
        self.fields['location'].choices = get_grouped_location_choices()
        self.fields['rate_type'].queryset = RateType.objects.filter(is_active=True)


class DepartureForm(forms.Form):
    """Form for logging horse departures."""
    departure_date = forms.DateField(
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'})
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
                'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal', 'min': '0.01', 'max': '100',
            }),
            'is_primary_contact': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 1}),
        }


class BaseOwnershipShareFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        total = Decimal('0')
        share_count = 0
        for form in self.forms:
            if form.cleaned_data and not form.cleaned_data.get('DELETE', False):
                pct = form.cleaned_data.get('share_percentage')
                if pct is not None:
                    total += pct
                    share_count += 1
        # A horse with no ownership shares is allowed — livery is then billed to
        # the placement owner. But once any share exists the shares must total
        # exactly 100%, otherwise the unallocated remainder would go unbilled.
        if share_count and total != Decimal('100.00'):
            raise forms.ValidationError(
                f"Total ownership must be exactly 100%. It is currently {total}%."
            )


OwnershipShareFormSet = forms.inlineformset_factory(
    Horse,
    OwnershipShare,
    form=OwnershipShareForm,
    formset=BaseOwnershipShareFormSet,
    extra=1,
    can_delete=True,
)


class RateTypeForm(forms.ModelForm):
    class Meta:
        model = RateType
        fields = ['name', 'daily_rate', 'description', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'daily_rate': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
            'description': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }


class BusinessSettingsForm(forms.ModelForm):
    class Meta:
        model = BusinessSettings
        fields = [
            'business_name', 'address', 'phone', 'email', 'website',
            'vat_registration', 'vat_rate', 'bank_details', 'card_payment_url',
            'default_payment_terms', 'invoice_prefix', 'auto_generate_invoices',
        ]
        widgets = {
            'business_name': forms.TextInput(attrs={'class': 'form-input'}),
            'address': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'phone': forms.TextInput(attrs={'class': 'form-input', 'type': 'tel', 'autocomplete': 'tel'}),
            'email': forms.EmailInput(attrs={'class': 'form-input'}),
            'website': forms.URLInput(attrs={'class': 'form-input'}),
            'vat_registration': forms.TextInput(attrs={'class': 'form-input'}),
            'bank_details': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
            'card_payment_url': forms.URLInput(attrs={'class': 'form-input'}),
            'default_payment_terms': forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
            'invoice_prefix': forms.TextInput(attrs={'class': 'form-input', 'maxlength': 10}),
            'auto_generate_invoices': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'vat_rate': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
        }

    def clean_vat_rate(self):
        rate = self.cleaned_data['vat_rate']
        # The Xero export/push maps any non-zero rate to the UK standard-rate
        # tax code, so only 0 or 20 keep the books consistent end to end.
        if rate not in (Decimal('0'), Decimal('0.00'), Decimal('20'), Decimal('20.00')):
            raise forms.ValidationError(
                "VAT rate must be 0 (not registered) or 20 (UK standard rate) "
                "— other rates would disagree with the Xero tax code."
            )
        return rate


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ['doc_type', 'title', 'file', 'expiry_date', 'notes']
        widgets = {
            'doc_type': forms.Select(attrs={'class': 'form-select'}),
            'title': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Passport — Weatherbys'}),
            'file': forms.ClearableFileInput(attrs={'class': 'form-input', 'accept': 'image/*,.pdf,.doc,.docx'}),
            'expiry_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """FileField that cleans a list of uploads (the Django ≥4.2 recipe)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)]


# Quick-add offers passport alongside the HorsePhoto categories; the view
# routes passport uploads to Document so expiry reminders keep working.
QUICK_PHOTO_PASSPORT = 'passport'
QUICK_PHOTO_CATEGORY_CHOICES = list(HorsePhoto.Category.choices) + [
    (QUICK_PHOTO_PASSPORT, 'Passport'),
]


class QuickPhotoForm(forms.Form):
    """Minimal camera-first upload form: category chips + photos + note.

    ``images`` carries no validators — each file is normalised and validated
    individually in the view so one bad file doesn't abort the batch.
    """

    category = forms.ChoiceField(
        choices=QUICK_PHOTO_CATEGORY_CHOICES,
        initial=HorsePhoto.Category.CONDITION,
        widget=forms.RadioSelect,
    )
    images = MultipleFileField(required=True)
    caption = forms.CharField(
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Optional note (applies to all photos)',
        }),
    )


# ──────────────────────────────────────────────────────────────────────────
# User management (Settings → Users & Roles)
# ──────────────────────────────────────────────────────────────────────────


class UserAccountForm(forms.Form):
    """Shared fields for creating/editing a login account.

    The email address doubles as the sign-in identifier, so it must be
    unique across both the email and username columns (legacy accounts may
    have a plain username).
    """

    first_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'given-name'}),
    )
    last_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'family-name'}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'form-input', 'autocomplete': 'email'}),
    )
    role = forms.ModelChoiceField(
        queryset=None,
        empty_label=None,
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="The role controls which areas this user can see and change "
                  "(Settings → Users & Roles).",
    )

    def __init__(self, *args, instance=None, **kwargs):
        from .models import Role
        self.instance = instance
        super().__init__(*args, **kwargs)
        self.fields['role'].queryset = Role.objects.order_by('-is_system', 'name')

    def clean_email(self):
        from django.contrib.auth import get_user_model
        email = self.cleaned_data['email'].strip().lower()
        clashes = get_user_model().objects.filter(
            Q(email__iexact=email) | Q(username__iexact=email)
        )
        if self.instance is not None:
            clashes = clashes.exclude(pk=self.instance.pk)
        if clashes.exists():
            raise forms.ValidationError("A user with this email address already exists.")
        return email


class UserCreateForm(UserAccountForm):
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'autocomplete': 'new-password'}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'autocomplete': 'new-password'}),
    )

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', "Passwords don't match.")
        elif p1:
            from django.contrib.auth import get_user_model, password_validation
            probe = get_user_model()(
                username=cleaned.get('email', ''),
                email=cleaned.get('email', ''),
                first_name=cleaned.get('first_name', ''),
                last_name=cleaned.get('last_name', ''),
            )
            try:
                password_validation.validate_password(p1, user=probe)
            except forms.ValidationError as e:
                self.add_error('password1', e)
        return cleaned

    def save(self):
        from django.contrib.auth import get_user_model
        from .models import UserRole
        data = self.cleaned_data
        user = get_user_model().objects.create_user(
            username=data['email'],
            email=data['email'],
            password=data['password1'],
            first_name=data['first_name'],
            last_name=data['last_name'],
        )
        UserRole.objects.create(user=user, role=data['role'])
        return user


class UserUpdateForm(UserAccountForm):
    def save(self):
        data = self.cleaned_data
        user = self.instance
        # Keep the sign-in identifier in step when the username is the email
        # (accounts created here). Legacy usernames — including email-less
        # accounts like the createsuperuser admin — are left untouched, so
        # their existing login name keeps working; they can sign in with the
        # new email as well via EmailOrUsernameBackend.
        if user.email and user.username.lower() == user.email.lower():
            user.username = data['email']
        user.first_name = data['first_name']
        user.last_name = data['last_name']
        user.email = data['email']
        user.save()
        from .models import UserRole
        UserRole.objects.update_or_create(user=user, defaults={'role': data['role']})
        return user


class AdminSetPasswordForm(forms.Form):
    """Set a new password for another user (no old password required)."""

    password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'autocomplete': 'new-password'}),
    )
    password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'autocomplete': 'new-password'}),
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', "Passwords don't match.")
        elif p1:
            from django.contrib.auth import password_validation
            try:
                password_validation.validate_password(p1, user=self.user)
            except forms.ValidationError as e:
                self.add_error('password1', e)
        return cleaned

    def save(self):
        self.user.set_password(self.cleaned_data['password1'])
        self.user.save(update_fields=['password'])
        return self.user


# ──────────────────────────────────────────────────────────────────────────
# Role Suite (Settings → Users & Roles)
# ──────────────────────────────────────────────────────────────────────────

class RoleForm(forms.Form):
    """Create/edit a role: name, description, and the per-feature matrix.

    One ChoiceField per registry feature, built dynamically so new features
    appear in the editor without touching this form. System roles keep an
    editable name/description but a locked (disabled, ignored) matrix.
    """

    name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-input'}),
    )
    description = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input'}),
        help_text="Shown in the role list — e.g. who this role is for.",
    )

    def __init__(self, *args, instance=None, **kwargs):
        from .features import FEATURES, LEVEL_LABELS, LEVELS
        self.instance = instance
        super().__init__(*args, **kwargs)

        locked = bool(instance and instance.is_system)
        current = instance.resolved_access() if instance else {}
        for feature in FEATURES:
            key = feature['key']
            levels = LEVELS if feature['supports_view'] else ('hidden', 'full')
            self.fields[f'access_{key}'] = forms.ChoiceField(
                choices=[(lv, LEVEL_LABELS[lv]) for lv in levels],
                initial=current.get(key, 'hidden'),
                disabled=locked,
                required=not locked,
                widget=forms.RadioSelect,
            )

    def clean_name(self):
        from .models import Role
        name = self.cleaned_data['name'].strip()
        clashes = Role.objects.filter(name__iexact=name)
        if self.instance:
            clashes = clashes.exclude(pk=self.instance.pk)
        if clashes.exists():
            raise forms.ValidationError("A role with this name already exists.")
        return name

    def access_value(self):
        """The access map this form describes (empty for locked system roles)."""
        from .features import FEATURES
        if self.instance and self.instance.is_system:
            return self.instance.access
        return {
            f['key']: self.cleaned_data[f"access_{f['key']}"]
            for f in FEATURES
        }

    def grouped_fields(self):
        """[(group, [(feature_dict, bound_field), ...]), ...] for the template."""
        from .features import FEATURES, GROUPS
        out = []
        for group in GROUPS:
            rows = [
                (f, self[f"access_{f['key']}"])
                for f in FEATURES if f['group'] == group
            ]
            if rows:
                out.append((group, rows))
        return out

    def save(self):
        from .models import Role
        data = self.cleaned_data
        if self.instance:
            self.instance.name = data['name']
            self.instance.description = data['description']
            if not self.instance.is_system:
                self.instance.access = self.access_value()
            self.instance.save()
            return self.instance
        return Role.objects.create(
            name=data['name'],
            description=data['description'],
            access=self.access_value(),
        )
