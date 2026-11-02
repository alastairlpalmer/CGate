"""Two-step sign-in (multi-factor authentication).

A user adds an authenticator app (TOTP, the six-digit codes in Google
Authenticator, Microsoft Authenticator, 1Password and similar) and gets
ten single-use recovery codes for the day the phone is lost.

The storage and the code checks are django-otp's, including its throttle,
which doubles the wait after each wrong code. This module holds the
yard's rules on top:

* who must use it (``mfa_required``);
* setting it up, replacing a phone and turning it off;
* checking a code against the user's own devices, one at a time, as
  django-otp recommends (``verify``).

``core.middleware.MFAMiddleware`` sends a signed-in user to the code
page until they pass it, and an administrator without it to set-up.
"""

from __future__ import annotations

import io

from django.conf import settings
from django.db import transaction

RECOVERY_CODE_COUNT = 10
TOTP_NAME = 'Authenticator app'
RECOVERY_NAME = 'Recovery codes'


def _totp_model():
    from django_otp.plugins.otp_totp.models import TOTPDevice
    return TOTPDevice


def _static_model():
    from django_otp.plugins.otp_static.models import StaticDevice
    return StaticDevice


def has_mfa(user) -> bool:
    """True when the user has a confirmed authenticator app."""
    if not getattr(user, 'is_authenticated', False):
        return False
    return _totp_model().objects.filter(user=user, confirmed=True).exists()


SESSION_ENROLLED_KEY = '_mfa_enrolled'


def session_enrolled(request) -> bool:
    """Whether the signed-in user has two-step sign-in, as this session knows.

    Worked out once at sign-in and kept in the session, so a page view does
    not pay for a database query. It cannot go stale in the unsafe
    direction: turning two-step sign-in on signs out every other session
    (``confirm_setup`` callers), and turning it off for someone else signs
    them out too.
    """
    enrolled = request.session.get(SESSION_ENROLLED_KEY)
    if enrolled is None:
        enrolled = remember_enrolment(request)
    return enrolled


def remember_enrolment(request, user=None) -> bool:
    enrolled = has_mfa(user or request.user)
    request.session[SESSION_ENROLLED_KEY] = enrolled
    return enrolled


def is_administrator(user) -> bool:
    """A superuser, or a user with the seeded Administrator role."""
    if user.is_superuser:
        return True
    role = getattr(getattr(user, 'role_assignment', None), 'role', None)
    return bool(role and role.is_system)


def mfa_required(user) -> bool:
    """True when this user may not use the app without two-step sign-in.

    Administrators reach every table, every user account and the Django
    admin, so a guessed or reused password is the most damaging there.
    """
    if not getattr(settings, 'MFA_REQUIRED_FOR_ADMINS', True):
        return False
    return is_administrator(user)


def recovery_codes_left(user) -> int:
    from django_otp.plugins.otp_static.models import StaticToken
    return StaticToken.objects.filter(device__user=user, device__confirmed=True).count()


def mfa_status(user) -> dict:
    return {
        'enabled': has_mfa(user),
        'required': mfa_required(user),
        'recovery_codes_left': recovery_codes_left(user),
    }


def start_setup(user):
    """A fresh, unconfirmed authenticator device for ``user``.

    Any earlier set-up that was never confirmed is thrown away, so an
    abandoned QR code cannot be finished later by someone else. A device
    already in use stays in use until the new one is confirmed.
    """
    TOTPDevice = _totp_model()
    TOTPDevice.objects.filter(user=user, confirmed=False).delete()
    return TOTPDevice.objects.create(user=user, name=TOTP_NAME, confirmed=False)


def pending_device(user, device_id):
    if not device_id:
        return None
    return _totp_model().objects.filter(user=user, pk=device_id, confirmed=False).first()


def qr_code_svg(device) -> str:
    """The set-up QR code as inline SVG markup."""
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(device.config_url, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode('utf-8')


def secret_for_display(device) -> str:
    """The key in base32, in groups of four, for typing in by hand."""
    from base64 import b32encode

    key = b32encode(device.bin_key).decode('ascii').rstrip('=')
    return ' '.join(key[i:i + 4] for i in range(0, len(key), 4))


@transaction.atomic
def confirm_setup(user, device, token) -> list[str] | None:
    """Confirm ``device`` with a code from the app.

    On success the new device replaces any older one (a new phone) and a
    new set of recovery codes is made. Returns the codes, which are shown
    once, or ``None`` when the code was wrong.
    """
    if not device.verify_token(_clean(token)):
        return None
    device.confirmed = True
    device.save(update_fields=['confirmed'])
    _totp_model().objects.filter(user=user).exclude(pk=device.pk).delete()
    return new_recovery_codes(user)


@transaction.atomic
def new_recovery_codes(user) -> list[str]:
    """Replace the user's recovery codes. Old codes stop working."""
    from django_otp.plugins.otp_static.models import StaticToken

    StaticDevice = _static_model()
    StaticDevice.objects.filter(user=user).delete()
    device = StaticDevice.objects.create(user=user, name=RECOVERY_NAME, confirmed=True)
    codes = [StaticToken.random_token() for _ in range(RECOVERY_CODE_COUNT)]
    StaticToken.objects.bulk_create([StaticToken(device=device, token=c) for c in codes])
    return codes


@transaction.atomic
def disable(user) -> None:
    """Remove the authenticator and the recovery codes."""
    _totp_model().objects.filter(user=user).delete()
    _static_model().objects.filter(user=user).delete()


def verify(user, token):
    """The device that accepts ``token``, or ``None``.

    The authenticator is tried first, then the recovery codes. A recovery
    code works once: django-otp deletes it when it matches.
    """
    from django_otp import verify_token

    token = _clean(token)
    if not token:
        return None
    # Send each code only to the kind of device that could have made it.
    # Every wrong code adds to a device's throttle, so a mistyped app code
    # must not also slow down the recovery codes, or the other way round.
    # App codes are 6 digits; recovery codes are 8 characters.
    if token.isdigit() and len(token) <= 6:
        devices = _totp_model().objects.filter(user=user, confirmed=True)
    else:
        devices = _static_model().objects.filter(user=user, confirmed=True)
    for device in devices:
        matched = verify_token(user, device.persistent_id, token)
        if matched is not None:
            return matched
    return None


def _clean(token) -> str:
    """Codes are often typed with spaces ('123 456') or in capitals."""
    return ''.join(str(token or '').split()).lower()
