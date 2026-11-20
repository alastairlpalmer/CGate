"""The pedigree pop-up (plan §7.2).

Six boxes: Sire, Dam and Dam's sire first (the racing minimum), then
Sire's sire, Sire's dam and Dam's dam. Type "Unknown" for a parent that
is known to be unknown. A box that is not changed is not written, so the
form never moves a shared record by accident.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.forms import HorseForm
from core.identity import normalise_country

from .models import PedigreeHorse
from .names import is_unknown
from .services import GRANDPARENTS, POSITION_SEX, other_users_of, resolve

POSITIONS = [
    ('S', 'Sire'), ('D', 'Dam'), ('DS', "Dam's sire"),
    ('SS', "Sire's sire"), ('SD', "Sire's dam"), ('DD', "Dam's dam"),
]
FIRST = ('S', 'D', 'DS')


def _initial_text(record, letter):
    """The box text for a record's sire or dam: its label, or 'Unknown'."""
    if record is None:
        return ''
    parent = getattr(record, letter)
    if parent is not None:
        return parent.label
    return 'Unknown' if getattr(record, f'{letter}_unknown') else ''


class _ChoiceMixin:
    """A box whose name needs a person: several records, or a record of
    the other sex. The template shows the records (if any) and New."""

    def _ask(self, code, text, res):
        if res.wrong_sex:
            self.wrong_sex[code] = True
            self.add_error(code, res.wrong_sex_message)
        else:
            self.choices[code] = res.choices
            self.add_error(code, f'{text} matches a record already on file. Choose it below, or choose New.')


class PedigreeForm(_ChoiceMixin, forms.Form):
    confirm_checked = forms.BooleanField(
        required=False, label='Yes, change the checked record',
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )

    def __init__(self, *args, horse, record, **kwargs):
        super().__init__(*args, **kwargs)
        self.horse = horse
        self.record = record
        sire, dam = record.sire, record.dam
        # The record each box writes on, and that record's current value.
        self.owner_of = {'S': record, 'D': record, 'SS': sire, 'SD': sire, 'DS': dam, 'DD': dam}
        self.current = {
            'S': sire, 'D': dam,
            'SS': sire.sire if sire else None, 'SD': sire.dam if sire else None,
            'DS': dam.sire if dam else None, 'DD': dam.dam if dam else None,
        }
        letters = {'S': 'sire', 'D': 'dam'}
        for code, label in POSITIONS:
            letter = letters[code[-1]]
            initial = _initial_text(self.owner_of[code], letter)
            self.fields[code] = forms.CharField(
                required=False, label=label, max_length=220, initial=initial,
                widget=forms.TextInput(attrs={
                    'class': 'form-input', 'autocomplete': 'off', 'spellcheck': 'false',
                    'list': 'pedigree-males' if POSITION_SEX[code] == 'M' else 'pedigree-females',
                }),
            )
            self.fields[f'{code}_choice'] = forms.CharField(required=False, widget=forms.HiddenInput)
        self.choices = {}
        self.wrong_sex = {}
        self.resolutions = {}
        # "Changing this changes it for N other horses" under the grandparents.
        self.shared = {
            'S': other_users_of(sire, horse) if sire else 0,
            'D': other_users_of(dam, horse) if dam else 0,
        }

    def rows(self):
        """(code, field, choices, shared count) for the template, first three first."""
        out = []
        for code, _label in POSITIONS:
            parent = code[0] if len(code) == 2 else ''
            out.append({
                'code': code, 'field': self[code], 'choices': self.choices.get(code, []),
                'wrong_sex': code in self.wrong_sex,
                'shared': self.shared.get(parent, 0) if parent else 0,
                'first': code in FIRST,
            })
        return out

    def clean(self):
        cleaned = super().clean()
        for code, _label in POSITIONS:
            text = (cleaned.get(code) or '').strip()
            if text == (self.fields[code].initial or '').strip():
                continue  # unchanged: not written
            res = resolve(text, POSITION_SEX[code], chosen=cleaned.get(f'{code}_choice') or None,
                          current=self.current[code])
            if res.needs_choice:
                self._ask(code, text, res)
                continue
            self.resolutions[code] = res

        for code, (parent_code, _letter) in GRANDPARENTS.items():
            if code not in self.resolutions or self.resolutions[code].empty:
                continue
            parent_res = self.resolutions.get(parent_code)
            parent_now = self.current[parent_code]
            parent_after = (parent_res.record or parent_res.new) if parent_res else parent_now
            if parent_after is None or (parent_res is not None and parent_res.unknown):
                label = 'sire' if parent_code == 'S' else 'dam'
                self.add_error(code, f'Fill in the {label} first.')
            elif (parent_res is None and parent_now is not None and parent_now.checked
                  and not cleaned.get('confirm_checked')):
                self.add_error(code, f'{parent_now.label} is marked as checked. '
                                     'Tick the box below to change it.')
                self.needs_confirm = True
        return cleaned

    needs_confirm = False


class RecordsIdentityForm(HorseForm):
    """The identity half of the Fill in records pane (plan §7.5).

    A subset of HorseForm, so the rules are the same as on the horse
    forms: the suffix split, the foaling year, the tidy numbers.
    """

    height = None  # not in the pane, so never cleared by it

    class Meta(HorseForm.Meta):
        fields = [
            'registered_name', 'country_of_birth', 'date_of_birth', 'breed',
            'microchip', 'ueln', 'passport_number', 'passport_issuer',
        ]


def _box(label, sex, initial):
    return forms.CharField(
        required=False, label=label, max_length=220, initial=initial,
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'autocomplete': 'off', 'spellcheck': 'false',
            'list': 'pedigree-males' if sex == 'M' else 'pedigree-females',
        }),
    )


class RecordEditForm(_ChoiceMixin, forms.ModelForm):
    """A shared record that is not on the yard: its name, suffix, year,
    sex, colour, and its sire and dam (Pedigree records tab, plan §7.5).

    A record that is in more than one pedigree asks again before a save,
    because the change shows on every one of them.
    """

    confirm_shared = forms.BooleanField(
        required=False, label='Yes, change it in all of them',
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )

    class Meta:
        model = PedigreeHorse
        fields = ['name', 'country', 'year_of_birth', 'sex', 'colour', 'checked']
        labels = {'country': 'Suffix (country)', 'year_of_birth': 'Year foaled',
                  'checked': 'Checked against a passport or a stud book'}
        help_texts = {'checked': ''}
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'off'}),
            'country': forms.TextInput(attrs={
                'class': 'form-input uppercase', 'list': 'country-suggestions', 'maxlength': '5',
                'autocomplete': 'off'}),
            'year_of_birth': forms.NumberInput(attrs={'class': 'form-input', 'inputmode': 'numeric'}),
            'sex': forms.Select(attrs={'class': 'form-select'}),
            'colour': forms.TextInput(attrs={'class': 'form-input'}),
            'checked': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        record = self.instance
        self.fields['S'] = _box('Sire', 'M', _initial_text(record, 'sire'))
        self.fields['D'] = _box('Dam', 'F', _initial_text(record, 'dam'))
        for code in ('S', 'D'):
            self.fields[f'{code}_choice'] = forms.CharField(required=False, widget=forms.HiddenInput)
        self.used_in = record.progeny().count() if record.pk else 0
        self.choices = {}
        self.wrong_sex = {}
        self.resolutions = {}
        self.needs_confirm = False

    def rows(self):
        return [{'code': code, 'field': self[code], 'choices': self.choices.get(code, []),
                 'wrong_sex': code in self.wrong_sex, 'shared': 0, 'first': True} for code in ('S', 'D')]

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if not name or is_unknown(name):
            raise forms.ValidationError('Type the registered name.')
        return name

    def clean_country(self):
        return normalise_country(self.cleaned_data.get('country'))

    def clean_year_of_birth(self):
        year = self.cleaned_data.get('year_of_birth')
        if year is not None and not 1700 <= year <= timezone.localdate().year:
            raise forms.ValidationError('Type a year from 1700 to this year.')
        return year

    def clean(self):
        cleaned = super().clean()
        record = self.instance
        for code, letter in (('S', 'sire'), ('D', 'dam')):
            text = (cleaned.get(code) or '').strip()
            if text == (self.fields[code].initial or '').strip():
                continue
            res = resolve(text, POSITION_SEX[code], chosen=cleaned.get(f'{code}_choice') or None,
                          current=getattr(record, letter))
            if res.needs_choice:
                self._ask(code, text, res)
                continue
            if res.record is not None and res.record.pk == record.pk:
                self.add_error(code, 'A horse cannot be its own parent.')
                continue
            self.resolutions[code] = res

        sex = cleaned.get('sex')
        if record.pk and sex == PedigreeHorse.Sex.FEMALE and record.progeny_as_sire.exists():
            self.add_error('sex', 'This record is the sire of other horses, so it cannot be female.')
        if record.pk and sex == PedigreeHorse.Sex.MALE and record.progeny_as_dam.exists():
            self.add_error('sex', 'This record is the dam of other horses, so it cannot be male.')

        changed = [f for f in self.changed_data if f != 'confirm_shared' and not f.endswith('_choice')]
        if self.used_in > 1 and changed and not cleaned.get('confirm_shared'):
            self.needs_confirm = True
            self.add_error(None, f'This record is in {self.used_in} pedigrees. Tick the box to change it '
                                 'in all of them.')
        return cleaned

    def save(self, commit=True, user=None):
        record = super().save(commit=False)
        if 'checked' in self.changed_data:
            record.checked_at = timezone.now() if record.checked else None
            record.checked_by = (user if record.checked and getattr(user, 'is_authenticated', False)
                                 else None)
        if commit:
            record.save()
        return record


def validation_messages(error: ValidationError):
    if hasattr(error, 'message_dict'):
        return [m for msgs in error.message_dict.values() for m in msgs]
    return list(error.messages)
