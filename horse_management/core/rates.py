"""The right rate for where a horse is (docs/BARNS_AND_STABLES_PLAN.md, phase 2b).

A rate type can say where it belongs — fields, barns, or both
(``RateType.applies_to``). From that one fact:

* ``suggest_rate`` finds the rate to offer when a horse moves between a
  field and a barn. It only ever *offers*: the move form shows what it
  picked and the person saving decides.
* ``mismatched_placements`` finds every horse on a rate that belongs
  somewhere else, whichever way it got there — a bulk move, a foaling, an
  edited placement, the admin. The dashboard lists them, so a wrong rate
  cannot sit unseen until the invoice.

A rate type left on "Fields and barns" takes no part in either. A yard
that sets nothing sees nothing new.

Nothing here reads or prints a price: a rate's name shows to everyone,
its price only to those who may see charges.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Location, Placement, RateType

ANY = RateType.AppliesTo.ANY

KIND_WORDS = {
    Location.LocationType.FIELD: 'a field',
    Location.LocationType.BARN: 'a barn',
}


def rate_mismatch(rate_type, location) -> bool:
    """True when the rate is for the other type of location."""
    if rate_type is None or location is None:
        return False
    return rate_type.applies_to not in (ANY, location.location_type)


def mismatch_text(rate_type, location) -> str:
    """"In a barn on Field care" — for a list of horses to check."""
    return f"In {KIND_WORDS[location.location_type]} on {rate_type.name}"


@dataclass
class RateSuggestion:
    """What the move form says when a move crosses between field and barn.

    ``rate`` is the rate to offer, or None when there is no single obvious
    one. ``options`` are the active rates that belong at the destination,
    for the form to point at when it cannot choose.
    """

    current: RateType
    destination: Location
    rate: RateType | None
    options: list

    @property
    def text(self) -> str:
        origin = (
            Location.LocationType.FIELD
            if self.destination.location_type == Location.LocationType.BARN
            else Location.LocationType.BARN
        )
        lead = (
            f"This horse moves from {KIND_WORDS[origin]} to "
            f"{KIND_WORDS[self.destination.location_type]}. It is on {self.current.name}."
        )
        if self.rate is not None:
            # True whether or not the form went on to set it: once the
            # person has chosen a rate themselves the form leaves it be.
            return (
                f"{lead} Suggested rate: {self.rate.name}. "
                f"To keep {self.current.name}, choose \"Keep the current rate\"."
            )
        if self.options:
            names = ', '.join(rate.name for rate in self.options)
            return f"{lead} Choose the new rate: {names}."
        return f"{lead} Check the rate."

    def as_event(self) -> dict:
        """The payload the move form's script acts on."""
        return {'rate': self.rate.pk if self.rate else None, 'text': self.text}


def _fits(rate_type, location) -> bool:
    return rate_type is not None and rate_type.is_active and not rate_mismatch(rate_type, location)


def suggest_rate(horse, current_placement, new_location) -> RateSuggestion | None:
    """The rate to offer for a move, or None when there is nothing to say.

    Nothing to say unless the horse's present rate belongs to the other
    type of location. Then, in order:

    1. the rate this horse last had somewhere of the new type;
    2. the rate the same owner's other horses all have there now — an
       owner on an agreed rate has it for every horse;
    3. the only active rate that belongs to the new type.

    Each must be active and must itself fit the destination. With none of
    them the suggestion still comes back, with ``rate`` None: the form
    says the rate needs choosing and does not guess.
    """
    if current_placement is None or new_location is None:
        return None
    current = current_placement.rate_type
    if not rate_mismatch(current, new_location):
        return None

    kind = new_location.location_type
    options = list(
        RateType.objects.filter(is_active=True, applies_to=kind).order_by('name')
    )

    def suggestion(rate):
        return RateSuggestion(current=current, destination=new_location, rate=rate, options=options)

    last = (
        Placement.objects.filter(horse=horse, location__location_type=kind)
        .exclude(pk=current_placement.pk)
        .select_related('rate_type').order_by('-start_date', '-pk').first()
    )
    if last is not None and _fits(last.rate_type, new_location):
        return suggestion(last.rate_type)

    owner_rates = set(
        Placement.objects.filter(
            owner_id=current_placement.owner_id, end_date__isnull=True,
            location__location_type=kind,
        ).exclude(horse=horse).values_list('rate_type_id', flat=True)
    )
    if len(owner_rates) == 1:
        rate = RateType.objects.filter(pk=owner_rates.pop()).first()
        if _fits(rate, new_location):
            return suggestion(rate)

    if len(options) == 1:
        return suggestion(options[0])
    return suggestion(None)


def mismatched_placements():
    """Open placements of active horses on a rate that belongs elsewhere."""
    from django.db.models import F

    return (
        Placement.objects.filter(end_date__isnull=True, horse__is_active=True)
        .exclude(rate_type__applies_to=ANY)
        .exclude(rate_type__applies_to=F('location__location_type'))
        .select_related('horse', 'location', 'rate_type', 'owner')
    )
