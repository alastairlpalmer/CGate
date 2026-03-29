"""
Django admin configuration for health models.
"""

from django.contrib import admin
from django.utils.html import format_html

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


@admin.register(VaccinationType)
class VaccinationTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'interval_months', 'reminder_days_before', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name']


@admin.register(Vaccination)
class VaccinationAdmin(admin.ModelAdmin):
    list_display = [
        'horse', 'vaccination_type', 'date_given', 'next_due_date',
        'vet', 'status_display', 'reminder_sent'
    ]
    list_filter = ['vaccination_type', 'reminder_sent', 'date_given']
    search_fields = ['horse__name', 'vet__name', 'batch_number']
    date_hierarchy = 'date_given'
    raw_id_fields = ['horse']
    readonly_fields = ['created_at', 'updated_at']

    def status_display(self, obj):
        if obj.is_overdue:
            return format_html('<span style="color: red;">Overdue</span>')
        elif obj.is_due_soon:
            return format_html('<span style="color: orange;">Due Soon</span>')
        return format_html('<span style="color: green;">OK</span>')
    status_display.short_description = 'Status'


@admin.register(FarrierVisit)
class FarrierVisitAdmin(admin.ModelAdmin):
    list_display = [
        'horse', 'date', 'work_done', 'service_provider',
        'next_due_date', 'cost', 'status_display'
    ]
    list_filter = ['work_done', 'date', 'service_provider']
    search_fields = ['horse__name', 'notes']
    date_hierarchy = 'date'
    raw_id_fields = ['horse', 'extra_charge']
    readonly_fields = ['created_at', 'updated_at']

    def status_display(self, obj):
        if obj.is_overdue:
            return format_html('<span style="color: red;">Overdue</span>')
        elif obj.is_due_soon:
            return format_html('<span style="color: orange;">Due Soon</span>')
        return format_html('<span style="color: green;">OK</span>')
    status_display.short_description = 'Status'


@admin.register(WormingTreatment)
class WormingTreatmentAdmin(admin.ModelAdmin):
    list_display = ['horse', 'date', 'product_name', 'active_ingredient', 'administered_by']
    list_filter = ['date', 'product_name']
    search_fields = ['horse__name', 'product_name', 'active_ingredient']
    date_hierarchy = 'date'
    raw_id_fields = ['horse']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(WormEggCount)
class WormEggCountAdmin(admin.ModelAdmin):
    list_display = ['horse', 'date', 'count', 'sample_type', 'lab_name', 'is_high_display']
    list_filter = ['sample_type', 'date']
    search_fields = ['horse__name', 'lab_name']
    date_hierarchy = 'date'
    raw_id_fields = ['horse']
    readonly_fields = ['created_at', 'updated_at']

    def is_high_display(self, obj):
        if obj.is_high:
            return format_html('<span style="color: red;">High (&gt;200)</span>')
        return format_html('<span style="color: green;">Normal</span>')
    is_high_display.short_description = 'Level'


@admin.register(MedicalCondition)
class MedicalConditionAdmin(admin.ModelAdmin):
    list_display = ['horse', 'name', 'status', 'diagnosed_date']
    list_filter = ['status']
    search_fields = ['horse__name', 'name']
    raw_id_fields = ['horse']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(VetVisit)
class VetVisitAdmin(admin.ModelAdmin):
    list_display = ['horse', 'date', 'reason', 'vet', 'cost', 'follow_up_date']
    list_filter = ['date', 'vet']
    search_fields = ['horse__name', 'reason', 'diagnosis']
    date_hierarchy = 'date'
    raw_id_fields = ['horse', 'extra_charge']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(BreedingRecord)
class BreedingRecordAdmin(admin.ModelAdmin):
    list_display = ['mare', 'stallion_name', 'date_covered', 'date_foal_due', 'status']
    list_filter = ['status', 'date_covered']
    search_fields = ['mare__name', 'stallion_name']
    date_hierarchy = 'date_covered'
    raw_id_fields = ['mare', 'foal']
    readonly_fields = ['created_at', 'updated_at']
