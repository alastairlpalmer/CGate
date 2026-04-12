"""
Owner views — CRUD and detail.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Prefetch, Q
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from ..forms import OwnerForm
from ..mixins import StaffRequiredMixin
from ..models import Horse, Owner, OwnershipShare, Placement


class OwnerListView(LoginRequiredMixin, ListView):
    model = Owner
    template_name = 'owners/owner_list.html'
    context_object_name = 'owners'

    def get_queryset(self):
        return Owner.objects.annotate(
            horse_count=Count(
                'ownership_shares__horse',
                filter=Q(
                    ownership_shares__horse__is_active=True,
                    ownership_shares__horse__placements__end_date__isnull=True,
                ),
                distinct=True,
            )
        ).order_by('name')


class OwnerDetailView(LoginRequiredMixin, DetailView):
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
        return context


class OwnerCreateView(StaffRequiredMixin, CreateView):
    model = Owner
    form_class = OwnerForm
    template_name = 'owners/owner_form.html'
    success_url = reverse_lazy('owner_list')


class OwnerUpdateView(StaffRequiredMixin, UpdateView):
    model = Owner
    form_class = OwnerForm
    template_name = 'owners/owner_form.html'

    def get_success_url(self):
        return reverse_lazy('owner_detail', kwargs={'pk': self.object.pk})
