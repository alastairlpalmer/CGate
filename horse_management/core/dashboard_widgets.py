"""Registry of home-dashboard widgets.

Single source of truth for widget keys, display names, and visual grouping.
Consumed by ``DashboardPreference`` (for default layout) and by the dashboard
view + template (for conditional rendering and reorder within a group).

Groups map to the visual sections of the dashboard:
  - ``kpi``     : 4-column KPI card row
  - ``chart``   : full-width charts (stacked)
  - ``list``    : two-column lists / tables / timeline
  - ``health``  : lazy-loaded three-column health alerts
"""

GROUPS = ("kpi", "chart", "list", "health")

WIDGETS = [
    {"key": "kpi_total_horses",         "name": "Total Horses",                "group": "kpi"},
    {"key": "kpi_vaccinations_due",     "name": "Vaccinations Due",            "group": "kpi"},
    {"key": "kpi_unbilled_charges",     "name": "Unbilled Charges",            "group": "kpi"},
    {"key": "kpi_outstanding_invoices", "name": "Outstanding Invoices",        "group": "kpi"},
    {"key": "chart_revenue",            "name": "Revenue vs Costs chart",      "group": "chart"},
    {"key": "chart_capacity",           "name": "Site Capacity chart",         "group": "chart"},
    {"key": "pending_departures",       "name": "Pending Departures",          "group": "list"},
    {"key": "recent_activity",          "name": "Recent Activity timeline",    "group": "list"},
    {"key": "list_vaccinations_due",    "name": "Vaccinations Due (30 days)",  "group": "list"},
    {"key": "list_farrier_due",         "name": "Farrier Due (2 weeks)",       "group": "list"},
    {"key": "table_outstanding",        "name": "Outstanding Invoices table",  "group": "list"},
    {"key": "health_upcoming_dep",      "name": "Upcoming Departures alert",   "group": "health"},
    {"key": "health_ehv_due",           "name": "EHV Vaccinations Due",        "group": "health"},
    {"key": "health_egg_counts",        "name": "High Egg Counts",             "group": "health"},
    {"key": "health_vet_followups",     "name": "Vet Follow-ups",              "group": "health"},
]

WIDGETS_BY_KEY = {w["key"]: w for w in WIDGETS}

DEFAULT_LAYOUT = {
    w["key"]: {"visible": True, "order": i}
    for i, w in enumerate(WIDGETS)
}


def widgets_in_group(group):
    """Return the registry entries for a single group, in registry order."""
    return [w for w in WIDGETS if w["group"] == group]
