"""Document links make a fresh signed link at the click (pre-launch
review): the page held a link that expired after 15 minutes."""

import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Document, Horse
from core.roles_testutils import make_admin, make_user_with_access


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DocumentOpenTests(TestCase):
    def setUp(self):
        self.horse = Horse.objects.create(name='Ghost')
        self.doc = Document.objects.create(
            horse=self.horse, doc_type='passport', title='Passport',
            file=SimpleUploadedFile('p.pdf', b'%PDF-1.4 p', content_type='application/pdf'),
        )

    def test_it_sends_the_browser_to_the_file(self):
        self.client.force_login(make_admin('docopen'))
        response = self.client.get(reverse('document_open', args=[self.doc.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.doc.file.url)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_it_needs_access_to_horses(self):
        self.client.force_login(make_user_with_access('nohorses', dashboard='full'))
        response = self.client.get(reverse('document_open', args=[self.doc.pk]))
        self.assertNotEqual(response.get('Location'), self.doc.file.url)

    def test_the_pages_link_to_it(self):
        self.client.force_login(make_admin('docpage'))
        page = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertContains(page, reverse('document_open', args=[self.doc.pk]))
        self.assertNotContains(page, self.doc.file.url + '"')
