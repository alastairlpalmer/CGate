"""Settings → Background Tasks: tasks that failed every retry, with Run
again and Dismiss, and when each scheduled task last ran (core/jobs.py)."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import jobs, mfa
from ..models import FailedTask


def _require_admin(user):
    # Administrators only: a failed task can name any area of the app.
    if not mfa.is_administrator(user):
        raise PermissionDenied


@login_required
def background_tasks(request):
    _require_admin(request.user)
    last = jobs.last_runs()
    schedule = [
        {
            'label': key.replace('-', ' ').capitalize(),
            'run': last.get(entry['task']),
        }
        for key, entry in settings.CELERY_BEAT_SCHEDULE.items()
    ]
    return render(request, 'settings/background_tasks.html', {
        'failures': FailedTask.objects.filter(resolved_at__isnull=True)[:100],
        'schedule': schedule,
    })


@login_required
@require_POST
def failed_task_resolve(request, pk):
    _require_admin(request.user)
    failed = get_object_or_404(FailedTask, pk=pk, resolved_at__isnull=True)
    if request.POST.get('action') == 'run_again':
        jobs.run_again(failed)
        failed.resolution = 'run again'
        messages.success(request, f'{failed.short_name}: queued to run again.')
    else:
        failed.resolution = 'dismissed'
        messages.success(request, f'{failed.short_name}: dismissed.')
    failed.resolved_at = timezone.now()
    failed.resolved_by = request.user
    failed.save(update_fields=['resolution', 'resolved_at', 'resolved_by'])
    return redirect('background_tasks')
