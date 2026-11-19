"""Training pages: the Log, Set up, and the pop-up forms (phase 1)."""

from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import Count, Prefetch
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
from .forms import ActivityForm, BoardSettingsForm, HorseTrainingForm, LotForm, WorkTypeForm
from .models import Activity, HorseTraining, Lot, LotMember, TrainingSettings, WorkType

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
            'lot': get.get('lot', '') if get.get('lot', '').isdigit() else '',
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
        if f['lot']:
            # Horses now in the lot. Phase 2b adds the lot of each day.
            queryset = queryset.filter(horse__lot_members__lot_id=f['lot'])
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
            'lots': Lot.objects.all(),
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
    board_form = BoardSettingsForm(instance=TrainingSettings.load())
    if request.method == 'POST':
        board_form = BoardSettingsForm(request.POST, instance=TrainingSettings.load())
        if board_form.is_valid():
            board_form.save()
            messages.success(request, "Lot board setting saved.")
            return redirect('training_setup')
    return render(request, 'training/setup.html', {
        'active_types': [w for w in work_types if w.is_active],
        'archived_types': [w for w in work_types if not w.is_active],
        'board_form': board_form,
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


# ─── Phase 2a: lots and training status ──────────────────────────────

@training_enabled
@feature_required('training', LEVEL_VIEW)
def training_lots(request):
    """The Lots tab: the standing lots, their horses in order (8.3)."""
    members = LotMember.objects.select_related('horse', 'horse__training').order_by('position', 'pk')
    lots = list(Lot.objects.select_related('usual_work_type').prefetch_related(Prefetch('members', queryset=members)))
    for lot in lots:
        everyone = list(lot.members.all())
        lot.on_yard = [m for m in everyone if m.horse.is_active]
        lot.left_yard = [m for m in everyone if not m.horse.is_active]
    in_work_no_lot = (
        Horse.objects.filter(is_active=True, training__status__in=HorseTraining.ON_BOARD, lot_members__isnull=True)
        .order_by('name').only('pk', 'name')
    )
    return render(request, 'training/lots.html', {
        'lots': [lot for lot in lots if lot.is_active],
        'archived_lots': [lot for lot in lots if not lot.is_active],
        'other_lots': Lot.objects.active(),
        'in_work_no_lot': list(in_work_no_lot),
        'can_change': has_feature_access(request.user, 'training', LEVEL_FULL),
        'training_tab': 'lots',
    })


class LotFormViewMixin:
    feature = 'training'
    model = Lot
    form_class = LotForm
    template_name = 'training/lot_form.html'
    popup_add_another = False

    def get_success_url(self):
        return reverse('training_lots')


class LotCreateView(TrainingEnabledMixin, LotFormViewMixin, PopupFormMixin, FeatureAccessMixin, CreateView):
    def get_initial(self):
        initial = super().get_initial()
        top = Lot.objects.order_by('-position').values_list('position', flat=True).first()
        initial['position'] = 0 if top is None else top + 1
        return initial

    def form_valid(self, form):
        messages.success(self.request, f"{form.instance.name} added. Now add its horses.")
        return super().form_valid(form)


class LotUpdateView(TrainingEnabledMixin, LotFormViewMixin, PopupFormMixin, FeatureAccessMixin, UpdateView):
    def form_valid(self, form):
        messages.success(self.request, f"{form.instance.name} saved.")
        return super().form_valid(form)


@training_enabled
@feature_required('training')
@require_POST
def lot_archive(request, pk):
    """Archive an empty lot, or bring an archived one back (7.3)."""
    lot = get_object_or_404(Lot, pk=pk)
    if lot.is_active:
        error = services.archive_lot(lot)
        if error:
            messages.error(request, error)
        else:
            messages.success(request, f"{lot.name} archived.")
    else:
        lot.is_active = True
        lot.save(update_fields=['is_active'])
        messages.success(request, f"{lot.name} is back.")
    return redirect('training_lots')


@training_enabled
@feature_required('training')
def lot_add_horses(request, pk):
    """Pick horses for a lot. A horse in another lot shows that lot's name."""
    lot = get_object_or_404(Lot, pk=pk, is_active=True)
    if request.method == 'POST':
        ids = [i for i in request.POST.getlist('horses') if i.isdigit()]
        horses = list(Horse.objects.filter(pk__in=ids).order_by('name'))
        result = services.add_to_lot(lot, horses)
        if result.added:
            names = ', '.join(h.name for h in result.added)
            messages.success(request, f"Added to {lot.name}: {names}.")
        if result.started_work:
            names = ', '.join(h.name for h in result.started_work)
            messages.info(request, f"Now In work: {names}.")
        for horse, other in result.already:
            messages.warning(request, f"{horse.name} is already in {other.name}. Use Move to.")
        if result.left:
            messages.warning(request, f"Not added, as they have left the yard: {', '.join(h.name for h in result.left)}.")
        if not (result.added or result.already or result.left):
            messages.info(request, "Tick at least one horse.")
        if is_popup_request(request):
            return popup_saved_response()
        return redirect('training_lots')
    current = dict(LotMember.objects.values_list('horse_id', 'lot__name'))
    candidates = [
        {'horse': h, 'lot_name': current.get(h.pk, '')}
        for h in Horse.objects.filter(is_active=True).order_by('name').only('pk', 'name')
    ]
    template = 'training/partials/lot_add_horses.html' if is_popup_request(request) else 'training/lot_add_horses.html'
    return render(request, template, {'lot': lot, 'candidates': candidates})


@training_enabled
@feature_required('training')
@require_POST
def lot_member_action(request, pk):
    """Up, Down, Move to another lot, or Remove, for one horse in a lot."""
    member = get_object_or_404(LotMember.objects.select_related('horse', 'lot'), pk=pk)
    action = request.POST.get('action', '')
    horse = member.horse
    if action in ('up', 'down'):
        services.move_member(member, action)
    elif action == 'remove':
        services.remove_from_lot(horse)
        messages.success(request, f"{horse.name} removed from {member.lot.name}.")
    elif action == 'move':
        target_id = request.POST.get('lot', '')
        target = Lot.objects.filter(pk=target_id, is_active=True).first() if target_id.isdigit() else None
        if target is None:
            messages.error(request, "Choose the lot to move to.")
        else:
            services.move_to_lot(horse, target)
            messages.success(request, f"{horse.name} moved to {target.name}.")
    else:
        return HttpResponseBadRequest('Unknown action')
    return redirect(_safe_next(request, reverse('training_lots')))


@training_enabled
@feature_required('training')
def horse_training_edit(request, horse_pk):
    """Training status and standing lot for one horse (the horse card's Change)."""
    horse = get_object_or_404(Horse, pk=horse_pk)
    row = HorseTraining.objects.filter(horse=horse).first()
    current_lot = services.standing_lot(horse)
    initial = {
        'status': row.status if row else HorseTraining.Status.NOT_IN_TRAINING,
        'status_since': row.status_since if row else timezone.localdate(),
        'lot': current_lot,
        'notes': row.notes if row else '',
    }
    form = HorseTrainingForm(request.POST or None, initial=initial, current_lot=current_lot)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        chose_status = 'status' in form.changed_data
        new_lot = data['lot']
        if new_lot is None and current_lot is not None:
            services.remove_from_lot(horse)
        elif new_lot is not None and (current_lot is None or new_lot.pk != current_lot.pk):
            if not horse.is_active:
                messages.warning(request, f"{horse.name} has left the yard, so cannot join a lot.")
            else:
                services.move_to_lot(horse, new_lot, auto_status=not chose_status, day=data['status_since'])
        if chose_status or 'status_since' in form.changed_data or 'notes' in form.changed_data:
            services.set_status(
                horse, data['status'], data['status_since'], notes=data['notes'],
                since=data['status_since'] if 'status_since' in form.changed_data else None,
            )
        messages.success(request, f"Training for {horse.name} saved.")
        if is_popup_request(request):
            return popup_saved_response()
        return redirect(reverse('horse_detail', kwargs={'pk': horse.pk}) + '#training')
    template = 'training/partials/horse_training_form.html' if is_popup_request(request) else 'training/horse_training_form.html'
    return render(request, template, {'horse': horse, 'form': form})
