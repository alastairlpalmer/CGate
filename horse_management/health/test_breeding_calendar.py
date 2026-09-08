"""Foaling calendar: the season timeline and the foaling-watch checklist."""

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse
from core.roles_testutils import make_admin, make_user_with_access
from health.calendar import default_season, season_calendar, seasons_with_foalings
from health.models import BreedingRecord
from health.services import record_foaling, record_scan

HTMX = {'HTTP_HX_REQUEST': 'true'}


class FoalingCalendarTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.client.force_login(make_admin())
        self.ada = Horse.objects.create(name='Ada', sex='mare')
        self.bella = Horse.objects.create(name='Bella', sex='mare')
        self.clover = Horse.objects.create(name='Clover', sex='mare')
        # Ada: confirmed, due in 10 days (on watch). Bella: covered, due in
        # 300 days. Clover: barren this season.
        self.ada_rec = BreedingRecord.objects.create(
            mare=self.ada, stallion_name='Hip Hop', date_covered=self.today - timedelta(days=330),
            status='confirmed', date_scanned_14_days=self.today - timedelta(days=316),
        )
        self.bella_rec = BreedingRecord.objects.create(
            mare=self.bella, stallion_name='Masterpiece', date_covered=self.today - timedelta(days=40),
        )
        self.clover_rec = BreedingRecord.objects.create(
            mare=self.clover, stallion_name='Hip Hop', date_covered=self.today - timedelta(days=330),
            status='barren',
        )

    def test_seasons_and_default(self):
        seasons = seasons_with_foalings()
        self.assertIn(self.ada_rec.date_foal_due.year, seasons)
        self.assertIn(self.bella_rec.date_foal_due.year, seasons)
        self.assertNotIn(2000, seasons)
        self.assertEqual(default_season(self.today, []), self.today.year)
        self.assertEqual(default_season(date(2001, 1, 1), [2005, 2003]), 2003)
        self.assertEqual(default_season(date(2010, 1, 1), [2005, 2003]), 2005)

    def test_season_calendar_rows_and_markers(self):
        season = self.ada_rec.date_foal_due.year
        cal = season_calendar(season, today=self.today)
        mares = [row['mare'] for row in cal['rows']]
        self.assertIn(self.ada, mares)
        self.assertEqual(cal['not_carrying'], [] if self.clover_rec.date_foal_due.year != season else [self.clover_rec])
        ada_row = next(r for r in cal['rows'] if r['mare'] == self.ada)
        kinds = {m['kind'] for m in ada_row['markers']}
        self.assertIn('covering', kinds)
        self.assertIn('ehv', kinds)
        self.assertTrue(ada_row['watch'])
        self.assertTrue(0 <= ada_row['left'] < ada_row['left'] + ada_row['width'] <= 100)
        self.assertEqual(cal['window_start'].day, 1)
        self.assertTrue(cal['months'])
        self.assertIsNotNone(cal['today_pct'])
        due_counts = sum(m['due'] for m in cal['months'])
        self.assertGreaterEqual(due_counts, 1)

    def test_born_rows_show_the_foaling(self):
        record_scan(self.ada_rec, scan_type='heartbeat', scan_date=self.today - timedelta(days=300), result='in_foal')
        foal = record_foaling(self.ada_rec, foal_dob=self.today, foal_sex='filly', foal_name='Little Ada')
        cal = season_calendar(self.today.year, today=self.today)
        row = next(r for r in cal['rows'] if r['mare'] == self.ada)
        self.assertEqual(row['progress'], 100)
        self.assertIn('born', {m['kind'] for m in row['markers']})
        self.assertIn('scan_pos', {m['kind'] for m in row['markers']})
        self.assertEqual(sum(m['born'] for m in cal['months']), 1)
        response = self.client.get(reverse('breeding_calendar'), {'season': self.today.year})
        self.assertContains(response, foal.name)

    def test_calendar_page_and_watch(self):
        response = self.client.get(reverse('breeding_calendar'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Foaling Calendar')
        self.assertContains(response, 'Foaling watch')
        self.assertEqual([br.pk for br in response.context['watch']], [self.ada_rec.pk])
        self.assertContains(response, 'Moved to foaling box')
        self.assertContains(response, reverse('breeding_foaling', args=[self.ada_rec.pk]))
        self.assertNotContains(response, f'id="watch-{self.bella_rec.pk}"')
        # A bogus season falls back to the default.
        response = self.client.get(reverse('breeding_calendar'), {'season': '1999'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(response.context['season'], response.context['seasons'])

    def test_watch_toggle_and_notes(self):
        url = reverse('breeding_watch_update', args=[self.ada_rec.pk])
        response = self.client.post(url, {'item': 'vet'}, **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'health/partials/foaling_watch_card.html')
        self.ada_rec.refresh_from_db()
        self.assertTrue(self.ada_rec.foaling_watch['items']['vet'])
        self.assertEqual(self.ada_rec.watch_done_count, 1)
        self.client.post(url, {'item': 'vet'}, **HTMX)
        self.ada_rec.refresh_from_db()
        self.assertFalse(self.ada_rec.foaling_watch['items']['vet'])
        # Unknown items are ignored; notes are saved and trimmed.
        self.client.post(url, {'item': 'bogus', 'notes': '  Camera on  '}, **HTMX)
        self.ada_rec.refresh_from_db()
        self.assertNotIn('bogus', self.ada_rec.foaling_watch['items'])
        self.assertEqual(self.ada_rec.watch_notes, 'Camera on')
        # Plain post redirects to the calendar; GET is refused.
        response = self.client.post(url, {'item': 'kit'})
        self.assertRedirects(response, reverse('breeding_calendar'))
        self.assertEqual(self.client.get(url).status_code, 400)

    def test_watch_needs_write_access_and_viewers_see_it_read_only(self):
        self.client.force_login(make_user_with_access('v', breeding='view', horses='view'))
        response = self.client.get(reverse('breeding_calendar'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse('breeding_watch_update', args=[self.ada_rec.pk]))
        response = self.client.post(reverse('breeding_watch_update', args=[self.ada_rec.pk]), {'item': 'vet'}, **HTMX)
        self.assertNotEqual(response.status_code, 200)
        self.ada_rec.refresh_from_db()
        self.assertEqual(self.ada_rec.foaling_watch, {})

    def test_links_from_list_and_dashboard(self):
        response = self.client.get(reverse('breeding_list'))
        self.assertContains(response, reverse('breeding_calendar'))
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, reverse('breeding_calendar'))
