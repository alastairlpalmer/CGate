from django.contrib import admin

from .models import ImportBatch, PedigreeHorse, PedigreeMerge


@admin.register(PedigreeHorse)
class PedigreeHorseAdmin(admin.ModelAdmin):
    list_display = ['name', 'country', 'year_of_birth', 'sex', 'sire', 'dam', 'horse', 'source', 'checked']
    list_filter = ['sex', 'source', 'checked', 'country']
    search_fields = ['name', 'name_key']
    raw_id_fields = ['sire', 'dam', 'horse', 'checked_by']
    readonly_fields = ['name_key', 'created_at', 'updated_at']


@admin.register(PedigreeMerge)
class PedigreeMergeAdmin(admin.ModelAdmin):
    """Read only: undo a merge from Fill in records, not here."""
    list_display = ['__str__', 'merged_by', 'merged_at', 'undone_at']
    readonly_fields = [f.name for f in PedigreeMerge._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    """Read only: review and apply from Fill in records, not here."""
    list_display = ['__str__', 'file_name', 'status', 'created_by', 'applied_at', 'summary']
    list_filter = ['source', 'status']
    readonly_fields = [f.name for f in ImportBatch._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
