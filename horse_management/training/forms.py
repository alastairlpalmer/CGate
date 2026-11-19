"""Forms for training records and work types."""

from django import forms
from django.db.models import Q
from django.utils import timezone

from health.forms import ActiveHorseFormMixin

from .models import Activity, HorseTraining, Lot, LotDay, LotWeekPlan, Rider, TrainingSettings, Venue, WorkType

DATE_WIDGET = forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'})
OWNER = 'owner'


def rider_choices(current=None):
    """The rider picker: The owner first (D1), then the active riders.
    An archived rider shows only on a record that already has it."""
    riders = Q(is_active=True)
    if current is not None:
        riders |= Q(pk=current)
    return [('', 'No rider'), (OWNER, 'The owner')] + [
        (str(r.pk), r.name) for r in Rider.objects.filter(riders)
    ]


def who_value(rider_id, is_owner):
    return OWNER if is_owner else (str(rider_id) if rider_id else '')


def parse_who(value):
    """(rider, is_owner) from the picker's value."""
    if value == OWNER:
        return None, True
    if value:
        return Rider.objects.filter(pk=value).first(), False
    return None, False


def venue_queryset(current=None):
    venues = Q(is_active=True)
    if current is not None:
        venues |= Q(pk=current)
    return Venue.objects.filter(venues)


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
        instance = self.instance
        self.fields['who'] = forms.ChoiceField(
            choices=rider_choices(getattr(instance, 'rider_id', None)), required=False, label='Rider',
            widget=forms.Select(attrs={'class': 'form-select'}),
        )
        if getattr(instance, 'pk', None):
            self.fields['who'].initial = who_value(instance.rider_id, instance.rider_is_owner)
        self.fields['venue'].queryset = venue_queryset(getattr(instance, 'venue_id', None))
        self.fields['venue'].empty_label = 'No venue'
        self.fields['venue'].required = False
        self.fields['gear'].help_text = 'For example: martingale, boots.'
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
        """Minutes from the work type when none were given; the rider from the picker."""
        if activity.minutes is None and activity.status != Activity.Status.NOT_DONE:
            activity.minutes = activity.work_type.default_minutes
        if 'who' in self.cleaned_data:
            activity.rider, activity.rider_is_owner = parse_who(self.cleaned_data['who'])
        return activity


ACTIVITY_WIDGETS = {
    'venue': forms.Select(attrs={'class': 'form-select'}),
    'gear': forms.TextInput(attrs={'class': 'form-input'}),
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
        fields = ['horse', 'date', 'work_type', 'status', 'reason', 'minutes', 'effort', 'venue', 'gear', 'notes']
        widgets = ACTIVITY_WIDGETS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_activity_fields()
        horse_id = self.initial.get('horse') if not self.instance.pk else None
        if horse_id:
            # A new record for one horse starts with its default rider and gear (R5).
            row = HorseTraining.objects.filter(horse_id=horse_id).first()
            if row:
                self.fields['who'].initial = who_value(row.default_rider_id, row.default_rider_is_owner)
                self.fields['gear'].initial = row.default_gear

    def save(self, commit=True):
        activity = self.fill_defaults(super().save(commit=False))
        if commit:
            activity.save()
        return activity


class BulkActivityForm(ActivityRulesMixin, forms.ModelForm):
    """The same work for many horses: the horses come from the bulk bar.
    With no rider or gear chosen, each horse takes its own default."""

    class Meta:
        model = Activity
        fields = ['date', 'work_type', 'status', 'reason', 'minutes', 'effort', 'venue', 'gear', 'notes']
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
    review_on = forms.DateField(
        required=False, widget=DATE_WIDGET, label='Review on',
        help_text='For Resting or Box rest: the date to check the horse again. '
                  'Cleared when the horse goes back in work.',
    )
    notes = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
    )
    default_who = forms.ChoiceField(
        required=False, label='Usual rider', widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='New work on the board takes this rider. A change for one day does not change it.',
    )
    default_gear = forms.CharField(
        required=False, max_length=120, label='Usual gear',
        widget=forms.TextInput(attrs={'class': 'form-input'}),
    )
    days_per_week = forms.IntegerField(
        required=False, min_value=1, max_value=7, label='Exercise days a week',
        widget=forms.NumberInput(attrs={'class': 'form-input', 'min': 1, 'max': 7}),
        help_text='For example 5 for a ridden livery. The card shows "3 of 5 days this week".',
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
        current_rider = self.initial.get('default_who')
        self.fields['default_who'].choices = rider_choices(
            int(current_rider) if current_rider and current_rider.isdigit() else None,
        )


class BoardSettingsForm(forms.ModelForm):
    class Meta:
        model = TrainingSettings
        fields = ['board_source', 'no_work_days']
        widgets = {
            'board_source': forms.RadioSelect,
            'no_work_days': forms.NumberInput(attrs={'class': 'form-input w-24', 'min': 1, 'max': 60}),
        }
        labels = {'board_source': 'Start a new day', 'no_work_days': '"No work" after (days)'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        labels = dict(TrainingSettings.BoardSource.choices)
        self.fields['board_source'].choices = [(value, labels[value]) for value in TrainingSettings.OFFERED_SOURCES]


class LotDayForm(forms.ModelForm):
    """The lot's work, time and notes for one date (the board's Change)."""

    class Meta:
        model = LotDay
        fields = ['work_type', 'venue', 'start_time', 'notes']
        widgets = {
            'work_type': forms.Select(attrs={'class': 'form-select'}),
            'venue': forms.Select(attrs={'class': 'form-select'}),
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
        self.fields['venue'].queryset = venue_queryset(self.instance.venue_id)
        self.fields['venue'].empty_label = 'No venue'



# ─── Phase 3: venues, riders, the week plan ──────────────────────────

class VenueForm(forms.ModelForm):
    class Meta:
        model = Venue
        fields = ['name', 'surface', 'position']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'For example: Woodchip gallop'}),
            'surface': forms.Select(attrs={'class': 'form-select'}),
            'position': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
        }


class RiderForm(forms.ModelForm):
    class Meta:
        model = Rider
        fields = ['name', 'user', 'position']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'user': forms.Select(attrs={'class': 'form-select'}),
            'position': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
        }
        labels = {'user': 'Login'}
        help_texts = {'user': 'Optional. A rider with a login sees their own horses at the top of the board.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model
        users = Q(is_active=True, rider__isnull=True)
        if self.instance.user_id:
            users |= Q(pk=self.instance.user_id)
        self.fields['user'].queryset = get_user_model().objects.filter(users).order_by('first_name', 'username')
        self.fields['user'].empty_label = 'No login'
        self.fields['user'].required = False


class WeekPlanForm(forms.Form):
    """Seven days for one lot: the usual work (or no lot) and the venue."""

    USUAL = ''
    NO_LOT = 'none'

    def __init__(self, *args, lot, **kwargs):
        super().__init__(*args, **kwargs)
        self.lot = lot
        plans = {p.weekday: p for p in lot.week_plan.all()}
        work_types = list(WorkType.objects.active())
        for day, name in enumerate(LotWeekPlan.WEEKDAYS):
            plan = plans.get(day)
            current_work = plan.work_type if plan else None
            choices = [(self.USUAL, f'Usual: {lot.usual_work_type.name}'), (self.NO_LOT, 'No lot')]
            choices += [(str(w.pk), w.name) for w in work_types]
            if current_work is not None and current_work not in work_types:
                choices.append((str(current_work.pk), f'{current_work.name} (archived)'))
            self.fields[f'work_{day}'] = forms.ChoiceField(
                choices=choices, required=False, label=name,
                widget=forms.Select(attrs={'class': 'form-select'}),
                initial=self.NO_LOT if plan and plan.no_lot else (str(current_work.pk) if current_work else self.USUAL),
            )
            self.fields[f'venue_{day}'] = forms.ModelChoiceField(
                queryset=venue_queryset(plan.venue_id if plan else None), required=False,
                empty_label='No venue', label=f'{name} venue',
                widget=forms.Select(attrs={'class': 'form-select'}),
                initial=plan.venue_id if plan else None,
            )

    def days(self):
        return [(name, self[f'work_{day}'], self[f'venue_{day}']) for day, name in enumerate(LotWeekPlan.WEEKDAYS)]

    def save(self):
        for day in range(7):
            work = self.cleaned_data.get(f'work_{day}') or self.USUAL
            venue = self.cleaned_data.get(f'venue_{day}')
            no_lot = work == self.NO_LOT
            work_type = WorkType.objects.filter(pk=work).first() if work not in (self.USUAL, self.NO_LOT) else None
            if not no_lot and work_type is None and venue is None:
                LotWeekPlan.objects.filter(lot=self.lot, weekday=day).delete()
                continue
            LotWeekPlan.objects.update_or_create(
                lot=self.lot, weekday=day,
                defaults={'work_type': None if no_lot else work_type, 'venue': None if no_lot else venue, 'no_lot': no_lot},
            )
