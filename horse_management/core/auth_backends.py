"""
Authentication backends: email address or username with a password, and
Google sign-in.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend, ModelBackend
from django.db.models import Q


class EmailOrUsernameBackend(ModelBackend):
    """Sign in with either email address or username (case-insensitive).

    Accounts created through Settings → Users store the email as the
    username, while older accounts (e.g. from ``createsuperuser``) keep
    working with their original username.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if not username or not password:
            return None

        # No LIMIT here: with three or more matches an unordered LIMIT 2
        # could omit the username owner, and the login was refused or not
        # depending on row order.
        users = list(UserModel._default_manager.filter(
            Q(username__iexact=username) | Q(email__iexact=username)
        ))
        if len(users) > 1:
            # The identifier matched one account's username and another's
            # email — the username owner wins. Two accounts sharing an email
            # (legacy data) stay ambiguous and are refused.
            users = [u for u in users if u.username.lower() == username.lower()]
        if len(users) != 1:
            # Hash anyway so response timing doesn't reveal whether the
            # account exists (mirrors ModelBackend).
            UserModel().set_password(password)
            return None

        user = users[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None


class GoogleBackend(BaseBackend):
    """The backend named on a session that signed in with Google.

    It checks nothing by itself: ``authenticate`` always answers ``None``,
    so a password form can never sign in through it. The Google checks are
    in ``core/views/google.py``, which then calls ``login()`` with this
    backend's name. Django needs the class only to load the user again on
    each later request, and a deactivated user is not loaded.

    Permissions are answered by ``EmailOrUsernameBackend``, which Django
    asks for every user whichever backend signed them in.
    """

    def authenticate(self, request, **credentials):
        return None

    def get_user(self, user_id):
        user = get_user_model()._default_manager.filter(pk=user_id).first()
        return user if user is not None and user.is_active else None
