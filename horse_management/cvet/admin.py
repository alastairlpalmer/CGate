from django.contrib import admin

from .models import Practice, PracticeMember, PracticeYardLink, PrototypeEntry

# ClinicalNote is not registered here, on purpose, and must never be. The
# Django admin is the yard administrator's tool, and the yard does not
# read a practice's clinical notes (docs/C_VET_PLAN.md, section 3).
# cvet/tests_ownership.py fails if it is added.


class PracticeMemberInline(admin.TabularInline):
    model = PracticeMember
    extra = 1
    fields = ('user', 'kind', 'is_active')


class PracticeYardLinkInline(admin.StackedInline):
    model = PracticeYardLink
    extra = 1
    max_num = 1
    fields = ('started_on', 'ended_on', 'service_provider', 'sends_vaccination_dates', 'sends_follow_up_dates')


@admin.register(Practice)
class PracticeAdmin(admin.ModelAdmin):
    """Set up a practice: its details, its logins and its link to the yard.

    Each login also needs the Vet role (Settings, Users). This screen
    shows who can read the practice's clinical notes; it does not show
    the notes.
    """

    list_display = ('name', 'is_active', 'member_count', 'linked_to_yard')
    inlines = [PracticeYardLinkInline, PracticeMemberInline]

    @admin.display(description='Logins')
    def member_count(self, obj):
        return obj.members.filter(is_active=True).count()

    @admin.display(description='Linked to the yard', boolean=True)
    def linked_to_yard(self, obj):
        link = getattr(obj, 'yard_link', None)
        return bool(link and link.is_open)


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
