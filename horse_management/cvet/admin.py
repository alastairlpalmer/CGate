from django.contrib import admin

from .models import Bill, ClinicalNote, Practice, PracticeMember, PracticeYardLink, SyncReceipt, Visit

# ClinicalNote, ClinicalFile, Visit, the price list and the bills are not
# registered here, on
# purpose, and must never be. The Django admin is the yard
# administrator's tool, and the yard does not read a practice's clinical
# records (docs/C_VET_PLAN.md, section 3). cvet/tests_ownership.py fails
# if one is added.


def delete_test_notes(practice):
    """Delete a practice's test notes, with their files and history.

    Only notes made while CVET_TEST_MODE was on. The count is all that
    comes back: nothing here reads a note.
    """
    # Test bills first: a bill protects its visit.
    Bill.objects.filter(practice=practice, is_test=True).delete()
    notes = ClinicalNote.objects.filter(practice=practice, is_test=True)
    ids = list(notes.values_list('pk', flat=True))
    # One at a time, so each file's row sends the signal that removes
    # the stored file.
    for note in notes:
        note.delete()
    # simple_history keeps the text of a deleted row. Test text goes too.
    ClinicalNote.history.filter(id__in=ids).delete()
    Visit.objects.filter(practice=practice, is_test=True, notes__isnull=True).delete()
    return len(ids)


class PracticeMemberInline(admin.TabularInline):
    """A login added here waits for a member of the practice to approve it
    (cvet.access.approve): a superuser cannot let a login read the notes."""

    model = PracticeMember
    extra = 1
    fields = ('user', 'kind', 'is_active', 'approved_at')
    readonly_fields = ('approved_at',)


class PracticeYardLinkInline(admin.StackedInline):
    model = PracticeYardLink
    extra = 1
    max_num = 1
    fields = (
        'started_on', 'ended_on', 'bill_to', 'bill_when', 'service_provider',
        'sends_charges', 'sends_vaccination_dates', 'sends_follow_up_dates',
    )


@admin.register(Practice)
class PracticeAdmin(admin.ModelAdmin):
    """Set up a practice: its details, its logins and its link to the yard.

    Each login also needs the Vet role (Settings, Users). This screen
    shows who can read the practice's clinical notes; it does not show
    the notes.
    """

    list_display = ('name', 'is_active', 'member_count', 'linked_to_yard', 'test_note_count')
    inlines = [PracticeYardLinkInline, PracticeMemberInline]
    actions = ['delete_test_notes_action']

    @admin.display(description='Test notes')
    def test_note_count(self, obj):
        return ClinicalNote.objects.filter(practice=obj, is_test=True).count()

    @admin.action(description='Delete the test notes of the selected practices')
    def delete_test_notes_action(self, request, queryset):
        total = sum(delete_test_notes(practice) for practice in queryset)
        self.message_user(request, f'{total} test note(s) deleted, with their photos and recordings.')

    @admin.display(description='Logins')
    def member_count(self, obj):
        return obj.members.filter(is_active=True).count()

    @admin.display(description='Linked to the yard', boolean=True)
    def linked_to_yard(self, obj):
        link = getattr(obj, 'yard_link', None)
        return bool(link and link.is_open)


@admin.register(SyncReceipt)
class SyncReceiptAdmin(admin.ModelAdmin):
    """How each note arrived from the phone. Read and delete only.

    The measures of the phone test. There is no clinical text here: the
    receipt holds lengths, sizes, times and what the phone said about
    itself, and the link to the note is not shown.
    """

    list_display = (
        'received_at', 'practice', 'user', 'is_test', 'waited_text', 'attempts',
        'text_length', 'photo_bytes', 'photo_intact', 'audio_bytes', 'audio_intact',
        'installed', 'opened_in',
    )
    list_filter = ('practice', 'is_test', 'photo_intact', 'audio_intact')
    date_hierarchy = 'received_at'
    exclude = ('note',)
    readonly_fields = [f.name for f in SyncReceipt._meta.fields if f.name != 'note']

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

    @admin.display(description='Installed app', boolean=True)
    def installed(self, obj):
        return bool(obj.client_info.get('standalone'))

    @admin.display(description='Opened in')
    def opened_in(self, obj):
        value = obj.client_info.get('openMs')
        return f'{value} ms' if isinstance(value, int) else ''
