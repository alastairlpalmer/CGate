"""
Forms for health app.
"""

from datetime import timedelta

from django import forms
from django.db.models import Q
from django.utils import timezone

from core.forms import MoveHorseForm
from core.models import Horse
from .models import (
    BreedingRecord,
    FarrierVisit,
    MedicalCondition,
    Vaccination,
    VaccinationType,
    VetVisit,
    WormEggCount,
    WormingTreatment,
)


class OptionalCostMixin:
    """Make the cost field optional: blank or omitted means 0 (not billable)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cost'].required = False

    def clean_cost(self):
        from decimal import Decimal
        return self.cleaned_data.get('cost') or Decimal('0.00')


class ActiveHorseFormMixin:
    """Mixin to restrict horse/mare dropdown to active horses only."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'horse' in self.fields:
            self.fields['horse'].queryset = Horse.objects.filter(is_active=True)
        if 'mare' in self.fields:
            self.fields['mare'].queryset = Horse.objects.filter(is_active=True)


class VaccinationForm(OptionalCostMixin, ActiveHorseFormMixin, forms.ModelForm):
    class Meta:
        model = Vaccination
        fields = [
            'horse', 'vaccination_type', 'date_given', 'next_due_date',
            'vet', 'batch_number', 'cost', 'notes'
        ]
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'vaccination_type': forms.Select(attrs={'class': 'form-select'}),
            'date_given': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'next_due_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'vet': forms.Select(attrs={'class': 'form-select'}),
            'batch_number': forms.TextInput(attrs={'class': 'form-input'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal', 'min': '0'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Allow blank so model.save() can auto-calculate from vaccination_type interval
        self.fields['next_due_date'].required = False
        # Filter vet dropdown to active vets only
        from billing.models import ServiceProvider
        self.fields['vet'].queryset = ServiceProvider.objects.filter(
            provider_type='vet', is_active=True
        )
        self.fields['vet'].empty_label = "Select vet..."
        # Deactivated vaccination types stay out of new records but remain
        # selectable on records that already use them.
        if 'vaccination_type' in self.fields:
            from django.db.models import Q
            keep = Q(is_active=True)
            if self.instance.pk and self.instance.vaccination_type_id:
                keep |= Q(pk=self.instance.vaccination_type_id)
            self.fields['vaccination_type'].queryset = (
                self.fields['vaccination_type'].queryset.filter(keep)
            )

    def clean(self):
        cleaned_data = super().clean()
        date_given = cleaned_data.get('date_given')
        next_due = cleaned_data.get('next_due_date')
        if date_given and next_due and next_due <= date_given:
            self.add_error('next_due_date', "Next due date must be after the date given.")
        from django.utils import timezone
        if date_given and date_given > timezone.localdate():
            self.add_error(
                'date_given',
                "Date given cannot be in the future — a typo here silently "
                "delays the whole booster schedule.",
            )
        # Editing the date given or type without touching the stored due
        # date means the user wants it recalculated — clearing it lets
        # model.save() recompute from the (new) type interval, instead of
        # keeping a due date derived from the old values.
        if (
            self.instance.pk
            and ('date_given' in self.changed_data
                 or 'vaccination_type' in self.changed_data)
            and 'next_due_date' not in self.changed_data
        ):
            cleaned_data['next_due_date'] = None
            self.instance.next_due_date = None
        return cleaned_data


class FarrierVisitForm(ActiveHorseFormMixin, forms.ModelForm):
    class Meta:
        model = FarrierVisit
        fields = [
            'horse', 'date', 'service_provider', 'work_done',
            'next_due_date', 'cost', 'notes'
        ]
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'service_provider': forms.Select(attrs={'class': 'form-select'}),
            'work_done': forms.Select(attrs={'class': 'form-select'}),
            'next_due_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def clean(self):
        cleaned_data = super().clean()
        visit_date = cleaned_data.get('date')
        next_due = cleaned_data.get('next_due_date')
        if visit_date and next_due and next_due <= visit_date:
            self.add_error('next_due_date', "Next due date must be after the visit date.")
        # Same rule as VaccinationForm: editing the visit date without
        # touching the due date means "recalculate it". FarrierVisit.save()
        # only fills next_due_date when empty, so the old value stayed
        # anchored to the old visit date and the horse showed due/overdue
        # on the wrong day.
        if (
            self.instance.pk
            and 'date' in self.changed_data
            and 'next_due_date' not in self.changed_data
        ):
            cleaned_data['next_due_date'] = None
            self.instance.next_due_date = None
        return cleaned_data


class VaccinationTypeForm(forms.ModelForm):
    class Meta:
        model = VaccinationType
        fields = ['name', 'interval_months', 'reminder_days_before', 'description', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'interval_months': forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric', 'min': '1'}),
            'reminder_days_before': forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric', 'min': '0'}),
            'description': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }

    def clean_interval_months(self):
        interval = self.cleaned_data['interval_months']
        if interval is not None and interval < 1:
            # interval 0 makes every record instantly overdue on creation.
            raise forms.ValidationError("Interval must be at least 1 month.")
        return interval


class WormingTreatmentForm(OptionalCostMixin, ActiveHorseFormMixin, forms.ModelForm):
    class Meta:
        model = WormingTreatment
        fields = [
            'horse', 'date', 'product_name', 'active_ingredient',
            'dose', 'administered_by', 'cost', 'notes'
        ]
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'product_name': forms.TextInput(attrs={'class': 'form-input'}),
            'active_ingredient': forms.TextInput(attrs={'class': 'form-input'}),
            'dose': forms.TextInput(attrs={'class': 'form-input'}),
            'administered_by': forms.TextInput(attrs={'class': 'form-input'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal', 'min': '0'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class WormEggCountForm(ActiveHorseFormMixin, forms.ModelForm):
    class Meta:
        model = WormEggCount
        fields = ['horse', 'date', 'count', 'lab_name', 'sample_type', 'notes']
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'count': forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
            'lab_name': forms.TextInput(attrs={'class': 'form-input'}),
            'sample_type': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class MedicalConditionForm(ActiveHorseFormMixin, forms.ModelForm):
    class Meta:
        model = MedicalCondition
        fields = ['horse', 'name', 'diagnosed_date', 'status', 'notes']
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'diagnosed_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class VetVisitForm(ActiveHorseFormMixin, forms.ModelForm):
    class Meta:
        model = VetVisit
        fields = [
            'horse', 'date', 'vet', 'reason', 'diagnosis',
            'treatment', 'follow_up_date', 'cost', 'notes'
        ]
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'vet': forms.Select(attrs={'class': 'form-select'}),
            'reason': forms.TextInput(attrs={'class': 'form-input'}),
            'diagnosis': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'treatment': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'follow_up_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def clean(self):
        cleaned_data = super().clean()
        visit_date = cleaned_data.get('date')
        follow_up = cleaned_data.get('follow_up_date')
        if visit_date and follow_up and follow_up <= visit_date:
            self.add_error('follow_up_date', "Follow-up date must be after the visit date.")
        return cleaned_data


# ─── Bulk Forms (no horse field) ──────────────────────────────────────

class BulkVaccinationForm(OptionalCostMixin, forms.ModelForm):
    class Meta:
        model = Vaccination
        fields = ['vaccination_type', 'date_given', 'next_due_date', 'vet', 'batch_number', 'cost', 'notes']
        labels = {'cost': 'Cost per horse (£)'}
        help_texts = {'cost': 'Charged in full to each selected horse’s owner.'}
        widgets = {
            'vaccination_type': forms.Select(attrs={'class': 'form-select'}),
            'date_given': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'next_due_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'vet': forms.Select(attrs={'class': 'form-select'}),
            'batch_number': forms.TextInput(attrs={'class': 'form-input'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal', 'min': '0'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['next_due_date'].required = False
        from billing.models import ServiceProvider
        self.fields['vet'].queryset = ServiceProvider.objects.filter(
            provider_type='vet', is_active=True
        )
        self.fields['vet'].empty_label = "Select vet..."


class BulkFarrierVisitForm(forms.ModelForm):
    class Meta:
        model = FarrierVisit
        fields = ['date', 'service_provider', 'work_done', 'next_due_date', 'cost', 'notes']
        # The bulk action creates one record — and one charge — per selected
        # horse, so an ambiguous "Cost" label invites entering the whole
        # visit's total and billing it N times over.
        labels = {'cost': 'Cost per horse (£)'}
        help_texts = {'cost': 'Charged in full to each selected horse’s owner.'}
        widgets = {
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'service_provider': forms.Select(attrs={'class': 'form-select'}),
            'work_done': forms.Select(attrs={'class': 'form-select'}),
            'next_due_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class BulkWormingTreatmentForm(OptionalCostMixin, forms.ModelForm):
    class Meta:
        model = WormingTreatment
        fields = ['date', 'product_name', 'active_ingredient', 'dose', 'administered_by', 'cost', 'notes']
        labels = {'cost': 'Cost per horse (£)'}
        help_texts = {'cost': 'Charged in full to each selected horse’s owner.'}
        widgets = {
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'product_name': forms.TextInput(attrs={'class': 'form-input'}),
            'active_ingredient': forms.TextInput(attrs={'class': 'form-input'}),
            'dose': forms.TextInput(attrs={'class': 'form-input'}),
            'administered_by': forms.TextInput(attrs={'class': 'form-input'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal', 'min': '0'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class BulkWormEggCountForm(forms.ModelForm):
    class Meta:
        model = WormEggCount
        fields = ['date', 'count', 'lab_name', 'sample_type', 'notes']
        widgets = {
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'count': forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
            'lab_name': forms.TextInput(attrs={'class': 'form-input'}),
            'sample_type': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class BulkVetVisitForm(forms.ModelForm):
    class Meta:
        model = VetVisit
        fields = ['date', 'vet', 'reason', 'diagnosis', 'treatment', 'follow_up_date', 'cost', 'notes']
        # See BulkFarrierVisitForm: one charge per selected horse.
        labels = {'cost': 'Cost per horse (£)'}
        help_texts = {'cost': 'Charged in full to each selected horse’s owner.'}
        widgets = {
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'vet': forms.Select(attrs={'class': 'form-select'}),
            'reason': forms.TextInput(attrs={'class': 'form-input'}),
            'diagnosis': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'treatment': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'follow_up_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'inputmode': 'decimal'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


# ─── Bulk Departure Forms ────────────────────────────────────────────

class BulkExpectedDepartureForm(forms.Form):
    date = forms.DateField(
        label='Expected Departure Date',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
    )


class BulkActualDepartureForm(forms.Form):
    date = forms.DateField(
        label='Departure Date',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
    )


class BulkMoveForm(MoveHorseForm):
    """Bulk action: move the selected horses to a new location.

    The single-horse move form minus the per-horse fields: every horse
    keeps its own owner, and the rate applies to all of them (left empty,
    each keeps its current rate). Subclassing keeps single and bulk move
    semantics from drifting apart.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        del self.fields['new_owner']
        del self.fields['expected_departure']
        self.fields['new_rate_type'].help_text = (
            "Leave empty to keep each horse's current rate"
        )


class BulkRestoreForm(forms.Form):
    """Bulk action: undo departures logged by mistake.

    No inputs — each selected horse's most recent stay is re-opened exactly
    as it was; the confirmation text in the modal is the whole UI.
    """


class BulkMedicalConditionForm(forms.ModelForm):
    class Meta:
        model = MedicalCondition
        fields = ['name', 'diagnosed_date', 'status', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'diagnosed_date': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class BreedingRecordForm(ActiveHorseFormMixin, forms.ModelForm):
    class Meta:
        model = BreedingRecord
        fields = [
            'mare', 'stallion_name', 'date_covered', 'status',
            'date_scanned_14_days', 'date_scanned_heartbeat', 'date_foal_due',
            'foal', 'foal_dob', 'foal_sex', 'foal_colour', 'foal_microchip',
            'foaling_notes',
        ]
        labels = {
            'date_scanned_14_days': '14-day scan',
            'date_scanned_heartbeat': 'Heartbeat scan',
            'date_foal_due': 'Foal due',
            'foal': 'Foal (existing record)',
            'foal_dob': 'Foal date of birth',
        }
        help_texts = {
            'date_covered': 'Foal due is worked out as 340 days from this date.',
            'foal': 'Only foals are listed. Use "Record foaling" to create the '
                    'foal and link it in one step.',
        }
        widgets = {
            'mare': forms.Select(attrs={'class': 'form-select'}),
            'stallion_name': forms.TextInput(attrs={'class': 'form-input'}),
            'date_covered': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'date_scanned_14_days': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'date_scanned_heartbeat': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'date_foal_due': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'foal': forms.Select(attrs={'class': 'form-select'}),
            'foal_dob': forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
            'foal_sex': forms.Select(attrs={'class': 'form-select'}),
            'foal_colour': forms.Select(attrs={'class': 'form-select'}),
            'foal_microchip': forms.TextInput(attrs={'class': 'form-input'}),
            'foaling_notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
            'status': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The mare picker lists mares only (the model says so too, but the
        # ActiveHorseFormMixin reset the queryset to every active horse).
        self.fields['mare'].queryset = Horse.objects.filter(
            is_active=True, sex=Horse.Sex.MARE,
        ).order_by('name')
        # The foal picker used to list every horse on the yard; a foal is
        # a colt/filly, a horse with a dam on record, or the one already
        # linked here.
        current_foal = getattr(self.instance, 'foal_id', None)
        foal_q = Q(sex__in=(Horse.Sex.COLT, Horse.Sex.FILLY)) | Q(dam__isnull=False)
        if current_foal:
            foal_q |= Q(pk=current_foal)
        self.fields['foal'].queryset = Horse.objects.filter(foal_q).order_by('-date_of_birth', 'name')
        self.fields['foal'].required = False

    def clean(self):
        cleaned_data = super().clean()
        date_covered = cleaned_data.get('date_covered')
        date_foal_due = cleaned_data.get('date_foal_due')
        # Auto-calculate foal due date if not provided
        if date_covered and not date_foal_due:
            cleaned_data['date_foal_due'] = date_covered + timedelta(days=340)
        mare = cleaned_data.get('mare')
        foal = cleaned_data.get('foal')
        if mare and foal and foal.pk == mare.pk:
            self.add_error('foal', 'The foal cannot be the mare herself.')
        foal_dob = cleaned_data.get('foal_dob')
        if date_covered and foal_dob and foal_dob < date_covered:
            self.add_error('foal_dob', 'The foal cannot be born before the mare was covered.')
        status = cleaned_data.get('status')
        if status == BreedingRecord.Status.BORN and not (foal or foal_dob):
            self.add_error(
                'status',
                'Add the foal\'s date of birth (or use Record foaling) before marking this as Born.',
            )
        return cleaned_data


class FoalingForm(forms.Form):
    """Record a foaling on an open breeding record.

    Creates the foal's horse record (linked to the mare as dam, the stallion
    as sire, and the mare's owners as owners) and closes the breeding record
    as Born. Pick an existing horse instead when the foal was already added.
    """

    foal_dob = forms.DateField(
        label='Date of birth',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}),
    )
    foal_name = forms.CharField(
        label='Foal name', max_length=200, required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Vivian 2026 foal'}),
    )
    foal_sex = forms.ChoiceField(
        label='Sex', choices=BreedingRecord.FoalSex.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    foal_colour = forms.ChoiceField(
        label='Colour', required=False,
        choices=[('', '— not yet known —')] + list(BreedingRecord.FoalColour.choices),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    foal_microchip = forms.CharField(
        label='Microchip', max_length=100, required=False,
        widget=forms.TextInput(attrs={'class': 'form-input'}),
    )
    existing_foal = forms.ModelChoiceField(
        label='Or link a horse already on the system', required=False,
        queryset=Horse.objects.none(), empty_label='— create a new horse record —',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    foaling_notes = forms.CharField(
        label='Foaling notes', required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3,
                                     'placeholder': 'Foaling ease, time of birth, vet attendance…'}),
    )

    def __init__(self, *args, record, **kwargs):
        super().__init__(*args, **kwargs)
        self.record = record
        # Horses that could be this foal: colts/fillies (or anything with a
        # dam on record) not already linked to another breeding record.
        self.fields['existing_foal'].queryset = Horse.objects.filter(
            Q(sex__in=(Horse.Sex.COLT, Horse.Sex.FILLY)) | Q(dam=record.mare_id),
        ).exclude(pk=record.mare_id).filter(birth_record__isnull=True).order_by('-date_of_birth', 'name')

    def clean_foal_dob(self):
        dob = self.cleaned_data['foal_dob']
        if self.record.date_covered and dob < self.record.date_covered:
            raise forms.ValidationError('The foal cannot be born before the mare was covered.')
        if dob > timezone.localdate():
            raise forms.ValidationError('The date of birth cannot be in the future.')
        return dob

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('existing_foal') and not (cleaned.get('foal_name') or '').strip():
            self.add_error('foal_name', 'Give the foal a name, or pick an existing horse record.')
        return cleaned
