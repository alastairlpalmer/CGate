"""Covering cycles, scan history, foaling into the yard board and billing,
breeding reminders, and season results. Every flow accepts past dates so
old seasons can be reconciled."""

from datetime import date, timedelta

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.dashboard import attention
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin
from health.models import BreedingRecord, Covering, PregnancyScan, VetVisit
from health.services import add_covering, record_foaling, record_scan
from notifications.tasks import send_breeding_reminders

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class CoveringCycleTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.client.force_login(make_admin())
        self.mare = Horse.objects.create(name='Clover', sex='mare')
        self.record = BreedingRecord.objects.create(
            mare=self.mare, stallion_name='Hip Hop',
            date_covered=self.today - timedelta(days=20),
        )

    def test_record_gets_a_first_covering_row(self):
        self.assertEqual(self.record.covering_count, 1)
        self.assertEqual(self.record.coverings.get().date, self.record.date_covered)
        self.assertEqual(self.record.season, self.record.date_covered.year)
        self.assertFalse(self.record.due_date_is_manual)

    def test_editing_the_date_moves_a_single_covering(self):
        new_date = self.today - timedelta(days=18)
        self.record.date_covered = new_date
        self.record.save()
        self.assertEqual(self.record.coverings.get().date, new_date)
        self.assertEqual(self.record.date_foal_due, new_date + timedelta(days=340))

    def test_adding_a_covering_moves_date_covered_and_due(self):
        second = self.today - timedelta(days=18)
        add_covering(self.record, date=second, method='ai_chilled')
        self.record.refresh_from_db()
        self.assertEqual(self.record.covering_count, 2)
        self.assertEqual(self.record.date_covered, second)
        self.assertEqual(self.record.date_foal_due, second + timedelta(days=340))
        self.assertEqual(self.record.first_covering_date, self.today - timedelta(days=20))

    def test_backdated_covering_keeps_the_latest_as_date_covered(self):
        earlier = self.today - timedelta(days=40)
        add_covering(self.record, date=earlier)
        self.record.refresh_from_db()
        self.assertEqual(self.record.date_covered, self.today - timedelta(days=20))
        self.assertEqual(self.record.first_covering_date, earlier)

    def test_manual_due_date_survives_a_new_covering(self):
        manual = self.today + timedelta(days=300)
        self.record.date_foal_due = manual
        self.record.due_date_is_manual = True
        self.record.save()
        add_covering(self.record, date=self.today - timedelta(days=5))
        self.record.refresh_from_db()
        self.assertEqual(self.record.date_foal_due, manual)

    def test_negative_scan_then_recover_reopens_the_record(self):
        record_scan(self.record, scan_type='14_day', scan_date=self.today - timedelta(days=5),
                    result='not_in_foal')
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'barren')
        self.assertTrue(self.record.can_add_covering)
        add_covering(self.record, date=self.today - timedelta(days=1))
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'covered')
        self.assertIsNone(self.record.date_scanned_14_days)
        self.assertEqual(self.record.scans.count(), 1)  # the negative stays in the history
        self.assertEqual(self.record.next_scan, '14-day')

    def test_covering_refused_once_confirmed_or_born(self):
        record_scan(self.record, scan_type='14_day', scan_date=self.today - timedelta(days=5),
                    result='in_foal')
        self.record.refresh_from_db()
        self.assertFalse(self.record.can_add_covering)
        with self.assertRaises(ValidationError):
            add_covering(self.record, date=self.today)

    def test_scan_history_and_denormalised_dates(self):
        d14 = self.today - timedelta(days=6)
        record_scan(self.record, scan_type='14_day', scan_date=d14, result='twins', notes='reduce')
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'confirmed')
        self.assertEqual(self.record.date_scanned_14_days, d14)
        self.assertEqual(self.record.scan_14_result, 'twins')
        record_scan(self.record, scan_type='heartbeat', scan_date=self.today, result='inconclusive')
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'confirmed')
        self.assertIsNone(self.record.date_scanned_heartbeat)
        self.assertEqual(self.record.scan_heartbeat_result, 'inconclusive')
        record_scan(self.record, scan_type='heartbeat', scan_date=self.today, result='not_in_foal')
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'lost')
        self.assertEqual(self.record.scans.count(), 3)

    def test_covering_popup_and_delete(self):
        url = reverse('breeding_covering_add', args=[self.record.pk])
        response = self.client.get(url, **POPUP)
        self.assertTemplateUsed(response, 'health/partials/covering_form.html')
        self.assertEqual(response.context['form'].initial['stallion_name'], 'Hip Hop')
        response = self.client.post(url, {
            'date': (self.today - timedelta(days=18)).isoformat(), 'method': 'natural',
            'stallion_name': 'Hip Hop',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.record.refresh_from_db()
        self.assertEqual(self.record.covering_count, 2)
        # Duplicate date refused, future date refused.
        response = self.client.post(url, {'date': (self.today - timedelta(days=18)).isoformat()}, **POPUP)
        self.assertContains(response, 'already recorded')
        response = self.client.post(url, {'date': (self.today + timedelta(days=1)).isoformat()}, **POPUP)
        self.assertContains(response, 'cannot be in the future')
        # Remove the second covering: dates follow the one left.
        second = self.record.coverings.order_by('-date').first()
        response = self.client.post(reverse('breeding_covering_delete', args=[second.pk]))
        self.assertRedirects(response, reverse('horse_detail', args=[self.mare.pk]))
        self.record.refresh_from_db()
        self.assertEqual(self.record.covering_count, 1)
        self.assertEqual(self.record.date_covered, self.today - timedelta(days=20))
        # The last one cannot be removed.
        last = self.record.coverings.get()
        self.client.post(reverse('breeding_covering_delete', args=[last.pk]))
        self.assertEqual(self.record.coverings.count(), 1)

    def test_scan_delete_resyncs_dates(self):
        record_scan(self.record, scan_type='14_day', scan_date=self.today - timedelta(days=5), result='in_foal')
        scan = self.record.scans.get()
        response = self.client.post(reverse('breeding_scan_delete', args=[scan.pk]))
        self.assertRedirects(response, reverse('horse_detail', args=[self.mare.pk]))
        self.record.refresh_from_db()
        self.assertIsNone(self.record.date_scanned_14_days)
        self.assertEqual(self.record.scans.count(), 0)

    def test_scan_form_accepts_a_vet_and_backdates(self):
        from billing.models import ServiceProvider
        vet = ServiceProvider.objects.create(name='Jones', provider_type='vet')
        response = self.client.post(reverse('breeding_scan', args=[self.record.pk]), {
            'scan_type': '14_day', 'scan_date': (self.today - timedelta(days=6)).isoformat(),
            'result': 'in_foal', 'vet': vet.pk,
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.record.scans.get().vet, vet)

    def test_mare_page_shows_history_rows_and_covering_action(self):
        add_covering(self.record, date=self.today - timedelta(days=18))
        response = self.client.get(reverse('horse_detail', args=[self.mare.pk]))
        self.assertContains(response, reverse('breeding_covering_add', args=[self.record.pk]))
        self.assertContains(response, 'Coverings')
        self.assertContains(response, reverse('breeding_covering_delete', args=[self.record.coverings.first().pk]))

    def test_edit_form_marks_a_typed_due_date_manual(self):
        typed = self.today + timedelta(days=300)
        response = self.client.post(reverse('breeding_update', args=[self.record.pk]), {
            'mare': self.mare.pk, 'stallion_name': 'Hip Hop',
            'date_covered': self.record.date_covered.isoformat(), 'status': 'covered',
            'date_foal_due': typed.isoformat(),
        })
        self.assertEqual(response.status_code, 302)
        self.record.refresh_from_db()
        self.assertTrue(self.record.due_date_is_manual)
        self.assertEqual(self.record.date_foal_due, typed)


class FoalingYardAndBillingTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.client.force_login(make_admin())
        self.owner = Owner.objects.create(name='Mrs Fox')
        self.location = Location.objects.create(name='Front field', site='Main')
        self.mare_rate = RateType.objects.create(name='Mare at grass', daily_rate=9)
        self.pair_rate = RateType.objects.create(name='Mare and foal at grass', daily_rate=11)
        self.foal_rate = RateType.objects.create(name='Foal at foot', daily_rate=0)
        self.mare = Horse.objects.create(name='Vivian', sex='mare')
        OwnershipShare.objects.create(horse=self.mare, owner=self.owner, share_percentage=100, is_primary_contact=True)
        Placement.objects.create(
            horse=self.mare, owner=self.owner, location=self.location,
            rate_type=self.mare_rate, start_date=self.today - timedelta(days=60),
        )
        self.record = BreedingRecord.objects.create(
            mare=self.mare, stallion_name='Hip Hop',
            date_covered=self.today - timedelta(days=338), status='confirmed',
        )

    def test_foaling_can_switch_the_mare_rate_and_place_the_foal(self):
        dob = self.today - timedelta(days=1)
        foal = record_foaling(
            self.record, foal_dob=dob, foal_sex='filly', foal_name='Little Viv',
            mare_rate_type=self.pair_rate, place_foal=True, foal_rate_type=self.foal_rate,
        )
        mare_placement = self.mare.placements.filter(end_date__isnull=True).get()
        self.assertEqual(mare_placement.rate_type, self.pair_rate)
        self.assertEqual(mare_placement.start_date, dob)
        self.assertEqual(mare_placement.location, self.location)
        old = self.mare.placements.exclude(pk=mare_placement.pk).get()
        self.assertEqual(old.end_date, dob - timedelta(days=1))
        foal_placement = foal.placements.get()
        self.assertEqual(foal_placement.location, self.location)
        self.assertEqual(foal_placement.rate_type, self.foal_rate)
        self.assertEqual(foal_placement.owner, self.owner)
        self.assertEqual(foal_placement.start_date, dob)

    def test_foaling_without_options_leaves_the_board_alone(self):
        foal = record_foaling(self.record, foal_dob=self.today, foal_sex='colt', foal_name='Bob')
        self.assertEqual(self.mare.placements.count(), 1)
        self.assertEqual(self.mare.placements.get().rate_type, self.mare_rate)
        self.assertFalse(foal.placements.exists())

    def test_form_requires_a_foal_rate_when_placing(self):
        url = reverse('breeding_foaling', args=[self.record.pk])
        response = self.client.post(url, {
            'foal_dob': self.today.isoformat(), 'foal_sex': 'colt', 'foal_name': 'Bob',
            'place_foal': 'on',
        }, **POPUP)
        self.assertContains(response, 'Pick the rate')
        response = self.client.post(url, {
            'foal_dob': self.today.isoformat(), 'foal_sex': 'colt', 'foal_name': 'Bob',
            'place_foal': 'on', 'foal_rate_type': self.foal_rate.pk,
            'mare_rate_type': self.pair_rate.pk,
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(Horse.objects.get(name='Bob').placements.count(), 1)

    def test_backdated_foaling_before_placement_start_is_refused_with_options(self):
        url = reverse('breeding_foaling', args=[self.record.pk])
        early = self.today - timedelta(days=61)
        self.record.date_covered = early - timedelta(days=300)
        self.record.save()
        response = self.client.post(url, {
            'foal_dob': early.isoformat(), 'foal_sex': 'colt', 'foal_name': 'Bob',
            'mare_rate_type': self.pair_rate.pk,
        }, **POPUP)
        self.assertContains(response, 'must start after that')
        # Without the option the back-dated foaling records fine.
        response = self.client.post(url, {
            'foal_dob': early.isoformat(), 'foal_sex': 'colt', 'foal_name': 'Bob',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)

    def test_form_shows_yard_block_and_suggests_a_foal_rate(self):
        response = self.client.get(reverse('breeding_foaling', args=[self.record.pk]), **POPUP)
        self.assertContains(response, 'Yard board')
        self.assertContains(response, 'Suggested: Mare and foal at grass')
        self.assertContains(response, 'Front field')

    def test_foaling_photo_category_exists(self):
        from core.models import HorsePhoto
        self.assertIn('foaling', HorsePhoto.Category.values)


class BreedingAttentionAndReminderTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.admin = make_admin()
        self.owner = Owner.objects.create(name='Mrs Fox', email='fox@example.com')
        self.mare = Horse.objects.create(name='Ada', sex='mare')
        OwnershipShare.objects.create(horse=self.mare, owner=self.owner, share_percentage=100, is_primary_contact=True)

    def collect(self, **kw):
        return attention.collect(self.admin, today=self.today, **kw)

    def test_scan_due_rows(self):
        record = BreedingRecord.objects.create(
            mare=self.mare, stallion_name='Hip Hop', date_covered=self.today - timedelta(days=14),
        )
        scans = [i for i in self.collect() if i.kind == 'scan']
        self.assertEqual(len(scans), 1)
        self.assertEqual(scans[0].due_date, self.today)
        self.assertIn('14-day scan', scans[0].detail)
        self.assertEqual(scans[0].actions[0].url, reverse('breeding_scan', args=[record.pk]))
        record_scan(record, scan_type='14_day', scan_date=self.today, result='in_foal')
        scans = [i for i in self.collect() if i.kind == 'scan']
        self.assertEqual(len(scans), 1)
        self.assertIn('Heartbeat', scans[0].detail)
        self.assertEqual(scans[0].due_date, self.today + timedelta(days=14))
        record_scan(record, scan_type='heartbeat', scan_date=self.today, result='in_foal')
        self.assertEqual([i for i in self.collect() if i.kind == 'scan'], [])

    def test_newborn_check_and_passport_rows(self):
        record = BreedingRecord.objects.create(
            mare=self.mare, stallion_name='Hip Hop', date_covered=self.today - timedelta(days=340),
            status='confirmed',
        )
        foal = record_foaling(record, foal_dob=self.today, foal_sex='colt', foal_name='Bob')
        kinds = {i.kind: i for i in self.collect()}
        self.assertIn('foal_check', kinds)
        self.assertEqual(kinds['foal_check'].horse, foal)
        self.assertEqual(kinds['foal_check'].due_date, self.today + timedelta(days=1))
        self.assertNotIn('foal_passport', kinds)  # six months away, outside the window
        VetVisit.objects.create(horse=foal, date=self.today, reason='Newborn check')
        self.assertNotIn('foal_check', {i.kind for i in self.collect()})
        # Five months on, the passport row appears; adding a passport clears it.
        later = self.today + timedelta(days=5 * 31)
        items = attention.collect(self.admin, today=later)
        passport = [i for i in items if i.kind == 'foal_passport']
        self.assertEqual(len(passport), 1)
        self.assertEqual(passport[0].due_date, attention._add_months(self.today, 6))
        foal.has_passport = True
        foal.save()
        self.assertEqual([i for i in attention.collect(self.admin, today=later) if i.kind == 'foal_passport'], [])

    def test_breeding_reminders_go_once_per_key(self):
        record = BreedingRecord.objects.create(
            mare=self.mare, stallion_name='Hip Hop', date_covered=self.today - timedelta(days=14),
        )
        result = send_breeding_reminders()
        self.assertEqual(result, 'Sent 1 breeding reminders')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('14-day', mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ['fox@example.com'])
        record.refresh_from_db()
        self.assertEqual(record.sent_reminder_keys, {'scan14'})
        # Second run: nothing new.
        self.assertEqual(send_breeding_reminders(), 'Sent 0 breeding reminders')
        # Confirmed and due in 30 days: one digest with foal30.
        record.status = 'confirmed'
        record.date_scanned_14_days = self.today
        record.date_scanned_heartbeat = self.today
        record.due_date_is_manual = True
        record.date_foal_due = self.today + timedelta(days=30)
        record.save()
        self.assertEqual(send_breeding_reminders(), 'Sent 1 breeding reminders')
        record.refresh_from_db()
        self.assertEqual(record.sent_reminder_keys, {'scan14', 'foal30'})
        self.assertIn('Foal due in 30 days', mail.outbox[-1].body)

    def test_reminders_skip_owners_without_email(self):
        self.owner.email = ''
        self.owner.save()
        BreedingRecord.objects.create(
            mare=self.mare, stallion_name='Hip Hop', date_covered=self.today - timedelta(days=14),
        )
        self.assertEqual(send_breeding_reminders(), 'Sent 0 breeding reminders')
        self.assertEqual(mail.outbox, [])


class SeasonResultsTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.client.force_login(make_admin())
        self.year = self.today.year - 1
        mares = [Horse.objects.create(name=n, sex='mare') for n in ('Ada', 'Bella', 'Clover', 'Dot')]
        base = date(self.year, 4, 1)
        # Ada: two covers, born. Bella: barren. Clover: lost. Dot: covered again after barren, then confirmed.
        ada = BreedingRecord.objects.create(mare=mares[0], stallion_name='Hip Hop', date_covered=base, status='born',
                                            foal_dob=base + timedelta(days=345), foal_sex='filly')
        Covering.objects.create(record=ada, date=base + timedelta(days=2))
        BreedingRecord.objects.create(mare=mares[1], stallion_name='Hip Hop', date_covered=base, status='barren')
        BreedingRecord.objects.create(mare=mares[2], stallion_name='Masterpiece', date_covered=base, status='lost')
        dot = BreedingRecord.objects.create(mare=mares[3], stallion_name='Masterpiece', date_covered=base)
        record_scan(dot, scan_type='14_day', scan_date=base + timedelta(days=14), result='not_in_foal')
        add_covering(dot, date=base + timedelta(days=30))
        record_scan(dot, scan_type='14_day', scan_date=base + timedelta(days=44), result='in_foal')
        # This year: one open record, must not leak into last season.
        BreedingRecord.objects.create(mare=mares[0], stallion_name='Hip Hop', date_covered=self.today - timedelta(days=3))

    def test_results_page_figures(self):
        response = self.client.get(reverse('breeding_results'), {'season': self.year})
        self.assertEqual(response.status_code, 200)
        t = response.context['results']['totals']
        self.assertEqual(t['records'], 4)
        self.assertEqual(t['mares'], 4)
        self.assertEqual(t['covers'], 6)
        self.assertEqual(t['held'], 3)       # born, lost, confirmed
        self.assertEqual(t['born'], 1)
        self.assertEqual(t['barren'], 1)
        self.assertEqual(t['lost'], 1)
        self.assertEqual(t['open'], 1)
        self.assertEqual(t['conception_rate'], 75)
        self.assertEqual(t['live_foal_rate'], 25)
        self.assertEqual(t['fillies'], 1)
        stallions = {s['name']: s for s in response.context['results']['stallions']}
        self.assertEqual(stallions['Hip Hop']['mares'], 2)
        self.assertEqual(stallions['Masterpiece']['held'], 2)
        self.assertContains(response, 'Season Results')
        # Default season is the newest with coverings; the year filter is offered.
        response = self.client.get(reverse('breeding_results'))
        self.assertEqual(response.context['season'], self.today.year)
        self.assertEqual(response.context['seasons'], [self.today.year, self.year])

    def test_list_season_filter(self):
        response = self.client.get(reverse('breeding_list'), {'season': self.year})
        self.assertEqual(len(response.context['breeding_records']), 4)
        response = self.client.get(reverse('breeding_list'), {'season': self.today.year})
        self.assertEqual(len(response.context['breeding_records']), 1)
        self.assertContains(response, 'Season results')

    def test_mare_page_breeding_history(self):
        ada = Horse.objects.get(name='Ada')
        response = self.client.get(reverse('horse_detail', args=[ada.pk]))
        self.assertContains(response, 'Breeding History')
        self.assertEqual(len(response.context['breeding_history']), 1)  # the born one; open one has its card
        self.assertContains(response, str(self.year))
