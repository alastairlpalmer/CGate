"""Registry of home-dashboard widgets.

Single source of truth for widget keys, display names and the feature area
each one belongs to. Consumed by ``DashboardPreference`` (default layout and
per-user visibility) and by the dashboard views and template.

The dashboard is six zones, each answering one question; every zone is a
widget a user can switch off in Settings. What needs doing is not a zone:
it lives in the notification centre (notifications/centre.py), and the
dashboard's headline counts it and opens it. Zones render in a designed
composition (the template decides where each sits), so the stored ``order``
is kept for compatibility but not used for placement.

  metrics     the yard's figures across the top: horses, owners, ground,
              money; each figure gated on its own feature area
  upcoming    the month calendar, the chosen day's list, and the
              visits worth booking together (key kept from "Next 14 days")
  near_you    the site you are on, drawn, with the nearest location lit
              (only with LOCATION_MAPS_ENABLED; see ``requires_setting``)
  in_foal     mares in foal (renders only when there are any)
  money       the month's billing position
  activity    the Timeline: what changed, newest first, filtered to
              the kinds the viewer's role may see (key kept from
              "What changed")

Rows inside a zone are gated on their own feature area (a role with
Invoices hidden never sees an invoice row in the inbox); the ``feature``
below is the area the zone as a whole needs.
"""

GROUPS = ("main",)

WIDGETS = [
    {"key": "metrics",    "name": "Yard figures",   "group": "main", "feature": "dashboard"},
    {"key": "upcoming",   "name": "Calendar",       "group": "main", "feature": "dashboard"},
    {"key": "near_you",   "name": "Near you",       "group": "main", "feature": "locations",
     "requires_setting": "LOCATION_MAPS_ENABLED"},
    {"key": "in_foal",    "name": "In foal",        "group": "main", "feature": "breeding"},
    {"key": "money",      "name": "Money",          "group": "main", "feature": "invoices"},
    {"key": "activity",   "name": "Timeline",       "group": "main", "feature": "dashboard"},
]

WIDGETS_BY_KEY = {w["key"]: w for w in WIDGETS}

# Every zone is on by default. Kept as a set so a future opt-in zone can be
# added without changing DashboardPreference.
DEFAULT_HIDDEN = set()

DEFAULT_LAYOUT = {
    w["key"]: {"visible": w["key"] not in DEFAULT_HIDDEN, "order": i}
    for i, w in enumerate(WIDGETS)
}


def widget_available(widget):
    """False for a widget behind a settings flag that is off."""
    from django.conf import settings
    flag = widget.get("requires_setting")
    return not flag or bool(getattr(settings, flag, False))


def widgets_in_group(group):
    """Return the registry entries for a single group, in registry order."""
    return [w for w in WIDGETS if w["group"] == group]
