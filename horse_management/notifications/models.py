"""The notification centre: one row per person per thing to know.

docs/SIDEBAR_AND_NOTIFICATIONS_PLAN.md, WP3. Three categories:

* ``action``: what needs doing, copied from the dashboard's attention list
  (core.dashboard.attention) by notifications.centre.sync_actions. A row
  closes itself (``resolved_at``) when the job is done or dismissed.
* ``movement``: a horse arrived, moved or left.
* ``money``: an invoice was paid or became overdue, or Xero sync failed.

Each person chooses, per category, whether to see them in the app and
whether to get them by email (NotificationPreference).
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):

    class Category(models.TextChoices):
        ACTION = 'action', 'Needs action'
        MOVEMENT = 'movement', 'Horse movements'
        MONEY = 'money', 'Money'

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications',
    )
    category = models.CharField(max_length=20, choices=Category.choices)
    # What happened: an attention kind ('vaccination', 'invoice'…) or an
    # event ('arrived', 'moved', 'departed', 'invoice_paid'…). Picks the icon.
    kind = models.CharField(max_length=40)
    # Needs-action rows only: 'overdue', 'due' or 'info'.
    severity = models.CharField(max_length=10, blank=True)
    # Space-separated filter chips this row answers to (overdue, health,
    # yard, money, movement).
    tags = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=500, blank=True)
    url = models.CharField(max_length=500, blank=True)
    due_date = models.DateField(null=True, blank=True)
    # Needs-action rows: their lines, buttons and dismiss tokens.
    data = models.JSONField(default=dict, blank=True)
    # The same thing never shows twice for one person.
    dedupe_key = models.CharField(max_length=200)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['recipient', 'dedupe_key'], name='notification_unique_per_person',
            ),
        ]
        indexes = [
            models.Index(fields=['recipient', 'read_at', 'resolved_at'], name='notification_unread_idx'),
        ]

    def __str__(self):
        return f'{self.title} → {self.recipient}'

    @property
    def is_unread(self):
        return self.read_at is None and self.resolved_at is None

    @property
    def is_open_action(self):
        return self.category == self.Category.ACTION and self.resolved_at is None

    @property
    def lines(self):
        """Needs-action lines, with their due dates as dates again."""
        from datetime import date
        result = []
        for line in (self.data or {}).get('lines', []):
            line = dict(line)
            due = line.get('due')
            line['due'] = date.fromisoformat(due) if due else None
            result.append(line)
        return result


class NotificationPreference(models.Model):
    """One person's choice for one category. No row means the default:
    in the app on, by email off."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_preferences',
    )
    category = models.CharField(max_length=20, choices=Notification.Category.choices)
    in_app = models.BooleanField(default=True)
    email = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'category'], name='notification_pref_unique'),
        ]

    def __str__(self):
        return f'{self.user} · {self.category}'
