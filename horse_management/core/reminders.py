"""Which kinds of reminder the yard wants on its dashboard.

Some jobs are easy to forget: a vaccination, a foal's due date. Others
the yard sees every day and does not need to be told about: a horse
that needs the farrier. Every reminder a yard does not need makes the
ones it does need harder to see, so each kind can be switched off in
Settings, and the farrier is off until a yard asks for it.

A switched-off kind leaves the dashboard (Needs action, Next 14 days,
the headline count) and the dots on the horse list. It stays on its own
page: the Farrier page still lists who is due.
"""

from __future__ import annotations

# (key, name, what it does, the feature area it belongs to). The key is
# the collector's name in core.dashboard.attention.COLLECTORS; a group
# may cover more than one collector.
REMINDERS = (
    ('vaccination', 'Vaccinations', 'A vaccination is due or overdue.', 'health'),
    ('breeding', 'Breeding and foaling', 'Foal due dates, scans, EHV doses and newborn checks.', 'breeding'),
    ('vet', 'Vet follow-ups', 'A vet asked to see the horse again.', 'health'),
    ('worming', 'Worming', 'A worming dose is due.', 'health'),
    ('egg_count', 'Egg counts', 'A high egg count that has no treatment after it.', 'health'),
    ('farrier', 'Farrier', 'A horse is due the farrier. The Farrier page lists these either way.', 'health'),
    ('document', 'Documents', 'A passport, insurance or other document is about to expire.', 'horses'),
    ('departure', 'Departures', 'A horse is due to leave, or has left and needs confirming.', 'horses'),
    ('invoice', 'Invoices', 'An invoice is overdue or waiting to be sent.', 'invoices'),
    ('feed', 'Feed stock', 'Feed is running low.', 'feed'),
)

REMINDER_KEYS = frozenset(key for key, *_ in REMINDERS)

# Off until a yard switches them on.
DEFAULT_MUTED = ('farrier',)

# Collectors each key switches off.
COLLECTORS_OF = {
    'departure': ('departure', 'departure_expected'),
}


def default_muted():
    """Default for BusinessSettings.muted_reminders (a callable for the field)."""
    return list(DEFAULT_MUTED)


def muted_keys(business=None):
    """The reminder keys this yard has switched off."""
    if business is None:
        from .models import BusinessSettings
        business = BusinessSettings.get_settings()
    stored = business.muted_reminders
    if not isinstance(stored, list):
        return set(DEFAULT_MUTED)
    return {key for key in stored if key in REMINDER_KEYS}


def muted_collectors(business=None):
    """The attention collectors that must not run for the dashboard."""
    collectors = set()
    for key in muted_keys(business):
        collectors.update(COLLECTORS_OF.get(key, (key,)))
    return collectors


def set_reminder(key, on, business=None):
    """Switch one kind on or off. Returns the yard's settings row."""
    if key not in REMINDER_KEYS:
        raise KeyError(key)
    if business is None:
        from .models import BusinessSettings
        business = BusinessSettings.get_settings()
    muted = muted_keys(business)
    if on:
        muted.discard(key)
    else:
        muted.add(key)
    business.muted_reminders = sorted(muted)
    business.save(update_fields=['muted_reminders', 'updated_at'])
    return business


def reminder_rows(user, business=None):
    """``[{key, name, help, on}]`` for the kinds this user's role can see."""
    from .permissions import access_map
    levels = access_map(user)
    muted = muted_keys(business)
    return [
        {'key': key, 'name': name, 'help': text, 'on': key not in muted}
        for key, name, text, feature in REMINDERS
        if levels.get(feature, 'hidden') != 'hidden'
    ]
