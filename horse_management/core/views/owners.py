"""
Owner views — CRUD and detail.
"""

from django.contrib import messages
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from ..forms import OwnerForm
from ._popup import PopupFormMixin
from ..permissions import LEVEL_VIEW, FeatureAccessMixin, feature_required
from ..models import Horse, Owner, OwnershipShare, Placement


class OwnerListView(FeatureAccessMixin, ListView):
    feature = 'owners'
    access_level = LEVEL_VIEW
    model = Owner
    template_name = 'owners/owner_list.html'
    context_object_name = 'owners'

    @property
    def showing_archived(self):
        return self.request.GET.get('archived') == '1'

    def get_queryset(self):
        queryset = Owner.objects.archived() if self.showing_archived else Owner.objects.active()
        queryset = queryset.annotate(
            horse_count=Count(
                'ownership_shares__horse',
                filter=Q(
                    ownership_shares__horse__is_active=True,
                    ownership_shares__horse__placements__end_date__isnull=True,
                ),
                distinct=True,
            )
        )
        search = self.request.GET.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
            )
        return queryset.order_by('name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['showing_archived'] = self.showing_archived
        context['archived_count'] = Owner.objects.archived().count()
        return context


class OwnerDetailView(FeatureAccessMixin, DetailView):
    feature = 'owners'
    access_level = LEVEL_VIEW
    model = Owner
    template_name = 'owners/owner_detail.html'
    context_object_name = 'owner'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Single query for all horses, split active/departed in Python
        active_placements = Prefetch(
            'placements',
            queryset=Placement.objects.filter(
                end_date__isnull=True
            ).select_related('location'),
            to_attr='active_placements',
        )
        last_placements = Prefetch(
            'placements',
            queryset=Placement.objects.select_related('location').order_by('-end_date'),
            to_attr='last_placements',
        )
        shares = OwnershipShare.objects.filter(owner=self.object).select_related('horse')
        share_map = {s.horse_id: s.share_percentage for s in shares}

        all_horses = list(Horse.objects.filter(
            ownership_shares__owner=self.object,
        ).distinct().prefetch_related(active_placements, last_placements))

        # Attach share_pct and split into active/departed
        active_horses = []
        departed_horses = []
        for horse in all_horses:
            horse.share_pct = share_map.get(horse.pk)
            if horse.is_active:
                active_horses.append(horse)
            else:
                departed_horses.append(horse)

        context['horses'] = active_horses
        context['departed_horses'] = departed_horses

        context['invoices'] = self.object.invoices.all()[:10]
        context['extra_charges'] = self.object.extra_charges.filter(
            invoiced=False
        ).select_related('horse')
        # One button, decided here: delete when nothing points at the
        # owner, otherwise archive (or say what blocks it).
        owner = self.object
        context['owner_history'] = owner.history_counts()
        context['archive_blockers'] = [] if owner.is_archived else owner.archive_blockers()
        return context


class OwnerCreateView(PopupFormMixin, FeatureAccessMixin, CreateView):
    feature = 'owners'
    model = Owner
    form_class = OwnerForm
    template_name = 'owners/owner_form.html'
    success_url = reverse_lazy('owner_list')


class OwnerUpdateView(PopupFormMixin, FeatureAccessMixin, UpdateView):
    feature = 'owners'
    model = Owner
    form_class = OwnerForm
    template_name = 'owners/owner_form.html'

    def get_success_url(self):
        return reverse_lazy('owner_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Owner '{self.object.name}' updated.")
        return response


# ── Archive / restore / delete ───────────────────────────────────────────
#
# An owner with history is archived: hidden from the Owners page and every
# picker, with every stay and invoice kept. Only an owner nothing points at
# is deleted. The owner page shows one button and says which it will do;
# the delete view also falls back to archiving if history appeared since
# the page was drawn.

def _safe_next(request, fallback):
    next_url = request.POST.get('next') or request.GET.get('next') or ''
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback


def _archive_owner(owner):
    """Archive one owner. Returns the blocking reasons (empty = archived)."""
    blockers = owner.archive_blockers()
    if blockers:
        return blockers
    owner.is_archived = True
    owner.archived_at = timezone.now()
    owner.save(update_fields=['is_archived', 'archived_at', 'updated_at'])
    return []


@feature_required('owners')
@require_POST
def owner_archive(request, pk):
    """Archive an owner: hide them from lists and pickers, keep the records."""
    owner = get_object_or_404(Owner, pk=pk)
    fallback = reverse('owner_detail', kwargs={'pk': owner.pk})
    if owner.is_archived:
        messages.info(request, f"{owner.name} is already archived.")
        return redirect(_safe_next(request, fallback))
    blockers = _archive_owner(owner)
    if blockers:
        messages.error(request, f"{owner.name} can't be archived. " + ' '.join(blockers))
    else:
        messages.success(
            request,
            f"{owner.name} archived. Their stays and invoices are kept, and "
            "you can restore them from their page.",
        )
    return redirect(_safe_next(request, fallback))


@feature_required('owners')
@require_POST
def owner_restore(request, pk):
    """Bring an archived owner back into lists and pickers."""
    owner = get_object_or_404(Owner, pk=pk)
    if owner.is_archived:
        owner.is_archived = False
        owner.archived_at = None
        owner.save(update_fields=['is_archived', 'archived_at', 'updated_at'])
        messages.success(request, f"{owner.name} restored.")
    else:
        messages.info(request, f"{owner.name} is already in use.")
    return redirect(_safe_next(request, reverse('owner_detail', kwargs={'pk': owner.pk})))


@feature_required('owners')
@require_POST
def owner_delete(request, pk):
    """Delete an owner nothing points at; archive one with history instead."""
    owner = get_object_or_404(Owner, pk=pk)
    fallback = reverse('owner_detail', kwargs={'pk': owner.pk})
    if owner.has_history:
        blockers = _archive_owner(owner)
        if blockers:
            messages.error(
                request,
                f"{owner.name} has history, so they can only be archived, and "
                "not yet: " + ' '.join(blockers),
            )
            return redirect(_safe_next(request, fallback))
        messages.success(
            request,
            f"{owner.name} has stays or invoices, so they were archived rather "
            "than deleted. Every record is kept.",
        )
        return redirect(_safe_next(request, fallback))

    name = owner.name
    owner.delete()
    messages.success(request, f"{name} deleted.")
    return redirect(_safe_next(request, reverse('owner_list')))
