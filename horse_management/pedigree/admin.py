from django.contrib import admin

from .models import PedigreeHorse


@admin.register(PedigreeHorse)
class PedigreeHorseAdmin(admin.ModelAdmin):
    list_display = ['name', 'country', 'year_of_birth', 'sex', 'sire', 'dam', 'horse', 'source', 'checked']
    list_filter = ['sex', 'source', 'checked', 'country']
    search_fields = ['name', 'name_key']
    raw_id_fields = ['sire', 'dam', 'horse', 'checked_by']
    readonly_fields = ['name_key', 'created_at', 'updated_at']
