"""Data behind the home dashboard.

Each module here is a set of plain functions that take the requesting user
(for feature gating), an optional site name (the dashboard's site switch)
and ``today``, and return plain Python data for the templates. The views in
``core.views.dashboard`` stay thin, and the tests hit these functions
directly.

- ``attention``  what needs doing, as one list of items across every area
- ``upcoming``   the next 14 days and the visits worth booking together
- ``board``      sites and their locations: occupancy, land use, rest days
- ``money``      the month's billing position
- ``activity``   what changed, as a real chronological log
- ``breeding``   mares in foal, for the seasonal block
"""
