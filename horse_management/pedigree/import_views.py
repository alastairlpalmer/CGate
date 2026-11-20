"""Fill in records → Import a file (plan §7.6) and the back-fill (§7.7).

Upload a CSV → a review screen with each row and each proposed change →
Apply. Nothing changes a horse before Apply.
"""

from datetime import date

from django import forms
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.csv_export import csv_response
from core.models import Horse, Location, Owner, RateType
from core.permissions import feature_required

from . import imports
from .models import ImportBatch, ProposedChange
from .sheet import MAX_BYTES, SheetError, read, template_header


class UploadForm(forms.Form):
    file = forms.FileField(label='CSV file', widget=forms.ClearableFileInput(attrs={
        'class': 'form-input', 'accept': '.csv,text/csv'}))
    location = forms.ModelChoiceField(
        Location.objects.order_by('name'), required=False, label='Location',
        widget=forms.Select(attrs={'class': 'form-select'}))
    owner = forms.ModelChoiceField(
        Owner.objects.order_by('name'), required=False, label='Owner',
        widget=forms.Select(attrs={'class': 'form-select'}))
    rate_type = forms.ModelChoiceField(
        RateType.objects.all(), required=False, label='Rate',
        widget=forms.Select(attrs={'class': 'form-select'}))
    arrival_date = forms.DateField(
        required=False, label='Arrival date', input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': 'form-input', 'type': 'date'}))

    def clean_file(self):
        upload = self.cleaned_data['file']
        if not upload.name.lower().endswith('.csv'):
            raise forms.ValidationError('Use a CSV file. In Excel: File, Save as, CSV UTF-8.')
        if upload.size > MAX_BYTES:
            raise forms.ValidationError('The file is too big. Use a file of at most 2 MB.')
        return upload

    def clean(self):
        cleaned = super().clean()
        given = [cleaned.get(k) for k in ('location', 'owner', 'rate_type')]
        if any(given) and not all(given):
            raise forms.ValidationError('For new horses, choose a location, an owner and a rate, or none of them.')
        return cleaned


@feature_required('horses')
def records_imports(request):
    """Upload a file, start the back-fill, and see earlier imports."""
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        upload = form.cleaned_data['file']
        try:
            rows, unknown = read(upload.read())
        except SheetError as error:
            form.add_error('file', str(error))
        else:
            if not rows:
                form.add_error('file', 'The file has no rows under the headings.')
            else:
                placement = {}
                if form.cleaned_data.get('location'):
                    placement = {k: form.cleaned_data[k] for k in ('location', 'owner', 'rate_type')}
                    placement['arrival_date'] = form.cleaned_data.get('arrival_date')
                with transaction.atomic():
                    batch = imports.batch_from_sheet(rows, user=request.user, file_name=upload.name,
                                                     placement=placement)
                if unknown:
                    messages.warning(request, 'These columns were not read: ' + ', '.join(unknown) + '.')
                return redirect('records_import_review', pk=batch.pk)
    return render(request, 'pedigree/records/imports.html', {
        'tab': 'imports', 'form': form, 'columns': template_header(),
        'batches': ImportBatch.objects.select_related('created_by', 'applied_by')[:20],
        'open_backfill': ImportBatch.objects.filter(source=ImportBatch.Source.BACKFILL,
                                                    status=ImportBatch.Status.OPEN).first(),
    })


@feature_required('horses')
def records_import_template(request):
    return csv_response('horse-records-template', template_header(), [])


@require_POST
@feature_required('horses')
def records_backfill(request):
    """Look in the breeding text and the yard names for details (§7.7)."""
    open_batch = ImportBatch.objects.filter(source=ImportBatch.Source.BACKFILL,
                                            status=ImportBatch.Status.OPEN).first()
    if open_batch is not None:
        return redirect('records_import_review', pk=open_batch.pk)
    with transaction.atomic():
        batch = imports.backfill_batch(request.user)
    if batch is None:
        messages.info(request, 'Nothing new was found in the breeding notes or the yard names.')
        return redirect('records_imports')
    return redirect('records_import_review', pk=batch.pk)


def _read_decisions(request, batch):
    """Store the ticks, choices and matches from the review form.
    Returns the rows whose match changed (their changes were remade)."""
    rematched = []
    for row in batch.rows.all():
        if row.result.startswith('Left out'):
            continue
        if row.horse_id is None or row.match_how == 'chosen':
            wanted = request.POST.get(f'match_{row.pk}')
            allowed = {str(pk) for pk in row.possible_ids}
            if wanted is not None:
                horse = Horse.objects.filter(pk=wanted).first() if wanted in allowed else None
                if (horse.pk if horse else None) != row.horse_id:
                    imports.rematch(row, horse)
                    rematched.append(row)
                    continue
        create = f'create_{row.pk}' in request.POST
        if row.horse_id is None and create != row.create_new:
            row.create_new = create
            row.save(update_fields=['create_new'])
    remade = {row.pk for row in rematched}
    for change in ProposedChange.objects.filter(row__batch=batch).exclude(row__pk__in=remade):
        accept = f'accept_{change.pk}' in request.POST
        choice = change.choice
        if change.is_pedigree and change.choice_ids:
            wanted = request.POST.get(f'choice_{change.pk}', '')
            if wanted == 'new' or wanted in {str(pk) for pk in change.choice_ids}:
                choice = wanted
        if accept != change.accept or choice != change.choice:
            change.accept = accept
            change.choice = choice
            change.save(update_fields=['accept', 'choice'])
    return rematched


@feature_required('horses')
def records_import_review(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    if request.method == 'POST' and batch.status == ImportBatch.Status.OPEN:
        action = request.POST.get('action')
        if action == 'discard':
            batch.status = ImportBatch.Status.DISCARDED
            batch.save(update_fields=['status'])
            messages.info(request, 'Discarded. Nothing was changed.')
            return redirect('records_imports')
        rematched = _read_decisions(request, batch)
        if rematched:
            names = ', '.join(f'row {row.number}' for row in rematched)
            messages.info(request, f'The match changed for {names}. Check the changes, then apply.')
            return redirect('records_import_review', pk=batch.pk)
        if action == 'apply':
            waiting = ProposedChange.objects.filter(row__batch=batch, accept=True, target__startswith='pedigree.',
                                                    choice='').count()
            if waiting:
                messages.error(request, f'Choose the record for {waiting} pedigree name{"s" if waiting != 1 else ""} '
                                        'first, or untick them.')
                return redirect('records_import_review', pk=batch.pk)
            try:
                counts = imports.apply_batch(batch, request.user)
            except imports.ApplyRefused as error:
                messages.error(request, str(error))
            else:
                text = f"Applied. {counts['rows']} horse{'s' if counts['rows'] != 1 else ''} changed"
                if counts['new']:
                    text += f", {counts['new']} new"
                if counts['failed']:
                    text += f". {counts['failed']} row{'s' if counts['failed'] != 1 else ''} not saved: see below"
                messages.success(request, text + '.')
            return redirect('records_import_review', pk=batch.pk)
        messages.success(request, 'Your choices are saved. Nothing is applied yet.')
        return redirect('records_import_review', pk=batch.pk)

    rows = list(batch.rows.select_related('horse').prefetch_related('changes'))
    ticked = sum(1 for row in rows for c in row.changes.all() if c.accept)
    return render(request, 'pedigree/records/import_review.html', {
        'tab': 'imports', 'batch': batch, 'rows': rows, 'ticked': ticked,
        'is_open': batch.status == ImportBatch.Status.OPEN, 'today': date.today(),
    })
