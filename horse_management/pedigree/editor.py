"""Fill in records (plan §7.5): a list, one form, Save and next.

Two tabs. **Horses**: the yard's horses with how many of the 9 key fields
are filled; select one to fill it in. **Pedigree records**: the shared
records, with a "Possible duplicates" filter and Merge. View-only roles
see the lists as a "what is missing" report.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.csv_export import csv_response
from core.models import Horse, Location, Owner
from core.permissions import LEVEL_FULL, LEVEL_VIEW, feature_required, has_feature_access
from core.views._undo import undo_tag

from . import records as rules
from .display import record_for_page
from .forms import PedigreeForm, RecordEditForm, RecordsIdentityForm, validation_messages
from .models import PedigreeHorse, PedigreeMerge
from .services import set_parents, set_record_parents, sync_progeny_copies
from .tree import build
from .views import _suggestions

HORSE_FILTERS = ('status', 'show', 'missing', 'q', 'location', 'owner', 'breed')
RECORD_LIMIT = 150


def _filters(params) -> dict:
    return {key: (params.get(key) or '').strip() for key in HORSE_FILTERS}


def _query(filters: dict, **extra) -> str:
    """The filters as a query string, without the empty ones."""
    data = QueryDict(mutable=True)
    for key, value in {**filters, **extra}.items():
        if value not in ('', None):
            data[key] = value
    return data.urlencode()


def _horses_url(filters, **extra):
    query = _query(filters, **extra)
    return reverse('records_horses') + (f'?{query}' if query else '')


def _breeds():
    return sorted({b.strip() for b in Horse.objects.exclude(breed='').values_list('breed', flat=True)},
                  key=str.lower)


# ---------------------------------------------------------------------------
# Horses tab
# ---------------------------------------------------------------------------

@feature_required('horses', LEVEL_VIEW)
def records_horses(request):
    can_edit = has_feature_access(request.user, 'horses', LEVEL_FULL)
    filters = _filters(request.GET)
    rows = rules.horse_rows(filters)

    horse = None
    pane = {}
    pk = request.GET.get('horse', '')
    if pk.isdigit() and can_edit:
        horse = get_object_or_404(Horse, pk=pk)
        response = _horse_pane(request, horse, filters, rows, pane)
        if response is not None:
            return response
    elif request.method == 'POST':
        return redirect(_horses_url(filters))

    position = next((i for i, row in enumerate(rows, 1) if horse and row['horse'].pk == horse.pk), None)
    return render(request, 'pedigree/records/horses.html', {
        'tab': 'horses', 'rows': rows, 'filters': filters, 'can_edit': can_edit,
        'missing_filters': rules.MISSING_FILTERS, 'key_count': len(rules.KEY_FIELDS),
        'locations': Location.objects.order_by('name'), 'owners': Owner.objects.order_by('name'),
        'breeds': _breeds(), 'horse': horse, 'position': position,
        'query': _query(filters), 'export_query': _query(filters),
        'active_filters': sum(1 for value in filters.values() if value),
        'list_url': _horses_url(filters),
        **pane,
    })


def _horse_pane(request, horse, filters, rows, pane):
    """Fill ``pane`` with the form context. Returns a redirect after a save."""
    record = record_for_page(horse)
    current = rules.version(horse, record)
    conflict = None
    if request.method == 'POST':
        id_form = RecordsIdentityForm(request.POST, instance=horse)
        ped_form = PedigreeForm(request.POST, horse=horse, record=record)
        if request.POST.get('version') != current:
            who, when = rules.last_change(horse, record)
            conflict = {'who': who, 'when': when}
            # Show the values as they are now, so nothing is saved on top.
            horse = Horse.objects.get(pk=horse.pk)
            id_form = RecordsIdentityForm(instance=horse)
            ped_form = PedigreeForm(horse=horse, record=record)
        elif id_form.is_valid() and ped_form.is_valid():
            chosen = [res.record for code, res in ped_form.resolutions.items()
                      if code in ('S', 'D') and res.record is not None]
            snap = rules.snapshot(Horse.objects.get(pk=horse.pk), also=chosen)
            try:
                with transaction.atomic():
                    saved = id_form.save(commit=False)
                    saved._change_reason = 'Records editor'
                    saved.save()
                    if ped_form.resolutions:
                        set_parents(saved, ped_form.resolutions)
            except ValidationError as error:
                for message in validation_messages(error):
                    ped_form.add_error(None, message)
            else:
                request.session[rules.UNDO_SESSION_KEY] = rules.finish_snapshot(snap, saved)
                messages.success(request, f'Saved {saved.name}.', extra_tags=undo_tag(reverse('records_undo')))
                for warning in saved.identity_warnings():
                    messages.warning(request, warning)
                if 'save_next' in request.POST:
                    after = rules.horse_rows(filters)
                    nxt = rules.next_after(after, saved)
                    if nxt is None:
                        messages.info(request, 'That was the last horse in this list.')
                        return redirect(_horses_url(filters))
                    return redirect(_horses_url(filters, horse=nxt.pk))
                return redirect(_horses_url(filters, horse=saved.pk))
    else:
        id_form = RecordsIdentityForm(instance=horse)
        ped_form = PedigreeForm(horse=horse, record=record)

    count, missing = rules.completeness(horse, PedigreeHorse.objects.select_related('dam').get(pk=record.pk))
    pane.update({
        'id_form': id_form, 'form': ped_form, 'record': record, 'version': rules.version(horse, record),
        'conflict': conflict, 'count': count, 'missing': missing,
        'males': _suggestions('M'), 'females': _suggestions('F'),
        'next_horse': rules.next_after(rows, horse),
    })
    return None


@require_POST
@feature_required('horses')
def records_undo(request):
    """The Undo button on "Saved Copper Rose." (plan §4.4, case 25)."""
    snap = request.session.get(rules.UNDO_SESSION_KEY)
    if not snap:
        messages.info(request, 'There is nothing to undo.')
        return redirect('records_horses')
    try:
        horse = rules.undo(snap)
    except rules.UndoRefused as error:
        messages.error(request, str(error))
        return redirect(_back(request, reverse('records_horses')))
    request.session.pop(rules.UNDO_SESSION_KEY, None)
    messages.success(request, f'Undone. {horse.name} is back as it was.')
    return redirect(_back(request, reverse('records_horses') + f'?horse={horse.pk}'))


def _back(request, default):
    url = request.POST.get('next') or request.META.get('HTTP_REFERER', '')
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return url
    return default


@feature_required('horses', LEVEL_VIEW)
def records_export(request):
    """Download as CSV: the filtered horses, in the import template's columns."""
    filters = _filters(request.GET)
    header = ['Yard name', 'Registered name', 'Country', 'Foaled', 'Sex', 'Colour', 'Breed',
              'Sire', 'Dam', "Dam's sire", 'Microchip', 'UELN', 'Passport number',
              'Passport issuer', 'Markings', 'Height']
    out = []
    for row in rules.horse_rows(filters):
        horse, record = row['horse'], row['record']
        dam = record.dam if record else None
        out.append([
            horse.name, horse.registered_name, horse.country_of_birth, _foaled(horse),
            horse.get_sex_display() if horse.sex else '', horse.get_color_display() if horse.color else '',
            horse.breed, _parent_text(record, 'sire'), _parent_text(record, 'dam'),
            _parent_text(dam, 'sire'), horse.microchip, horse.ueln, horse.passport_number,
            horse.passport_issuer, horse.markings,
            horse.height_display.removesuffix(' hh') if horse.height_inches else '',
        ])
    return csv_response('horse-records', header, out)


def _foaled(horse):
    if horse.date_of_birth is None:
        return ''
    if horse.dob_is_exact:
        return horse.date_of_birth.isoformat()
    year = str(horse.date_of_birth.year)
    return f'c. {year}' if horse.dob_precision == Horse.DobPrecision.ESTIMATED else year


def _parent_text(record, letter):
    if record is None:
        return ''
    parent = getattr(record, letter)
    if parent is not None:
        return parent.short_label
    return 'Unknown' if getattr(record, f'{letter}_unknown') else ''


# ---------------------------------------------------------------------------
# Pedigree records tab
# ---------------------------------------------------------------------------

def _record_rows(q: str, dupes: bool):
    rows = PedigreeHorse.objects.all()
    if q:
        from .names import name_key
        key = name_key(q)
        rows = rows.filter(name_key__contains=key) if key else rows.filter(name__icontains=q)
    if dupes:
        groups = rules.duplicate_groups(rows.only('pk', 'name', 'name_key', 'country', 'year_of_birth',
                                                  'sex', 'horse'))
        rows = rows.select_related('sire', 'dam', 'horse')
        ids = [r.pk for group in groups for r in group]
        by_pk = {r.pk: r for r in rows.filter(pk__in=ids)}
        return [[by_pk[r.pk] for r in group] for group in groups], False
    total = rows.count()
    rows = rows.select_related('sire', 'dam', 'horse')
    return list(rows.order_by('name_key', 'country', 'year_of_birth')[:RECORD_LIMIT]), total > RECORD_LIMIT


@feature_required('horses', LEVEL_VIEW)
def records_pedigree(request):
    can_edit = has_feature_access(request.user, 'horses', LEVEL_FULL)
    q = (request.GET.get('q') or '').strip()
    dupes = request.GET.get('dupes') == '1'
    rows, more = _record_rows(q, dupes)
    query = _query({'q': q, 'dupes': '1' if dupes else ''})

    record = form = None
    pk = request.GET.get('record', '')
    if pk.isdigit():
        record = get_object_or_404(PedigreeHorse.objects.select_related('sire', 'dam', 'horse'), pk=pk)
        if can_edit and not record.horse_id:
            form = RecordEditForm(request.POST or None, instance=record)
            if request.method == 'POST' and form.is_valid():
                try:
                    with transaction.atomic():
                        names_changed = bool({'name', 'country', 'year_of_birth'} & set(form.changed_data))
                        saved = form.save(user=request.user)
                        if form.resolutions:
                            set_record_parents(saved, form.resolutions)
                        if names_changed:
                            sync_progeny_copies(saved)
                except ValidationError as error:
                    for message in validation_messages(error):
                        form.add_error(None, message)
                else:
                    messages.success(request, f'Saved {saved.label}.')
                    url = reverse('records_pedigree') + '?' + _query({'q': q, 'dupes': '1' if dupes else '',
                                                                      'record': saved.pk})
                    return redirect(url)
    if request.method == 'POST' and form is None:
        return redirect(reverse('records_pedigree') + (f'?{query}' if query else ''))

    return render(request, 'pedigree/records/pedigree.html', {
        'tab': 'records', 'rows': rows, 'more': more, 'q': q, 'dupes': dupes, 'query': query,
        'can_edit': can_edit, 'record': record, 'form': form,
        'used_in': record.progeny().count() if record else 0,
        'progeny': list(record.progeny().select_related('horse')[:12]) if record else [],
        'tree': build(record, 2) if record else None,
        'merges': PedigreeMerge.objects.filter(kept=record, undone_at__isnull=True)[:5] if record else [],
        'males': _suggestions('M') if form else [], 'females': _suggestions('F') if form else [],
        'list_url': reverse('records_pedigree') + (f'?{query}' if query else ''),
    })


def _pick_record(text):
    """A record from a typed label ('Moonlit Bay (FR) 2001') or a pk."""
    text = (text or '').strip()
    if text.isdigit():
        return PedigreeHorse.objects.filter(pk=text).first()
    from .names import parse
    parsed = parse(text)
    if parsed is None:
        return None
    found = [r for r in PedigreeHorse.objects.filter(name_key=parsed.key) if r.label == text]
    return found[0] if len(found) == 1 else None


@feature_required('horses')
def records_merge(request):
    """Merge two records: both side by side, choose the one to keep."""
    a = _pick_record(request.GET.get('a'))
    b = _pick_record(request.GET.get('b'))
    if a is None or b is None:
        messages.error(request, 'Choose two records to compare. Pick the second name from the list.')
        back = request.GET.get('a', '')
        return redirect(reverse('records_pedigree') + (f'?record={back}' if back.isdigit() else ''))
    problem = rules.merge_problem(a, b) or rules.merge_problem(b, a)

    if request.method == 'POST' and not problem:
        keep_pk = request.POST.get('keep', '')
        if keep_pk not in (str(a.pk), str(b.pk)):
            messages.error(request, 'Choose the record to keep.')
        else:
            keep, remove = (a, b) if keep_pk == str(a.pk) else (b, a)
            if keep.horse_id is None and remove.horse_id is not None:
                keep, remove = remove, keep  # the yard horse's record always stays
            try:
                merge_row = rules.merge(keep, remove, user=request.user)
            except (rules.MergeRefused, ValidationError) as error:
                messages.error(request, '; '.join(getattr(error, 'messages', [str(error)])))
            else:
                messages.success(
                    request, f'Merged {merge_row.removed_label} into {keep.label}.',
                    extra_tags=undo_tag(reverse('records_merge_undo', args=[merge_row.pk])))
                return redirect(reverse('records_pedigree') + f'?record={keep.pk}')

    sides = []
    for rec in (a, b):
        sides.append({
            'record': rec, 'tree': build(rec, 2), 'used_in': rec.progeny().count(),
            'progeny': list(rec.progeny().select_related('horse')[:6]),
        })
    return render(request, 'pedigree/records/merge.html', {
        'tab': 'records', 'a': a, 'b': b, 'sides': sides, 'problem': problem,
        'yard_keeps': a.horse_id or b.horse_id,
    })


@require_POST
@feature_required('horses')
def records_merge_undo(request, pk):
    merge_row = get_object_or_404(PedigreeMerge, pk=pk)
    try:
        restored = rules.undo_merge(merge_row)
    except (rules.MergeRefused, ValidationError) as error:
        messages.error(request, '; '.join(getattr(error, 'messages', [str(error)])))
        return redirect(reverse('records_pedigree') + f'?record={merge_row.kept_id}')
    messages.success(request, f'Merge undone. {restored.label} is a separate record again.')
    return redirect(reverse('records_pedigree') + f'?record={restored.pk}')
