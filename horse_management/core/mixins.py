"""Back-compat import point for view guards.

The is_staff-based ``StaffRequiredMixin``/``staff_required`` pair is gone —
access control is now per-feature via the Role Suite. Import the guards from
here or from ``core.permissions`` interchangeably.
"""

from .permissions import (  # noqa: F401
    LEVEL_FULL,
    LEVEL_VIEW,
    FeatureAccessMixin,
    feature_required,
    has_feature_access,
)
