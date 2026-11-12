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
        # Lock and read again: two saves at once (a double-click) both
        # passed the check above and each created a foal.
        locked = BreedingRecord.objects.select_for_update().get(pk=record.pk)
        if not locked.can_record_foaling:
            raise ValidationError('A foaling has already been recorded on this breeding record.')
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
                microchip=foal_microchip or '',
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
                # A foal at foot is in the mare's stable. No question to
                # ask: sharing is the whole point of the pair.
                from core.stables import StableService, open_stay
                mare_stay = open_stay(mare)
                if mare_stay is not None:
                    StableService.assign(
                        foal, mare_stay.stable,
                        max(foal_dob, mare_stay.start_date), share=True,
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
        # Scan dates are counted from the latest covering, so the scan
        # reminders already sent were for the previous attempt. Left marked
        # as sent, the new covering's 14-day and heartbeat scan reminders
        # never went out.
        sent = record.sent_reminder_keys
        if sent & {'scan14', 'heartbeat'}:
            record.breeding_reminders_sent = ','.join(sorted(sent - {'scan14', 'heartbeat'}))
            changed.append('breeding_reminders_sent')
        if changed:
            record.save(update_fields=changed + ['updated_at'])
    record.refresh_from_db()
    return covering


def _move_stay_boundary(horse, old_day, new_day):
    """Move the stay that started on ``old_day`` to start on ``new_day``.

    The stay before it (ending the day before) moves with it, so there is
    no gap or overlap. Used when a foaling date is corrected: the foal's
    stay and the mare's foaling-rate stay began on the old date, so the
    foal was billed from before it was born.
    """
    from datetime import timedelta

    from core.models import Placement

    stay = Placement.objects.filter(horse=horse, start_date=old_day).first()
    if stay is None:
        return
    before = Placement.objects.filter(horse=horse, end_date=old_day - timedelta(days=1)).first()
    if (stay.end_date and new_day > stay.end_date) or (before and new_day <= before.start_date):
        raise ValidationError(
            f"{horse.name}'s stays changed after the foaling, so the date of "
            "birth cannot be moved this far. Correct the stays first."
        )
    # Shorten before lengthening, so the two never overlap.
    if before and new_day < old_day:
        before.end_date = new_day - timedelta(days=1)
        before.save()
    stay.start_date = new_day
    stay.save()
    if before and new_day > old_day:
        before.end_date = new_day - timedelta(days=1)
        before.save()


def update_foal_details(record, *, foal_name, foal_dob, foal_sex, foal_colour='',
                        foal_microchip='', foaling_notes=''):
    """Edit the foal's birth details on the record and its horse record."""
    with transaction.atomic():
        old_dob = record.foal_dob
        if old_dob and foal_dob and foal_dob != old_dob:
            if record.foal is not None:
                _move_stay_boundary(record.foal, old_dob, foal_dob)
            _move_stay_boundary(record.mare, old_dob, foal_dob)
        record.foal_dob = foal_dob
        record.foal_sex = foal_sex
        record.foal_colour = foal_colour
        record.foal_microchip = foal_microchip
        record.foaling_notes = foaling_notes
        record.save()
        foal = record.foal
        if foal is not None:
            foal.name = foal_name.strip() or foal.name
            foal.date_of_birth = foal_dob
            foal.sex = foal_sex
            foal.color = foal_colour
            # The form says the record and the horse are kept in step.
            foal.microchip = foal_microchip or foal.microchip
            foal.save(update_fields=['name', 'date_of_birth', 'sex', 'color', 'microchip', 'updated_at'])
    return record


def record_weaning(record, *, weaned_date, foal_location=None, foal_rate_type=None,
                   mare_rate_type=None, weaning_notes=''):
    """Wean the foal: date it, and optionally move the foal and re-rate the
    mare and foal from that date. Board changes are opt-in so a back-dated
    weaning on an old record changes nothing but the date."""
    if not record.can_wean:
        raise ValidationError(
            'Weaning can only be recorded on a foal that has been born and not yet weaned.'
        )
    foal = record.foal
    mare = record.mare
    with transaction.atomic():
        record.weaned_date = weaned_date
        record.weaning_notes = weaning_notes
        record.save(update_fields=['weaned_date', 'weaning_notes', 'updated_at'])

        from core.services import PlacementService
        if foal is not None:
            foal_placement = foal.current_placement
            if foal_location is not None or (foal_rate_type and foal_placement):
                location = foal_location or foal_placement.location
                rate = foal_rate_type or (foal_placement.rate_type if foal_placement else None)
                owner = foal_placement.owner if foal_placement else foal.primary_owner
                if owner is None and not foal_placement and mare.current_placement:
                    # A foal of a mare billed through her stay alone has no
                    # shares: bill it the way the mare is billed.
                    owner = mare.current_placement.owner
                if not foal_placement and (owner is None or rate is None):
                    # Saying "weaned" and leaving the foal unplaced (and
                    # never billed) was a silent failure.
                    raise ValidationError(
                        'The foal could not be placed: it has no owner. Add an '
                        'owner on the foal page, then record the weaning.'
                        if owner is None else
                        'Pick the rate for the foal at its new location.'
                    )
                if foal_placement:
                    PlacementService.move_horse(
                        foal, new_location=location, move_date=weaned_date,
                        new_owner=owner, new_rate_type=rate,
                        notes=f"Weaned from {mare.name}",
                    )
                else:
                    PlacementService.arrive_horse(
                        foal, owner=owner, location=location, rate_type=rate,
                        arrival_date=weaned_date, notes=f"Weaned from {mare.name}",
                    )
        mare_placement = mare.current_placement
        if mare_rate_type and mare_placement and mare_rate_type != mare_placement.rate_type:
            PlacementService.move_horse(
                mare, new_location=mare_placement.location, move_date=weaned_date,
                new_owner=mare_placement.owner, new_rate_type=mare_rate_type,
                expected_departure=mare_placement.expected_departure,
                notes="Rate changed at weaning",
            )
    return record


def foal_milestones(record):
    """The foal's first-year milestones as [(key, label, done, date, hint)].

    Read from what the yard already records (vet visits, farrier, passport,
    vaccinations) plus the weaning date, so nothing has to be ticked twice.
    """
    from .models import FarrierVisit, Vaccination, VetVisit

    foal = record.foal
    dob = record.foal_dob
    milestones = []
    if foal is None or dob is None:
        return milestones

    def first(qs, field):
        return qs.order_by(field).values_list(field, flat=True).first()

    vet = first(VetVisit.objects.filter(horse=foal, date__gte=dob), 'date')
    milestones.append(('vet_check', 'Newborn vet check', vet is not None, vet,
                       'IgG and first examination, within a day of birth'))
    passport_done = bool(foal.has_passport)
    milestones.append(('passport', 'Passport & microchip', passport_done,
                       None, f'Due by {Vaccination._add_months(dob, 6):%d %b %Y}'))
    farrier = first(FarrierVisit.objects.filter(horse=foal, date__gte=dob), 'date')
    milestones.append(('farrier', 'First farrier visit', farrier is not None, farrier,
                       'Feet trimmed from about four to six weeks'))
    vax = first(Vaccination.objects.filter(horse=foal, date_given__gte=dob), 'date_given')
    milestones.append(('vaccination', 'First vaccination', vax is not None, vax,
                       'Primary course from about five to six months'))
    milestones.append(('weaned', 'Weaned', record.is_weaned, record.weaned_date,
                       f'Usually from {record.weaning_due:%b %Y}' if record.weaning_due else ''))
    return milestones
