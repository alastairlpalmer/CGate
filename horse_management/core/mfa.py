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

A device can be remembered after a correct code ("Remember this device",
the last part of this module): the next sign-in on it then asks for the
password only, for ``MFA_REMEMBER_DAYS``.
"""

from __future__ import annotations

import io
import time

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


GOOGLE_BACKEND = 'core.auth_backends.GoogleBackend'


def passed_by_google(request) -> bool:
    """True when this session signed in with Google and that is enough.

    Google has checked the person, so the app does not ask for its own
    code as well (docs/GOOGLE_SIGN_IN_PLAN.md, decision D2). The app cannot
    see whether the Google account has 2-Step Verification, so this trusts
    each person to protect their Google account;
    ``GOOGLE_SIGN_IN_SKIPS_TWO_STEP=False`` restores the code.

    Read from the session, which only ``login()`` writes: a password
    sign-in by the same user still gives a code. Connecting a Google
    account from Account Security does not sign in again, so it does not
    count either.
    """
    if not getattr(settings, 'GOOGLE_SIGN_IN_SKIPS_TWO_STEP', True):
        return False
    from django.contrib.auth import BACKEND_SESSION_KEY

    session = getattr(request, 'session', None)
    backend = session.get(BACKEND_SESSION_KEY, '') if session is not None else ''
    return backend == GOOGLE_BACKEND


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
    return is_administrator(user) and not is_exempt(user)


def is_exempt(user) -> bool:
    """True when an Administrator has excused this user (``MFAExemption``)."""
    from .models import MFAExemption
    if not getattr(user, 'pk', None):
        return False
    return MFAExemption.objects.filter(user=user).exists()


def set_exempt(user, exempt: bool, granted_by=None) -> bool:
    """Add or remove the exemption. True when something changed."""
    from .models import MFAExemption
    if exempt:
        _, created = MFAExemption.objects.get_or_create(user=user, defaults={'granted_by': granted_by})
        return created
    deleted, _ = MFAExemption.objects.filter(user=user).delete()
    return bool(deleted)


def recovery_codes_left(user) -> int:
    from django_otp.plugins.otp_static.models import StaticToken
    return StaticToken.objects.filter(device__user=user, device__confirmed=True).count()


def mfa_status(user) -> dict:
    return {
        'enabled': has_mfa(user),
        'required': mfa_required(user),
        'exempt': is_exempt(user),
        'recovery_codes_left': recovery_codes_left(user),
        'remember_days': remember_days(),
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


# ── Remember this device ─────────────────────────────────────────────────────
#
# After a correct code the person can have the device remembered. The app
# then puts a signed cookie on it. At the next sign-in, with the right
# password, a valid cookie stands in for the code.
#
# The cookie holds the user's id, the time it was made and a fingerprint.
# It is signed with SECRET_KEY, so it cannot be made or changed outside the
# app. It stops working:
#
#   * after MFA_REMEMBER_DAYS (the signature is checked against its age,
#     so a cookie that a browser keeps too long is still refused);
#   * when the password changes, or the authenticator is replaced or
#     removed: both are part of the fingerprint;
#   * when the account is signed out everywhere: the profile then holds
#     a time, and a cookie made before it is refused. That is the answer
#     to a lost or stolen device.
#
# The cookie alone does nothing: the password is still needed. It is
# HttpOnly, so a script in a page cannot read it.

REMEMBER_COOKIE = 'two_step_remembered'
REMEMBER_SALT = 'core.mfa.remember'


def remember_days() -> int:
    """How long a device is remembered. 0 means the feature is off."""
    return max(0, int(getattr(settings, 'MFA_REMEMBER_DAYS', 14) or 0))


def _authenticator(user):
    return _totp_model().objects.filter(user=user, confirmed=True).first()


def _fingerprint(user, device) -> str:
    """Changes when the password or the authenticator changes."""
    from django.utils.crypto import salted_hmac

    return salted_hmac(
        REMEMBER_SALT, f'{user.pk}|{user.password}|{device.pk}|{device.key}', algorithm='sha256',
    ).hexdigest()


def remember_device(response, user) -> bool:
    """Put the cookie on ``response``. False when there is nothing to remember."""
    from django.core import signing

    days = remember_days()
    device = _authenticator(user)
    if not days or device is None:
        return False
    value = signing.dumps(
        {'u': user.pk, 't': time.time(), 'f': _fingerprint(user, device)}, salt=REMEMBER_SALT,
    )
    response.set_cookie(
        REMEMBER_COOKIE, value,
        max_age=days * 24 * 60 * 60,
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite='Lax',
    )
    return True


def forget_this_device(response) -> None:
    """Take the cookie off this device."""
    response.delete_cookie(REMEMBER_COOKIE, samesite='Lax')


def forget_remembered_devices(user) -> None:
    """Make every device ask for a code again at its next sign-in."""
    from django.utils import timezone

    from .models import UserProfile

    if not getattr(user, 'pk', None):
        return
    UserProfile.objects.update_or_create(user=user, defaults={'two_step_remembered_from': timezone.now()})


def remembered_authenticator(request):
    """The user's authenticator, when this device is remembered for them.

    ``None`` when there is no cookie, or it is for someone else, too old,
    changed, or made before the last "sign out everywhere".
    """
    from django.core import signing
    from django.utils.crypto import constant_time_compare

    from .models import UserProfile

    days = remember_days()
    value = request.COOKIES.get(REMEMBER_COOKIE)
    user = getattr(request, 'user', None)
    if not days or not value or user is None or not user.is_authenticated:
        return None
    try:
        data = signing.loads(value, salt=REMEMBER_SALT, max_age=days * 24 * 60 * 60)
        made = float(data['t'])
        fingerprint = str(data['f'])
        if int(data['u']) != user.pk:
            return None
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None
    device = _authenticator(user)
    if device is None or not constant_time_compare(fingerprint, _fingerprint(user, device)):
        return None
    forgotten = (
        UserProfile.objects.filter(user=user).values_list('two_step_remembered_from', flat=True).first()
    )
    if forgotten is not None and made <= forgotten.timestamp():
        return None
    return device


def pass_remembered_device(request) -> bool:
    """Let this session through without a code when the device is remembered.

    Called for a session that has signed in with a password and has not
    given a code yet. Marks the session as verified, exactly as a correct
    code does, and writes the fact to the account events.
    """
    device = remembered_authenticator(request)
    if device is None:
        return False
    from django_otp import login as otp_login

    from . import audit
    from .models import AuditEvent

    otp_login(request, device)
    audit.record(AuditEvent.Action.MFA_CODE_SKIPPED, request=request, target=request.user)
    return True
