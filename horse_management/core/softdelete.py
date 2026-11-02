"""Soft delete: "I deleted it by accident" becomes a restore, not an incident.

A soft-deleted row keeps everything it had and gains ``deleted_at`` and
``deleted_by``. The default manager (``objects``) hides it, so lists,
totals, invoice runs and related managers (``horse.extra_charges``) behave
as if it were gone. ``all_objects`` still sees it, for the restore and
the Recently Deleted page.

Rows stay restorable for ``SOFT_DELETE_RETENTION_DAYS`` and are then
deleted for good by ``core.tasks.purge_soft_deleted``.

Two things the manager cannot do on its own:

* A join from another model (``Sum('extra_charges__amount')``,
  ``filter(extra_charges__date=...)``) does not go through the manager.
  Such queries must add ``<relation>__deleted_at__isnull=True``.
* A foreign key read (``record.extra_charge``) uses the plain base
  manager, so it can return a soft-deleted row. Check ``is_deleted``.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class LiveQuerySet(models.QuerySet):
    def live(self):
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        return self.filter(deleted_at__isnull=False)


class LiveManager(models.Manager.from_queryset(LiveQuerySet)):
    """Hides soft-deleted rows."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class SoftDeleteModel(models.Model):
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True, editable=False)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
        on_delete=models.SET_NULL, related_name='+',
    )

    objects = LiveManager()
    all_objects = models.Manager.from_queryset(LiveQuerySet)()

    class Meta:
        abstract = True

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    def soft_delete(self, user=None):
        self.deleted_at = timezone.now()
        self.deleted_by = user if getattr(user, 'is_authenticated', False) else None
        self.save(update_fields=['deleted_at', 'deleted_by'])

    def restore(self):
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=['deleted_at', 'deleted_by'])

    @property
    def purge_after(self):
        """When the purge job deletes this row for good."""
        if self.deleted_at is None:
            return None
        return self.deleted_at + timezone.timedelta(days=settings.SOFT_DELETE_RETENTION_DAYS)
