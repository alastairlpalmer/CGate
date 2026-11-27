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
# A session's own absolute lifetime, in seconds, in place of
# SESSION_ABSOLUTE_AGE. Set at sign-in for a login that works with no
# signal (C-vet's Vet role: cvet/signals.py). The session is stored on
# the server, so a browser cannot set or change it.
SESSION_ABSOLUTE_AGE_KEY = '_session_absolute_age'


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
            else:
                limit = request.session.get(SESSION_ABSOLUTE_AGE_KEY)
                if not isinstance(limit, int) or limit <= 0:
                    limit = settings.SESSION_ABSOLUTE_AGE
                if now - started > limit:
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
            # "Remember this device": a code was given on this device in
            # the last MFA_REMEMBER_DAYS, so the password was enough.
            if mfa.pass_remembered_device(request):
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


class SignInRedirectMiddleware:
    """Send an htmx request to the sign-in page as a whole page load.

    A sign-in ends after SESSION_COOKIE_AGE with no use, or after
    SESSION_ABSOLUTE_AGE. The next request gets a redirect to the sign-in
    page. A browser follows that for a page load. For an htmx request the
    answer was read as the page that was asked for: htmx looked in the
    sign-in page for ``#main-content``, found nothing, and left the person
    with the bars and an empty screen. ``HX-Redirect`` makes the browser
    load the sign-in page itself. ``MFAMiddleware`` does the same for the
    code page.

    ``next`` is where to go after sign-in. For a tap on a link it is the
    page that was asked for. For a part of a page (a sheet, a count), or
    for a save, it is the page the person is on: the part alone is not a
    page, and a save cannot be sent again by a redirect.

    Must come after HtmxMiddleware, which reads the htmx headers.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code not in (301, 302):
            return response
        if request.headers.get('HX-Request') != 'true':
            return response

        from urllib.parse import urlencode, urlsplit

        from django.conf import settings
        from django.shortcuts import resolve_url
        from django_htmx.http import HttpResponseClientRedirect

        sign_in = resolve_url(settings.LOGIN_URL)
        if urlsplit(response.get('Location', '')).path != sign_in:
            return response
        if request.path == sign_in:
            return response

        htmx = getattr(request, 'htmx', None)
        whole_page = request.method == 'GET' and (
            request.headers.get('HX-Boosted') == 'true'
            or request.headers.get('HX-History-Restore-Request') == 'true'
        )
        if whole_page:
            back_to = request.get_full_path()
        else:
            back_to = (htmx.current_url_abs_path if htmx else None) or ''
        url = f'{sign_in}?{urlencode({"next": back_to})}' if back_to else sign_in
        return HttpResponseClientRedirect(url)


class AppVersionMiddleware:
    """Say which version of the app made this response.

    A tap replaces the content of a page; the scripts and styles in its
    head stay as they were first loaded. A page that stays open across a
    deploy would then run new HTML with old scripts. The page carries its
    own version (``{% app_version %}`` in base.html) and compares it with
    this header; when they differ, the next tap loads the page in full
    (static/js/connection.js).
    """

    HEADER = 'X-App-Version'

    def __init__(self, get_response):
        from core.templatetags.cache_bust import app_version

        self.get_response = get_response
        self.version = app_version()

    def __call__(self, request):
        response = self.get_response(request)
        if response.get('Content-Type', '').startswith('text/html'):
            response.headers.setdefault(self.HEADER, self.version)
        return response


class SlimPageCacheMiddleware:
    """Keep the short answer to a tap out of the way of a page load.

    A tap on a link (``HX-Boosted``) gets the page without its frame
    (``core.context_processors.page_frame``). The address is the same as
    for a page load. A browser that kept the short answer could show it
    for a later page load of that address, most likely on Back after a
    full load of another page: a page with no styles and no scripts.

    Two headers prevent that. ``Vary: HX-Boosted`` on every page says the
    answer depends on that header. ``Cache-Control: no-store`` on the short
    answer says it is not to be kept at all.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.utils.cache import patch_vary_headers

        response = self.get_response(request)
        if response.get('Content-Type', '').startswith('text/html'):
            patch_vary_headers(response, ['HX-Boosted'])
            if request.headers.get('HX-Boosted') == 'true':
                response.headers.setdefault('Cache-Control', 'no-store')
        return response


class ProviderPortalMiddleware:
    """Keep a visiting professional's login on their own page.

    A Farrier-role user (``core.permissions.is_provider_only``) may reach
    the farrier page, horse profile photos, the two-step code page (not
    two-step set-up), changing their password, and signing out. Every other path,
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
    # No mfa_setup: a QR session (no password) must not be able to turn on
    # two-step sign-in and lock the real farrier out of their login.
    ALLOWED_NAMES = (
        'logout', 'password_change', 'password_change_done',
        'mfa_verify', 'mfa_new_recovery_codes',
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


class RequestSizeLimitMiddleware:
    """Refuse a request body larger than MAX_REQUEST_BYTES, before it is read.

    DATA_UPLOAD_MAX_MEMORY_SIZE does not count uploaded files, and Django
    writes a large upload to disk before any view (or sign-in check) runs.
    Without this, anyone could send a request of any size to any address.
    The limit is the largest form a person can send: a few 5 MB files.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.http import HttpResponse

        try:
            length = int(request.META.get('CONTENT_LENGTH') or 0)
        except ValueError:
            length = 0
        if length > settings.MAX_REQUEST_BYTES:
            return HttpResponse(
                'This upload is too large. Each file can be at most 5 MB.',
                status=413, content_type='text/plain; charset=utf-8',
            )
        return self.get_response(request)


BACKGROUND_HEADER = 'HTTP_X_BACKGROUND'


from django.contrib.sessions.middleware import SessionMiddleware as _DjangoSessionMiddleware  # noqa: E402


class SessionMiddleware(_DjangoSessionMiddleware):
    """Django's session middleware, except for background polls.

    SESSION_SAVE_EVERY_REQUEST rolls the idle timeout forward on every
    request. The notification badge asks every minute, so an open tab
    nobody touched was never signed out (pre-launch review). A request
    sent with ``X-Background: 1`` and no session change is not saved, so
    it does not count as activity.
    """

    def process_response(self, request, response):
        session = getattr(request, 'session', None)
        if (
            request.META.get(BACKGROUND_HEADER) == '1'
            and session is not None
            and not session.modified
        ):
            return response
        return super().process_response(request, response)
