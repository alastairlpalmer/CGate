"""
Views for billing app.
"""

from datetime import date
from decimal import ROUND_DOWN, Decimal
from itertools import chain
from operator import attrgetter

from django.contrib import messages

from core.permissions import (
    LEVEL_VIEW, FeatureAccessMixin, deny, feature_required, has_feature_access,
)
from core.views._popup import PopupFormMixin, is_popup_request, popup_saved_response
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from core.models import Horse, Owner
from core.views._next import safe_next
from core.views._undo import undo_tag

from .forms import (
    ExtraChargeForm, FeedOutForm, FeedStockForm, MultiHorseChargeForm, ServiceProviderForm, YardCostForm,
)
from .models import ExtraCharge, FeedOut, FeedStock, ServiceProvider, YardCost
from core.templatetags.ui_extras import gbp


class ExtraChargeListView(FeatureAccessMixin, ListView):
    feature = 'charges'
    access_level = LEVEL_VIEW
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
        if horse and horse.isdigit():
            queryset = queryset.filter(horse_id=horse)

        # Filter by owner
        owner = self.request.GET.get('owner')
        if owner and owner.isdigit():
            queryset = queryset.filter(owner_id=owner)

        search = self.request.GET.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(description__icontains=search)
                | Q(horse__name__icontains=search)
                | Q(owner__name__icontains=search)
                | Q(service_provider__name__icontains=search)
            )

        return queryset.order_by('-date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['horses'] = Horse.objects.only('pk', 'name', 'is_active').order_by('-is_active', 'name')
        context['owners'] = Owner.objects.only('pk', 'name')
        context['charge_types'] = ExtraCharge.ChargeType.choices
        return context


class ExtraChargeCreateView(PopupFormMixin, FeatureAccessMixin, CreateView):
    feature = 'charges'
    model = ExtraCharge
    form_class = ExtraChargeForm
    template_name = 'billing/charge_form.html'
    success_url = reverse_lazy('charge_list')

    def get_form_class(self):
        # The pop-up sheet (a horse's Quick Action) is about one horse; the
        # full page lets the user tick several and adds a charge for each.
        return ExtraChargeForm if self.in_popup else MultiHorseChargeForm

    def get_initial(self):
        initial = super().get_initial()
        initial['date'] = timezone.localdate()
        horse_id = self.request.GET.get('horse', '')
        if not horse_id.isdigit():
            return initial
        if not self.in_popup:
            # Pre-tick it. Bill To stays on "Each horse's current owner",
            # which bills this horse's owner anyway.
            initial['horses'] = [horse_id]
            return initial
        initial['horse'] = horse_id
        horse = Horse.objects.filter(pk=horse_id).first()
        if horse and horse.current_owner:
            initial['owner'] = horse.current_owner.pk
        return initial

    def form_valid(self, form):
        if not isinstance(form, MultiHorseChargeForm):
            messages.success(self.request, "Charge added successfully.")
            _warn_if_owner_unrelated(self.request, form)
            return super().form_valid(form)
        charges = form.save_charges()
        self.object = charges[0]
        if len(charges) == 1:
            messages.success(self.request, "Charge added successfully.")
        else:
            messages.success(
                self.request,
                f"{len(charges)} charges added, one for each horse "
                f"({gbp(form.cleaned_data['amount'])} each).",
            )
        if form.cleaned_data.get('owner'):
            for charge in charges:
                _warn_if_owner_unrelated(self.request, horse=charge.horse, owner=charge.owner)
        return HttpResponseRedirect(self.get_success_url())


@feature_required('charges', LEVEL_VIEW)
def horse_owner(request):
    """Return the current owner for a horse (used to prefill Bill To)."""
    horse_id = request.GET.get('horse', '')
    owner_id = None
    if horse_id.isdigit():
        horse = Horse.objects.filter(pk=horse_id).first()
        if horse and horse.current_owner:
            owner_id = horse.current_owner.pk
    return JsonResponse({'owner_id': owner_id})


def _warn_if_owner_unrelated(request, form=None, horse=None, owner=None):
    """Warn (without blocking) when a charge bills someone who neither owns
    nor part-owns the horse — usually a mis-picked dropdown."""
    if form is not None:
        horse = form.cleaned_data.get('horse')
        owner = form.cleaned_data.get('owner')
    if not horse or not owner:
        return
    related_ids = set(
        horse.ownership_shares.values_list('owner_id', flat=True)
    )
    if horse.current_owner:
        related_ids.add(horse.current_owner.pk)
    if owner.pk not in related_ids:
        messages.warning(
            request,
            f"Heads up: {owner.name} is not recorded as an owner of "
            f"{horse.name} — double-check the Bill To selection."
        )


def _on_live_invoice(charge):
    """True when the charge is billed on any non-cancelled invoice.

    Thin wrapper over ``ExtraCharge.on_live_invoice``, which is the single
    definition — the health record → charge sync asks the same question.
    """
    return charge.on_live_invoice


class ExtraChargeUpdateView(PopupFormMixin, FeatureAccessMixin, UpdateView):
    feature = 'charges'
    model = ExtraCharge
    form_class = ExtraChargeForm
    template_name = 'billing/charge_form.html'
    success_url = reverse_lazy('charge_list')

    def dispatch(self, request, *args, **kwargs):
        # Access check first: the old order ran get_object() and answered
        # "already invoiced" before FeatureAccessMixin got to deny, leaking
        # the charge's existence and billing state to anyone.
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not has_feature_access(request.user, self.feature, self.access_level):
            return deny(request)
        obj = self.get_object()
        if _on_live_invoice(obj):
            messages.error(request, "This charge has already been invoiced and cannot be edited.")
            return redirect('charge_list')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        messages.success(self.request, "Charge updated successfully.")
        _warn_if_owner_unrelated(self.request, form)
        response = super().form_valid(form)
        # A charge made from a health record is rebuilt from record.cost
        # each time the record is saved. Keep the record's cost in step, or
        # a discount given here is undone by the next edit to the record.
        record = self.object.linked_health_record()
        if record is not None and record.cost != self.object.amount:
            record.cost = self.object.amount
            record.save(update_fields=['cost'])
        return response


class ExtraChargeDeleteView(FeatureAccessMixin, DeleteView):
    """Soft delete: the charge moves to Recently Deleted and can be restored.

    A charge made from a health record stays linked to it while deleted,
    and sync_record_charge leaves a deleted charge alone, so editing the
    record does not bring the charge back (core/softdelete.py).
    """

    feature = 'charges'
    model = ExtraCharge
    template_name = 'billing/charge_confirm_delete.html'
    success_url = reverse_lazy('charge_list')

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not has_feature_access(request.user, self.feature, self.access_level):
            return deny(request)
        obj = self.get_object()
        if _on_live_invoice(obj):
            messages.error(request, "This charge has already been invoiced and cannot be deleted.")
            return redirect('charge_list')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        charge = self.object
        charge.soft_delete(self.request.user)
        messages.success(
            self.request, "Charge deleted.",
            extra_tags=undo_tag(reverse('charge_restore', args=[charge.pk])),
        )
        return redirect(self.success_url)


@feature_required('charges')
@require_POST
def charge_restore(request, pk):
    charge = get_object_or_404(ExtraCharge.all_objects.deleted(), pk=pk)
    # A charge made from a health record follows the record. While the
    # charge was deleted, edits to the record's cost were not passed on, so
    # the restored charge could bill an old amount: bring it in line now.
    record = charge.linked_health_record()
    if record is not None and (not record.cost or record.cost <= 0):
        messages.warning(
            request,
            "Not restored: the health record this charge was for now has no "
            "cost. Enter the cost on the record to bill it again.",
        )
        return redirect(safe_next(request, reverse('charge_list')))
    charge.restore()
    if record is not None:
        from health.views import sync_record_charge

        old_amount = charge.amount
        record.refresh_from_db(fields=['extra_charge'])
        sync_record_charge(record)
        charge.refresh_from_db()
        if charge.amount != old_amount:
            messages.info(
                request,
                f"The amount was updated from {gbp(old_amount)} to {gbp(charge.amount)} "
                "to match the cost on the health record.",
            )
    messages.success(request, f"Charge restored: {charge.description or charge.get_charge_type_display()}, {gbp(charge.amount)}.")
    return redirect(safe_next(request, reverse('charge_list')))


class ServiceProviderListView(FeatureAccessMixin, ListView):
    feature = 'settings'
    model = ServiceProvider
    template_name = 'billing/provider_list.html'
    context_object_name = 'providers'

    def get_queryset(self):
        queryset = ServiceProvider.objects.select_related('user')

        provider_type = self.request.GET.get('type')
        if provider_type:
            queryset = queryset.filter(provider_type=provider_type)

        return queryset.order_by('provider_type', 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['provider_types'] = ServiceProvider.ProviderType.choices
        return context


class ServiceProviderCreateView(FeatureAccessMixin, CreateView):
    feature = 'settings'
    model = ServiceProvider
    form_class = ServiceProviderForm
    template_name = 'billing/provider_form.html'
    success_url = reverse_lazy('provider_list')


class ServiceProviderUpdateView(FeatureAccessMixin, UpdateView):
    feature = 'settings'
    model = ServiceProvider
    form_class = ServiceProviderForm
    template_name = 'billing/provider_form.html'
    success_url = reverse_lazy('provider_list')


# ── Unified Costs View ──────────────────────────────────────────

class CostsListView(FeatureAccessMixin, ListView):
    feature = 'costs'
    access_level = LEVEL_VIEW
    template_name = 'billing/costs_list.html'
    context_object_name = 'costs'
    paginate_by = 50

    def _date_range(self):
        """Return (start_date, end_date, period_label) based on GET params."""
        today = timezone.localdate()
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
        today = timezone.localdate()
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

        # Summary totals — batch into 2 queries instead of 7
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)
        # A recharged feed-out's charges pass on a cost already booked as a
        # YardCost (the delivery or the feed-out itself): counting both
        # showed £1,000 for £500 of hay.
        # Up to today, as the list is: a cost entered early for next month
        # (or next January) counted in "This month" and "This year".
        to_date = Q(date__lte=today)
        charge_agg = ExtraCharge.objects.filter(feed_out__isnull=True).aggregate(
            month=Sum('amount', filter=Q(date__gte=month_start) & to_date),
            year=Sum('amount', filter=Q(date__gte=year_start) & to_date),
        )
        yard_agg = YardCost.objects.aggregate(
            month=Sum('amount', filter=Q(date__gte=month_start) & to_date),
            year=Sum('amount', filter=Q(date__gte=year_start) & to_date),
        )
        context['month_total'] = (charge_agg['month'] or Decimal('0')) + (yard_agg['month'] or Decimal('0'))
        context['year_total'] = (charge_agg['year'] or Decimal('0')) + (yard_agg['year'] or Decimal('0'))
        # unbilled_total() subtracts the portions of split charges already on
        # live invoices — a plain Sum over invoiced=False double-counts them
        # (QA #6, previously fixed on the dashboard and finances pages).
        context['unbilled_total'] = ExtraCharge.unbilled_total()
        context['yard_month_total'] = yard_agg['month'] or Decimal('0')

        context['period_label'] = period_label
        context['current_period'] = self.request.GET.get('period', 'month')
        # Drives the period rail in the template, so the options live in one
        # place rather than three near-identical links.
        context['period_options'] = (
            ('month', 'Month'), ('quarter', 'Quarter'), ('year', 'Year'),
        )

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

class YardCostCreateView(FeatureAccessMixin, CreateView):
    feature = 'costs'
    model = YardCost
    form_class = YardCostForm
    template_name = 'billing/yard_cost_form.html'
    success_url = reverse_lazy('costs_list')

    def get_initial(self):
        initial = super().get_initial()
        initial['date'] = timezone.localdate()
        return initial

    def form_valid(self, form):
        messages.success(self.request, "Yard cost added.")
        return super().form_valid(form)


class YardCostUpdateView(FeatureAccessMixin, UpdateView):
    feature = 'costs'
    model = YardCost
    form_class = YardCostForm
    template_name = 'billing/yard_cost_form.html'
    success_url = reverse_lazy('costs_list')

    def form_valid(self, form):
        messages.success(self.request, "Yard cost updated.")
        return super().form_valid(form)


class YardCostDeleteView(FeatureAccessMixin, DeleteView):
    feature = 'costs'
    model = YardCost
    template_name = 'billing/yard_cost_confirm_delete.html'
    success_url = reverse_lazy('costs_list')

    def form_valid(self, form):
        cost = self.object
        cost.soft_delete(self.request.user)
        messages.success(
            self.request, "Yard cost deleted.",
            extra_tags=undo_tag(reverse('yard_cost_restore', args=[cost.pk])),
        )
        return redirect(self.success_url)


@feature_required('costs')
@require_POST
def yard_cost_restore(request, pk):
    cost = get_object_or_404(YardCost.all_objects.deleted(), pk=pk)
    cost.restore()
    messages.success(request, f"Yard cost restored: {cost.description}, {gbp(cost.amount)}.")
    return redirect(safe_next(request, reverse('costs_list')))


@feature_required('costs')
@require_POST
def yard_cost_duplicate(request, pk):
    """Duplicate a yard cost with today's date."""
    original = get_object_or_404(YardCost, pk=pk)
    YardCost.objects.create(
        category=original.category,
        date=timezone.localdate(),
        supplier=original.supplier,
        description=original.description,
        amount=original.amount,
        vat_amount=original.vat_amount,
        is_recurring=original.is_recurring,
        recurrence_interval=original.recurrence_interval,
        recurrence_end_date=original.recurrence_end_date,
        notes=original.notes,
    )
    messages.success(request, f"Duplicated '{original.description}' with today's date.")
    return redirect('costs_list')


@feature_required('feed', LEVEL_VIEW)
def feed_dashboard(request):
    """Standalone feed store dashboard."""
    from core.models import Location
    from .models import FeedType, FeedUnit

    feed_filter = request.GET.get('feed_type', '')
    site_filter = request.GET.get('site', '')

    stock_qs = FeedStock.objects.all()
    out_qs = FeedOut.objects.filter(quantity_numeric__isnull=False, unit__gt='')

    if feed_filter:
        stock_qs = stock_qs.filter(feed_type=feed_filter)
        out_qs = out_qs.filter(feed_type=feed_filter)
    if site_filter:
        stock_qs = stock_qs.filter(site=site_filter)
        out_qs = out_qs.filter(location__site=site_filter)

    # Stock rows: positive in, negative (waste, a downward adjustment) out.
    # Summed together, a waste-only line read "+-3 in".
    stock_in = stock_qs.values('site', 'feed_type', 'unit').annotate(
        added=Sum('quantity', filter=Q(quantity__gt=0)),
        removed=Sum('quantity', filter=Q(quantity__lt=0)),
    )
    stock_out = out_qs.values('location__site', 'feed_type', 'unit').annotate(total=Sum('quantity_numeric'))

    balances = {}
    for s in stock_in:
        key = (s['site'] or 'Unassigned', s['feed_type'], s['unit'])
        balances[key] = {'in': float(s['added'] or 0), 'out': -float(s['removed'] or 0)}
    for s in stock_out:
        key = (s['location__site'] or 'Unassigned', s['feed_type'], s['unit'])
        if key not in balances:
            balances[key] = {'in': 0, 'out': 0}
        balances[key]['out'] += float(s['total'] or 0)

    stock_summary = []
    for (site, ft, unit), vals in sorted(balances.items()):
        balance = vals['in'] - vals['out']
        stock_summary.append({
            'site': site,
            'feed_type': ft,
            'feed_type_display': dict(FeedType.choices).get(ft, ft),
            'unit': unit,
            'unit_display': dict(FeedUnit.choices).get(unit, unit),
            'total_in': vals['in'],
            'total_out': vals['out'],
            'balance': balance,
        })

    ledger = []
    for s in stock_qs.order_by('-date')[:30]:
        ledger.append({
            # Waste and downward adjustments are stored negative: show them
            # as stock going out ("-3"), not "+-3".
            'date': s.date, 'direction': 'in' if s.quantity >= 0 else 'out', 'site': s.site or '',
            'feed_type': s.get_feed_type_display(), 'quantity': abs(s.quantity),
            'unit': s.get_unit_display(), 'entry_type': s.get_entry_type_display(),
            'detail': s.supplier or s.notes or s.get_entry_type_display(),
            'cost': s.cost if s.cost else None,
        })
    for fo in out_qs.select_related('location').order_by('-date')[:30]:
        ledger.append({
            'date': fo.date, 'direction': 'out', 'site': fo.location.site,
            'feed_type': fo.get_feed_type_display(), 'quantity': fo.quantity_numeric,
            'unit': fo.get_unit_display(), 'entry_type': 'Feed Out',
            'detail': fo.location.name,
            'cost': fo.total_cost if fo.total_cost else None,
        })
    ledger.sort(key=lambda x: x['date'], reverse=True)
    ledger = ledger[:40]

    all_sites = list(Location.objects.values_list('site', flat=True).distinct().order_by('site'))

    return render(request, 'billing/feed_dashboard.html', {
        'stock_summary': stock_summary,
        'ledger': ledger,
        'feed_types': FeedType.choices,
        'all_sites': all_sites,
        'current_feed_filter': feed_filter,
        'current_site_filter': site_filter,
    })


class FeedStockCreateView(FeatureAccessMixin, CreateView):
    feature = 'feed'
    model = FeedStock
    form_class = FeedStockForm
    template_name = 'billing/feed_stock_form.html'
    success_url = reverse_lazy('feed_dashboard')

    def get_initial(self):
        initial = super().get_initial()
        initial['date'] = timezone.localdate()
        return initial

    def get_success_url(self):
        return reverse_lazy('feed_dashboard')

    def form_valid(self, form):
        response = super().form_valid(form)
        stock = self.object
        # A yard cost only for a delivery: waste or an adjustment is not a
        # new purchase.
        if stock.entry_type == FeedStock.EntryType.DELIVERY and stock.cost and stock.cost > 0:
            cat = 'hay' if stock.feed_type in ('hay', 'haylage') else 'feed'
            YardCost.objects.create(
                category=cat,
                date=stock.date,
                supplier=stock.supplier,
                description=f"Feed delivery: {stock.quantity} {stock.get_unit_display()} {stock.get_feed_type_display()}",
                amount=stock.cost,
            )
        messages.success(self.request, f"Logged {stock.quantity} {stock.get_unit_display()} of {stock.get_feed_type_display()}.")
        return response


@feature_required('feed')
def feed_stock_adjust(request):
    """Manually adjust feed stock (reduce/increase without a feed-out)."""
    from .forms import FeedStockForm
    from .models import FeedType, FeedUnit

    if request.method == 'POST':
        form = FeedStockForm(request.POST)
        if form.is_valid():
            # FeedStockForm.clean makes waste negative.
            stock = form.save()
            messages.success(request, f"Stock adjusted: {stock.quantity:+g} {stock.get_unit_display()} {stock.get_feed_type_display()} at {stock.site}.")
            return redirect('feed_dashboard')
    else:
        form = FeedStockForm(initial={
            'date': timezone.localdate(),
            'entry_type': 'adjustment',
        })

    return render(request, 'billing/feed_stock_adjust.html', {'form': form})


@feature_required('feed')
def feed_stock_clear(request):
    """Clear all feed stock records to start fresh."""
    if request.method == 'POST':
        count = FeedStock.objects.count()
        FeedStock.objects.all().delete()
        messages.success(request, f"Cleared {count} stock records. You can now start tracking from scratch.")
        return redirect('feed_dashboard')
    return render(request, 'billing/feed_stock_clear.html', {
        'count': FeedStock.objects.count(),
    })


@feature_required('costs', LEVEL_VIEW)
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

@feature_required('feed')
def feed_out_create(request, location_pk):
    """Record feed delivered to a location, optionally recharging to horse owners."""
    from django.db import transaction

    from core.models import Location

    location = get_object_or_404(Location, pk=location_pk)
    in_popup = is_popup_request(request)
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

                # Auto-calculate cost from stock if not provided
                cost_from_stock = False
                if not feed_out.total_cost and feed_out.quantity_numeric and feed_out.unit:
                    # The price of this site's latest delivery; another
                    # site's only when this one has none. Any site's (and
                    # any entry type's) newest row priced a Main field's
                    # bale at the Other site's price.
                    priced = FeedStock.objects.filter(
                        feed_type=feed_out.feed_type,
                        unit=feed_out.unit,
                        entry_type=FeedStock.EntryType.DELIVERY,
                        cost__gt=0,
                        quantity__gt=0,
                    ).order_by('-date', '-pk')
                    latest_stock = (
                        priced.filter(site=location.site).first() or priced.first()
                    )
                    if latest_stock:
                        cost_per_unit = latest_stock.cost / latest_stock.quantity
                        feed_out.total_cost = (cost_per_unit * feed_out.quantity_numeric).quantize(Decimal('0.01'))
                        cost_from_stock = True
                    else:
                        feed_out.total_cost = Decimal('0.00')
                elif not feed_out.total_cost:
                    feed_out.total_cost = Decimal('0.00')

                feed_out.save()

                # Record a YardCost only for manually-entered costs. A cost
                # derived from purchased stock was already recorded as a
                # YardCost when the delivery was logged — creating another
                # here double-counted every bale in cost reporting (~£1,500
                # reported for £500 actually spent once fed out + recharged).
                if not cost_from_stock and feed_out.total_cost > 0:
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

                # Recharge to horse owners. Only horses with an owner share
                # the cost — an owner-less horse in the selection used to
                # silently absorb its slice (and sometimes the rounding
                # remainder) with the yard eating the difference.
                skipped_ownerless = []
                zero_cost_recharge = False
                if feed_out.is_recharged and feed_out.total_cost <= 0:
                    # No cost and no priced stock to derive one from: every
                    # selected horse used to get a permanent £0.00 charge
                    # that no invoice run could ever clear.
                    zero_cost_recharge = True
                    feed_out.is_recharged = False
                    feed_out.save(update_fields=['is_recharged'])
                if feed_out.is_recharged:
                    selected_ids = [
                        i for i in request.POST.getlist('recharge_horses')
                        if i.isdigit()
                    ]
                    selected = horses_at_location.filter(pk__in=selected_ids)
                    billable = []
                    for horse in selected:
                        owner = horse.current_owner
                        if owner:
                            billable.append((horse, owner))
                        else:
                            skipped_ownerless.append(horse.name)
                    count = len(billable)
                    if count > 0:
                        # Round each share down, then give the leftover
                        # pennies out one at a time. Rounding to the nearest
                        # penny could round up, and the negative remainder
                        # put on the first horse took it below £0 (a 500 on
                        # £0.04 over 7 horses).
                        per_horse = (feed_out.total_cost / Decimal(count)).quantize(
                            Decimal('0.01'), rounding=ROUND_DOWN,
                        )
                        leftover_pennies = int(
                            (feed_out.total_cost - per_horse * count) / Decimal('0.01')
                        )
                        for i, (horse, owner) in enumerate(billable):
                            amount = per_horse + (Decimal('0.01') if i < leftover_pennies else 0)
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
            if zero_cost_recharge:
                messages.warning(
                    request,
                    "Not recharged: this feed out has no cost (enter a total "
                    "cost, or log the stock purchase first so one can be "
                    "worked out).",
                )
            if skipped_ownerless:
                messages.warning(
                    request,
                    "Not recharged (no owner to bill): "
                    + ", ".join(skipped_ownerless)
                    + ". The full cost was spread across the owned horses.",
                )
            if in_popup:
                return popup_saved_response()
            return redirect('location_detail', pk=location.pk)
    else:
        form = FeedOutForm(initial={'date': timezone.localdate()})

    template = 'billing/partials/feed_out_form.html' if in_popup else 'billing/feed_out_form.html'
    return render(request, template, {
        'in_popup': in_popup,
        'form': form,
        'location': location,
        'horses_with_owners': horses_with_owners,
    })
