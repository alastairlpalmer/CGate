"""The horse list's preview pane and its sheet on a phone.

Picking a horse on the list loads horses/partials/preview.html into the
pane beside it (core.views.horses.horse_preview) instead of navigating.
The same partial is the body of the bottom sheet on a narrow screen.

Covered here: what the pane says, that only the sheet's own request gets
it, that the list carries the hooks static/js/split_view.js needs, and
that a departure logged from the pane comes back to the list.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Document, Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from health.models import (
    FarrierVisit,
    Vaccination,
    VaccinationType,
    VetVisit,
    WormingTreatment,
)

PANE = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'horse-preview-body'}
BOOSTED = {
    'HTTP_HX_REQUEST': 'true',
    'HTTP_HX_BOOSTED': 'true',
    'HTTP_HX_TARGET': 'main-content',
}


class PreviewFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.owner = Owner.objects.create(name='Milly Hine')
        cls.location = Location.objects.create(
            name='Grain store paddock', site='Colgate Farm',
        )
        cls.rate = RateType.objects.create(
            name='Grass Livery incl hay', daily_rate=Decimal('5.50'),
        )
        cls.horse = Horse.objects.create(
            name='Antoinette', color=Horse.Color.BAY, sex=Horse.Sex.MARE,
            date_of_birth=date(cls.today.year - 19, 1, 1),
            has_passport=False,
        )
        cls.placement = Placement.objects.create(
            horse=cls.horse, owner=cls.owner, location=cls.location,
            rate_type=cls.rate, start_date=cls.today - timedelta(days=11),
        )
        cls.url = reverse('horse_preview', args=[cls.horse.pk])

    def pane(self, horse=None):
        url = reverse('horse_preview', args=[(horse or self.horse).pk])
        return self.client.get(url, **PANE).content.decode()


class PreviewAccessTests(PreviewFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def test_only_the_pane_gets_the_partial(self):
        """A direct visit, or a boosted navigation, lands on the horse's
        own page — so every row on the list is still an ordinary link."""
        profile = reverse('horse_detail', args=[self.horse.pk])
        self.assertRedirects(self.client.get(self.url), profile)
        self.assertRedirects(self.client.get(self.url, **BOOSTED), profile)

    def test_the_pane_request_renders_the_partial(self):
        response = self.client.get(self.url, **PANE)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'horses/partials/preview.html')
        self.assertNotIn(b'<html', response.content)

    def test_the_partial_carries_the_id_the_swap_selects_on(self):
        """split_view.js asks htmx for #horse-preview-content by name: with
        no source element htmx falls back to <body>, whose hx-select is
        #main-content, and a partial holds none."""
        self.assertIn('id="horse-preview-content"', self.pane())

    def test_a_missing_horse_is_a_404(self):
        response = self.client.get(reverse('horse_preview', args=[99999]), **PANE)
        self.assertEqual(response.status_code, 404)

    def test_a_role_without_horses_cannot_open_it(self):
        self.client.force_login(make_user_with_access('nohorses', horses='hidden'))
        response = self.client.get(self.url, **PANE)
        self.assertNotEqual(response.status_code, 200)


class PreviewContentTests(PreviewFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def test_it_names_the_horse_and_flags_a_missing_passport(self):
        body = self.pane()
        self.assertIn('Antoinette', body)
        self.assertIn('No passport', body)

    def test_it_says_where_the_horse_is_what_it_costs_and_how_long(self):
        body = self.pane()
        self.assertIn('Grain store paddock', body)
        self.assertIn('£5.50/day', body)
        self.assertIn('Grass Livery incl hay', body)
        # 11 days on site reads as weeks and days, not a bare number.
        self.assertIn('1 week 4 days', body)

    def test_a_horse_with_no_placement_says_so(self):
        spare = Horse.objects.create(name='Gata', sex=Horse.Sex.MARE)
        self.assertIn('Not on the yard', self.pane(spare))

    def test_microchip_reads_not_recorded_until_one_is_entered(self):
        self.assertIn('Not recorded', self.pane())
        self.horse.microchip = '985141000123456'
        self.horse.save(update_fields=['microchip'])
        self.assertIn('985141000123456', self.pane())

    def test_a_passport_number_shows_instead_of_the_warning(self):
        self.horse.has_passport = True
        self.horse.passport_number = 'GB40012345'
        self.horse.save(update_fields=['has_passport', 'passport_number'])
        body = self.pane()
        self.assertIn('GB40012345', body)
        self.assertNotIn('No passport', body)

    def test_documents_are_listed_and_an_empty_shelf_says_so(self):
        self.assertIn('Nothing on file.', self.pane())
        Document.objects.create(
            horse=self.horse, doc_type=Document.DocType.PASSPORT,
            title='pport', file='documents/2026/09/pport.pdf',
        )
        self.assertIn('pport', self.pane())

    def test_the_timeline_lists_what_has_happened(self):
        FarrierVisit.objects.create(
            horse=self.horse, date=self.today - timedelta(days=39),
            work_done='trim',
        )
        VetVisit.objects.create(
            horse=self.horse, date=self.today - timedelta(days=167),
            reason='Routine dental check',
        )
        body = self.pane()
        self.assertIn('Moved to Grain store paddock', body)
        self.assertIn('Routine dental check', body)


class CredentialDueDateTests(PreviewFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def test_vaccination_rows_are_named_by_the_yard_s_own_types(self):
        """Flu and Tetanus are rows in the database, not fields on the
        horse, so a yard running its own programme gets its own labels."""
        flu = VaccinationType.objects.create(name='Flu vaccination')
        strangles = VaccinationType.objects.create(name='Strangles')
        for vaccination_type in (flu, strangles):
            Vaccination.objects.create(
                horse=self.horse, vaccination_type=vaccination_type,
                date_given=self.today - timedelta(days=30),
            )
        body = self.pane()
        self.assertIn('Flu vaccination', body)
        self.assertIn('Strangles', body)

    def test_only_the_latest_dose_of_a_type_sets_the_due_date(self):
        """An old booster's due date is in the past forever; left in, every
        horse would read as overdue the day after it was re-done."""
        flu = VaccinationType.objects.create(name='Flu vaccination')
        Vaccination.objects.create(
            horse=self.horse, vaccination_type=flu,
            date_given=self.today - timedelta(days=800),
        )
        current = Vaccination.objects.create(
            horse=self.horse, vaccination_type=flu,
            date_given=self.today - timedelta(days=10),
        )
        body = self.pane()
        self.assertIn(current.next_due_date.strftime('%d %b %Y'), body)
        self.assertEqual(body.count('Flu vaccination</dt>'), 1)

    def test_farrier_and_worming_both_carry_a_due_date(self):
        FarrierVisit.objects.create(
            horse=self.horse, date=self.today - timedelta(days=14), work_done='trim',
        )
        worming = WormingTreatment.objects.create(
            horse=self.horse, date=self.today - timedelta(days=14),
            product_name='Equest Pramox',
        )
        body = self.pane()
        self.assertIn('Farrier', body)
        self.assertIn('Worming', body)
        self.assertIn(worming.next_due_date.strftime('%d %b %Y'), body)

    def test_nothing_recorded_reads_as_not_recorded_not_as_overdue(self):
        body = self.pane()
        self.assertIn('Farrier', body)
        self.assertIn('Not recorded', body)

    def test_an_overdue_date_is_marked(self):
        WormingTreatment.objects.create(
            horse=self.horse, date=self.today - timedelta(days=365),
            product_name='Equest Pramox',
        )
        self.assertIn('is-alert', self.pane())


class WormingDueDateTests(TestCase):
    """The due date added for the Credentials block."""

    def setUp(self):
        self.horse = Horse.objects.create(name='Beech', sex=Horse.Sex.MARE)
        self.today = timezone.localdate()

    def test_it_defaults_to_thirteen_weeks_after_the_dose(self):
        treatment = WormingTreatment.objects.create(
            horse=self.horse, date=self.today, product_name='Equest Pramox',
        )
        self.assertEqual(treatment.next_due_date, self.today + timedelta(weeks=13))

    def test_a_date_that_was_typed_in_is_kept(self):
        chosen = self.today + timedelta(days=40)
        treatment = WormingTreatment.objects.create(
            horse=self.horse, date=self.today, product_name='Panacur',
            next_due_date=chosen,
        )
        treatment.refresh_from_db()
        self.assertEqual(treatment.next_due_date, chosen)

    def test_due_soon_and_overdue_read_off_the_date(self):
        due_soon = WormingTreatment.objects.create(
            horse=self.horse, date=self.today - timedelta(weeks=12),
            product_name='Equest Pramox',
        )
        overdue = WormingTreatment.objects.create(
            horse=self.horse, date=self.today - timedelta(weeks=30),
            product_name='Panacur',
        )
        self.assertTrue(due_soon.is_due_soon)
        self.assertFalse(due_soon.is_overdue)
        self.assertTrue(overdue.is_overdue)


class SplitViewMarkupTests(PreviewFixture):
    """What the list page has to carry for static/js/split_view.js."""

    def setUp(self):
        self.client.force_login(make_admin())
        self.body = self.client.get(reverse('horse_list')).content.decode()

    def test_the_list_carries_the_pane_and_its_url_template(self):
        self.assertIn('data-split-view', self.body)
        self.assertIn('id="horse-preview-body"', self.body)
        # The key is stamped in by the script; the template holds a 0.
        self.assertIn(f'data-preview-url="{reverse("horse_preview", args=[0])}"', self.body)

    def test_every_row_names_its_horse_and_keeps_its_link(self):
        self.assertIn(f'data-horse-pk="{self.horse.pk}"', self.body)
        self.assertIn('data-horse-link', self.body)
        self.assertIn(reverse('horse_detail', args=[self.horse.pk]), self.body)

    def test_the_pane_is_hidden_until_a_horse_is_picked(self):
        self.assertIn('<aside id="horse-preview" class="split-view-pane" hidden', self.body)

    def test_the_columns_the_pane_repeats_are_marked(self):
        self.assertIn('split-optional', self.body)

    def test_tick_boxes_wait_for_the_select_switch(self):
        self.assertIn('data-select-cell', self.body)
        self.assertIn('toggleSelectMode()', self.body)


class DepartFromThePaneTests(PreviewFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def test_a_departure_comes_back_to_where_it_was_logged(self):
        response = self.client.post(
            reverse('horse_depart', args=[self.horse.pk]) + '?next=/horses/',
            {'departure_date': self.today.isoformat()},
        )
        self.assertRedirects(response, '/horses/')

    def test_without_a_next_it_still_lands_on_the_horse(self):
        response = self.client.post(
            reverse('horse_depart', args=[self.horse.pk]),
            {'departure_date': self.today.isoformat()},
        )
        self.assertRedirects(response, reverse('horse_detail', args=[self.horse.pk]))

    def test_an_off_site_next_is_refused(self):
        response = self.client.post(
            reverse('horse_depart', args=[self.horse.pk]) + '?next=https://evil.test/',
            {'departure_date': self.today.isoformat()},
        )
        self.assertRedirects(response, reverse('horse_detail', args=[self.horse.pk]))


class PreviewQueryCountTests(PreviewFixture):
    """The pane is opened once per horse while flicking through a list, so
    it has to stay cheap. The cap has headroom over the measured count."""

    def setUp(self):
        self.client.force_login(make_admin())
        flu = VaccinationType.objects.create(name='Flu vaccination')
        tetanus = VaccinationType.objects.create(name='Tetanus')
        for vaccination_type in (flu, tetanus):
            for weeks in (4, 60):
                Vaccination.objects.create(
                    horse=self.horse, vaccination_type=vaccination_type,
                    date_given=self.today - timedelta(weeks=weeks),
                )
        for weeks in (2, 8, 14):
            FarrierVisit.objects.create(
                horse=self.horse, date=self.today - timedelta(weeks=weeks),
                work_done='trim',
            )
            WormingTreatment.objects.create(
                horse=self.horse, date=self.today - timedelta(weeks=weeks),
                product_name='Equest Pramox',
            )

    def test_opening_the_pane_stays_under_the_cap(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, **PANE)
        self.assertEqual(response.status_code, 200)
        self.assertLess(
            len(queries), 30,
            'the preview pane got more expensive:\n'
            + '\n'.join(q['sql'][:120] for q in queries),
        )
