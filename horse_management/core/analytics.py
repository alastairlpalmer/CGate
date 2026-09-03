"""Product analytics (PostHog).

Two halves work together:

* **Browser** — ``templates/includes/posthog.html`` loads ``posthog-js`` for
  signed-in users. It autocaptures clicks, records heatmap data, and sends one
  ``$pageview`` per navigation. The app boosts every link with HTMX, so
  navigation is a DOM swap, not a page load; the template fires the pageview
  itself instead of letting the library guess.
* **Server** — this module. It captures events that must not depend on
  JavaScript, above all sign in and sign out.

Analytics is **off** unless ``POSTHOG_API_KEY`` is set, so local development,
CI and the test suite never send anything.

Privacy
-------
This app holds owner names, addresses and invoice figures. Both halves are
configured to mask personal data:

* Session replay masks every input and every piece of text on the page.
* Autocapture masks element text and element attributes, so a button labelled
  with an owner's name is recorded by position and selector only.
* URL query strings are stripped, because the search box puts typed names
  into ``?q=``.
* Person profiles carry a user id and a role. Usernames are sent only when
  ``POSTHOG_SEND_USERNAMES`` is on, because this app lets people sign in with
  an email address.
"""

from __future__ import annotations

import logging
import threading

from django.conf import settings

logger = logging.getLogger('core')

_client = None
_client_lock = threading.Lock()


def is_enabled() -> bool:
    """True when a project API key is configured."""
    return bool(getattr(settings, 'POSTHOG_API_KEY', ''))


def get_client():
    """Return the shared PostHog client, or ``None`` when analytics is off.

    Built on first use so an unconfigured deployment never imports the SDK.
    """
    global _client
    if not is_enabled():
        return None
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        try:
            from posthog import Posthog
        except ImportError:
            logger.warning(
                'POSTHOG_API_KEY is set but the posthog package is not '
                'installed. Server-side analytics is off.'
            )
            return None
        _client = Posthog(
            settings.POSTHOG_API_KEY,
            host=settings.POSTHOG_HOST,
            # Serverless functions freeze after the response, which can kill a
            # background flush thread before it sends. Sync mode costs a few
            # milliseconds on the sign-in request and nothing elsewhere.
            sync_mode=settings.POSTHOG_SYNC_MODE,
            # Feature flags are not used. Without this the SDK starts a poller
            # that makes a network call every 30 seconds.
            enable_local_evaluation=False,
            # Do not resolve the caller's IP address to a location.
            disable_geoip=True,
            timeout=5,
        )
        return _client


def distinct_id_for(user) -> str:
    """Stable id shared by the browser and the server for one user."""
    return f'user:{user.pk}'


def person_properties_for(user) -> dict:
    """Person profile fields. Deliberately small — see the module docstring."""
    from core.permissions import role_name_for

    props = {
        'role': role_name_for(user),
        'is_staff': bool(user.is_staff),
        'is_superuser': bool(user.is_superuser),
    }
    if settings.POSTHOG_SEND_USERNAMES:
        props['username'] = user.get_username()
    return props


def capture(user, event: str, properties: dict | None = None) -> None:
    """Send one event for ``user``. Never raises — analytics must not break a
    request. ``user`` may be ``None`` for events with no signed-in person."""
    client = get_client()
    if client is None:
        return
    props = dict(properties or {})
    if user is None or not getattr(user, 'is_authenticated', False):
        # Keep anonymous events out of the person database.
        props['$process_person_profile'] = False
        did = 'anonymous'
    else:
        did = distinct_id_for(user)
        props.setdefault('$set', person_properties_for(user))
    try:
        client.capture(event, distinct_id=did, properties=props)
    except Exception:  # pragma: no cover - defensive
        logger.exception('PostHog capture failed for event %r', event)


def template_context(request) -> dict:
    """Context processor: ``posthog`` config for the browser snippet.

    Returns an empty config unless analytics is on and someone is signed in.
    Anonymous visitors — including the sign-in page — load no tracker at all,
    which keeps the app clear of cookie-consent obligations.
    """
    user = getattr(request, 'user', None)
    if not is_enabled() or user is None or not user.is_authenticated:
        return {'posthog': {'enabled': False}}
    # With the proxy on, the browser talks only to this origin. posthog-js
    # accepts a relative api_host, and the loader builds its script URL from
    # asset_host, so both become the proxy path. ui_host stays absolute: the
    # toolbar needs the real dashboard.
    if settings.POSTHOG_PROXY:
        api_host = asset_host = settings.POSTHOG_PROXY_PATH
    else:
        api_host = settings.POSTHOG_HOST
        asset_host = settings.POSTHOG_ASSET_HOST
    return {
        'posthog': {
            'enabled': True,
            'api_key': settings.POSTHOG_API_KEY,
            'api_host': api_host,
            'ui_host': settings.POSTHOG_UI_HOST,
            'asset_host': asset_host,
            'session_recording': settings.POSTHOG_SESSION_RECORDING,
            'debug': settings.POSTHOG_DEBUG,
            'distinct_id': distinct_id_for(user),
            'person_properties': person_properties_for(user),
        }
    }
