"""The TRAINING_ENABLED switch: with it off, every Training URL is 404."""

from functools import wraps

from django.conf import settings
from django.http import Http404


def training_on():
    return bool(getattr(settings, 'TRAINING_ENABLED', False))


class TrainingEnabledMixin:
    """Put it first in the bases, so the switch is checked before access."""

    def dispatch(self, request, *args, **kwargs):
        if not training_on():
            raise Http404("Training is switched off.")
        return super().dispatch(request, *args, **kwargs)


def training_enabled(view_func):
    """FBV guard. Put it above ``feature_required``, so it runs first."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not training_on():
            raise Http404("Training is switched off.")
        return view_func(request, *args, **kwargs)
    return _wrapped
