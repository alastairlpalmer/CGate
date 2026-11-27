"""Username normalisation for django-axes.

Sign-in accepts an email address or a username, case-insensitively (see
``core.auth_backends.EmailOrUsernameBackend``). django-axes counts failed
attempts against the string the form was posted with, so without this every
capitalisation would get its own allowance: five guesses at ``jo@yard.co``,
five more at ``Jo@yard.co``, and so on without limit.

Wired up by AXES_USERNAME_CALLABLE in settings.py.
"""


def normalise_username(request, credentials=None):
    """The identity a lockout counts against: trimmed and lower-cased.

    Returns an empty string when no username was supplied, which is what
    axes expects for an attempt it cannot attribute.
    """
    username = None
    if credentials:
        username = credentials.get('username')
    if username is None and request is not None:
        post = getattr(request, 'POST', None)
        if post is not None:
            username = post.get('username')
    if not username:
        return ''
    typed = str(username).strip().lower()
    return _account_key(typed)


def _account_key(typed):
    """One key for one account, whichever of its names was typed.

    An account whose username is not its email address could be signed in
    to with either, and each had its own five tries: ten in all. The key is
    the account's own username, found by the same rule as the sign-in
    backend. A name that matches no account (or more than one) is counted
    as typed.
    """
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    users = list(
        get_user_model()._default_manager
        .filter(Q(username__iexact=typed) | Q(email__iexact=typed))
        .values_list('username', flat=True)[:3]
    )
    if len(users) > 1:
        users = [name for name in users if name.lower() == typed]
    if len(users) == 1:
        return users[0].lower()
    return typed
