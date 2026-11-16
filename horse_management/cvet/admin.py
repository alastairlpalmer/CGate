from django.contrib import admin

from .models import PrototypeEntry


@admin.register(PrototypeEntry)
class PrototypeEntryAdmin(admin.ModelAdmin):
    """The results of the offline test. Read and delete only."""

    list_display = (
        'received_at', 'user', 'horse_name', 'waited_text', 'attempts',
        'note_length', 'photo_bytes', 'photo_intact', 'audio_bytes', 'audio_intact',
        'installed',
    )
    list_filter = ('user', 'photo_intact', 'audio_intact')
    date_hierarchy = 'received_at'
    readonly_fields = [f.name for f in PrototypeEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description='Waited on phone')
    def waited_text(self, obj):
        seconds = int(obj.waited.total_seconds())
        if seconds < 0:
            return 'phone clock ahead'
        hours, rest = divmod(seconds, 3600)
        minutes, seconds = divmod(rest, 60)
        if hours:
            return f'{hours} h {minutes} min'
        if minutes:
            return f'{minutes} min {seconds} s'
        return f'{seconds} s'

    @admin.display(description='Note length')
    def note_length(self, obj):
        return len(obj.note)

    @admin.display(description='Installed app', boolean=True)
    def installed(self, obj):
        return bool(obj.client_info.get('standalone'))
