"""The C-vet offline prototype (docs/C_VET_PLAN.md, phase 1).

One page, one service worker and three JSON endpoints:

* ``app`` is the page. It carries no data, so the service worker can keep
  a copy and open it with no signal.
* ``service_worker`` is the script that keeps that copy.
* ``api_session`` says who is signed in and gives a CSRF token. The page
  cannot read the token from the cookie (``CSRF_COOKIE_HTTPONLY``), and a
  token printed in a stored page would go stale.
* ``api_bootstrap`` is the horse list the phone stores before a visit.
* ``api_entries`` takes one note, with its photo and recording, and
  stores it once however many times it is sent.

Everything is a 404 unless ``CVET_PROTOTYPE_ENABLED`` is set. The gate is
``cvet.access``: Full on the ``cvet`` feature and membership of a practice.
The horse list also needs the practice's link to the yard to be open.
"""

import hashlib
import json
import uuid
from datetime import timezone as dt_timezone
from functools import wraps
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from core import ratelimit
from core.models import Horse, Placement, prime_current_owners
from core.templatetags.cache_bust import _VERSION, static_v

from .access import cvet_enabled, horses_for, open_link, practice_api, practice_page
from .models import PrototypeEntry

NOTE_MAX_CHARS = 5000
PHOTO_MAX_BYTES = 8 * 1024 * 1024
AUDIO_MAX_BYTES = 8 * 1024 * 1024
CLIENT_INFO_MAX_CHARS = 4000
# Notes one login may send in an hour. A visit is tens of notes; this
# only stops a faulty phone that sends in a loop.
ENTRIES_PER_HOUR = 300

# What Pillow says the photo is -> the extension it is stored under.
PHOTO_FORMATS = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp', 'HEIF': 'heic'}
# What the phone says the recording is -> the extension it is stored
# under. iPhones record audio/mp4; Android and desktop Chrome, audio/webm.
AUDIO_TYPES = {
    'audio/mp4': 'm4a',
    'audio/aac': 'aac',
    'audio/webm': 'webm',
    'audio/ogg': 'ogg',
    'audio/mpeg': 'mp3',
    'audio/wav': 'wav',
}

# The static files the page needs. The service worker stores each of them.
STATIC_FILES = (
    'cvet/proto.css',
    'cvet/proto_core.js',
    'cvet/proto.js',
    'cvet/manifest.json',
    'icons/icon-192.png',
    'icons/apple-touch-icon.png',
)


def _user_payload(user):
    return {'id': user.pk, 'name': user.get_full_name() or user.get_username()}


@practice_page
def app(request):
    """The page. No data in it: the phone gets that from the API."""
    response = render(request, 'cvet/prototype.html', {
        'config': {
            'version': _VERSION,
            'sw_url': reverse('cvet:service_worker'),
            'scope': reverse('cvet:app'),
            'session_url': reverse('cvet:api_session'),
            'bootstrap_url': reverse('cvet:api_bootstrap'),
            'entries_url': reverse('cvet:api_entries'),
            'login_url': f"{reverse('login')}?next={reverse('cvet:app')}",
        },
    })
    # Voice recording needs the microphone, which the site turns off
    # everywhere else (settings.PERMISSIONS_POLICY). The middleware keeps
    # a header a view has already set, so this page alone allows it.
    policy = dict(settings.PERMISSIONS_POLICY, microphone=['self'])
    response.headers['Permissions-Policy'] = ', '.join(
        f"{feature}=({' '.join(origins)})" for feature, origins in policy.items()
    )
    return response


@cvet_enabled
@require_GET
def service_worker(request):
    """The service worker script.

    Served from /c-vet/ so that its scope is /c-vet/: a worker under
    /static/ could only control pages under /static/. No sign-in is
    needed: it holds a list of addresses and nothing else.
    """
    # json.dumps writes each value as a JavaScript literal. All three are
    # made here, from the deploy version and the URL table.
    response = render(request, 'cvet/sw.js', {
        'version': json.dumps(_VERSION),
        'shell_url': json.dumps(reverse('cvet:app')),
        'asset_urls': json.dumps([static_v(path) for path in STATIC_FILES]),
    }, content_type='text/javascript')
    # The browser checks for a new worker on each visit. It must get the
    # current one, not a stored copy.
    response.headers['Cache-Control'] = 'no-cache'
    return response


@practice_api
@require_GET
def api_session(request):
    return JsonResponse({
        'user': _user_payload(request.user),
        'practice': request.practice.name,
        'csrf': get_token(request),
    })


@practice_api
@require_GET
def api_bootstrap(request):
    """The yard's horses, with what the vet needs to pick the right one.

    Identity, owner and field name are shared data (docs/C_VET_PLAN.md,
    section 3), but only while the practice's link to the yard is open.
    """
    if open_link(request.practice) is None:
        return JsonResponse({'error': 'no_yard_link'}, status=403)
    horses = list(horses_for(request.practice).order_by('name'))
    prime_current_owners(horses)
    locations = {}
    for placement in (
        Placement.objects.filter(horse__in=horses, end_date__isnull=True)
        .select_related('location').order_by('horse_id', '-start_date')
    ):
        locations.setdefault(placement.horse_id, placement.location.name)
    return JsonResponse({
        'generated_at': timezone.now().isoformat(),
        'user': _user_payload(request.user),
        'practice': request.practice.name,
        'horses': [
            {
                'id': horse.pk,
                'name': horse.name,
                'microchip': horse.microchip,
                'passport': horse.passport_number,
                'location': locations.get(horse.pk, ''),
                'owner': horse.current_owner.name if horse.current_owner else '',
            }
            for horse in horses
        ],
    })


class EntryError(Exception):
    """The phone sent something this endpoint will not store."""


def _read_photo(upload):
    """The photo's bytes and extension, or EntryError if it is not a photo."""
    from PIL import Image, UnidentifiedImageError

    if upload.size > PHOTO_MAX_BYTES:
        raise EntryError('photo_too_large')
    data = upload.read()
    try:
        with Image.open(BytesIO(data)) as image:
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise EntryError('photo_not_an_image')
    extension = PHOTO_FORMATS.get(image_format)
    if extension is None:
        raise EntryError('photo_type_not_allowed')
    return data, extension


def _read_audio(upload):
    """The recording's bytes and extension.

    The content cannot be checked the way a photo can. The type must be on
    the list, the stored name is ours, and the file is never served to a
    browser as a page (the media view sends unknown folders to superusers
    only, as a download).
    """
    if upload.size > AUDIO_MAX_BYTES:
        raise EntryError('audio_too_large')
    # "audio/webm;codecs=opus" -> "audio/webm"
    content_type = (upload.content_type or '').split(';')[0].strip().lower()
    extension = AUDIO_TYPES.get(content_type)
    if extension is None:
        raise EntryError('audio_type_not_allowed')
    return upload.read(), extension


def _intact(data, claimed_sha256):
    """True or False when the phone sent a hash to check against, else None."""
    claimed = (claimed_sha256 or '').strip().lower()
    if not claimed:
        return None
    return hashlib.sha256(data).hexdigest() == claimed


def _entry_payload(entry, status):
    return {
        'status': status,
        'id': entry.pk,
        'received_at': entry.received_at.isoformat(),
        'photo_intact': entry.photo_intact,
        'audio_intact': entry.audio_intact,
    }


@practice_api
@require_POST
def api_entries(request):
    """Store one note. Safe to call again with the same ``client_id``."""
    try:
        client_id = uuid.UUID(request.POST.get('client_id', ''))
    except ValueError:
        return JsonResponse({'error': 'client_id_invalid'}, status=400)

    # Checked before anything else, so a repeat is cheap and is answered
    # even when the first attempt used up the hourly limit.
    existing = PrototypeEntry.objects.filter(client_id=client_id).first()
    if existing is not None:
        return _duplicate_response(request, existing)

    if not ratelimit.allow('cvet-prototype-entries', str(request.user.pk), ENTRIES_PER_HOUR, 3600):
        return JsonResponse({'error': 'too_many'}, status=429)

    created = parse_datetime(request.POST.get('device_created_at', ''))
    if created is None:
        return JsonResponse({'error': 'device_created_at_invalid'}, status=400)
    if timezone.is_naive(created):
        created = timezone.make_aware(created, dt_timezone.utc)

    note = request.POST.get('note', '')
    if len(note) > NOTE_MAX_CHARS:
        return JsonResponse({'error': 'note_too_long'}, status=400)

    horse = None
    horse_id = request.POST.get('horse_id', '')
    if horse_id.isdigit():
        horse = Horse.objects.filter(pk=int(horse_id)).first()

    raw_info = request.POST.get('client_info', '')[:CLIENT_INFO_MAX_CHARS]
    try:
        client_info = json.loads(raw_info) if raw_info else {}
    except ValueError:
        client_info = {}
    if not isinstance(client_info, dict):
        client_info = {}

    entry = PrototypeEntry(
        client_id=client_id,
        user=request.user,
        horse=horse,
        horse_name=request.POST.get('horse_name', '')[:200],
        note=note,
        device_created_at=created,
        attempts=_attempts(request),
        client_info=client_info,
    )
    try:
        photo = request.FILES.get('photo')
        if photo is not None:
            data, extension = _read_photo(photo)
            entry.photo_bytes = len(data)
            entry.photo_intact = _intact(data, request.POST.get('photo_sha256'))
            entry.photo.save(f'photo.{extension}', ContentFile(data), save=False)
        audio = request.FILES.get('audio')
        if audio is not None:
            data, extension = _read_audio(audio)
            entry.audio_bytes = len(data)
            entry.audio_intact = _intact(data, request.POST.get('audio_sha256'))
            entry.audio.save(f'audio.{extension}', ContentFile(data), save=False)
    except EntryError as error:
        _discard_files(entry)
        return JsonResponse({'error': str(error)}, status=400)

    try:
        with transaction.atomic():
            entry.save()
    except IntegrityError:
        # The same note arrived twice at the same moment and the other
        # request won. Keep that one; drop this one's files.
        _discard_files(entry)
        existing = PrototypeEntry.objects.filter(client_id=client_id).first()
        if existing is None:
            raise
        return _duplicate_response(request, existing)
    return JsonResponse(_entry_payload(entry, 'created'), status=201)


def _duplicate_response(request, existing):
    if existing.user_id != request.user.pk:
        # Another login's note: say nothing about it.
        return JsonResponse({'error': 'client_id_in_use'}, status=409)
    # Keep the highest count of tries: it is one of the test's measures
    # (how often a reply is lost in a weak signal). Nothing else changes.
    attempts = _attempts(request)
    if attempts > existing.attempts:
        existing.attempts = attempts
        existing.save(update_fields=['attempts'])
    return JsonResponse(_entry_payload(existing, 'duplicate'))


def _attempts(request):
    value = request.POST.get('attempts', '1')
    return max(1, min(int(value), 9999)) if value.isdigit() else 1


def _discard_files(entry):
    for field in (entry.photo, entry.audio):
        if field:
            field.delete(save=False)
