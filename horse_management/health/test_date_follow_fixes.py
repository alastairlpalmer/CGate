"""Due dates and scan reminders follow a corrected or repeated date.

- Correcting a mare's covering date moves the foal due date with it (the
  edit form sends the stored due date back, which used to read as a date
  typed in by hand).
- Covering a mare again re-arms the 14-day and heartbeat scan reminders.
- Correcting a worming dose date moves the next due date with it.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Owner, OwnershipShare
from core.roles_testutils import make_admin
from health.models import BreedingRecord, WormingTreatment
from health.services import add_covering, record_scan
from notifications.tasks import breeding_reminder_entries, send_breeding_reminders

TODAY = timezone.localdate()


def owned_horse(name, **fields):
    owner = Owner.objects.create(name=f'{name} owner', email=f'{name.lower()}@example.com')
    horse = Horse.objects.create(name=name, **fields)
    OwnershipShare.objects.create(
        horse=horse, owner=owner, share_percentage=Decimal('100'), is_primary_contact=True,
    )
    return horse


class DateFollowTests(TestCase):
    def setUp(self):
        self.client.force_login(make_admin())

    def _form_data(self, url):
        form = self.client.get(url).context['form']
        return {name: (form[name].value() or '') for name in form.fields}

    def test_correcting_the_covering_date_moves_the_foal_due_date(self):
        mare = Horse.objects.create(name='Duchess', sex='mare')
        record = BreedingRecord.objects.create(
            mare=mare, stallion_name='Storm', date_covered=TODAY - timedelta(days=40),
        )
        url = reverse('breeding_update', args=[record.pk])
        data = self._form_data(url)
        new_covering = TODAY - timedelta(days=20)
        data['date_covered'] = new_covering.isoformat()
        data['date_foal_due'] = record.date_foal_due.isoformat()  # sent back unchanged
        self.assertEqual(self.client.post(url, data).status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.date_foal_due, new_covering + timedelta(days=340))
        self.assertFalse(record.due_date_is_manual)

    def test_a_due_date_typed_in_is_still_kept(self):
        mare = Horse.objects.create(name='Duchess', sex='mare')
        record = BreedingRecord.objects.create(
            mare=mare, stallion_name='Storm', date_covered=TODAY - timedelta(days=40),
        )
        url = reverse('breeding_update', args=[record.pk])
        data = self._form_data(url)
        typed = TODAY + timedelta(days=310)
        data['date_foal_due'] = typed.isoformat()
        self.client.post(url, data)
        record.refresh_from_db()
        self.assertEqual(record.date_foal_due, typed)
        self.assertTrue(record.due_date_is_manual)

    def test_covering_again_re_arms_the_scan_reminder(self):
        mare = owned_horse('Duchess', sex='mare')
        record = BreedingRecord.objects.create(
            mare=mare, stallion_name='Storm', date_covered=TODAY - timedelta(days=20),
        )
        send_breeding_reminders()  # the 14-day scan was due 6 days ago
        record.refresh_from_db()
        self.assertIn('scan14', record.sent_reminder_keys)
        record_scan(record, scan_type='14_day', scan_date=TODAY - timedelta(days=5), result='not_in_foal')
        record.refresh_from_db()
        add_covering(record, date=TODAY - timedelta(days=1))
        record.refresh_from_db()
        self.assertNotIn('scan14', record.sent_reminder_keys)
        new_scan_day = TODAY - timedelta(days=1) + timedelta(days=14)
        keys = [entry['key'] for entry in breeding_reminder_entries(record, new_scan_day)]
        self.assertIn('scan14', keys)

    def test_correcting_the_worming_dose_date_moves_the_due_date(self):
        horse = owned_horse('Bluebell')
        dose = WormingTreatment.objects.create(
            horse=horse, date=TODAY - timedelta(days=100), product_name='Equest',
        )
        url = reverse('worming_update', args=[dose.pk])
        data = self._form_data(url)
        data['horse'] = horse.pk
        data['date'] = (TODAY - timedelta(days=10)).isoformat()
        data['next_due_date'] = dose.next_due_date.isoformat()  # sent back unchanged
        self.assertEqual(self.client.post(url, data).status_code, 302)
        dose.refresh_from_db()
        self.assertEqual(dose.next_due_date, dose.date + timedelta(weeks=13))
