"""Breeding page and mare-page workflows: record a foaling in one step,
the list's season summary and Active filter, and the operable cards on
the mare's page."""

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Owner, OwnershipShare
from core.roles_testutils import make_admin, make_user_with_access
from health.models import BreedingRecord
from health.services import record_foaling

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class BreedingWorkflowTestCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.client.force_login(make_admin())
        self.owner = Owner.objects.create(name='Mrs Tamara Fox')
        self.mare = Horse.objects.create(name='Vivian', sex='mare')
        OwnershipShare.objects.create(
            horse=self.mare, owner=self.owner, share_percentage=100, is_primary_contact=True,
        )
        self.record = BreedingRecord.objects.create(
            mare=self.mare, stallion_name='Hip Hop',
            date_covered=self.today - timedelta(days=330),
            status=BreedingRecord.Status.CONFIRMED,
        )

    # ── Model helpers ──────────────────────────────────────────────

    def test_due_date_and_gestation_helpers(self):
        self.assertEqual(self.record.date_foal_due, self.record.date_covered + timedelta(days=340))
        self.assertEqual(self.record.day_of_gestation, 330)
        self.assertEqual(self.record.gestation_progress, 97)
        self.assertEqual(self.record.days_to_foal_due, 10)
        self.assertTrue(self.record.is_due_soon)
        self.assertFalse(self.record.is_past_due)
        self.assertTrue(self.record.can_record_foaling)
        # Month 5, 7 and 9 have all passed on day 330.
        self.assertIsNone(self.record.next_ehv)

    def test_next_ehv_is_the_first_future_dose(self):
        self.record.date_covered = self.today - timedelta(days=100)
        self.record.save()
        nxt = self.record.next_ehv
        self.assertEqual(nxt['month'], 5)
        self.assertEqual(nxt['date'], self.record.ehv_vaccination_dates[5])
        self.assertFalse(nxt['sent'])

    def test_closed_records_have_no_gestation(self):
        self.record.status = BreedingRecord.Status.BARREN
        self.record.save()
        self.assertIsNone(self.record.day_of_gestation)
        self.assertIsNone(self.record.days_to_foal_due)
        self.assertFalse(self.record.can_record_foaling)

    # ── Record foaling ─────────────────────────────────────────────

    def test_record_foaling_creates_the_foal_and_closes_the_record(self):
        dob = self.today - timedelta(days=1)
        foal = record_foaling(
            self.record, foal_dob=dob, foal_sex='filly', foal_colour='bay',
            foal_name='Vivian 2026 foal', foal_microchip='985', foaling_notes='Easy foaling',
        )
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, BreedingRecord.Status.BORN)
        self.assertEqual(self.record.foal, foal)
        self.assertEqual(self.record.foal_dob, dob)
        self.assertEqual(self.record.foal_sex, 'filly')
        self.assertEqual(self.record.foal_microchip, '985')
        self.assertEqual(self.record.foaling_notes, 'Easy foaling')
        # The foal's horse record carries the parentage and the mare's owner.
        self.assertEqual(foal.name, 'Vivian 2026 foal')
        self.assertEqual(foal.sex, 'filly')
        self.assertEqual(foal.color, 'bay')
        self.assertEqual(foal.date_of_birth, dob)
        self.assertEqual(foal.dam, self.mare)
        self.assertEqual(foal.dam_name, 'Vivian')
        self.assertEqual(foal.sire_name, 'Hip Hop')
        self.assertFalse(foal.has_passport)
        self.assertTrue(foal.is_active)
        self.assertEqual(foal.primary_owner, self.owner)
        self.assertIn(foal, Horse.objects.filter(dam=self.mare))

    def test_record_foaling_can_link_an_existing_horse(self):
        existing = Horse.objects.create(name='Already Added', sex='colt')
        foal = record_foaling(
            self.record, foal_dob=self.today, foal_sex='colt', existing_foal=existing,
        )
        self.assertEqual(foal, existing)
        existing.refresh_from_db()
        self.assertEqual(existing.dam, self.mare)
        self.assertEqual(existing.sire_name, 'Hip Hop')
        self.assertEqual(existing.date_of_birth, self.today)
        self.assertEqual(Horse.objects.count(), 2)

    def test_record_foaling_refuses_a_closed_record(self):
        self.record.status = BreedingRecord.Status.LOST
        self.record.save()
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            record_foaling(self.record, foal_dob=self.today, foal_sex='colt', foal_name='X')
        self.assertEqual(Horse.objects.count(), 1)

    # ── Foaling view ───────────────────────────────────────────────

    def test_foaling_view_renders_full_page_and_popup(self):
        url = reverse('breeding_foaling', args=[self.record.pk])
        full = self.client.get(url)
        self.assertEqual(full.status_code, 200)
        self.assertTemplateUsed(full, 'health/breeding_foaling.html')
        self.assertContains(full, 'Record Foaling')
        popup = self.client.get(url, **POPUP)
        self.assertEqual(popup.status_code, 200)
        self.assertTemplateUsed(popup, 'health/partials/foaling_form.html')
        self.assertTemplateNotUsed(popup, 'base.html')
        self.assertContains(popup, 'hx-post=')

    def test_foaling_post_in_popup_saves_and_answers_204(self):
        url = reverse('breeding_foaling', args=[self.record.pk])
        response = self.client.post(url, {
            'foal_dob': self.today.isoformat(), 'foal_sex': 'colt',
            'foal_colour': 'grey', 'foal_name': 'Little Bob',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get('HX-Trigger'), 'popup:saved')
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'born')
        self.assertEqual(self.record.foal.name, 'Little Bob')

    def test_foaling_post_full_page_redirects_to_the_foal(self):
        url = reverse('breeding_foaling', args=[self.record.pk])
        response = self.client.post(url, {
            'foal_dob': self.today.isoformat(), 'foal_sex': 'filly', 'foal_name': 'Little Viv',
        })
        foal = Horse.objects.get(name='Little Viv')
        self.assertRedirects(response, reverse('horse_detail', args=[foal.pk]))

    def test_foaling_post_needs_a_name_or_an_existing_horse(self):
        url = reverse('breeding_foaling', args=[self.record.pk])
        response = self.client.post(url, {
            'foal_dob': self.today.isoformat(), 'foal_sex': 'filly',
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Give the foal a name')
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'confirmed')

    def test_foaling_post_rejects_a_birth_before_covering(self):
        url = reverse('breeding_foaling', args=[self.record.pk])
        response = self.client.post(url, {
            'foal_dob': (self.record.date_covered - timedelta(days=1)).isoformat(),
            'foal_sex': 'filly', 'foal_name': 'Too Early',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'before the mare was covered')

    def test_foaling_on_a_closed_record_bounces_with_a_message(self):
        self.record.status = BreedingRecord.Status.BORN
        self.record.foal_dob = self.today
        self.record.save()
        url = reverse('breeding_foaling', args=[self.record.pk])
        response = self.client.get(url)
        self.assertRedirects(response, reverse('horse_detail', args=[self.mare.pk]))
        self.assertEqual(self.client.get(url, **POPUP).status_code, 204)

    def test_foaling_needs_breeding_write_access(self):
        self.client.force_login(make_user_with_access('viewer_only', breeding='view', horses='view'))
        url = reverse('breeding_foaling', args=[self.record.pk])
        response = self.client.post(url, {
            'foal_dob': self.today.isoformat(), 'foal_sex': 'filly', 'foal_name': 'Nope',
        })
        self.assertNotEqual(response.status_code, 204)
        self.assertFalse(Horse.objects.filter(name='Nope').exists())

    # ── Breeding list ──────────────────────────────────────────────

    def test_list_shows_summary_gestation_and_foaling_action(self):
        response = self.client.get(reverse('breeding_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Record foaling')
        self.assertContains(response, 'Day 330 of 340')
        self.assertContains(response, 'Foals due in 30 days')
        self.assertContains(response, reverse('breeding_foaling', args=[self.record.pk]))
        summary = response.context['summary']
        self.assertEqual(summary['in_foal'], 1)
        self.assertEqual(summary['awaiting_scan'], 0)
        self.assertEqual(summary['due_30'], 1)
        self.assertEqual(summary['past_due'], 0)

    def test_list_active_filter_and_ordering(self):
        other = Horse.objects.create(name='Ada', sex='mare')
        born = BreedingRecord.objects.create(
            mare=other, stallion_name='Old Boy', date_covered=self.today - timedelta(days=700),
            status=BreedingRecord.Status.BORN, foal_dob=self.today - timedelta(days=360),
        )
        covered = BreedingRecord.objects.create(
            mare=other, stallion_name='New Boy', date_covered=self.today - timedelta(days=20),
            status=BreedingRecord.Status.COVERED,
        )
        response = self.client.get(reverse('breeding_list'), {'status': 'active'})
        records = list(response.context['breeding_records'])
        self.assertEqual(records, [self.record, covered])
        self.assertNotIn(born, records)
        # Unfiltered: open pregnancies first (soonest due), closed after.
        response = self.client.get(reverse('breeding_list'))
        self.assertEqual(list(response.context['breeding_records']), [self.record, covered, born])
        # An unknown status is ignored rather than erroring.
        self.assertEqual(self.client.get(reverse('breeding_list'), {'status': 'bogus'}).status_code, 200)

    def test_list_flags_a_passed_due_date(self):
        self.record.date_covered = self.today - timedelta(days=350)
        self.record.date_foal_due = self.today - timedelta(days=10)
        self.record.save()
        response = self.client.get(reverse('breeding_list'))
        self.assertContains(response, 'Due date passed')
        self.assertEqual(response.context['summary']['past_due'], 1)

    def test_list_hides_write_actions_from_viewers(self):
        self.client.force_login(make_user_with_access('viewer_only', breeding='view', horses='view'))
        response = self.client.get(reverse('breeding_list'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Record foaling')
        self.assertNotContains(response, 'Record Covering')

    # ── Mare page ──────────────────────────────────────────────────

    def test_mare_page_offers_record_foaling_for_an_open_pregnancy(self):
        response = self.client.get(reverse('horse_detail', args=[self.mare.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('breeding_foaling', args=[self.record.pk]))
        self.assertContains(response, 'Record foaling')
        self.assertContains(response, 'Day 330 of 340')

    def test_mare_page_offers_a_covering_when_not_in_foal(self):
        self.record.delete()
        response = self.client.get(reverse('horse_detail', args=[self.mare.pk]))
        self.assertContains(response, 'No breeding records yet')
        self.assertContains(response, reverse('breeding_create') + '?horse=' + str(self.mare.pk))
        self.assertNotContains(response, 'Record foaling')

    def test_foal_page_links_back_to_the_dam(self):
        foal = record_foaling(
            self.record, foal_dob=self.today, foal_sex='colt', foal_name='Junior',
        )
        response = self.client.get(reverse('horse_detail', args=[foal.pk]))
        self.assertContains(response, reverse('horse_detail', args=[self.mare.pk]))
        self.assertContains(response, 'Hip Hop')
        # And the mare's page lists the foal.
        response = self.client.get(reverse('horse_detail', args=[self.mare.pk]))
        self.assertContains(response, 'Junior')

    # ── Edit form ──────────────────────────────────────────────────

    def test_edit_form_lists_only_foals_and_mares(self):
        gelding = Horse.objects.create(name='Gerald', sex='gelding')
        colt = Horse.objects.create(name='Colty', sex='colt')
        response = self.client.get(reverse('breeding_update', args=[self.record.pk]))
        form = response.context['form']
        self.assertNotIn(gelding, form.fields['foal'].queryset)
        self.assertIn(colt, form.fields['foal'].queryset)
        self.assertNotIn(gelding, form.fields['mare'].queryset)
        self.assertIn(self.mare, form.fields['mare'].queryset)

    def test_edit_form_will_not_mark_born_without_a_foal(self):
        response = self.client.post(reverse('breeding_update', args=[self.record.pk]), {
            'mare': self.mare.pk, 'stallion_name': 'Hip Hop',
            'date_covered': self.record.date_covered.isoformat(), 'status': 'born',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Record foaling')
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'confirmed')


class ScanResultTestCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.client.force_login(make_admin())
        self.mare = Horse.objects.create(name='Clover', sex='mare')
        self.record = BreedingRecord.objects.create(
            mare=self.mare, stallion_name='Hip Hop',
            date_covered=self.today - timedelta(days=16),
            status=BreedingRecord.Status.COVERED,
        )

    def _post(self, **data):
        base = {'scan_type': '14_day', 'scan_date': self.today.isoformat(), 'result': 'in_foal'}
        base.update(data)
        return self.client.post(reverse('breeding_scan', args=[self.record.pk]), base, **POPUP)

    def test_next_scan_helper(self):
        self.assertEqual(self.record.next_scan, '14-day')
        self.record.date_scanned_14_days = self.today
        self.assertEqual(self.record.next_scan, 'heartbeat')
        self.record.date_scanned_heartbeat = self.today
        self.assertIsNone(self.record.next_scan)
        self.record.status = 'barren'
        self.record.date_scanned_heartbeat = None
        self.assertIsNone(self.record.next_scan)

    def test_14_day_in_foal_confirms(self):
        response = self._post(notes='Single, vet Jones')
        self.assertEqual(response.status_code, 204)
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'confirmed')
        self.assertEqual(self.record.date_scanned_14_days, self.today)
        self.assertIsNone(self.record.date_scanned_heartbeat)
        self.assertIn('scan: Single, vet Jones', self.record.foaling_notes)

    def test_14_day_not_in_foal_closes_as_barren(self):
        self._post(result='not_in_foal')
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'barren')
        self.assertEqual(self.record.date_scanned_14_days, self.today)

    def test_heartbeat_not_in_foal_closes_as_lost(self):
        self.record.date_scanned_14_days = self.today - timedelta(days=2)
        self.record.status = 'confirmed'
        self.record.save()
        self._post(scan_type='heartbeat', result='not_in_foal')
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'lost')
        self.assertEqual(self.record.date_scanned_heartbeat, self.today)

    def test_form_defaults_to_the_next_scan(self):
        response = self.client.get(reverse('breeding_scan', args=[self.record.pk]), **POPUP)
        self.assertEqual(response.context['form'].initial['scan_type'], '14_day')
        self.record.date_scanned_14_days = self.today
        self.record.save()
        response = self.client.get(reverse('breeding_scan', args=[self.record.pk]), **POPUP)
        self.assertEqual(response.context['form'].initial['scan_type'], 'heartbeat')
        self.assertTemplateUsed(response, 'health/partials/scan_form.html')

    def test_scan_rejects_bad_dates(self):
        response = self._post(scan_date=(self.record.date_covered - timedelta(days=1)).isoformat())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'before the mare was covered')
        response = self._post(scan_date=(self.today + timedelta(days=1)).isoformat())
        self.assertContains(response, 'cannot be in the future')
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'covered')

    def test_scan_on_a_closed_record_bounces(self):
        self.record.status = 'barren'
        self.record.save()
        response = self.client.get(reverse('breeding_scan', args=[self.record.pk]))
        self.assertRedirects(response, reverse('horse_detail', args=[self.mare.pk]))
        self.assertEqual(self._post().status_code, 204)
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'barren')

    def test_scan_needs_breeding_write_access(self):
        self.client.force_login(make_user_with_access('viewer_only', breeding='view', horses='view'))
        response = self._post()
        self.assertNotEqual(response.status_code, 204)
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, 'covered')

    def test_scan_action_is_offered_where_a_scan_is_due(self):
        url = reverse('breeding_scan', args=[self.record.pk])
        response = self.client.get(reverse('breeding_list'))
        self.assertContains(response, url)
        self.assertContains(response, '14-day scan due')
        response = self.client.get(reverse('horse_detail', args=[self.mare.pk]))
        self.assertContains(response, url)
        self.assertContains(response, 'Scan result')
        # Both scans in: no scan action, foaling stays.
        self.record.date_scanned_14_days = self.today - timedelta(days=1)
        self.record.date_scanned_heartbeat = self.today
        self.record.status = 'confirmed'
        self.record.save()
        response = self.client.get(reverse('breeding_list'))
        self.assertNotContains(response, url)
        self.assertContains(response, reverse('breeding_foaling', args=[self.record.pk]))
