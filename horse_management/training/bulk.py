"""Bulk add: the same training for many horses, from the bulk bar.

The bar sends ``action_type=training`` to health's bulk views, which pass
the request here before their own form lookup (docs/TRAINING_MODULE_PLAN.md,
6, "Bulk actions"). This keeps the training form, its rules and its save in
the training app.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import render

from core.models import Horse
from core.permissions import LEVEL_FULL, has_feature_access
from core.views._popup import popup_saved_response

from .access import training_on
from .forms import BulkActivityForm
from .models import Activity

TEMPLATE = 'training/partials/bulk_form.html'


def _horse_ids(data):
    """Numeric ``horse_ids``, in order and de-duplicated."""
    return list(dict.fromkeys(i for i in data.getlist('horse_ids') if i.isdigit()))


def _check(request):
    if not training_on():
        raise Http404("Training is switched off.")
    if not has_feature_access(request.user, 'training', LEVEL_FULL):
        raise PermissionDenied


def _context(form, horse_ids):
    horses = Horse.objects.filter(pk__in=horse_ids).order_by('name').only('pk', 'name', 'is_active')
    return {
        'form': form,
        'horse_ids': horse_ids,
        'horse_names': [h.name for h in horses if h.is_active],
        'skipped_names': [h.name for h in horses if not h.is_active],
        'initial_status': form['status'].value() or Activity.Status.DONE,
    }


def bulk_form(request):
    _check(request)
    horse_ids = _horse_ids(request.GET)
    return render(request, TEMPLATE, _context(BulkActivityForm(), horse_ids))


def bulk_apply(request):
    if request.method != 'POST':
        return HttpResponseBadRequest('POST required')
    _check(request)
    horse_ids = _horse_ids(request.POST)
    if not horse_ids:
        return HttpResponseBadRequest('Invalid request')
    form = BulkActivityForm(request.POST)
    if not form.is_valid():
        return render(request, TEMPLATE, _context(form, horse_ids))

    horses = list(Horse.objects.filter(pk__in=horse_ids).order_by('name'))
    active = [h for h in horses if h.is_active]
    skipped = [h.name for h in horses if not h.is_active]
    data = form.cleaned_data
    with transaction.atomic():
        for horse in active:
            activity = Activity(
                horse=horse,
                date=data['date'],
                work_type=data['work_type'],
                status=data['status'],
                reason=data.get('reason') or '',
                minutes=data.get('minutes'),
                effort=data.get('effort'),
                notes=data.get('notes') or '',
                source=Activity.Source.BULK,
                created_by=request.user,
                updated_by=request.user,
            )
            form.fill_defaults(activity)
            activity.save()

    if active:
        count = len(active)
        messages.success(
            request,
            f"Recorded {data['work_type'].name} for {count} horse{'s' if count != 1 else ''}"
            f" on {data['date']:%a} {data['date'].day} {data['date']:%b}"
            f" ({Activity.Status(data['status']).label.lower()}).",
        )
    if skipped:
        messages.warning(request, f"Not recorded, as they have left the yard: {', '.join(skipped)}.")
    return popup_saved_response()
