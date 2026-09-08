"""The foal's own record: days-old count, birth details, milestones,
dated notes and weaning (with optional move and re-rate)."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from health.models import BreedingRecord, FarrierVisit, FoalNote, VetVisit
from health.services import foal_milestones, record_foaling, record_weaning

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}
HTMX = {'HTTP_HX_REQUEST': 'true'}


class FoalRecordTestCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.client.force_login(make_admin())
        self.owner = Owner.objects.create(name='Mrs Fox')
        self.field = Location.objects.create(name='Front field', site='Main')
        self.paddock = Location.objects.create(name='Weaning paddock', site='Main')
        self.pair_rate = RateType.objects.create(name='Mare and foal at grass', daily_rate=11)
        self.mare_rate = RateType.objects.create(name='Mare at grass', daily_rate=9)
        self.foal_rate = RateType.objects.create(name='Youngstock at grass', daily_rate=6)
        self.mare = Horse.objects.create(name='Vivian', sex='mare')
        OwnershipShare.objects.create(horse=self.mare, owner=self.owner, share_percentage=100, is_primary_contact=True)
        Placement.objects.create(horse=self.mare, owner=self.owner, location=self.field,
                                 rate_type=self.pair_rate, start_date=self.today - timedelta(days=100))
        self.record = BreedingRecord.objects.create(
            mare=self.mare, stallion_name='Hip Hop', date_covered=self.today - timedelta(days=350),
            status='confirmed',
        )
        self.dob = self.today - timedelta(days=12)
        self.foal = record_foaling(
            self.record, foal_dob=self.dob, foal_sex='filly', foal_colour='bay',
            foal_name='Little Viv', foaling_notes='Quick and easy',
            place_foal=True, foal_rate_type=self.foal_rate,
        )
        self.record.refresh_from_db()

    # ── Age helpers ──

    def test_age_labels(self):
        self.assertEqual(self.foal.days_old, 12)
        self.assertEqual(self.foal.age_label, '12 days old')
        self.assertTrue(self.foal.is_youngster)
        self.assertEqual(self.record.foal_age_label, '12 days old')
        self.foal.date_of_birth = self.today - timedelta(days=30)
        self.assertEqual(self.foal.age_label, '4 weeks old')
        self.foal.date_of_birth = self.today - timedelta(days=200)
        self.assertEqual(self.foal.age_label, '6 months old')
        self.foal.date_of_birth = self.today - timedelta(days=800)
        self.assertEqual(self.foal.age_label, '2yo')
        self.assertFalse(self.foal.is_youngster)
        self.foal.date_of_birth = None
        self.foal.age = 7
        self.assertEqual(self.foal.age_label, '7yo')

    # ── Foal page ──

    def test_foal_page_shows_the_record(self):
        response = self.client.get(reverse('horse_detail', args=[self.foal.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Foal Record')
        self.assertContains(response, '12 days old')
        self.assertContains(response, 'At foot')
        self.assertContains(response, 'Quick and easy')
        self.assertContains(response, reverse('breeding_wean', args=[self.record.pk]))
        self.assertContains(response, reverse('breeding_foal_edit', args=[self.record.pk]))
        self.assertContains(response, 'Newborn vet check')
        self.assertContains(response, 'No notes yet')
        self.assertEqual(response.context['foal_record'], self.record)
        # A horse with no birth record has no foal card.
        response = self.client.get(reverse('horse_detail', args=[self.mare.pk]))
        self.assertIsNone(response.context['foal_record'])
        self.assertContains(response, 'Little Viv')
        self.assertContains(response, 'At foot')

    def test_milestones_read_from_yard_records(self):
        keys = {k: done for k, _, done, _, _ in foal_milestones(self.record)}
        self.assertEqual(keys, {'vet_check': False, 'passport': False, 'farrier': False,
                                'vaccination': False, 'weaned': False})
        VetVisit.objects.create(horse=self.foal, date=self.dob + timedelta(days=1), reason='IgG')
        FarrierVisit.objects.create(horse=self.foal, date=self.today)
        self.foal.has_passport = True
        self.foal.save()
        record = BreedingRecord.objects.select_related('foal').get(pk=self.record.pk)
        ms = {k: (done, when) for k, _, done, when, _ in foal_milestones(record)}
        self.assertEqual(ms['vet_check'], (True, self.dob + timedelta(days=1)))
        self.assertEqual(ms['farrier'], (True, self.today))
        self.assertTrue(ms['passport'][0])
        self.assertFalse(ms['weaned'][0])
        # A visit before birth (a data slip) does not count.
        self.assertEqual(foal_milestones(BreedingRecord(mare=self.mare)), [])

    # ── Details ──

    def test_edit_details_updates_record_and_horse(self):
        url = reverse('breeding_foal_edit', args=[self.record.pk])
        response = self.client.get(url, **POPUP)
        self.assertTemplateUsed(response, 'health/partials/foal_details_form.html')
        self.assertEqual(response.context['form'].initial['foal_name'], 'Little Viv')
        new_dob = self.today - timedelta(days=13)
        response = self.client.post(url, {
            'foal_name': 'Vivienne', 'foal_dob': new_dob.isoformat(), 'foal_sex': 'filly',
            'foal_colour': 'grey', 'foal_microchip': '985-1', 'foaling_notes': 'Placenta passed',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.record.refresh_from_db()
        self.foal.refresh_from_db()
        self.assertEqual((self.record.foal_dob, self.record.foal_colour, self.record.foal_microchip),
                         (new_dob, 'grey', '985-1'))
        self.assertEqual((self.foal.name, self.foal.date_of_birth, self.foal.color), ('Vivienne', new_dob, 'grey'))
        # Bad dates are refused.
        response = self.client.post(url, {
            'foal_name': 'X', 'foal_dob': (self.today + timedelta(days=1)).isoformat(), 'foal_sex': 'filly',
        }, **POPUP)
        self.assertContains(response, 'cannot be in the future')

    def test_edit_details_bounces_before_birth(self):
        rec = BreedingRecord.objects.create(mare=Horse.objects.create(name='Ada', sex='mare'),
                                            stallion_name='X', date_covered=self.today)
        response = self.client.get(reverse('breeding_foal_edit', args=[rec.pk]))
        self.assertRedirects(response, reverse('horse_detail', args=[rec.mare_id]))

    # ── Notes ──

    def test_notes_add_and_remove_over_htmx(self):
        url = reverse('breeding_foal_note_add', args=[self.record.pk])
        response = self.client.post(url, {'date': self.today.isoformat(), 'note': 'Nursing well'}, **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'health/partials/foal_notes.html')
        self.assertContains(response, 'Nursing well')
        note = FoalNote.objects.get()
        self.assertEqual(note.record, self.record)
        # A future date is refused and the form re-renders with the error.
        response = self.client.post(url, {'date': (self.today + timedelta(days=1)).isoformat(), 'note': 'x'}, **HTMX)
        self.assertContains(response, 'cannot be in the future')
        self.assertEqual(FoalNote.objects.count(), 1)
        # Plain post redirects to the foal's page.
        response = self.client.post(url, {'date': self.today.isoformat(), 'note': 'Turned out'})
        self.assertRedirects(response, reverse('horse_detail', args=[self.foal.pk]))
        self.assertEqual(FoalNote.objects.count(), 2)
        response = self.client.post(reverse('breeding_foal_note_delete', args=[note.pk]), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(FoalNote.objects.filter(pk=note.pk).exists())
        self.assertEqual(self.client.get(url).status_code, 400)

    # ── Weaning ──

    def test_wean_date_only(self):
        url = reverse('breeding_wean', args=[self.record.pk])
        response = self.client.get(url, **POPUP)
        self.assertTemplateUsed(response, 'health/partials/wean_form.html')
        self.assertContains(response, 'Suggested: Mare at grass')
        day = self.today - timedelta(days=1)
        response = self.client.post(url, {'weaned_date': day.isoformat(), 'weaning_notes': 'Gradual'}, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.record.refresh_from_db()
        self.assertEqual(self.record.weaned_date, day)
        self.assertTrue(self.record.is_weaned)
        self.assertFalse(self.record.can_wean)
        # Board untouched.
        self.assertEqual(self.foal.placements.count(), 1)
        self.assertEqual(self.mare.placements.count(), 1)
        # Second weaning bounces.
        response = self.client.get(url)
        self.assertRedirects(response, reverse('horse_detail', args=[self.foal.pk]))
        response = self.client.get(reverse('horse_detail', args=[self.foal.pk]))
        self.assertContains(response, 'Weaned')
        self.assertNotContains(response, 'At foot')

    def test_wean_moves_the_foal_and_re_rates_the_mare(self):
        day = self.today
        record_weaning(
            self.record, weaned_date=day, foal_location=self.paddock,
            foal_rate_type=self.foal_rate, mare_rate_type=self.mare_rate, weaning_notes='',
        )
        foal_now = self.foal.placements.filter(end_date__isnull=True).get()
        self.assertEqual((foal_now.location, foal_now.start_date, foal_now.rate_type),
                         (self.paddock, day, self.foal_rate))
        old = self.foal.placements.exclude(pk=foal_now.pk).get()
        self.assertEqual(old.end_date, day - timedelta(days=1))
        mare_now = self.mare.placements.filter(end_date__isnull=True).get()
        self.assertEqual((mare_now.location, mare_now.rate_type, mare_now.start_date),
                         (self.field, self.mare_rate, day))

    def test_wean_form_guards_dates(self):
        url = reverse('breeding_wean', args=[self.record.pk])
        response = self.client.post(url, {'weaned_date': (self.dob - timedelta(days=1)).isoformat()}, **POPUP)
        self.assertContains(response, 'before the foal was born')
        # A move dated on the foal's placement start is refused; the same
        # date with no board change is fine.
        response = self.client.post(url, {'weaned_date': self.dob.isoformat(), 'foal_location': self.paddock.pk}, **POPUP)
        self.assertContains(response, 'must be after that')
        response = self.client.post(url, {'weaned_date': self.dob.isoformat()}, **POPUP)
        self.assertEqual(response.status_code, 204)

    def test_wean_refused_before_birth_and_for_viewers(self):
        rec = BreedingRecord.objects.create(mare=Horse.objects.create(name='Ada', sex='mare'),
                                            stallion_name='X', date_covered=self.today)
        self.assertFalse(rec.can_wean)
        self.assertEqual(self.client.get(reverse('breeding_wean', args=[rec.pk]), **POPUP).status_code, 204)
        self.client.force_login(make_user_with_access('v', breeding='view', horses='view'))
        response = self.client.post(reverse('breeding_wean', args=[self.record.pk]),
                                    {'weaned_date': self.today.isoformat()}, **POPUP)
        self.assertNotEqual(response.status_code, 204)
        self.record.refresh_from_db()
        self.assertFalse(self.record.is_weaned)
        response = self.client.get(reverse('horse_detail', args=[self.foal.pk]))
        self.assertContains(response, 'Foal Record')
        self.assertNotContains(response, reverse('breeding_wean', args=[self.record.pk]))

    def test_list_and_calendar_show_foal_age(self):
        response = self.client.get(reverse('breeding_list'))
        self.assertContains(response, '12 days old')
        response = self.client.get(reverse('breeding_calendar'), {'season': self.today.year})
        self.assertContains(response, '12 days old')
