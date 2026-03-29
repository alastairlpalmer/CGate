"""
Forms for health app.
"""

from datetime import timedelta

from django import forms

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


class VaccinationForm(forms.ModelForm):
    class Meta:
        model = Vaccination
        fields = [
            'horse', 'vaccination_type', 'date_given', 'next_due_date',
            'vet', 'batch_number', 'notes'
        ]
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'vaccination_type': forms.Select(attrs={'class': 'form-select'}),
            'date_given': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'next_due_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'vet': forms.Select(attrs={'class': 'form-select'}),
            'batch_number': forms.TextInput(attrs={'class': 'form-input'}),
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

    def clean(self):
        cleaned_data = super().clean()
        date_given = cleaned_data.get('date_given')
        next_due = cleaned_data.get('next_due_date')
        if date_given and next_due and next_due <= date_given:
            self.add_error('next_due_date', "Next due date must be after the date given.")
        return cleaned_data


class FarrierVisitForm(forms.ModelForm):
    class Meta:
        model = FarrierVisit
        fields = [
            'horse', 'date', 'service_provider', 'work_done',
            'next_due_date', 'cost', 'notes'
        ]
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'service_provider': forms.Select(attrs={'class': 'form-select'}),
            'work_done': forms.Select(attrs={'class': 'form-select'}),
            'next_due_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def clean(self):
        cleaned_data = super().clean()
        visit_date = cleaned_data.get('date')
        next_due = cleaned_data.get('next_due_date')
        if visit_date and next_due and next_due <= visit_date:
            self.add_error('next_due_date', "Next due date must be after the visit date.")
        return cleaned_data


class VaccinationTypeForm(forms.ModelForm):
    class Meta:
        model = VaccinationType
        fields = ['name', 'interval_months', 'reminder_days_before', 'description', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'interval_months': forms.NumberInput(attrs={'class': 'form-input'}),
            'reminder_days_before': forms.NumberInput(attrs={'class': 'form-input'}),
            'description': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }


class WormingTreatmentForm(forms.ModelForm):
    class Meta:
        model = WormingTreatment
        fields = [
            'horse', 'date', 'product_name', 'active_ingredient',
            'dose', 'administered_by', 'notes'
        ]
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'product_name': forms.TextInput(attrs={'class': 'form-input'}),
            'active_ingredient': forms.TextInput(attrs={'class': 'form-input'}),
            'dose': forms.TextInput(attrs={'class': 'form-input'}),
            'administered_by': forms.TextInput(attrs={'class': 'form-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class WormEggCountForm(forms.ModelForm):
    class Meta:
        model = WormEggCount
        fields = ['horse', 'date', 'count', 'lab_name', 'sample_type', 'notes']
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'count': forms.NumberInput(attrs={'class': 'form-input'}),
            'lab_name': forms.TextInput(attrs={'class': 'form-input'}),
            'sample_type': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class MedicalConditionForm(forms.ModelForm):
    class Meta:
        model = MedicalCondition
        fields = ['horse', 'name', 'diagnosed_date', 'status', 'notes']
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'diagnosed_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class VetVisitForm(forms.ModelForm):
    class Meta:
        model = VetVisit
        fields = [
            'horse', 'date', 'vet', 'reason', 'diagnosis',
            'treatment', 'follow_up_date', 'cost', 'notes'
        ]
        widgets = {
            'horse': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'vet': forms.Select(attrs={'class': 'form-select'}),
            'reason': forms.TextInput(attrs={'class': 'form-input'}),
            'diagnosis': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'treatment': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'follow_up_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01'}),
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

class BulkVaccinationForm(forms.ModelForm):
    class Meta:
        model = Vaccination
        fields = ['vaccination_type', 'date_given', 'next_due_date', 'vet', 'batch_number', 'notes']
        widgets = {
            'vaccination_type': forms.Select(attrs={'class': 'form-select'}),
            'date_given': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'next_due_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'vet': forms.Select(attrs={'class': 'form-select'}),
            'batch_number': forms.TextInput(attrs={'class': 'form-input'}),
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
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'service_provider': forms.Select(attrs={'class': 'form-select'}),
            'work_done': forms.Select(attrs={'class': 'form-select'}),
            'next_due_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class BulkWormingTreatmentForm(forms.ModelForm):
    class Meta:
        model = WormingTreatment
        fields = ['date', 'product_name', 'active_ingredient', 'dose', 'administered_by', 'notes']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'product_name': forms.TextInput(attrs={'class': 'form-input'}),
            'active_ingredient': forms.TextInput(attrs={'class': 'form-input'}),
            'dose': forms.TextInput(attrs={'class': 'form-input'}),
            'administered_by': forms.TextInput(attrs={'class': 'form-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class BulkWormEggCountForm(forms.ModelForm):
    class Meta:
        model = WormEggCount
        fields = ['date', 'count', 'lab_name', 'sample_type', 'notes']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'count': forms.NumberInput(attrs={'class': 'form-input'}),
            'lab_name': forms.TextInput(attrs={'class': 'form-input'}),
            'sample_type': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class BulkVetVisitForm(forms.ModelForm):
    class Meta:
        model = VetVisit
        fields = ['date', 'vet', 'reason', 'diagnosis', 'treatment', 'follow_up_date', 'cost', 'notes']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'vet': forms.Select(attrs={'class': 'form-select'}),
            'reason': forms.TextInput(attrs={'class': 'form-input'}),
            'diagnosis': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'treatment': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
            'follow_up_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'cost': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class BulkMedicalConditionForm(forms.ModelForm):
    class Meta:
        model = MedicalCondition
        fields = ['name', 'diagnosed_date', 'status', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'diagnosed_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }


class BreedingRecordForm(forms.ModelForm):
    class Meta:
        model = BreedingRecord
        fields = [
            'mare', 'stallion_name', 'date_covered',
            'date_scanned_14_days', 'date_scanned_heartbeat', 'date_foal_due',
            'foal', 'foal_dob', 'foal_sex', 'foal_colour', 'foal_microchip',
            'foaling_notes', 'status'
        ]
        widgets = {
            'mare': forms.Select(attrs={'class': 'form-select'}),
            'stallion_name': forms.TextInput(attrs={'class': 'form-input'}),
            'date_covered': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'date_scanned_14_days': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'date_scanned_heartbeat': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'date_foal_due': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'foal': forms.Select(attrs={'class': 'form-select'}),
            'foal_dob': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'foal_sex': forms.Select(attrs={'class': 'form-select'}),
            'foal_colour': forms.Select(attrs={'class': 'form-select'}),
            'foal_microchip': forms.TextInput(attrs={'class': 'form-input'}),
            'foaling_notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
            'status': forms.Select(attrs={'class': 'form-select'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        date_covered = cleaned_data.get('date_covered')
        date_foal_due = cleaned_data.get('date_foal_due')
        # Auto-calculate foal due date if not provided
        if date_covered and not date_foal_due:
            cleaned_data['date_foal_due'] = date_covered + timedelta(days=340)
        return cleaned_data
