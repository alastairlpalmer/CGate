"""/.well-known/security.txt (RFC 9116): where to report a security problem."""

from datetime import UTC, timedelta

from django.conf import settings
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET


@require_GET
def security_txt(request):
    contact = settings.SECURITY_CONTACT
    if not contact:
        raise Http404  # Nobody named yet; better no file than a dead address.
    if '@' in contact and not contact.startswith(('mailto:', 'https:')):
        contact = f'mailto:{contact}'
    # RFC 9116 requires an expiry date. A rolling year keeps it valid
    # without anyone having to remember to renew it.
    expires = (timezone.now() + timedelta(days=365)).replace(microsecond=0)
    lines = [
        f'Contact: {contact}',
        f"Expires: {expires.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        'Preferred-Languages: en',
    ]
    if settings.SITE_URL:
        lines.append(f"Canonical: {settings.SITE_URL.rstrip('/')}/.well-known/security.txt")
    return HttpResponse('\n'.join(lines) + '\n', content_type='text/plain; charset=utf-8')
