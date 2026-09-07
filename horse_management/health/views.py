"""
Views for health app.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required

from core.dashboard import attention
from core.permissions import (
    LEVEL_FULL,
    LEVEL_VIEW,
    FeatureAccessMixin,
    feature_required,
    has_feature_access,
)
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from billing.forms import BulkChargeForm
from billing.models import ExtraCharge
from core.models import Horse, Placement
from core.views._popup import PopupFormMixin, is_popup_request, popup_saved_response

from .forms import (
    BreedingRecordForm,
    CoveringForm,
    FoalingForm,
    ScanResultForm,
    BulkActualDepartureForm,
    BulkExpectedDepartureForm,
    BulkFarrierVisitForm,
    BulkMedicalConditionForm,
    BulkMoveForm,
    BulkRestoreForm,
    BulkVaccinationForm,
    BulkVetVisitForm,
    BulkWormEggCountForm,
    BulkWormingTreatmentForm,
    FarrierVisitForm,
    MedicalConditionForm,
    VaccinationForm,
    VaccinationTypeForm,
    VetVisitForm,
    WormEggCountForm,
    WormingTreatmentForm,
)
from .models import (
    BreedingRecord,
    Covering,
    PregnancyScan,
    FarrierVisit,
    MedicalCondition,
    Vaccination,
    VaccinationType,
    VetVisit,
    WormEggCount,
    WormingTreatment,
    current_farrier_visits,
    current_vaccinations,
)


# ─── Health Dashboard ────────────────────────────────────────────────

HEALTH_TABS = [
    ('overview', 'Overview'),
    ('vaccinations', 'Vaccinations'),
    ('farrier', 'Farrier'),
    ('worming', 'Worming'),
    ('egg_counts', 'Egg Counts'),
    ('conditions', 'Conditions'),
    ('vet_visits', 'Vet Visits'),
]


@feature_required('health', LEVEL_VIEW)
def health_dashboard(request):
    tab = request.GET.get('type', 'overview')
    if tab not in {t[0] for t in HEALTH_TABS}:
        # The tab name is interpolated into a partial template path below;
        # an unknown value was a TemplateDoesNotExist 500 (or a
        # SuspiciousFileOperation for '../'), not an empty page.
        tab = 'overview'
    today = timezone.localdate()
    is_htmx = request.headers.get('HX-Request') == 'true'
    htmx_target = request.headers.get('HX-Target', '')

    context = {
        'tabs': HEALTH_TABS,
        'active_tab': tab,
        'today': today,
    }

    if tab == 'overview':
        # Action Required / Coming Up come from the dashboard's collectors, so
        # the two pages can never disagree about what is overdue.
        action_required, coming_up = attention.health_lists(request.user, today=today)

        # High egg counts (last 90 days)
        high_egg_counts = list(WormEggCount.objects.select_related('horse').filter(
            horse__is_active=True,
            date__gte=today - timedelta(days=90),
            count__gt=200,
        ).order_by('-date'))

        # Active conditions
        active_conditions = MedicalCondition.objects.select_related('horse').filter(
            horse__is_active=True,
            status='active',
        ).order_by('-created_at')[:10]


        context.update({
            'action_required': action_required,
            'coming_up': coming_up,
            'high_egg_counts': high_egg_counts,
            'active_conditions': active_conditions,
            'stat_overdue_vax': sum(1 for e in action_required if e['type'] == 'Vaccination'),
            'stat_due_farrier': sum(1 for e in action_required + coming_up if e['type'] == 'Farrier'),
            'stat_vet_followups': sum(1 for e in action_required + coming_up if e['type'] == 'Vet Follow-up'),
            'stat_high_eggs': len(high_egg_counts),
        })

    elif tab == 'vaccinations':
        queryset = Vaccination.objects.select_related(
            'horse', 'vaccination_type'
        ).filter(horse__is_active=True)
        status = request.GET.get('status')
        if status == 'due':
            queryset = current_vaccinations(queryset).filter(
                next_due_date__lte=today + timedelta(days=30),
                next_due_date__gte=today,
            )
        elif status == 'overdue':
            queryset = current_vaccinations(queryset).filter(
                next_due_date__lt=today,
            )
        horse = request.GET.get('horse')
        if horse and horse.isdigit():
            queryset = queryset.filter(horse_id=horse)
        paginator = Paginator(queryset.order_by('next_due_date'), 50)
        page_obj = paginator.get_page(request.GET.get('page'))
        context['vaccinations'] = page_obj
        context['page_obj'] = page_obj
        context['is_paginated'] = page_obj.has_other_pages()
        context['horses'] = Horse.objects.filter(is_active=True).only('pk', 'name')

    elif tab == 'farrier':
        queryset = FarrierVisit.objects.select_related(
            'horse', 'service_provider'
        ).filter(horse__is_active=True)
        status = request.GET.get('status')
        if status == 'due':
            queryset = current_farrier_visits(queryset).filter(
                next_due_date__lte=today + timedelta(days=14),
                next_due_date__gte=today,
            )
        elif status == 'overdue':
            queryset = current_farrier_visits(queryset).filter(
                next_due_date__lt=today,
            )
        horse = request.GET.get('horse')
        if horse and horse.isdigit():
            queryset = queryset.filter(horse_id=horse)
        paginator = Paginator(queryset.order_by('-date'), 50)
        page_obj = paginator.get_page(request.GET.get('page'))
        context['visits'] = page_obj
        context['page_obj'] = page_obj
        context['is_paginated'] = page_obj.has_other_pages()
        context['horses'] = Horse.objects.filter(is_active=True).only('pk', 'name')

    elif tab == 'worming':
        queryset = WormingTreatment.objects.select_related('horse').filter(
            horse__is_active=True
        )
        horse = request.GET.get('horse')
        if horse and horse.isdigit():
            queryset = queryset.filter(horse_id=horse)
        paginator = Paginator(queryset.order_by('-date'), 50)
        page_obj = paginator.get_page(request.GET.get('page'))
        context['treatments'] = page_obj
        context['page_obj'] = page_obj
        context['is_paginated'] = page_obj.has_other_pages()
        context['horses'] = Horse.objects.filter(is_active=True).only('pk', 'name')

    elif tab == 'egg_counts':
        queryset = WormEggCount.objects.select_related('horse').filter(
            horse__is_active=True
        )
        horse = request.GET.get('horse')
        if horse and horse.isdigit():
            queryset = queryset.filter(horse_id=horse)
        paginator = Paginator(queryset.order_by('-date'), 50)
        page_obj = paginator.get_page(request.GET.get('page'))
        context['egg_counts'] = page_obj
        context['page_obj'] = page_obj
        context['is_paginated'] = page_obj.has_other_pages()
        context['horses'] = Horse.objects.filter(is_active=True).only('pk', 'name')

    elif tab == 'conditions':
        queryset = MedicalCondition.objects.select_related('horse').filter(
            horse__is_active=True
        )
        horse = request.GET.get('horse')
        if horse and horse.isdigit():
            queryset = queryset.filter(horse_id=horse)
        status = request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        paginator = Paginator(queryset.order_by('-created_at'), 50)
        page_obj = paginator.get_page(request.GET.get('page'))
        context['conditions'] = page_obj
        context['page_obj'] = page_obj
        context['is_paginated'] = page_obj.has_other_pages()
        context['horses'] = Horse.objects.filter(is_active=True).only('pk', 'name')

    elif tab == 'vet_visits':
        queryset = VetVisit.objects.select_related('horse', 'vet').filter(
            horse__is_active=True
        )
        horse = request.GET.get('horse')
        if horse and horse.isdigit():
            queryset = queryset.filter(horse_id=horse)
        paginator = Paginator(queryset.order_by('-date'), 50)
        page_obj = paginator.get_page(request.GET.get('page'))
        context['vet_visits'] = page_obj
        context['page_obj'] = page_obj
        context['is_paginated'] = page_obj.has_other_pages()
        context['horses'] = Horse.objects.filter(is_active=True).only('pk', 'name')

    if is_htmx and htmx_target == 'health-table-area':
        template = f'health/partials/{tab}_content.html'
        return render(request, template, context)

    return render(request, 'health/health_dashboard.html', context)


# ─── Bulk Health Actions ─────────────────────────────────────────────

BULK_FORM_MAP = {
    'vaccination': BulkVaccinationForm,
    'farrier': BulkFarrierVisitForm,
    'worming': BulkWormingTreatmentForm,
    'egg_count': BulkWormEggCountForm,
    'vet_visit': BulkVetVisitForm,
    'condition': BulkMedicalConditionForm,
    'expected_departure': BulkExpectedDepartureForm,
    'actual_departure': BulkActualDepartureForm,
    'move': BulkMoveForm,
    'restore': BulkRestoreForm,
    'charge': BulkChargeForm,
}

BULK_MODEL_MAP = {
    'vaccination': Vaccination,
    'farrier': FarrierVisit,
    'worming': WormingTreatment,
    'egg_count': WormEggCount,
    'vet_visit': VetVisit,
    'condition': MedicalCondition,
}

BULK_LABELS = {
    'vaccination': 'Vaccination',
    'farrier': 'Farrier Visit',
    'worming': 'Worming Treatment',
    'egg_count': 'Egg Count',
    'vet_visit': 'Vet Visit',
    'condition': 'Medical Condition',
    'expected_departure': 'Expected Departure',
    'actual_departure': 'Departure Date',
    'move': 'Move to Location',
    'restore': 'Undo Departure',
    'charge': 'Charge',
}

def _charge_details(record):
    """ExtraCharge fields derived from a billable health record, or None
    for record types that don't bill (conditions, egg counts)."""
    if isinstance(record, FarrierVisit):
        return {'charge_type': 'farrier', 'service_provider': record.service_provider,
                'description': f"Farrier - {record.get_work_done_display()}",
                'date': record.date}
    if isinstance(record, VetVisit):
        return {'charge_type': 'vet', 'service_provider': record.vet,
                'description': f"Vet - {record.reason[:200]}",
                'date': record.date}
    if isinstance(record, Vaccination):
        return {'charge_type': 'vaccination', 'service_provider': record.vet,
                'description': f"Vaccination - {record.vaccination_type.name}",
                'date': record.date_given}
    if isinstance(record, WormingTreatment):
        return {'charge_type': 'medication', 'service_provider': None,
                'description': f"Worming - {record.product_name}",
                'date': record.date}
    if isinstance(record, WormEggCount):
        return {'charge_type': 'vet', 'service_provider': None,
                'description': f"Worm egg count - {record.get_sample_type_display()}",
                'date': record.date}
    return None


def sync_record_charge(record):
    """Create or update the ExtraCharge behind a billable health record.

    The single definition of record→charge billing, shared by the create,
    update and bulk flows for farrier, vet, vaccination and worming records:
    cost > 0 with no charge yet → create one for the horse's current owner;
    an existing uninvoiced charge → resync amount/date/description/provider;
    cost cleared/zeroed → delete the uninvoiced charge (no £0.00 invoice
    lines). Invoiced charges are never touched.

    Returns a status string so views can give honest feedback:
    'created' | 'updated' | 'deleted' | 'no_owner' (cost recorded but not
    billable — nobody to invoice) | 'invoiced' (charge already invoiced;
    the edit does NOT change what was billed) | None (nothing to do).
    """
    details = _charge_details(record)
    if details is None:
        return None
    charge = record.extra_charge
    if charge is None:
        if not record.cost or record.cost <= 0:
            return None
        owner = record.horse.current_owner
        if not owner:
            return 'no_owner'
        record.extra_charge = ExtraCharge.objects.create(
            horse=record.horse,
            owner=owner,
            amount=record.cost,
            **details,
        )
        record.save(update_fields=['extra_charge'])
        return 'created'
    if charge.invoiced:
        # The bill is already on an invoice; silently diverging would lose
        # money — callers surface this so staff know to raise an adjustment.
        return 'invoiced' if record.cost != charge.amount else None
    if not record.cost or record.cost <= 0:
        # Cost cleared: remove the pending charge rather than letting a
        # £0.00 line land on the next invoice.
        record.extra_charge = None
        record.save(update_fields=['extra_charge'])
        charge.delete()
        return 'deleted'
    charge.amount = record.cost
    charge.date = details['date']
    charge.description = details['description']
    charge.service_provider = details['service_provider']
    charge.save(update_fields=['amount', 'date', 'description', 'service_provider'])
    return 'updated'


CHARGE_SYNC_MESSAGES = {
    'no_owner': (
        "Cost recorded, but no charge was raised — {horse} has no current "
        "owner to bill. Assign an owner and re-save the record to bill it."
    ),
    'invoiced': (
        "The charge for this record is already on an invoice — the amount "
        "billed has NOT changed. Raise a separate charge for any difference."
    ),
}


def _report_charge_sync(request, record, status):
    """Surface non-silent sync outcomes to the user."""
    template = CHARGE_SYNC_MESSAGES.get(status)
    if template:
        messages.warning(
            request, template.format(horse=record.horse.name),
        )


# Placement-lifecycle bulk actions are gated on the same feature as their
# single-horse equivalents (horse_move, horse_depart, horse_reactivate,
# confirm/cancel_departure all require horses=full); everything else in the
# bulk bar is a health record and requires health=full. The bar template
# mirrors these gates so users are never offered an action they'd 403 on.
PLACEMENT_BULK_ACTIONS = ('move', 'restore', 'expected_departure', 'actual_departure')


def _bulk_action_allowed(user, action_type):
    if action_type in PLACEMENT_BULK_ACTIONS:
        return has_feature_access(user, 'horses', LEVEL_FULL)
    if action_type == 'charge':
        return has_feature_access(user, 'charges', LEVEL_FULL)
    return has_feature_access(user, 'health', LEVEL_FULL)


def _bulk_horse_ids(data):
    """Numeric ``horse_ids`` from a QueryDict, in order and de-duplicated.

    Non-numeric ids would raise ValueError inside a pk__in filter.
    """
    return list(dict.fromkeys(
        i for i in data.getlist('horse_ids') if i.isdigit()
    ))


@login_required
def bulk_health_form(request):
    """Render the bulk-action form into the shared pop-up sheet.

    The selected horses come in the query string and are rendered as
    hidden inputs, so the form carries its own selection: a re-render
    after a validation error keeps them (the old modal appended them
    client-side, and lost them on the first error).
    """
    action_type = request.GET.get('action_type', '')
    form_class = BULK_FORM_MAP.get(action_type)
    if not form_class:
        return HttpResponseBadRequest('Invalid action type')
    if not _bulk_action_allowed(request.user, action_type):
        raise PermissionDenied
    horse_ids = _bulk_horse_ids(request.GET)

    # Determine initial date value
    if action_type == 'vaccination':
        form = form_class(initial={'date_given': timezone.localdate()})
    elif action_type in ('expected_departure', 'actual_departure'):
        form = form_class(initial={'date': timezone.localdate()})
    elif action_type == 'move':
        form = form_class(initial={'move_date': timezone.localdate()})
    elif hasattr(form_class, 'Meta') and hasattr(form_class.Meta, 'model') and 'date' in [f.name for f in form_class.Meta.model._meta.get_fields()]:
        form = form_class(initial={'date': timezone.localdate()})
    else:
        form = form_class()

    return render(request, 'health/partials/bulk_health_form.html', {
        'form': form,
        'action_type': action_type,
        'action_label': BULK_LABELS.get(action_type, action_type),
        **_bulk_form_context(horse_ids),
    })


def _bulk_form_context(horse_ids):
    """The selection the bulk form carries: ids as hidden inputs, names for
    the "For N horses: …" line. Both the list pages' action bar and the
    dashboard's "Record for N" open the form in the pop-up sheet with the
    horses already chosen in the query string."""
    horse_names = list(
        Horse.objects.filter(pk__in=horse_ids, is_active=True)
        .order_by('name').values_list('name', flat=True)
    ) if horse_ids else []
    return {
        'horse_ids': horse_ids,
        'horse_names': horse_names,
    }


@login_required
def bulk_health_apply(request):
    if request.method != 'POST':
        return HttpResponseBadRequest('POST required')

    action_type = request.POST.get('action_type', '')
    horse_ids = _bulk_horse_ids(request.POST)
    form_class = BULK_FORM_MAP.get(action_type)

    if not form_class or not horse_ids:
        return HttpResponseBadRequest('Invalid request')

    if not _bulk_action_allowed(request.user, action_type):
        raise PermissionDenied

    form = form_class(request.POST)
    if not form.is_valid():
        return render(request, 'health/partials/bulk_health_form.html', {
            'form': form,
            'action_type': action_type,
            'action_label': BULK_LABELS.get(action_type, action_type),
            **_bulk_form_context(horse_ids),
        })

    from core.services import PlacementService
    from django.db.models import Exists, OuterRef

    open_placements = Placement.objects.filter(
        horse=OuterRef('pk'), end_date__isnull=True
    )
    if action_type == 'restore':
        # Restore targets departed (inactive) horses — the one bulk action
        # that must not be limited to active ones. The annotation replaces a
        # per-horse open-placement query in the skip check below.
        horses = Horse.objects.filter(pk__in=horse_ids).annotate(
            has_open_placement=Exists(open_placements)
        )
    else:
        horses = Horse.objects.filter(pk__in=horse_ids, is_active=True)
    count = 0
    # Failures are appended with their message prefix baked in, so one shared
    # loop below reports them for every action type.
    action_errors = []
    restore_skipped = []

    with transaction.atomic():
        # Placement actions go through PlacementService so per-horse
        # validation applies and the model-level lifecycle hooks keep
        # is_active and field-usage history correct.
        if action_type == 'restore':
            for horse in horses:
                if horse.is_active and horse.has_open_placement:
                    # Active and placed — nothing to undo
                    restore_skipped.append(horse.name)
                    continue
                try:
                    if PlacementService.cancel_departure(horse):
                        count += 1
                    else:
                        # No placement history to re-open
                        restore_skipped.append(horse.name)
                except ValidationError as e:
                    # Re-opening re-validates the placement; one bad horse
                    # must be reported, not 500 the whole batch.
                    action_errors.append(
                        f"Not restored — {horse.name}: {'; '.join(e.messages)}"
                    )
        elif action_type == 'move':
            for horse in horses:
                try:
                    PlacementService.move_horse(
                        horse,
                        new_location=form.cleaned_data['new_location'],
                        move_date=form.cleaned_data['move_date'],
                        new_rate_type=form.cleaned_data.get('new_rate_type'),
                        notes=form.cleaned_data.get('notes', ''),
                    )
                    count += 1
                except ValidationError as e:
                    action_errors.append(
                        f"Not moved — {horse.name}: {'; '.join(e.messages)}"
                    )
        # Departure date actions update placements, not health records.
        # Placement saves re-validate dates (e.g. departure before arrival),
        # so each horse gets its own savepoint and failures are reported by
        # name instead of 500-ing the whole batch.
        elif action_type in ('expected_departure', 'actual_departure'):
            date_val = form.cleaned_data['date']
            # One query for every open placement in the batch; priming
            # current_placement stops depart_horse re-fetching it per horse.
            placements_by_horse = {
                p.horse_id: p
                for p in Placement.objects.filter(
                    horse__in=horses, end_date__isnull=True
                )
            }
            for horse in horses:
                placement = placements_by_horse.get(horse.pk)
                if not placement:
                    continue
                try:
                    with transaction.atomic():
                        if action_type == 'expected_departure':
                            placement.expected_departure = date_val
                            placement.save()
                        else:
                            # Same path as the single-horse Depart button:
                            # validates dates, deactivates when due, and
                            # rests the field if it empties out.
                            horse.current_placement = placement
                            PlacementService.depart_horse(horse, date_val)
                    count += 1
                except ValidationError as e:
                    action_errors.append(
                        f"Not set — {horse.name}: {'; '.join(e.messages)}"
                    )
        elif action_type == 'charge':
            # A plain charge (transport, worm test, anything in the services
            # directory) per horse, billed to its current owner — the same
            # owner rule sync_record_charge applies to health records.
            for horse in horses:
                owner = horse.current_owner
                if not owner:
                    action_errors.append(
                        f"Not charged — {horse.name} has no current owner to bill."
                    )
                    continue
                charge = form.save(commit=False)
                charge.pk = None
                charge.horse = horse
                charge.owner = owner
                charge.save()
                count += 1
        else:
            for horse in horses:
                obj = form.save(commit=False)
                obj.pk = None
                obj.horse = horse
                if hasattr(obj, 'extra_charge'):
                    # form.save reuses one instance across the loop — without
                    # this, horse #2 inherits horse #1's charge FK and hits
                    # the one-to-one constraint.
                    obj.extra_charge = None
                obj.save()

                # Farrier/vet/vaccination/worming records with a cost bill
                # the horse's owner — same helper as the single-record views.
                _report_charge_sync(request, obj, sync_record_charge(obj))

                count += 1

    # One shared reporting tail: branches only pick the success wording;
    # errors carry their prefix from where they were appended.
    label = BULK_LABELS.get(action_type, action_type)
    plural = 's' if count != 1 else ''
    if action_type == 'restore':
        success_msg = (
            f"{count} horse{plural} restored to "
            f"{'their' if count != 1 else 'its'} last location."
        )
    elif action_type == 'move':
        success_msg = (
            f"{count} horse{plural} moved to "
            f"{form.cleaned_data['new_location'].name}."
        )
    elif action_type in ('expected_departure', 'actual_departure'):
        success_msg = f"{label} set for {count} horse{plural}."
    elif action_type == 'charge':
        success_msg = (
            f"£{form.cleaned_data['amount']:,.2f} charged to the owner"
            f"{'s' if count != 1 else ''} of {count} horse{plural}."
        )
    else:
        success_msg = f"{label} recorded for {count} horse{plural}."
    if count or not (action_errors or restore_skipped):
        messages.success(request, success_msg)
    for err in action_errors:
        messages.error(request, err)
    if restore_skipped:
        messages.warning(
            request,
            "Not restored (already active, or no placement history): "
            + ", ".join(restore_skipped)
        )

    # Same contract as every other pop-up form: 204 + popup:saved closes
    # the sheet and refreshes #main-content in place (static/js/popup.js).
    return popup_saved_response()


# ─── Vaccination Views ───────────────────────────────────────────────

class VaccinationListView(FeatureAccessMixin, ListView):
    feature = 'health'
    access_level = LEVEL_VIEW
    model = Vaccination
    template_name = 'health/vaccination_list.html'
    context_object_name = 'vaccinations'
    paginate_by = 50

    def get_queryset(self):
        queryset = Vaccination.objects.select_related(
            'horse', 'vaccination_type'
        ).filter(horse__is_active=True)

        # Filter by status
        status = self.request.GET.get('status')
        today = timezone.localdate()

        if status == 'due':
            thirty_days = today + timedelta(days=30)
            queryset = current_vaccinations(queryset).filter(
                next_due_date__lte=thirty_days,
                next_due_date__gte=today
            )
        elif status == 'overdue':
            queryset = current_vaccinations(queryset).filter(
                next_due_date__lt=today,
            )

        # Filter by horse
        horse = self.request.GET.get('horse')
        if horse and horse.isdigit():
            queryset = queryset.filter(horse_id=horse)

        return queryset.order_by('next_due_date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True).only('pk', 'name')
        context['today'] = timezone.localdate()
        return context


class HealthRecordSuccessUrlMixin:
    """Send the user back to where they started after saving a record.

    Priority: "Save & add another" re-opens the same blank form with the same
    context; an explicit safe ?next= URL wins next; then the horse page when
    the form was opened via a ?horse= quick action (so recording three things
    after a vet visit doesn't mean re-finding the horse three times); and
    finally the relevant health dashboard tab.
    """

    dashboard_type = ''

    def get_success_url(self):
        if 'save_and_add' in self.request.POST:
            query = self.request.GET.urlencode()
            return self.request.path + (f'?{query}' if query else '')
        next_url = self.request.GET.get('next')
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return next_url
        horse_id = self.request.GET.get('horse', '')
        if horse_id.isdigit():
            return reverse('horse_detail', kwargs={'pk': horse_id})
        return reverse('health_dashboard') + f'?type={self.dashboard_type}'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        horse_id = self.request.GET.get('horse', '')
        # Validated here so templates can safely reverse horse_detail with it.
        context['from_horse_id'] = horse_id if horse_id.isdigit() else ''
        return context


class VaccinationCreateView(PopupFormMixin, HealthRecordSuccessUrlMixin, FeatureAccessMixin, CreateView):
    feature = 'health'
    model = Vaccination
    form_class = VaccinationForm
    template_name = 'health/vaccination_form.html'
    dashboard_type = 'vaccinations'

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['horse'] = horse_id
        initial['date_given'] = timezone.localdate()
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        _report_charge_sync(
            self.request, form.instance,
            sync_record_charge(form.instance),
        )
        messages.success(self.request, "Vaccination record added successfully.")
        return response


class VaccinationUpdateView(PopupFormMixin, FeatureAccessMixin, UpdateView):
    feature = 'health'
    model = Vaccination
    form_class = VaccinationForm
    template_name = 'health/vaccination_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=vaccinations'

    def form_valid(self, form):
        response = super().form_valid(form)
        _report_charge_sync(
            self.request, form.instance,
            sync_record_charge(form.instance),
        )
        return response


# ─── Vaccination Type Views ──────────────────────────────────────────

class VaccinationTypeListView(FeatureAccessMixin, ListView):
    feature = 'settings'
    model = VaccinationType
    template_name = 'health/vaccination_type_list.html'
    context_object_name = 'vaccination_types'
    paginate_by = 50

    def get_queryset(self):
        queryset = VaccinationType.objects.all()
        status = self.request.GET.get('status')
        if status == 'active':
            queryset = queryset.filter(is_active=True)
        elif status == 'inactive':
            queryset = queryset.filter(is_active=False)
        return queryset.order_by('name')


class VaccinationTypeCreateView(FeatureAccessMixin, CreateView):
    feature = 'settings'
    model = VaccinationType
    form_class = VaccinationTypeForm
    template_name = 'health/vaccination_type_form.html'
    success_url = reverse_lazy('vaccination_type_list')

    def form_valid(self, form):
        messages.success(self.request, "Vaccination type added successfully.")
        return super().form_valid(form)


class VaccinationTypeUpdateView(FeatureAccessMixin, UpdateView):
    feature = 'settings'
    model = VaccinationType
    form_class = VaccinationTypeForm
    template_name = 'health/vaccination_type_form.html'
    success_url = reverse_lazy('vaccination_type_list')

    def form_valid(self, form):
        messages.success(self.request, "Vaccination type updated successfully.")
        return super().form_valid(form)


# ─── Farrier Views ───────────────────────────────────────────────────

class FarrierListView(FeatureAccessMixin, ListView):
    feature = 'health'
    access_level = LEVEL_VIEW
    model = FarrierVisit
    template_name = 'health/farrier_list.html'
    context_object_name = 'visits'
    paginate_by = 50

    def get_queryset(self):
        queryset = FarrierVisit.objects.select_related(
            'horse', 'service_provider'
        ).filter(horse__is_active=True)

        # Filter by status
        status = self.request.GET.get('status')
        today = timezone.localdate()

        if status == 'due':
            two_weeks = today + timedelta(days=14)
            queryset = current_farrier_visits(queryset).filter(
                next_due_date__lte=two_weeks,
                next_due_date__gte=today
            )
        elif status == 'overdue':
            queryset = current_farrier_visits(queryset).filter(
                next_due_date__lt=today,
            )

        # Filter by horse
        horse = self.request.GET.get('horse')
        if horse and horse.isdigit():
            queryset = queryset.filter(horse_id=horse)

        return queryset.order_by('-date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True).only('pk', 'name')
        context['today'] = timezone.localdate()
        return context


class FarrierCreateView(PopupFormMixin, HealthRecordSuccessUrlMixin, FeatureAccessMixin, CreateView):
    feature = 'health'
    model = FarrierVisit
    form_class = FarrierVisitForm
    template_name = 'health/farrier_form.html'
    dashboard_type = 'farrier'

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['horse'] = horse_id
        initial['date'] = timezone.localdate()
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        _report_charge_sync(
            self.request, form.instance,
            sync_record_charge(form.instance),
        )
        messages.success(self.request, "Farrier visit recorded successfully.")
        return response


class FarrierUpdateView(PopupFormMixin, FeatureAccessMixin, UpdateView):
    feature = 'health'
    model = FarrierVisit
    form_class = FarrierVisitForm
    template_name = 'health/farrier_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=farrier'

    def form_valid(self, form):
        response = super().form_valid(form)
        _report_charge_sync(
            self.request, form.instance,
            sync_record_charge(form.instance),
        )
        messages.success(self.request, "Farrier visit updated successfully.")
        return response


# ─── Worming Treatment Views ─────────────────────────────────────────

class WormingListView(FeatureAccessMixin, ListView):
    feature = 'health'
    access_level = LEVEL_VIEW
    model = WormingTreatment
    template_name = 'health/worming_list.html'
    context_object_name = 'treatments'
    paginate_by = 50

    def get_queryset(self):
        queryset = WormingTreatment.objects.select_related('horse').filter(
            horse__is_active=True
        )
        horse = self.request.GET.get('horse')
        if horse and horse.isdigit():
            queryset = queryset.filter(horse_id=horse)
        return queryset.order_by('-date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True).only('pk', 'name')
        return context


class WormingCreateView(PopupFormMixin, HealthRecordSuccessUrlMixin, FeatureAccessMixin, CreateView):
    feature = 'health'
    model = WormingTreatment
    form_class = WormingTreatmentForm
    template_name = 'health/worming_form.html'
    dashboard_type = 'worming'

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['horse'] = horse_id
        initial['date'] = timezone.localdate()
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        _report_charge_sync(
            self.request, form.instance,
            sync_record_charge(form.instance),
        )
        messages.success(self.request, "Worming treatment recorded successfully.")
        return response


class WormingUpdateView(PopupFormMixin, FeatureAccessMixin, UpdateView):
    feature = 'health'
    model = WormingTreatment
    form_class = WormingTreatmentForm
    template_name = 'health/worming_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=worming'

    def form_valid(self, form):
        response = super().form_valid(form)
        _report_charge_sync(
            self.request, form.instance,
            sync_record_charge(form.instance),
        )
        return response


# ─── Worm Egg Count Views ────────────────────────────────────────────

class WormEggCountListView(FeatureAccessMixin, ListView):
    feature = 'health'
    access_level = LEVEL_VIEW
    model = WormEggCount
    template_name = 'health/egg_count_list.html'
    context_object_name = 'egg_counts'
    paginate_by = 50

    def get_queryset(self):
        queryset = WormEggCount.objects.select_related('horse').filter(
            horse__is_active=True
        )
        horse = self.request.GET.get('horse')
        if horse and horse.isdigit():
            queryset = queryset.filter(horse_id=horse)
        return queryset.order_by('-date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True).only('pk', 'name')
        return context


class WormEggCountCreateView(PopupFormMixin, HealthRecordSuccessUrlMixin, FeatureAccessMixin, CreateView):
    feature = 'health'
    model = WormEggCount
    form_class = WormEggCountForm
    template_name = 'health/egg_count_form.html'
    dashboard_type = 'egg_counts'

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['horse'] = horse_id
        initial['date'] = timezone.localdate()
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        _report_charge_sync(
            self.request, form.instance,
            sync_record_charge(form.instance),
        )
        messages.success(self.request, "Egg count recorded successfully.")
        return response


class WormEggCountUpdateView(PopupFormMixin, FeatureAccessMixin, UpdateView):
    feature = 'health'
    model = WormEggCount
    form_class = WormEggCountForm
    template_name = 'health/egg_count_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=egg_counts'

    def form_valid(self, form):
        response = super().form_valid(form)
        _report_charge_sync(
            self.request, form.instance,
            sync_record_charge(form.instance),
        )
        return response


# ─── Medical Condition Views ─────────────────────────────────────────

class MedicalConditionListView(FeatureAccessMixin, ListView):
    feature = 'health'
    access_level = LEVEL_VIEW
    model = MedicalCondition
    template_name = 'health/condition_list.html'
    context_object_name = 'conditions'
    paginate_by = 50

    def get_queryset(self):
        queryset = MedicalCondition.objects.select_related('horse').filter(
            horse__is_active=True
        )
        horse = self.request.GET.get('horse')
        if horse and horse.isdigit():
            queryset = queryset.filter(horse_id=horse)
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True).only('pk', 'name')
        return context


class MedicalConditionCreateView(PopupFormMixin, HealthRecordSuccessUrlMixin, FeatureAccessMixin, CreateView):
    feature = 'health'
    model = MedicalCondition
    form_class = MedicalConditionForm
    template_name = 'health/condition_form.html'
    dashboard_type = 'conditions'

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['horse'] = horse_id
        return initial

    def form_valid(self, form):
        messages.success(self.request, "Medical condition recorded successfully.")
        return super().form_valid(form)


class MedicalConditionUpdateView(PopupFormMixin, FeatureAccessMixin, UpdateView):
    feature = 'health'
    model = MedicalCondition
    form_class = MedicalConditionForm
    template_name = 'health/condition_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=conditions'


# ─── Vet Visit Views ─────────────────────────────────────────────────

class VetVisitListView(FeatureAccessMixin, ListView):
    feature = 'health'
    access_level = LEVEL_VIEW
    model = VetVisit
    template_name = 'health/vet_visit_list.html'
    context_object_name = 'vet_visits'
    paginate_by = 50

    def get_queryset(self):
        queryset = VetVisit.objects.select_related('horse', 'vet').filter(
            horse__is_active=True
        )
        horse = self.request.GET.get('horse')
        if horse and horse.isdigit():
            queryset = queryset.filter(horse_id=horse)
        return queryset.order_by('-date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True).only('pk', 'name')
        return context


class VetVisitCreateView(PopupFormMixin, HealthRecordSuccessUrlMixin, FeatureAccessMixin, CreateView):
    feature = 'health'
    model = VetVisit
    form_class = VetVisitForm
    template_name = 'health/vet_visit_form.html'
    dashboard_type = 'vet_visits'

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['horse'] = horse_id
        initial['date'] = timezone.localdate()
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        _report_charge_sync(
            self.request, form.instance,
            sync_record_charge(form.instance),
        )
        messages.success(self.request, "Vet visit recorded successfully.")
        return response


class VetVisitUpdateView(PopupFormMixin, FeatureAccessMixin, UpdateView):
    feature = 'health'
    model = VetVisit
    form_class = VetVisitForm
    template_name = 'health/vet_visit_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=vet_visits'

    def form_valid(self, form):
        response = super().form_valid(form)
        _report_charge_sync(
            self.request, form.instance,
            sync_record_charge(form.instance),
        )
        messages.success(self.request, "Vet visit updated successfully.")
        return response


# ─── Breeding Record Views ───────────────────────────────────────────

def breeding_seasons():
    """Years with at least one covering, newest first."""
    return sorted({
        d.year for d in Covering.objects.values_list('date', flat=True)
    }, reverse=True)


def season_results(season):
    """Outcome figures for one season: mares, pregnancies, foals, rates,
    and the same broken down by stallion. Backdated records count too, so
    old seasons can be reconciled and compared."""
    records = list(
        BreedingRecord.objects.filter(coverings__date__year=season)
        .select_related('mare', 'foal').prefetch_related('coverings', 'scans').distinct()
    )
    open_statuses = set(BreedingRecord.ACTIVE_STATUSES)

    def tally(rows):
        mares = {r.mare_id for r in rows}
        covers = sum(r.covering_count for r in rows)
        held = [r for r in rows if r.status in ('confirmed', 'born', 'lost')]
        born = [r for r in rows if r.status == 'born']
        lost = [r for r in rows if r.status == 'lost']
        barren = [r for r in rows if r.status == 'barren']
        still_open = [r for r in rows if r.status in open_statuses]
        settled = len(rows) - len([r for r in rows if r.status == 'covered'])
        return {
            'records': len(rows),
            'mares': len(mares),
            'covers': covers,
            'covers_per_record': round(covers / len(rows), 1) if rows else 0,
            'held': len(held),
            'born': len(born),
            'lost': len(lost),
            'barren': len(barren),
            'open': len(still_open),
            'conception_rate': round(100 * len(held) / settled) if settled else None,
            'live_foal_rate': round(100 * len(born) / len(rows)) if rows else None,
            'colts': sum(1 for r in born if r.foal_sex == 'colt'),
            'fillies': sum(1 for r in born if r.foal_sex == 'filly'),
        }

    by_stallion = {}
    for r in records:
        by_stallion.setdefault(r.stallion_name or '(no stallion)', []).append(r)
    stallions = [
        {'name': name, **tally(rows)}
        for name, rows in sorted(by_stallion.items(), key=lambda kv: kv[0].lower())
    ]
    records.sort(key=lambda r: (r.mare.name.lower(), r.date_covered))
    return {'season': season, 'totals': tally(records), 'stallions': stallions, 'records': records}


@feature_required('breeding', LEVEL_VIEW)
def breeding_results(request):
    """Season results: conception and live-foal rates, by stallion, with
    every record of the season listed so the figures can be checked."""
    seasons = breeding_seasons()
    season = request.GET.get('season', '')
    if season.isdigit() and int(season) in seasons:
        season = int(season)
    else:
        season = seasons[0] if seasons else timezone.localdate().year
    results = season_results(season)
    # Year-on-year comparison line for the previous seasons.
    history = [
        {'season': yr, **season_results(yr)['totals']}
        for yr in seasons if yr != season
    ][:5]
    return render(request, 'health/breeding_results.html', {
        'seasons': seasons,
        'season': season,
        'results': results,
        'history': history,
        'can_edit': has_feature_access(request.user, 'breeding', LEVEL_FULL),
    })


BREEDING_STATUS_FILTERS = [
    ('', 'All'),
    ('active', 'Active (covered or in foal)'),
    ('covered', 'Covered — awaiting scan'),
    ('confirmed', 'Confirmed in foal'),
    ('born', 'Born'),
    ('lost', 'Lost'),
    ('barren', 'Barren'),
]


class BreedingRecordListView(FeatureAccessMixin, ListView):
    feature = 'breeding'
    access_level = LEVEL_VIEW
    model = BreedingRecord
    template_name = 'health/breeding_list.html'
    context_object_name = 'breeding_records'
    paginate_by = 50

    def get_queryset(self):
        queryset = BreedingRecord.objects.select_related('mare', 'foal').filter(
            mare__is_active=True
        )
        horse = self.request.GET.get('horse')
        if horse and horse.isdigit():
            queryset = queryset.filter(mare_id=horse)
        season = self.request.GET.get('season', '')
        if season.isdigit():
            queryset = queryset.filter(coverings__date__year=int(season)).distinct()
        status = self.request.GET.get('status', '')
        if status == 'active':
            queryset = queryset.filter(status__in=BreedingRecord.ACTIVE_STATUSES)
        elif status in BreedingRecord.Status.values:
            queryset = queryset.filter(status=status)
        # Open pregnancies first, soonest foal at the top; closed records
        # follow, newest covering first.
        return queryset.prefetch_related('coverings', 'scans').order_by(
            Case(
                When(status__in=BreedingRecord.ACTIVE_STATUSES, then=Value(0)),
                default=Value(1), output_field=IntegerField(),
            ),
            F('date_foal_due').asc(nulls_last=True),
            '-date_covered',
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        context['today'] = today
        context['horses'] = Horse.objects.filter(is_active=True, sex='mare').order_by('name')
        context['status_filters'] = BREEDING_STATUS_FILTERS
        context['status_filter'] = self.request.GET.get('status', '')
        context['season_filter'] = self.request.GET.get('season', '')
        context['seasons'] = breeding_seasons()
        context['can_edit'] = has_feature_access(self.request.user, 'breeding', LEVEL_FULL)

        # Season at a glance — unfiltered, so the tiles read the same
        # whatever the list below is narrowed to.
        live = BreedingRecord.objects.filter(mare__is_active=True)
        open_records = live.filter(status__in=BreedingRecord.ACTIVE_STATUSES)
        context['summary'] = {
            'in_foal': open_records.filter(status=BreedingRecord.Status.CONFIRMED).count(),
            'awaiting_scan': open_records.filter(status=BreedingRecord.Status.COVERED).count(),
            'due_30': open_records.filter(
                date_foal_due__gte=today, date_foal_due__lte=today + timedelta(days=30),
            ).count(),
            'past_due': open_records.filter(date_foal_due__lt=today).count(),
            'born_this_year': live.filter(
                status=BreedingRecord.Status.BORN, foal_dob__year=today.year,
            ).count(),
        }
        return context


class BreedingRecordCreateView(PopupFormMixin, FeatureAccessMixin, CreateView):
    feature = 'breeding'
    model = BreedingRecord
    form_class = BreedingRecordForm
    template_name = 'health/breeding_form.html'
    success_url = reverse_lazy('breeding_list')

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['mare'] = horse_id
        return initial

    def form_valid(self, form):
        messages.success(self.request, "Breeding record added successfully.")
        return super().form_valid(form)


class BreedingRecordUpdateView(PopupFormMixin, FeatureAccessMixin, UpdateView):
    feature = 'breeding'
    model = BreedingRecord
    form_class = BreedingRecordForm
    template_name = 'health/breeding_form.html'
    success_url = reverse_lazy('breeding_list')

    def form_valid(self, form):
        messages.success(self.request, "Breeding record updated.")
        return super().form_valid(form)


@feature_required('breeding')
def breeding_foaling(request, pk):
    """Record a foaling: create (or link) the foal and close the record as Born.

    Serves the pop-up sheet too (HX-Target: popup-body): the form partial
    alone, 204 + ``popup:saved`` on success.
    """
    from .services import record_foaling

    record = get_object_or_404(BreedingRecord.objects.select_related('mare'), pk=pk)
    in_popup = is_popup_request(request)
    today = timezone.localdate()

    if not record.can_record_foaling:
        messages.info(
            request,
            f"{record.mare.name}'s record with {record.stallion_name} is "
            f"{record.get_status_display().lower()}, so there is no foaling to record.",
        )
        if in_popup:
            return popup_saved_response()
        return redirect('horse_detail', pk=record.mare_id)

    if request.method == 'POST':
        form = FoalingForm(request.POST, record=record)
        if form.is_valid():
            try:
                foal = record_foaling(
                    record,
                    foal_dob=form.cleaned_data['foal_dob'],
                    foal_sex=form.cleaned_data['foal_sex'],
                    foal_colour=form.cleaned_data['foal_colour'],
                    foal_name=form.cleaned_data['foal_name'],
                    foal_microchip=form.cleaned_data['foal_microchip'],
                    foaling_notes=form.cleaned_data['foaling_notes'],
                    existing_foal=form.cleaned_data['existing_foal'],
                    mare_rate_type=form.cleaned_data.get('mare_rate_type'),
                    place_foal=bool(form.cleaned_data.get('place_foal')),
                    foal_rate_type=form.cleaned_data.get('foal_rate_type'),
                )
            except ValidationError as e:
                form.add_error(None, e)
            else:
                messages.success(request, format_html(
                    'Foaling recorded: <a href="{}" class="underline font-semibold">{}</a> '
                    'born {} to {}. <a href="{}?category=foaling" class="underline font-semibold">Add foaling photos</a>',
                    reverse('horse_detail', args=[foal.pk]),
                    foal.name,
                    form.cleaned_data['foal_dob'].strftime('%d %b %Y'),
                    record.mare.name,
                    reverse('horse_photo_add', args=[record.mare.pk]),
                ))
                if in_popup:
                    return popup_saved_response()
                return redirect('horse_detail', pk=foal.pk)
    else:
        initial = {'foal_dob': today}
        if record.foal_sex:
            initial['foal_sex'] = record.foal_sex
        if record.foal_colour:
            initial['foal_colour'] = record.foal_colour
        form = FoalingForm(initial=initial, record=record)

    template = 'health/partials/foaling_form.html' if in_popup else 'health/breeding_foaling.html'
    return render(request, template, {
        'record': record,
        'mare': record.mare,
        'form': form,
        'in_popup': in_popup,
        'today': today,
    })


@feature_required('breeding')
def breeding_scan(request, pk):
    """Record a scan result: confirm the pregnancy, or close it as Barren/Lost.

    Serves the pop-up sheet too (HX-Target: popup-body).
    """
    from .services import record_scan

    record = get_object_or_404(BreedingRecord.objects.select_related('mare'), pk=pk)
    in_popup = is_popup_request(request)
    today = timezone.localdate()

    if not record.is_active_pregnancy:
        messages.info(
            request,
            f"{record.mare.name}'s record with {record.stallion_name} is "
            f"{record.get_status_display().lower()}, so there is no scan to record.",
        )
        if in_popup:
            return popup_saved_response()
        return redirect('horse_detail', pk=record.mare_id)

    if request.method == 'POST':
        form = ScanResultForm(request.POST, record=record)
        if form.is_valid():
            try:
                status = record_scan(
                    record,
                    scan_type=form.cleaned_data['scan_type'],
                    scan_date=form.cleaned_data['scan_date'],
                    result=form.cleaned_data['result'],
                    notes=form.cleaned_data['notes'],
                    vet=form.cleaned_data['vet'],
                )
            except ValidationError as e:
                form.add_error(None, e)
            else:
                label = dict(BreedingRecord.Status.choices)[status]
                messages.success(
                    request,
                    f"Scan recorded for {record.mare.name}: {label.lower()}.",
                )
                if in_popup:
                    return popup_saved_response()
                return redirect('horse_detail', pk=record.mare_id)
    else:
        # The first scan is the 14-day one; once that is in, the next is
        # the heartbeat scan.
        scan_type = (
            ScanResultForm.SCAN_HEARTBEAT if record.date_scanned_14_days
            else ScanResultForm.SCAN_14
        )
        form = ScanResultForm(
            initial={'scan_type': scan_type, 'scan_date': today, 'result': 'in_foal'},
            record=record,
        )

    template = 'health/partials/scan_form.html' if in_popup else 'health/breeding_scan.html'
    return render(request, template, {
        'record': record,
        'mare': record.mare,
        'form': form,
        'in_popup': in_popup,
        'today': today,
    })


@feature_required('breeding')
def breeding_covering_add(request, pk):
    """Add a covering (first, repeat or re-cover) to a breeding record.

    Serves the pop-up sheet too (HX-Target: popup-body).
    """
    from .services import add_covering

    record = get_object_or_404(BreedingRecord.objects.select_related('mare'), pk=pk)
    in_popup = is_popup_request(request)
    today = timezone.localdate()

    if not record.can_add_covering:
        messages.info(
            request,
            f"{record.mare.name}'s record with {record.stallion_name} is "
            f"{record.get_status_display().lower()}; start a new record for another covering.",
        )
        if in_popup:
            return popup_saved_response()
        return redirect('horse_detail', pk=record.mare_id)

    if request.method == 'POST':
        form = CoveringForm(request.POST, record=record)
        if form.is_valid():
            try:
                add_covering(
                    record,
                    date=form.cleaned_data['date'],
                    method=form.cleaned_data['method'],
                    stallion_name=form.cleaned_data['stallion_name'].strip(),
                    vet=form.cleaned_data['vet'],
                    notes=form.cleaned_data['notes'],
                )
            except ValidationError as e:
                form.add_error(None, e)
            else:
                messages.success(
                    request,
                    f"Covering recorded for {record.mare.name} on "
                    f"{form.cleaned_data['date']:%d %b %Y}. "
                    f"Foal due {record.date_foal_due:%d %b %Y}.",
                )
                if in_popup:
                    return popup_saved_response()
                return redirect('horse_detail', pk=record.mare_id)
    else:
        form = CoveringForm(
            initial={'date': today, 'stallion_name': record.stallion_name,
                     'method': record.coverings.order_by('-date').values_list('method', flat=True).first() or ''},
            record=record,
        )

    template = 'health/partials/covering_form.html' if in_popup else 'health/breeding_covering.html'
    return render(request, template, {
        'record': record,
        'mare': record.mare,
        'form': form,
        'in_popup': in_popup,
        'today': today,
    })


@feature_required('breeding')
def breeding_covering_delete(request, pk):
    """Remove a covering added by mistake (POST). The record's dates follow
    the remaining coverings; the last covering cannot be removed."""
    covering = get_object_or_404(Covering.objects.select_related('record__mare'), pk=pk)
    record = covering.record
    if request.method != 'POST':
        return HttpResponseBadRequest("POST required")
    if record.coverings.count() <= 1:
        messages.error(request, "A record needs at least one covering; edit the date instead.")
    else:
        covering.delete()
        messages.success(request, f"Covering on {covering.date:%d %b %Y} removed.")
    return redirect('horse_detail', pk=record.mare_id)


@feature_required('breeding')
def breeding_scan_delete(request, pk):
    """Remove a scan entered by mistake (POST). The status is not rewound;
    use Edit if the outcome needs changing too."""
    scan = get_object_or_404(PregnancyScan.objects.select_related('record__mare'), pk=pk)
    record = scan.record
    if request.method != 'POST':
        return HttpResponseBadRequest("POST required")
    scan.delete()
    # Keep the denormalised positive dates in step with what is left.
    changed = []
    for scan_type, field_name in (
        (PregnancyScan.ScanType.DAY_14, 'date_scanned_14_days'),
        (PregnancyScan.ScanType.HEARTBEAT, 'date_scanned_heartbeat'),
    ):
        latest = record.scans.filter(
            scan_type=scan_type, result__in=('in_foal', 'twins'),
        ).order_by('-date').values_list('date', flat=True).first()
        if getattr(record, field_name) != latest:
            setattr(record, field_name, latest)
            changed.append(field_name)
    if changed:
        record.save(update_fields=changed + ['updated_at'])
    messages.success(request, f"{scan.get_scan_type_display()} on {scan.date:%d %b %Y} removed.")
    return redirect('horse_detail', pk=record.mare_id)


# ─── Quick-add vet (HTMX) ───────────────────────────────────────────

@feature_required('health')
def quick_add_vet(request):
    """Create a ServiceProvider (vet) inline and return an <option> element."""
    if request.method != 'POST':
        return HttpResponseBadRequest("POST required")
    name = request.POST.get('vet_name', '').strip()
    if not name:
        return HttpResponseBadRequest("Name is required")
    from billing.models import ServiceProvider
    provider = ServiceProvider.objects.create(
        name=name,
        provider_type='vet',
    )
    html = format_html(
        '<option value="{}" selected>{} (Veterinarian)</option>',
        provider.pk,
        provider.name,
    )
    return HttpResponse(html)
