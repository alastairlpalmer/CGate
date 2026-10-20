"""Turn off two-step sign-in for one user, from a shell on the server.

For the day the only Administrator loses their phone and their recovery
codes, so nobody can do it from Settings → Users. Anyone who can run this
already holds the server, so it asks for nothing more.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from core import audit, mfa
from core.account_security import sign_out_everywhere
from core.models import AuditEvent


class Command(BaseCommand):
    help = "Turn off two-step sign-in for a user (by email or username) and sign them out everywhere."

    def add_arguments(self, parser):
        parser.add_argument('identifier', help="The user's email address or username.")

    def handle(self, *args, identifier, **options):
        users = list(get_user_model().objects.filter(
            Q(username__iexact=identifier) | Q(email__iexact=identifier)
        ))
        if len(users) != 1:
            raise CommandError(f"Expected one user matching {identifier!r}, found {len(users)}.")
        user = users[0]
        mfa.disable(user)
        ended = sign_out_everywhere(user)
        audit.record(AuditEvent.Action.MFA_RESET, target=user, detail='From a shell (mfa_reset command)')
        self.stdout.write(self.style.SUCCESS(
            f"Two-step sign-in is off for {user.get_username()}. Ended {ended} session(s)."
        ))
        if mfa.mfa_required(user):
            self.stdout.write("Their role needs it, so they must set it up again at their next sign-in.")
