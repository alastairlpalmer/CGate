"""Fill in records (docs/HORSE_IDENTITY_AND_PEDIGREE_PLAN.md §7.5, phase 1c).

The rules behind the editor: how complete a horse's record is, which
filter a horse is missing from, which pedigree records may be the same
horse, and merging two records (reversibly).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Q
from django.db.models.functions import Lower
from django.utils import timezone

from .models import PedigreeHorse, PedigreeMerge

# The 9 key fields (§7.5), in the order the editor shows them.
KEY_FIELDS = (
    ('registered', 'registered name'),
    ('country', 'country'),
    ('foaled', 'foaling date'),
    ('microchip', 'microchip'),
    ('ueln', 'UELN'),
    ('passport', 'passport number'),
    ('sire', 'sire'),
    ('dam', 'dam'),
    ('dam_sire', "dam's sire"),
)

MISSING_FILTERS = {
    'chip': 'Microchip',
    'ueln': 'UELN',
    'passport': 'Passport',
    'pedigree': 'Pedigree',
    'foaled': 'Foaling date',
    'estimated': 'Estimated year',
}


def filled(horse, record) -> dict:
    """{key field: True/False} for one horse. "Unknown" counts as filled."""
    dam = record.dam if record is not None else None
    return {
        'registered': bool(horse.registered_name),
        'country': bool(horse.country_of_birth),
        'foaled': horse.date_of_birth is not None,
        'microchip': bool(horse.microchip),
        'ueln': bool(horse.ueln),
        'passport': bool(horse.passport_number),
        'sire': record is not None and (record.sire_id is not None or record.sire_unknown),
        'dam': record is not None and (record.dam_id is not None or record.dam_unknown),
        'dam_sire': record is not None and (
            record.dam_unknown or (dam is not None and (dam.sire_id is not None or dam.sire_unknown))),
    }


def completeness(horse, record) -> tuple[int, list[str]]:
    """(how many of the 9 are filled, the names of the missing ones)."""
    state = filled(horse, record)
    missing = [label for key, label in KEY_FIELDS if not state[key]]
    return len(KEY_FIELDS) - len(missing), missing


def matches_missing(horse, record, key: str) -> bool:
    state = filled(horse, record)
    if key == 'chip':
        return not state['microchip']
    if key == 'ueln':
        return not state['ueln']
    if key == 'passport':
        return not state['passport']
    if key == 'pedigree':
        return not (state['sire'] and state['dam'] and state['dam_sire'])
    if key == 'foaled':
        return not state['foaled']
    if key == 'estimated':
        return horse.date_of_birth is not None and horse.dob_precision == horse.DobPrecision.ESTIMATED
    return True


# ---------------------------------------------------------------------------
# The horse list
# ---------------------------------------------------------------------------

def record_of(horse):
    """The horse's own pedigree record, or None (never a query when the
    list used select_related)."""
    return getattr(horse, 'pedigree', None)


def horse_queryset(filters: dict):
    """Horses for the list, before the "missing" filters (which need the
    pedigree records, so they run in Python)."""
    from core.models import Horse, Placement

    rows = Horse.objects.all()
    if filters.get('status') != 'all':
        rows = rows.filter(is_active=True).filter(
            Exists(Placement.objects.filter(horse=OuterRef('pk'), end_date__isnull=True)))
    for key in ('location', 'owner'):
        value = filters.get(key, '')
        if value.isdigit():
            rows = rows.filter(Exists(Placement.objects.filter(
                horse=OuterRef('pk'), end_date__isnull=True, **{f'{key}_id': value})))
    if filters.get('breed'):
        rows = rows.filter(breed__iexact=filters['breed'])
    q = (filters.get('q') or '').strip()
    if q:
        rows = rows.filter(Q(name__icontains=q) | Q(registered_name__icontains=q))
    return (rows.select_related('pedigree', 'pedigree__dam')
            .order_by(Lower('name'), 'pk'))


def horse_rows(filters: dict) -> list[dict]:
    """One dict per horse in the list: horse, record, count, missing."""
    out = []
    missing_key = filters.get('missing', '')
    gaps_only = filters.get('show') != 'all'
    for horse in horse_queryset(filters):
        record = record_of(horse)
        if missing_key in MISSING_FILTERS and not matches_missing(horse, record, missing_key):
            continue
        count, missing = completeness(horse, record)
        if gaps_only and not missing and missing_key != 'estimated':
            continue
        out.append({'horse': horse, 'record': record, 'count': count, 'missing': missing})
    return out


def _sort_key(horse):
    return (horse.name.lower(), horse.pk)


def next_after(rows: list[dict], horse):
    """The horse after ``horse`` in the list order, even when ``horse``
    itself has just left the list (all its gaps are filled now)."""
    key = _sort_key(horse)
    for row in rows:
        if _sort_key(row['horse']) > key:
            return row['horse']
    return None


# ---------------------------------------------------------------------------
# Versions and undo (plan §4.4, cases 25 and 26)
# ---------------------------------------------------------------------------

UNDO_SESSION_KEY = 'records_undo'
UNDO_MINUTES = 10

SNAPSHOT_FIELDS = (
    'registered_name', 'country_of_birth', 'date_of_birth', 'dob_precision', 'breed',
    'microchip', 'ueln', 'passport_number', 'passport_issuer', 'has_passport',
    'identity_checked_at', 'identity_checked_by_id',
)
PARENT_VALUES = ('sire_id', 'dam_id', 'sire_unknown', 'dam_unknown')


def version(horse, record) -> str:
    """Changes whenever the horse or its own record is saved."""
    stamp = record.updated_at.isoformat() if record is not None else ''
    return f'{horse.updated_at.isoformat()}|{stamp}'


def last_change(horse, record):
    """(who, when) of the newest saved change to the horse or its record."""
    newest = None
    for model_obj in (horse, record):
        if model_obj is None:
            continue
        row = model_obj.history.order_by('-history_date').select_related('history_user').first()
        if row is not None and (newest is None or row.history_date > newest.history_date):
            newest = row
    if newest is None:
        return None, None
    return newest.history_user, newest.history_date


def _to_json(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def snapshot(horse, also=()) -> dict:
    """What the editor's save can change, as it is now.

    ``also``: records the save will link as the new sire or dam. Their
    own parents can change too (a grandparent typed in the same save).
    """
    record = PedigreeHorse.objects.filter(horse=horse).first()
    parents = {}
    if record is not None:
        for rec in (record, record.sire, record.dam, *also):
            if rec is not None:
                parents[str(rec.pk)] = {f: getattr(rec, f) for f in PARENT_VALUES}
    return {
        'horse': horse.pk,
        'fields': {f: _to_json(getattr(horse, f)) for f in SNAPSHOT_FIELDS},
        'parents': parents,
        'max_record': PedigreeHorse.objects.aggregate(top=Max('pk'))['top'] or 0,
        'taken_at': timezone.now().isoformat(),
    }


def finish_snapshot(snap: dict, horse) -> dict:
    """After the save: the records it made, and the new version."""
    from core.models import Horse

    horse = Horse.objects.get(pk=horse.pk)
    record = PedigreeHorse.objects.filter(horse=horse).first()
    snap['created'] = list(PedigreeHorse.objects.filter(pk__gt=snap['max_record'])
                           .values_list('pk', flat=True))
    snap['after'] = version(horse, record)
    return snap


class UndoRefused(Exception):
    pass


@transaction.atomic
def undo(snap: dict):
    """Put the horse and its parent links back as they were before the save."""
    from core.models import Horse

    from .services import sync_horse_copies

    taken = datetime.fromisoformat(snap['taken_at'])
    if timezone.now() - taken > timedelta(minutes=UNDO_MINUTES):
        raise UndoRefused('It is too late to undo that save. Change the values by hand.')
    horse = Horse.objects.select_for_update().filter(pk=snap['horse']).first()
    if horse is None:
        raise UndoRefused('That horse is not on the system any more.')
    record = PedigreeHorse.objects.filter(horse=horse).first()
    if version(horse, record) != snap.get('after'):
        raise UndoRefused(f'{horse.name} was changed again after that save, so it cannot be undone here.')

    for name, value in snap['fields'].items():
        if value is not None and name == 'date_of_birth':
            value = date.fromisoformat(value)
        elif value is not None and name == 'identity_checked_at':
            value = datetime.fromisoformat(value)
        setattr(horse, name, value)
    horse._identity_just_checked = True  # the stamp is put back as it was
    horse._change_reason = 'Records editor: undo'
    names = [f.removesuffix('_id') for f in SNAPSHOT_FIELDS]
    horse.save(update_fields=names + ['updated_at'])

    existing = set(PedigreeHorse.objects.values_list('pk', flat=True).filter(
        pk__in=[int(pk) for pk in snap['parents']] + [
            v for vals in snap['parents'].values() for v in (vals['sire_id'], vals['dam_id']) if v]))
    for pk, values in snap['parents'].items():
        if int(pk) not in existing:
            continue
        rec = PedigreeHorse.objects.get(pk=pk)
        for name, value in values.items():
            if name.endswith('_id') and value is not None and value not in existing:
                value = None
            setattr(rec, name, value)
        rec.save(update_fields=['sire', 'dam', 'sire_unknown', 'dam_unknown', 'updated_at'])

    # Records the save made, and that nothing uses now. A new sire's new
    # parent is used by the new sire until that goes, so repeat.
    for _ in range(3):
        made = PedigreeHorse.objects.filter(pk__in=snap.get('created', []), horse__isnull=True)
        deleted, _rows = (made.exclude(Exists(PedigreeHorse.objects.filter(sire=OuterRef('pk'))))
                          .exclude(Exists(PedigreeHorse.objects.filter(dam=OuterRef('pk')))).delete())
        if not deleted:
            break
    record = PedigreeHorse.objects.filter(horse=horse).first()
    if record is not None:
        sync_horse_copies(horse, record)
    return horse


# ---------------------------------------------------------------------------
# Possible duplicates
# ---------------------------------------------------------------------------

def _deletion_variants(key: str, depth: int) -> set[str]:
    """The key with up to ``depth`` letters left out, and the key itself.

    Two keys share a variant when they are at most ``depth`` letters
    apart: letters added, left out or changed ("moonlitbay" and
    "moonlightbay" are two apart).
    """
    found = {key}
    layer = {key}
    for _ in range(depth):
        layer = {k[:i] + k[i + 1:] for k in layer for i in range(len(k))}
        found |= layer
    return found


def _depth(key: str) -> int:
    """How many letters apart two names may be: none for short names,
    one up to 9 letters, two from 10 letters."""
    if len(key) <= 4:
        return 0
    return 1 if len(key) < 10 else 2


def duplicate_groups(records) -> list[list[PedigreeHorse]]:
    """Groups of records that may be the same horse.

    Same name key, or names a letter or two apart (see ``_depth``), and
    not one male and one female. Records on the system as two different
    horses stay apart.
    """
    records = [r for r in records if r.name_key]
    by_variant = defaultdict(set)
    for r in records:
        for v in _deletion_variants(r.name_key, _depth(r.name_key)):
            by_variant[v].add(r.pk)
    parent = {r.pk: r.pk for r in records}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for ids in by_variant.values():
        ids = sorted(ids)
        for other in ids[1:]:
            parent[find(other)] = find(ids[0])

    groups = defaultdict(list)
    for r in records:
        groups[find(r.pk)].append(r)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        sexes = {m.sex for m in members if m.sex}
        on_yard = [m for m in members if m.horse_id]
        if len(sexes) > 1 and len(members) == 2:
            continue
        if len(on_yard) == len(members):
            continue
        out.append(sorted(members, key=lambda m: (m.name, m.country, m.year_of_birth or 0, m.pk)))
    return sorted(out, key=lambda g: g[0].name_key)


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

class MergeRefused(Exception):
    pass


FILLABLE = ('country', 'year_of_birth', 'sex', 'colour')


def _ancestor_ids(record, generations=6) -> set[int]:
    ids, found = {pid for pid in (record.sire_id, record.dam_id) if pid}, set()
    for _ in range(generations):
        if not ids:
            break
        found |= ids
        rows = PedigreeHorse.objects.filter(pk__in=ids).values_list('sire_id', 'dam_id')
        ids = {pid for pair in rows for pid in pair if pid} - found
    return found


def merge_problem(keep: PedigreeHorse, remove: PedigreeHorse) -> str:
    """Why these two cannot be joined, or ''."""
    if keep.pk == remove.pk:
        return 'Choose two different records.'
    if keep.horse_id and remove.horse_id:
        return ('Both records belong to horses on the system. They are two horse records, '
                'so they cannot be joined here.')
    if keep.sex and remove.sex and keep.sex != remove.sex:
        return 'One record is male and the other is female.'
    if remove.pk in _ancestor_ids(keep) or keep.pk in _ancestor_ids(remove):
        return 'One record is an ancestor of the other.'
    return ''


@transaction.atomic
def merge(keep: PedigreeHorse, remove: PedigreeHorse, user=None) -> PedigreeMerge:
    """Join ``remove`` into ``keep``. Every link to ``remove`` moves to
    ``keep``; blanks on ``keep`` are filled from ``remove``. Reversible
    with ``undo_merge``.
    """
    from .services import sync_from_horse, sync_horse_copies

    keep = PedigreeHorse.objects.select_for_update().get(pk=keep.pk)
    remove = PedigreeHorse.objects.select_for_update().get(pk=remove.pk)
    problem = merge_problem(keep, remove)
    if problem:
        raise MergeRefused(problem)

    snapshot = {f.attname: getattr(remove, f.attname) for f in PedigreeHorse._meta.concrete_fields}
    for key in ('created_at', 'updated_at', 'checked_at'):
        if snapshot.get(key) is not None:
            snapshot[key] = snapshot[key].isoformat()
    moved_sire = list(PedigreeHorse.objects.filter(sire=remove).values_list('pk', flat=True))
    moved_dam = list(PedigreeHorse.objects.filter(dam=remove).values_list('pk', flat=True))
    filled_fields = []
    for name in FILLABLE:
        if not getattr(keep, name) and getattr(remove, name):
            setattr(keep, name, getattr(remove, name))
            filled_fields.append(name)
    for letter in ('sire', 'dam'):
        if getattr(keep, f'{letter}_id') is None and not getattr(keep, f'{letter}_unknown'):
            if getattr(remove, f'{letter}_id') is not None:
                setattr(keep, f'{letter}_id', getattr(remove, f'{letter}_id'))
                filled_fields.append(letter)
            elif getattr(remove, f'{letter}_unknown'):
                setattr(keep, f'{letter}_unknown', True)
                filled_fields.append(f'{letter}_unknown')
    moved_horse = remove.horse_id if remove.horse_id and not keep.horse_id else None

    record = PedigreeMerge.objects.create(
        kept=keep, removed_snapshot=snapshot, moved_sire_ids=moved_sire, moved_dam_ids=moved_dam,
        filled_fields=filled_fields, moved_horse_id=moved_horse, merged_by=user if getattr(
            user, 'is_authenticated', False) else None,
    )
    PedigreeHorse.objects.filter(pk__in=moved_sire).update(sire=keep)
    PedigreeHorse.objects.filter(pk__in=moved_dam).update(dam=keep)
    if moved_horse:
        PedigreeHorse.objects.filter(pk=remove.pk).update(horse=None)
        keep.horse_id = moved_horse
    remove.delete()
    keep.save()
    if moved_horse:
        sync_from_horse(keep.horse)
    for child in PedigreeHorse.objects.filter(pk__in=moved_sire + moved_dam, horse__isnull=False) \
            .select_related('horse'):
        sync_horse_copies(child.horse, child)
    return record


@transaction.atomic
def undo_merge(merge_row: PedigreeMerge) -> PedigreeHorse:
    """Put the removed record back, and move its links back to it."""
    from .services import sync_from_horse, sync_horse_copies

    if merge_row.undone_at:
        raise MergeRefused('This merge was already undone.')
    snap = dict(merge_row.removed_snapshot)
    keep = PedigreeHorse.objects.select_for_update().get(pk=merge_row.kept_id)
    if PedigreeHorse.objects.filter(pk=snap['id']).exists():
        raise MergeRefused('The removed record is back already.')
    horse_id = snap.pop('horse_id', None)
    for key in ('created_at', 'updated_at', 'checked_at'):
        snap.pop(key, None)
    # A parent or user deleted since the merge is left blank.
    for key in ('sire_id', 'dam_id'):
        if snap.get(key) and not PedigreeHorse.objects.filter(pk=snap[key]).exists():
            snap[key] = None
    if snap.get('checked_by_id'):
        from django.contrib.auth import get_user_model
        if not get_user_model().objects.filter(pk=snap['checked_by_id']).exists():
            snap['checked_by_id'] = None
    restored = PedigreeHorse(**snap)
    restored.save()
    # Empty again what the merge filled, unless it was changed since.
    for name in merge_row.filled_fields:
        if name in ('sire', 'dam'):
            if getattr(keep, f'{name}_id') == snap.get(f'{name}_id'):
                setattr(keep, f'{name}_id', None)
        elif name.endswith('_unknown'):
            setattr(keep, name, False)
        elif getattr(keep, name) == snap.get(name):
            setattr(keep, name, '' if name != 'year_of_birth' else None)
    if merge_row.moved_horse_id:
        keep.horse_id = None
    keep.save()
    if horse_id:
        restored.horse_id = horse_id
        restored.save(update_fields=['horse'])
    PedigreeHorse.objects.filter(pk__in=merge_row.moved_sire_ids).update(sire=restored)
    PedigreeHorse.objects.filter(pk__in=merge_row.moved_dam_ids).update(dam=restored)
    merge_row.undone_at = timezone.now()
    merge_row.save(update_fields=['undone_at'])
    if horse_id:
        sync_from_horse(restored.horse)
    for child in PedigreeHorse.objects.filter(
            pk__in=merge_row.moved_sire_ids + merge_row.moved_dam_ids, horse__isnull=False) \
            .select_related('horse'):
        sync_horse_copies(child.horse, child)
    return restored
