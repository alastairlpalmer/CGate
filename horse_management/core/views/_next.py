"""The one validated ``?next=`` helper.

A view that returns to the page it was called from reads ``next`` from
the POST body or the query string and follows it only when it points at
this host, so a crafted link can never turn a redirect into an open one.
Four views used to carry their own copy of this check; a fix here now
reaches all of them.
"""

from django.utils.http import url_has_allowed_host_and_scheme


def safe_next(request, fallback=None):
    """The ``next`` target when it is a safe local URL, else ``fallback``."""
    candidate = request.POST.get('next') or request.GET.get('next') or ''
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback
