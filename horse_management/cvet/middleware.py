from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import reverse


class VetPortalMiddleware:
    """Keep a practice login (the Vet role) on C-vet.

    A vet works for the practice, not for the yard. Such a login
    (``cvet.access.is_vet_only``) may reach C-vet, the two-step sign-in
    pages, changing its password, and signing out. Every other path,
    including staff pages that only check that someone is signed in, the
    Django admin and uploaded files, sends it to C-vet. A POST to a staff
    URL is redirected too, so it never runs.

    The same rule as ``core.middleware.ProviderPortalMiddleware`` has for
    the farrier, kept apart from it so that neither login is taken for
    the other. Must come after MFAMiddleware.

    It also gives such a login a long idle window
    (``CVET_SESSION_IDLE_AGE``), so the vet is not asked for a password
    at the yard, where there is no signal to sign in with.
    """

    PORTAL_PREFIX = '/c-vet/'
    ALLOWED_NAMES = (
        'logout', 'password_change', 'password_change_done',
        'mfa_verify', 'mfa_setup', 'mfa_new_recovery_codes',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from core.middleware import SESSION_ABSOLUTE_AGE_KEY

        from .access import is_vet_only

        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            if is_vet_only(user):
                self._extend_session(request)
                if self._must_redirect(request):
                    return HttpResponseRedirect(reverse('cvet:app'))
            elif SESSION_ABSOLUTE_AGE_KEY in request.session:
                # No longer a Vet login (the role changed): back to the
                # sign-in lengths that every other login has.
                del request.session[SESSION_ABSOLUTE_AGE_KEY]
                request.session.set_expiry(settings.SESSION_COOKIE_AGE)
        return self.get_response(request)

    @staticmethod
    def _extend_session(request):
        # The idle window. The cap from sign-in is set in cvet/signals.py.
        age = settings.CVET_SESSION_IDLE_AGE
        if request.session.get_expiry_age() != age:
            request.session.set_expiry(age)

    def _must_redirect(self, request):
        path = request.path
        if path.startswith(self.PORTAL_PREFIX) or path.startswith(settings.STATIC_URL):
            return False
        return path not in {reverse(name) for name in self.ALLOWED_NAMES}
