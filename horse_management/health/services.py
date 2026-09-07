"""Breeding workflows that touch more than one model."""

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Horse, OwnershipShare

from .models import BreedingRecord


def record_foaling(record, *, foal_dob, foal_sex, foal_colour='', foal_name='',
                   foal_microchip='', foaling_notes='', existing_foal=None):
    """Close an open breeding record as Born and give the foal a horse record.

    Creates the foal (dam = the mare, sire = the stallion name, the mare's
    ownership copied across, no passport yet) unless ``existing_foal`` is
    given, in which case that horse is linked and its parentage filled in
    where blank. Returns the foal.
    """
    if not record.can_record_foaling:
        raise ValidationError(
            'This breeding record is not an open pregnancy, so a foaling '
            'cannot be recorded on it.'
        )
    mare = record.mare
    with transaction.atomic():
        if existing_foal is not None:
            foal = existing_foal
            changed = []
            if not foal.date_of_birth:
                foal.date_of_birth = foal_dob
                changed.append('date_of_birth')
            if foal.dam_id is None:
                foal.dam = mare
                changed.append('dam')
            if not foal.dam_name:
                foal.dam_name = mare.name
                changed.append('dam_name')
            if not foal.sire_name:
                foal.sire_name = record.stallion_name
                changed.append('sire_name')
            if not foal.sex and foal_sex:
                foal.sex = foal_sex
                changed.append('sex')
            if not foal.color and foal_colour:
                foal.color = foal_colour
                changed.append('color')
            if changed:
                foal.save(update_fields=changed + ['updated_at'])
        else:
            foal = Horse.objects.create(
                name=foal_name.strip(),
                date_of_birth=foal_dob,
                sex=foal_sex,
                color=foal_colour,
                dam=mare,
                dam_name=mare.name,
                sire_name=record.stallion_name,
                breeding=f"By {record.stallion_name} out of {mare.name}",
                has_passport=False,
                is_active=True,
            )
            # A foal belongs to whoever owns the mare unless told otherwise.
            for share in mare.ownership_shares.all():
                OwnershipShare.objects.create(
                    horse=foal,
                    owner=share.owner,
                    share_percentage=share.share_percentage,
                    is_primary_contact=share.is_primary_contact,
                )

        record.foal = foal
        record.foal_dob = foal_dob
        record.foal_sex = foal_sex
        record.foal_colour = foal_colour
        record.foal_microchip = foal_microchip
        if foaling_notes:
            record.foaling_notes = (
                f"{record.foaling_notes}\n{foaling_notes}".strip()
                if record.foaling_notes else foaling_notes
            )
        record.status = BreedingRecord.Status.BORN
        record.save()
    return foal


def record_scan(record, *, scan_type, scan_date, result, notes=''):
    """Apply a scan result to an open breeding record.

    ``scan_type`` is ``'14_day'`` or ``'heartbeat'``; ``result`` is
    ``'in_foal'`` or ``'not_in_foal'``. In foal confirms the pregnancy.
    Not in foal closes the record: Barren on the 14-day scan (the mare
    never held), Lost on the heartbeat scan (she held, then lost it).
    Returns the record's new status.
    """
    if not record.is_active_pregnancy:
        raise ValidationError(
            'This breeding record is not an open pregnancy, so a scan '
            'cannot be recorded on it.'
        )
    if scan_type == '14_day':
        record.date_scanned_14_days = scan_date
    elif scan_type == 'heartbeat':
        record.date_scanned_heartbeat = scan_date
    else:
        raise ValidationError('Unknown scan type.')

    if result == 'in_foal':
        record.status = BreedingRecord.Status.CONFIRMED
    elif result == 'not_in_foal':
        record.status = (
            BreedingRecord.Status.BARREN if scan_type == '14_day'
            else BreedingRecord.Status.LOST
        )
    else:
        raise ValidationError('Unknown scan result.')

    if notes:
        line = f"{scan_date:%d %b %Y} scan: {notes}"
        record.foaling_notes = (
            f"{record.foaling_notes}\n{line}".strip() if record.foaling_notes else line
        )
    record.save()
    return record.status
