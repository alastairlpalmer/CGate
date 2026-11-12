"""The maths that draws a barn (core.stables), with no database.

``plan_cells`` turns a layout and a number of cells into rows and columns.
The properties checked here are the ones a wrong formula breaks first: two
stables drawn on top of each other, a stable drawn inside the yard, and a
courtyard whose sides touch.
"""

from django.test import SimpleTestCase

from core.models import Location
from core.stables import ROW_WRAP, next_labels, plan_cells

COUNTS = (1, 4, 5, 6, 7, 12, 13, 20, 21, 30)
LAYOUTS = (Location.Layout.COURTYARD, Location.Layout.AISLE, Location.Layout.ROW)


def _feature_squares(plan):
    squares = set()
    for feature in plan['features']:
        for row in range(feature['row'], feature['row'] + feature['row_span']):
            for col in range(feature['col'], feature['col'] + feature['col_span']):
                squares.add((row, col))
    return squares


class PlanCellsTests(SimpleTestCase):
    def test_every_cell_has_its_own_square_inside_the_grid(self):
        for layout in LAYOUTS:
            for count in COUNTS:
                with self.subTest(layout=layout, count=count):
                    plan = plan_cells(layout, count)
                    self.assertEqual(len(plan['cells']), count)
                    self.assertEqual(len(set(plan['cells'])), count)
                    for row, col in plan['cells']:
                        self.assertTrue(1 <= row <= plan['rows'])
                        self.assertTrue(1 <= col <= plan['cols'])

    def test_no_cell_is_drawn_inside_a_feature(self):
        for layout in LAYOUTS:
            for count in COUNTS:
                with self.subTest(layout=layout, count=count):
                    plan = plan_cells(layout, count)
                    self.assertFalse(set(plan['cells']) & _feature_squares(plan))

    def test_features_stay_inside_the_grid(self):
        for layout in LAYOUTS:
            for count in COUNTS:
                with self.subTest(layout=layout, count=count):
                    plan = plan_cells(layout, count)
                    for feature in plan['features']:
                        self.assertGreaterEqual(feature['row_span'], 1)
                        self.assertGreaterEqual(feature['col_span'], 1)
                        self.assertLessEqual(
                            feature['col'] + feature['col_span'] - 1, plan['cols'])

    def test_no_cells_is_an_empty_plan(self):
        self.assertEqual(
            plan_cells(Location.Layout.COURTYARD, 0),
            {'cols': 0, 'rows': 0, 'cells': [], 'features': []},
        )

    # ── courtyard ────────────────────────────────────────────────────
    def test_courtyard_of_twenty_is_eight_across_and_six_down_each_side(self):
        plan = plan_cells(Location.Layout.COURTYARD, 20)
        self.assertEqual((plan['cols'], plan['rows']), (8, 7))
        self.assertEqual(plan['cells'][:8], [(1, col) for col in range(1, 9)])
        # Clockwise: down the right...
        self.assertEqual(plan['cells'][8:14], [(row, 8) for row in range(2, 8)])
        # ...and back up the left.
        self.assertEqual(plan['cells'][14:], [(row, 1) for row in range(7, 1, -1)])
        self.assertEqual(plan['features'], [
            {'kind': 'yard', 'row': 2, 'col': 2, 'row_span': 6, 'col_span': 6},
        ])

    def test_courtyard_of_six_still_has_a_yard(self):
        # count // 3 alone gives two a side and a top row of two: the
        # sides touch and the yard has no width.
        plan = plan_cells(Location.Layout.COURTYARD, 6)
        self.assertEqual(plan['cols'], 4)
        self.assertEqual(plan['features'][0]['col_span'], 2)

    def test_courtyard_top_row_is_never_under_three(self):
        for count in range(5, 61):
            with self.subTest(count=count):
                plan = plan_cells(Location.Layout.COURTYARD, count)
                self.assertGreaterEqual(plan['cols'], 3)
                self.assertEqual(plan['features'][0]['kind'], 'yard')

    def test_courtyard_under_five_falls_back_to_a_row(self):
        plan = plan_cells(Location.Layout.COURTYARD, 4)
        self.assertEqual(plan['cells'], [(1, 1), (1, 2), (1, 3), (1, 4)])
        self.assertEqual(plan['features'][0]['kind'], 'walkway')

    # ── centre aisle ─────────────────────────────────────────────────
    def test_aisle_puts_the_odd_one_on_the_first_side(self):
        plan = plan_cells(Location.Layout.AISLE, 7)
        self.assertEqual(plan['cols'], 4)
        self.assertEqual(plan['cells'][:4], [(1, 1), (1, 2), (1, 3), (1, 4)])
        self.assertEqual(plan['cells'][4:], [(3, 1), (3, 2), (3, 3)])
        self.assertEqual(plan['features'], [
            {'kind': 'aisle', 'row': 2, 'col': 1, 'row_span': 1, 'col_span': 4},
        ])

    def test_aisle_of_one_falls_back_to_a_row(self):
        self.assertEqual(plan_cells(Location.Layout.AISLE, 1)['features'][0]['kind'], 'walkway')

    # ── row ──────────────────────────────────────────────────────────
    def test_row_wraps(self):
        plan = plan_cells(Location.Layout.ROW, ROW_WRAP + 1)
        self.assertEqual(plan['cols'], ROW_WRAP)
        self.assertEqual(plan['cells'][ROW_WRAP - 1], (1, ROW_WRAP))
        self.assertEqual(plan['cells'][ROW_WRAP], (3, 1))
        self.assertEqual([f['row'] for f in plan['features']], [2, 4])

    def test_unknown_layout_draws_a_row(self):
        self.assertEqual(plan_cells('', 3)['cells'], [(1, 1), (1, 2), (1, 3)])


class NextLabelsTests(SimpleTestCase):
    def test_an_empty_barn_starts_at_one(self):
        self.assertEqual(next_labels([], 3), ['1', '2', '3'])

    def test_first_number(self):
        self.assertEqual(next_labels([], 3, first_number=21), ['21', '22', '23'])

    def test_continues_from_the_highest_number(self):
        self.assertEqual(next_labels(['1', '2', '9'], 2), ['10', '11'])

    def test_names_are_not_numbers(self):
        self.assertEqual(next_labels(['Foaling', 'Tack room', '4'], 2), ['5', '6'])

    def test_only_names_starts_at_one(self):
        self.assertEqual(next_labels(['Foaling'], 1), ['1'])

    def test_a_taken_number_is_stepped_over(self):
        self.assertEqual(next_labels(['2'], 3, first_number=1), ['1', '3', '4'])
