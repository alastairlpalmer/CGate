"""
Quick-add photo views: camera-first capture of condition/markings/passport
shots against a horse's record, with far fewer steps than the full
document or horse-edit forms.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import date_format

from core.permissions import LEVEL_VIEW, FeatureAccessMixin, feature_required

from ..forms import QUICK_PHOTO_PASSPORT, QuickPhotoForm
from ..images import normalise_photo
from ..models import Document, Horse, HorsePhoto, validate_file_size

_image_extension_validator = FileExtensionValidator(
    allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif'],
)


def _passport_title(index, total):
    """Auto-title for a passport Document, unique within one batch."""
    title = f"Passport photo — {date_format(timezone.localdate(), 'j M Y')}"
    if total > 1:
        title += f" ({index + 1})"
    return title


@feature_required('horses')
def horse_photo_add(request, pk):
    """Quick-add one or more photos to a horse (?category= preselects).

    Files are normalised (HEIC→JPEG, downscale) and validated one by one:
    valid files are saved and invalid ones reported per file, because a
    browser can't re-populate a file input on redisplay — all-or-nothing
    would force re-shooting the whole batch over yard 4G.
    """
    horse = get_object_or_404(Horse, pk=pk)

    if request.method == 'POST':
        form = QuickPhotoForm(request.POST, request.FILES)
        if form.is_valid():
            category = form.cleaned_data['category']
            caption = form.cleaned_data['caption']
            uploads = form.cleaned_data['images']

            saved = 0
            skipped = []
            for index, upload in enumerate(uploads):
                upload = normalise_photo(upload)
                try:
                    _image_extension_validator(upload)
                    validate_file_size(upload)
                except ValidationError as e:
                    skipped.append((upload.name, '; '.join(e.messages)))
                    continue

                if category == QUICK_PHOTO_PASSPORT:
                    Document.objects.create(
                        horse=horse,
                        doc_type=Document.DocType.PASSPORT,
                        title=_passport_title(index, len(uploads)),
                        file=upload,
                        notes=caption,
                        uploaded_by=request.user,
                    )
                else:
                    HorsePhoto.objects.create(
                        horse=horse,
                        image=upload,
                        category=category,
                        caption=caption,
                        uploaded_by=request.user,
                    )
                saved += 1

            for name, reason in skipped:
                messages.error(request, f"Skipped {name}: {reason}")

            if saved:
                noun = 'photo' if saved == 1 else 'photos'
                messages.success(request, f"{saved} {noun} saved to {horse.name}.")
                return redirect('horse_detail', pk=horse.pk)
            # Nothing survived: fall through and redisplay the form with
            # the per-file errors shown as toasts.
    else:
        initial = {}
        category = request.GET.get('category', '')
        valid_categories = {c for c, _ in QuickPhotoForm.base_fields['category'].choices}
        if category in valid_categories:
            initial['category'] = category
        form = QuickPhotoForm(initial=initial)

    return render(request, 'horses/photo_quick_add.html', {
        'form': form,
        'horse': horse,
    })


@feature_required('horses')
def horse_photo_delete(request, pk):
    """Delete a horse photo (POST only, confirmed client-side)."""
    photo = get_object_or_404(HorsePhoto, pk=pk)
    horse = photo.horse

    if request.method != 'POST':
        return redirect('horse_detail', pk=horse.pk)

    photo.image.delete(save=False)
    if photo.thumb:
        photo.thumb.delete(save=False)
    photo.delete()
    messages.success(request, "Photo deleted.")
    return redirect('horse_detail', pk=horse.pk)
