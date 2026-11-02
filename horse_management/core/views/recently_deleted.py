"""Settings → Recently Deleted: soft-deleted rows, with Restore.

Each section shows only to users with full access to its area, the same
access the delete needed (core/softdelete.py).
"""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from ..permissions import LEVEL_FULL, has_feature_access


@login_required
def recently_deleted(request):
    from billing.models import ExtraCharge, YardCost

    from ..models import Document

    sections = []
    if has_feature_access(request.user, 'charges', LEVEL_FULL):
        sections.append({
            'title': 'Charges',
            'restore_url': 'charge_restore',
            'rows': [
                (c, f"{c.horse.name}: {c.description or c.get_charge_type_display()}, £{c.amount:.2f} ({c.date:%d %b %Y})")
                for c in ExtraCharge.all_objects.deleted().select_related('horse', 'deleted_by').order_by('-deleted_at')
            ],
        })
    if has_feature_access(request.user, 'costs', LEVEL_FULL):
        sections.append({
            'title': 'Yard costs',
            'restore_url': 'yard_cost_restore',
            'rows': [
                (c, f"{c.description}, £{c.amount:.2f} ({c.date:%d %b %Y})")
                for c in YardCost.all_objects.deleted().select_related('deleted_by').order_by('-deleted_at')
            ],
        })
    if has_feature_access(request.user, 'horses', LEVEL_FULL):
        sections.append({
            'title': 'Documents',
            'restore_url': 'document_restore',
            'rows': [
                (d, f"{d.title} ({d.get_doc_type_display()}, {d.horse or d.owner})")
                for d in Document.all_objects.deleted().select_related('horse', 'owner', 'deleted_by').order_by('-deleted_at')
            ],
        })
    return render(request, 'settings/recently_deleted.html', {
        'sections': sections,
        'retention_days': settings.SOFT_DELETE_RETENTION_DAYS,
    })
