"""
Permission mixins and decorators for role-based access control.

Uses Django's built-in is_staff flag:
  - is_staff=True  → Admin (full access)
  - is_staff=False → Viewer (read-only + health recording)
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Mixin for class-based views that require admin (is_staff) access."""

    def test_func(self):
        return self.request.user.is_staff


def staff_required(view_func):
    """Decorator for function-based views that require admin (is_staff) access."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return _wrapped
