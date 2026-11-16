"""Models for C-vet (docs/C_VET_PLAN.md).

Phase 2, the ownership seam: ``Practice``, ``PracticeMember``,
``PracticeYardLink`` and ``ClinicalNote``. A clinical note belongs to a
practice, not to the yard. Read these models only through ``cvet.access``;
no code outside this app may import them (cvet/tests_ownership.py checks).

Phase 1, the offline prototype: ``PrototypeEntry``. Throwaway.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords


class Practice(models.Model):
    """A veterinary practice: the owner of the clinical records in C-vet.

    The practice is a separate organisation from the yard. The yard's
    staff, administrators included, are not members and see none of its
    clinical records. Today there is one practice and one yard in one
    database; the rule is the same when there are many.
    """

    name = models.CharField(max_length=200, unique=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    vat_number = models.CharField('VAT number', max_length=30, blank=True)
    is_active = models.BooleanField(
        default=True,
        help_text="Untick to close the practice's access. Its records are kept.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords(excluded_fields=['updated_at'])

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class PracticeMember(models.Model):
    """A login that works for a practice.

    One login belongs to one practice at most. The login also needs a
    role with Full on the ``cvet`` feature (the seeded Vet role); this
    row says whose records it then sees.
    """

    class Kind(models.TextChoices):
        VET = 'vet', 'Vet'
        OFFICE = 'office', 'Practice office'

    practice = models.ForeignKey(Practice, on_delete=models.CASCADE, related_name='members')
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='practice_membership',
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.VET)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Who could read a practice's clinical records, and when.
    history = HistoricalRecords()

    class Meta:
        ordering = ['practice', 'user']

    def __str__(self):
        return f'{self.user} at {self.practice}'


class PracticeYardLink(models.Model):
    """The agreement between a practice and this yard.

    While the link is open, the practice sees the yard's horse list
    (identity, owner, who pays, field name) and the yard receives the
    dates it needs from the practice. Close the link (``ended_on``) and
    both stop. The clinical notes never cross it in either state.

    One per practice for now: the yard is the whole database. When C-gate
    holds more than one yard, this gets a ``yard`` key and a practice has
    one link for each yard it visits.
    """

    practice = models.OneToOneField(Practice, on_delete=models.CASCADE, related_name='yard_link')
    # The practice as the yard already knows it: the entry in the yard's
    # own list of providers, which its vet visit log and charges use.
    service_provider = models.OneToOneField(
        'billing.ServiceProvider', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='practice_link', limit_choices_to={'provider_type': 'vet'},
        help_text="This practice in the yard's list of service providers, if it is there.",
    )
    started_on = models.DateField(default=timezone.localdate)
    ended_on = models.DateField(
        null=True, blank=True,
        help_text="Set a date to end the link. The practice then sees no horses of this yard.",
    )
    # What the practice sends back to the yard (phase 6 reads these).
    sends_vaccination_dates = models.BooleanField(
        default=True, help_text="The yard receives vaccination dates and next due dates.",
    )
    sends_follow_up_dates = models.BooleanField(
        default=True, help_text="The yard receives follow-up dates, with no reason.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    def __str__(self):
        return f'{self.practice} and the yard'

    @property
    def is_open(self):
        today = timezone.localdate()
        return self.started_on <= today and (self.ended_on is None or self.ended_on > today)


class ClinicalNoteQuerySet(models.QuerySet):
    def for_practice(self, practice):
        """The notes one practice may read. ``None`` gives no notes."""
        if practice is None:
            return self.none()
        return self.filter(practice=practice)


class ClinicalNote(models.Model):
    """What a vet found and did for one horse on one day.

    It belongs to the practice. The yard does not see it: no yard page,
    export, search or admin screen reads this table. The horse is linked
    with ``related_name='+'``, so a ``Horse`` has no way back to its
    notes, and the yard's code cannot reach them by accident.

    The horse's name and numbers are copied onto the note. The record
    must stay complete when the yard renames the horse, or deletes it.
    """

    practice = models.ForeignKey(Practice, on_delete=models.PROTECT, related_name='clinical_notes')
    horse = models.ForeignKey(
        'core.Horse', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    horse_name = models.CharField(max_length=200)
    horse_microchip = models.CharField(max_length=50, blank=True)
    horse_passport = models.CharField(max_length=100, blank=True)

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    author_name = models.CharField(max_length=200, blank=True)

    # Made on the phone when the note is saved (phase 3). A note that is
    # sent twice is stored once.
    client_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)

    seen_on = models.DateField(default=timezone.localdate)
    findings = models.TextField()
    treatment = models.TextField(blank=True)
    follow_up_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # A clinical record is corrected, never silently replaced: every
    # change keeps the text before it.
    history = HistoricalRecords(excluded_fields=['updated_at'])

    objects = ClinicalNoteQuerySet.as_manager()

    class Meta:
        ordering = ['-seen_on', '-created_at']
        indexes = [
            models.Index(fields=['practice', 'horse', '-seen_on'], name='cvet_note_practice_horse'),
        ]

    def __str__(self):
        # No clinical text here: this string reaches logs and error pages.
        return f'Clinical note {self.pk} ({self.seen_on})'


def _upload_path(kind):
    def path(instance, filename):
        # The name the phone sent is never used: only its extension, which
        # the view has already replaced with one from its own list.
        extension = filename.rsplit('.', 1)[-1].lower()
        return f'cvet_prototype/{kind}/{uuid.uuid4().hex}.{extension}'
    return path


def photo_path(instance, filename):
    return _upload_path('photos')(instance, filename)


def audio_path(instance, filename):
    return _upload_path('audio')(instance, filename)


class PrototypeEntry(models.Model):
    """One note saved on a phone during the offline test, as it arrived.

    This is test data, not a clinical record. Nothing reads it except the
    Django admin, where the test results are checked. Delete every row
    (and with it every file) when phase 1 ends.

    ``client_id`` is made on the phone when the note is saved. The phone
    sends it with every attempt, so a note that is sent twice (the reply
    was lost in a weak signal) is stored once.
    """

    client_id = models.UUIDField(unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+',
    )
    horse = models.ForeignKey(
        'core.Horse', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    # The name on the phone when the note was saved: the horse can be
    # renamed or deleted before the note arrives.
    horse_name = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)

    photo = models.FileField(upload_to=photo_path, blank=True)
    photo_bytes = models.PositiveIntegerField(default=0)
    # True when the file that arrived has the SHA-256 the phone worked out
    # before it sent it. None when there is no file or the phone sent no hash.
    photo_intact = models.BooleanField(null=True, blank=True)

    audio = models.FileField(upload_to=audio_path, blank=True)
    audio_bytes = models.PositiveIntegerField(default=0)
    audio_intact = models.BooleanField(null=True, blank=True)

    # The phone's clock when the note was saved, and the server's clock
    # when it arrived. The difference is how long the note waited.
    device_created_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    # How many times the phone tried to send this note, this one included.
    attempts = models.PositiveIntegerField(default=1)
    # What the phone reported about itself: browser, installed or not,
    # storage used. See static/cvet/proto.js, clientInfo().
    client_info = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-received_at']
        verbose_name = 'offline prototype entry'
        verbose_name_plural = 'offline prototype entries'

    def __str__(self):
        return f'{self.horse_name or "No horse"} - {self.received_at:%d %b %Y %H:%M}'

    @property
    def waited(self):
        """Time from Save on the phone to arrival on the server."""
        return self.received_at - self.device_created_at
