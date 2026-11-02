"""Give every tracked record a starting point in its history.

History only begins when the audit trail is deployed. Without a first
copy of each row, the first change after that would have nothing to be
compared with, and the history panel could not say what changed.

Runs on every deploy (railway/web.json). It only fills a model whose
history is still empty, so after the first run it does nothing.
"""

from django.apps import apps
from django.core.management.base import BaseCommand

from core.audit import BASELINE_REASON, TRACKED_MODELS


class Command(BaseCommand):
    help = "Record the current state of each tracked model as the start of its history."

    def handle(self, *args, **options):
        for _, label, path in TRACKED_MODELS:
            model = apps.get_model(path)
            if model.history.exists():
                continue
            rows = list(model._default_manager.all())
            if rows:
                model.history.bulk_history_create(
                    rows, batch_size=500, default_change_reason=BASELINE_REASON,
                )
            self.stdout.write(f"{label}: {len(rows)} starting record(s).")
