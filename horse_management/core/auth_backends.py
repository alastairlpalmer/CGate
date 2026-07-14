"""
Authentication backend that accepts an email address or a username.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
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

        users = list(UserModel._default_manager.filter(
            Q(username__iexact=username) | Q(email__iexact=username)
        )[:2])
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
