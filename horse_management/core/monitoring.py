"""Sentry error monitoring.

Before this, an unhandled 500 in production went to the host's console log
and nowhere else. Nobody watches a console log, so errors were found by
someone reporting that a page did not work — if they reported it at all.

Off unless SENTRY_DSN is set, so local runs and the test suite send
nothing anywhere.

Privacy is the reason this file is longer than the four lines the Sentry
quickstart shows. The app holds owner names, email addresses, postal
addresses and invoice figures, all personal data under UK GDPR. An error
report that carries a request body or a full local-variable dump would
copy that personal data to a third party, and the yard has no record of it
going. So:

* ``send_default_pii`` stays False. Sentry then omits the request body,
  cookies and the user's IP address.
* ``_scrub`` removes this app's own secrets from anything that is sent,
  because a traceback frame can hold settings values as local variables.
* Query strings are stripped. The search box puts owner and horse names
  into ``?q=``, the same reason the PostHog integration strips them.
"""

import logging

logger = logging.getLogger(__name__)

# Values that must never leave this app. Matched case-insensitively against
# the *name* of a local variable, a header or an extra-context key. Sentry
# already scrubs a default list (password, secret, token, api_key and
# similar); these are the ones specific to this app or worth being explicit
# about, because a miss here leaks a live credential.
SENSITIVE_KEYS = frozenset({
    'secret_key',
    'field_encryption_keys',
    'database_url',
    'xero_client_secret',
    'xero_client_id',
    'email_host_password',
    'access_token',
    'refresh_token',
    'oauth_state',
    'celery_broker_url',
    'celery_result_backend',
    'posthog_api_key',
    'authorization',
    'cookie',
    'set-cookie',
    'csrfmiddlewaretoken',
})

MASK = '[Filtered]'


def _is_sensitive(key) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    if lowered in SENSITIVE_KEYS:
        return True
    # Catch variants like 'xero_refresh_token' or 'db_password' that the
    # exact list above would miss.
    return any(
        marker in lowered
        for marker in ('password', 'secret', 'token', 'api_key', 'apikey')
    )


def _scrub(value, depth=0):
    """Replace sensitive values anywhere in a nested event structure.

    Depth-limited: a Sentry event is a plain dict tree, but a traceback
    frame's local variables can hold a self-referencing repr and this must
    never be the thing that breaks error reporting.
    """
    if depth > 12:
        return value
    if isinstance(value, dict):
        return {
            key: (MASK if _is_sensitive(key) else _scrub(item, depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item, depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub(item, depth + 1) for item in value)
    return value


def _strip_query_string(event):
    """Drop ``?...`` from the reported URL.

    The search box puts typed text — often an owner or horse name — into
    the query string.
    """
    request = event.get('request')
    if isinstance(request, dict):
        url = request.get('url')
        if isinstance(url, str) and '?' in url:
            request['url'] = url.split('?', 1)[0]
        request.pop('query_string', None)
    return event


def before_send(event, hint):
    """Last gate before an event leaves this process."""
    try:
        return _scrub(_strip_query_string(event))
    except Exception:
        # A bug in scrubbing must not send an unscrubbed event, and must
        # not crash the thread that raised the original error. Dropping the
        # event loses one report; sending it could leak a credential.
        logger.exception('Sentry before_send failed; dropping the event')
        return None


def init(dsn, environment='production', release='', traces_sample_rate=0.0):
    """Start Sentry, or do nothing when no DSN is given.

    Values are passed in rather than read from ``django.conf.settings``.
    This is called from the bottom of settings.py, and reading
    ``django.conf.settings`` at that point would re-enter the settings
    module before it has finished loading.
    """
    if not dsn:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release or None,
        integrations=[
            DjangoIntegration(),
            # Without this, a failing reminder or invoice task fails
            # silently in the worker — which is where the automated work
            # happens and where nobody is watching.
            CeleryIntegration(),
            LoggingIntegration(
                level=logging.INFO,        # breadcrumbs from INFO up
                event_level=logging.ERROR,  # an event from ERROR up
            ),
        ],
        # False: no request bodies, no cookies, no IP addresses. See the
        # module docstring — this app's data is personal data.
        send_default_pii=False,
        traces_sample_rate=traces_sample_rate,
        before_send=before_send,
    )
    return True
