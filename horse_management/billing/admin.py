"""
Django admin configuration for billing models.
"""

from django.contrib import admin
from django.utils.html import format_html

from .models import ExtraCharge, ServiceProvider, YardCost


@admin.register(ServiceProvider)
class ServiceProviderAdmin(admin.ModelAdmin):
    list_display = ['name', 'provider_type', 'phone', 'email', 'is_active']
    list_filter = ['provider_type', 'is_active']
    search_fields = ['name', 'email', 'phone']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ExtraCharge)
class ExtraChargeAdmin(admin.ModelAdmin):
    list_display = [
        'horse', 'owner', 'charge_type', 'date',
        'description', 'amount', 'invoiced_display'
    ]
    list_filter = ['charge_type', 'invoiced', 'date']
    search_fields = ['horse__name', 'owner__name', 'description']
    date_hierarchy = 'date'
    raw_id_fields = ['horse', 'owner', 'invoice']
    readonly_fields = ['created_at', 'updated_at']

    def invoiced_display(self, obj):
        if obj.invoiced:
            return format_html(
                '<span style="color: green;">Yes - {}</span>',
                obj.invoice.invoice_number if obj.invoice else 'Unknown'
            )
        return format_html('<span style="color: orange;">No</span>')
    invoiced_display.short_description = 'Invoiced'


@admin.register(YardCost)
class YardCostAdmin(admin.ModelAdmin):
    list_display = ['category', 'date', 'supplier', 'description', 'amount', 'is_recurring']
    list_filter = ['category', 'is_recurring', 'date']
    search_fields = ['supplier', 'description']
    date_hierarchy = 'date'
    readonly_fields = ['created_at', 'updated_at']
