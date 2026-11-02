"""Building and sending the app's emails.

Every email goes out as HTML with a plain-text version next to it. Spam
filters count an HTML-only message against the sender, and some people
read mail as text.

Reminder emails that people can choose not to receive also carry
``List-Unsubscribe`` headers (RFC 2369 and RFC 8058), so Gmail and Apple
Mail show their own one-click unsubscribe button.
"""

from __future__ import annotations

import html
import re

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.signing import BadSignature, Signer
from django.urls import reverse

# Returned by a send function that chose not to send (the owner turned the
# email off, or their address bounces). Truthy, so a task that claimed the
# reminder keeps the claim: the reminder is dealt with, not failed.
SKIPPED = 'skipped'

_BLOCK_END = re.compile(r'</(p|div|h[1-6]|li|tr|table|ul|ol)\s*>', re.I)
_BREAK = re.compile(r'<br\s*/?>', re.I)
_CELL_END = re.compile(r'</t[dh]\s*>', re.I)
_DROP = re.compile(r'<(style|script|head)[^>]*>.*?</\1\s*>', re.I | re.S)
_LINK = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a\s*>', re.I | re.S)
_TAG = re.compile(r'<[^>]+>')


def html_to_text(markup: str) -> str:
    """A readable plain-text version of one of our HTML emails."""
    text = _DROP.sub('', markup)
    text = _LINK.sub(lambda m: f'{m.group(2)} ({m.group(1)})' if m.group(1) not in m.group(2) else m.group(2), text)
    text = _BREAK.sub('\n', text)
    text = _CELL_END.sub('  ', text)
    text = _BLOCK_END.sub('\n\n', text)
    text = html.unescape(_TAG.sub('', text))
    lines = [' '.join(line.split()) for line in text.splitlines()]
    text = '\n'.join(lines)
    return re.sub(r'\n{3,}', '\n\n', text).strip() + '\n'


def message(subject, html_body, to, *, unsubscribe_url=None) -> EmailMultiAlternatives:
    """An email with HTML and plain-text parts, ready for attachments."""
    msg = EmailMultiAlternatives(
        subject=subject,
        body=html_to_text(html_body),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=to,
    )
    msg.attach_alternative(html_body, 'text/html')
    if unsubscribe_url:
        msg.extra_headers['List-Unsubscribe'] = f'<{unsubscribe_url}>'
        msg.extra_headers['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
    return msg


def can_email(owner) -> bool:
    """An address to send to that is not known to bounce."""
    return bool(owner and owner.email and not owner.email_bounced_at)


# ── Links ────────────────────────────────────────────────────────────────────

def absolute_url(path: str) -> str | None:
    """``path`` on the public site, or None when the site address is unknown.

    Emails are sent by scheduled tasks with no request to take the host
    from, so it comes from SITE_URL, or else the first real ALLOWED_HOSTS
    entry.
    """
    base = (settings.SITE_URL or '').rstrip('/')
    if not base:
        hosts = [h for h in settings.ALLOWED_HOSTS if h and '*' not in h and not h.startswith('.')
                 and h not in ('localhost', '127.0.0.1', 'healthcheck.railway.app')]
        if not hosts:
            return None
        base = f'https://{hosts[0]}'
    return base + path


_signer = Signer(salt='notifications.unsubscribe')


def unsubscribe_token(owner) -> str:
    return _signer.sign(str(owner.pk))


def owner_from_token(token: str):
    from core.models import Owner

    try:
        pk = _signer.unsign(token)
    except BadSignature:
        return None
    return Owner.objects.filter(pk=pk).first()


def health_unsubscribe_url(owner) -> str | None:
    return absolute_url(reverse('email_unsubscribe', args=[unsubscribe_token(owner)]))
