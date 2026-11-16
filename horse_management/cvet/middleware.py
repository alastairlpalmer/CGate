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
    """

    PORTAL_PREFIX = '/c-vet/'
    ALLOWED_NAMES = (
        'logout', 'password_change', 'password_change_done',
        'mfa_verify', 'mfa_setup', 'mfa_new_recovery_codes',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .access import is_vet_only

        user = getattr(request, 'user', None)
        if user is not None and is_vet_only(user) and self._must_redirect(request):
            return HttpResponseRedirect(reverse('cvet:app'))
        return self.get_response(request)

    def _must_redirect(self, request):
        path = request.path
        if path.startswith(self.PORTAL_PREFIX) or path.startswith(settings.STATIC_URL):
            return False
        return path not in {reverse(name) for name in self.ALLOWED_NAMES}
