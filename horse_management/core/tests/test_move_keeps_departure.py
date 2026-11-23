"""A move keeps the horse's expected departure date.

Until 23 November 2026 a move lost it. The group form has no box for the
date, so the new stay had none. The single form has the box, and it
started empty, so the date was lost unless the person typed it again. A
move inside the yard does not change the day the horse leaves the yard.
"""

from datetime import timedelta

from django.urls import reverse

from core.models import Horse
from core.services import PlacementService
from core.tests.test_location_move_horses import POPUP, MoveFixture


class DepartureFixture(MoveFixture):
    def setUp(self):
        super().setUp()
        self.leaves = self.today + timedelta(days=10)
        self.expect(self.ace, self.leaves)

    def expect(self, horse, day):
        stay = horse.placements.get(end_date__isnull=True)
        stay.expected_departure = day
        stay.save()

    def expected(self, horse):
        return Horse.objects.get(pk=horse.pk).placements.get(end_date__isnull=True).expected_departure


class GroupMoveTests(DepartureFixture):
    def test_the_new_stay_has_the_date_of_the_old_one(self):
        moved, failed = PlacementService.move_group(
            [self.ace, self.bella], new_location=self.clover, move_date=self.today)
        self.assertEqual(failed, [])
        self.assertEqual(self.expected(self.ace), self.leaves)
        # A horse with no date gets none.
        self.assertIsNone(self.expected(self.bella))

    def test_a_horse_that_leaves_on_the_day_of_the_move_keeps_the_date(self):
        self.expect(self.ace, self.today)
        PlacementService.move_group([self.ace], new_location=self.clover, move_date=self.today)
        self.assertEqual(self.expected(self.ace), self.today)

    def test_a_date_that_passed_is_not_taken(self):
        # The horse is still here, so the date is out of date. And a stay
        # cannot be closed on a day before it began.
        self.expect(self.ace, self.today - timedelta(days=2))
        PlacementService.move_group([self.ace], new_location=self.clover, move_date=self.today)
        self.assertIsNone(self.expected(self.ace))

    def test_a_move_dated_before_today_compares_with_the_move_date(self):
        day = self.today - timedelta(days=5)
        self.expect(self.ace, self.today - timedelta(days=2))
        PlacementService.move_group([self.ace], new_location=self.clover, move_date=day)
        self.assertEqual(self.expected(self.ace), self.today - timedelta(days=2))

    def test_move_horses_on_a_location_keeps_it(self):
        response = self.client.post(self.url, self.data(self.ace, self.bella), **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.where(self.ace), self.clover)
        self.assertEqual(self.expected(self.ace), self.leaves)

    def test_the_bulk_action_on_the_horse_list_keeps_it(self):
        response = self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'move', 'horse_ids': [self.ace.pk],
            'new_location': self.clover.pk, 'move_date': self.today.isoformat(), 'notes': '',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.expected(self.ace), self.leaves)

    def test_the_sheet_says_that_the_date_is_kept(self):
        sheet = self.client.get(self.url, **POPUP).content.decode()
        self.assertIn('Each horse keeps its owner and its expected departure date.', sheet)


class SingleMoveTests(DepartureFixture):
    def url_for(self, horse):
        return reverse('horse_move', args=[horse.pk])

    def post(self, horse, **data):
        return self.client.post(self.url_for(horse), {
            'new_location': self.clover.pk, 'move_date': self.today.isoformat(), 'notes': '', **data,
        }, **POPUP)

    def test_the_box_starts_with_the_date_of_the_stay(self):
        sheet = self.client.get(self.url_for(self.ace), **POPUP).content.decode()
        box = sheet.split('name="expected_departure"', 1)[1].split('>', 1)[0]
        self.assertIn(f'value="{self.leaves.isoformat()}"', box)

    def test_the_kept_date_does_not_open_more_options_and_the_line_names_it(self):
        sheet = self.client.get(self.url_for(self.ace), **POPUP).content.decode()
        block = sheet.split('<details', 1)[1].split('>', 1)[0]
        self.assertNotRegex(block, r'\bopen\b')
        self.assertIn(
            f'<span data-testid="move-keeps-departure">({self.leaves.day} {self.leaves:%b})</span>', sheet)

    def test_a_horse_with_no_date_has_an_empty_box(self):
        sheet = self.client.get(self.url_for(self.bella), **POPUP).content.decode()
        box = sheet.split('name="expected_departure"', 1)[1].split('>', 1)[0]
        self.assertNotIn('value=', box)
        self.assertNotIn('move-keeps-departure', sheet)

    def test_a_date_that_passed_is_not_in_the_box(self):
        self.expect(self.ace, self.today - timedelta(days=1))
        sheet = self.client.get(self.url_for(self.ace), **POPUP).content.decode()
        box = sheet.split('name="expected_departure"', 1)[1].split('>', 1)[0]
        self.assertNotIn('value=', box)

    def test_a_save_with_the_box_as_it_started_keeps_the_date(self):
        response = self.post(self.ace, expected_departure=self.leaves.isoformat())
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.where(self.ace), self.clover)
        self.assertEqual(self.expected(self.ace), self.leaves)

    def test_the_person_can_change_the_date_or_take_it_out(self):
        later = self.leaves + timedelta(days=30)
        self.post(self.ace, expected_departure=later.isoformat())
        self.assertEqual(self.expected(self.ace), later)
        self.expect(self.clio, self.leaves)
        self.post(self.clio, expected_departure='')
        self.assertIsNone(self.expected(self.clio))

    def test_a_form_that_comes_back_with_an_error_shows_the_date_the_person_sent(self):
        # No location: the form comes back. The block is open, because the
        # date in it is now the person's.
        response = self.client.post(self.url_for(self.ace), {
            'new_location': '', 'move_date': self.today.isoformat(), 'notes': '',
            'expected_departure': self.leaves.isoformat(),
        }, **POPUP)
        sheet = response.content.decode()
        block = sheet.split('<details', 1)[1].split('>', 1)[0]
        self.assertRegex(block, r'\bopen\b')
        self.assertNotIn('move-keeps-departure', sheet)

    def test_the_full_page_form_has_the_date_also(self):
        page = self.client.get(self.url_for(self.ace)).content.decode()
        box = page.split('name="expected_departure"', 1)[1].split('>', 1)[0]
        self.assertIn(f'value="{self.leaves.isoformat()}"', box)
