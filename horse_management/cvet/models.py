"""Models for C-vet (docs/C_VET_PLAN.md).

The practice side: ``Practice``, ``PracticeMember``, ``PracticeYardLink``.
The clinical records: ``Visit``, ``ClinicalNote``, ``ClinicalFile``. They
belong to a practice, not to the yard. Read them only through
``cvet.access``; no code outside this app may import them
(cvet/tests_ownership.py checks).

``SyncReceipt`` is how each note arrived from the phone. It holds no
clinical text, so the yard's administrator may read it.
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


class PracticeScopedQuerySet(models.QuerySet):
    def for_practice(self, practice):
        """The rows one practice may read. ``None`` gives no rows."""
        if practice is None:
            return self.none()
        return self.filter(practice=practice)


class Visit(models.Model):
    """One day's work by one vet at the yard: the notes made that day.

    It belongs to the practice. It is not ``health.ProviderVisit``, which
    is the yard's record of a farrier's day and of what the yard owes the
    farrier. A vet bills in the practice's name, so this visit never
    enters the yard's books.

    The phone makes ``client_id`` when the first note of the day is
    saved, and sends it with every note of that visit.
    """

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        FINISHED = 'finished', 'Finished'

    practice = models.ForeignKey(Practice, on_delete=models.PROTECT, related_name='visits')
    client_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    author_name = models.CharField(max_length=200, blank=True)
    date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    finished_at = models.DateTimeField(null=True, blank=True)
    is_test = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PracticeScopedQuerySet.as_manager()

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'Visit {self.pk} ({self.date})'


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
    visit = models.ForeignKey(
        Visit, on_delete=models.SET_NULL, null=True, blank=True, related_name='notes',
    )
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

    # Made on the phone when the note is saved. A note that is sent twice
    # is stored once.
    client_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)

    seen_on = models.DateField(default=timezone.localdate)
    # Empty when the note is a photo or a recording with no text.
    findings = models.TextField(blank=True)
    treatment = models.TextField(blank=True)
    follow_up_date = models.DateField(null=True, blank=True)

    # The phone's clock when the vet tapped Save.
    written_at = models.DateTimeField(default=timezone.now)
    # Made while CVET_TEST_MODE was on: not a clinical record, and the
    # yard's administrator may delete it (without reading it).
    is_test = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # A clinical record is corrected, never silently replaced: every
    # change keeps the text before it.
    history = HistoricalRecords(excluded_fields=['updated_at'])

    objects = PracticeScopedQuerySet.as_manager()

    class Meta:
        ordering = ['-seen_on', '-written_at']
        indexes = [
            models.Index(fields=['practice', 'horse', '-seen_on'], name='cvet_note_practice_horse'),
        ]

    def __str__(self):
        # No clinical text here: this string reaches logs and error pages.
        return f'Clinical note {self.pk} ({self.seen_on})'


def clinical_file_path(instance, filename):
    # The name the phone sent is never used: only an extension, which the
    # view has already replaced with one from its own list. Files under
    # cvet/ are never served by the media view (horse_management/urls.py).
    extension = filename.rsplit('.', 1)[-1].lower()
    return f'cvet/{instance.kind}/{uuid.uuid4().hex}.{extension}'


class ClinicalFile(models.Model):
    """A photo or a voice recording that belongs to a clinical note.

    Served only by ``cvet.views.api_file``, to a member of the practice.
    ``practice`` repeats the note's practice so that the file can be
    scoped without a join, and stays scoped if the note is ever moved.
    """

    class Kind(models.TextChoices):
        PHOTO = 'photo', 'Photo'
        AUDIO = 'audio', 'Voice recording'

    practice = models.ForeignKey(Practice, on_delete=models.PROTECT, related_name='+')
    note = models.ForeignKey(ClinicalNote, on_delete=models.CASCADE, related_name='files')
    # Made on the phone. The address of the file is built from it.
    client_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    file = models.FileField(upload_to=clinical_file_path)
    content_type = models.CharField(max_length=50)
    bytes = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PracticeScopedQuerySet.as_manager()

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.get_kind_display()} {self.pk}'


class SyncReceipt(models.Model):
    """How one note arrived from the phone. No clinical text.

    The measures of the phone test (docs/C_VET_PLAN.md, section 8): how
    long the note waited on the phone, how many tries it took, and if
    its files arrived complete. The yard's administrator reads these in
    the Django admin. The note itself is not shown there.
    """

    practice = models.ForeignKey(Practice, on_delete=models.CASCADE, related_name='+')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    # Kept when the note is deleted, so the test results outlive the
    # test notes.
    note = models.OneToOneField(
        ClinicalNote, on_delete=models.SET_NULL, null=True, blank=True, related_name='receipt',
    )
    note_client_id = models.UUIDField(unique=True)
    is_test = models.BooleanField(default=False)

    # The phone's clock at Save, and the server's clock at arrival.
    device_created_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    # How many times the phone tried to send this note.
    attempts = models.PositiveIntegerField(default=1)
    text_length = models.PositiveIntegerField(default=0)
    photo_bytes = models.PositiveIntegerField(default=0)
    # True when the file that arrived has the SHA-256 the phone worked
    # out before it sent it. None: no file, or the phone sent no hash.
    photo_intact = models.BooleanField(null=True, blank=True)
    audio_bytes = models.PositiveIntegerField(default=0)
    audio_intact = models.BooleanField(null=True, blank=True)
    # What the phone reported about itself: browser, installed or not,
    # storage used, time to open. See static/cvet/app.js, clientInfo().
    client_info = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-received_at']
        verbose_name = 'sync receipt'

    def __str__(self):
        return f'Receipt {self.pk} ({self.received_at:%d %b %Y %H:%M})'

    @property
    def waited(self):
        """Time from Save on the phone to arrival on the server."""
        return self.received_at - self.device_created_at
