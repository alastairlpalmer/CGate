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
    return str(username).strip().lower()
