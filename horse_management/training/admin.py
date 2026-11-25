from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Activity, DayNote, HorseTraining, Lot, LotMember, TrainingNote, TrainingSettings, WorkType


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



class LotMemberInline(admin.TabularInline):
    model = LotMember
    raw_id_fields = ['horse']
    extra = 0


@admin.register(Lot)
class LotAdmin(admin.ModelAdmin):
    list_display = ['name', 'position', 'usual_time', 'usual_work_type', 'is_active']
    list_filter = ['is_active']
    inlines = [LotMemberInline]


@admin.register(HorseTraining)
class HorseTrainingAdmin(SimpleHistoryAdmin):
    list_display = ['horse', 'status', 'status_since']
    list_filter = ['status']
    raw_id_fields = ['horse']


@admin.register(TrainingSettings)
class TrainingSettingsAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'board_source']


@admin.register(TrainingNote)
class TrainingNoteAdmin(SimpleHistoryAdmin):
    list_display = ['date', 'horse', 'created_by']
    search_fields = ['horse__name', 'text']
    date_hierarchy = 'date'
    raw_id_fields = ['horse']


@admin.register(DayNote)
class DayNoteAdmin(admin.ModelAdmin):
    list_display = ['date', 'text', 'updated_by']
    date_hierarchy = 'date'
