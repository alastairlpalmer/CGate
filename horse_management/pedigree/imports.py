"""Proposed changes: spreadsheet import and back-fill (plan §6.5, §7.6, §7.7).

Every route except typing writes proposals, not data. The rules:

- Pre-tick a change only when the current value is empty.
- A pedigree name that fits more than one record waits for a person to
  choose (§6.4). It is never pre-ticked.
- On Apply, check each current value again. If it changed since the
  proposal, skip that change and say so.
- Apply runs in one transaction. A row that fails does not stop the
  other rows. One audit event records the batch.
"""

from __future__ import annotations

from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Horse

from . import backfill as text_rules
from .matching import find_horse
from .models import ImportBatch, ImportRow, PedigreeHorse, ProposedChange
from .services import POSITION_SEX, Resolution, pedigree_for, resolve, set_parents
from .sheet import Row

# (key in the row values, label, Horse field). 'foaled' and 'height' are
# special: they write two fields, or a converted value.
HORSE_TARGETS = [
    ('name', 'Yard name', 'name'),
    ('registered_name', 'Registered name', 'registered_name'),
    ('country', 'Country', 'country_of_birth'),
    ('foaled', 'Foaled', 'date_of_birth'),
    ('sex', 'Sex', 'sex'),
    ('colour', 'Colour', 'color'),
    ('breed', 'Breed', 'breed'),
    ('microchip', 'Microchip', 'microchip'),
    ('ueln', 'UELN', 'ueln'),
    ('passport_number', 'Passport number', 'passport_number'),
    ('passport_issuer', 'Passport issuer', 'passport_issuer'),
    ('markings', 'Markings', 'markings'),
    ('height', 'Height', 'height_inches'),
]
PEDIGREE_TARGETS = [('S', 'Sire'), ('D', 'Dam'), ('DS', "Dam's sire")]
SOURCE_FOR_BATCH = {
    ImportBatch.Source.SPREADSHEET: PedigreeHorse.Source.SPREADSHEET,
    ImportBatch.Source.BACKFILL: PedigreeHorse.Source.BACKFILL,
}


# ---------------------------------------------------------------------------
# Values: the stored text (compared on Apply) and the text people read
# ---------------------------------------------------------------------------

def _foaled_value(day, precision):
    return f'{day.isoformat()}|{precision}' if day else ''


def _foaled_display(value):
    if not value:
        return ''
    iso, _sep, precision = value.partition('|')
    day = date.fromisoformat(iso)
    if precision == Horse.DobPrecision.EXACT:
        return f'{day.day} {day:%b %Y}'
    if precision == Horse.DobPrecision.ESTIMATED:
        return f'c. {day.year}'
    return str(day.year)


def _height_display(inches):
    if not inches:
        return ''
    return f'{int(inches) // 4}.{int(inches) % 4} hh'


def _choice_display(key, value):
    if key == 'sex':
        return dict(Horse.Sex.choices).get(value, value)
    if key == 'colour':
        return dict(Horse.Color.choices).get(value, value)
    return value


def horse_value(horse: Horse | None, key: str) -> str:
    """The stored text of one target on a horse (empty for a new horse)."""
    if horse is None:
        return ''
    if key == 'foaled':
        return _foaled_value(horse.date_of_birth, horse.dob_precision)
    if key == 'height':
        return str(horse.height_inches or '')
    field = dict((k, f) for k, _label, f in HORSE_TARGETS)[key]
    return str(getattr(horse, field) or '')


def display(key: str, value: str) -> str:
    if key == 'foaled':
        return _foaled_display(value)
    if key == 'height':
        return _height_display(value)
    if key in ('sex', 'colour'):
        return _choice_display(key, value)
    return value


def row_value(values: dict, key: str) -> str:
    """The stored text of one target from a row's tidied values."""
    if key not in values:
        return ''
    value = values[key]
    if key == 'foaled':
        day, precision = value
        if isinstance(day, str):
            day = date.fromisoformat(day)
        return _foaled_value(day, precision)
    return str(value)


def _parent_record(record: PedigreeHorse | None, code: str):
    """The record now in a pedigree position (S, D or DS), or None."""
    if record is None:
        return None
    if code == 'S':
        return record.sire
    if code == 'D':
        return record.dam
    return record.dam.sire if record.dam else None


def pedigree_text(record: PedigreeHorse | None, code: str) -> str:
    """What the pedigree box shows now: a label, 'Unknown' or ''."""
    if record is None:
        return ''
    owner = record if code in ('S', 'D') else record.dam
    if owner is None:
        return ''
    letter = 'sire' if code in ('S', 'DS') else 'dam'
    parent = getattr(owner, letter)
    if parent is not None:
        return parent.label
    return 'Unknown' if getattr(owner, f'{letter}_unknown') else ''


def _same_name(current: str, proposed: str, record) -> bool:
    if not current:
        return False
    if current == proposed:
        return True
    return record is not None and proposed in (record.label, record.short_label, record.name)


# ---------------------------------------------------------------------------
# Building the proposals
# ---------------------------------------------------------------------------

def _json_values(values: dict) -> dict:
    out = dict(values)
    if 'foaled' in out:
        day, precision = out['foaled']
        out['foaled'] = [day.isoformat() if hasattr(day, 'isoformat') else day, precision]
    return out


def build_changes(row: ImportRow) -> list[ProposedChange]:
    """(Re)make the proposed changes of one row against ``row.horse``."""
    row.changes.all().delete()
    horse = row.horse
    values = row.values
    new_horse = horse is None
    made = []
    for key, label, _field in HORSE_TARGETS:
        if key not in values:
            continue
        if key == 'name' and new_horse:
            continue  # a new horse's name is the row's own label
        proposed = row_value(values, key)
        current = horse_value(horse, key)
        if proposed == current:
            continue
        made.append(ProposedChange(
            row=row, target=key, label=label,
            current_value=current, current_display=display(key, current),
            proposed_value=proposed, proposed_display=display(key, proposed),
            accept=not current,
        ))

    record = PedigreeHorse.objects.filter(horse=horse).select_related(
        'sire', 'dam', 'dam__sire').first() if horse else None
    for code, label in PEDIGREE_TARGETS:
        text = (values.get(code) or '').strip()
        if not text:
            continue
        now = _parent_record(record, code)
        current = pedigree_text(record, code)
        if _same_name(current, text, now):
            continue
        res = resolve(text, POSITION_SEX[code], current=now)
        change = ProposedChange(
            row=row, target=f'pedigree.{code}', label=label,
            current_value=current, current_display=current,
            proposed_value=text, proposed_display=text,
        )
        if res.unknown:
            change.choice = 'unknown'
        elif res.record is not None:
            change.choice = str(res.record.pk)
            change.proposed_display = res.record.label
        elif res.choices:
            change.choice_ids = [r.pk for r in res.choices]
        elif res.wrong_sex:
            change.note = res.wrong_sex_message[:300]
        else:
            change.choice = 'new'
        change.accept = not current and bool(change.choice)
        made.append(change)
    ProposedChange.objects.bulk_create(made)
    return made


def add_row(batch: ImportBatch, number: int, values: dict, problems=(), *, horse=None,
            match=None) -> ImportRow:
    row = ImportRow(batch=batch, number=number, values=_json_values(values), problems=list(problems))
    if horse is not None:
        row.horse = horse
        row.match_how = 'backfill'
    elif match is not None:
        row.horse = match.horse
        row.match_how = match.how
        row.match_note = match.note
        row.possible_ids = [h.pk for h in match.possible]
        row.create_new = match.horse is None and not match.possible
    row.save()
    build_changes(row)
    return row


def batch_from_sheet(rows: list[Row], *, user, file_name: str, placement=None) -> ImportBatch:
    batch = ImportBatch.objects.create(
        source=ImportBatch.Source.SPREADSHEET, file_name=file_name[:255],
        created_by=user if getattr(user, 'is_authenticated', False) else None,
        **(placement or {}),
    )
    for sheet_row in rows:
        values = sheet_row.values
        if not values.get('name') and not values.get('registered_name'):
            ImportRow.objects.create(batch=batch, number=sheet_row.number, values=_json_values(values),
                                     problems=sheet_row.problems, result='Left out: no name.')
            continue
        foaled = values.get('foaled')
        match = find_horse(
            microchip=values.get('microchip', ''), ueln=values.get('ueln', ''),
            registered_name=values.get('registered_name', ''), country=values.get('country', ''),
            year=foaled[0].year if foaled else None, yard_name=values.get('name', ''),
        )
        add_row(batch, sheet_row.number, values, sheet_row.problems, match=match)
    return batch


def backfill_batch(user) -> ImportBatch | None:
    """Proposals from the breeding text and the yard names (§7.7).
    None when nothing was found."""
    batch = ImportBatch.objects.create(
        source=ImportBatch.Source.BACKFILL,
        created_by=user if getattr(user, 'is_authenticated', False) else None,
    )
    number = 0
    for horse in Horse.objects.order_by('name', 'pk'):
        values = {}
        parents = text_rules.parse_breeding(horse.breeding)
        if parents is not None:
            values['S'], values['D'] = parents.sire, parents.dam
        if not horse.registered_name:
            found = text_rules.registered_from_name(horse.name)
            if found:
                values['name'], values['registered_name'] = found
        if not horse.markings:
            found = text_rules.markings_from_name(horse.name)
            if found:
                values['name'], values['markings'] = found
        if not values:
            continue
        number += 1
        row = add_row(batch, number, values, horse=horse)
        if not row.changes.exists():
            row.delete()
    if not batch.rows.exists():
        batch.delete()
        return None
    return batch


# ---------------------------------------------------------------------------
# Review and apply
# ---------------------------------------------------------------------------

class ApplyRefused(Exception):
    pass


def rematch(row: ImportRow, horse: Horse | None) -> None:
    """A person chose the horse for a row (or "new horse")."""
    row.horse = horse
    row.match_how = 'chosen' if horse else ''
    row.create_new = horse is None
    row.save(update_fields=['horse', 'match_how', 'create_new'])
    build_changes(row)


def _resolution(change: ProposedChange) -> Resolution:
    from .names import parse

    text = change.proposed_value
    if change.choice == 'unknown':
        return Resolution(text, unknown=True)
    if change.choice == 'new':
        return Resolution(text, new=parse(text))
    record = PedigreeHorse.objects.filter(pk=change.choice).first()
    if record is None:
        raise ApplyRefused('The chosen record is not on file any more.')
    return Resolution(text, record=record)


def _set_horse_value(horse, key, value):
    if key == 'foaled':
        if value:
            iso, _sep, precision = value.partition('|')
            horse.date_of_birth = date.fromisoformat(iso)
            horse.dob_precision = precision
        return
    if key == 'height':
        horse.height_inches = int(value) if value else None
        return
    field = dict((k, f) for k, _label, f in HORSE_TARGETS)[key]
    setattr(horse, field, value)


def _new_horse(batch, row):
    from core.services import PlacementService

    name = (row.values.get('name') or row.values.get('registered_name') or '').strip()[:100]
    if batch.location_id and batch.owner_id and batch.rate_type_id:
        horse, _placement = PlacementService.create_new_arrival(
            name=name, owner=batch.owner, location=batch.location, rate_type=batch.rate_type,
            arrival_date=batch.arrival_date or timezone.localdate())
        return horse
    return Horse.objects.create(name=name)


def _apply_row(batch: ImportBatch, row: ImportRow, source) -> str:
    changes = list(row.changes.all())
    accepted = [c for c in changes if c.accept]
    if row.horse_id is None and not row.create_new:
        for c in accepted:
            c.result = 'Not saved: "Add as a new horse" was not ticked.'
            c.save(update_fields=['result'])
        return 'Not added. Tick "Add as a new horse", or choose the horse it matches.'
    if not accepted and row.horse_id is not None:
        return 'Nothing ticked.'
    created = False
    if row.horse_id is None:
        horse = _new_horse(batch, row)
        row.horse = horse
        created = True
    else:
        horse = Horse.objects.select_for_update().get(pk=row.horse_id)

    saved = skipped = 0
    touched = False
    for c in accepted:
        if c.is_pedigree:
            continue
        if not created and horse_value(horse, c.target) != c.current_value:
            c.result = 'Skipped: it changed after the file was read.'
            skipped += 1
        else:
            _set_horse_value(horse, c.target, c.proposed_value)
            c.result = 'Saved'
            saved += 1
            touched = True
        c.save(update_fields=['result'])
    if touched:
        horse._change_reason = batch.change_reason
        horse.save()

    boxes, box_changes = {}, []
    record = PedigreeHorse.objects.select_related('sire', 'dam', 'dam__sire').get(pk=pedigree_for(horse).pk)
    order = {'pedigree.S': 0, 'pedigree.D': 1, 'pedigree.DS': 2}
    for c in sorted((c for c in accepted if c.is_pedigree), key=lambda c: order[c.target]):
        code = c.target.split('.', 1)[1]
        if c.needs_choice:
            c.result = 'Skipped: choose the record first.'
        elif not created and pedigree_text(record, code) != c.current_value:
            c.result = 'Skipped: it changed after the file was read.'
        elif code == 'DS' and ('D' in boxes and boxes['D'].unknown
                               or 'D' not in boxes and record.dam_id is None):
            c.result = "Skipped: fill in the dam first."
        else:
            try:
                boxes[code] = _resolution(c)
            except ApplyRefused as error:
                c.result = f'Skipped: {error}'
            else:
                box_changes.append(c)
        if c.result:
            skipped += 1
            c.save(update_fields=['result'])
    if boxes:
        try:
            set_parents(horse, boxes, source=source)
        except ValidationError as error:
            message = '; '.join(error.messages)
            for c in box_changes:
                c.result = f'Not saved: {message}'
                c.save(update_fields=['result'])
            skipped += len(box_changes)
        else:
            for c in box_changes:
                c.result = 'Saved'
                c.save(update_fields=['result'])
            saved += len(box_changes)

    parts = ['Added as a new horse.' if created else f'{saved} saved.']
    if skipped:
        parts.append(f'{skipped} skipped.')
    parts += Horse.objects.get(pk=horse.pk).identity_warnings()
    return ' '.join(parts)


def apply_batch(batch: ImportBatch, user) -> dict:
    """Apply the ticked changes. Returns counts for the message."""
    from core import audit

    counts = {'rows': 0, 'new': 0, 'failed': 0}
    with transaction.atomic():
        batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
        if batch.status != ImportBatch.Status.OPEN:
            raise ApplyRefused('This import was already applied or discarded.')
        batch.applied_at = timezone.now()
        source = SOURCE_FOR_BATCH[batch.source]
        for row in batch.rows.select_related('horse'):
            if row.result.startswith('Left out'):
                continue
            had_horse = row.horse_id is not None
            try:
                with transaction.atomic():
                    row.result = _apply_row(batch, row, source)[:500]
                    row.save(update_fields=['result', 'horse'])
            except Exception as error:  # one bad row must not stop the others
                messages = getattr(error, 'messages', None) or [str(error)]
                row.refresh_from_db()
                row.result = f'Not saved: {"; ".join(messages)}'[:500]
                row.save(update_fields=['result'])
                counts['failed'] += 1
                continue
            if row.changes.filter(result='Saved').exists() or (not had_horse and row.horse_id):
                counts['rows'] += 1
                if not had_horse and row.horse_id:
                    counts['new'] += 1
        batch.status = ImportBatch.Status.APPLIED
        batch.applied_by = user if getattr(user, 'is_authenticated', False) else None
        batch.summary = (f"{counts['rows']} horse{'s' if counts['rows'] != 1 else ''} changed, "
                         f"{counts['new']} new, {counts['failed']} not saved")
        batch.save(update_fields=['status', 'applied_at', 'applied_by', 'summary'])
    audit.record('records_imported', actor=user, detail=f'{batch.title}: {batch.summary}')
    return counts
