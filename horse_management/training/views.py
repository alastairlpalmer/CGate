"""Training pages: the Log, Set up, and the pop-up forms (phase 1)."""

from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponseBadRequest
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

from . import board, load, services
from .access import TrainingEnabledMixin, training_enabled
from .forms import (
    ActivityForm, BoardSettingsForm, HorseTrainingForm, LotDayForm, LotForm, RiderForm, VenueForm,
    WeekPlanForm, WorkTypeForm, parse_who, who_value,
)
from .models import Activity, HorseTraining, Lot, LotDay, LotMember, Rider, TrainingSettings, Venue, WorkType

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
    """The sidebar's Training link: today's board (phase 2b)."""
    return redirect('training_board')


class ActivityLogView(TrainingEnabledMixin, FeatureAccessMixin, ListView):
    """The Log tab. ``?format=csv`` downloads every row the filters give."""

    def get(self, request, *args, **kwargs):
        if request.GET.get('format') == 'csv':
            return self.csv()
        return super().get(request, *args, **kwargs)

    def csv(self):
        from core.csv_export import csv_response
        rows = self.get_queryset().select_related('rider', 'venue').order_by('date', 'horse__name', 'pk')
        header = ['Date', 'Horse', 'Lot', 'Work', 'Effort', 'Minutes', 'Status', 'Reason',
                  'Rider', 'Venue', 'Gear', 'Notes']
        data = (
            [a.date, a.horse.name, a.lot_day.lot.name if a.lot_day_id else '', a.work_type.name,
             a.effort_label, a.minutes, a.status_label, a.get_reason_display() if a.reason else '',
             a.rider_label, a.venue.name if a.venue_id else '', a.gear, a.notes]
            for a in rows
        )
        return csv_response(f'training-log-{timezone.localdate().isoformat()}', header, data)

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
        queryset = Activity.objects.select_related('horse', 'work_type', 'updated_by', 'lot_day__lot')
        f = self._filters()
        if f['horse']:
            queryset = queryset.filter(horse_id=f['horse'])
        if f['lot']:
            # Work done in the lot on the board; work outside the board
            # counts for the lot the horse is in now.
            queryset = queryset.filter(
                Q(lot_day__lot_id=f['lot']) | Q(lot_day__isnull=True, horse__lot_members__lot_id=f['lot'])
            )
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
        board.apply_row_edit(form.instance, set(form.changed_data))
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
    elif action == 'planned':
        _report(request, services.mark_planned([activity.pk], user=request.user), 'as planned again')
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
    lots = list(Lot.objects.active().select_related('usual_work_type').prefetch_related(
        'week_plan__work_type', 'week_plan__venue'))
    for lot in lots:
        by_day = {p.weekday: p for p in lot.week_plan.all()}
        lot.week = [by_day.get(day) for day in range(7)]
    return render(request, 'training/setup.html', {
        'active_types': [w for w in work_types if w.is_active],
        'archived_types': [w for w in work_types if not w.is_active],
        'board_form': board_form,
        'venues': Venue.objects.all(),
        'riders': Rider.objects.select_related('user'),
        'week_lots': lots,
        'weekdays': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
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
        'boards_started': board.boards_started_from(timezone.localdate()),
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
        removed = 0
        if request.POST.get('also_boards'):
            removed = board.remove_from_started_boards(horse, member.lot)
        note = f" Taken off {removed} planned day{'s' if removed != 1 else ''} on the board." if removed else ''
        messages.success(request, f"{horse.name} removed from {member.lot.name}.{note}")
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
        'default_who': who_value(row.default_rider_id, row.default_rider_is_owner) if row else '',
        'default_gear': row.default_gear if row else '',
        'days_per_week': row.days_per_week if row else None,
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
        if 'default_who' in form.changed_data or 'default_gear' in form.changed_data:
            rider, is_owner = parse_who(data['default_who'])
            services.set_defaults(horse, rider=rider, rider_is_owner=is_owner, gear=data['default_gear'])
        if 'days_per_week' in form.changed_data:
            services.set_days_per_week(horse, data['days_per_week'])
        if chose_status and data.get('update_boards'):
            effect = board.apply_status_to_boards(horse, data['status'], data['status_since'], user=request.user)
            if effect.not_done or effect.removed:
                messages.info(request, f"Lot boards changed for {horse.name}: "
                                       f"{effect.not_done} marked Did not work, {effect.removed} planned day"
                                       f"{'s' if effect.removed != 1 else ''} removed.")
            if effect.added_to:
                messages.info(request, f"{horse.name} is on today's board in {effect.added_to.lot.name}.")
        messages.success(request, f"Training for {horse.name} saved.")
        if is_popup_request(request):
            return popup_saved_response()
        return redirect(reverse('horse_detail', kwargs={'pk': horse.pk}) + '#training')
    template = 'training/partials/horse_training_form.html' if is_popup_request(request) else 'training/horse_training_form.html'
    return render(request, template, {
        'horse': horse, 'form': form,
        'boards_started': board.boards_started_from(timezone.localdate()),
    })


# ─── Phase 2b: the lot board ─────────────────────────────────────────

def _parse_day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise Http404("Not a date.")


def _board_url(day, anchor=''):
    if day == timezone.localdate():
        url = reverse('training_board')
    else:
        url = reverse('training_board_day', args=[day.isoformat()])
    return url + (f'#{anchor}' if anchor else '')


@training_enabled
@feature_required('training', LEVEL_VIEW)
def training_board(request, day=None):
    """The lot board for one date (8.2). Showing it never writes it (5.3)."""
    today = timezone.localdate()
    day = _parse_day(day) if day else today
    can_change = has_feature_access(request.user, 'training', LEVEL_FULL)
    lot_days = board.board_days(day)
    rows = list(
        Activity.objects.filter(lot_day__in=lot_days)
        .select_related('horse', 'horse__training', 'work_type', 'rider', 'venue')
        .order_by('position', 'pk')
    )
    # The Rider filter shows one rider's horses; a rider with a login sees
    # their own horses first, in a card at the top (phase 3).
    rider_filter = request.GET.get('rider', '')
    my_rider = Rider.objects.filter(user=request.user, is_active=True).first()
    lot_names = {ld.pk: ld.lot.name for ld in lot_days}
    my_rows = [r for r in rows if my_rider and r.rider_id == my_rider.pk]
    for r in my_rows:
        r.lot_name = lot_names.get(r.lot_day_id, '')
    if rider_filter == 'owner':
        rows = [r for r in rows if r.rider_is_owner]
    elif rider_filter.isdigit():
        rows = [r for r in rows if str(r.rider_id) == rider_filter]
    by_day = {}
    for row in rows:
        by_day.setdefault(row.lot_day_id, []).append(row)
    # One workload fact on each row: the most important one (plan 9.5).
    if rows:
        history = load.rows_for({r.horse_id for r in rows}, day)
        threshold = TrainingSettings.load().no_work_days
        for row in rows:
            status = getattr(row.horse, 'training', None)
            facts = load.chips(
                history.get(row.horse_id, []), day, threshold=threshold,
                days_per_week=status.days_per_week if status else None, on_board=True,
                since=status.status_since if status else None,
            )
            row.fact = facts[0] if facts else None
    on_board = board.on_board_horse_ids() if lot_days and can_change else set()
    taken = board.horses_on_board(day) if lot_days else set()
    horse_count = 0
    for lot_day in lot_days:
        lot_day.rows = by_day.get(lot_day.pk, [])
        horse_count += len(lot_day.rows)
        lot_day.missing = (
            board.missing_members(lot_day, today=today, on_board=on_board, taken=taken) if can_change else []
        )
        lot_day.can_mark_done = lot_day.is_open and day <= today
        lot_day.other_days = [other for other in lot_days if other.pk != lot_day.pk and other.is_open]
    last_not_confirmed = board.last_not_confirmed_before(today)
    settings_row = TrainingSettings.load()
    return render(request, 'training/board.html', {
        'day': day,
        'today': today,
        'is_today': day == today,
        'is_future': day > today,
        'previous_day': day - timedelta(days=1),
        'next_day': day + timedelta(days=1),
        'lot_days': lot_days,
        'horse_count': horse_count,
        'done_count': sum(1 for ld in lot_days if ld.status == LotDay.Status.DONE),
        'lots_without_day': list(Lot.objects.active().exclude(days__date=day)) if lot_days else [],
        'not_on_board': board.not_on_board_summary(day) if lot_days else [],
        'last_not_confirmed': last_not_confirmed if last_not_confirmed != day else None,
        'sources': [(v, dict(TrainingSettings.BoardSource.choices)[v]) for v in TrainingSettings.OFFERED_SOURCES],
        'default_source': settings_row.board_source,
        'last_board': board.last_board_before(day),
        'reasons': Activity.Reason.choices,
        'riders': Rider.objects.active(),
        'rider_filter': rider_filter,
        'my_rider': my_rider,
        'my_rows': my_rows,
        'can_change': can_change,
        'can_record_health': has_feature_access(request.user, 'health', LEVEL_FULL),
        'board_url': request.get_full_path(),
        'training_tab': 'board',
    })


@training_enabled
@feature_required('training')
@require_POST
def board_start(request, day):
    day = _parse_day(day)
    result = board.start_board(day, user=request.user, source=request.POST.get('source') or None)
    if result.note:
        messages.info(request, result.note)
    if result.created:
        count = Activity.objects.filter(lot_day__in=result.lot_days).count()
        messages.success(request, f"Board started: {len(result.lot_days)} lots, {count} horses.")
    return redirect(_board_url(day))


@training_enabled
@feature_required('training')
@require_POST
def board_add_lot(request, day, lot_pk):
    day = _parse_day(day)
    lot = get_object_or_404(Lot, pk=lot_pk, is_active=True)
    lot_day = board.add_lot_to_board(lot, day, user=request.user)
    messages.success(request, f"{lot.name} added to the board.")
    return redirect(_board_url(day, f'lot-day-{lot_day.pk}'))


@training_enabled
@feature_required('training')
@require_POST
def lot_day_action(request, pk, action):
    """Mark lot done, Reopen lot, or Cancel lot with one reason."""
    lot_day = get_object_or_404(LotDay.objects.select_related('lot'), pk=pk)
    try:
        if action == 'done':
            result = board.mark_lot_done(lot_day, user=request.user)
            messages.success(request, f"{lot_day.lot.name} done: {result.changed} horse{'s' if result.changed != 1 else ''}.")
            if result.left_yard:
                messages.warning(request, f"Left the yard, so not marked: {', '.join(result.left_yard)}.")
        elif action == 'reopen':
            board.reopen_lot(lot_day, user=request.user)
            messages.success(request, f"{lot_day.lot.name} is open again.")
        elif action == 'cancel':
            count = board.cancel_lot(lot_day, request.POST.get('reason', ''), user=request.user)
            messages.success(request, f"{lot_day.lot.name} cancelled: {count} horse{'s' if count != 1 else ''} did not work.")
        else:
            return HttpResponseBadRequest('Unknown action')
    except board.BoardError as error:
        messages.error(request, str(error))
    if is_popup_request(request):
        return popup_saved_response()
    return redirect(_board_url(lot_day.date, f'lot-day-{lot_day.pk}'))


@training_enabled
@feature_required('training')
def lot_day_change(request, pk):
    """The lot's work, time and notes for this date, and Cancel lot."""
    lot_day = get_object_or_404(LotDay.objects.select_related('lot', 'work_type'), pk=pk)
    form = LotDayForm(request.POST or None, instance=lot_day)
    if request.method == 'POST' and form.is_valid():
        try:
            count = board.set_lot_work(
                lot_day, work_type=form.cleaned_data['work_type'], start_time=form.cleaned_data['start_time'],
                notes=form.cleaned_data['notes'], venue=form.cleaned_data['venue'], user=request.user,
            )
            messages.success(request, f"{lot_day.lot.name} saved. {count} horse{'s' if count != 1 else ''} follow the new work.")
        except board.BoardError as error:
            messages.error(request, str(error))
        if is_popup_request(request):
            return popup_saved_response()
        return redirect(_board_url(lot_day.date, f'lot-day-{lot_day.pk}'))
    template = 'training/partials/lot_day_change.html' if is_popup_request(request) else 'training/lot_day_change.html'
    return render(request, template, {'lot_day': lot_day, 'form': form, 'reasons': Activity.Reason.choices})


@training_enabled
@feature_required('training')
def lot_day_horses(request, pk):
    """Add horses to this lot day only. The standing lot does not change."""
    lot_day = get_object_or_404(LotDay.objects.select_related('lot'), pk=pk)
    if request.method == 'POST':
        ids = [i for i in request.POST.getlist('horses') if i.isdigit()]
        try:
            result = board.add_horses(lot_day, list(Horse.objects.filter(pk__in=ids).order_by('name')), user=request.user)
        except board.BoardError as error:
            messages.error(request, str(error))
        else:
            if result.added:
                messages.success(request, f"On {lot_day.lot.name} for the day: {', '.join(result.added)}.")
            if result.already:
                messages.info(request, f"Already in {lot_day.lot.name}: {', '.join(result.already)}.")
            if result.left:
                messages.warning(request, f"Not added, as they have left the yard: {', '.join(result.left)}.")
        if is_popup_request(request):
            return popup_saved_response()
        return redirect(_board_url(lot_day.date, f'lot-day-{lot_day.pk}'))
    here = set(lot_day.activities.values_list('horse_id', flat=True))
    elsewhere = dict(
        Activity.objects.filter(date=lot_day.date, lot_day__isnull=False).exclude(lot_day=lot_day)
        .values_list('horse_id', 'lot_day__lot__name')
    )
    candidates = [
        {'horse': h, 'here': h.pk in here, 'elsewhere': elsewhere.get(h.pk, '')}
        for h in Horse.objects.filter(is_active=True).order_by('name').only('pk', 'name')
    ]
    template = 'training/partials/lot_day_horses.html' if is_popup_request(request) else 'training/lot_day_horses.html'
    return render(request, template, {'lot_day': lot_day, 'candidates': candidates})


@training_enabled
@feature_required('training')
@require_POST
def activity_move(request, pk):
    """Move one row to another lot on the same date."""
    activity = get_object_or_404(Activity.objects.select_related('horse'), pk=pk)
    target = get_object_or_404(LotDay.objects.select_related('lot'), pk=request.POST.get('lot_day') or 0)
    try:
        board.move_horse(activity, target, user=request.user)
        messages.success(request, f"{activity.horse.name} moved to {target.lot.name} for the day.")
    except board.BoardError as error:
        messages.error(request, str(error))
    return redirect(_safe_next(request, _board_url(target.date, f'lot-day-{target.pk}')))


# ─── Phase 3: venues, riders, the week plan ──────────────────────────

class SetupFormViewMixin:
    feature = 'training'
    popup_add_another = False
    template_name = 'training/simple_form.html'

    def get_success_url(self):
        return reverse('training_setup')

    def form_valid(self, form):
        messages.success(self.request, f"{form.instance} saved.")
        return super().form_valid(form)


class VenueCreateView(TrainingEnabledMixin, SetupFormViewMixin, PopupFormMixin, FeatureAccessMixin, CreateView):
    model = Venue
    form_class = VenueForm


class VenueUpdateView(TrainingEnabledMixin, SetupFormViewMixin, PopupFormMixin, FeatureAccessMixin, UpdateView):
    model = Venue
    form_class = VenueForm


class RiderCreateView(TrainingEnabledMixin, SetupFormViewMixin, PopupFormMixin, FeatureAccessMixin, CreateView):
    model = Rider
    form_class = RiderForm


class RiderUpdateView(TrainingEnabledMixin, SetupFormViewMixin, PopupFormMixin, FeatureAccessMixin, UpdateView):
    model = Rider
    form_class = RiderForm


@training_enabled
@feature_required('training')
@require_POST
def setup_archive(request, kind, pk):
    """Archive or restore a venue or a rider. Old records keep the name."""
    model = {'venue': Venue, 'rider': Rider}.get(kind)
    if model is None:
        raise Http404
    item = get_object_or_404(model, pk=pk)
    item.is_active = not item.is_active
    item.save(update_fields=['is_active'])
    messages.success(request, f"{item} {'is back' if item.is_active else 'archived. Old records keep it'}.")
    return redirect('training_setup')


@training_enabled
@feature_required('training')
def lot_week_plan(request, pk):
    """The usual work and venue of one lot for each day of the week."""
    lot = get_object_or_404(Lot.objects.select_related('usual_work_type'), pk=pk, is_active=True)
    form = WeekPlanForm(request.POST or None, lot=lot)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f"Week plan for {lot.name} saved.")
        if is_popup_request(request):
            return popup_saved_response()
        return redirect('training_setup')
    template = 'training/partials/week_plan_form.html' if is_popup_request(request) else 'training/week_plan_form.html'
    return render(request, template, {'lot': lot, 'form': form})


# ─── Phase 4: print, TV and CSV ──────────────────────────────────────

@training_enabled
@feature_required('training', LEVEL_VIEW)
def board_sheet(request, day, mode):
    """The board on paper (``print``) or on a tack-room TV (``tv``): no buttons.
    The TV page reloads itself each minute (8.6)."""
    if mode not in ('print', 'tv'):
        raise Http404
    day = _parse_day(day)
    lot_days = board.board_days(day)
    rows = (
        Activity.objects.filter(lot_day__in=lot_days)
        .select_related('horse', 'work_type', 'rider', 'venue').order_by('position', 'pk')
    )
    by_day = {}
    for row in rows:
        by_day.setdefault(row.lot_day_id, []).append(row)
    for lot_day in lot_days:
        lot_day.rows = by_day.get(lot_day.pk, [])
    return render(request, 'training/board_sheet.html', {
        'day': day, 'lot_days': lot_days, 'mode': mode, 'now': timezone.localtime(),
    })
