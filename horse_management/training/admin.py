from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Activity, WorkType


@admin.register(WorkType)
class WorkTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'kind', 'effort', 'default_minutes', 'position', 'is_active']
    list_filter = ['kind', 'is_active']


@admin.register(Activity)
class ActivityAdmin(SimpleHistoryAdmin):
    list_display = ['date', 'horse', 'work_type', 'status', 'reason', 'minutes', 'source']
    list_filter = ['status', 'source', 'work_type']
    search_fields = ['horse__name', 'notes']
    date_hierarchy = 'date'
    raw_id_fields = ['horse']
