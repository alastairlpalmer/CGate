"""Day turnout screens (docs/BARNS_AND_STABLES_PLAN.md, phase 3).

One form, reached two ways: from a horse (its own turnout) and from a
barn (a tick list of everything stabled in it). Changing where a horse
grazes is a horse move, so it needs ``horses`` at full access and enough
of ``locations`` to see the yard — the rule for stables.
"""

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from ..forms import TurnoutForm
from ..models import Horse, Location
from ..turnout import TurnoutService, lives_in_a_barn, open_turnout
from ._next import safe_next
from ._popup import is_popup_request, popup_saved_response
from .stables import CLASH, horse_moves


def _require_barns():
    if not settings.BARNS_ENABLED:
        raise Http404


def _done(request, back):
    return popup_saved_response() if is_popup_request(request) else redirect(back)


def _apply(horses, location, day):
    """Set (or, with no location, stop) the turnout of each horse.

    All or nothing: one horse that cannot be changed — a date before it
    arrived — leaves the rest as they were, with the reason shown.
    Returns how many actually changed.
    """
    changed = 0
    with transaction.atomic():
        for horse in horses:
            before = open_turnout(horse)
            if location is None:
                if TurnoutService.stop(horse, day) is not None:
                    changed += 1
            else:
                after = TurnoutService.start(horse, location, day)
                if before is None or before.pk != after.pk:
                    changed += 1
    return changed


def _serve(request, *, barn, horses, single, back, initial, title_horse=None):
    form = TurnoutForm(
        request.POST or None, barn=barn, horses=horses, single=single, initial=initial,
    )
    if request.method == 'POST' and form.is_valid():
        chosen = list(form.cleaned_data['horses'])
        location = form.cleaned_data['location']
        try:
            changed = _apply(chosen, location, form.cleaned_data['day'])
        except ValidationError as e:
            form.add_error(None, e)
        except IntegrityError:
            form.add_error(None, CLASH)
        else:
            who = title_horse.name if title_horse else f"{changed} horse{'' if changed == 1 else 's'}"
            if not changed:
                messages.info(request, "Nothing to change: the turnout was already as you set it.")
            elif location is None:
                messages.success(request, f"Turnout stopped for {who}.")
            else:
                verb = 'goes' if title_horse or changed == 1 else 'go'
                messages.success(request, f"{who} {verb} out in {location.name}.")
            return _done(request, back)

    if not is_popup_request(request):
        return redirect(back)
    return render(request, 'locations/partials/turnout_form.html', {
        'form': form,
        'single': single,
    })


@horse_moves
def horse_turnout(request, pk):
    """Set, change or stop one horse's turnout."""
    _require_barns()
    horse = get_object_or_404(Horse, pk=pk)
    back = safe_next(request, '') or reverse('horse_detail', args=[horse.pk])
    barn = lives_in_a_barn(horse)
    if barn is None:
        messages.error(request, f"{horse.name} does not live in a barn, so it has no turnout to set.")
        return _done(request, back)
    current = open_turnout(horse)
    return _serve(
        request, barn=barn, horses=Horse.objects.filter(pk=horse.pk), single=True, back=back,
        title_horse=horse,
        initial={
            'horses': [horse.pk],
            'location': str(current.location_id) if current else '',
            'day': timezone.localdate(),
        },
    )


@horse_moves
def barn_turnout(request, pk):
    """Set, change or stop the turnout of the horses stabled in one barn."""
    _require_barns()
    barn = get_object_or_404(
        Location.objects.active(), pk=pk, location_type=Location.LocationType.BARN)
    back = reverse('location_detail', args=[barn.pk])
    horses = Horse.objects.filter(
        placements__location=barn, placements__end_date__isnull=True, is_active=True,
    ).distinct().order_by('name')
    return _serve(
        request, barn=barn, horses=horses, single=False, back=back,
        initial={
            'horses': [horse.pk for horse in horses],
            'day': timezone.localdate(),
        },
    )
