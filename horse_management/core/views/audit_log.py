"""Settings → Audit Log: the whole trail, newest first (core/audit.py)."""

from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils.dateparse import parse_datetime

from .. import audit, mfa


@login_required
def audit_log(request):
    # Administrators only: the trail spans every area, including ones a
    # custom role may not be allowed to see.
    if not mfa.is_administrator(request.user):
        raise PermissionDenied
    category = request.GET.get('category', '')
    if category not in dict(audit.CATEGORIES):
        category = ''
    before = parse_datetime(request.GET.get('before', '') or '')
    if before is not None and not isinstance(before, datetime):
        before = None
    entries, next_before = audit.timeline(category=category, before=before)
    return render(request, 'settings/audit_log.html', {
        'entries': entries,
        'categories': audit.CATEGORIES,
        'category': category,
        'next_before': next_before.isoformat() if next_before else '',
        'is_first_page': before is None,
    })
