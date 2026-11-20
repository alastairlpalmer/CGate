"""Find the horse on the yard for an imported row (plan §6.4).

In this order:

1. Same microchip: the same horse.
2. Same UELN: the same horse.
3. Same registered name, suffix and foaling year: probably the same.
4. Same yard name only: shown as "possible", never chosen for the user.

Every match is shown on the review screen; nothing saves before a person
selects Apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.identity import normalise_microchip, normalise_ueln
from core.models import Horse

from .names import name_key


@dataclass
class HorseMatch:
    horse: Horse | None = None
    how: str = ''          # 'microchip', 'ueln', 'registered'
    note: str = ''         # for the review screen
    possible: list = field(default_factory=list)  # yard-name matches, never chosen

    @property
    def found(self):
        return self.horse is not None


def find_horse(*, microchip='', ueln='', registered_name='', country='', year=None,
               yard_name='') -> HorseMatch:
    chip = normalise_microchip(microchip)
    if chip:
        hits = list(Horse.objects.filter(microchip=chip)[:2])
        if len(hits) == 1:
            return HorseMatch(hits[0], 'microchip', f'Matches {hits[0].name} by microchip')
        if len(hits) > 1:
            return HorseMatch(note='This microchip is on more than one horse. Check it first.',
                              possible=hits)
    code = normalise_ueln(ueln)
    if code:
        hits = list(Horse.objects.filter(ueln=code)[:2])
        if len(hits) == 1:
            return HorseMatch(hits[0], 'ueln', f'Matches {hits[0].name} by UELN')
    key = name_key(registered_name)
    if key and country and year:
        hits = [h for h in Horse.objects.filter(country_of_birth=country.upper(),
                                                date_of_birth__year=year).exclude(registered_name='')
                if name_key(h.registered_name) == key]
        if len(hits) == 1:
            return HorseMatch(hits[0], 'registered',
                              f'Matches {hits[0].name} by registered name, country and year')
    possible = []
    if yard_name:
        possible = list(Horse.objects.filter(name__iexact=yard_name.strip())[:5])
    if registered_name and not possible:
        possible = [h for h in Horse.objects.exclude(registered_name='')
                    .filter(registered_name__iexact=registered_name.strip())[:5]]
    note = ''
    if possible:
        names = ', '.join(h.name for h in possible)
        note = f'Possible match: {names}.'
    return HorseMatch(note=note, possible=possible)
