"""What a practice reads from the yard's records (docs/C_VET_PLAN.md, section 3).

Shared data only: who pays for a horse, and when its vaccinations are
due. Each function takes horses that ``cvet.access.horses_for`` has
already let the practice see. Nothing here writes to a yard record.
"""

from collections import defaultdict

from core.models import OwnershipShare, Placement
from health.models import Vaccination

from . import access


def payers_by_horse(horses):
    """{horse id: [{'owner_id', 'name', 'share'}]}: who pays, and how much of it.

    The same rule as the yard's own invoices: the ownership shares when
    the horse has them, else the owner on its open placement, at 100%.
    A horse with neither has an empty list.
    """
    ids = [horse.pk for horse in horses]
    payers = defaultdict(list)
    for share in (
        OwnershipShare.objects.filter(horse_id__in=ids).select_related('owner')
        .order_by('horse_id', '-is_primary_contact', '-share_percentage', 'pk')
    ):
        payers[share.horse_id].append({
            'owner_id': share.owner_id,
            'name': share.owner.name,
            'share': f'{share.share_percentage:.2f}',
        })
    missing = [pk for pk in ids if pk not in payers]
    if missing:
        for placement in (
            Placement.objects.filter(horse_id__in=missing, end_date__isnull=True)
            .select_related('owner').order_by('horse_id', '-start_date')
        ):
            if placement.owner_id and placement.horse_id not in payers:
                payers[placement.horse_id].append({
                    'owner_id': placement.owner_id,
                    'name': placement.owner.name,
                    'share': '100.00',
                })
    return payers


def locations_by_horse(horses):
    """{horse id: field or stable name}, to find the horse at the yard."""
    locations = {}
    for placement in (
        Placement.objects.filter(horse__in=horses, end_date__isnull=True)
        .select_related('location').order_by('horse_id', '-start_date')
    ):
        locations.setdefault(placement.horse_id, placement.location.name)
    return locations


def due_by_horse(practice, horses):
    """{horse id: [{'kind', 'label', 'date'}]}: what the vet may have to do.

    * Vaccinations, from the yard's records: the newest dose of each type
      and the date its next dose is due.
    * Follow-ups, from the practice's own notes: the date on the horse's
      newest note. A newer note with no date clears it: the vet saw the
      horse again and asked for nothing more.
    """
    ids = [horse.pk for horse in horses]
    due = defaultdict(list)

    seen = set()
    for vaccination in (
        Vaccination.objects.filter(horse_id__in=ids).select_related('vaccination_type')
        .order_by('horse_id', 'vaccination_type_id', '-date_given', '-pk')
    ):
        key = (vaccination.horse_id, vaccination.vaccination_type_id)
        if key in seen:
            continue
        seen.add(key)
        if vaccination.next_due_date:
            due[vaccination.horse_id].append({
                'kind': 'vaccination',
                'label': vaccination.vaccination_type.name,
                'date': vaccination.next_due_date.isoformat(),
            })

    newest = set()
    for note in (
        access.current_notes_for(practice).filter(horse_id__in=ids)
        .order_by('horse_id', '-seen_on', '-written_at').only('horse_id', 'follow_up_date')
    ):
        if note.horse_id in newest:
            continue
        newest.add(note.horse_id)
        if note.follow_up_date:
            due[note.horse_id].append({
                'kind': 'follow_up',
                'label': 'Recheck',
                'date': note.follow_up_date.isoformat(),
            })

    for items in due.values():
        items.sort(key=lambda item: item['date'])
    return due
