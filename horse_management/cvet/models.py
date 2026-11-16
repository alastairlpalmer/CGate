"""Models for the C-vet offline prototype (docs/C_VET_PLAN.md, phase 1)."""

import uuid

from django.conf import settings
from django.db import models


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
