"""
Views for core app — split by domain.

All views are re-exported here so that urls.py imports remain unchanged.
"""

from .dashboard import dashboard, dashboard_health_alerts  # noqa: F401
from .horses import (  # noqa: F401
    HorseCreateView,
    HorseDetailView,
    HorseListView,
    HorseUpdateView,
    cancel_departure,
    confirm_departure,
    confirm_departures_bulk,
    horse_arrive,
    horse_depart,
    horse_move,
    manage_ownership_shares,
    new_arrival,
)
from .locations import (  # noqa: F401
    LocationCreateView,
    LocationDetailView,
    LocationListView,
    LocationUpdateView,
    log_arrival,
    log_departure,
)
from .owners import (  # noqa: F401
    OwnerCreateView,
    OwnerDetailView,
    OwnerListView,
    OwnerUpdateView,
)
from .placements import (  # noqa: F401
    PlacementCreateView,
    PlacementListView,
    PlacementUpdateView,
)
from .settings import (  # noqa: F401
    app_settings,
    dashboard_toggle,
    health_check,
    rate_type_create,
    rate_type_update,
)
