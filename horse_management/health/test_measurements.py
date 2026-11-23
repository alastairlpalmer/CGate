"""Weight and condition over time (Dev board "Advanced record keeping",
23 Nov 2026): health.models.HorseMeasurement, health/measurements.py and
the horse page's card."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse
from core.roles_testutils import make_admin, make_user_with_access
from health.forms import MeasurementForm
from health.measurements import summary, weight_chart
from health.models import HorseMeasurement

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class Base(TestCase):

    def setUp(self):
        self.today = timezone.localdate()
        self.client.force_login(make_admin())
        self.horse = Horse.objects.create(name='Avicii', height_inches=64)

    def measure(self, days_ago, **values):
        return HorseMeasurement.objects.create(horse=self.horse, date=self.today - timedelta(days=days_ago), **values)

    def page(self):
        return self.client.get(reverse('horse_detail', args=[self.horse.pk])).content.decode()


class FormTests(Base):

    def form(self, **data):
        base = {'horse': self.horse.pk, 'date': self.today.isoformat()}
        base.update(data)
        return MeasurementForm(base)

    def test_needs_one_value(self):
        form = self.form()
        self.assertFalse(form.is_valid())
        self.assertIn('Enter at least one of weight, height, body condition or girth.', form.non_field_errors())
        self.assertTrue(self.form(girth_cm='190').is_valid())

    def test_height_in_hands_and_checks(self):
        form = self.form(height='16.2')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().height_inches, 66)
        self.assertIn('inches, 0 to 3', str(self.form(height='16.5').errors))
        self.assertIn('cannot be in the future', str(self.form(weight_kg='500', date=(self.today + timedelta(days=1)).isoformat()).errors))
        self.assertFalse(self.form(weight_kg='5').is_valid())
        self.assertTrue(self.form(body_condition='3.0').is_valid())
        self.assertFalse(self.form(body_condition='3.3').is_valid())


class SaveTests(Base):

    def test_record_from_the_horse_page(self):
        url = reverse('measurement_create') + f'?horse={self.horse.pk}'
        response = self.client.post(url, {'horse': self.horse.pk, 'date': self.today.isoformat(),
                                          'weight_kg': '512.5', 'height': '16.1'})
        self.assertRedirects(response, reverse('horse_detail', args=[self.horse.pk]))
        row = HorseMeasurement.objects.get()
        self.assertEqual((row.weight_kg, row.height_inches), (Decimal('512.5'), 65))
        self.horse.refresh_from_db()
        self.assertEqual(self.horse.height_inches, 65)  # the latest height is the horse's height

    def test_an_older_height_does_not_change_the_horse(self):
        self.measure(0, height_inches=66)
        self.horse.refresh_from_db()
        self.client.post(reverse('measurement_create') + f'?horse={self.horse.pk}',
                         {'horse': self.horse.pk, 'date': (self.today - timedelta(days=30)).isoformat(), 'height': '15.3'})
        self.horse.refresh_from_db()
        self.assertEqual(self.horse.height_inches, 64)  # unchanged: 66 was set by .create, not the form

    def test_popup_edit_and_delete(self):
        row = self.measure(3, weight_kg=Decimal('500'))
        response = self.client.get(reverse('measurement_update', args=[row.pk]), **POPUP)
        self.assertContains(response, 'name="weight_kg"')
        self.client.post(reverse('measurement_update', args=[row.pk]),
                         {'horse': self.horse.pk, 'date': row.date.isoformat(), 'weight_kg': '505'})
        row.refresh_from_db()
        self.assertEqual(row.weight_kg, Decimal('505'))
        self.assertEqual(self.client.get(reverse('measurement_delete', args=[row.pk])).status_code, 405)
        self.client.post(reverse('measurement_delete', args=[row.pk]))
        self.assertFalse(HorseMeasurement.objects.exists())


class CardTests(Base):

    def test_no_card_until_the_first_measurement_and_a_tile_to_add_it(self):
        body = self.page()
        self.assertNotIn('data-testid="measurements-card"', body)
        self.assertIn('data-testid="weight-tile"', body)

    def test_latest_values_change_and_chart(self):
        self.measure(60, weight_kg=Decimal('500'), body_condition=Decimal('2.5'))
        self.measure(30, weight_kg=Decimal('506'))
        self.measure(2, weight_kg=Decimal('512.5'), girth_cm=Decimal('190'), body_condition=Decimal('3.0'))
        body = self.page()
        card = body.split('data-testid="measurements-card"', 1)[1].split('</section>', 1)[0]
        self.assertIn('512.5 kg', card)
        self.assertIn('6.5 kg more since', card)
        self.assertIn('Up 0.5 since', card)
        self.assertIn('190 cm', card)
        self.assertIn('data-testid="weight-chart"', card)
        self.assertIn('aria-labelledby="weight-chart-title"', card)
        self.assertEqual(card.count('class="measure-dot"'), 3)

    def test_one_weight_has_no_chart(self):
        self.measure(2, weight_kg=Decimal('512'))
        self.assertNotIn('data-testid="weight-chart"', self.page())

    def test_a_viewer_sees_it_without_buttons(self):
        self.measure(2, weight_kg=Decimal('512'))
        self.client.force_login(make_user_with_access('looker', horses='view', health='view'))
        card = self.page().split('data-testid="measurements-card"', 1)[1].split('</section>', 1)[0]
        self.assertIn('512 kg', card)
        self.assertNotIn('Edit', card)
        self.assertNotIn(reverse('measurement_create'), card)

    def test_no_health_access_no_card(self):
        self.measure(2, weight_kg=Decimal('512'))
        self.client.force_login(make_user_with_access('yard', horses='view', health='hidden'))
        self.assertNotIn('data-testid="measurements-card"', self.page())


class ChartTests(Base):

    def test_geometry(self):
        self.measure(20, weight_kg=Decimal('500'))
        self.measure(10, weight_kg=Decimal('500'))  # flat: drawn flat in a 10 kg band
        self.measure(0, weight_kg=Decimal('500'))
        chart = weight_chart(list(self.horse.measurements.all()))
        self.assertEqual(len({m['y'] for m in chart['marks']}), 1)
        self.assertEqual((chart['low'], chart['high']), (495, 505))
        self.assertEqual(len(chart['segments']), 2)
        self.assertEqual((chart['marks'][0]['x'], chart['marks'][-1]['x']), (2.5, 97.5))
        self.assertEqual(chart['last']['kg'], '500')
        self.assertIn('3 measurements', chart['summary'])
        self.assertIsNone(summary(Horse.objects.create(name='Empty')))
