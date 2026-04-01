"""
Views for billing app.
"""

from datetime import date
from decimal import Decimal
from itertools import chain
from operator import attrgetter

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin

from core.mixins import StaffRequiredMixin, staff_required
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from core.models import Horse, Owner

from .forms import ExtraChargeForm, FeedOutForm, ServiceProviderForm, YardCostForm
from .models import ExtraCharge, FeedOut, ServiceProvider, YardCost


class ExtraChargeListView(LoginRequiredMixin, ListView):
    model = ExtraCharge
    template_name = 'billing/charge_list.html'
    context_object_name = 'charges'
    paginate_by = 50

    def get_queryset(self):
        queryset = ExtraCharge.objects.select_related(
            'horse', 'owner', 'service_provider', 'invoice'
        )

        # Filter by invoiced status
        invoiced = self.request.GET.get('invoiced')
        if invoiced == 'yes':
            queryset = queryset.filter(invoiced=True)
        elif invoiced == 'no':
            queryset = queryset.filter(invoiced=False)

        # Filter by type
        charge_type = self.request.GET.get('type')
        if charge_type:
            queryset = queryset.filter(charge_type=charge_type)

        # Filter by horse
        horse = self.request.GET.get('horse')
        if horse:
            queryset = queryset.filter(horse_id=horse)

        # Filter by owner
        owner = self.request.GET.get('owner')
        if owner:
            queryset = queryset.filter(owner_id=owner)

        return queryset.order_by('-date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.filter(is_active=True)
        context['owners'] = Owner.objects.all()
        context['charge_types'] = ExtraCharge.ChargeType.choices
        return context


class ExtraChargeCreateView(StaffRequiredMixin, CreateView):
    model = ExtraCharge
    form_class = ExtraChargeForm
    template_name = 'billing/charge_form.html'
    success_url = reverse_lazy('charge_list')

    def get_initial(self):
        initial = super().get_initial()
        horse_id = self.request.GET.get('horse')
        if horse_id:
            initial['horse'] = horse_id
            try:
                horse = Horse.objects.get(pk=horse_id)
                if horse.current_owner:
                    initial['owner'] = horse.current_owner.pk
            except Horse.DoesNotExist:
                pass
        initial['date'] = timezone.now().date()
        return initial

    def form_valid(self, form):
        messages.success(self.request, "Charge added successfully.")
        return super().form_valid(form)


class ExtraChargeUpdateView(StaffRequiredMixin, UpdateView):
    model = ExtraCharge
    form_class = ExtraChargeForm
    template_name = 'billing/charge_form.html'
    success_url = reverse_lazy('charge_list')

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.invoiced:
            messages.error(request, "This charge has already been invoiced and cannot be edited.")
            return redirect('charge_list')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        messages.success(self.request, "Charge updated successfully.")
        return super().form_valid(form)


class ExtraChargeDeleteView(StaffRequiredMixin, DeleteView):
    model = ExtraCharge
    template_name = 'billing/charge_confirm_delete.html'
    success_url = reverse_lazy('charge_list')

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.invoiced:
            messages.error(request, "This charge has already been invoiced and cannot be deleted.")
            return redirect('charge_list')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        messages.success(self.request, "Charge deleted successfully.")
        return super().form_valid(form)


class ServiceProviderListView(LoginRequiredMixin, ListView):
    model = ServiceProvider
    template_name = 'billing/provider_list.html'
    context_object_name = 'providers'

    def get_queryset(self):
        queryset = ServiceProvider.objects.all()

        provider_type = self.request.GET.get('type')
        if provider_type:
            queryset = queryset.filter(provider_type=provider_type)

        return queryset.order_by('provider_type', 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['provider_types'] = ServiceProvider.ProviderType.choices
        return context


class ServiceProviderCreateView(StaffRequiredMixin, CreateView):
    model = ServiceProvider
    form_class = ServiceProviderForm
    template_name = 'billing/provider_form.html'
    success_url = reverse_lazy('provider_list')


class ServiceProviderUpdateView(StaffRequiredMixin, UpdateView):
    model = ServiceProvider
    form_class = ServiceProviderForm
    template_name = 'billing/provider_form.html'
    success_url = reverse_lazy('provider_list')


# ── Unified Costs View ──────────────────────────────────────────

class CostsListView(StaffRequiredMixin, ListView):
    template_name = 'billing/costs_list.html'
    context_object_name = 'costs'
    paginate_by = 50

    def _date_range(self):
        """Return (start_date, end_date, period_label) based on GET params."""
        today = date.today()
        period = self.request.GET.get('period', 'month')
        if period == 'quarter':
            q_start_month = ((today.month - 1) // 3) * 3 + 1
            start = today.replace(month=q_start_month, day=1)
            label = 'This Quarter'
        elif period == 'year':
            start = today.replace(month=1, day=1)
            label = 'This Year'
        elif period == 'custom':
            from_str = self.request.GET.get('from', '')
            to_str = self.request.GET.get('to', '')
            try:
                start = date.fromisoformat(from_str)
                end = date.fromisoformat(to_str)
                return start, end, 'Custom'
            except (ValueError, TypeError):
                start = today.replace(day=1)
                label = 'This Month'
        else:
            start = today.replace(day=1)
            label = 'This Month'
        return start, today, label

    def get_queryset(self):
        # We don't use a real queryset — we merge in get_context_data
        return ExtraCharge.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = date.today()
        start_date, end_date, period_label = self._date_range()

        # Base querysets
        charges_qs = ExtraCharge.objects.select_related(
            'horse', 'owner', 'service_provider'
        )
        yard_qs = YardCost.objects.all()

        # Apply date filter
        charges_qs = charges_qs.filter(date__gte=start_date, date__lte=end_date)
        yard_qs = yard_qs.filter(date__gte=start_date, date__lte=end_date)

        # Category filter
        category = self.request.GET.get('category', '')
        if category:
            charges_qs = charges_qs.filter(charge_type=category)
            yard_qs = yard_qs.filter(category=category)

        # Rechargeable filter
        source = self.request.GET.get('source', '')
        if source == 'rechargeable':
            yard_qs = YardCost.objects.none()
        elif source == 'yard':
            charges_qs = ExtraCharge.objects.none()

        # Supplier search
        supplier = self.request.GET.get('supplier', '')
        if supplier:
            charges_qs = charges_qs.filter(service_provider__name__icontains=supplier)
            yard_qs = yard_qs.filter(supplier__icontains=supplier)

        # Normalize into dicts for merged display
        merged = []
        for c in charges_qs:
            merged.append({
                'source': 'charge',
                'date': c.date,
                'category': c.get_charge_type_display(),
                'category_key': c.charge_type,
                'description': c.description,
                'supplier': c.service_provider.name if c.service_provider else '',
                'amount': c.amount,
                'horse': c.horse.name if c.horse else '',
                'horse_pk': c.horse.pk if c.horse else None,
                'invoiced': c.invoiced,
                'is_recurring': False,
                'pk': c.pk,
                'edit_url': f'/billing/charges/{c.pk}/edit/',
                'delete_url': f'/billing/charges/{c.pk}/delete/',
            })
        for y in yard_qs:
            merged.append({
                'source': 'yard',
                'date': y.date,
                'category': y.get_category_display(),
                'category_key': y.category,
                'description': y.description,
                'supplier': y.supplier,
                'amount': y.amount,
                'horse': '',
                'horse_pk': None,
                'invoiced': False,
                'is_recurring': y.is_recurring,
                'pk': y.pk,
                'edit_url': f'/billing/costs/yard/{y.pk}/edit/',
                'delete_url': f'/billing/costs/yard/{y.pk}/delete/',
            })
        merged.sort(key=lambda x: x['date'], reverse=True)

        # Paginate the merged list
        paginator = Paginator(merged, self.paginate_by)
        page_number = self.request.GET.get('page', 1)
        page_obj = paginator.get_page(page_number)

        context['costs'] = page_obj
        context['page_obj'] = page_obj
        context['paginator'] = paginator
        context['is_paginated'] = page_obj.has_other_pages()

        # Summary totals
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)
        context['month_total'] = (
            (ExtraCharge.objects.filter(date__gte=month_start).aggregate(s=Sum('amount'))['s'] or Decimal('0')) +
            (YardCost.objects.filter(date__gte=month_start).aggregate(s=Sum('amount'))['s'] or Decimal('0'))
        )
        context['year_total'] = (
            (ExtraCharge.objects.filter(date__gte=year_start).aggregate(s=Sum('amount'))['s'] or Decimal('0')) +
            (YardCost.objects.filter(date__gte=year_start).aggregate(s=Sum('amount'))['s'] or Decimal('0'))
        )
        context['unbilled_total'] = (
            ExtraCharge.objects.filter(invoiced=False).aggregate(s=Sum('amount'))['s'] or Decimal('0')
        )
        context['yard_month_total'] = (
            YardCost.objects.filter(date__gte=month_start).aggregate(s=Sum('amount'))['s'] or Decimal('0')
        )

        context['period_label'] = period_label
        context['current_period'] = self.request.GET.get('period', 'month')

        # Merged category choices for filter dropdown
        all_categories = []
        for val, label in ExtraCharge.ChargeType.choices:
            all_categories.append((val, label))
        for val, label in YardCost.CostCategory.choices:
            if val not in dict(ExtraCharge.ChargeType.choices):
                all_categories.append((val, label))
        all_categories.sort(key=lambda x: x[1])
        context['all_categories'] = all_categories

        return context


# ── YardCost CRUD ────────────────────────────────────────────────

class YardCostCreateView(StaffRequiredMixin, CreateView):
    model = YardCost
    form_class = YardCostForm
    template_name = 'billing/yard_cost_form.html'
    success_url = reverse_lazy('costs_list')

    def get_initial(self):
        initial = super().get_initial()
        initial['date'] = timezone.now().date()
        return initial

    def form_valid(self, form):
        messages.success(self.request, "Yard cost added.")
        return super().form_valid(form)


class YardCostUpdateView(StaffRequiredMixin, UpdateView):
    model = YardCost
    form_class = YardCostForm
    template_name = 'billing/yard_cost_form.html'
    success_url = reverse_lazy('costs_list')

    def form_valid(self, form):
        messages.success(self.request, "Yard cost updated.")
        return super().form_valid(form)


class YardCostDeleteView(StaffRequiredMixin, DeleteView):
    model = YardCost
    template_name = 'billing/yard_cost_confirm_delete.html'
    success_url = reverse_lazy('costs_list')

    def form_valid(self, form):
        messages.success(self.request, "Yard cost deleted.")
        return super().form_valid(form)


@staff_required
def yard_cost_duplicate(request, pk):
    """Duplicate a yard cost with today's date."""
    original = get_object_or_404(YardCost, pk=pk)
    YardCost.objects.create(
        category=original.category,
        date=timezone.now().date(),
        supplier=original.supplier,
        description=original.description,
        amount=original.amount,
        vat_amount=original.vat_amount,
        is_recurring=original.is_recurring,
        recurrence_interval=original.recurrence_interval,
        notes=original.notes,
    )
    messages.success(request, f"Duplicated '{original.description}' with today's date.")
    return redirect('costs_list')


@login_required
def supplier_autocomplete(request):
    """Return distinct supplier names for autocomplete."""
    q = request.GET.get('q', '')
    if len(q) < 2:
        return JsonResponse([], safe=False)
    suppliers = (
        YardCost.objects
        .filter(supplier__icontains=q)
        .values_list('supplier', flat=True)
        .distinct()[:10]
    )
    return JsonResponse(list(suppliers), safe=False)


# ── Feed Out ─────────────────────────────────────────────────────

@staff_required
def feed_out_create(request, location_pk):
    """Record feed delivered to a location, optionally recharging to horse owners."""
    from django.db import transaction

    from core.models import Location

    location = get_object_or_404(Location, pk=location_pk)
    horses_at_location = Horse.objects.filter(
        placements__location=location,
        placements__end_date__isnull=True,
    ).distinct().select_related()

    # Build list with owner info for the template
    horses_with_owners = []
    for h in horses_at_location:
        owner = h.current_owner
        horses_with_owners.append({
            'horse': h,
            'owner_name': owner.name if owner else 'No owner',
            'has_owner': owner is not None,
        })

    if request.method == 'POST':
        form = FeedOutForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                feed_out = form.save(commit=False)
                feed_out.location = location
                feed_out.save()

                # Create YardCost for internal tracking
                cat = 'hay' if feed_out.feed_type in ('hay', 'haylage') else 'feed'
                yard_cost = YardCost.objects.create(
                    category=cat,
                    date=feed_out.date,
                    description=f"Feed out: {feed_out.get_feed_type_display()} to {location.name}"
                                + (f" ({feed_out.quantity})" if feed_out.quantity else ""),
                    amount=feed_out.total_cost,
                )
                feed_out.yard_cost = yard_cost
                feed_out.save(update_fields=['yard_cost'])

                # Recharge to horse owners
                if feed_out.is_recharged:
                    selected_ids = request.POST.getlist('recharge_horses')
                    selected = horses_at_location.filter(pk__in=selected_ids)
                    count = selected.count()
                    if count > 0:
                        per_horse = (feed_out.total_cost / Decimal(count)).quantize(Decimal('0.01'))
                        remainder = feed_out.total_cost - (per_horse * count)
                        for i, horse in enumerate(selected):
                            owner = horse.current_owner
                            if not owner:
                                continue
                            amount = per_horse + remainder if i == 0 else per_horse
                            ExtraCharge.objects.create(
                                horse=horse,
                                owner=owner,
                                charge_type='feed',
                                date=feed_out.date,
                                description=f"{feed_out.get_feed_type_display()} - {location.name}"
                                            + (f" ({feed_out.quantity})" if feed_out.quantity else ""),
                                amount=amount,
                                split_by_ownership=True,
                                feed_out=feed_out,
                            )

            messages.success(request, f"Feed out recorded for {location.name}.")
            return redirect('location_detail', pk=location.pk)
    else:
        form = FeedOutForm(initial={'date': timezone.now().date()})

    return render(request, 'billing/feed_out_form.html', {
        'form': form,
        'location': location,
        'horses_with_owners': horses_with_owners,
    })
