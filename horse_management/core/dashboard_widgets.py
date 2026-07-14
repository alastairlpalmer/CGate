"""Registry of home-dashboard widgets.

Single source of truth for widget keys, display names, and visual grouping.
Consumed by ``DashboardPreference`` (for default layout) and by the dashboard
view + template (for conditional rendering and reorder within a group).

Groups map to the visual sections of the dashboard:
  - ``kpi``     : 4-column KPI card row
  - ``list``    : two-column lists / tables / timeline
  - ``health``  : lazy-loaded three-column health alerts

The Revenue/Capacity charts moved to the Finances page and are no longer
dashboard widgets; ``DashboardPreference.resolved_layout()`` ignores their
stale keys in stored layouts.
"""

GROUPS = ("kpi", "list", "health")

# ``feature`` ties a widget to a Role Suite feature area (core.features):
# users whose role can't view that feature never see (or fetch) the widget.
WIDGETS = [
    {"key": "kpi_total_horses",         "name": "Total Horses",                "group": "kpi",    "feature": "horses"},
    {"key": "kpi_vaccinations_due",     "name": "Vaccinations Due (stat)",     "group": "kpi",    "feature": "health"},
    {"key": "kpi_unbilled_charges",     "name": "Unbilled Charges",            "group": "kpi",    "feature": "charges"},
    {"key": "kpi_outstanding_invoices", "name": "Outstanding Invoices (stat)", "group": "kpi",    "feature": "invoices"},
    {"key": "pending_departures",       "name": "Pending Departures",          "group": "list",   "feature": "horses"},
    {"key": "recent_activity",          "name": "Recent Activity",             "group": "list",   "feature": "horses"},
    {"key": "list_vaccinations_due",    "name": "Vaccinations Due (30 days)",  "group": "list",   "feature": "health"},
    {"key": "list_farrier_due",         "name": "Farrier Due (2 weeks)",       "group": "list",   "feature": "health"},
    {"key": "table_outstanding",        "name": "Outstanding Invoices (table)", "group": "list",  "feature": "invoices"},
    {"key": "list_field_rest",          "name": "Field Rest (This Year)",      "group": "list",   "feature": "locations"},
    {"key": "health_upcoming_dep",      "name": "Upcoming Departures",         "group": "health", "feature": "horses"},
    {"key": "health_ehv_due",           "name": "EHV Vaccinations Due",        "group": "health", "feature": "health"},
    {"key": "health_egg_counts",        "name": "High Egg Counts",             "group": "health", "feature": "health"},
    {"key": "health_vet_followups",     "name": "Vet Follow-ups",              "group": "health", "feature": "health"},
]

WIDGETS_BY_KEY = {w["key"]: w for w in WIDGETS}

# Hidden unless a user opts in via settings. Expected-departure dates rarely
# match the day horses are actually collected, so Pending Departures is noise
# for most users; users who explicitly enabled it keep it (stored prefs win).
DEFAULT_HIDDEN = {"pending_departures"}

DEFAULT_LAYOUT = {
    w["key"]: {"visible": w["key"] not in DEFAULT_HIDDEN, "order": i}
    for i, w in enumerate(WIDGETS)
}


def widgets_in_group(group):
    """Return the registry entries for a single group, in registry order."""
    return [w for w in WIDGETS if w["group"] == group]
