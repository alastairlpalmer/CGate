"""Working with shared pedigree records (plan §6.3, §6.4, §7.2, §7.4).

The matching rule (§6.4): a typed name joins an existing record only when
a person chose it. Picking a suggestion types its full label ("Copper
Ridge (GB) 2004"), which counts as choosing. A name that matches records
in any other way is shown with the choices, and the person picks one or
"New". A name that matches nothing makes a new record.

The one exception is foaling (``link_foal``): the stallion was picked at
the covering, so a name that fits exactly one stallion is that stallion.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Q

from .models import PedigreeHorse
from .names import ParsedName, is_unknown, name_key, parse

MALE, FEMALE = PedigreeHorse.Sex.MALE, PedigreeHorse.Sex.FEMALE

# The positions the edit form writes: the horse's parents, and through
# them its grandparents. (parent position, letter on the parent record).
GRANDPARENTS = {'SS': ('S', 'sire'), 'SD': ('S', 'dam'), 'DS': ('D', 'sire'), 'DD': ('D', 'dam')}
POSITION_SEX = {'S': MALE, 'D': FEMALE, 'SS': MALE, 'SD': FEMALE, 'DS': MALE, 'DD': FEMALE}


# ---------------------------------------------------------------------------
# A yard horse's own record
# ---------------------------------------------------------------------------

def sex_of(horse) -> str:
    if horse.sex in ('mare', 'filly'):
        return FEMALE
    if horse.sex in ('stallion', 'colt', 'gelding'):
        return MALE
    return ''


def values_from_horse(horse) -> dict:
    """What a yard horse's record shows: its registered name, suffix, year."""
    return {
        'name': (horse.registered_name or horse.name or '').strip(),
        'country': (horse.country_of_birth or '').upper(),
        'year_of_birth': horse.date_of_birth.year if horse.date_of_birth else None,
        'sex': sex_of(horse),
        'colour': horse.get_color_display() if horse.color else '',
    }


def pedigree_for(horse) -> PedigreeHorse:
    """The horse's own record, made if it has none."""
    try:
        return horse.pedigree
    except PedigreeHorse.DoesNotExist:
        pass
    record = PedigreeHorse(horse=horse, **values_from_horse(horse))
    record.save()
    horse.pedigree = record
    return record


def sync_from_horse(horse) -> None:
    """Keep the record of a yard horse in step with the horse (one query).

    A copy of the record already loaded on ``horse`` is updated too, so a
    later save of that copy cannot write the old name back.
    """
    values = values_from_horse(horse)
    values['name_key'] = name_key(values['name'])
    PedigreeHorse.objects.filter(horse_id=horse.pk).update(**values)
    cached = horse._state.fields_cache.get('pedigree')
    if cached is not None:
        for attr, value in values.items():
            setattr(cached, attr, value)


# ---------------------------------------------------------------------------
# Matching a typed name
# ---------------------------------------------------------------------------

@dataclass
class Resolution:
    """What a typed box means.

    Exactly one of: ``empty``, ``unknown``, ``record`` (an existing record),
    ``new`` (a record to make), or ``choices`` (a person must choose).
    """
    text: str = ''
    empty: bool = False
    unknown: bool = False
    record: PedigreeHorse | None = None
    new: ParsedName | None = None
    choices: list = field(default_factory=list)

    @property
    def needs_choice(self):
        return bool(self.choices)


def candidates(parsed: ParsedName, sex: str = ''):
    rows = PedigreeHorse.objects.filter(name_key=parsed.key).select_related('sire', 'dam', 'horse')
    if parsed.country:
        rows = rows.filter(country=parsed.country)
    if parsed.year:
        rows = rows.filter(year_of_birth=parsed.year)
    if sex == MALE:
        rows = rows.exclude(sex=FEMALE)
    elif sex == FEMALE:
        rows = rows.exclude(sex=MALE)
    return rows.order_by('country', 'year_of_birth', 'pk')


def resolve(text: str | None, sex: str = '', *, chosen: str | None = None,
            current: PedigreeHorse | None = None) -> Resolution:
    """Read one box of the pedigree form (or an imported name)."""
    text = (text or '').strip()
    if not text:
        return Resolution(text, empty=True)
    if is_unknown(text):
        return Resolution(text, unknown=True)
    parsed = parse(text)
    if chosen:
        if chosen == 'new':
            return Resolution(text, new=parsed)
        record = PedigreeHorse.objects.filter(pk=chosen).first()
        if record is not None and record.name_key == parsed.key:
            return Resolution(text, record=record)
    if current is not None and text in (current.label, current.short_label, current.name):
        return Resolution(text, record=current)
    found = list(candidates(parsed, sex))
    # A full label links without a question only when it says more than
    # the name: a plain "Rosie" can be two polo ponies, so it always asks.
    exact = [r for r in found if text == r.label and (r.country or r.year_of_birth)]
    if len(exact) == 1:
        return Resolution(text, record=exact[0])
    if found:
        return Resolution(text, choices=found)
    return Resolution(text, new=parsed)


def make_record(parsed: ParsedName, sex: str, source=PedigreeHorse.Source.TYPED) -> PedigreeHorse:
    record = PedigreeHorse(name=parsed.name, country=parsed.country,
                           year_of_birth=parsed.year, sex=sex, source=source)
    record.save()
    return record


def apply(resolution: Resolution, sex: str, source=PedigreeHorse.Source.TYPED):
    """(record or None, unknown flag) for a resolved box. Makes new records."""
    if resolution.unknown:
        return None, True
    if resolution.empty:
        return None, False
    if resolution.record is not None:
        record = resolution.record
        if not record.sex and sex:
            record.sex = sex
            record.save(update_fields=['sex', 'updated_at'])
        return record, False
    if resolution.new is not None:
        return make_record(resolution.new, sex, source), False
    raise ValueError('A box with choices must be chosen before it is saved.')


# ---------------------------------------------------------------------------
# Writing parents
# ---------------------------------------------------------------------------

def sync_horse_copies(horse, record: PedigreeHorse | None = None) -> None:
    """Keep Horse.sire_name, dam_name and dam in step with the record.

    The foaling flow, the Foals table, C-vet and CSV export read these.
    """
    record = record or pedigree_for(horse)
    sire, dam = record.sire, record.dam
    horse.sire_name = sire.short_label if sire else ''
    horse.dam_name = dam.short_label if dam else ''
    horse.dam = dam.horse if dam is not None and dam.horse_id else None
    horse.save(update_fields=['sire_name', 'dam_name', 'dam', 'updated_at'])


PARENT_FIELDS = ['sire', 'dam', 'sire_unknown', 'dam_unknown', 'updated_at']


def _set_parent(record, letter, value, unknown):
    setattr(record, letter, value)
    setattr(record, f'{letter}_unknown', unknown)


@transaction.atomic
def set_parents(horse, boxes: dict, *, source=PedigreeHorse.Source.TYPED) -> PedigreeHorse:
    """Write the edit form's boxes. ``boxes`` maps a position code to a
    Resolution; only positions present in it are written.

    A grandparent is written on the parent's own record, so it changes
    for every horse that shares that parent. The form says so first.
    """
    # Read again: a copy held since the horse was made can have an older
    # name than the one sync_from_horse wrote since.
    record = PedigreeHorse.objects.get(pk=pedigree_for(horse).pk)
    for code in ('S', 'D'):
        if code in boxes:
            value, unknown = apply(boxes[code], POSITION_SEX[code], source)
            _set_parent(record, 'sire' if code == 'S' else 'dam', value, unknown)
    record.full_clean(exclude=['name_key'])
    # Only the parent fields: never write back anything else on a record.
    record.save(update_fields=PARENT_FIELDS)

    for code, (parent_code, letter) in GRANDPARENTS.items():
        if code not in boxes:
            continue
        parent = record.sire if parent_code == 'S' else record.dam
        if parent is None:
            continue
        value, unknown = apply(boxes[code], POSITION_SEX[code], source)
        _set_parent(parent, letter, value, unknown)
        parent.full_clean(exclude=['name_key'])
        parent.save(update_fields=PARENT_FIELDS)

    sync_horse_copies(horse, record)
    return record


def other_users_of(parent: PedigreeHorse, horse) -> int:
    """How many other records list ``parent`` as their sire or dam."""
    if parent is None:
        return 0
    return parent.progeny().exclude(horse=horse).count()


# ---------------------------------------------------------------------------
# Foaling (plan §7.4)
# ---------------------------------------------------------------------------

def stallion_for(stallion_name: str) -> PedigreeHorse | None:
    """The stallion record for a covering's stallion name.

    The name was picked from the suggestions at the covering, so a name
    that fits one stallion is him. Several, or none: a new record (two
    records for one stallion can be merged; a wrong join is worse).
    """
    parsed = parse(stallion_name)
    if parsed is None or is_unknown(stallion_name):
        return None
    found = list(candidates(parsed, MALE))
    exact = [r for r in found if stallion_name.strip() == r.label]
    if len(exact) == 1:
        return exact[0]
    if len(found) == 1:
        return found[0]
    return make_record(parsed, MALE, PedigreeHorse.Source.FOALING)


@transaction.atomic
def link_foal(foal, mare, stallion_name: str) -> PedigreeHorse:
    """Give a foal born here its tree: dam = the mare's record, sire = the
    stallion's record. Fills only what is blank, so a dam changed by hand
    (embryo transfer) stays changed."""
    record = PedigreeHorse.objects.get(pk=pedigree_for(foal).pk)
    changed = False
    if record.dam_id is None and not record.dam_unknown and mare is not None:
        record.dam = pedigree_for(mare)
        changed = True
    if record.sire_id is None and not record.sire_unknown and stallion_name:
        sire = stallion_for(stallion_name)
        if sire is not None:
            record.sire = sire
            changed = True
    if changed:
        record.source = PedigreeHorse.Source.FOALING
        record.save(update_fields=PARENT_FIELDS + ['source'])
        sync_horse_copies(foal, record)
    return record


def stallion_suggestions(limit=200):
    """Labels of male records, for the covering form's suggestions."""
    rows = (PedigreeHorse.objects.filter(sex=MALE)
            .filter(Q(horse__isnull=True) | Q(horse__sex='stallion') | Q(horse__sex='colt'))
            .order_by('name', 'country', 'year_of_birth')[:limit])
    return [r.label for r in rows]


# ---------------------------------------------------------------------------
# Family on this yard
# ---------------------------------------------------------------------------

FAMILY_LIMIT = 12


def family(record: PedigreeHorse) -> dict:
    """Horses on the system related to ``record``: parents, siblings, progeny.

    Three queries at most. Lists are capped; ``more`` counts the rest.
    """
    out = {'dam': None, 'sire': None, 'full': [], 'half': [], 'progeny': [], 'more': 0}
    if record is None:
        return out
    if record.dam_id and record.dam.horse_id:
        out['dam'] = record.dam.horse
    if record.sire_id and record.sire.horse_id:
        out['sire'] = record.sire.horse
    parent_match = Q()
    if record.dam_id:
        parent_match |= Q(dam_id=record.dam_id)
    if record.sire_id:
        parent_match |= Q(sire_id=record.sire_id)
    if parent_match:
        siblings = (PedigreeHorse.objects.filter(parent_match, horse__isnull=False)
                    .exclude(pk=record.pk).select_related('horse').order_by('name')[:FAMILY_LIMIT * 2])
        for sib in siblings:
            same_dam = record.dam_id and sib.dam_id == record.dam_id
            same_sire = record.sire_id and sib.sire_id == record.sire_id
            (out['full'] if same_dam and same_sire else out['half']).append(sib.horse)
    progeny = list(record.progeny().filter(horse__isnull=False).select_related('horse')
                   .order_by('-year_of_birth', 'name')[:FAMILY_LIMIT + 1])
    out['progeny'] = [p.horse for p in progeny[:FAMILY_LIMIT]]
    out['more'] = max(len(progeny) - FAMILY_LIMIT, 0)
    out['half'] = out['half'][:FAMILY_LIMIT]
    return out
