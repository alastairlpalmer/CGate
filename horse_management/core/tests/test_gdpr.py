"""Owner personal data: export, erasure, anonymisation, retention, and the
CSV list exports. docs/LAUNCH_READINESS_PLAN.md, P1 work package 4."""

import io
import tempfile
import zipfile
from datetime import date, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import gdpr
from core.models import AuditEvent, Document, Horse, Owner
from core.roles_testutils import make_admin, make_user_with_access
from invoicing.models import Invoice
from xero_integration.models import XeroContactMapping


def _owner(**extra):
    data = dict(name="Siobhán O'Brien", email='siobhan@example.com', phone='07700 900123',
                address='1 Lane\nTown', notes='Prefers calls', account_code='OBR01')
    data.update(extra)
    return Owner.objects.create(**data)


def _invoice(owner, period_end, number):
    return Invoice.objects.create(
        owner=owner, invoice_number=number,
        period_start=period_end.replace(day=1), period_end=period_end, total=Decimal('100'),
    )


def _archive(owner, when=None):
    owner.is_archived = True
    owner.archived_at = when or timezone.now()
    owner.save()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ExportTests(TestCase):
    def setUp(self):
        self.admin = make_admin('gdpradmin')
        self.client.force_login(self.admin)
        self.owner = _owner()
        _invoice(self.owner, date(2026, 9, 30), 'G-1')
        self.doc = Document.objects.create(
            owner=self.owner, doc_type='insurance', title='Insurance',
            file=SimpleUploadedFile('ins.pdf', b'%PDF-1.4 ins', content_type='application/pdf'),
        )

    def _zip(self):
        response = self.client.get(reverse('owner_data_export', args=[self.owner.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        return zipfile.ZipFile(io.BytesIO(response.content))

    def test_zip_holds_every_section_and_the_files(self):
        names = set(self._zip().namelist())
        for expected in ('README.txt', 'owner.csv', 'owner_history.csv', 'invoices.csv',
                         'payments.csv', 'charges.csv', 'stays.csv', 'documents.csv'):
            self.assertIn(expected, names)
        # Storage may add a suffix to the file name, so match the folder and type.
        self.assertTrue(any(n.startswith('documents/') and n.endswith('.pdf') for n in names))

    def test_csv_opens_in_excel_with_accents(self):
        data = self._zip().read('owner.csv')
        self.assertTrue(data.startswith('﻿'.encode('utf-8')))
        self.assertIn("Siobhán O'Brien", data.decode('utf-8-sig'))

    def test_formula_in_a_name_stays_text(self):
        self.owner.name = '=HYPERLINK("http://x","y")'
        self.owner.save()
        self.assertIn("'=HYPERLINK", self._zip().read('owner.csv').decode('utf-8-sig'))

    def test_export_is_audited_without_the_name(self):
        self._zip()
        event = AuditEvent.objects.get(action=AuditEvent.Action.OWNER_EXPORTED)
        self.assertEqual(event.actor, self.admin)
        self.assertIn(f'Owner #{self.owner.pk}', event.detail)
        self.assertNotIn('Brien', event.detail)

    def test_needs_full_owner_access(self):
        self.client.force_login(make_user_with_access('ownview', owners='view'))
        response = self.client.get(reverse('owner_data_export', args=[self.owner.pk]))
        self.assertNotEqual(response.status_code, 200)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class EraseTests(TestCase):
    def setUp(self):
        self.admin = make_admin('eraser')
        self.client.force_login(self.admin)
        self.owner = _owner()
        self.owner.phone = '07700 900999'
        self.owner.save()  # A second history row with personal data.

    def _erase(self, **data):
        return self.client.post(reverse('owner_erase', args=[self.owner.pk]), data)

    def test_refused_while_not_archived(self):
        self._erase()
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.email, 'siobhan@example.com')

    def test_contact_details_go_name_and_address_stay_with_invoices(self):
        _invoice(self.owner, date(2026, 9, 30), 'E-1')
        _archive(self.owner)
        doc = Document.objects.create(
            owner=self.owner, doc_type='insurance', title='Ins',
            file=SimpleUploadedFile('i.pdf', b'%PDF-1.4', content_type='application/pdf'),
        )
        storage, file_name = doc.file.storage, doc.file.name
        self._erase()
        self.owner.refresh_from_db()
        self.assertEqual((self.owner.email, self.owner.phone, self.owner.notes, self.owner.account_code), ('', '', '', ''))
        self.assertEqual(self.owner.name, "Siobhán O'Brien")
        self.assertEqual(self.owner.address, '1 Lane\nTown')
        # Earlier copies too.
        self.assertFalse(self.owner.history.exclude(email='').exists())
        self.assertFalse(self.owner.history.exclude(phone='').exists())
        self.assertFalse(Document.all_objects.filter(owner=self.owner).exists())
        self.assertFalse(storage.exists(file_name))
        event = AuditEvent.objects.get(action=AuditEvent.Action.OWNER_ERASED)
        self.assertNotIn('Brien', event.detail)

    def test_owner_without_invoices_can_be_fully_anonymised(self):
        _archive(self.owner)
        XeroContactMapping.objects.create(owner=self.owner, xero_contact_id='x-1', xero_contact_name="Siobhán O'Brien")
        self._erase(scope='all')
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.name, f'Former owner #{self.owner.pk}')
        self.assertEqual(self.owner.address, '')
        self.assertFalse(self.owner.history.exclude(name=self.owner.name).exists())
        self.assertFalse(XeroContactMapping.objects.filter(owner=self.owner).exists())

    def test_full_anonymise_refused_while_invoices_must_be_kept(self):
        _invoice(self.owner, date(2026, 9, 30), 'E-2')
        _archive(self.owner)
        self._erase(scope='all')
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.name, "Siobhán O'Brien")

    def test_owner_page_offers_it(self):
        _archive(self.owner)
        response = self.client.get(reverse('owner_detail', args=[self.owner.pk]))
        self.assertContains(response, 'Personal Data')
        self.assertContains(response, reverse('owner_erase', args=[self.owner.pk]))


@override_settings(OWNER_RETENTION_YEARS=6, AUDIT_EVENT_RETENTION_DAYS=730)
class RetentionTests(TestCase):
    def test_owners_past_retention_are_anonymised(self):
        today = timezone.localdate()
        old = _owner(name='Old Owner')
        _invoice(old, today.replace(year=today.year - 7, day=1), 'R-1')
        _archive(old)
        recent = _owner(name='Recent Owner')
        _invoice(recent, today.replace(year=today.year - 2, day=1), 'R-2')
        _archive(recent)
        active = _owner(name='Active Owner')
        _invoice(active, today.replace(year=today.year - 9, day=1), 'R-3')
        no_invoices = _owner(name='Never Billed')
        _archive(no_invoices, timezone.now() - timedelta(days=7 * 366))

        result = gdpr.apply_retention()
        self.assertEqual(result['owners_anonymised'], 2)
        for owner, anonymised in ((old, True), (recent, False), (active, False), (no_invoices, True)):
            owner.refresh_from_db()
            self.assertEqual(owner.name.startswith('Former owner #'), anonymised, owner.pk)
        # Running again changes nothing.
        self.assertEqual(gdpr.apply_retention()['owners_anonymised'], 0)

    def test_old_account_events_are_dropped(self):
        AuditEvent.objects.create(action=AuditEvent.Action.SIGNED_IN, created_at=timezone.now() - timedelta(days=800))
        AuditEvent.objects.create(action=AuditEvent.Action.SIGNED_IN, created_at=timezone.now() - timedelta(days=10))
        result = gdpr.apply_retention()
        self.assertEqual(result['audit_events_deleted'], 1)
        self.assertEqual(AuditEvent.objects.filter(action=AuditEvent.Action.SIGNED_IN).count(), 1)


class ListExportTests(TestCase):
    def test_owners_csv(self):
        _owner()
        self.client.force_login(make_user_with_access('csvview', owners='view'))
        response = self.client.get(reverse('owners_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith('﻿'.encode('utf-8')))
        self.assertIn("Siobhán O'Brien", response.content.decode('utf-8-sig'))
        self.assertIn('attachment; filename="owners-', response['Content-Disposition'])

    def test_horses_and_charges_csv(self):
        from billing.models import ExtraCharge
        owner = _owner()
        horse = Horse.objects.create(name='Zoë')
        ExtraCharge.objects.create(horse=horse, owner=owner, charge_type='vet', date=date(2026, 5, 1),
                                   description='Callout', amount=Decimal('80'))
        self.client.force_login(make_admin('csvadmin'))
        self.assertIn('Zoë', self.client.get(reverse('horses_csv')).content.decode('utf-8-sig'))
        self.assertIn('Callout', self.client.get(reverse('charges_csv')).content.decode('utf-8-sig'))

    def test_needs_access(self):
        self.client.force_login(make_user_with_access('noowners', dashboard='full'))
        self.assertNotEqual(self.client.get(reverse('owners_csv')).status_code, 200)
