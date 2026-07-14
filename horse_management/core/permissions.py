"""Role Suite enforcement: per-feature access checks for views and templates.

The single authorization seam for the app (successor to the old is_staff
Admin/Viewer scheme). Views declare the feature area they belong to and the
level they need:

    class InvoiceCreateView(FeatureAccessMixin, CreateView):
        feature = 'invoices'                 # access_level defaults to 'full'

    @feature_required('invoices', LEVEL_VIEW)
    def invoice_pdf(request, pk): ...

Denials for logged-in users redirect to the dashboard with a message (or to
Settings when the dashboard itself is hidden); HTMX and non-GET requests get
a 403 instead, since a redirect would corrupt partial swaps or silently
re-target form posts. Anonymous users get the normal login redirect.
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .features import (  # noqa: F401  (re-exported for call sites)
    ALL_FULL,
    DEFAULT_LEVELS,
    FEATURES,
    FEATURES_BY_KEY,
    LEVEL_FULL,
    LEVEL_HIDDEN,
    LEVEL_ORDER,
    LEVEL_VIEW,
)

DENIED_MESSAGE = (
    "Your role doesn't include access to that area — "
    "ask an administrator to update it in Settings → Users & Roles."
)


def access_map(user):
    """The user's resolved feature→level map, memoized on the user object.

    Superusers always get full access (so ``createsuperuser`` accounts work
    before any Role exists). Anonymous users and users without a role
    assignment resolve to all-hidden.
    """
    cached = getattr(user, '_feature_access', None)
    if cached is not None:
        return cached

    if not user.is_authenticated:
        result, role_name = dict(DEFAULT_LEVELS), ""
    elif user.is_superuser:
        result, role_name = dict(ALL_FULL), "Superuser"
    else:
        from .models import UserRole
        try:
            assignment = UserRole.objects.select_related('role').get(user=user)
        except UserRole.DoesNotExist:
            result, role_name = dict(DEFAULT_LEVELS), "No role"
        else:
            result, role_name = assignment.role.resolved_access(), assignment.role.name

    user._feature_access = result
    user._role_name = role_name
    return result


def has_feature_access(user, feature, level=LEVEL_VIEW):
    """True if the user's level for ``feature`` is at least ``level``.

    Unknown feature keys raise KeyError — a typo in a view declaration should
    fail loudly, not silently deny (or worse, allow).
    """
    required = LEVEL_ORDER[level]
    actual = LEVEL_ORDER[access_map(user)[feature]]
    return actual >= required


def deny(request):
    """The shared insufficient-access response for logged-in users."""
    if getattr(request, 'htmx', False) or request.method != 'GET':
        raise PermissionDenied
    messages.error(request, DENIED_MESSAGE)
    if has_feature_access(request.user, 'dashboard', LEVEL_VIEW):
        return redirect('dashboard')
    # The settings page always renders at least the Account section.
    return redirect('app_settings')


class FeatureAccessMixin(LoginRequiredMixin):
    """CBV guard: require ``access_level`` on ``feature``.

    Built on plain LoginRequiredMixin (not UserPassesTestMixin) so the
    denial response is ours: redirect-with-message rather than 403.
    """

    feature = None
    access_level = LEVEL_FULL

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not has_feature_access(request.user, self.feature, self.access_level):
            return deny(request)
        return super().dispatch(request, *args, **kwargs)


def feature_required(feature, level=LEVEL_FULL):
    """FBV guard: ``@feature_required('invoices', LEVEL_VIEW)``."""
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if not has_feature_access(request.user, feature, level):
                return deny(request)
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


class _FeatureLevels:
    """Template-friendly bools for one feature: ``.view`` / ``.full``."""

    __slots__ = ('view', 'full')

    def __init__(self, level):
        order = LEVEL_ORDER[level]
        self.view = order >= LEVEL_ORDER[LEVEL_VIEW]
        self.full = order >= LEVEL_ORDER[LEVEL_FULL]


class FeatureAccess:
    """Lazy per-request wrapper: ``feature_access.invoices.full`` in templates."""

    def __init__(self, user):
        self._user = user

    def __getattr__(self, key):
        if key not in FEATURES_BY_KEY:
            raise AttributeError(key)
        levels = _FeatureLevels(access_map(self._user)[key])
        setattr(self, key, levels)
        return levels


def feature_access_context(request):
    """Context processor: ``feature_access`` + ``current_role_name``."""
    user = getattr(request, 'user', None)
    if user is None:
        return {}
    return {
        'feature_access': FeatureAccess(user),
        'current_role_name': role_name_for(user),
    }


def role_name_for(user):
    """Display name of the user's role for badges (memoized with access_map)."""
    access_map(user)
    return user._role_name
