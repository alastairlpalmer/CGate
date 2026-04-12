from datetime import timedelta

from django.db import models
from django.utils import timezone


class XeroConnection(models.Model):
    """Singleton: stores the Xero OAuth2 connection for this business."""

    access_token = models.TextField(blank=True)
    refresh_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    xero_tenant_id = models.CharField(max_length=100, blank=True)
    xero_tenant_name = models.CharField(max_length=200, blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    last_refreshed_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=False)

    # OAuth state parameter for CSRF protection during flow
    oauth_state = models.CharField(max_length=128, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Xero Connection'

    def __str__(self):
        if self.is_active:
            return f'Connected to {self.xero_tenant_name}'
        return 'Not connected'

    def save(self, *args, **kwargs):
        self.pk = 1  # Singleton pattern
        super().save(*args, **kwargs)

    @classmethod
    def get_connection(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def is_token_expired(self):
        if not self.token_expires_at:
            return True
        return timezone.now() >= self.token_expires_at

    @property
    def is_refresh_expired(self):
        """Refresh tokens expire after 60 days."""
        if not self.connected_at and not self.last_refreshed_at:
            return True
        last_activity = self.last_refreshed_at or self.connected_at
        return timezone.now() > last_activity + timedelta(days=60)

    @property
    def is_connected(self):
        return self.is_active and bool(self.refresh_token) and not self.is_refresh_expired


class XeroContactMapping(models.Model):
    """Maps a LivMan Owner to a Xero Contact."""

    owner = models.OneToOneField(
        'core.Owner',
        on_delete=models.CASCADE,
        related_name='xero_contact',
    )
    xero_contact_id = models.CharField(max_length=100, unique=True)
    xero_contact_name = models.CharField(max_length=200)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Xero Contact Mapping'

    def __str__(self):
        return f'{self.owner} → {self.xero_contact_name}'


class XeroInvoiceSync(models.Model):
    """Tracks the sync state of an invoice with Xero."""

    class SyncStatus(models.TextChoices):
        NOT_PUSHED = 'not_pushed', 'Not Pushed'
        PUSHED = 'pushed', 'Pushed to Xero'
        PAID_IN_XERO = 'paid_in_xero', 'Paid in Xero'
        ERROR = 'error', 'Error'

    invoice = models.OneToOneField(
        'invoicing.Invoice',
        on_delete=models.CASCADE,
        related_name='xero_sync',
    )
    xero_invoice_id = models.CharField(max_length=100, blank=True)
    xero_invoice_number = models.CharField(max_length=100, blank=True)
    sync_status = models.CharField(
        max_length=20,
        choices=SyncStatus.choices,
        default=SyncStatus.NOT_PUSHED,
    )
    last_pushed_at = models.DateTimeField(null=True, blank=True)
    last_status_check_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Xero Invoice Sync'

    def __str__(self):
        return f'{self.invoice.invoice_number} — {self.get_sync_status_display()}'
