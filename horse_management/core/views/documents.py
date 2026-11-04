"""
Document upload/management views (passport scans, insurance certs, etc.).
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from django.contrib.auth.decorators import login_required

from core.permissions import LEVEL_FULL, LEVEL_VIEW, FeatureAccessMixin, deny, feature_required, has_feature_access

from ..forms import DocumentForm
from ._popup import is_popup_request, popup_saved_response
from ._undo import undo_tag
from ..models import Document, Horse, Owner


def _attachment_target(request):
    """Resolve the horse/owner a document is being attached to from GET/POST."""
    horse_id = request.POST.get('horse') or request.GET.get('horse', '')
    owner_id = request.POST.get('owner') or request.GET.get('owner', '')
    horse = get_object_or_404(Horse, pk=horse_id) if str(horse_id).isdigit() else None
    owner = get_object_or_404(Owner, pk=owner_id) if str(owner_id).isdigit() else None
    return horse, owner


def _document_feature(horse, owner):
    """A horse's documents follow Horses access; an owner's, Owners access."""
    return 'horses' if horse else 'owners'


def _may_change(request, horse, owner):
    return has_feature_access(request.user, _document_feature(horse, owner), LEVEL_FULL)


def _back_to(horse, owner):
    if horse:
        return redirect('horse_detail', pk=horse.pk)
    return redirect('owner_detail', pk=owner.pk)


@login_required
def document_create(request):
    """Upload a document against a horse or an owner (?horse= / ?owner=)."""
    horse, owner = _attachment_target(request)
    if not horse and not owner:
        if not has_feature_access(request.user, 'horses', LEVEL_FULL):
            return deny(request)
        messages.error(request, "Choose a horse or an owner to attach the document to.")
        return redirect('horse_list')
    if not _may_change(request, horse, owner):
        return deny(request)

    in_popup = is_popup_request(request)

    if request.method == 'POST':
        # Seed the instance so the model's horse-or-owner clean() passes.
        document = Document(horse=horse, owner=owner, uploaded_by=request.user)
        form = DocumentForm(request.POST, request.FILES, instance=document)
        if form.is_valid():
            document = form.save()
            messages.success(
                request,
                f"{document.get_doc_type_display()} “{document.title}” uploaded."
            )
            if in_popup:
                return popup_saved_response()
            return _back_to(horse, owner)
    else:
        form = DocumentForm()

    template = 'includes/popup_form.html' if in_popup else 'documents/document_form.html'
    return render(request, template, {
        'form': form,
        'horse': horse,
        'owner': owner,
        'in_popup': in_popup,
        'popup_submit_label': 'Upload',
    })


@login_required
def document_delete(request, pk):
    """Delete a document (POST only, confirmed client-side)."""
    document = get_object_or_404(Document, pk=pk)
    horse, owner = document.horse, document.owner
    if not _may_change(request, horse, owner):
        return deny(request)

    if request.method != 'POST':
        return _back_to(horse, owner)

    # Soft delete: the file stays in storage until the nightly purge, so a
    # passport scan deleted by mistake can be restored (core/softdelete.py).
    document.soft_delete(request.user)
    messages.success(
        request, f"Document “{document.title}” deleted.",
        extra_tags=undo_tag(reverse('document_restore', args=[document.pk])),
    )
    return _back_to(horse, owner)


@login_required
@require_POST
def document_restore(request, pk):
    document = get_object_or_404(Document.all_objects.deleted(), pk=pk)
    if not _may_change(request, document.horse, document.owner):
        return deny(request)
    document.restore()
    messages.success(request, f"Document “{document.title}” restored.")
    return _back_to(document.horse, document.owner)
