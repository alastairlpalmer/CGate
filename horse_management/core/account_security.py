"""Account security helpers: sign-in lockouts and session control.

Kept in one module so the views (Settings → Users, the password reset
flow, the user's own account page) share one definition of "locked" and
one way to end a user's sessions.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.sessions.models import Session
from django.utils import timezone


def lockout_identities(user) -> list[str]:
    """The strings a lockout can be counted against for ``user``.

    Sign-in accepts the email address or the username, and
    ``core.axes_username`` lower-cases whichever was typed. So one person
    can have a lock under either string.
    """
    identities = {user.username.strip().lower()}
    if user.email:
        identities.add(user.email.strip().lower())
    return sorted(i for i in identities if i)


def is_locked_out(user) -> bool:
    """True when django-axes would refuse this user's next sign-in."""
    from axes.models import AccessAttempt

    if not getattr(settings, 'AXES_ENABLED', True):
        return False
    attempts = AccessAttempt.objects.filter(
        username__in=lockout_identities(user),
        failures_since_start__gte=settings.AXES_FAILURE_LIMIT,
    )
    cooloff = getattr(settings, 'AXES_COOLOFF_TIME', None)
    if cooloff:
        attempts = attempts.filter(attempt_time__gte=timezone.now() - cooloff)
    return attempts.exists()


def clear_lockout(user) -> int:
    """Remove every failed-attempt record for ``user``. Returns the count."""
    from axes.utils import reset

    return sum(reset(username=identity) for identity in lockout_identities(user))


def sign_out_everywhere(user, keep_session_key: str | None = None) -> int:
    """End every live session that belongs to ``user``.

    Sessions are stored in the database (Django's default engine), and a
    yard has a handful of staff, so decoding each live session is cheap.
    ``keep_session_key`` spares the session the request came from, so a
    user who signs out their other devices stays signed in here.

    Returns the number of sessions ended.
    """
    ended = 0
    live = Session.objects.filter(expire_date__gt=timezone.now())
    for session in live.iterator():
        if session.session_key == keep_session_key:
            continue
        if session.get_decoded().get('_auth_user_id') == str(user.pk):
            session.delete()
            ended += 1
    return ended
