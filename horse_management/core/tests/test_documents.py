"""Tests for document storage (passports, insurance) and expiry reminders."""

from datetime import timedelta

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import BusinessSettings, Document, Horse, Owner
from core.roles_testutils import make_admin, make_viewer
from notifications.tasks import send_document_expiry_reminders

PDF_BYTES = b"%PDF-1.4 test"


def _pdf(name="passport.pdf"):
    return SimpleUploadedFile(name, PDF_BYTES, content_type="application/pdf")


class DocumentModelTests(TestCase):

    def setUp(self):
        self.horse = Horse.objects.create(name="Ghost")

    def test_requires_horse_or_owner(self):
        doc = Document(doc_type="passport", title="Orphan", file=_pdf())
        with self.assertRaises(ValidationError):
            doc.full_clean()

    def test_changed_expiry_rearms_reminder(self):
        doc = Document.objects.create(
            horse=self.horse, doc_type="insurance", title="Policy",
            file=_pdf(), expiry_date=timezone.now().date() + timedelta(days=10),
            expiry_reminder_sent=True,
        )
        doc.expiry_date = timezone.now().date() + timedelta(days=400)
        doc.save()
        doc.refresh_from_db()
        self.assertFalse(doc.expiry_reminder_sent)

    def test_unchanged_save_keeps_flag(self):
        doc = Document.objects.create(
            horse=self.horse, doc_type="insurance", title="Policy",
            file=_pdf(), expiry_date=timezone.now().date() + timedelta(days=10),
            expiry_reminder_sent=True,
        )
        doc.notes = "renewal requested"
        doc.save()
        doc.refresh_from_db()
        self.assertTrue(doc.expiry_reminder_sent)

    def test_is_expired(self):
        doc = Document.objects.create(
            horse=self.horse, doc_type="insurance", title="Old policy",
            file=_pdf(), expiry_date=timezone.now().date() - timedelta(days=1),
        )
        self.assertTrue(doc.is_expired)


class DocumentViewTests(TestCase):

    def setUp(self):
        self.staff = make_admin()
        self.viewer = make_viewer()
        self.horse = Horse.objects.create(name="Ghost")
        self.owner = Owner.objects.create(name="Alice", email="a@example.com")

    def test_upload_against_horse(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("document_create") + f"?horse={self.horse.pk}",
            {
                "horse": self.horse.pk,
                "doc_type": "passport",
                "title": "Passport — Weatherbys",
                "file": _pdf(),
            },
        )
        self.assertRedirects(response, reverse("horse_detail", args=[self.horse.pk]))
        doc = Document.objects.get()
        self.assertEqual(doc.horse, self.horse)
        self.assertEqual(doc.uploaded_by, self.staff)

    def test_upload_against_owner(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("document_create") + f"?owner={self.owner.pk}",
            {
                "owner": self.owner.pk,
                "doc_type": "other",
                "title": "Livery agreement",
                "file": _pdf("agreement.pdf"),
            },
        )
        self.assertRedirects(response, reverse("owner_detail", args=[self.owner.pk]))
        self.assertEqual(Document.objects.get().owner, self.owner)

    def test_upload_requires_target(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("document_create"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Document.objects.count(), 0)

    def test_viewer_cannot_upload_or_delete(self):
        # Documents need 'horses' full; the Viewer role only has view.
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse("document_create") + f"?horse={self.horse.pk}"
        )
        # Plain GET denial redirects to the dashboard with a message.
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(Document.objects.count(), 0)
        doc = Document.objects.create(
            horse=self.horse, doc_type="passport", title="P", file=_pdf()
        )
        # POST denial is a hard 403.
        response = self.client.post(reverse("document_delete", args=[doc.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())

    def test_delete(self):
        self.client.force_login(self.staff)
        doc = Document.objects.create(
            horse=self.horse, doc_type="passport", title="P", file=_pdf()
        )
        response = self.client.post(reverse("document_delete", args=[doc.pk]))
        self.assertRedirects(response, reverse("horse_detail", args=[self.horse.pk]))
        self.assertEqual(Document.objects.count(), 0)

    def test_card_renders_on_detail_pages(self):
        Document.objects.create(
            horse=self.horse, doc_type="passport", title="Ghost passport",
            file=_pdf(),
        )
        self.client.force_login(self.staff)
        response = self.client.get(reverse("horse_detail", args=[self.horse.pk]))
        self.assertContains(response, "Ghost passport")
        response = self.client.get(reverse("owner_detail", args=[self.owner.pk]))
        self.assertContains(response, "Documents")


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DocumentExpiryReminderTests(TestCase):

    def setUp(self):
        settings_obj = BusinessSettings.get_settings()
        settings_obj.email = "office@yard.example"
        settings_obj.save()
        self.horse = Horse.objects.create(name="Ghost")
        today = timezone.now().date()
        self.soon = Document.objects.create(
            horse=self.horse, doc_type="insurance", title="Expiring policy",
            file=_pdf("a.pdf"), expiry_date=today + timedelta(days=10),
        )
        self.expired = Document.objects.create(
            horse=self.horse, doc_type="passport", title="Lapsed passport",
            file=_pdf("b.pdf"), expiry_date=today - timedelta(days=5),
        )
        self.far = Document.objects.create(
            horse=self.horse, doc_type="insurance", title="Fresh policy",
            file=_pdf("c.pdf"), expiry_date=today + timedelta(days=200),
        )
        self.no_expiry = Document.objects.create(
            horse=self.horse, doc_type="other", title="No expiry",
            file=_pdf("d.pdf"),
        )

    def test_selects_expiring_and_expired_only(self):
        result = send_document_expiry_reminders()
        self.assertIn("2 document", result)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("Expiring policy", body)
        self.assertIn("Lapsed passport", body)
        self.assertNotIn("Fresh policy", body)
        self.assertIn("office@yard.example", mail.outbox[0].to)

    def test_one_reminder_per_document(self):
        send_document_expiry_reminders()
        mail.outbox.clear()
        result = send_document_expiry_reminders()
        self.assertIn("0 document", result)
        self.assertEqual(len(mail.outbox), 0)

    def test_skips_without_business_email(self):
        settings_obj = BusinessSettings.get_settings()
        settings_obj.email = ""
        settings_obj.save()
        result = send_document_expiry_reminders()
        self.assertEqual(result, "no_business_email")
        self.soon.refresh_from_db()
        # Nothing consumed — reminders still pending for when email is set.
        self.assertFalse(self.soon.expiry_reminder_sent)
