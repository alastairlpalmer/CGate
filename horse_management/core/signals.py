"""Server-side analytics events.

Sign in and sign out are captured here rather than in the browser, so they are
recorded even when JavaScript is blocked, the tab is closed mid-redirect, or
the user signs in from the Django admin.

Receivers are connected in ``core.apps.CoreConfig.ready``. Every one of them
is a no-op when ``POSTHOG_API_KEY`` is unset.
"""

from __future__ import annotations

from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from core import analytics


def _source(request) -> str:
    """Where the sign-in came from, for splitting admin from app usage."""
    if request is None:
        return 'unknown'
    path = request.path or ''
    if path.startswith('/admin/'):
        return 'admin'
    return 'app'


@receiver(user_logged_in, dispatch_uid='core.analytics.user_logged_in')
def on_user_logged_in(sender, request, user, **kwargs):
    analytics.capture(user, 'user_signed_in', {'source': _source(request)})


@receiver(user_logged_out, dispatch_uid='core.analytics.user_logged_out')
def on_user_logged_out(sender, request, user, **kwargs):
    if user is None:
        return
    analytics.capture(user, 'user_signed_out', {'source': _source(request)})


@receiver(user_login_failed, dispatch_uid='core.analytics.user_login_failed')
def on_user_login_failed(sender, credentials=None, request=None, **kwargs):
    # ``credentials`` holds the attempted username and is never sent. Only the
    # count of failures is useful, and only as an anonymous event.
    analytics.capture(None, 'user_sign_in_failed', {'source': _source(request)})


# ── Business name cache ──────────────────────────────────────────────────────
# The sidebar shows the yard's name through the ``business_name`` template
# tag, which caches it. Clear the cache the moment Business Settings save.

from django.core.cache import cache  # noqa: E402
from django.db.models.signals import post_save  # noqa: E402

from core.models import BusinessSettings  # noqa: E402
from core.templatetags.ui_extras import BUSINESS_NAME_CACHE_KEY  # noqa: E402


@receiver(post_save, sender=BusinessSettings)
def clear_business_name_cache(sender, **kwargs):
    cache.delete(BUSINESS_NAME_CACHE_KEY)
