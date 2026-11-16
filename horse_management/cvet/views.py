"""C-vet: the page and its API (docs/C_VET_PLAN.md, sections 4 and 5).

One page, one service worker and a small JSON API:

* ``app`` is the page. It carries no data, so the service worker can keep
  a copy and open it with no signal.
* ``service_worker`` is the script that keeps that copy.
* ``api_session`` says who is signed in and gives a CSRF token. The page
  cannot read the token from the cookie (``CSRF_COOKIE_HTTPONLY``), and a
  token printed in a stored page would go stale.
* ``api_yard`` is the horse list the phone stores before a visit.
* ``api_notes`` takes one clinical note, with its photo and recording,
  and stores it once however many times it is sent. A GET lists the
  practice's notes for one horse.
* ``api_file`` serves a note's photo or recording to its practice.
* ``api_prices`` saves one line of the practice's price list.
* ``api_visit_finish`` closes a visit, when the vet taps Finish visit, and
  bills it (``cvet.billing``).
* ``bill_list``, ``bill_detail`` and ``bill_export`` are the practice's own
  pages for its bills. They need a signal and are not part of the stored app.

Everything is a 404 unless ``CVET_ENABLED`` is set. The gate and the scope
are in ``cvet.access``: every view below gets ``request.practice`` from
the signed-in login and reads clinical records only through it.

Sync rules kept here (section 5 of the plan):

* the phone only adds: no endpoint edits a yard record;
* each note and each visit has an id made on the phone;
* a note is accepted when the horse has gone, or the link to the yard
  has ended since the phone downloaded the list. Nothing a vet saved at
  the yard is refused for a reason the vet could not see there.
"""

import hashlib
import json
import uuid
from datetime import timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core import ratelimit
from core.csv_export import csv_response
from core.models import prime_current_owners
from core.templatetags.cache_bust import _VERSION, static_v

from . import access, billing, yard
from .access import cvet_enabled, practice_api, practice_page
from .models import ClinicalFile, ClinicalNote, NoteLine, PriceItem, SyncReceipt, Visit

NOTE_MAX_CHARS = 5000
PHOTO_MAX_BYTES = 8 * 1024 * 1024
AUDIO_MAX_BYTES = 8 * 1024 * 1024
CLIENT_INFO_MAX_CHARS = 4000
# Notes one login may send in an hour. A visit is tens of notes; this
# only stops a faulty phone that sends in a loop.
NOTES_PER_HOUR = 300
NOTES_LISTED = 50
LINES_PER_NOTE = 50
PRICE_MAX = Decimal('99999.99')
QUANTITY_MAX = Decimal('999')
VAT_RATE_MAX = Decimal('99.99')

# What Pillow says the photo is -> (extension, content type).
PHOTO_FORMATS = {
    'JPEG': ('jpg', 'image/jpeg'),
    'PNG': ('png', 'image/png'),
    'WEBP': ('webp', 'image/webp'),
    'HEIF': ('heic', 'image/heic'),
}
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
    'cvet/app.css',
    'cvet/app_core.js',
    'cvet/app.js',
    'cvet/manifest.json',
    'icons/icon-192.png',
    'icons/apple-touch-icon.png',
)


def _user_name(user):
    return user.get_full_name() or user.get_username()


def _session_payload(request):
    return {
        'user': {'id': request.user.pk, 'name': _user_name(request.user)},
        'practice': request.practice.name,
        'test_mode': settings.CVET_TEST_MODE,
    }


# ── The page ─────────────────────────────────────────────────────────────

@practice_page
def app(request):
    """The page. No data in it: the phone gets that from the API."""
    response = render(request, 'cvet/app.html', {
        'config': {
            'version': _VERSION,
            'sw_url': reverse('cvet:service_worker'),
            'scope': reverse('cvet:app'),
            'session_url': reverse('cvet:api_session'),
            'yard_url': reverse('cvet:api_yard'),
            'notes_url': reverse('cvet:api_notes'),
            'prices_url': reverse('cvet:api_prices'),
            # The phone adds "<visit id>/finish/".
            'visits_url': f"{reverse('cvet:app')}api/visits/",
            'bills_url': reverse('cvet:bill_list'),
            'login_url': f"{reverse('login')}?next={reverse('cvet:app')}",
            'logout_url': reverse('logout'),
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


# ── What the phone downloads ─────────────────────────────────────────────

@practice_api
@require_GET
def api_session(request):
    return JsonResponse(dict(_session_payload(request), csrf=get_token(request)))


@practice_api
@require_GET
def api_yard(request):
    """The yard's horses, with what the vet needs to pick the right one.

    Identity, owner and field name are shared data (section 3 of the
    plan), but only while the practice's link to the yard is open.
    """
    if access.open_link(request.practice) is None:
        return JsonResponse({'error': 'no_yard_link'}, status=403)
    horses = list(access.horses_for(request.practice).order_by('name'))
    prime_current_owners(horses)
    locations = yard.locations_by_horse(horses)
    payers = yard.payers_by_horse(horses)
    due = yard.due_by_horse(request.practice, horses)
    link = access.open_link(request.practice)
    return JsonResponse(dict(
        _session_payload(request),
        generated_at=timezone.now().isoformat(),
        # The practice's client: the yard the vet is at, and who the
        # bill for a visit goes to.
        yard={'name': yard.yard_name(), 'bill_to': link.bill_to},
        horses=[
            {
                'id': horse.pk,
                'name': horse.name,
                'microchip': horse.microchip,
                'passport': horse.passport_number,
                'location': locations.get(horse.pk, ''),
                'owner': horse.current_owner.name if horse.current_owner else '',
                # Who pays and each share: the phone shows the split on
                # the visit summary. No owner id and no contact details.
                'payers': [
                    {'name': payer['name'], 'share': payer['share']}
                    for payer in payers.get(horse.pk, [])
                ],
                'due': due.get(horse.pk, []),
            }
            for horse in horses
        ],
        prices=[_price_payload(item) for item in access.prices_for(request.practice)],
        # The yard's own list of vaccinations. A price can be marked as
        # one of them; giving it then records the dose at the yard.
        vaccination_types=yard.vaccination_types() if link.sends_vaccination_dates else [],
    ))


# ── The price list ───────────────────────────────────────────────────────

def _price_payload(item):
    return {
        'id': str(item.client_id),
        'name': item.name,
        'price': f'{item.price_inc_vat:.2f}',
        'vat_rate': f'{item.vat_rate:.2f}',
        'is_active': item.is_active,
        'vaccination_type_id': item.vaccination_type_id,
    }


def _decimal(value, maximum, minimum=Decimal('0')):
    """A plain amount with at most 2 decimal places, or None."""
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < minimum or number > maximum:
        return None
    if number != number.quantize(Decimal('0.01')):
        return None
    return number


@practice_api
@require_POST
def api_prices(request):
    """Save one price: a new one, or a change to one of the practice's own.

    The phone makes the id, so a save that is sent twice is one price.
    """
    client_id = _uuid(request.POST.get('id'))
    if client_id is None:
        return JsonResponse({'error': 'id_invalid'}, status=400)
    name = request.POST.get('name', '').strip()
    if not name or len(name) > 100:
        return JsonResponse({'error': 'name_invalid'}, status=400)
    price = _decimal(request.POST.get('price', ''), PRICE_MAX)
    if price is None:
        return JsonResponse({'error': 'price_invalid'}, status=400)
    vat_rate = _decimal(request.POST.get('vat_rate', '20'), VAT_RATE_MAX)
    if vat_rate is None:
        return JsonResponse({'error': 'vat_rate_invalid'}, status=400)
    is_active = request.POST.get('is_active', 'true') != 'false'
    vaccination_type_id = None
    if request.POST.get('vaccination_type_id'):
        known = {item['id'] for item in yard.vaccination_types()}
        raw = request.POST['vaccination_type_id']
        if not raw.isdigit() or int(raw) not in known:
            return JsonResponse({'error': 'vaccination_type_invalid'}, status=400)
        vaccination_type_id = int(raw)

    existing = PriceItem.objects.filter(client_id=client_id).first()
    if existing is not None and existing.practice_id != request.practice.pk:
        return JsonResponse({'error': 'id_in_use'}, status=409)
    fields = {
        'name': name, 'price_inc_vat': price, 'vat_rate': vat_rate, 'is_active': is_active,
        'vaccination_type_id': vaccination_type_id,
    }
    if existing is None:
        try:
            with transaction.atomic():
                item = PriceItem.objects.create(practice=request.practice, client_id=client_id, **fields)
        except IntegrityError:
            return JsonResponse({'error': 'id_in_use'}, status=409)
        return JsonResponse(_price_payload(item), status=201)
    for field, value in fields.items():
        setattr(existing, field, value)
    existing.save()
    return JsonResponse(_price_payload(existing))


# ── Clinical notes ───────────────────────────────────────────────────────

class NoteError(Exception):
    """The phone sent something this endpoint will not store."""


def _read_photo(upload):
    """(bytes, extension, content type), or NoteError if it is not a photo."""
    from PIL import Image, UnidentifiedImageError

    if upload.size > PHOTO_MAX_BYTES:
        raise NoteError('photo_too_large')
    data = upload.read()
    try:
        with Image.open(BytesIO(data)) as image:
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise NoteError('photo_not_an_image')
    if image_format not in PHOTO_FORMATS:
        raise NoteError('photo_type_not_allowed')
    return (data, *PHOTO_FORMATS[image_format])


def _read_audio(upload):
    """(bytes, extension, content type) of a recording.

    The content cannot be checked the way a photo can. The type must be
    on the list, the stored name is ours, and the file is only ever sent
    back as a download, to the practice (``api_file``).
    """
    if upload.size > AUDIO_MAX_BYTES:
        raise NoteError('audio_too_large')
    # "audio/webm;codecs=opus" -> "audio/webm"
    content_type = (upload.content_type or '').split(';')[0].strip().lower()
    if content_type not in AUDIO_TYPES:
        raise NoteError('audio_type_not_allowed')
    return upload.read(), AUDIO_TYPES[content_type], content_type


def _intact(data, claimed_sha256):
    """True or False when the phone sent a hash to check against, else None."""
    claimed = (claimed_sha256 or '').strip().lower()
    if not claimed:
        return None
    return hashlib.sha256(data).hexdigest() == claimed


def _uuid(value):
    try:
        return uuid.UUID(value or '')
    except (ValueError, AttributeError, TypeError):
        return None


def _attempts(request):
    value = request.POST.get('attempts', '1')
    return max(1, min(int(value), 9999)) if value.isdigit() else 1


def _file_payload(file):
    return {
        'id': str(file.client_id),
        'kind': file.kind,
        'bytes': file.bytes,
        'url': reverse('cvet:api_file', args=[file.client_id]),
    }


def _note_payload(note):
    return {
        'id': str(note.client_id),
        'horse_id': note.horse_id,
        'horse_name': note.horse_name,
        'seen_on': note.seen_on.isoformat(),
        'written_at': note.written_at.isoformat(),
        'author': note.author_name,
        'findings': note.findings,
        'treatment': note.treatment,
        'follow_up_date': note.follow_up_date.isoformat() if note.follow_up_date else None,
        'is_test': note.is_test,
        'files': [_file_payload(file) for file in note.files.all()],
        'lines': [
            {
                'id': str(line.client_id),
                'description': line.description,
                'quantity': f'{line.quantity.normalize():f}',
                'unit_price': f'{line.unit_price_inc_vat:.2f}',
                'vat_rate': f'{line.vat_rate:.2f}',
                'total': f'{line.total_inc_vat:.2f}',
            }
            for line in note.lines.all()
        ],
    }


def _receipt_payload(note, status):
    receipt = getattr(note, 'receipt', None)
    return {
        'status': status,
        'id': str(note.client_id),
        'received_at': note.created_at.isoformat(),
        'photo_intact': receipt.photo_intact if receipt else None,
        'audio_intact': receipt.audio_intact if receipt else None,
    }


@practice_api
@require_http_methods(['GET', 'POST'])
def api_notes(request):
    if request.method == 'GET':
        return _list_notes(request)
    return _store_note(request)


def _list_notes(request):
    """The practice's notes, newest first. ``?horse=<id>`` for one horse."""
    notes = access.current_notes_for(request.practice).prefetch_related('files', 'lines')
    horse_id = request.GET.get('horse', '')
    if horse_id:
        if not horse_id.isdigit():
            return JsonResponse({'error': 'horse_invalid'}, status=400)
        notes = notes.filter(horse_id=int(horse_id))
    return JsonResponse({'notes': [_note_payload(note) for note in notes[:NOTES_LISTED]]})


def _duplicate_response(request, client_id):
    """The reply for a note the server already holds, or None.

    Looked up through the whole table, not through the practice: the id
    must be refused, without a word about the note, when it belongs to
    another practice.
    """
    existing = ClinicalNote.objects.select_related('receipt').filter(client_id=client_id).first()
    if existing is None:
        return None
    if existing.practice_id != request.practice.pk:
        return JsonResponse({'error': 'client_id_in_use'}, status=409)
    # Keep the highest count of tries: it measures how often a reply is
    # lost in a weak signal. Nothing else changes on a repeat.
    receipt = getattr(existing, 'receipt', None)
    attempts = _attempts(request)
    if receipt is not None and attempts > receipt.attempts:
        receipt.attempts = attempts
        receipt.save(update_fields=['attempts'])
    return JsonResponse(_receipt_payload(existing, 'duplicate'))


def _visit_for(request, visit_id, seen_on):
    """The visit this note belongs to, made on its first note."""
    if visit_id is None:
        return None
    visit = Visit.objects.filter(client_id=visit_id).first()
    if visit is not None:
        # Another practice's visit id: store the note without a visit
        # rather than refuse it or join it to their visit.
        return visit if visit.practice_id == request.practice.pk else None
    link = access.open_link(request.practice)
    try:
        with transaction.atomic():
            return Visit.objects.create(
                practice=request.practice, client_id=visit_id, author=request.user,
                author_name=_user_name(request.user), date=seen_on,
                yard_name=yard.yard_name() if link else '',
                bill_to=link.bill_to if link else Visit._meta.get_field('bill_to').default,
                is_test=settings.CVET_TEST_MODE,
            )
    except IntegrityError:
        # Two notes of a new visit arrived at the same moment.
        return access.visits_for(request.practice).filter(client_id=visit_id).first()


def _read_lines(request):
    """The items done, from the phone's JSON. Raises NoteError.

    Each line carries its own description, price and VAT rate: the phone
    copied them from the price list when the vet tapped the item, maybe
    with no signal, and maybe changed the price for this horse.
    """
    raw = request.POST.get('lines', '')
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        raise NoteError('lines_invalid')
    if not isinstance(items, list) or len(items) > LINES_PER_NOTE:
        raise NoteError('lines_invalid')
    own_prices = {
        str(item.client_id): item for item in access.prices_for(request.practice)
    }
    lines = []
    for item in items:
        if not isinstance(item, dict):
            raise NoteError('lines_invalid')
        client_id = _uuid(item.get('id'))
        description = str(item.get('description', '')).strip()
        quantity = _decimal(item.get('quantity', '1'), QUANTITY_MAX, minimum=Decimal('0.01'))
        unit_price = _decimal(item.get('unit_price', ''), PRICE_MAX)
        vat_rate = _decimal(item.get('vat_rate', '20'), VAT_RATE_MAX)
        if client_id is None or not description or len(description) > 200:
            raise NoteError('line_invalid')
        if quantity is None or unit_price is None or vat_rate is None:
            raise NoteError('line_invalid')
        lines.append({
            'client_id': client_id,
            # Only one of this practice's own prices is linked. An id
            # that is not ours leaves the line whole, with no link.
            'price_item': own_prices.get(str(item.get('price_id', ''))),
            'description': description,
            'quantity': quantity,
            'unit_price_inc_vat': unit_price,
            'vat_rate': vat_rate,
        })
    return lines


def _store_note(request):
    """Store one note. Safe to call again with the same ``client_id``."""
    client_id = _uuid(request.POST.get('client_id'))
    if client_id is None:
        return JsonResponse({'error': 'client_id_invalid'}, status=400)

    # Checked before anything else, so a repeat is cheap and is answered
    # even when the first attempt used up the hourly limit.
    duplicate = _duplicate_response(request, client_id)
    if duplicate is not None:
        return duplicate

    if not ratelimit.allow('cvet-notes', str(request.user.pk), NOTES_PER_HOUR, 3600):
        return JsonResponse({'error': 'too_many'}, status=429)

    written_at = parse_datetime(request.POST.get('device_created_at', ''))
    if written_at is None:
        return JsonResponse({'error': 'device_created_at_invalid'}, status=400)
    if timezone.is_naive(written_at):
        written_at = timezone.make_aware(written_at, dt_timezone.utc)
    seen_on = timezone.localtime(written_at).date()

    findings = request.POST.get('note', '')
    if len(findings) > NOTE_MAX_CHARS:
        return JsonResponse({'error': 'note_too_long'}, status=400)

    follow_up_date = None
    if request.POST.get('follow_up_date'):
        follow_up_date = parse_date(request.POST['follow_up_date'])
        if follow_up_date is None:
            return JsonResponse({'error': 'follow_up_date_invalid'}, status=400)

    # The horse, if the practice may still see it. If not (it left, or
    # the link ended after the download), the note is kept under the name
    # the phone had: the vet did see that horse.
    horse = None
    horse_id = request.POST.get('horse_id', '')
    if horse_id.isdigit():
        horse = access.horses_for(request.practice).filter(pk=int(horse_id)).first()
    horse_name = (horse.name if horse else request.POST.get('horse_name', ''))[:200]
    if not horse_name:
        return JsonResponse({'error': 'horse_missing'}, status=400)
    # Who pays, decided here and now from the yard's records, not from
    # what the phone downloaded before the visit (sync rule 4).
    payers = yard.payers_by_horse([horse]).get(horse.pk, []) if horse else []

    raw_info = request.POST.get('client_info', '')[:CLIENT_INFO_MAX_CHARS]
    try:
        client_info = json.loads(raw_info) if raw_info else {}
    except ValueError:
        client_info = {}
    if not isinstance(client_info, dict):
        client_info = {}

    # Read and check both files before anything is stored.
    files = []
    receipt_fields = {}
    try:
        lines = _read_lines(request)
        for kind, reader in ((ClinicalFile.Kind.PHOTO, _read_photo), (ClinicalFile.Kind.AUDIO, _read_audio)):
            upload = request.FILES.get(kind)
            if upload is None:
                continue
            data, extension, content_type = reader(upload)
            file_id = _uuid(request.POST.get(f'{kind}_id')) or uuid.uuid4()
            files.append((kind, file_id, data, extension, content_type))
            receipt_fields[f'{kind}_bytes'] = len(data)
            receipt_fields[f'{kind}_intact'] = _intact(data, request.POST.get(f'{kind}_sha256'))
    except NoteError as error:
        return JsonResponse({'error': str(error)}, status=400)

    if not findings.strip() and not files and not lines:
        return JsonResponse({'error': 'note_empty'}, status=400)

    visit = _visit_for(request, _uuid(request.POST.get('visit_id')), seen_on)
    is_test = settings.CVET_TEST_MODE
    stored = []
    try:
        with transaction.atomic():
            note = ClinicalNote.objects.create(
                practice=request.practice, visit=visit, client_id=client_id,
                horse=horse, horse_name=horse_name,
                horse_microchip=horse.microchip if horse else '',
                horse_passport=horse.passport_number if horse else '',
                author=request.user, author_name=_user_name(request.user),
                seen_on=seen_on, written_at=written_at, findings=findings,
                follow_up_date=follow_up_date, payers=payers, is_test=is_test,
            )
            NoteLine.objects.bulk_create([
                NoteLine(practice=request.practice, note=note, **line) for line in lines
            ])
            for kind, file_id, data, extension, content_type in files:
                record = ClinicalFile(
                    practice=request.practice, note=note, client_id=file_id, kind=kind,
                    content_type=content_type, bytes=len(data),
                )
                record.file.save(f'file.{extension}', ContentFile(data), save=False)
                stored.append(record.file)
                record.save()
            SyncReceipt.objects.create(
                practice=request.practice, user=request.user, note=note,
                note_client_id=client_id, is_test=is_test,
                device_created_at=written_at, attempts=_attempts(request),
                text_length=len(findings), client_info=client_info, **receipt_fields,
            )
    except IntegrityError:
        # The same note arrived twice at the same moment and the other
        # request won, or a file id or a line id is already in use. The rows of this
        # attempt are rolled back; take its files out of storage too.
        for file in stored:
            file.delete(save=False)
        duplicate = _duplicate_response(request, client_id)
        if duplicate is not None:
            return duplicate
        return JsonResponse({'error': 'id_in_use'}, status=409)
    if visit is not None and visit.status == Visit.Status.FINISHED:
        # A note that arrived after Finish visit: bill it and send the
        # yard its part now, on a further bill.
        billing.close_out(visit)
    return JsonResponse(_receipt_payload(note, 'created'), status=201)


@practice_api
@require_GET
def api_file(request, client_id):
    """A note's photo or recording, for a member of its practice.

    The same 404 for a file that does not exist and for another
    practice's file. Always a download with a type from our own list, so
    an uploaded file is never shown as a page of this site.
    """
    record = access.files_for(request.practice).filter(client_id=client_id).first()
    if record is None:
        raise Http404
    try:
        handle = record.file.open('rb')
    except (FileNotFoundError, OSError):
        raise Http404
    extension = record.file.name.rsplit('.', 1)[-1]
    response = FileResponse(
        handle, content_type=record.content_type, as_attachment=True,
        filename=f'{record.kind}-{record.client_id}.{extension}',
    )
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'private, no-store'
    return response


# ── Visits ───────────────────────────────────────────────────────────────

def _visit_payload(visit):
    lines = access.lines_for(visit.practice).filter(note__visit=visit)
    total = sum((line.total_inc_vat for line in lines), Decimal('0.00'))
    return {
        'status': visit.status,
        'id': str(visit.client_id),
        'date': visit.date.isoformat(),
        'yard': visit.yard_name,
        'bill_to': visit.bill_to,
        # 'month': no bill now; this visit goes on the monthly bill.
        'bill_when': getattr(access.open_link(visit.practice), 'bill_when', 'visit'),
        'finished_at': visit.finished_at.isoformat() if visit.finished_at else None,
        'notes': visit.notes.count(),
        'horses': visit.notes.values('horse_name').distinct().count(),
        'total': f'{total:.2f}',
        'bills': [
            {'number': bill.number, 'to': bill.to_name, 'total': f'{bill.total_inc_vat:.2f}'}
            for bill in visit.bills.order_by('pk')
        ],
    }


@practice_api
@require_POST
def api_visit_finish(request, client_id):
    """Close a visit. Safe to call again: the first time sets the time.

    The phone sends this after every note of the visit has arrived. A
    note that arrives later still joins the visit: nothing is refused
    because the vet tapped Finish first.
    """
    visit = access.visits_for(request.practice).filter(client_id=client_id).first()
    if visit is None:
        raise Http404
    if visit.status != Visit.Status.FINISHED:
        finished_at = parse_datetime(request.POST.get('finished_at', '')) or timezone.now()
        if timezone.is_naive(finished_at):
            finished_at = timezone.make_aware(finished_at, dt_timezone.utc)
        visit.status = Visit.Status.FINISHED
        visit.finished_at = finished_at
        visit.save(update_fields=['status', 'finished_at'])
    # Every call, not only the first: if the first one made the bill and
    # then failed, this one finishes the job. It makes nothing twice.
    billing.close_out(visit)
    return JsonResponse(_visit_payload(visit))


# ── The practice's bills (pages, with a signal) ──────────────────────────

@practice_page
@require_GET
def bill_list(request):
    is_test = settings.CVET_TEST_MODE
    bills = access.bills_for(request.practice).filter(is_test=is_test)
    visits, total = billing.unbilled_summary(request.practice, is_test)
    return render(request, 'cvet/bill_list.html', {
        'practice': request.practice,
        'bills': bills.select_related('visit')[:200],
        'test_mode': is_test,
        'link': access.open_link(request.practice),
        'unbilled_visits': visits,
        'unbilled_total': total,
    })


@practice_page
@require_POST
def bill_settings(request):
    """The practice chooses when it bills this yard: each visit, or monthly."""
    link = access.open_link(request.practice)
    choice = request.POST.get('bill_when', '')
    if link is not None and choice in billing.PracticeYardLink.BillWhen.values:
        link.bill_when = choice
        link.save(update_fields=['bill_when'])
    return redirect('cvet:bill_list')


@practice_page
@require_POST
def bill_now(request):
    """Bill everything that is finished and not on a bill yet."""
    billing.bill_now(request.practice)
    return redirect('cvet:bill_list')


def _bill_or_404(request, pk):
    bill = access.bills_for(request.practice).select_related('practice', 'visit').filter(pk=pk).first()
    if bill is None:
        raise Http404
    return bill


@practice_page
@require_GET
def bill_detail(request, pk):
    """One bill, as a page to print or to save as a PDF."""
    return render(request, 'cvet/bill_detail.html', billing.bill_context(_bill_or_404(request, pk)))


@practice_page
@require_GET
def bill_export(request):
    """Every bill line of the practice as a CSV file, for its accounts."""
    lines = (
        billing.BillLine.objects
        .filter(bill__practice=request.practice, bill__is_test=settings.CVET_TEST_MODE)
        .select_related('bill').order_by('bill__issued_at', 'bill_id', 'pk')
    )
    rows = []
    for line in lines:
        vat = billing.vat_in(line.amount_inc_vat, line.vat_rate)
        rows.append([
            line.bill.number, line.bill.date, line.bill.to_name, line.date, line.horse_name, line.description,
            line.quantity, line.unit_price_inc_vat, line.vat_rate, line.share,
            line.amount_inc_vat - vat, vat, line.amount_inc_vat,
        ])
    return csv_response(
        'c-vet-bills',
        ['Bill', 'Bill date', 'Bill to', 'Visit date', 'Horse', 'Item', 'Quantity', 'Unit price inc VAT',
         'VAT rate %', 'Share %', 'Net', 'VAT', 'Total inc VAT'],
        rows,
    )
