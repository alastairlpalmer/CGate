"""Registry of home-dashboard widgets.

Single source of truth for widget keys, display names and the feature area
each one belongs to. Consumed by ``DashboardPreference`` (default layout and
per-user visibility) and by the dashboard views and template.

The dashboard is six zones, each answering one question; every zone is a
widget a user can switch off in Settings. Zones render in a designed
composition (the template decides where each sits), so the stored ``order``
is kept for compatibility but not used for placement.

  attention   what needs doing: one inbox across every area
  upcoming    the next 14 days and the visits worth booking together
  yard_board  sites and their locations: occupancy, land use, rest
  in_foal     mares in foal (renders only when there are any)
  money       the month's billing position
  activity    what changed, as a chronological log

Rows inside a zone are gated on their own feature area (a role with
Invoices hidden never sees an invoice row in the inbox); the ``feature``
below is the area the zone as a whole needs.
"""

GROUPS = ("main",)

WIDGETS = [
    {"key": "attention",  "name": "Needs action",   "group": "main", "feature": "dashboard"},
    {"key": "upcoming",   "name": "Next 14 days",   "group": "main", "feature": "dashboard"},
    {"key": "yard_board", "name": "Yard board",     "group": "main", "feature": "locations"},
    {"key": "in_foal",    "name": "In foal",        "group": "main", "feature": "breeding"},
    {"key": "money",      "name": "Money",          "group": "main", "feature": "invoices"},
    {"key": "activity",   "name": "What changed",   "group": "main", "feature": "dashboard"},
]

WIDGETS_BY_KEY = {w["key"]: w for w in WIDGETS}

# Every zone is on by default. Kept as a set so a future opt-in zone can be
# added without changing DashboardPreference.
DEFAULT_HIDDEN = set()

DEFAULT_LAYOUT = {
    w["key"]: {"visible": w["key"] not in DEFAULT_HIDDEN, "order": i}
    for i, w in enumerate(WIDGETS)
}


def widgets_in_group(group):
    """Return the registry entries for a single group, in registry order."""
    return [w for w in WIDGETS if w["group"] == group]
