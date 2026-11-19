"""Forms for training records and work types."""

from django import forms
from django.db.models import Q
from django.utils import timezone

from health.forms import ActiveHorseFormMixin

from .models import Activity, WorkType

DATE_WIDGET = forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'})


class ActivityRulesMixin:
    """The status rules of docs/TRAINING_MODULE_PLAN.md, section 5.12.

    - New work dated after today is Planned unless the user chose a status.
    - Done is never allowed on a date after today.
    - A reason goes with Did not work, and only with it. The form shows the
      reason only for Did not work, so a reason left from an earlier choice
      is cleared, not refused.
    - A rest work type has no effort.
    """

    def _setup_activity_fields(self):
        work_types = Q(is_active=True)
        current = getattr(self.instance, 'work_type_id', None)
        if current:
            work_types |= Q(pk=current)
        self.fields['work_type'].queryset = WorkType.objects.filter(work_types)
        self.fields['work_type'].empty_label = 'Choose the work…'
        self.fields['work_type'].label = 'Work'
        self.fields['reason'].required = False
        self.fields['reason'].choices = [('', 'Choose a reason…')] + list(Activity.Reason.choices)
        self.fields['reason'].help_text = 'Only for Did not work.'
        # Not sent while hidden (the caller's x-data holds ``status``).
        self.fields['reason'].widget.attrs[':disabled'] = "status !== 'not_done'"
        self.fields['effort'].required = False
        self.fields['effort'].label = 'Effort'
        self.fields['effort'].choices = [('', 'Usual for this work')] + [
            (value, label) for value, label in self.fields['effort'].choices if value != ''
        ]
        self.fields['minutes'].label = 'Minutes'
        self.fields['minutes'].help_text = "Leave empty to use the work's usual minutes."
        if not getattr(self.instance, 'pk', None):
            self.fields['date'].initial = timezone.localdate()
            self.fields['status'].initial = Activity.Status.DONE

    def clean(self):
        cleaned = super().clean()
        day = cleaned.get('date')
        status = cleaned.get('status')
        reason = cleaned.get('reason') or ''
        work_type = cleaned.get('work_type')
        today = timezone.localdate()
        is_new = not getattr(self.instance, 'pk', None)

        if day and status == Activity.Status.DONE and day > today:
            if is_new and 'status' not in self.changed_data:
                cleaned['status'] = status = Activity.Status.PLANNED
            else:
                self.add_error('status', 'Work cannot be Done on a date after today. Choose Planned.')

        if status == Activity.Status.NOT_DONE and not reason:
            self.add_error('reason', 'Choose why the horse did not work.')
        elif status and status != Activity.Status.NOT_DONE:
            cleaned['reason'] = ''

        if work_type and work_type.is_rest and cleaned.get('effort'):
            self.add_error('effort', 'A rest has no effort. Leave it empty.')
        return cleaned

    def fill_defaults(self, activity):
        """Minutes from the work type when none were given."""
        if activity.minutes is None and activity.status != Activity.Status.NOT_DONE:
            activity.minutes = activity.work_type.default_minutes
        return activity


ACTIVITY_WIDGETS = {
    'horse': forms.Select(attrs={'class': 'form-select'}),
    'date': DATE_WIDGET,
    'work_type': forms.Select(attrs={'class': 'form-select'}),
    'status': forms.Select(attrs={'class': 'form-select', 'x-model': 'status'}),
    'reason': forms.Select(attrs={'class': 'form-select'}),
    'minutes': forms.NumberInput(attrs={'class': 'form-input', 'min': 0, 'max': 600, 'inputmode': 'numeric'}),
    'effort': forms.Select(attrs={'class': 'form-select'}),
    'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
}


class ActivityForm(ActivityRulesMixin, ActiveHorseFormMixin, forms.ModelForm):
    """One piece of work for one horse."""

    class Meta:
        model = Activity
        fields = ['horse', 'date', 'work_type', 'status', 'reason', 'minutes', 'effort', 'notes']
        widgets = ACTIVITY_WIDGETS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_activity_fields()

    def save(self, commit=True):
        activity = self.fill_defaults(super().save(commit=False))
        if commit:
            activity.save()
        return activity


class BulkActivityForm(ActivityRulesMixin, forms.ModelForm):
    """The same work for many horses: the horses come from the bulk bar."""

    class Meta:
        model = Activity
        fields = ['date', 'work_type', 'status', 'reason', 'minutes', 'effort', 'notes']
        widgets = ACTIVITY_WIDGETS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_activity_fields()


class WorkTypeForm(forms.ModelForm):
    class Meta:
        model = WorkType
        fields = ['name', 'kind', 'effort', 'default_minutes', 'position']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'kind': forms.Select(attrs={'class': 'form-select'}),
            'effort': forms.Select(attrs={'class': 'form-select'}),
            'default_minutes': forms.NumberInput(attrs={'class': 'form-input', 'min': 0, 'max': 600}),
            'position': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
        }
        labels = {'default_minutes': 'Usual minutes'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['effort'].choices = [
            (value, 'None (rest only)' if value == WorkType.Effort.NONE else label)
            for value, label in WorkType.Effort.choices
        ]

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get('kind')
        effort = cleaned.get('effort')
        if kind == WorkType.Kind.REST:
            cleaned['effort'] = WorkType.Effort.NONE
        elif kind and effort == WorkType.Effort.NONE:
            self.add_error('effort', 'Choose an effort: Easy, Steady or Fast. Only a rest has none.')
        return cleaned
