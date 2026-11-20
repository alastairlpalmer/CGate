"""Returning horses: find the old record on New Arrival.

When a horse comes back, staff used New Arrival and the app made a second
record, so the horse's history was split in two. New Arrival now looks for
the old record first, with the identity plan's matching rules
(docs/HORSE_IDENTITY_AND_PEDIGREE_PLAN.md §6.4, ``pedigree.matching``):

1. Same microchip: the same horse. New Arrival offers "Bring it back" and
   asks for one more click before it makes a second record.
2. No microchip match: horses with the same name are shown as "possible"
   (the same foaling date first). A person chooses; a name alone never
   joins two records.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Q

from .models import Horse

# How many "same name" horses to show.
POSSIBLE_LIMIT = 5


@dataclass
class Returning:
    chip: str = ''                 # the scanned or typed chip, normalised
    by_chip: Horse | None = None   # the one horse with this chip
    chip_many: list = field(default_factory=list)  # the chip is on more than one horse
    possible: list = field(default_factory=list)   # same name; never chosen for the user

    @property
    def found(self):
        return bool(self.by_chip or self.chip_many or self.possible)

    @property
    def chip_matches(self):
        """True when New Arrival must ask before it makes a second record."""
        return bool(self.by_chip or self.chip_many)


def _with_state(horse):
    """Add ``here`` (the open stay, or None) and ``left`` (the last ended
    stay, or None) for the panel's "on the yard now" or "left on …" line."""
    stays = list(horse.placements.select_related('location').order_by('-start_date', '-pk')[:5])
    horse.here = next((p for p in stays if p.end_date is None), None)
    horse.left = None if horse.here else next(
        iter(sorted((p for p in stays if p.end_date), key=lambda p: p.end_date, reverse=True)), None)
    return horse


def find_returning(*, microchip='', name='', date_of_birth=None) -> Returning:
    """The old record (or records) a New Arrival may be."""
    from pedigree.matching import find_horse

    from .identity import normalise_microchip

    found = Returning(chip=normalise_microchip(microchip or ''))
    if found.chip:
        match = find_horse(microchip=found.chip)
        if match.horse is not None:
            found.by_chip = _with_state(match.horse)
            return found
        if match.possible:
            found.chip_many = [_with_state(h) for h in match.possible]
            return found

    name = (name or '').strip()
    if name:
        hits = list(
            Horse.objects.filter(Q(name__iexact=name) | Q(registered_name__iexact=name))
            .order_by('-is_active', 'name')[:POSSIBLE_LIMIT * 2])
        if date_of_birth:
            # The same foaling date first, then the same year, then the rest.
            hits.sort(key=lambda h: (h.date_of_birth != date_of_birth,
                                     getattr(h.date_of_birth, 'year', None) != date_of_birth.year))
        found.possible = [_with_state(h) for h in hits[:POSSIBLE_LIMIT]]
    return found
