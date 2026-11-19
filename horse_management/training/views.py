"""Training pages: the Log, Set up, and the pop-up forms (phase 1)."""

from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import Count
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, UpdateView

from core.models import Horse
from core.permissions import (
    LEVEL_FULL, LEVEL_VIEW, FeatureAccessMixin, feature_required, has_feature_access,
)
from core.views._popup import PopupFormMixin, is_popup_request, popup_saved_response

from . import services
from .access import TrainingEnabledMixin, training_enabled
from .forms import ActivityForm, WorkTypeForm
from .models import Activity, WorkType

STATUS_FILTERS = [
    ('done', 'Done'),
    ('planned', 'Planned'),
    ('not_confirmed', 'Not confirmed'),
    ('not_done', 'Did not work'),
]


def _safe_next(request, fallback):
    """``next`` from the form or the query string, if it stays on this site."""
    candidate = request.POST.get('next') or request.GET.get('next') or ''
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return candidate
    return fallback


def _date_label(day):
    return f"{day:%a} {day.day} {day:%b %Y}"


def _report(request, result, done_word):
    """One message for a status change on one or many rows."""
    if result.changed:
        noun = 'record' if result.changed == 1 else 'records'
        messages.success(request, f"{result.changed} {noun} marked {done_word}.")
    if result.future:
        messages.warning(
            request,
            f"{result.future} not changed: work cannot be Done on a date after today.",
        )
    if result.other:
        messages.info(request, f"{result.other} not changed: only Planned work can be changed this way.")
    if not (result.changed or result.future or result.other):
        messages.info(request, "Nothing was changed.")


# ─── Home and Log ────────────────────────────────────────────────────

@training_enabled
@feature_required('training', LEVEL_VIEW)
def training_home(request):
    """The sidebar's Training link. Phase 1 has no board, so: the Log."""
    return redirect('training_log')


class ActivityLogView(TrainingEnabledMixin, FeatureAccessMixin, ListView):
    feature = 'training'
    access_level = LEVEL_VIEW
    model = Activity
    template_name = 'training/log.html'
    context_object_name = 'activities'
    paginate_by = 50

    def _filters(self):
        get = self.request.GET
        return {
            'horse': get.get('horse', '') if get.get('horse', '').isdigit() else '',
            'work_type': get.get('work_type', '') if get.get('work_type', '').isdigit() else '',
            'status': get.get('status', '') if get.get('status', '') in dict(STATUS_FILTERS) else '',
            'date_from': get.get('date_from', ''),
            'date_to': get.get('date_to', ''),
        }

    def get_queryset(self):
        queryset = Activity.objects.select_related('horse', 'work_type', 'updated_by')
        f = self._filters()
        if f['horse']:
            queryset = queryset.filter(horse_id=f['horse'])
        if f['work_type']:
            queryset = queryset.filter(work_type_id=f['work_type'])
        if f['status'] == 'not_confirmed':
            queryset = queryset.not_confirmed()
        elif f['status']:
            queryset = queryset.filter(status=f['status'])
        for key, lookup in (('date_from', 'date__gte'), ('date_to', 'date__lte')):
            try:
                if f[key]:
                    queryset = queryset.filter(**{lookup: f[key]})
            except Exception:   # a malformed date in the address: ignore it
                f[key] = ''
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = self._filters()
        context.update({
            'filters': filters,
            'filter_query': urlencode({k: v for k, v in filters.items() if v}),
            'status_filters': STATUS_FILTERS,
            'horses': Horse.objects.filter(activities__isnull=False).distinct().order_by('name').only('pk', 'name'),
            'work_types': WorkType.objects.all(),
            'reasons': Activity.Reason.choices,
            'can_change': has_feature_access(self.request.user, 'training', LEVEL_FULL),
            'not_confirmed_count': Activity.objects.not_confirmed().count(),
            'today': timezone.localdate(),
            'training_tab': 'log',
        })
        return context


@training_enabled
@feature_required('training')
@require_POST
def log_mark(request):
    """Mark the ticked rows of the Log Done or Did not work."""
    ids = [i for i in request.POST.getlist('ids') if i.isdigit()]
    action = request.POST.get('action', '')
    back = _safe_next(request, reverse('training_log'))
    if not ids:
        messages.info(request, "Tick at least one row first.")
        return redirect(back)
    if action == 'done':
        _report(request, services.mark_done(ids, user=request.user), 'done')
    elif action == 'not_done':
        reason = request.POST.get('reason', '')
        if reason not in Activity.Reason.values:
            messages.error(request, "Choose why the horses did not work.")
            return redirect(back)
        _report(request, services.mark_not_done(ids, reason, user=request.user), 'as did not work')
    else:
        return HttpResponseBadRequest('Unknown action')
    return redirect(back)


# ─── One record ──────────────────────────────────────────────────────

class ActivityFormViewMixin:
    """Shared by add and change: who did it, where to go after.

    It comes before PopupFormMixin in the bases, so its pop-up template wins.
    """

    feature = 'training'
    model = Activity
    form_class = ActivityForm
    template_name = 'training/activity_form.html'
    popup_template_name = 'training/partials/activity_form_popup.html'

    def get_success_url(self):
        if 'save_and_add' in self.request.POST:
            query = self.request.GET.urlencode()
            return self.request.path + (f'?{query}' if query else '')
        horse_id = self.request.GET.get('horse', '')
        if horse_id.isdigit():
            fallback = reverse('horse_detail', kwargs={'pk': horse_id})
        elif self.object is not None and self.object.pk:
            fallback = reverse('horse_detail', kwargs={'pk': self.object.horse_id})
        else:
            fallback = reverse('training_log')
        return _safe_next(self.request, fallback)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get('form')
        status = form['status'].value() if form is not None else Activity.Status.DONE
        context['initial_status'] = status or Activity.Status.DONE
        return context


class ActivityCreateView(TrainingEnabledMixin, ActivityFormViewMixin, PopupFormMixin,
                         FeatureAccessMixin, CreateView):

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse', '')
        if horse_id.isdigit():
            initial['horse'] = horse_id
        day = self.request.GET.get('date', '')
        if day:
            initial['date'] = day
        return initial

    def form_valid(self, form):
        form.instance.source = Activity.Source.HORSE
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        activity = form.instance
        messages.success(
            self.request,
            f"Recorded {activity.work_type.name} for {activity.horse.name} on {_date_label(activity.date)}"
            f" ({activity.get_status_display().lower()}).",
        )
        return super().form_valid(form)


class ActivityUpdateView(TrainingEnabledMixin, ActivityFormViewMixin, PopupFormMixin,
                         FeatureAccessMixin, UpdateView):

    def get_queryset(self):
        return Activity.objects.select_related('horse', 'work_type')

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, f"Training record for {form.instance.horse.name} saved.")
        return super().form_valid(form)


@training_enabled
@feature_required('training')
def activity_delete(request, pk):
    """Ask, then delete one record. Deleting is for mistakes; history keeps it."""
    activity = get_object_or_404(Activity.objects.select_related('horse', 'work_type'), pk=pk)
    back = _safe_next(request, reverse('horse_detail', kwargs={'pk': activity.horse_id}))
    if request.method == 'POST':
        name = activity.horse.name
        activity.delete()
        messages.success(request, f"Training record for {name} deleted.")
        if is_popup_request(request):
            return popup_saved_response()
        return redirect(back)
    template = 'training/partials/activity_delete.html' if is_popup_request(request) else 'training/activity_delete.html'
    return render(request, template, {'activity': activity, 'next': back})


@training_enabled
@feature_required('training')
@require_POST
def activity_mark(request, pk):
    """Mark done or Did not work on one row (the horse page's Training card)."""
    activity = get_object_or_404(Activity, pk=pk)
    back = _safe_next(request, reverse('horse_detail', kwargs={'pk': activity.horse_id}))
    action = request.POST.get('action', '')
    if action == 'done':
        _report(request, services.mark_done([activity.pk], user=request.user), 'done')
    elif action == 'not_done':
        reason = request.POST.get('reason', '')
        if reason not in Activity.Reason.values:
            messages.error(request, "Choose why the horse did not work.")
            return redirect(back)
        _report(request, services.mark_not_done([activity.pk], reason, user=request.user), 'as did not work')
    else:
        return HttpResponseBadRequest('Unknown action')
    return redirect(back)


# ─── Set up: work types ──────────────────────────────────────────────

@training_enabled
@feature_required('training')
def training_setup(request):
    # An aggregate drops Meta.ordering, so the order is given here.
    work_types = WorkType.objects.annotate(uses=Count('activities')).order_by('position', 'name')
    return render(request, 'training/setup.html', {
        'active_types': [w for w in work_types if w.is_active],
        'archived_types': [w for w in work_types if not w.is_active],
        'training_tab': 'setup',
    })


class WorkTypeFormViewMixin:
    feature = 'training'
    model = WorkType
    form_class = WorkTypeForm
    template_name = 'training/work_type_form.html'
    popup_add_another = False

    def get_success_url(self):
        return reverse('training_setup')


class WorkTypeCreateView(TrainingEnabledMixin, WorkTypeFormViewMixin, PopupFormMixin,
                         FeatureAccessMixin, CreateView):
    def form_valid(self, form):
        messages.success(self.request, f"Work type {form.instance.name} added.")
        return super().form_valid(form)


class WorkTypeUpdateView(TrainingEnabledMixin, WorkTypeFormViewMixin, PopupFormMixin,
                         FeatureAccessMixin, UpdateView):
    def form_valid(self, form):
        messages.success(self.request, f"Work type {form.instance.name} saved.")
        return super().form_valid(form)


@training_enabled
@feature_required('training')
@require_POST
def work_type_archive(request, pk):
    """Archive a work type, or bring it back. Old records keep its name."""
    work_type = get_object_or_404(WorkType, pk=pk)
    work_type.is_active = not work_type.is_active
    work_type.save(update_fields=['is_active'])
    if work_type.is_active:
        messages.success(request, f"{work_type.name} is back in the lists.")
    else:
        messages.success(request, f"{work_type.name} archived. Old records keep it.")
    return redirect('training_setup')
