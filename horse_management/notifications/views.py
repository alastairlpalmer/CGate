"""Public endpoints for email: the unsubscribe link in reminder emails,
and the provider webhooks that report bounces and spam complaints.

None of these needs a signed-in user. The unsubscribe link carries a
signed owner id; the webhooks prove where they came from with a secret
token (Postmark) or a signature (Resend).
"""

import base64
import hashlib
import hmac
import json
import logging
import time

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .mail import owner_from_token

logger = logging.getLogger(__name__)


@csrf_exempt  # RFC 8058: mail clients POST the one-click unsubscribe with no CSRF token.
def unsubscribe(request, token):
    owner = owner_from_token(token)
    if owner is None:
        raise Http404
    done = False
    if request.method == 'POST':
        if owner.email_health_reminders:
            owner.email_health_reminders = False
            owner.save(update_fields=['email_health_reminders'])
            logger.info('Owner %s turned off health reminder emails.', owner.pk)
        done = True
    return render(request, 'notifications/unsubscribe.html', {'done': done})


# ── Bounces and complaints ───────────────────────────────────────────────────

POSTMARK_HARD_TYPES = {'HardBounce', 'BadEmailAddress', 'ManuallyDeactivated', 'SpamNotification'}


def mark_bounced(address: str, reason: str) -> int:
    """Stop emailing ``address``. Returns how many owners use it."""
    from core.models import Owner

    if not address:
        return 0
    count = Owner.objects.filter(email__iexact=address.strip()).update(
        email_bounced_at=timezone.now(), email_bounce_reason=reason[:255],
    )
    logger.warning('Email to an owner address bounced or was reported (%s); %d owner(s) flagged.', reason, count)
    return count


@csrf_exempt
@require_POST
def postmark_events(request, token):
    expected = settings.EMAIL_WEBHOOK_TOKEN
    if not expected or not hmac.compare_digest(token, expected):
        return HttpResponseForbidden()
    try:
        event = json.loads(request.body)
    except ValueError:
        return HttpResponseBadRequest()
    record = event.get('RecordType')
    if record == 'SpamComplaint':
        mark_bounced(event.get('Email', ''), 'Marked as spam')
    elif record == 'Bounce' and event.get('Type') in POSTMARK_HARD_TYPES:
        mark_bounced(event.get('Email', ''), event.get('Description') or event.get('Type', 'Bounced'))
    return HttpResponse(status=204)


def _svix_valid(request, secret: str, tolerance=300) -> bool:
    """Check a Svix (Resend) signature: HMAC-SHA256 over id.timestamp.body."""
    msg_id = request.headers.get('svix-id', '')
    stamp = request.headers.get('svix-timestamp', '')
    signatures = request.headers.get('svix-signature', '')
    if not (msg_id and stamp and signatures):
        return False
    try:
        if abs(time.time() - int(stamp)) > tolerance:
            return False
        key = base64.b64decode(secret.split('_', 1)[1] if secret.startswith('whsec_') else secret)
    except (ValueError, IndexError):
        return False
    signed = f'{msg_id}.{stamp}.'.encode() + request.body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in signatures.split():
        version, _, value = part.partition(',')
        if version == 'v1' and hmac.compare_digest(value, expected):
            return True
    return False


@csrf_exempt
@require_POST
def resend_events(request):
    secret = settings.RESEND_WEBHOOK_SECRET
    if not secret or not _svix_valid(request, secret):
        return HttpResponseForbidden()
    try:
        event = json.loads(request.body)
    except ValueError:
        return HttpResponseBadRequest()
    data = event.get('data') or {}
    recipients = data.get('to') or []
    if isinstance(recipients, str):
        recipients = [recipients]
    kind = event.get('type')
    if kind == 'email.complained':
        for address in recipients:
            mark_bounced(address, 'Marked as spam')
    elif kind == 'email.bounced':
        bounce = data.get('bounce') or {}
        if (bounce.get('type') or 'Permanent').lower() != 'transient':
            for address in recipients:
                mark_bounced(address, bounce.get('message') or 'Bounced')
    return HttpResponse(status=204)
