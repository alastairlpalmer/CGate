"""The pedigree pop-up (plan §7.2).

Six boxes: Sire, Dam and Dam's sire first (the racing minimum), then
Sire's sire, Sire's dam and Dam's dam. Type "Unknown" for a parent that
is known to be unknown. A box that is not changed is not written, so the
form never moves a shared record by accident.
"""

from django import forms
from django.core.exceptions import ValidationError

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


class PedigreeForm(forms.Form):
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
                self.choices[code] = res.choices
                self.add_error(code, f'{text} matches a record already on file. Choose it below, or choose New.')
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


def validation_messages(error: ValidationError):
    if hasattr(error, 'message_dict'):
        return [m for msgs in error.message_dict.values() for m in msgs]
    return list(error.messages)
