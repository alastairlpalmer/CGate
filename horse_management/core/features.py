"""Registry of gateable feature areas for the Role Suite.

Single source of truth for feature keys, labels, matrix grouping, and which
access levels each feature supports. Consumed by ``Role.resolved_access()``
(defaults + clamping), the enforcement layer (``core.permissions``), the
role matrix editor, and the ``feature_access`` template context.

Access levels form an ordered ladder:
  - ``hidden`` : feature absent from nav; direct URLs redirect away
  - ``view``   : read pages render, write affordances hidden, POSTs denied
  - ``full``   : everything

Features with ``supports_view: False`` are binary (Hidden / Full access) —
a "view only" state is meaningless for them (e.g. the Settings forms or the
Xero connect/push actions). A stored ``view`` on a binary feature clamps to
``hidden``.
"""

LEVEL_HIDDEN = "hidden"
LEVEL_VIEW = "view"
LEVEL_FULL = "full"

LEVELS = (LEVEL_HIDDEN, LEVEL_VIEW, LEVEL_FULL)
LEVEL_ORDER = {LEVEL_HIDDEN: 0, LEVEL_VIEW: 1, LEVEL_FULL: 2}

LEVEL_LABELS = {
    LEVEL_HIDDEN: "Hidden",
    LEVEL_VIEW: "View only",
    LEVEL_FULL: "Full access",
}

GROUPS = ("Overview", "Manage", "Finance", "Admin")

FEATURES = [
    {
        "key": "dashboard",
        "label": "Dashboard",
        "group": "Overview",
        "supports_view": False,
        "description": "Home dashboard, quick find and health alerts.",
    },
    {
        "key": "horses",
        "label": "Horses",
        "group": "Manage",
        "supports_view": True,
        "description": "Horse records, photos, documents, ownership and arrivals/departures.",
    },
    {
        "key": "owners",
        "label": "Owners",
        "group": "Manage",
        "supports_view": True,
        "description": "Owner contact records and their horses.",
    },
    {
        "key": "locations",
        "label": "Locations",
        "group": "Manage",
        "supports_view": True,
        "description": "Fields and stables, placements, moves and usage changes.",
    },
    {
        "key": "health",
        "label": "Health",
        "group": "Manage",
        "supports_view": True,
        "description": "Vaccinations, farrier, worming, egg counts, conditions and vet visits.",
    },
    {
        "key": "breeding",
        "label": "Breeding",
        "group": "Manage",
        "supports_view": True,
        "description": "Breeding and foaling records.",
    },
    {
        "key": "feed",
        "label": "Feed",
        "group": "Manage",
        "supports_view": True,
        "description": "Feed stock levels and feeding-out records.",
    },
    {
        "key": "finances",
        "label": "Finances overview",
        "group": "Finance",
        "supports_view": False,
        "description": "The read-only revenue and capacity overview page.",
    },
    {
        "key": "invoices",
        "label": "Invoices",
        "group": "Finance",
        "supports_view": True,
        "description": "Invoices, payments, owner statements and aged debtors.",
    },
    {
        "key": "costs",
        "label": "Costs",
        "group": "Finance",
        "supports_view": True,
        "description": "Yard running costs.",
    },
    {
        "key": "charges",
        "label": "Charges",
        "group": "Finance",
        "supports_view": True,
        "description": "Billable extra charges (vet, farrier, feed…).",
    },
    {
        "key": "xero",
        "label": "Xero integration",
        "group": "Finance",
        "supports_view": True,
        "description": "View gives read-only sync status on invoices; "
                       "full also connects/disconnects Xero and pushes invoices.",
    },
    {
        "key": "settings",
        "label": "Business settings",
        "group": "Admin",
        "supports_view": False,
        "description": "Business details, rate types, vaccination types, providers and data management.",
    },
    {
        "key": "users",
        "label": "Users & Roles",
        "group": "Admin",
        "supports_view": False,
        "description": "Manage login accounts, roles and their access levels.",
    },
]

FEATURES_BY_KEY = {f["key"]: f for f in FEATURES}

# Fail-safe: an empty/unknown access map resolves to nothing visible.
DEFAULT_LEVELS = {f["key"]: LEVEL_HIDDEN for f in FEATURES}

ALL_FULL = {f["key"]: LEVEL_FULL for f in FEATURES}


def features_in_group(group):
    """Return the registry entries for a single group, in registry order."""
    return [f for f in FEATURES if f["group"] == group]


def clamp_level(feature_key, level):
    """Coerce a stored level to one this feature legally supports."""
    if level not in LEVEL_ORDER:
        return DEFAULT_LEVELS[feature_key]
    if level == LEVEL_VIEW and not FEATURES_BY_KEY[feature_key]["supports_view"]:
        return LEVEL_HIDDEN
    return level
