"""One format for money and dates across the app (docs/UX_REVIEW_PLAN.md,
items C1 and C2).

Money goes through the ``gbp`` filter, so every page shows ``£1,470.00``
and none shows ``£1470.00``. Screens and emails show dates as
``6 Nov 2026``; ``dd/mm/yyyy`` stays only in the PDF and the Xero CSV.
"""
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from core.templatetags.ui_extras import gbp

TEMPLATES = Path(settings.BASE_DIR) / 'templates'


class GbpFilterTests(SimpleTestCase):
    def test_thousands_separator(self):
        self.assertEqual(gbp(Decimal('1470')), '£1,470.00')

    def test_rounds_half_up_like_floatformat(self):
        # floatformat:2 gives 0.13 here; gbp must agree to the penny.
        self.assertEqual(gbp(Decimal('0.125')), '£0.13')
        self.assertEqual(gbp(Decimal('2.675')), '£2.68')

    def test_negative_and_blank(self):
        self.assertEqual(gbp(Decimal('-12.5')), '-£12.50')
        self.assertEqual(gbp(Decimal('-0.001')), '£0.00')
        self.assertEqual(gbp(None), '£0.00')
        self.assertEqual(gbp(''), '£0.00')

    def test_float(self):
        self.assertEqual(gbp(2.675), '£2.68')


class TemplateFormatTests(SimpleTestCase):
    HAND_MONEY = re.compile(r'(?:£|&pound;)\s*\{\{[^}]*floatformat')
    SLASH_DATE = re.compile(r'''\|date:["']d/m/Y''')

    def _templates(self):
        return [p for p in TEMPLATES.rglob('*') if p.suffix in ('.html', '.txt')]

    def test_no_hand_built_money(self):
        bad = [str(p.relative_to(TEMPLATES)) for p in self._templates()
               if self.HAND_MONEY.search(p.read_text())]
        self.assertEqual(bad, [], 'Use {{ value|gbp }} for money.')

    def test_no_slash_dates_on_screen(self):
        bad = [str(p.relative_to(TEMPLATES)) for p in self._templates()
               if self.SLASH_DATE.search(p.read_text())]
        self.assertEqual(bad, [], 'Use |date:"j M Y" on screens and in emails.')
