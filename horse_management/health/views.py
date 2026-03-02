"""
Views for health app.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from billing.models import ExtraCharge
from core.models import Horse

from .forms import (
    BreedingRecordForm,
    BulkFarrierVisitForm,
    BulkMedicalConditionForm,
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
    FarrierVisit,
    MedicalCondition,
    Vaccination,
    VaccinationType,
    VetVisit,
    WormEggCount,
    WormingTreatment,
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


@login_required
def health_dashboard(request):
    tab = request.GET.get('type', 'overview')
    today = timezone.now().date()
    is_htmx = request.headers.get('HX-Request') == 'true'
    htmx_target = request.headers.get('HX-Target', '')

    context = {
        'tabs': HEALTH_TABS,
        'active_tab': tab,
        'today': today,
    }

    if tab == 'overview':
        thirty_days = today + timedelta(days=30)
        two_weeks = today + timedelta(days=14)

        # Overdue vaccinations
        overdue_vaccinations = Vaccination.objects.select_related(
            'horse', 'vaccination_type'
        ).filter(horse__is_active=True, next_due_date__lt=today).order_by('next_due_date')

        # Due soon vaccinations
        due_vaccinations = Vaccination.objects.select_related(
            'horse', 'vaccination_type'
        ).filter(
            horse__is_active=True,
            next_due_date__gte=today,
            next_due_date__lte=thirty_days,
        ).order_by('next_due_date')

        # Overdue farrier
        overdue_farrier = FarrierVisit.objects.select_related(
            'horse', 'service_provider'
        ).filter(
            horse__is_active=True,
            next_due_date__isnull=False,
            next_due_date__lt=today,
        ).order_by('next_due_date')

        # Due soon farrier
        due_farrier = FarrierVisit.objects.select_related(
            'horse', 'service_provider'
        ).filter(
            horse__is_active=True,
            next_due_date__gte=today,
            next_due_date__lte=two_weeks,
        ).order_by('next_due_date')

        # Vet follow-ups (overdue)
        overdue_vet = VetVisit.objects.select_related(
            'horse', 'vet'
        ).filter(
            horse__is_active=True,
            follow_up_date__isnull=False,
            follow_up_date__lt=today,
        ).order_by('follow_up_date')

        # Vet follow-ups (upcoming)
        due_vet = VetVisit.objects.select_related(
            'horse', 'vet'
        ).filter(
            horse__is_active=True,
            follow_up_date__isnull=False,
            follow_up_date__gte=today,
            follow_up_date__lte=thirty_days,
        ).order_by('follow_up_date')

        # High egg counts (last 90 days)
        high_egg_counts = WormEggCount.objects.select_related('horse').filter(
            horse__is_active=True,
            date__gte=today - timedelta(days=90),
            count__gt=200,
        ).order_by('-date')

        # Active conditions
        active_conditions = MedicalCondition.objects.select_related('horse').filter(
            horse__is_active=True,
            status='active',
        ).order_by('-created_at')[:10]

        # Build unified action_required list (overdue items)
        action_required = []
        for vax in overdue_vaccinations:
            action_required.append({
                'horse': vax.horse,
                'type': 'Vaccination',
                'detail': vax.vaccination_type.name,
                'due_date': vax.next_due_date,
                'url': reverse('vaccination_create') + f'?horse={vax.horse.pk}',
                'action_label': 'Re-vaccinate',
            })
        for visit in overdue_farrier:
            action_required.append({
                'horse': visit.horse,
                'type': 'Farrier',
                'detail': visit.get_work_done_display(),
                'due_date': visit.next_due_date,
                'url': reverse('farrier_create') + f'?horse={visit.horse.pk}',
                'action_label': 'Book',
            })
        for v in overdue_vet:
            action_required.append({
                'horse': v.horse,
                'type': 'Vet Follow-up',
                'detail': v.reason[:60] if v.reason else '-',
                'due_date': v.follow_up_date,
                'url': reverse('vet_visit_create') + f'?horse={v.horse.pk}',
                'action_label': 'New Visit',
            })
        action_required.sort(key=lambda x: x['due_date'])

        # Build unified coming_up list (due soon items)
        coming_up = []
        for vax in due_vaccinations:
            coming_up.append({
                'horse': vax.horse,
                'type': 'Vaccination',
                'detail': vax.vaccination_type.name,
                'due_date': vax.next_due_date,
                'url': reverse('vaccination_create') + f'?horse={vax.horse.pk}',
                'action_label': 'Re-vaccinate',
            })
        for visit in due_farrier:
            coming_up.append({
                'horse': visit.horse,
                'type': 'Farrier',
                'detail': visit.get_work_done_display(),
                'due_date': visit.next_due_date,
                'url': reverse('farrier_create') + f'?horse={visit.horse.pk}',
                'action_label': 'Book',
            })
        for v in due_vet:
            coming_up.append({
                'horse': v.horse,
                'type': 'Vet Follow-up',
                'detail': v.reason[:60] if v.reason else '-',
                'due_date': v.follow_up_date,
                'url': reverse('vet_visit_create') + f'?horse={v.horse.pk}',
                'action_label': 'New Visit',
            })
        coming_up.sort(key=lambda x: x['due_date'])

        context.update({
            'action_required': action_required,
            'coming_up': coming_up,
            'high_egg_counts': high_egg_counts,
            'active_conditions': active_conditions,
            'stat_overdue_vax': overdue_vaccinations.count(),
            'stat_due_farrier': overdue_farrier.count() + due_farrier.count(),
            'stat_vet_followups': overdue_vet.count() + due_vet.count(),
            'stat_high_eggs': high_egg_counts.count(),
        })

    elif tab == 'vaccinations':
        queryset = Vaccination.objects.select_related(
            'horse', 'vaccination_type'
        ).filter(horse__is_active=True)
        status = request.GET.get('status')
        if status == 'due':
            queryset = queryset.filter(
                next_due_date__lte=today + timedelta(days=30),
                next_due_date__gte=today,
            )
        elif status == 'overdue':
            queryset = queryset.filter(next_due_date__lt=today)
        horse = request.GET.get('horse')
        if horse:
            queryset = queryset.filter(horse_id=horse)
        paginator = Paginator(queryset.order_by('next_due_date'), 50)
        page_obj = paginator.get_page(request.GET.get('page'))
        context['vaccinations'] = page_obj
        context['page_obj'] = page_obj
        context['is_paginated'] = page_obj.has_other_pages()
        context['horses'] = Horse.objects.filter(is_active=True)

    elif tab == 'farrier':
        queryset = FarrierVisit.objects.select_related(
            'horse', 'service_provider'
        ).filter(horse__is_active=True)
        status = request.GET.get('status')
        if status == 'due':
            queryset = queryset.filter(
                next_due_date__lte=today + timedelta(days=14),
                next_due_date__gte=today,
            )
        elif status == 'overdue':
            queryset = queryset.filter(next_due_date__lt=today)
        horse = request.GET.get('horse')
        if horse:
            queryset = queryset.filter(horse_id=horse)
        paginator = Paginator(queryset.order_by('-date'), 50)
        page_obj = paginator.get_page(request.GET.get('page'))
        context['visits'] = page_obj
        context['page_obj'] = page_obj
        context['is_paginated'] = page_obj.has_other_pages()
        context['horses'] = Horse.objects.filter(is_active=True)

    elif tab == 'worming':
        queryset = WormingTreatment.objects.select_related('horse').filter(
            horse__is_active=True
        )
        horse = request.GET.get('horse')
        if horse:
            queryset = queryset.filter(horse_id=horse)
        paginator = Paginator(queryset.order_by('-date'), 50)
        page_obj = paginator.get_page(request.GET.get('page'))
        context['treatments'] = page_obj
        context['page_obj'] = page_obj
        context['is_paginated'] = page_obj.has_other_pages()
        context['horses'] = Horse.objects.filter(is_active=True)

    elif tab == 'egg_counts':
        queryset = WormEggCount.objects.select_related('horse').filter(
            horse__is_active=True
        )
        horse = request.GET.get('horse')
        if horse:
            queryset = queryset.filter(horse_id=horse)
        paginator = Paginator(queryset.order_by('-date'), 50)
        page_obj = paginator.get_page(request.GET.get('page'))
        context['egg_counts'] = page_obj
        context['page_obj'] = page_obj
        context['is_paginated'] = page_obj.has_other_pages()
        context['horses'] = Horse.objects.filter(is_active=True)

    elif tab == 'conditions':
        queryset = MedicalCondition.objects.select_related('horse').filter(
            horse__is_active=True
        )
        horse = request.GET.get('horse')
        if horse:
            queryset = queryset.filter(horse_id=horse)
        status = request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        paginator = Paginator(queryset.order_by('-created_at'), 50)
        page_obj = paginator.get_page(request.GET.get('page'))
        context['conditions'] = page_obj
        context['page_obj'] = page_obj
        context['is_paginated'] = page_obj.has_other_pages()
        context['horses'] = Horse.objects.filter(is_active=True)

    elif tab == 'vet_visits':
        queryset = VetVisit.objects.select_related('horse', 'vet').filter(
            horse__is_active=True
        )
        horse = request.GET.get('horse')
        if horse:
            queryset = queryset.filter(horse_id=horse)
        paginator = Paginator(queryset.order_by('-date'), 50)
        page_obj = paginator.get_page(request.GET.get('page'))
        context['vet_visits'] = page_obj
        context['page_obj'] = page_obj
        context['is_paginated'] = page_obj.has_other_pages()
        context['horses'] = Horse.objects.filter(is_active=True)

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
}


@login_required
def bulk_health_form(request):
    action_type = request.GET.get('action_type', '')
    form_class = BULK_FORM_MAP.get(action_type)
    if not form_class:
        return HttpResponseBadRequest('Invalid action type')

    form = form_class(initial={'date': timezone.now().date()} if 'date' in [f.name for f in form_class.Meta.model._meta.get_fields()] else {})
    # Set initial date for vaccination (field is date_given)
    if action_type == 'vaccination':
        form = form_class(initial={'date_given': timezone.now().date()})

    return render(request, 'health/partials/bulk_health_form.html', {
        'form': form,
        'action_type': action_type,
        'action_label': BULK_LABELS.get(action_type, action_type),
    })


@login_required
def bulk_health_apply(request):
    if request.method != 'POST':
        return HttpResponseBadRequest('POST required')

    action_type = request.POST.get('action_type', '')
    horse_ids = request.POST.getlist('horse_ids')
    form_class = BULK_FORM_MAP.get(action_type)

    if not form_class or not horse_ids:
        return HttpResponseBadRequest('Invalid request')

    form = form_class(request.POST)
    if not form.is_valid():
        return render(request, 'health/partials/bulk_health_form.html', {
            'form': form,
            'action_type': action_type,
            'action_label': BULK_LABELS.get(action_type, action_type),
        })

    horses = Horse.objects.filter(pk__in=horse_ids, is_active=True)
    count = 0

    with transaction.atomic():
        for horse in horses:
            obj = form.save(commit=False)
            obj.pk = None
            obj.horse = horse
            obj.save()

            # Create ExtraCharge for farrier visits with cost > 0
            if action_type == 'farrier' and form.cleaned_data.get('cost', 0) > 0:
                owner = horse.current_owner
                if owner:
                    charge = ExtraCharge.objects.create(
                        horse=horse,
                        owner=owner,
                        service_provider=obj.service_provider,
                        charge_type='farrier',
                        date=obj.date,
                        description=f"Farrier - {obj.get_work_done_display()}",
                        amount=obj.cost,
                    )
                    obj.extra_charge = charge
                    obj.save()

            # Create ExtraCharge for vet visits with cost > 0
            if action_type == 'vet_visit' and form.cleaned_data.get('cost', 0) > 0:
                owner = horse.current_owner
                if owner:
                    charge = ExtraCharge.objects.create(
                        horse=horse,
                        owner=owner,
                        service_provider=obj.vet,
                        charge_type='vet',
                        date=obj.date,
                        description=f"Vet - {obj.reason[:200]}",
                        amount=obj.cost,
                    )
                    obj.extra_charge = charge
                    obj.save()

            count += 1

    label = BULK_LABELS.get(action_type, action_type)
    messages.success(request, f"{label} recorded for {count} horse{'s' if count != 1 else ''}.")

    # Return HX-Trigger to close modal and refresh page
    response = HttpResponse(status=204)
    response['HX-Trigger'] = 'bulkActionComplete'
    return response


# ─── Vaccination Views ───────────────────────────────────────────────

class VaccinationListView(LoginRequiredMixin, ListView):
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
        today = timezone.now().date()

        if status == 'due':
            thirty_days = today + timedelta(days=30)
            queryset = queryset.filter(
                next_due_date__lte=thirty_days,
                next_due_date__gte=today
            )
        elif status == 'overdue':
            queryset = queryset.filter(next_due_date__lt=today)

        # Filter by horse
        horse = self.request.GET.get('horse')
        if horse:
            queryset = queryset.filter(horse_id=horse)

        return queryset.order_by('next_due_date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True)
        context['today'] = timezone.now().date()
        return context


class VaccinationCreateView(LoginRequiredMixin, CreateView):
    model = Vaccination
    form_class = VaccinationForm
    template_name = 'health/vaccination_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=vaccinations'

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['horse'] = horse_id
        initial['date_given'] = timezone.now().date()
        return initial

    def form_valid(self, form):
        messages.success(self.request, "Vaccination record added successfully.")
        return super().form_valid(form)


class VaccinationUpdateView(LoginRequiredMixin, UpdateView):
    model = Vaccination
    form_class = VaccinationForm
    template_name = 'health/vaccination_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=vaccinations'


# ─── Vaccination Type Views ──────────────────────────────────────────

class VaccinationTypeListView(LoginRequiredMixin, ListView):
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


class VaccinationTypeCreateView(LoginRequiredMixin, CreateView):
    model = VaccinationType
    form_class = VaccinationTypeForm
    template_name = 'health/vaccination_type_form.html'
    success_url = reverse_lazy('vaccination_type_list')

    def form_valid(self, form):
        messages.success(self.request, "Vaccination type added successfully.")
        return super().form_valid(form)


class VaccinationTypeUpdateView(LoginRequiredMixin, UpdateView):
    model = VaccinationType
    form_class = VaccinationTypeForm
    template_name = 'health/vaccination_type_form.html'
    success_url = reverse_lazy('vaccination_type_list')

    def form_valid(self, form):
        messages.success(self.request, "Vaccination type updated successfully.")
        return super().form_valid(form)


# ─── Farrier Views ───────────────────────────────────────────────────

class FarrierListView(LoginRequiredMixin, ListView):
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
        today = timezone.now().date()

        if status == 'due':
            two_weeks = today + timedelta(days=14)
            queryset = queryset.filter(
                next_due_date__lte=two_weeks,
                next_due_date__gte=today
            )
        elif status == 'overdue':
            queryset = queryset.filter(next_due_date__lt=today)

        # Filter by horse
        horse = self.request.GET.get('horse')
        if horse:
            queryset = queryset.filter(horse_id=horse)

        return queryset.order_by('-date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True)
        context['today'] = timezone.now().date()
        return context


class FarrierCreateView(LoginRequiredMixin, CreateView):
    model = FarrierVisit
    form_class = FarrierVisitForm
    template_name = 'health/farrier_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=farrier'

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['horse'] = horse_id
        initial['date'] = timezone.now().date()
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)

        # Optionally create an extra charge for the farrier visit
        if form.cleaned_data['cost'] > 0:
            horse = form.instance.horse
            owner = horse.current_owner

            if owner:
                charge = ExtraCharge.objects.create(
                    horse=horse,
                    owner=owner,
                    service_provider=form.instance.service_provider,
                    charge_type='farrier',
                    date=form.instance.date,
                    description=f"Farrier - {form.instance.get_work_done_display()}",
                    amount=form.instance.cost,
                )
                form.instance.extra_charge = charge
                form.instance.save()

        messages.success(self.request, "Farrier visit recorded successfully.")
        return response


class FarrierUpdateView(LoginRequiredMixin, UpdateView):
    model = FarrierVisit
    form_class = FarrierVisitForm
    template_name = 'health/farrier_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=farrier'

    def form_valid(self, form):
        response = super().form_valid(form)

        # Sync linked ExtraCharge if it exists and hasn't been invoiced
        if form.instance.extra_charge and not form.instance.extra_charge.invoiced:
            charge = form.instance.extra_charge
            charge.amount = form.instance.cost
            charge.date = form.instance.date
            charge.description = f"Farrier - {form.instance.get_work_done_display()}"
            charge.service_provider = form.instance.service_provider
            charge.save(update_fields=['amount', 'date', 'description', 'service_provider'])

        messages.success(self.request, "Farrier visit updated successfully.")
        return response


# ─── Worming Treatment Views ─────────────────────────────────────────

class WormingListView(LoginRequiredMixin, ListView):
    model = WormingTreatment
    template_name = 'health/worming_list.html'
    context_object_name = 'treatments'
    paginate_by = 50

    def get_queryset(self):
        queryset = WormingTreatment.objects.select_related('horse').filter(
            horse__is_active=True
        )
        horse = self.request.GET.get('horse')
        if horse:
            queryset = queryset.filter(horse_id=horse)
        return queryset.order_by('-date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True)
        return context


class WormingCreateView(LoginRequiredMixin, CreateView):
    model = WormingTreatment
    form_class = WormingTreatmentForm
    template_name = 'health/worming_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=worming'

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['horse'] = horse_id
        initial['date'] = timezone.now().date()
        return initial

    def form_valid(self, form):
        messages.success(self.request, "Worming treatment recorded successfully.")
        return super().form_valid(form)


class WormingUpdateView(LoginRequiredMixin, UpdateView):
    model = WormingTreatment
    form_class = WormingTreatmentForm
    template_name = 'health/worming_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=worming'


# ─── Worm Egg Count Views ────────────────────────────────────────────

class WormEggCountListView(LoginRequiredMixin, ListView):
    model = WormEggCount
    template_name = 'health/egg_count_list.html'
    context_object_name = 'egg_counts'
    paginate_by = 50

    def get_queryset(self):
        queryset = WormEggCount.objects.select_related('horse').filter(
            horse__is_active=True
        )
        horse = self.request.GET.get('horse')
        if horse:
            queryset = queryset.filter(horse_id=horse)
        return queryset.order_by('-date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True)
        return context


class WormEggCountCreateView(LoginRequiredMixin, CreateView):
    model = WormEggCount
    form_class = WormEggCountForm
    template_name = 'health/egg_count_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=egg_counts'

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['horse'] = horse_id
        initial['date'] = timezone.now().date()
        return initial

    def form_valid(self, form):
        messages.success(self.request, "Egg count recorded successfully.")
        return super().form_valid(form)


class WormEggCountUpdateView(LoginRequiredMixin, UpdateView):
    model = WormEggCount
    form_class = WormEggCountForm
    template_name = 'health/egg_count_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=egg_counts'


# ─── Medical Condition Views ─────────────────────────────────────────

class MedicalConditionListView(LoginRequiredMixin, ListView):
    model = MedicalCondition
    template_name = 'health/condition_list.html'
    context_object_name = 'conditions'
    paginate_by = 50

    def get_queryset(self):
        queryset = MedicalCondition.objects.select_related('horse').filter(
            horse__is_active=True
        )
        horse = self.request.GET.get('horse')
        if horse:
            queryset = queryset.filter(horse_id=horse)
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True)
        return context


class MedicalConditionCreateView(LoginRequiredMixin, CreateView):
    model = MedicalCondition
    form_class = MedicalConditionForm
    template_name = 'health/condition_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=conditions'

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['horse'] = horse_id
        return initial

    def form_valid(self, form):
        messages.success(self.request, "Medical condition recorded successfully.")
        return super().form_valid(form)


class MedicalConditionUpdateView(LoginRequiredMixin, UpdateView):
    model = MedicalCondition
    form_class = MedicalConditionForm
    template_name = 'health/condition_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=conditions'


# ─── Vet Visit Views ─────────────────────────────────────────────────

class VetVisitListView(LoginRequiredMixin, ListView):
    model = VetVisit
    template_name = 'health/vet_visit_list.html'
    context_object_name = 'vet_visits'
    paginate_by = 50

    def get_queryset(self):
        queryset = VetVisit.objects.select_related('horse', 'vet').filter(
            horse__is_active=True
        )
        horse = self.request.GET.get('horse')
        if horse:
            queryset = queryset.filter(horse_id=horse)
        return queryset.order_by('-date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True)
        return context


class VetVisitCreateView(LoginRequiredMixin, CreateView):
    model = VetVisit
    form_class = VetVisitForm
    template_name = 'health/vet_visit_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=vet_visits'

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['horse'] = horse_id
        initial['date'] = timezone.now().date()
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)

        # Auto-create ExtraCharge if cost > 0 (same pattern as FarrierCreateView)
        if form.cleaned_data['cost'] > 0:
            horse = form.instance.horse
            owner = horse.current_owner

            if owner:
                charge = ExtraCharge.objects.create(
                    horse=horse,
                    owner=owner,
                    service_provider=form.instance.vet,
                    charge_type='vet',
                    date=form.instance.date,
                    description=f"Vet - {form.instance.reason[:200]}",
                    amount=form.instance.cost,
                )
                form.instance.extra_charge = charge
                form.instance.save()

        messages.success(self.request, "Vet visit recorded successfully.")
        return response


class VetVisitUpdateView(LoginRequiredMixin, UpdateView):
    model = VetVisit
    form_class = VetVisitForm
    template_name = 'health/vet_visit_form.html'

    def get_success_url(self):
        return reverse('health_dashboard') + '?type=vet_visits'

    def form_valid(self, form):
        response = super().form_valid(form)

        # Sync linked ExtraCharge if it exists and hasn't been invoiced
        if form.instance.extra_charge and not form.instance.extra_charge.invoiced:
            charge = form.instance.extra_charge
            charge.amount = form.instance.cost
            charge.date = form.instance.date
            charge.description = f"Vet - {form.instance.reason[:200]}"
            charge.service_provider = form.instance.vet
            charge.save(update_fields=['amount', 'date', 'description', 'service_provider'])

        messages.success(self.request, "Vet visit updated successfully.")
        return response


# ─── Breeding Record Views ───────────────────────────────────────────

class BreedingRecordListView(LoginRequiredMixin, ListView):
    model = BreedingRecord
    template_name = 'health/breeding_list.html'
    context_object_name = 'breeding_records'
    paginate_by = 50

    def get_queryset(self):
        queryset = BreedingRecord.objects.select_related('mare', 'foal').filter(
            mare__is_active=True
        )
        horse = self.request.GET.get('horse')
        if horse:
            queryset = queryset.filter(mare_id=horse)
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        return queryset.order_by('-date_covered')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True, sex='mare')
        return context


class BreedingRecordCreateView(LoginRequiredMixin, CreateView):
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


class BreedingRecordUpdateView(LoginRequiredMixin, UpdateView):
    model = BreedingRecord
    form_class = BreedingRecordForm
    template_name = 'health/breeding_form.html'
    success_url = reverse_lazy('breeding_list')
