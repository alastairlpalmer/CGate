"""CSV exports of the main lists, and the owner privacy actions
(core/gdpr.py): download everything held about an owner, or erase it."""

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import audit, gdpr
from ..csv_export import csv_response
from ..downloads import attachment_header
from ..models import AuditEvent, Horse, Owner, OwnershipShare
from ..permissions import LEVEL_VIEW, feature_required


def _stamp():
    return f'{timezone.localdate():%Y-%m-%d}'


@feature_required('owners', LEVEL_VIEW)
def owners_csv(request):
    owners = Owner.objects.order_by('is_archived', 'name')
    return csv_response(
        f'owners-{_stamp()}',
        ['Name', 'Email', 'Phone', 'Address', 'Account code', 'Archived'],
        ([o.name, o.email, o.phone, o.address, o.account_code, o.is_archived] for o in owners),
    )


@feature_required('horses', LEVEL_VIEW)
def horses_csv(request):
    shares = {}
    for s in OwnershipShare.objects.select_related('owner'):
        shares.setdefault(s.horse_id, []).append(f'{s.owner.name} ({s.share_percentage:g}%)')
    horses = Horse.objects.order_by('-is_active', 'name')
    return csv_response(
        f'horses-{_stamp()}',
        ['Name', 'Active', 'Date of birth', 'Sex', 'Colour', 'Owners'],
        ([h.name, h.is_active, h.date_of_birth, h.get_sex_display() if h.sex else '', h.get_color_display() if h.color else '',
          '; '.join(shares.get(h.pk, []))] for h in horses),
    )


@feature_required('charges', LEVEL_VIEW)
def charges_csv(request):
    from billing.models import ExtraCharge

    charges = ExtraCharge.objects.select_related('horse', 'owner').order_by('-date')
    return csv_response(
        f'charges-{_stamp()}',
        ['Date', 'Horse', 'Owner', 'Type', 'Description', 'Amount', 'Invoiced'],
        ([c.date, c.horse.name, c.owner.name if c.owner else '', c.get_charge_type_display(),
          c.description, c.amount, c.invoiced] for c in charges),
    )


@feature_required('owners')
def owner_data_export(request, pk):
    """A subject access request: everything about one owner, as a ZIP."""
    owner = get_object_or_404(Owner, pk=pk)
    data = gdpr.export_owner(owner)
    audit.record(AuditEvent.Action.OWNER_EXPORTED, request=request, detail=gdpr.owner_label(owner))
    response = HttpResponse(data, content_type='application/zip')
    response['Content-Disposition'] = attachment_header(f'{owner.name or f"owner-{owner.pk}"} - data - {_stamp()}.zip')
    return response


@feature_required('owners')
@require_POST
def owner_erase(request, pk):
    """A request to be forgotten (core/gdpr.py says what goes and what stays)."""
    owner = get_object_or_404(Owner, pk=pk)
    full = request.POST.get('scope') == 'all'
    if full and owner.invoices.exists():
        messages.error(
            request,
            "This owner has invoices, which must keep their name and address for "
            "six years. Erase contact details now; the rest goes automatically then.",
        )
        return redirect('owner_detail', pk=owner.pk)
    try:
        if full:
            gdpr.anonymise_owner(owner, request.user)
            messages.success(request, f"Owner anonymised. They now show as “{owner.name}”.")
        else:
            gdpr.erase_contact_details(owner, request.user)
            messages.success(
                request,
                "Email, phone, notes, account code and documents erased, including earlier "
                "copies. Name and address stay on their invoices until the retention period ends.",
            )
    except gdpr.ErasureRefused as exc:
        messages.error(request, str(exc))
    return redirect('owner_detail', pk=owner.pk)


@feature_required('owners')
@require_POST
def owner_email_ok(request, pk):
    """Clear a bounce flag: the address was fixed outside the app, or the
    bounce was a one-off."""
    owner = get_object_or_404(Owner, pk=pk)
    owner.email_bounced_at = None
    owner.email_bounce_reason = ''
    owner.save(update_fields=['email_bounced_at', 'email_bounce_reason'])
    messages.success(request, "Emails to this owner will be sent again.")
    return redirect('owner_detail', pk=owner.pk)
