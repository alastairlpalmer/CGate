import logging
import time

logger = logging.getLogger("performance")


class ServerTimingMiddleware:
    """Adds Server-Timing header to every response and logs slow requests."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        total_ms = (time.monotonic() - start) * 1000

        response["Server-Timing"] = f"total;dur={total_ms:.1f}"

        if total_ms > 2000:
            logger.warning(
                "Slow request: %s %s took %.0fms",
                request.method,
                request.get_full_path(),
                total_ms,
            )

        return response


SESSION_STARTED_KEY = '_session_started'


def mark_session_started(request):
    """Record when this session signed in. Called on user_logged_in."""
    if request is not None and hasattr(request, 'session'):
        request.session[SESSION_STARTED_KEY] = int(time.time())


class AbsoluteSessionLifetimeMiddleware:
    """Sign a session out once it is SESSION_ABSOLUTE_AGE seconds old.

    SESSION_COOKIE_AGE is an idle timeout that rolls forward on every
    request, so on its own a cookie in constant use lives for ever. This
    counts from sign-in instead. Sessions that were signed in before this
    middleware existed have no start time; they start counting now.

    Must come after AuthenticationMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.contrib.auth import logout

        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            started = request.session.get(SESSION_STARTED_KEY)
            now = int(time.time())
            if started is None:
                request.session[SESSION_STARTED_KEY] = now
            elif now - started > settings.SESSION_ABSOLUTE_AGE:
                logout(request)
        return self.get_response(request)


class MFAMiddleware:
    """Hold a signed-in user at the two-step sign-in pages until they pass.

    * A user with an authenticator app who has not entered a code in this
      session can only reach the code page and sign out.
    * A user who must use two-step sign-in (``core.mfa.mfa_required``) but
      has not set it up can only reach the set-up page and sign out.

    Every other path, the Django admin and uploaded files included, is
    redirected. Must come after django_otp's OTPMiddleware, which works
    out whether this session has passed a code.

    A session that signed in with Google is not held: Google's check
    stands in for the code (``core.mfa.passed_by_google``).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        redirect_to = self._redirect_for(request)
        if redirect_to is not None:
            return self._redirect(request, redirect_to)
        return self.get_response(request)

    def _redirect_for(self, request):
        from django.conf import settings
        from django.urls import reverse

        from core import mfa

        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return None
        path = request.path
        if path.startswith(settings.STATIC_URL) or path == reverse('logout'):
            return None

        if mfa.passed_by_google(request):
            return None

        verify_url = reverse('mfa_verify')
        setup_url = reverse('mfa_setup')
        if mfa.session_enrolled(request):
            if user.is_verified() or path == verify_url:
                return None
            return verify_url
        if mfa.mfa_required(user) and path != setup_url:
            return setup_url
        return None

    @staticmethod
    def _redirect(request, url):
        from urllib.parse import urlencode

        from django.http import HttpResponseRedirect
        from django_htmx.http import HttpResponseClientRedirect

        if request.method == 'GET':
            url = f'{url}?{urlencode({"next": request.get_full_path()})}'
        if request.headers.get('HX-Request') == 'true':
            # A boosted request would swap the code page into the app's
            # layout. Make the browser load it as a whole page instead.
            return HttpResponseClientRedirect(url)
        return HttpResponseRedirect(url)


class PermissionsPolicyMiddleware:
    """Send ``Permissions-Policy`` on every response.

    The header turns off browser features the app never uses, so a script
    injected into a page cannot use them either. Location stays on for this
    site only: the "near you" chip and the maps use it. The camera stays on
    for this site only, for photo uploads. The value is in
    settings.PERMISSIONS_POLICY.
    """

    def __init__(self, get_response):
        from django.conf import settings

        self.get_response = get_response
        self.value = ', '.join(
            f"{feature}=({' '.join(origins)})"
            for feature, origins in settings.PERMISSIONS_POLICY.items()
        )

    def __call__(self, request):
        response = self.get_response(request)
        if self.value:
            response.headers.setdefault('Permissions-Policy', self.value)
        return response


class ProviderPortalMiddleware:
    """Keep a visiting professional's login on their own page.

    A Farrier-role user (``core.permissions.is_provider_only``) may reach
    the farrier page, horse profile photos, the two-step sign-in
    pages, changing their password, and signing out. Every other path,
    including staff pages that only check that someone is signed in, the
    Django admin and HTMX endpoints, sends them to Today's visit. A POST
    to a staff URL is redirected too, so it never runs.

    Must come after MFAMiddleware, so a farrier who still owes a code is
    held at the code page first. See docs/FARRIER_PAGE_PLAN.md, section 4.

    It also gives such a login a longer idle window
    (``PROVIDER_SESSION_IDLE_AGE``), so the farrier does not type a
    password in a field (section 9). The absolute cap from sign-in
    (``SESSION_ABSOLUTE_AGE``) still applies.
    """

    PORTAL_PREFIX = '/farrier/'
    ALLOWED_NAMES = (
        'logout', 'password_change', 'password_change_done',
        'mfa_verify', 'mfa_setup', 'mfa_new_recovery_codes',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from core.permissions import is_provider_only

        user = getattr(request, 'user', None)
        if user is not None and is_provider_only(user):
            self._extend_session(request)
            if self._must_redirect(request):
                return self._redirect(request)
        return self.get_response(request)

    @staticmethod
    def _extend_session(request):
        from django.conf import settings

        age = settings.PROVIDER_SESSION_IDLE_AGE
        if request.session.get_expiry_age() != age:
            request.session.set_expiry(age)

    def _must_redirect(self, request):
        from django.conf import settings
        from django.urls import reverse

        path = request.path
        if path.startswith(self.PORTAL_PREFIX) or path.startswith(settings.STATIC_URL):
            return False
        # Horse profile photos only (the horse list shows them). Every
        # other upload (passports, receipts, owner documents) stays fenced,
        # and the media view's own role check still applies on top.
        if settings.MEDIA_URL and path.startswith(f'{settings.MEDIA_URL}horses/'):
            return False
        return path not in {reverse(name) for name in self.ALLOWED_NAMES}

    @staticmethod
    def _redirect(request):
        from django.http import HttpResponseRedirect
        from django.urls import reverse
        from django_htmx.http import HttpResponseClientRedirect

        url = reverse('farrier:today')
        if request.headers.get('HX-Request') == 'true':
            return HttpResponseClientRedirect(url)
        return HttpResponseRedirect(url)
