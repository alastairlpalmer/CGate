"""Forms for training records and work types."""

from django import forms
from django.db.models import Q
from django.utils import timezone

from health.forms import ActiveHorseFormMixin

from .models import Activity, HorseTraining, Lot, LotDay, TrainingSettings, WorkType

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


# ─── Phase 2a: lots, training status, the board setting ─────────────


class LotForm(forms.ModelForm):
    class Meta:
        model = Lot
        fields = ['name', 'usual_work_type', 'usual_time', 'position', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'For example: 1st lot'}),
            'usual_work_type': forms.Select(attrs={'class': 'form-select'}),
            'usual_time': forms.TimeInput(format='%H:%M', attrs={'class': 'form-input', 'type': 'time'}),
            'position': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }
        labels = {'usual_work_type': 'Usual work', 'usual_time': 'Usual time', 'position': 'Order in the day'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        work_types = Q(is_active=True)
        if self.instance.usual_work_type_id:
            work_types |= Q(pk=self.instance.usual_work_type_id)
        self.fields['usual_work_type'].queryset = WorkType.objects.filter(work_types)
        self.fields['usual_work_type'].empty_label = 'Choose the work…'


class HorseTrainingForm(forms.Form):
    """Training status and standing lot for one horse (the horse card's Change).

    A plain form, not a ModelForm: opening it must not make a row (5.3).
    """

    status = forms.ChoiceField(
        choices=HorseTraining.Status.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Training status',
    )
    status_since = forms.DateField(
        widget=DATE_WIDGET, label='From',
        help_text='The date this status started. A new status starts on the date you give.',
    )
    lot = forms.ModelChoiceField(
        queryset=Lot.objects.none(), required=False, empty_label='No lot',
        widget=forms.Select(attrs={'class': 'form-select'}), label='Lot',
        help_text='The group the horse usually works with.',
    )
    notes = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
    )
    update_boards = forms.BooleanField(
        required=False, initial=True, label='Also change the lot boards from this date',
        help_text=('Off the board: the planned work on this date becomes Did not work, '
                   'and later planned work is removed. Back in work: the horse goes on '
                   "today's board in its lot."),
    )

    def __init__(self, *args, current_lot=None, **kwargs):
        super().__init__(*args, **kwargs)
        lots = Q(is_active=True)
        if current_lot is not None:
            lots |= Q(pk=current_lot.pk)
        self.fields['lot'].queryset = Lot.objects.filter(lots)


class BoardSettingsForm(forms.ModelForm):
    class Meta:
        model = TrainingSettings
        fields = ['board_source']
        widgets = {'board_source': forms.RadioSelect}
        labels = {'board_source': 'Start a new day'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        offered = TrainingSettings.OFFERED_SOURCES
        self.fields['board_source'].choices = [
            (value, label) for value, label in TrainingSettings.BoardSource.choices if value in offered
        ]


class LotDayForm(forms.ModelForm):
    """The lot's work, time and notes for one date (the board's Change)."""

    class Meta:
        model = LotDay
        fields = ['work_type', 'start_time', 'notes']
        widgets = {
            'work_type': forms.Select(attrs={'class': 'form-select'}),
            'start_time': forms.TimeInput(format='%H:%M', attrs={'class': 'form-input', 'type': 'time'}),
            'notes': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }
        labels = {'work_type': 'Work', 'start_time': 'Time'}
        help_texts = {'work_type': "Horses with their own work for the day keep it."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        work_types = Q(is_active=True)
        if self.instance.work_type_id:
            work_types |= Q(pk=self.instance.work_type_id)
        self.fields['work_type'].queryset = WorkType.objects.filter(work_types)
        self.fields['work_type'].empty_label = None
