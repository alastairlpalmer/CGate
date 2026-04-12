"""
Django admin configuration for core models.
"""

from django.contrib import admin
from django.db.models import Count, Prefetch, Q
from django.utils.html import format_html

from invoicing.models import Invoice, InvoiceLineItem

from .models import (
    BusinessSettings,
    Horse,
    HorseOwnership,
    Location,
    Owner,
    OwnershipShare,
    Placement,
    RateType,
)


@admin.register(Owner)
class OwnerAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'phone', 'account_code', 'active_horse_count_display', 'created_at']
    search_fields = ['name', 'email', 'phone', 'account_code']
    list_filter = ['created_at']
    readonly_fields = ['created_at', 'updated_at']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _active_horse_count=Count(
                'placements__horse',
                filter=Q(placements__end_date__isnull=True),
                distinct=True,
            )
        )

    def active_horse_count_display(self, obj):
        return obj._active_horse_count
    active_horse_count_display.short_description = 'Active Horses'


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ['name', 'site', 'capacity', 'current_horse_count_display', 'availability_display']
    list_filter = ['site']
    search_fields = ['name', 'site']
    readonly_fields = ['created_at', 'updated_at']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _current_horse_count=Count(
                'placements__horse',
                filter=Q(placements__end_date__isnull=True),
                distinct=True,
            )
        )

    def current_horse_count_display(self, obj):
        return obj._current_horse_count
    current_horse_count_display.short_description = 'Current Horses'

    def availability_display(self, obj):
        if obj.capacity is not None:
            return obj.capacity - obj._current_horse_count
        return None
    availability_display.short_description = 'Available'


class PlacementInline(admin.TabularInline):
    model = Placement
    extra = 0
    readonly_fields = ['created_at']


class HorseOwnershipInline(admin.TabularInline):
    model = HorseOwnership
    extra = 0
    readonly_fields = ['created_at']
    fields = ['owner', 'share_percentage', 'effective_from', 'effective_to', 'is_billing_contact', 'notes']


class OwnershipShareInline(admin.TabularInline):
    model = OwnershipShare
    extra = 1
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Horse)
class HorseAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'date_of_birth', 'age', 'sex', 'color', 'current_owner_display',
        'current_location_display', 'is_active'
    ]
    list_filter = ['sex', 'color', 'is_active', 'has_passport']
    search_fields = ['name', 'passport_number', 'notes', 'sire_name']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['dam']
    inlines = [OwnershipShareInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related(
            Prefetch(
                'placements',
                queryset=Placement.objects.filter(
                    end_date__isnull=True
                ).select_related('owner', 'location'),
                to_attr='_active_placements',
            )
        )

    def current_owner_display(self, obj):
        owners = obj.current_owners
        if not owners:
            return '-'
        if len(owners) == 1:
            return owners[0][0].name
        # Multiple owners - show names with percentages
        return ', '.join([f"{o.name} ({pct}%)" for o, pct in owners])
    current_owner_display.short_description = 'Owner(s)'

    def current_location_display(self, obj):
        placements = obj._active_placements
        return placements[0].location.name if placements else '-'
    current_location_display.short_description = 'Location'


@admin.register(RateType)
class RateTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'daily_rate', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name']


@admin.register(OwnershipShare)
class OwnershipShareAdmin(admin.ModelAdmin):
    list_display = ['horse', 'owner', 'share_percentage', 'is_primary_contact', 'created_at']
    list_filter = ['is_primary_contact']
    search_fields = ['horse__name', 'owner__name']
    raw_id_fields = ['horse', 'owner']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Placement)
class PlacementAdmin(admin.ModelAdmin):
    list_display = [
        'horse', 'owner', 'location', 'rate_type',
        'start_date', 'end_date', 'is_current'
    ]
    list_filter = ['location', 'owner', 'rate_type', 'start_date']
    search_fields = ['horse__name', 'owner__name', 'location__name']
    date_hierarchy = 'start_date'
    raw_id_fields = ['horse', 'owner']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(BusinessSettings)
class BusinessSettingsAdmin(admin.ModelAdmin):
    list_display = ['business_name', 'email', 'phone', 'website', 'vat_registration', 'default_payment_terms']

    def has_add_permission(self, request):
        # Only allow one instance
        return not BusinessSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class InvoiceLineItemInline(admin.TabularInline):
    model = InvoiceLineItem
    extra = 0
    readonly_fields = ['line_total']


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = [
        'invoice_number', 'owner', 'period_start', 'period_end',
        'total', 'status', 'due_date', 'is_overdue_display'
    ]
    list_filter = ['status', 'created_at', 'due_date']
    search_fields = ['invoice_number', 'owner__name']
    date_hierarchy = 'created_at'
    raw_id_fields = ['owner']
    readonly_fields = ['created_at', 'sent_at', 'paid_at']
    inlines = [InvoiceLineItemInline]

    def is_overdue_display(self, obj):
        if obj.is_overdue:
            return format_html('<span style="color: red;">Overdue</span>')
        return '-'
    is_overdue_display.short_description = 'Overdue'


@admin.register(InvoiceLineItem)
class InvoiceLineItemAdmin(admin.ModelAdmin):
    list_display = [
        'invoice', 'horse', 'line_type', 'description',
        'quantity', 'unit_price', 'line_total'
    ]
    list_filter = ['line_type']
    search_fields = ['description', 'horse__name', 'invoice__invoice_number']
    raw_id_fields = ['invoice', 'horse', 'placement', 'charge']


@admin.register(HorseOwnership)
class HorseOwnershipAdmin(admin.ModelAdmin):
    list_display = [
        'horse', 'owner', 'share_percentage', 'effective_from',
        'effective_to', 'is_current', 'is_billing_contact'
    ]
    list_filter = ['is_billing_contact', 'effective_from', 'owner']
    search_fields = ['horse__name', 'owner__name']
    date_hierarchy = 'effective_from'
    raw_id_fields = ['horse', 'owner']
    readonly_fields = ['created_at', 'updated_at']

    def is_current(self, obj):
        return obj.is_current
    is_current.boolean = True
    is_current.short_description = 'Current'
