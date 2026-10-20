"""How every background task runs. docs/LAUNCH_READINESS_PLAN.md, P1
work package 7.

Every task is a ``ReliableTask`` (set as the app's task class in
horse_management/celery.py). That gives each one three things:

1. **Retries with backoff.** A database or network error (the worker lost
   Postgres for a second, Redis or the mail server refused a connection)
   retries the task up to ``max_retries`` times, waiting about 1, 2 and 4
   minutes. Other errors are bugs, and a retry would only fail again.

2. **A record when it gives up.** A task that fails every retry is written
   to ``FailedTask``, and Settings → Background Tasks lists it with a
   **Run again** button. Before this, a failed task left a line in the
   log and nothing else.

3. **Once a day for scheduled tasks.** A task in ``CELERY_BEAT_SCHEDULE``
   first claims a ``ScheduledRun`` row for (task, today). A second beat
   process, a redelivered message or a manual "run" from the Django admin
   finds the claim taken and does nothing. A failed run gives up its claim,
   so **Run again** works the same day. The rows also show when each task
   last ran, which the health check and the Background Tasks page use.

Calling a task as a plain function (``send_vaccination_reminders()``, as
the tests and the inline invoice send do) skips all three: the claim and
the record need a task id, and there is no queue to retry through.
"""

import logging
import traceback

from celery import Task
from django.conf import settings
from django.db import IntegrityError, InterfaceError, OperationalError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Errors that a second try a few minutes later can fix. OSError covers
# socket errors, timeouts, refused connections and smtplib's errors.
RETRYABLE_ERRORS = (OperationalError, InterfaceError, OSError)

# The result Celery stores when a scheduled task finds today's claim taken.
ALREADY_RAN = 'already_ran'


def scheduled_task_names():
    """Dotted names of the tasks that beat runs on a schedule."""
    return {entry['task'] for entry in settings.CELERY_BEAT_SCHEDULE.values()}


class ReliableTask(Task):
    autoretry_for = RETRYABLE_ERRORS
    max_retries = 3
    retry_backoff = 60
    retry_backoff_max = 10 * 60
    retry_jitter = True

    def __call__(self, *args, **kwargs):
        task_id = self.request.id
        if not task_id or self.name not in scheduled_task_names():
            return super().__call__(*args, **kwargs)
        try:
            run = claim_scheduled_run(self.name, task_id)
        except RETRYABLE_ERRORS as exc:
            # autoretry_for covers the task body only, not this claim.
            raise self.retry(exc=exc)
        if run is None:
            logger.info('%s already ran today; skipped this copy.', self.name)
            return ALREADY_RAN
        result = super().__call__(*args, **kwargs)
        finish_scheduled_run(run, result)
        return result

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        logger.warning('%s failed (%s); retrying.', self.name, exc)

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        # Never let the record hide the real error. If the database is the
        # thing that failed, this write fails too, and the log still has it.
        try:
            record_failure(self, exc, task_id, args, kwargs, einfo)
        except Exception:
            logger.exception('Could not record the failure of %s', self.name)


def _run_key():
    return timezone.localdate().isoformat()


def claim_scheduled_run(task_name, task_id):
    """Claim today's run of a scheduled task. None if another copy has it.

    The same task id may take its own claim back: that is a retry, or the
    broker redelivering the message after the worker died (acks_late).
    A failed run may be taken over by a new id, so "Run again" works.
    """
    from django.db.models import Q

    from core.models import ScheduledRun

    key = _run_key()
    try:
        with transaction.atomic():
            return ScheduledRun.objects.create(task_name=task_name, run_key=key, task_id=task_id)
    except IntegrityError:
        pass
    claimed = ScheduledRun.objects.filter(task_name=task_name, run_key=key).filter(
        Q(task_id=task_id) | Q(status=ScheduledRun.Status.FAILED)
    ).update(task_id=task_id, status=ScheduledRun.Status.RUNNING,
             started_at=timezone.now(), finished_at=None, result='')
    if not claimed:
        return None
    return ScheduledRun.objects.get(task_name=task_name, run_key=key)


def finish_scheduled_run(run, result):
    from core.models import ScheduledRun

    run.status = ScheduledRun.Status.SUCCESS
    run.finished_at = timezone.now()
    run.result = str(result)[:500]
    run.save(update_fields=['status', 'finished_at', 'result'])
    ping_heartbeat(run.task_name)


def record_failure(task, exc, task_id, args, kwargs, einfo):
    from core.models import FailedTask, ScheduledRun

    FailedTask.objects.create(
        task_name=task.name,
        task_id=task_id or '',
        args=list(args or []),
        kwargs=dict(kwargs or {}),
        error=f'{type(exc).__name__}: {exc}'[:1000],
        traceback=(str(einfo) if einfo else traceback.format_exc())[-8000:],
        retries=task.request.retries,
    )
    if task.name in scheduled_task_names():
        ScheduledRun.objects.filter(task_name=task.name, task_id=task_id).update(
            status=ScheduledRun.Status.FAILED, finished_at=timezone.now(),
            result=f'{type(exc).__name__}: {exc}'[:500],
        )
    logger.error('%s failed after %s retries: %s', task.name, task.request.retries, exc)


def ping_heartbeat(task_name):
    """Tell an outside monitor that a scheduled task finished.

    ``CRON_HEARTBEAT_URL`` is a URL with ``{task}`` in it, for example a
    Healthchecks.io ping URL with a slug per check. The monitor alerts when
    a ping does not arrive on time, which catches a beat that has stopped:
    nothing inside the app can report that it is not running.
    """
    template = getattr(settings, 'CRON_HEARTBEAT_URL', '')
    if not template:
        return
    import requests

    url = template.replace('{task}', task_name.rsplit('.', 1)[-1])
    try:
        requests.get(url, timeout=5)
    except requests.RequestException:
        logger.warning('Heartbeat ping failed for %s', task_name)


def run_again(failed):
    """Queue a failed task again with its original arguments."""
    from celery import current_app

    current_app.send_task(failed.task_name, args=failed.args, kwargs=failed.kwargs)


def last_runs():
    """The newest ScheduledRun of each scheduled task, by task name."""
    from core.models import ScheduledRun

    latest = {}
    for run in ScheduledRun.objects.order_by('-started_at')[:500]:
        latest.setdefault(run.task_name, run)
    return latest
