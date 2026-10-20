"""A small fixed-window rate limiter on Django's cache.

Used where django-axes does not reach: the public password-reset form,
which anyone can post to without signing in.

The counters live in ``CACHES['default']``. With the default local-memory
cache each server process counts on its own, so the real limit is the
configured one times the number of processes. Set ``CACHE_URL`` to a Redis
URL to share one count across all of them.
"""

from __future__ import annotations

import hashlib

from django.core.cache import cache


def allow(scope: str, key: str, limit: int, window_seconds: int) -> bool:
    """Count one hit for ``key`` and say whether it is within ``limit``.

    ``scope`` keeps unrelated limits apart. ``key`` should already be
    normalised (lower-cased email, IP address).
    """
    # Hashed: an email address can hold characters some cache backends
    # refuse in a key, and the raw address has no business in the cache.
    digest = hashlib.sha256(key.encode('utf-8')).hexdigest()
    cache_key = f'ratelimit:{scope}:{digest}'
    # add() only sets the key when it is missing, so the window starts at
    # the first hit and is not extended by later ones.
    if cache.add(cache_key, 1, timeout=window_seconds):
        return limit >= 1
    try:
        count = cache.incr(cache_key)
    except ValueError:
        # The key expired between add() and incr(): this is a new window.
        cache.add(cache_key, 1, timeout=window_seconds)
        return limit >= 1
    return count <= limit
