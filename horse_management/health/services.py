"""Breeding workflows that touch more than one model."""

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Horse, OwnershipShare

from .models import BreedingRecord, Covering, PregnancyScan


def record_foaling(record, *, foal_dob, foal_sex, foal_colour='', foal_name='',
                   foal_microchip='', foaling_notes='', existing_foal=None,
                   mare_rate_type=None, place_foal=False, foal_rate_type=None):
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

        # Yard board and billing from the date of birth. Both are opt-in so
        # a back-dated foaling never rewrites placement history by itself.
        mare_placement = mare.current_placement
        if mare_placement and (mare_rate_type or place_foal):
            from core.services import PlacementService
            if mare_rate_type and mare_rate_type != mare_placement.rate_type:
                PlacementService.move_horse(
                    mare, new_location=mare_placement.location, move_date=foal_dob,
                    new_owner=mare_placement.owner, new_rate_type=mare_rate_type,
                    expected_departure=mare_placement.expected_departure,
                    notes=f"Rate changed at foaling ({foal.name})",
                )
            if place_foal and foal_rate_type and not foal.current_placement:
                PlacementService.arrive_horse(
                    foal, owner=mare_placement.owner, location=mare_placement.location,
                    rate_type=foal_rate_type, arrival_date=foal_dob,
                    notes=f"Foaled at foot of {mare.name}",
                )
    return foal


def record_scan(record, *, scan_type, scan_date, result, notes='', vet=None):
    """Apply a scan result to an open breeding record and keep the history.

    ``scan_type`` is a PregnancyScan.ScanType value; ``result`` a
    PregnancyScan.Result value. In foal (or twins) confirms the pregnancy.
    Not in foal closes the record: Barren on the 14-day scan (the mare
    never held; add a covering to try again), Lost on any later scan.
    Inconclusive records the scan and changes nothing else. Returns the
    record's status afterwards.
    """
    if not record.is_active_pregnancy:
        raise ValidationError(
            'This breeding record is not an open pregnancy, so a scan '
            'cannot be recorded on it.'
        )
    if scan_type not in PregnancyScan.ScanType.values:
        raise ValidationError('Unknown scan type.')
    if result not in PregnancyScan.Result.values:
        raise ValidationError('Unknown scan result.')

    with transaction.atomic():
        PregnancyScan.objects.create(
            record=record, date=scan_date, scan_type=scan_type,
            result=result, notes=notes, vet=vet,
        )
        positive = result in (PregnancyScan.Result.IN_FOAL, PregnancyScan.Result.TWINS)
        if positive:
            if scan_type == PregnancyScan.ScanType.DAY_14:
                record.date_scanned_14_days = scan_date
            elif scan_type == PregnancyScan.ScanType.HEARTBEAT:
                record.date_scanned_heartbeat = scan_date
            record.status = BreedingRecord.Status.CONFIRMED
        elif result == PregnancyScan.Result.NOT_IN_FOAL:
            if scan_type == PregnancyScan.ScanType.DAY_14 or record.status == BreedingRecord.Status.COVERED:
                record.status = BreedingRecord.Status.BARREN
            else:
                record.status = BreedingRecord.Status.LOST
        record.save()
    return record.status


def add_covering(record, *, date, method='', stallion_name='', vet=None, notes=''):
    """Add a covering to a record and reopen it if a scan had closed it.

    The record's ``date_covered`` and due date follow the latest covering.
    A Barren record (negative 14-day scan) goes back to Covered: the scan
    stays in the history, the denormalised scan dates are cleared so the
    next scan is asked for again.
    """
    if not record.can_add_covering:
        raise ValidationError(
            f'{record.mare.name} is {record.get_status_display().lower()}; '
            'a covering can only be added while the mare is covered or barren.'
        )
    with transaction.atomic():
        covering = Covering.objects.create(
            record=record, date=date, method=method,
            stallion_name='' if stallion_name == record.stallion_name else stallion_name,
            vet=vet, notes=notes,
        )
        changed = []
        if record.status == BreedingRecord.Status.BARREN:
            record.status = BreedingRecord.Status.COVERED
            changed.append('status')
        # A covering after the recorded scans means those scans belong to the
        # previous attempt; the next scan is wanted again.
        if record.date_scanned_14_days and record.date_scanned_14_days <= date:
            record.date_scanned_14_days = None
            changed.append('date_scanned_14_days')
        if record.date_scanned_heartbeat and record.date_scanned_heartbeat <= date:
            record.date_scanned_heartbeat = None
            changed.append('date_scanned_heartbeat')
        if changed:
            record.save(update_fields=changed + ['updated_at'])
    record.refresh_from_db()
    return covering
