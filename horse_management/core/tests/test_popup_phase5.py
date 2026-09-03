"""Phase 5 of the pop-up sheet: Record payment, Feed out, Log arrival,
Add document, Placement edit, health record edits, Add owner/location,
and the app-bar Add menu."""

import io
import shutil
import tempfile
from datetime import date, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.forms import FeedOutForm
from billing.models import FeedOut
from core.forms import DocumentForm, LocationForm
from core.models import Document, Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_viewer
from health.models import Vaccination, VaccinationType
from invoicing.models import Invoice, Payment

TEMP_MEDIA = tempfile.mkdtemp(prefix='cgate-popup5-tests-')
POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


def _first_choice(form_class, field_name, **kwargs):
    form = form_class(**kwargs)
    for value, _label in form.fields[field_name].choices:
        if value not in ('', None):
            return value
    raise AssertionError(f'{form_class.__name__}.{field_name} has no choices')


def _jpeg(name='scan.jpg'):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (32, 32), 'blue').save(buffer, format='JPEG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class Phase5TestCase(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.today = timezone.localdate()
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.location = Location.objects.create(name='Top Field', site='Main')
        self.other_location = Location.objects.create(name='Bottom Field', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)
        self.horse = Horse.objects.create(name='Dobbin')
        self.placement = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=30),
        )
        self.loose = Horse.objects.create(name='Loose')  # no placement
        self.client.force_login(make_admin())

    def _invoice(self, total=100):
        invoice = Invoice.objects.create(
            owner=self.owner, invoice_number='INV-9001',
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31),
        )
        Invoice.objects.filter(pk=invoice.pk).update(subtotal=total, total=total)
        invoice.refresh_from_db()
        return invoice


class RecordPaymentSheetTests(Phase5TestCase):

    def test_popup_get_returns_the_generic_partial(self):
        invoice = self._invoice()
        url = reverse('payment_create', args=[invoice.pk])
        response = self.client.get(url, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'includes/popup_form.html')
        self.assertNotContains(response, '<html')
        self.assertContains(response, 'Record Payment')
        # Amount is prefilled with the balance due
        self.assertContains(response, 'value="100')

    def test_popup_save_answers_204(self):
        invoice = self._invoice()
        url = reverse('payment_create', args=[invoice.pk])
        response = self.client.post(url, {
            'date': self.today.isoformat(), 'amount': '40',
            'method': Payment._meta.get_field('method').choices[0][0],
            'reference': '', 'notes': '',
        }, **POPUP)
        self.assertEqual(response.status_code, 204, response.content[:300])
        self.assertEqual(response['HX-Trigger'], 'popup:saved')
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance_due, 60)

    def test_fully_paid_invoice_closes_the_sheet_with_a_message(self):
        invoice = self._invoice(total=0)
        url = reverse('payment_create', args=[invoice.pk])
        response = self.client.get(url, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['HX-Trigger'], 'popup:saved')

    def test_full_page_unchanged(self):
        invoice = self._invoice()
        response = self.client.get(reverse('payment_create', args=[invoice.pk]))
        self.assertTemplateUsed(response, 'invoicing/payment_form.html')
        self.assertContains(response, '<html')

    def test_invoice_page_offers_the_sheet(self):
        invoice = self._invoice()
        response = self.client.get(reverse('invoice_detail', args=[invoice.pk]))
        self.assertContains(response, f'data-popup-title="Record payment for {invoice.invoice_number}"')


class FeedOutSheetTests(Phase5TestCase):

    def setUp(self):
        super().setUp()
        self.url = reverse('feed_out_create', kwargs={'location_pk': self.location.pk})

    def test_popup_get_returns_the_partial_with_the_recharge_list(self):
        response = self.client.get(self.url, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'billing/partials/feed_out_form.html')
        self.assertNotContains(response, '<html')
        self.assertContains(response, f'hx-post="{self.url}"')
        self.assertContains(response, 'name="recharge_horses"')
        self.assertContains(response, 'Dobbin')
        self.assertContains(response, 'popup-footer')

    def test_popup_save_answers_204(self):
        response = self.client.post(self.url, {
            'date': self.today.isoformat(),
            'feed_type': _first_choice(FeedOutForm, 'feed_type'),
            'quantity_numeric': '2', 'unit': _first_choice(FeedOutForm, 'unit'),
            'quantity': '', 'total_cost': '12.00', 'notes': '',
        }, **POPUP)
        self.assertEqual(response.status_code, 204, response.content[:300])
        self.assertEqual(FeedOut.objects.filter(location=self.location).count(), 1)

    def test_full_page_still_includes_the_partial(self):
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, 'billing/feed_out_form.html')
        self.assertTemplateUsed(response, 'billing/partials/feed_out_form.html')
        self.assertContains(response, '<html')
        self.assertContains(response, 'form-footer')
        self.assertNotContains(response, 'hx-post=')

    def test_location_page_offers_the_sheet(self):
        response = self.client.get(reverse('location_detail', args=[self.location.pk]))
        self.assertContains(response, 'data-popup-title="Feed out at Top Field"')


class LogArrivalSheetTests(Phase5TestCase):

    def setUp(self):
        super().setUp()
        self.url = reverse('horse_arrive', args=[self.loose.pk])

    def _data(self, **overrides):
        data = {
            'location': self.location.pk, 'owner': self.owner.pk,
            'rate_type': self.rate.pk, 'arrival_date': self.today.isoformat(), 'notes': '',
        }
        data.update(overrides)
        return data

    def test_popup_get_returns_the_partial_with_today_chip(self):
        response = self.client.get(self.url, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'horses/partials/arrive_form.html')
        self.assertNotContains(response, '<html')
        self.assertContains(response, 'pickDate: false')
        self.assertContains(response, 'Pick a date')

    def test_popup_save_answers_204(self):
        response = self.client.post(self.url, self._data(), **POPUP)
        self.assertEqual(response.status_code, 204, response.content[:300])
        self.assertTrue(self.loose.placements.filter(end_date__isnull=True).exists())

    def test_service_error_shows_inline(self):
        # Dobbin is already placed: the service refuses a second open stay
        response = self.client.post(reverse('horse_arrive', args=[self.horse.pk]), self._data(), **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'horses/partials/arrive_form.html')
        self.assertContains(response, 'role="alert"')
        self.assertEqual(self.horse.placements.count(), 1)

    def test_full_page_save_still_redirects_to_the_list(self):
        response = self.client.post(self.url, self._data())
        self.assertRedirects(response, reverse('horse_list'))

    def test_horse_page_offers_the_sheet(self):
        response = self.client.get(reverse('horse_detail', args=[self.loose.pk]))
        self.assertContains(response, 'data-popup-title="Log arrival for Loose"')


class AddDocumentSheetTests(Phase5TestCase):

    def test_popup_get_and_save(self):
        url = reverse('document_create') + f'?horse={self.horse.pk}'
        response = self.client.get(url, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'includes/popup_form.html')
        self.assertContains(response, 'hx-encoding="multipart/form-data"')
        self.assertContains(response, 'Upload')

        response = self.client.post(url, {
            'doc_type': _first_choice(DocumentForm, 'doc_type'),
            'title': 'Insurance certificate', 'file': _jpeg(), 'expiry_date': '', 'notes': '',
        }, **POPUP)
        self.assertEqual(response.status_code, 204, response.content[:300])
        self.assertEqual(Document.objects.filter(horse=self.horse).count(), 1)

    def test_documents_card_offers_the_sheet(self):
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertContains(response, 'data-popup-title="Add document"')


class PlacementEditSheetTests(Phase5TestCase):

    def test_popup_get_and_save(self):
        url = reverse('placement_update', args=[self.placement.pk])
        response = self.client.get(url, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'includes/popup_form.html')
        response = self.client.post(url, {
            'horse': self.horse.pk, 'owner': self.owner.pk, 'location': self.other_location.pk,
            'rate_type': self.rate.pk, 'start_date': self.placement.start_date.isoformat(),
            'end_date': '', 'expected_departure': '', 'notes': 'corrected',
        }, **POPUP)
        self.assertEqual(response.status_code, 204, response.content[:300])
        self.placement.refresh_from_db()
        self.assertEqual(self.placement.location, self.other_location)

    def test_horse_page_offers_the_sheet(self):
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertContains(response, 'data-popup-title="Edit placement"')


class RecordEditAndCreateSheetTests(Phase5TestCase):

    def test_vaccination_edit_in_the_sheet(self):
        vax_type = VaccinationType.objects.create(name='Flu', interval_months=6)
        vax = Vaccination.objects.create(
            horse=self.horse, vaccination_type=vax_type, date_given=self.today,
        )
        url = reverse('vaccination_update', args=[vax.pk])
        response = self.client.get(url, **POPUP)
        self.assertTemplateUsed(response, 'includes/popup_form.html')
        self.assertContains(response, 'Save Changes')
        response = self.client.post(url, {
            'horse': self.horse.pk, 'vaccination_type': vax_type.pk,
            'date_given': self.today.isoformat(), 'batch_number': 'B-1',
        }, **POPUP)
        self.assertEqual(response.status_code, 204, response.content[:300])
        vax.refresh_from_db()
        self.assertEqual(vax.batch_number, 'B-1')
        response = self.client.get(reverse('health_dashboard'), {'type': 'vaccinations'})
        self.assertContains(response, 'data-popup-title="Edit vaccination"')

    def test_add_owner_and_add_location_in_the_sheet(self):
        response = self.client.post(reverse('owner_create'), {
            'name': 'New Owner', 'email': '', 'phone': '', 'address': '', 'account_code': '', 'notes': '',
        }, **POPUP)
        self.assertEqual(response.status_code, 204, response.content[:300])
        self.assertTrue(Owner.objects.filter(name='New Owner').exists())

        response = self.client.post(reverse('location_create'), {
            'name': 'New Field', 'site': 'Main', 'usage': _first_choice(LocationForm, 'usage'),
            'description': '', 'capacity': '',
        }, **POPUP)
        self.assertEqual(response.status_code, 204, response.content[:300])
        self.assertTrue(Location.objects.filter(name='New Field').exists())

    def test_app_bar_add_menu_offers_the_sheets(self):
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'data-popup-title="Add owner"')
        self.assertContains(response, 'data-popup-title="Add location"')

    def test_viewer_is_refused(self):
        self.client.force_login(make_viewer())
        self.assertEqual(self.client.get(reverse('owner_create'), **POPUP).status_code, 403)
        self.assertEqual(
            self.client.get(reverse('feed_out_create', kwargs={'location_pk': self.location.pk}), **POPUP).status_code,
            403,
        )
