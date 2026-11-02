"""Soft delete, restore, Undo and the nightly purge.

docs/LAUNCH_READINESS_PLAN.md, P1 work package 3. Payment voiding is
covered in invoicing/test_payments.py.
"""

import tempfile
from datetime import date, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge, YardCost
from core.models import Document, Horse, Owner
from core.roles_testutils import make_admin, make_user_with_access
from core.tasks import purge_soft_deleted
from core.views._undo import undo_tag, undo_url_from_tags
from invoicing.models import Invoice, Payment
from invoicing.services import InvoiceService


def _charge(horse, owner, **extra):
    data = dict(horse=horse, owner=owner, charge_type='vet', date=date(2026, 5, 20),
                description='Callout', amount=Decimal('80.00'), split_by_ownership=False)
    data.update(extra)
    return ExtraCharge.objects.create(**data)


class ChargeSoftDeleteTests(TestCase):
    def setUp(self):
        self.admin = make_admin('sdadmin', email='sdadmin@example.com')
        self.client.force_login(self.admin)
        self.owner = Owner.objects.create(name='Alice')
        self.horse = Horse.objects.create(name='Duo')
        self.charge = _charge(self.horse, self.owner)

    def _delete(self):
        return self.client.post(reverse('charge_delete', args=[self.charge.pk]), follow=True)

    def test_delete_hides_the_charge_and_offers_undo(self):
        response = self._delete()
        self.assertFalse(ExtraCharge.objects.filter(pk=self.charge.pk).exists())
        deleted = ExtraCharge.all_objects.get(pk=self.charge.pk)
        self.assertEqual(deleted.deleted_by, self.admin)
        self.assertContains(response, 'Charge deleted.')
        self.assertContains(response, reverse('charge_restore', args=[self.charge.pk]))
        self.assertContains(response, '>Undo<')

    def test_deleted_charge_is_not_invoiced(self):
        self._delete()
        self.assertNotIn(self.owner, set(InvoiceService.get_owners_for_billing(date(2026, 5, 1), date(2026, 5, 31))))
        self.assertEqual(list(InvoiceService.get_unbilled_charges(self.owner, date(2026, 5, 31))), [])

    def test_undo_restores_it(self):
        self._delete()
        self.client.post(reverse('charge_restore', args=[self.charge.pk]))
        self.assertTrue(ExtraCharge.objects.filter(pk=self.charge.pk).exists())
        self.assertIsNone(ExtraCharge.objects.get(pk=self.charge.pk).deleted_by)

    def test_restore_needs_full_access(self):
        self._delete()
        viewer = make_user_with_access('viewer', charges='view')
        self.client.force_login(viewer)
        self.client.post(reverse('charge_restore', args=[self.charge.pk]))
        self.assertFalse(ExtraCharge.objects.filter(pk=self.charge.pk).exists())

    def test_a_live_charge_cannot_be_restored(self):
        response = self.client.post(reverse('charge_restore', args=[self.charge.pk]))
        self.assertEqual(response.status_code, 404)


class YardCostAndDocumentTests(TestCase):
    def setUp(self):
        self.admin = make_admin('sdadmin2')
        self.client.force_login(self.admin)

    def test_yard_cost_delete_and_restore(self):
        cost = YardCost.objects.create(category='feed', date=date(2026, 5, 1), description='Hay', amount=Decimal('300'))
        self.client.post(reverse('yard_cost_delete', args=[cost.pk]))
        self.assertFalse(YardCost.objects.filter(pk=cost.pk).exists())
        self.client.post(reverse('yard_cost_restore', args=[cost.pk]))
        self.assertTrue(YardCost.objects.filter(pk=cost.pk).exists())

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_document_delete_keeps_the_file_until_purged(self):
        horse = Horse.objects.create(name='Doc Horse')
        doc = Document.objects.create(
            horse=horse, doc_type='passport', title='Ghost scan 4417',
            file=SimpleUploadedFile('p.pdf', b'%PDF-1.4 test', content_type='application/pdf'),
        )
        # follow=True shows (and so clears) the "deleted" message, which
        # names the document.
        self.client.post(reverse('document_delete', args=[doc.pk]), follow=True)
        deleted = Document.all_objects.get(pk=doc.pk)
        self.assertTrue(deleted.is_deleted)
        self.assertTrue(deleted.file.storage.exists(deleted.file.name))
        page = self.client.get(reverse('horse_detail', args=[horse.pk]))
        self.assertNotContains(page, 'Ghost scan 4417')
        self.client.post(reverse('document_restore', args=[doc.pk]))
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())


class RecentlyDeletedPageTests(TestCase):
    def test_lists_deleted_rows_with_restore(self):
        admin = make_admin('rdadmin')
        owner = Owner.objects.create(name='Bob')
        horse = Horse.objects.create(name='Pip')
        charge = _charge(horse, owner, description='Disputed vet fee')
        charge.soft_delete(admin)
        self.client.force_login(admin)
        response = self.client.get(reverse('recently_deleted'))
        self.assertContains(response, 'Disputed vet fee')
        self.assertContains(response, reverse('charge_restore', args=[charge.pk]))
        self.assertContains(response, 'removed for good on')

    def test_sections_follow_access(self):
        user = make_user_with_access('costsonly', costs='full')
        self.client.force_login(user)
        response = self.client.get(reverse('recently_deleted'))
        self.assertContains(response, 'Yard costs')
        self.assertNotContains(response, 'Charges')
        self.assertNotContains(response, 'Documents')


@override_settings(SOFT_DELETE_RETENTION_DAYS=30, MEDIA_ROOT=tempfile.mkdtemp())
class PurgeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name='Cat')
        self.horse = Horse.objects.create(name='Purge')

    def _age(self, obj, days):
        type(obj).all_objects.filter(pk=obj.pk).update(deleted_at=timezone.now() - timedelta(days=days))

    def test_old_rows_go_recent_rows_stay(self):
        old = _charge(self.horse, self.owner)
        recent = _charge(self.horse, self.owner)
        old.soft_delete()
        recent.soft_delete()
        self._age(old, 31)
        self._age(recent, 5)
        counts = purge_soft_deleted()
        self.assertEqual(counts['ExtraCharge'], 1)
        self.assertFalse(ExtraCharge.all_objects.filter(pk=old.pk).exists())
        self.assertTrue(ExtraCharge.all_objects.filter(pk=recent.pk).exists())

    def test_live_rows_are_never_purged(self):
        live = _charge(self.horse, self.owner)
        purge_soft_deleted()
        self.assertTrue(ExtraCharge.objects.filter(pk=live.pk).exists())

    def test_purged_document_loses_its_file(self):
        doc = Document.objects.create(
            horse=self.horse, doc_type='passport', title='Old',
            file=SimpleUploadedFile('old.pdf', b'%PDF-1.4 old', content_type='application/pdf'),
        )
        storage, name = doc.file.storage, doc.file.name
        doc.soft_delete()
        self._age(doc, 40)
        purge_soft_deleted()
        self.assertFalse(Document.all_objects.filter(pk=doc.pk).exists())
        self.assertFalse(storage.exists(name))

    def test_voided_payments_are_kept(self):
        invoice = Invoice.objects.create(
            owner=self.owner, invoice_number='PRG-1',
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
            total=Decimal('50'),
        )
        payment = Payment.objects.create(invoice=invoice, date=date(2026, 2, 1), amount=Decimal('50'))
        payment.void(None, 'Bounced')
        Payment.all_objects.filter(pk=payment.pk).update(voided_at=timezone.now() - timedelta(days=400))
        purge_soft_deleted()
        self.assertTrue(Payment.all_objects.filter(pk=payment.pk).exists())


class UndoTagTests(TestCase):
    def test_round_trip(self):
        self.assertEqual(undo_url_from_tags(undo_tag('/billing/charges/3/restore/')), '/billing/charges/3/restore/')

    def test_only_local_paths(self):
        self.assertEqual(undo_url_from_tags('undo://evil.example/x'), '')
        self.assertEqual(undo_url_from_tags('undo:https://evil.example/x'), '')
        self.assertEqual(undo_url_from_tags(''), '')
