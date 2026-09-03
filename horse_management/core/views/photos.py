"""
Quick-add photo views: camera-first capture of condition/markings/passport
shots against a horse's record, with far fewer steps than the full
document or horse-edit forms.
"""

from pathlib import Path

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
from ._popup import is_popup_request, popup_saved_response

_image_extension_validator = FileExtensionValidator(
    allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif'],
)


def _validate_decodes_as_image(upload):
    """Reject files that only look like images by extension.

    objects.create() skips field validation entirely, so the ImageField's
    Pillow check never ran on this path — a renamed non-image was stored
    as a broken asset.
    """
    from PIL import Image, UnidentifiedImageError
    try:
        upload.seek(0)
        with Image.open(upload) as img:
            img.verify()
    except (UnidentifiedImageError, OSError):
        raise ValidationError("File is not a valid image.")
    finally:
        upload.seek(0)


def _passport_title(index, total):
    """Auto-title for a passport Document, unique within one batch."""
    title = f"Passport photo — {date_format(timezone.localdate(), 'j M Y')}"
    if total > 1:
        title += f" ({index + 1})"
    return title


def _use_as_profile(horse, photo):
    """Copy a HorsePhoto's image onto Horse.photo — the picture shown next
    to the horse's name. The log entry keeps its own file; Horse.save()
    regenerates the avatar thumbnail from the new photo."""
    photo.image.open('rb')
    try:
        horse.photo.save(Path(photo.image.name).name, photo.image, save=False)
    finally:
        photo.image.close()
    horse.save(update_fields=['photo'])


@feature_required('horses')
def horse_photo_add(request, pk):
    """Quick-add one or more photos to a horse (?category= preselects).

    Files are normalised (HEIC→JPEG, downscale) and validated one by one:
    valid files are saved and invalid ones reported per file, because a
    browser can't re-populate a file input on redisplay — all-or-nothing
    would force re-shooting the whole batch over yard 4G.

    Serves the pop-up sheet too (HX-Target: popup-body): the form partial
    alone, 204 + ``popup:saved`` once anything is saved (per-file skips
    ride along as toasts on the refresh), and the partial with the skips
    inline when nothing could be saved.
    """
    horse = get_object_or_404(Horse, pk=pk)
    in_popup = is_popup_request(request)
    skipped = []

    if request.method == 'POST':
        form = QuickPhotoForm(request.POST, request.FILES)
        if form.is_valid():
            category = form.cleaned_data['category']
            caption = form.cleaned_data['caption']
            uploads = form.cleaned_data['images']
            set_profile = (
                form.cleaned_data['set_profile'] and category != QUICK_PHOTO_PASSPORT
            )

            saved = 0
            first_photo = None
            for index, upload in enumerate(uploads):
                upload = normalise_photo(upload)
                try:
                    _image_extension_validator(upload)
                    validate_file_size(upload)
                    _validate_decodes_as_image(upload)
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
                    photo = HorsePhoto.objects.create(
                        horse=horse,
                        image=upload,
                        category=category,
                        caption=caption,
                        uploaded_by=request.user,
                    )
                    if first_photo is None:
                        first_photo = photo
                saved += 1

            if saved:
                for name, reason in skipped:
                    messages.error(request, f"Skipped {name}: {reason}")
                noun = 'photo' if saved == 1 else 'photos'
                msg = f"{saved} {noun} saved to {horse.name}."
                if set_profile and first_photo is not None:
                    _use_as_profile(horse, first_photo)
                    msg += " Profile picture updated."
                messages.success(request, msg)
                if in_popup:
                    return popup_saved_response()
                return redirect('horse_detail', pk=horse.pk)
            # Nothing survived: fall through and redisplay the form with
            # the per-file errors shown inline.
    else:
        initial = {}
        category = request.GET.get('category', '')
        valid_categories = {c for c, _ in QuickPhotoForm.base_fields['category'].choices}
        if category in valid_categories:
            initial['category'] = category
        form = QuickPhotoForm(initial=initial)

    template = 'horses/partials/photo_form.html' if in_popup else 'horses/photo_quick_add.html'
    return render(request, template, {
        'form': form,
        'horse': horse,
        'in_popup': in_popup,
        'skipped': skipped,
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


@feature_required('horses')
def horse_photo_set_profile(request, pk):
    """Make a logged photo the horse's profile picture (POST only)."""
    photo = get_object_or_404(HorsePhoto, pk=pk)
    horse = photo.horse

    if request.method == 'POST':
        _use_as_profile(horse, photo)
        messages.success(request, f"{horse.name}'s profile picture updated.")
    return redirect('horse_detail', pk=horse.pk)
