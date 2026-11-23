"""
Forms for core app.
"""

from datetime import date
from decimal import Decimal

from django import forms
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import FileExtensionValidator
from django.db.models import Q

from .geo import coords_from_link, parse_coords_text
from .images import heic_to_jpeg
from .widgets import MoneyInput
from .models import (
    BusinessSettings, CalendarEntry, Document, Horse, HorseIdentifier, HorsePhoto, Location,
    Owner, OwnershipShare, Placement, RateType, SiteSettings, Stable, validate_coordinate_pair,
)


def get_grouped_location_choices(include_pk=None):
    """Build location choices grouped by site for <optgroup> rendering.

    Archived fields are left out — they are retired, so nothing new should
    be placed on them. ``include_pk`` keeps one archived field in the list:
    the one a past stay was on, so that stay can still be edited.
    """
    from django.db.models import Q

    wanted = Q(pk__in=Location.objects.active().values('pk'))
    if include_pk:
        wanted |= Q(pk=include_pk)
    locations = Location.objects.filter(wanted).order_by('site', 'name')
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
        fields = ['name', 'email', 'phone', 'address', 'account_code', 'invoice_reference',
                  'invoice_per_horse', 'pause_reminders', 'email_health_reminders', 'notes']
        widgets = {
            'invoice_reference': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. PO-4471'}),
            'name': forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'name'}),
            'email': forms.EmailInput(attrs={'class': 'form-input', 'autocomplete': 'email'}),
            'phone': forms.TextInput(attrs={'class': 'form-input', 'type': 'tel', 'autocomplete': 'tel'}),
            'address': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
            'account_code': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Xero account code'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
        }

    def save(self, commit=True):
        owner = super().save(commit=False)
        if 'email' in self.changed_data:
            # A new address gets a fresh start: the bounce was the old one's.
            owner.email_bounced_at = None
            owner.email_bounce_reason = ''
        if commit:
            owner.save()
        return owner


def owners_for_picker(current_pk=None):
    """Owners a picker offers: the ones in use, plus the record's own owner
    when that owner has since been archived, so an old stay or share can
    still be edited without its owner vanishing from the list."""
    owners = Owner.objects.active()
    if current_pk:
        owners = Owner.objects.filter(Q(is_archived=False) | Q(pk=current_pk))
    return owners.order_by('name')


class SitePickerWidget(forms.Select):
    """Drop-down of the sites already in use, plus an "Add a new site…" row.

    Picking the new-site row reveals a text box (``<name>_new``) whose value
    becomes the field's value. A plain ``site=<text>`` submission still
    works, so nothing that posts the old text field breaks.
    """

    template_name = 'core/widgets/site_picker.html'
    NEW = '__new__'
    NEW_LABEL = 'Add a new site…'

    def __init__(self, attrs=None):
        super().__init__(attrs)
        self._new_value = ''
        self._picked_new = False

    def value_from_datadict(self, data, files, name):
        value = data.get(name)
        if value == self.NEW:
            self._picked_new = True
            self._new_value = (data.get(f'{name}_new') or '').strip()
            return self._new_value
        return value

    def get_context(self, name, value, attrs):
        selected = value or ''
        known = {str(v) for v, _ in self.choices}
        if selected and selected not in known:
            # Keep a freshly typed name on the form after a validation error
            # instead of dropping it back to the placeholder row.
            self._new_value = selected
            selected = self.NEW
        elif not selected and self._picked_new:
            # "Add a new site…" chosen but left blank: keep the box open so
            # the required-field error lands next to it.
            selected = self.NEW
        context = super().get_context(name, selected, attrs)
        context['widget']['selected'] = selected
        context['widget']['new_value'] = self._new_value
        context['widget']['new_option'] = self.NEW
        return context


def get_site_choices(include=None):
    """Every distinct site name, archived fields included, A–Z.

    ``include`` adds a name that must appear even if no field carries it
    yet (the instance being edited, for example).
    """
    names = set(Location.objects.values_list('site', flat=True).distinct())
    if include:
        names.add(include)
    names.discard('')
    choices = [('', '---------')]
    choices += [(n, n) for n in sorted(names, key=str.lower)]
    choices.append((SitePickerWidget.NEW, SitePickerWidget.NEW_LABEL))
    return choices


class CoordinateFieldsMixin(forms.Form):
    """Three ways to enter one latitude/longitude pair.

    ``latitude`` and ``longitude`` are the model fields, rendered hidden;
    the coordinate picker (templates/locations/_coord_picker.html) writes
    to them from a dragged pin. ``coords_text`` ("52.1, -1.2") and
    ``maps_link`` (a Google Maps URL) are parsed here, server-side, and
    win over the hidden pair when filled in. Every route ends in
    ``validate_coordinate_pair``.

    Mixed into a ModelForm whose model has the two coordinate columns.
    """

    # These are the names the picker's inputs use.
    COORDINATE_FIELD_NAMES = ('coords_text', 'maps_link')

    coords_text = forms.CharField(
        required=False, label='Coordinates',
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'placeholder': '51.5074, -0.1278',
            'inputmode': 'decimal', 'autocomplete': 'off',
        }),
    )
    maps_link = forms.CharField(
        required=False, label='Google Maps link',
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'placeholder': 'https://maps.app.goo.gl/…',
            'inputmode': 'url', 'autocomplete': 'off',
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('latitude', 'longitude'):
            self.fields[name].widget = forms.HiddenInput()
            self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()
        lat, lng = cleaned.get('latitude'), cleaned.get('longitude')
        link = (cleaned.get('maps_link') or '').strip()
        text = (cleaned.get('coords_text') or '').strip()

        if link:
            try:
                coords, _url = coords_from_link(link, timeout=5)
            except Exception:
                coords = False
            if coords is False:
                self.add_error(
                    'maps_link',
                    "That link couldn't be opened. Check your signal, or "
                    "paste the coordinates instead.",
                )
            elif coords is None:
                self.add_error(
                    'maps_link',
                    "No coordinates in that link. Open it in Google Maps and "
                    "copy the two numbers after the @ in the address bar.",
                )
            else:
                lat, lng = coords
        elif text:
            pair = parse_coords_text(text)
            if pair is None:
                self.add_error(
                    'coords_text',
                    'Enter the latitude then the longitude, e.g. 51.5074, -0.1278.',
                )
            else:
                lat, lng = pair

        try:
            validate_coordinate_pair(lat, lng)
        except DjangoValidationError as exc:
            # The pair fields are hidden, so their errors would surface as
            # "(Hidden field latitude)…". Put them on the visible box, and
            # drop the pair from cleaned_data so the model keeps its saved
            # values and does not repeat the error from its own clean().
            for messages in exc.message_dict.values():
                for message in messages:
                    self.add_error('coords_text', message)
            cleaned.pop('latitude', None)
            cleaned.pop('longitude', None)
            return cleaned

        cleaned['latitude'] = lat
        cleaned['longitude'] = lng
        return cleaned


class LocationForm(CoordinateFieldsMixin, forms.ModelForm):
    pin_to_dashboard = forms.BooleanField(
        required=False, label='Pin to dashboard',
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        help_text='The Near you card highlights this location when your phone '
                  'has no position.',
    )

    class Meta:
        model = Location
        fields = ['name', 'site', 'usage', 'description', 'capacity', 'latitude', 'longitude']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'site': SitePickerWidget(attrs={'class': 'form-select'}),
            'usage': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
            'capacity': forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        site = self.fields['site']
        current = self.instance.site if self.instance and self.instance.pk else None
        site.widget.choices = get_site_choices(include=current)
        site.help_text = 'Pick a site, or choose "Add a new site…" to type one in.'
        # Pinning is a per-user dashboard preference, not a location
        # attribute, so it only makes sense with a user and a saved row.
        if user is None or not (self.instance and self.instance.pk):
            del self.fields['pin_to_dashboard']
        elif not self.is_bound:
            from .models import DashboardPreference
            pref = DashboardPreference.objects.filter(user=user).first()
            self.fields['pin_to_dashboard'].initial = bool(
                pref and pref.pinned_location_id == self.instance.pk
            )

    def save(self, commit=True):
        location = super().save(commit=commit)
        if commit and 'pin_to_dashboard' in self.fields:
            self.save_pin(location)
        return location

    def save_pin(self, location):
        from .models import DashboardPreference
        pref = DashboardPreference.get_for(self.user)
        pinned = self.cleaned_data.get('pin_to_dashboard')
        if pinned and pref.pinned_location_id != location.pk:
            pref.pinned_location = location
            pref.save(update_fields=['pinned_location', 'updated_at'])
        elif not pinned and pref.pinned_location_id == location.pk:
            pref.pinned_location = None
            pref.save(update_fields=['pinned_location', 'updated_at'])


class BarnForm(LocationForm):
    """Add or edit a barn: a location with a layout and, optionally, stables.

    A form of its own rather than extra fields on ``LocationForm``. The
    pop-up sheet lays out every visible field of whatever form it is
    given, so one form for both would show a layout to somebody adding a
    paddock and a land use to somebody adding a barn.

    The stables are made here, with the barn, in one go. After that they
    are managed from the barn's own page.
    """

    stable_count = forms.IntegerField(
        label='Number of stables', required=False, min_value=0, initial=20,
        widget=forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
        help_text='Leave at 0 to add the stables later.',
    )
    first_number = forms.IntegerField(
        label='First stable number', required=False, min_value=0, initial=1,
        widget=forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
    )

    field_order = [
        'name', 'site', 'layout', 'stable_count', 'first_number', 'capacity',
        'description',
    ]

    class Meta(LocationForm.Meta):
        fields = ['name', 'site', 'layout', 'description', 'capacity', 'latitude', 'longitude']
        widgets = {
            **LocationForm.Meta.widgets,
            'layout': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .stables import MAX_STABLES

        # Set here, not in save(): the model's own clean() runs while the
        # form validates, and it refuses a layout on anything but a barn.
        self.instance.location_type = Location.LocationType.BARN
        self.instance.usage = Location.Usage.HORSES

        layout = self.fields['layout']
        layout.required = True
        layout.choices = [('', '---------')] + list(Location.Layout.choices)
        layout.help_text = (
            'Courtyard: stables round three sides of a yard. Centre aisle: '
            'two lines that face each other. Row: one line.'
        )
        self.fields['stable_count'].max_value = MAX_STABLES
        self.fields['stable_count'].widget.attrs['max'] = MAX_STABLES

        capacity = self.fields['capacity']
        editing = bool(self.instance and self.instance.pk)
        if editing:
            # Stables are added and removed on the barn's page from here on.
            del self.fields['stable_count']
            del self.fields['first_number']
            if self.instance.stables.filter(is_gap=False).exists():
                # The number of stables in use, written by
                # core.stables.sync_barn_capacity — never typed.
                del self.fields['capacity']
            else:
                capacity.help_text = 'How many horses the barn holds. Set by its stables once it has some.'
        else:
            capacity.help_text = 'Only for a barn with no stables. Otherwise the stables set it.'

    def clean_stable_count(self):
        from .stables import MAX_STABLES
        count = self.cleaned_data.get('stable_count') or 0
        if count > MAX_STABLES:
            raise forms.ValidationError(f'A barn can have up to {MAX_STABLES} stables.')
        return count

    def save(self, commit=True):
        from django.db import transaction

        from .stables import add_stables

        creating = not (self.instance and self.instance.pk)
        with transaction.atomic():
            barn = super().save(commit=commit)
            count = self.cleaned_data.get('stable_count') or 0
            if commit and creating and count:
                first = self.cleaned_data.get('first_number')
                add_stables(barn, count, 1 if first is None else first)
        return barn


class StableForm(forms.ModelForm):
    """Rename a stable, leave a note on it, or take it out of use."""

    class Meta:
        model = Stable
        fields = ['label', 'in_use', 'note']
        labels = {'label': 'Name or number', 'in_use': 'In use'}
        widgets = {
            'label': forms.TextInput(attrs={'class': 'form-input'}),
            'in_use': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'note': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Door repair'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.is_gap:
            # A gap is not a stable, so it is never "out of use".
            del self.fields['in_use']

    def clean_label(self):
        label = (self.cleaned_data.get('label') or '').strip()
        clash = Stable.objects.filter(
            location=self.instance.location, label__iexact=label,
        ).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(f'This barn already has a "{label}".')
        return label

    def clean_in_use(self):
        in_use = self.cleaned_data.get('in_use')
        if not in_use:
            from .stables import _join_names, occupants_of
            horses = occupants_of(self.instance)
            if horses:
                names = _join_names([horse.name for horse in horses])
                raise forms.ValidationError(
                    f'{names} {"is" if len(horses) == 1 else "are"} in this stable. '
                    f'Move {"it" if len(horses) == 1 else "them"} out first.'
                )
        return in_use


class AddStablesForm(forms.Form):
    """Add more stables to the end of a barn."""

    count = forms.IntegerField(
        label='How many stables to add', min_value=1, initial=1,
        widget=forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
        help_text='They are numbered on from the highest number in the barn.',
    )

    def __init__(self, *args, barn, **kwargs):
        super().__init__(*args, **kwargs)
        self.barn = barn

    def clean_count(self):
        from .stables import MAX_STABLES
        count = self.cleaned_data['count']
        have = self.barn.stables.count()
        if have + count > MAX_STABLES:
            room = max(MAX_STABLES - have, 0)
            raise forms.ValidationError(
                f'A barn can have up to {MAX_STABLES} stables. This one has room for {room} more.'
            )
        return count


class StableGapForm(forms.Form):
    """Put something that is not a stable into the barn's order."""

    label = forms.CharField(
        label='What is it', max_length=20,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Tack room'}),
    )
    after = forms.ModelChoiceField(
        label='Where', queryset=Stable.objects.none(), required=False,
        empty_label='At the start',
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='The stables after it each move one place on.',
    )

    def __init__(self, *args, barn, **kwargs):
        super().__init__(*args, **kwargs)
        self.barn = barn
        after = self.fields['after']
        after.queryset = barn.stables.order_by('position', 'pk')
        after.label_from_instance = lambda stable: f'After {stable.label}'
        if not self.is_bound:
            after.initial = after.queryset.last()

    def clean_label(self):
        label = self.cleaned_data['label'].strip()
        if self.barn.stables.filter(label__iexact=label).exists():
            raise forms.ValidationError(f'This barn already has a "{label}".')
        return label


class ConvertToBarnForm(forms.Form):
    """Turn a location that was added as ground into a barn."""

    layout = forms.ChoiceField(
        choices=Location.Layout.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=(
            'Courtyard: stables round three sides of a yard. Centre aisle: '
            'two lines that face each other. Row: one line.'
        ),
    )
    stable_count = forms.IntegerField(
        label='Number of stables', min_value=0, initial=0, required=False,
        widget=forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
        help_text='Leave at 0 to add the stables later.',
    )
    first_number = forms.IntegerField(
        label='First stable number', min_value=0, initial=1, required=False,
        widget=forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
    )

    def clean_stable_count(self):
        from .stables import MAX_STABLES
        count = self.cleaned_data.get('stable_count') or 0
        if count > MAX_STABLES:
            raise forms.ValidationError(f'A barn can have up to {MAX_STABLES} stables.')
        return count


class SiteSettingsForm(CoordinateFieldsMixin, forms.ModelForm):
    """The site centre and its "on this site" radius (Edit site)."""

    class Meta:
        model = SiteSettings
        fields = ['latitude', 'longitude', 'radius_m']
        widgets = {
            'radius_m': forms.NumberInput(attrs={
                'class': 'form-input', 'inputmode': 'numeric', 'min': 50, 'step': 50,
            }),
        }
        labels = {'radius_m': 'Site radius (metres)'}
        help_texts = {
            'radius_m': 'Within this distance of the centre, a phone counts as on this site.',
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

    def clean_change_date(self):
        day = self.cleaned_data['change_date']
        if day > timezone.localdate():
            raise forms.ValidationError("The date can't be in the future. Record the change on the day.")
        return day



# A chip scanner that types like a keyboard ends with Enter. In the
# microchip box that must not submit the form half filled.
# A chip is digits, so a phone shows the number pad (UX review D1); a
# scanner still types into the box as before.
MICROCHIP_INPUT_ATTRS = {
    'class': 'form-input font-mono', 'autocomplete': 'off', 'spellcheck': 'false',
    'inputmode': 'numeric', 'x-data': '', '@keydown.enter.prevent': '',
}


def live_identity_check(form, *names):
    """Check the microchip and UELN boxes while they are typed (UX review
    D1): htmx asks core.views.horses.horse_identity_check and puts the
    answer in the next ``.id-check`` line (includes/form_field.html with
    check=True). The horse being edited is left out of "Also on …"."""
    import json

    from django.urls import reverse

    for name in names:
        if name not in form.fields:
            continue
        attrs = form.fields[name].widget.attrs
        attrs.update({
            'hx-get': reverse('horse_identity_check'),
            'hx-trigger': 'input changed delay:500ms, blur',
            'hx-target': 'next .id-check', 'hx-swap': 'innerHTML',
            'hx-select': 'unset', 'hx-push-url': 'false', 'hx-sync': 'this:replace',
        })
        if getattr(form.instance, 'pk', None):
            attrs['hx-vals'] = json.dumps({'horse': form.instance.pk})


class HorseForm(forms.ModelForm):
    """The horse's details, with the identity rules of core/identity.py.

    The foaling date is a full date, or a year (possibly a guess). Both
    end up in ``date_of_birth`` with ``dob_precision`` saying which.
    """

    # Override date_of_birth to disable localization so HTML5 date input gets YYYY-MM-DD
    # help_text, not only the template's help=, so the box is linked to its
    # help line with aria-describedby (UX review C10).
    date_of_birth = forms.DateField(
        required=False, label='Foaling date', help_text='Age is worked out from this',
        input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
    )
    foaled_year = forms.IntegerField(
        required=False, label='Or the year only', min_value=1950,
        help_text='Use this if the exact date is not known',
        widget=forms.NumberInput(attrs={
            'class': 'form-input', 'inputmode': 'numeric', 'placeholder': 'For example 2011',
        }),
    )
    foaled_year_estimated = forms.BooleanField(
        required=False, label='The year is a guess',
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )
    height = forms.CharField(
        required=False, label='Height (hands)', help_text='Hands and inches, for example 16.2',
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'inputmode': 'decimal', 'placeholder': 'For example 16.2',
        }),
    )
    # Longer than the stored 15 characters, so a UELN typed with spaces
    # or dashes reaches clean_ueln, which tidies it and then checks 15.
    ueln = forms.CharField(
        required=False, max_length=30, label='UELN', help_text='15 characters, from the passport',
        widget=forms.TextInput(attrs={
            'class': 'form-input font-mono uppercase', 'autocomplete': 'off', 'spellcheck': 'false',
            'autocapitalize': 'characters',
        }),
    )

    class Meta:
        model = Horse
        fields = [
            'name', 'registered_name', 'date_of_birth', 'sex', 'color',
            'country_of_birth', 'breed', 'markings', 'breeder',
            'dam_name', 'sire_name', 'breeding', 'photo',
            'notes', 'passport_number', 'passport_issuer', 'has_passport',
            'microchip', 'ueln', 'is_active'
        ]
        labels = {
            'name': 'Yard name',
            'registered_name': 'Registered name',
            'country_of_birth': 'Country (suffix)',
            'ueln': 'UELN',
            # The free text was called "Breeding", like the mare's
            # Breeding card (UX review A3).
            'breeding': 'Lineage notes',
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'registered_name': forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'off'}),
            'country_of_birth': forms.TextInput(attrs={
                'class': 'form-input uppercase', 'list': 'country-suggestions',
                'autocomplete': 'off', 'maxlength': '5', 'autocapitalize': 'characters',
            }),
            'breed': forms.TextInput(attrs={'class': 'form-input', 'list': 'breed-suggestions'}),
            'markings': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'breeder': forms.TextInput(attrs={'class': 'form-input'}),
            'sex': forms.Select(attrs={'class': 'form-select'}),
            'color': forms.Select(attrs={'class': 'form-select'}),
            'dam_name': forms.TextInput(attrs={'class': 'form-input'}),
            'sire_name': forms.TextInput(attrs={'class': 'form-input'}),
            'breeding': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'photo': forms.ClearableFileInput(attrs={'class': 'form-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
            'passport_number': forms.TextInput(attrs={'class': 'form-input'}),
            'passport_issuer': forms.TextInput(attrs={'class': 'form-input', 'list': 'issuer-suggestions'}),
            'has_passport': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'microchip': forms.TextInput(attrs=MICROCHIP_INPUT_ATTRS),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        horse = self.instance
        if horse.pk and horse.date_of_birth and not horse.dob_is_exact:
            # A year-only date shows as a year, not as 1 January.
            self.initial['date_of_birth'] = None
            self.initial['foaled_year'] = horse.date_of_birth.year
            self.initial['foaled_year_estimated'] = (
                horse.dob_precision == Horse.DobPrecision.ESTIMATED)
        if horse.height_inches:
            self.initial['height'] = horse.height_display.removesuffix(' hh')
        live_identity_check(self, 'microchip', 'ueln')

    def clean_photo(self):
        return heic_to_jpeg(self.cleaned_data.get('photo'))

    def clean_microchip(self):
        from .identity import normalise_microchip
        return normalise_microchip(self.cleaned_data.get('microchip'))

    def clean_ueln(self):
        from .identity import normalise_ueln, validate_ueln
        ueln = normalise_ueln(self.cleaned_data.get('ueln'))
        validate_ueln(ueln)
        if len(ueln) > Horse._meta.get_field('ueln').max_length:
            raise forms.ValidationError('A UELN has at most 15 characters.')
        return ueln

    def clean_country_of_birth(self):
        from .identity import normalise_country
        return normalise_country(self.cleaned_data.get('country_of_birth'))

    def clean_height(self):
        from .identity import parse_height
        return parse_height(self.cleaned_data.get('height'))

    def clean(self):
        from .identity import normalise_country, split_suffix
        cleaned = super().clean()

        # "Karma Avicii (GB)" typed as the registered name: keep the name,
        # and use the suffix as the country when none was typed.
        if 'registered_name' in self.fields and cleaned.get('registered_name'):
            name, suffix = split_suffix(cleaned['registered_name'])
            cleaned['registered_name'] = name
            if suffix and not cleaned.get('country_of_birth') and 'country_of_birth' in self.fields:
                cleaned['country_of_birth'] = normalise_country(suffix)

        # The foaling date: a full date wins; else a year, maybe a guess.
        today = timezone.localdate()
        dob = cleaned.get('date_of_birth')
        year = cleaned.get('foaled_year')
        if dob:
            if dob > today:
                self.add_error('date_of_birth', "The foaling date can't be in the future.")
            precision = Horse.DobPrecision.EXACT
        elif year:
            if year > today.year:
                self.add_error('foaled_year', "The year can't be in the future.")
            dob = date(year, 1, 1)
            precision = (Horse.DobPrecision.ESTIMATED if cleaned.get('foaled_year_estimated')
                         else Horse.DobPrecision.YEAR)
        else:
            precision = Horse.DobPrecision.EXACT
        cleaned['date_of_birth'] = dob
        self.instance.dob_precision = precision

        if 'height' in self.fields and 'height' not in self.errors:
            self.instance.height_inches = cleaned.get('height')
        return cleaned

    # The "can't untick Active while a placement is open" guard lives on
    # Horse.clean() so it also covers the Django admin — ModelForm runs it
    # automatically and maps the error onto the is_active field.


class QuickHorseForm(HorseForm):
    """The day-to-day subset of HorseForm for the pop-up sheet.

    Profile picture, dam/sire, breeding text and ownership shares stay on
    the full Edit page; everything else (and the same validation, via
    inheritance) is here.
    """

    class Meta(HorseForm.Meta):
        # No "Active" tick box: a departure goes through Depart, which
        # closes the stay too (UX review D2). The full edit page keeps it.
        fields = [
            'name', 'registered_name', 'sex', 'color', 'date_of_birth',
            'country_of_birth', 'breed', 'markings',
            'passport_number', 'passport_issuer', 'has_passport',
            'microchip', 'ueln', 'notes',
        ]

    def _update_errors(self, errors):
        # Horse.clean() refuses "not active while a stay is open". This form
        # does not change Active, so that check is not about this edit: a
        # horse already in that state (the page's banner offers the repair)
        # can still have its details changed. Without the box, Django would
        # raise ValueError for an error on a field the form does not have.
        if hasattr(errors, 'error_dict'):
            errors.error_dict.pop('is_active', None)
            if not errors.error_dict:
                return
        super()._update_errors(errors)


class ScanCheckForm(forms.Form):
    """One box for "Scan to check": the scanner types the chip number."""

    scanned = forms.CharField(
        label='Microchip', max_length=60,
        widget=forms.TextInput(attrs={
            'class': 'form-input font-mono text-lg', 'autocomplete': 'off',
            'spellcheck': 'false', 'autofocus': True, 'enterkeyhint': 'done',
        }),
    )

    def clean_scanned(self):
        from .identity import normalise_microchip
        chip = normalise_microchip(self.cleaned_data['scanned'])
        if not chip:
            raise forms.ValidationError('Scan the chip, or type the number.')
        if len(chip) > Horse._meta.get_field('microchip').max_length:
            raise forms.ValidationError('That is too long to be a microchip number.')
        return chip


class HorseIdentifierForm(forms.ModelForm):
    """A stud-book number, a Racing Post link, an old chip, …"""

    class Meta:
        model = HorseIdentifier
        fields = ['kind', 'issuer', 'value', 'url', 'checked', 'notes']
        labels = {'value': 'Number', 'checked': 'Checked against the passport or the issuer'}
        widgets = {
            'kind': forms.Select(attrs={'class': 'form-select'}),
            'issuer': forms.TextInput(attrs={'class': 'form-input', 'list': 'identifier-issuers'}),
            'value': forms.TextInput(attrs={'class': 'form-input font-mono', 'autocomplete': 'off'}),
            'url': forms.URLInput(attrs={'class': 'form-input', 'placeholder': 'https://'}),
            'checked': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_value(self):
        value = (self.cleaned_data.get('value') or '').strip()
        if self.cleaned_data.get('kind') == HorseIdentifier.Kind.OLD_MICROCHIP:
            from .identity import normalise_microchip
            value = normalise_microchip(value)
        return value

    def save(self, commit=True):
        ident = super().save(commit=False)
        if ident.checked and not ident.checked_at:
            ident.checked_at = timezone.now()
            ident.checked_by = self.user if getattr(self.user, 'is_authenticated', False) else None
        elif not ident.checked:
            ident.checked_at = None
            ident.checked_by = None
        if commit:
            ident.save()
        return ident


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
        # Active fields, plus the stay's own field if it was archived since:
        # without it an old stay could not be saved without moving it.
        # The queryset matches, so a crafted POST cannot pick another
        # archived field.
        own_location = self.instance.location_id if self.instance.pk else None
        self.fields['location'].choices = get_grouped_location_choices(include_pk=own_location)
        allowed = Location.objects.active()
        if own_location:
            allowed = Location.objects.filter(
                Q(pk__in=allowed.values('pk')) | Q(pk=own_location)
            )
        self.fields['location'].queryset = allowed
        if self.instance.pk and self.instance.end_date is None:
            # The open stay is billed to whoever the Ownership card names,
            # and follows it (PlacementService.sync_placement_owner). An
            # owner typed here would be undone at the next ownership save,
            # so the field is not offered; past stays keep it, since they
            # were billed to whoever owned the horse at the time.
            del self.fields['owner']
        else:
            self.fields['owner'].queryset = owners_for_picker(self.instance.owner_id)

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


class StablePickerMixin(forms.Form):
    """An optional stable beside a form's location select.

    The picker is not a field of every location: it appears when the
    chosen location is a barn with stables, fetched by the location select
    itself (``hx-get`` to ``stable_options``) and drawn by
    locations/partials/stable_picker.html. With JavaScript off there is no
    picker and the horse simply goes into the barn with no stable, which
    is allowed.

    The view does the work: after the placement is written it calls
    ``StableService.assign`` with ``stable`` and ``share_stable``, inside
    the same transaction, so a stable that turns out to be taken undoes
    the move rather than half-saving it.
    """

    # The name of the form's location field.
    stable_location_field = 'location'

    stable = forms.ModelChoiceField(
        queryset=Stable.objects.none(), required=False, label='Stable',
    )
    share_stable = forms.BooleanField(required=False, label='Share the stable')

    def init_stable_picker(self, horse=None):
        """``horse`` is the horse being moved, when there is one: the
        location select then also asks whether the move wants a different
        rate (core.views.stables.stable_options)."""
        if not settings.BARNS_ENABLED:
            del self.fields['stable']
            del self.fields['share_stable']
            return
        self.fields['stable'].queryset = Stable.objects.filter(
            is_gap=False, in_use=True, location__is_archived=False,
            location__location_type=Location.LocationType.BARN,
        )
        url = reverse('stable_options')
        if horse is not None:
            url += f'?horse={horse.pk}'
        self.fields[self.stable_location_field].widget.attrs.update({
            'hx-get': url,
            'hx-trigger': 'change',
            'hx-target': '#stable-picker',
            'hx-select': 'unset',
            'hx-swap': 'innerHTML',
            'hx-push-url': 'false',
        })

    def clean(self):
        cleaned = super().clean()
        stable = cleaned.get('stable')
        location = cleaned.get(self.stable_location_field)
        if stable is not None and location is not None and stable.location_id != location.pk:
            self.add_error('stable', f'Stable {stable.label} is not in {location.name}.')
        return cleaned

    @property
    def stable_picker(self):
        """What the picker shows when the form is first drawn or drawn
        again with errors: the stables of whichever location is chosen."""
        if 'stable' not in self.fields:
            return None
        from .stables import picker_options

        name = self.stable_location_field
        if self.is_bound:
            chosen = self.data.get(self.add_prefix(name))
            selected = self.data.get(self.add_prefix('stable'))
        else:
            chosen = self.initial.get(name)
            selected = self.initial.get('stable')
        chosen = getattr(chosen, 'pk', chosen)
        if not chosen or not str(chosen).isdigit():
            return None
        location = Location.objects.active().filter(pk=chosen).first()
        picker = picker_options(location, selected=getattr(selected, 'pk', selected))
        if picker is not None:
            picker['share'] = bool(self.is_bound and self.data.get(self.add_prefix('share_stable')))
        return picker


class HorseStableForm(forms.Form):
    """Put a horse that is already in a barn into one of its stables."""

    stable = forms.ModelChoiceField(
        queryset=Stable.objects.none(), label='Stable',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    day = forms.DateField(
        label='From', input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
    )
    share_stable = forms.BooleanField(
        required=False, label='Share the stable',
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        help_text='Tick to put this horse in with the one already there.',
    )

    def __init__(self, *args, horse, barn, **kwargs):
        super().__init__(*args, **kwargs)
        from .stables import picker_options

        self.horse = horse
        field = self.fields['stable']
        field.queryset = barn.stables.filter(is_gap=False, in_use=True).order_by('position', 'pk')
        picker = picker_options(barn, exclude_horse=horse)
        who = {option['stable'].pk: option['names'] for option in (picker or {'options': []})['options']}
        field.label_from_instance = lambda stable: (
            f"{stable.label} — {who[stable.pk]}" if who.get(stable.pk) else f"{stable.label} — empty"
        )
        field.empty_label = 'Choose a stable'
        if not (picker and picker['any_occupied']):
            del self.fields['share_stable']

    def clean_day(self):
        day = self.cleaned_data['day']
        if day > timezone.localdate():
            raise forms.ValidationError("A stable change can't be dated in the future.")
        return day


class SwapStableForm(forms.Form):
    """Swap one horse's stable with another's in the same barn."""

    other = forms.ModelChoiceField(
        queryset=Horse.objects.none(), label='Swap with',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    day = forms.DateField(
        label='From', input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
    )

    def __init__(self, *args, horse, stay, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import StableStay

        others = StableStay.objects.filter(
            stable__location_id=stay.stable.location_id, end_date__isnull=True,
        ).exclude(stable_id=stay.stable_id).select_related('stable')
        where = {other.horse_id: other.stable.label for other in others}
        field = self.fields['other']
        field.queryset = Horse.objects.filter(pk__in=where).order_by('name')
        field.label_from_instance = lambda other: f"{other.name} — stable {where[other.pk]}"
        field.empty_label = 'Choose a horse'

    def clean_day(self):
        day = self.cleaned_data['day']
        if day > timezone.localdate():
            raise forms.ValidationError("A stable change can't be dated in the future.")
        return day


class TurnoutForm(forms.Form):
    """Set, change or stop the turnout of one or more stabled horses.

    One form for all three, for one horse or a barn-full: the location
    select has a "No turnout" row, and the horses are a tick list (hidden
    when the form is for a single horse). A yard changes its turnout a
    barn at a time when a paddock is rested, so the tick list starts with
    everything ticked.

    The select starts on a blank row that will not save. With every horse
    ticked, a form that opened on "No turnout" would stop a whole barn's
    turnout on one stray press of Save.
    """

    STOP = 'none'
    horses = forms.ModelMultipleChoiceField(
        queryset=Horse.objects.none(), label='Horses',
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-checkbox'}),
    )
    location = forms.ChoiceField(
        label='Goes out in',
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='The ground these horses go out on by day. They still live in the barn.',
    )
    day = forms.DateField(
        label='From', input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
    )

    def __init__(self, *args, barn, horses, single=False, **kwargs):
        super().__init__(*args, **kwargs)
        from .turnout import turnout_choices

        self.fields['horses'].queryset = horses
        if single:
            self.fields['horses'].widget = forms.MultipleHiddenInput()
        self.fields['location'].choices = (
            [('', 'Choose…'), (self.STOP, 'No turnout')] + turnout_choices(barn)
        )

    def clean_day(self):
        day = self.cleaned_data['day']
        if day > timezone.localdate():
            raise forms.ValidationError("A turnout change can't be dated in the future.")
        return day

    def clean_location(self):
        """The ground chosen, or None for "No turnout"."""
        value = self.cleaned_data.get('location')
        if value == self.STOP:
            return None
        location = Location.objects.active().fields().filter(pk=value).first()
        if location is None:
            raise forms.ValidationError('Choose ground that is in use.')
        return location


class MoveHorseForm(StablePickerMixin, forms.Form):
    """Form for moving a horse to a new location."""
    stable_location_field = 'new_location'

    new_location = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, horse=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Setting .choices only changes the rendering; ModelChoiceField
        # validates against .queryset, so an archived location's pk (stale
        # tab, back button, hand-crafted post) was still accepted.
        self.fields['new_location'].queryset = Location.objects.active()
        self.fields['new_location'].choices = get_grouped_location_choices()
        self.fields['new_owner'].queryset = Owner.objects.active()
        rate = self.fields['new_rate_type']
        rate.queryset = RateType.objects.filter(is_active=True)
        if settings.BARNS_ENABLED:
            # The move form's script sets this select when a move between
            # a field and a barn has an obvious rate, and leaves it alone
            # once the person has chosen for themselves
            # (templates/horses/partials/move_form.html).
            rate.empty_label = 'Keep the current rate'
            rate.widget.attrs.update({'x-ref': 'rate', '@change': 'rateTouched = true'})
        self.init_stable_picker(horse)

    new_owner = forms.ModelChoiceField(
        queryset=Owner.objects.active(),
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


def move_destinations(exclude_pk=None):
    """Where a group can move to: ``(choices, room)``.

    ``choices`` are the active locations grouped by site, each with how
    full it is: "Top Field (4 of 8)". Someone who moves a group must see
    if the group fits. ``room`` is ``{pk: spaces left}`` for the
    locations that have a limit (0 when full or over), so the form can
    say before the save that a group does not fit. ``exclude_pk`` leaves
    out the location the horses are in.
    """
    from django.db.models import Count

    counts = dict(
        Placement.objects.filter(end_date__isnull=True, horse__is_active=True)
        .values_list('location').annotate(n=Count('pk'))
    )
    locations = Location.objects.active().order_by('site', 'name')
    if exclude_pk:
        locations = locations.exclude(pk=exclude_pk)
    choices = [('', 'Choose a location…')]
    groups, room = {}, {}
    for loc in locations:
        here = counts.get(loc.pk, 0)
        # The limit counts whatever the land is used for today: rested
        # ground is where a group goes next, and it holds horses again
        # from the moment they arrive.
        if loc.capacity:
            room[loc.pk] = max(0, loc.capacity - here)
            fill = f'{here} of {loc.capacity}' if here else f'empty, room for {loc.capacity}'
        elif here:
            fill = f"{here} horse{'s' if here != 1 else ''}"
        else:
            fill = 'empty'
        # A location with no site still needs a group with a name: an
        # empty one would read as the "Choose a location…" line.
        groups.setdefault(loc.site or 'No site', []).append((loc.pk, f'{loc.name} ({fill})'))
    choices += list(groups.items())
    return choices, room


class GroupMoveForm(MoveHorseForm):
    """Move several horses to one location.

    The single-horse move form minus the per-horse fields: every horse
    keeps its own owner, and the rate applies to all of them (left empty,
    each keeps its current rate). Subclassing keeps single and group move
    semantics from drifting apart.

    Used by the bulk action "Move to Location" (health.forms.BulkMoveForm)
    and by "Move horses" on a location (LocationMoveForm).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        del self.fields['new_owner']
        del self.fields['expected_departure']
        # A stable is one horse's. A group goes into the barn, and each
        # horse gets its stable on the barn's page. The single form's
        # location select fetches a stable picker and a rate hint for one
        # horse; a group form has no place to show them.
        for name in ('stable', 'share_stable'):
            self.fields.pop(name, None)
        location = self.fields['new_location'].widget.attrs
        for key in [key for key in location if key.startswith('hx-')]:
            del location[key]
        rate = self.fields['new_rate_type']
        for key in ('x-ref', '@change'):
            rate.widget.attrs.pop(key, None)
        rate.help_text = "Leave empty to keep each horse's current rate"
        # A move is recorded on the day (PlacementService), so the date
        # box offers no day after today.
        self.fields['move_date'].widget.attrs['max'] = timezone.localdate().isoformat()


class LocationMoveForm(GroupMoveForm):
    """Move the horses of one location (``source``) to another
    (core.views.locations.location_move_horses)."""

    def __init__(self, *args, source, **kwargs):
        super().__init__(*args, **kwargs)
        self.source = source
        field = self.fields['new_location']
        field.label = 'Move to'
        field.queryset = Location.objects.active().exclude(pk=source.pk)
        field.choices, self.room = move_destinations(exclude_pk=source.pk)


class ArrivalForm(forms.Form):
    """Form for logging horse arrivals at a location (supports multiple horses)."""
    horses = forms.ModelMultipleChoiceField(
        queryset=Horse.objects.filter(is_active=True),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-checkbox rounded'}),
        help_text="Select horses to arrive at this location"
    )
    owner = forms.ModelChoiceField(
        queryset=Owner.objects.active(),
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
        self.fields['owner'].queryset = Owner.objects.active()
        self.fields['rate_type'].queryset = RateType.objects.filter(is_active=True)


class SingleArrivalForm(StablePickerMixin, forms.Form):
    """Form for logging a single horse arrival (from Horse Detail page)."""
    location = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # See MoveHorseForm: .queryset is what validates, .choices only renders.
        self.fields['location'].queryset = Location.objects.active()
        self.fields['location'].choices = get_grouped_location_choices()
        self.fields['owner'].queryset = Owner.objects.active()
        self.fields['rate_type'].queryset = RateType.objects.filter(is_active=True)
        self.init_stable_picker()

    owner = forms.ModelChoiceField(
        queryset=Owner.objects.active(),
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


class NewArrivalForm(StablePickerMixin, forms.Form):
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
    microchip = forms.CharField(
        max_length=60, required=False,
        widget=forms.TextInput(attrs=MICROCHIP_INPUT_ATTRS),
    )

    # Owner
    owner = forms.ModelChoiceField(
        queryset=Owner.objects.active(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    # The main owner's share when other owners are added (CoOwnerFormSet).
    # With no other owners it is 100, whatever the box says.
    owner_share = forms.DecimalField(
        required=False, max_digits=5, decimal_places=2,
        min_value=Decimal('0.01'), max_value=Decimal('100'), initial=Decimal('100'),
        widget=forms.NumberInput(attrs={
            'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal', 'min': '0.01', 'max': '100',
        }),
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
        self.fields['owner'].queryset = Owner.objects.active()
        # See MoveHorseForm: .queryset is what validates, .choices only renders.
        self.fields['location'].queryset = Location.objects.active()
        self.fields['location'].choices = get_grouped_location_choices()
        self.init_stable_picker()
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


class CoOwnerForm(forms.Form):
    """One other owner on the new arrival page: who, and their share."""
    owner = forms.ModelChoiceField(
        queryset=Owner.objects.active(), required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    share_percentage = forms.DecimalField(
        required=False, max_digits=5, decimal_places=2,
        min_value=Decimal('0.01'), max_value=Decimal('99.99'),
        widget=forms.NumberInput(attrs={
            'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal', 'min': '0.01', 'max': '99.99',
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['owner'].queryset = Owner.objects.active()

    def clean(self):
        data = super().clean()
        owner, share = data.get('owner'), data.get('share_percentage')
        if owner and share is None and 'share_percentage' not in self.errors:
            self.add_error('share_percentage', 'Enter this owner\'s share.')
        if share is not None and not owner:
            self.add_error('owner', 'Choose the owner for this share.')
        return data


class BaseCoOwnerFormSet(forms.BaseFormSet):
    """The other owners on the new arrival page (Dev board, 21 Nov 2026:
    "When adding a new horse, don't get ownership split").

    shares(main_owner, main_share) checks them against the main owner and
    answers the full list of (owner, share, is_primary), or None after it
    adds the error. Rows left empty are skipped.
    """

    def rows(self):
        out = []
        for form in self.forms:
            data = getattr(form, 'cleaned_data', None) or {}
            if data.get('owner') and data.get('share_percentage') is not None:
                out.append((data['owner'], data['share_percentage']))
        return out

    def shares(self, main_owner, main_share):
        others = self.rows()
        if not others:
            return [(main_owner, Decimal('100'), True)]
        seen = {main_owner.pk}
        for owner, _share in others:
            if owner.pk in seen:
                self._non_form_errors = self.error_class(
                    [f'{owner.name} is on the list twice. Give each owner one row.'])
                return None
            seen.add(owner.pk)
        if main_share is None:
            self._non_form_errors = self.error_class(
                ['Enter the main owner\'s share, so the shares add up to 100%.'])
            return None
        total = main_share + sum(share for _owner, share in others)
        if total != Decimal('100'):
            self._non_form_errors = self.error_class(
                [f'The shares must add up to 100%. They add up to {total.normalize():f}% now.'])
            return None
        return [(main_owner, main_share, True)] + [(owner, share, False) for owner, share in others]


CoOwnerFormSet = forms.formset_factory(CoOwnerForm, formset=BaseCoOwnerFormSet, extra=0, max_num=10)


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['owner'].queryset = owners_for_picker(self.instance.owner_id)
        # The formset checks the rows' total as submitted. The model's own
        # check adds this row to the OTHER rows' saved values, so moving
        # shares between owners (A 100 -> A 50 + B 50, 50/50 -> 40/60, or
        # replacing an owner) always failed it, in validation and again
        # row by row on save.
        self.instance._total_checked_by_formset = True


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
        fields = ['name', 'daily_rate', 'applies_to', 'description', 'includes_exercise', 'is_active']
        widgets = {
            'includes_exercise': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'applies_to': forms.Select(attrs={'class': 'form-select'}),
            'daily_rate': MoneyInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
            'description': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.conf import settings
        if not getattr(settings, 'TRAINING_ENABLED', False):
            # Training phase 5: the tick matters only with Training on.
            self.fields.pop('includes_exercise', None)
        if self.instance.pk:
            # An existing rate's price changes from a date (RatePriceForm).
            del self.fields['daily_rate']
        if not settings.BARNS_ENABLED:
            # Only means anything once a yard can have barns.
            del self.fields['applies_to']


class RatePriceForm(forms.Form):
    """A new daily price for a rate type, from a date in the past or future."""

    daily_rate = forms.DecimalField(
        label='New price per day (£)', min_value=Decimal('0.00'), max_digits=8, decimal_places=2,
        widget=MoneyInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
    )
    effective_from = forms.DateField(
        label='From', input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
        help_text='The first day at the new price. It can be in the past or the future.',
    )


class BusinessSettingsForm(forms.ModelForm):
    class Meta:
        model = BusinessSettings
        fields = [
            'business_name', 'address', 'phone', 'email', 'website',
            'vat_registration', 'vat_rate', 'bank_details', 'card_payment_url',
            'default_payment_terms', 'invoice_prefix', 'auto_generate_invoices',
            'overdue_reminder_days', 'overdue_reminder_message',
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
            # Rendered as a switch on the settings page: the input is visually
            # hidden and the .switch-track next to it draws the control.
            'auto_generate_invoices': forms.CheckboxInput(attrs={'class': 'switch-input'}),
            'vat_rate': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
            'overdue_reminder_days': forms.TextInput(attrs={'class': 'form-input', 'placeholder': '1, 14, 30', 'inputmode': 'numeric'}),
            'overdue_reminder_message': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def clean_overdue_reminder_days(self):
        from invoicing.reminders import parse_days
        text = self.cleaned_data.get('overdue_reminder_days', '')
        try:
            days = parse_days(text)
        except ValueError as e:
            raise forms.ValidationError(str(e))
        return ', '.join(str(d) for d in days) if days else ''

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
            'title': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Optional, e.g. Weatherbys'}),
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
    # Also copy the first saved photo onto Horse.photo (the avatar). Ignored
    # for passport shots, which become Documents rather than HorsePhotos.
    set_profile = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
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

    def __init__(self, *args, instance=None, can_grant_admin=False, **kwargs):
        from .models import Role
        self.instance = instance
        super().__init__(*args, **kwargs)
        roles = Role.objects.order_by('-is_system', 'name')
        if not can_grant_admin:
            # The Administrator role (is_system) passes every check that is
            # kept to Administrators, so only an Administrator may give it.
            roles = roles.filter(is_system=False)
        self.fields['role'].queryset = roles

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


def _clean_password_pair(form, cleaned, user):
    """Shared match + strength validation for the create and reset forms —
    one definition so the two flows can't drift on what they accept."""
    p1, p2 = cleaned.get('password1'), cleaned.get('password2')
    if p1 and p2 and p1 != p2:
        form.add_error('password2', "Passwords don't match.")
    elif p1:
        from django.contrib.auth import password_validation
        try:
            password_validation.validate_password(p1, user=user)
        except forms.ValidationError as e:
            form.add_error('password1', e)


class UserCreateForm(UserAccountForm):
    """New account. Leave the password blank to email an invitation instead,
    so nobody else ever knows the new user's password."""

    password1 = forms.CharField(
        label="Password",
        required=False,
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'autocomplete': 'new-password'}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        required=False,
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'autocomplete': 'new-password'}),
    )

    @property
    def sends_invite(self):
        return not self.cleaned_data.get('password1')

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('password2') and not cleaned.get('password1'):
            self.add_error('password1', "Enter the password, or leave both boxes blank to send an invitation.")
        from django.contrib.auth import get_user_model
        # Unsaved probe so similarity validators can see the name/email.
        probe = get_user_model()(
            username=cleaned.get('email', ''),
            email=cleaned.get('email', ''),
            first_name=cleaned.get('first_name', ''),
            last_name=cleaned.get('last_name', ''),
        )
        _clean_password_pair(self, cleaned, probe)
        return cleaned

    def save(self):
        from django.contrib.auth import get_user_model
        from .models import UserRole
        data = self.cleaned_data
        user = get_user_model().objects.create_user(
            username=data['email'],
            email=data['email'],
            # None gives an unusable password: the account cannot sign in
            # until the invitation link has been used.
            password=data['password1'] or None,
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
        _clean_password_pair(self, cleaned, self.user)
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


# ── Land App boundary import (phase 4b) ──────────────────────────────────

BOUNDARY_FILE_EXTENSIONS = ('geojson', 'json')


def validate_boundary_file_size(value):
    """Reject uploads larger than 10MB (the boundary import's limit)."""
    from .boundary_import import MAX_FILE_BYTES
    try:
        size = value.size
    except (FileNotFoundError, OSError):
        return
    if size > MAX_FILE_BYTES:
        raise DjangoValidationError('That file is over 10MB. Export one plan at a time from Land App.')


class BoundaryUploadForm(forms.Form):
    """Step one of the import: which site, and the exported file."""

    site = forms.ChoiceField(
        label='Site',
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='The shapes will be matched to this site\'s locations.',
    )
    file = forms.FileField(
        label='Land App export (.geojson)',
        widget=forms.ClearableFileInput(attrs={'class': 'form-input', 'accept': '.geojson,.json,application/geo+json,application/json'}),
        validators=[
            FileExtensionValidator(
                BOUNDARY_FILE_EXTENSIONS,
                message='Upload a GeoJSON file (.geojson or .json). In Land App choose '
                        'Export → GeoJSON; Shapefile, KML and DXF are not read.',
            ),
            validate_boundary_file_size,
        ],
    )

    def __init__(self, *args, sites=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].choices = [(name, name) for name in sites]


class ProfileDetailsForm(forms.Form):
    """The Details tab of the profile pop-up (core.views.profile).

    Name and email live on Django's user; phone and photo on UserProfile.
    The photo is checked the same way as a horse photo (type, size, and
    that it decodes), then stored as a small square JPEG only.
    """

    first_name = forms.CharField(
        max_length=150, required=False, label='First name',
        widget=forms.TextInput(attrs={'autocomplete': 'given-name'}),
    )
    last_name = forms.CharField(
        max_length=150, required=False, label='Last name',
        widget=forms.TextInput(attrs={'autocomplete': 'family-name'}),
    )
    email = forms.EmailField(
        max_length=254, label='Email',
        widget=forms.EmailInput(attrs={'autocomplete': 'email'}),
    )
    phone = forms.CharField(
        max_length=50, required=False, label='Mobile phone',
        widget=forms.TextInput(attrs={'type': 'tel', 'autocomplete': 'tel', 'inputmode': 'tel'}),
    )
    photo = forms.FileField(
        required=False, label='Photo',
        widget=forms.ClearableFileInput(attrs={'accept': 'image/*'}),
    )
    remove_photo = forms.BooleanField(required=False, label='Remove my photo')
    display_size = forms.ChoiceField(
        label='Size on a computer',
        choices=[
            ('compact', 'Compact (best on a laptop)'),
            ('medium', 'Medium'),
            ('large', 'Large'),
        ],
        initial='compact',
        help_text='How big text and buttons are on a computer. A phone has its own size, below.',
    )
    # Not required: a form sent without it (a page that was open before
    # this setting existed) keeps the size the profile has.
    phone_text_size = forms.ChoiceField(
        label='Text size on a phone',
        required=False,
        choices=[
            ('standard', 'Standard'),
            ('large', 'Large (easier to read outdoors)'),
        ],
        initial='standard',
        help_text='How big text and buttons are on a phone or a tablet.',
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        from django.contrib.auth import get_user_model
        email = self.cleaned_data['email'].strip()
        taken = (
            get_user_model().objects
            .filter(email__iexact=email)
            .exclude(pk=self.user.pk)
            .exists()
        )
        if taken:
            raise forms.ValidationError('Another user already has this email address.')
        return email

    def clean_phone(self):
        return self.cleaned_data.get('phone', '').strip()

    def clean_photo(self):
        upload = self.cleaned_data.get('photo')
        if not upload:
            return None
        from PIL import Image, UnidentifiedImageError

        from .images import normalise_photo
        from .models import validate_file_size

        upload = normalise_photo(upload)
        FileExtensionValidator(
            allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif'],
        )(upload)
        validate_file_size(upload)
        try:
            upload.seek(0)
            with Image.open(upload) as img:
                img.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise forms.ValidationError('This file is not a photo we can read. Use a JPEG, PNG or HEIC photo.')
        upload.seek(0)
        return upload


class CalendarEntryForm(forms.ModelForm):
    """A general item on the dashboard calendar (core.models.CalendarEntry)."""

    class Meta:
        model = CalendarEntry
        fields = ['title', 'date', 'time', 'repeat', 'repeat_until', 'location', 'horse', 'notes']
        labels = {
            'title': 'What', 'date': 'Date', 'time': 'Time', 'repeat': 'Repeat',
            'repeat_until': 'Until', 'location': 'Where', 'horse': 'Horse',
        }
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Hay delivery', 'autofocus': True}),
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'time': forms.TimeInput(format='%H:%M', attrs={'class': 'form-input', 'type': 'time'}),
            # Alpine (calendar_entry_form.html): "Until" shows only while set.
            'repeat': forms.Select(attrs={'class': 'form-select', 'x-on:change': "repeats = $event.target.value !== ''"}),
            'repeat_until': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'location': forms.Select(attrs={'class': 'form-select'}),
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
        }
        help_texts = {
            'time': '', 'location': 'Optional.', 'horse': 'Optional.',
            'repeat_until': 'Optional. Blank: no end.',
        }
        error_messages = {
            'title': {'required': 'Say what is on this day.'},
            'date': {'required': 'Choose a day.'},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['location'].queryset = Location.objects.active().order_by('site', 'name')
        self.fields['location'].label_from_instance = (
            lambda loc: f'{loc.name} · {loc.site}' if loc.site else loc.name)
        self.fields['horse'].queryset = Horse.objects.filter(is_active=True).order_by('name')
        self.fields['location'].empty_label = 'Anywhere'
        self.fields['horse'].empty_label = 'No horse'

    def clean_title(self):
        title = (self.cleaned_data.get('title') or '').strip()
        if not title:
            raise forms.ValidationError('Say what is on this day.')
        return title

    def clean(self):
        cleaned = super().clean()
        start, until = cleaned.get('date'), cleaned.get('repeat_until')
        if not cleaned.get('repeat'):
            # "Until" means nothing for an item that does not repeat.
            cleaned['repeat_until'] = None
        elif start and until and until < start:
            self.add_error('repeat_until', 'The last day must be on or after the first day.')
        return cleaned
