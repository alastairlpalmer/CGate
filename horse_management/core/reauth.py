"""Confirm it is you, before a change that takes over an account.

Pre-launch review: a signed-in session (a laptop left open, a stolen
session cookie) could replace the authenticator app, change the email
address or link a Google account, with no password. Each of those hands
the account to someone else.

Those actions now need a recent confirmation: the password, or a
two-step code for a login with no password. A sign-in counts as one.
It lasts REAUTH_SECONDS.
"""

import time

from django.conf import settings

REAUTH_KEY = '_reauth_at'


def _window():
    return getattr(settings, 'REAUTH_SECONDS', 10 * 60)


def recently_confirmed(request) -> bool:
    from .middleware import SESSION_STARTED_KEY

    session = getattr(request, 'session', None)
    if session is None:
        return False
    last = max(session.get(REAUTH_KEY) or 0, session.get(SESSION_STARTED_KEY) or 0)
    return time.time() - last <= _window()


def mark_confirmed(request) -> None:
    request.session[REAUTH_KEY] = int(time.time())


def can_confirm_with_code(user) -> bool:
    from . import mfa

    return mfa.has_mfa(user)


def check(user, password='', code='') -> bool:
    """True if ``password`` is the user's, or ``code`` is a valid two-step code."""
    from . import mfa

    if password and user.has_usable_password() and user.check_password(password):
        return True
    if code and mfa.has_mfa(user) and mfa.verify(user, code) is not None:
        return True
    return False


def confirm_url(next_url) -> str:
    from urllib.parse import urlencode

    from django.urls import reverse

    return f"{reverse('reauth')}?{urlencode({'next': next_url})}"
