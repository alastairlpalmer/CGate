"""Breeding and foaling defects from the third bug hunt.

Each test states the right behaviour; all but the page check failed
before the fix.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin
from health.models import BreedingRecord, Covering, PregnancyScan
from health.services import (add_covering, record_foaling, record_scan,
                             record_weaning, update_foal_details)

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.client.force_login(make_admin())
        self.owner = Owner.objects.create(name='Mrs Fox')
        self.field = Location.objects.create(name='Front field', site='Main')
        self.paddock = Location.objects.create(name='Weaning paddock', site='Main')
        self.rate = RateType.objects.create(name='Mare and foal', daily_rate=11)
        self.foal_rate = RateType.objects.create(name='Foal at foot', daily_rate=5)
        self.mare = Horse.objects.create(name='Vivian', sex='mare')
        self.record = BreedingRecord.objects.create(
            mare=self.mare, stallion_name='Hip Hop',
            date_covered=self.today - timedelta(days=345), status='confirmed',
        )


class P1DoubleFoaling(Base):
    def test_double_submit_creates_two_foals(self):
        a = BreedingRecord.objects.get(pk=self.record.pk)
        b = BreedingRecord.objects.get(pk=self.record.pk)
        record_foaling(a, foal_dob=self.today, foal_sex='colt', foal_name='Bob')
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            record_foaling(b, foal_dob=self.today, foal_sex='colt', foal_name='Bob')
        self.assertEqual(Horse.objects.filter(dam=self.mare).count(), 1)


class P2Microchip(Base):
    def test_microchip_not_on_horse(self):
        foal = record_foaling(self.record, foal_dob=self.today, foal_sex='colt',
                              foal_name='Bob', foal_microchip='985000111')
        foal.refresh_from_db()
        self.assertEqual(foal.microchip, '985000111')

    def test_microchip_edit_not_on_horse(self):
        foal = record_foaling(self.record, foal_dob=self.today, foal_sex='colt', foal_name='Bob')
        self.record.refresh_from_db()
        update_foal_details(self.record, foal_name='Bob', foal_dob=self.today, foal_sex='colt',
                            foal_microchip='985000222')
        foal.refresh_from_db()
        self.assertEqual(foal.microchip, '985000222')


class P3WeanNoOwner(Base):
    """Mare billed via placement only (no OwnershipShare)."""
    def test_wean_move_silently_does_nothing(self):
        Placement.objects.create(horse=self.mare, owner=self.owner, location=self.field,
                                 rate_type=self.rate, start_date=self.today - timedelta(days=100))
        dob = self.today - timedelta(days=5)
        foal = record_foaling(self.record, foal_dob=dob, foal_sex='colt', foal_name='Bob')
        self.record.refresh_from_db()
        self.client.post(reverse('breeding_wean', args=[self.record.pk]), {
            'weaned_date': self.today.isoformat(), 'foal_location': self.paddock.pk,
            'foal_rate_type': self.foal_rate.pk,
        })
        self.record.refresh_from_db()
        self.assertIsNotNone(self.record.weaned_date)
        self.assertTrue(foal.placements.exists(), 'foal not placed although weaning said moved')


class P4DobEditPlacement(Base):
    def test_dob_edit_leaves_placement_on_old_date(self):
        OwnershipShare.objects.create(horse=self.mare, owner=self.owner, share_percentage=100, is_primary_contact=True)
        Placement.objects.create(horse=self.mare, owner=self.owner, location=self.field,
                                 rate_type=self.rate, start_date=self.today - timedelta(days=100))
        wrong = self.today - timedelta(days=10)
        foal = record_foaling(self.record, foal_dob=wrong, foal_sex='colt', foal_name='Bob',
                              place_foal=True, foal_rate_type=self.foal_rate)
        self.record.refresh_from_db()
        right = self.today - timedelta(days=3)
        resp = self.client.post(reverse('breeding_foal_edit', args=[self.record.pk]), {
            'foal_name': 'Bob', 'foal_dob': right.isoformat(), 'foal_sex': 'colt',
        })
        self.assertEqual(resp.status_code, 302)
        foal.refresh_from_db()
        self.assertEqual(foal.date_of_birth, right)
        self.assertGreaterEqual(foal.placements.get().start_date, right,
                                'foal billed from before its corrected birth date')


class P5CoveringDeleteUndo(Base):
    def test_undo_recover_leaves_barren_mare_covered(self):
        mare = Horse.objects.create(name='Barren mare', sex='mare')
        rec = BreedingRecord.objects.create(mare=mare, stallion_name='X',
                                            date_covered=self.today - timedelta(days=20))
        record_scan(rec, scan_type='14_day', scan_date=self.today - timedelta(days=5), result='not_in_foal')
        rec.refresh_from_db()
        self.assertEqual(rec.status, 'barren')
        cov = add_covering(rec, date=self.today - timedelta(days=2))
        rec.refresh_from_db()
        self.assertEqual(rec.status, 'covered')
        self.client.post(reverse('breeding_covering_delete', args=[cov.pk]))
        rec.refresh_from_db()
        self.assertEqual(rec.status, 'barren')

    def test_delete_covering_on_born_record_moves_dates(self):
        Covering.objects.create(record=self.record, date=self.today - timedelta(days=350))
        record_foaling(self.record, foal_dob=self.today, foal_sex='colt', foal_name='Bob')
        self.record.refresh_from_db()
        latest = self.record.coverings.order_by('-date').first()
        self.client.post(reverse('breeding_covering_delete', args=[latest.pk]))
        self.record.refresh_from_db()
        self.assertEqual(self.record.coverings.count(), 2, 'covering deleted on a closed born record')


class P6EditMultiCovering(Base):
    def test_edit_date_covered_with_two_coverings(self):
        mare = Horse.objects.create(name='Second mare', sex='mare')
        rec = BreedingRecord.objects.create(mare=mare, stallion_name='X',
                                            date_covered=self.today - timedelta(days=30))
        add_covering(rec, date=self.today - timedelta(days=28))
        rec.refresh_from_db()
        new = self.today - timedelta(days=25)
        resp = self.client.post(reverse('breeding_update', args=[rec.pk]), {
            'mare': mare.pk, 'stallion_name': 'X', 'date_covered': new.isoformat(),
            'status': 'covered', 'date_foal_due': rec.date_foal_due.isoformat(),
        })
        self.assertEqual(resp.status_code, 302, getattr(resp, 'context', None) and resp.context['form'].errors)
        rec.refresh_from_db()
        dates = sorted(c.date for c in rec.coverings.all())
        self.assertEqual(rec.date_covered, max(dates))


class P7DuplicateOpenRecord(Base):
    def test_second_open_record_for_same_mare(self):
        self.client.post(reverse('breeding_create'), {
            'mare': self.mare.pk, 'stallion_name': 'Other', 'status': 'covered',
            'date_covered': (self.today - timedelta(days=10)).isoformat(),
        })
        open_ = BreedingRecord.objects.filter(mare=self.mare, status__in=('covered', 'confirmed')).count()
        self.client.get(reverse('horse_detail', args=[self.mare.pk]))
        self.assertEqual(open_, 1)


class P8SameFoalTwoRecords(Base):
    def test_edit_form_links_foal_already_linked(self):
        foal = record_foaling(self.record, foal_dob=self.today, foal_sex='colt', foal_name='Bob')
        mare2 = Horse.objects.create(name='Other mare', sex='mare')
        rec2 = BreedingRecord.objects.create(mare=mare2, stallion_name='Y',
                                             date_covered=self.today - timedelta(days=345), status='confirmed')
        self.client.post(reverse('breeding_update', args=[rec2.pk]), {
            'mare': mare2.pk, 'stallion_name': 'Y',
            'date_covered': rec2.date_covered.isoformat(), 'status': 'born',
            'date_foal_due': rec2.date_foal_due.isoformat(), 'foal': foal.pk,
        })
        self.assertEqual(foal.birth_record.count(), 1)


class P9Pages(Base):
    def test_born_without_foal_pages(self):
        resp = self.client.post(reverse('breeding_update', args=[self.record.pk]), {
            'mare': self.mare.pk, 'stallion_name': 'Hip Hop',
            'date_covered': self.record.date_covered.isoformat(), 'status': 'born',
            'date_foal_due': self.record.date_foal_due.isoformat(),
            'foal_dob': self.today.isoformat(), 'foal_sex': 'colt',
        })
        self.assertEqual(resp.status_code, 302)
        for name in ('breeding_foal_edit', 'breeding_wean'):
            r = self.client.get(reverse(name, args=[self.record.pk]))
            self.assertIn(r.status_code, (200, 302))
            r = self.client.get(reverse(name, args=[self.record.pk]), **POPUP)
            self.assertIn(r.status_code, (200, 204, 302))
        r = self.client.post(reverse('breeding_wean', args=[self.record.pk]),
                             {'weaned_date': self.today.isoformat()})
        for url in (reverse('horse_detail', args=[self.mare.pk]), reverse('breeding_list'),
                    reverse('breeding_calendar'), reverse('breeding_results'),
                    reverse('breeding_list') + '?status=born&season=abc'):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)


class P3bWeanUnplacedFoal(Base):
    def test_unplaced_foal_cannot_be_moved_at_weaning(self):
        OwnershipShare.objects.create(horse=self.mare, owner=self.owner, share_percentage=100, is_primary_contact=True)
        Placement.objects.create(horse=self.mare, owner=self.owner, location=self.field,
                                 rate_type=self.rate, start_date=self.today - timedelta(days=100))
        foal = record_foaling(self.record, foal_dob=self.today - timedelta(days=5),
                              foal_sex='colt', foal_name='Bob')
        resp = self.client.post(reverse('breeding_wean', args=[self.record.pk]), {
            'weaned_date': self.today.isoformat(), 'foal_location': self.paddock.pk,
            'foal_rate_type': self.foal_rate.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(foal.placements.exists())

    def test_service_with_no_owner_silently_skips(self):
        Placement.objects.create(horse=self.mare, owner=self.owner, location=self.field,
                                 rate_type=self.rate, start_date=self.today - timedelta(days=100))
        foal = record_foaling(self.record, foal_dob=self.today - timedelta(days=5),
                              foal_sex='colt', foal_name='Bob')
        self.record.refresh_from_db()
        record_weaning(self.record, weaned_date=self.today, foal_location=self.paddock,
                       foal_rate_type=self.foal_rate)
        self.assertTrue(foal.placements.exists(), 'no error and no placement')
