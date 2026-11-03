"""Settings → Database Check: manage.py schema_check, without a console."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from .. import mfa
from ..schema_drift import column_differences, migration_differences


@login_required
def database_check(request):
    # Administrators only: it names every table and column.
    if not mfa.is_administrator(request.user):
        raise PermissionDenied
    return render(request, 'settings/database_check.html', {
        'columns': column_differences(),
        'migrations': migration_differences(),
    })
