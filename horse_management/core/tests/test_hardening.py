"""Upload content checks, download headers, Permissions-Policy,
security.txt, backup encryption, the database TLS setting in backups and
Xero error trimming. docs/LAUNCH_READINESS_PLAN.md, P1 work package 8."""

import io
import json
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.backup import crypto, runner
from core.models import Document, Horse
from core.roles_testutils import make_admin
from core.storage import MediaStorage
from core.uploads import content_disposition, validate_file_content
from xero_integration.client import error_summary

PDF = b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n'
JPEG = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00' + b'\x00' * 20
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20
WEBP = b'RIFF\x24\x00\x00\x00WEBPVP8 ' + b'\x00' * 20
HEIC = b'\x00\x00\x00\x18ftypheic\x00\x00\x00\x00' + b'\x00' * 20
DOC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 20
HTML = b'<!doctype html><script>alert(document.cookie)</script>'


def _docx(word=True):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        if word:
            archive.writestr('word/document.xml', '<w:document/>')
    return buffer.getvalue()


def _upload(name, content):
    return SimpleUploadedFile(name, content)


class UploadContentTests(SimpleTestCase):
    def test_real_files_pass(self):
        for name, content in [
            ('passport.pdf', PDF), ('photo.jpg', JPEG), ('photo.JPEG', JPEG),
            ('scan.png', PNG), ('pic.webp', WEBP), ('iphone.heic', HEIC),
            ('old.doc', DOC), ('letter.docx', _docx()),
        ]:
            with self.subTest(name=name):
                validate_file_content(_upload(name, content))

    def test_pdf_header_after_a_few_bytes_passes(self):
        validate_file_content(_upload('odd.pdf', b'\r\n\r\n' + PDF))

    def test_renamed_files_are_refused(self):
        for name, content in [
            ('passport.pdf', HTML), ('photo.jpg', PNG), ('scan.png', JPEG),
            ('letter.docx', _docx(word=False)), ('letter.docx', b'PK\x03\x04broken'),
            ('old.doc', PDF), ('iphone.heic', JPEG),
        ]:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                validate_file_content(_upload(name, content))

    def test_file_is_rewound_after_the_check(self):
        upload = _upload('passport.pdf', PDF)
        validate_file_content(upload)
        self.assertEqual(upload.read(), PDF)

    def test_stored_files_are_not_read_again(self):
        stored = mock.Mock(_committed=True)
        validate_file_content(stored)
        stored.read.assert_not_called()

    def test_inline_or_download(self):
        self.assertEqual(content_disposition('documents/2026/01/a.pdf'), 'inline')
        self.assertEqual(content_disposition('horse_photos/x.JPG'), 'inline')
        self.assertEqual(content_disposition('documents/letter.docx'), 'attachment')
        self.assertEqual(content_disposition('documents/iphone.heic'), 'attachment')
        self.assertEqual(content_disposition('documents/no_extension'), 'attachment')

    def test_bucket_objects_carry_the_disposition(self):
        storage = MediaStorage(bucket_name='b', access_key='a', secret_key='s')
        self.assertEqual(storage.get_object_parameters('documents/a.docx')['ContentDisposition'], 'attachment')
        self.assertEqual(storage.get_object_parameters('documents/a.pdf')['ContentDisposition'], 'inline')


class DocumentUploadTests(TestCase):
    def setUp(self):
        self.horse = Horse.objects.create(name='Upload Horse')
        self.client.force_login(make_admin('uploadadmin'))

    def _post(self, upload):
        return self.client.post(
            reverse('document_create') + f'?horse={self.horse.pk}',
            {'doc_type': Document.DocType.OTHER, 'title': 'Test', 'file': upload},
        )

    def test_an_html_file_named_pdf_is_refused(self):
        response = self._post(_upload('passport.pdf', HTML))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'not a real .pdf file')
        self.assertFalse(Document.objects.exists())

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_a_real_pdf_is_accepted(self):
        self._post(_upload('passport.pdf', PDF))
        self.assertTrue(Document.objects.exists())


class HeaderTests(TestCase):
    def test_permissions_policy_on_every_response(self):
        policy = self.client.get('/_health/')['Permissions-Policy']
        self.assertIn('geolocation=(self)', policy)
        self.assertIn('camera=(self)', policy)
        self.assertIn('microphone=()', policy)
        self.assertIn('payment=()', policy)


class SecurityTxtTests(TestCase):
    @override_settings(SECURITY_CONTACT='')
    def test_missing_until_a_contact_is_set(self):
        self.assertEqual(self.client.get('/.well-known/security.txt').status_code, 404)

    @override_settings(SECURITY_CONTACT='security@yard.example', SITE_URL='https://yard.example')
    def test_names_the_contact(self):
        response = self.client.get('/.well-known/security.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain; charset=utf-8')
        body = response.content.decode()
        self.assertIn('Contact: mailto:security@yard.example', body)
        self.assertRegex(body, r'Expires: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')
        self.assertIn('Canonical: https://yard.example/.well-known/security.txt', body)


KEY = crypto.new_key()
OLD_KEY = crypto.new_key()


class BackupEncryptionTests(SimpleTestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def _file(self, content, name='yardway-db-2026-01-01T0200.dump'):
        path = self.dir / name
        path.write_bytes(content)
        return path

    def _round_trip(self, content):
        source = self._file(content)
        sealed = crypto.encrypt_file(source, crypto.encryption_key())
        self.assertNotIn(b'owner@example.com', sealed.read_bytes())
        source.unlink()
        return crypto.decrypt_file(sealed).read_bytes()

    @override_settings(BACKUP_ENCRYPTION_KEY=KEY)
    def test_round_trip(self):
        for content in (b'', b'owner@example.com ' * 10, b'owner@example.com' + bytes(range(256)) * 50):
            with self.subTest(size=len(content)), mock.patch.object(crypto, 'CHUNK_SIZE', 1000):
                self.assertEqual(self._round_trip(content), content)

    @override_settings(BACKUP_ENCRYPTION_KEY=KEY)
    def test_changed_cut_or_extended_files_fail(self):
        with mock.patch.object(crypto, 'CHUNK_SIZE', 100):
            sealed = crypto.encrypt_file(self._file(b'x' * 1000), crypto.encryption_key())
        data = sealed.read_bytes()
        chunk_record = 4 + 12 + 100 + 16
        header = len(crypto.MAGIC) + 8
        for label, broken in [
            ('flipped byte', data[:-5] + bytes([data[-5] ^ 1]) + data[-4:]),
            ('cut after a chunk', data[:header + chunk_record * 3]),
            ('cut mid-chunk', data[:-7]),
            ('bytes added', data + data[header:header + chunk_record]),
        ]:
            with self.subTest(label):
                sealed.write_bytes(broken)
                with self.assertRaises(crypto.BackupKeyError):
                    crypto.decrypt_file(sealed)

    def test_old_key_still_restores(self):
        with override_settings(BACKUP_ENCRYPTION_KEY=OLD_KEY):
            sealed = crypto.encrypt_file(self._file(b'old backup'), crypto.encryption_key())
        with override_settings(BACKUP_ENCRYPTION_KEY=KEY, BACKUP_DECRYPTION_KEYS=OLD_KEY):
            self.assertEqual(crypto.decrypt_file(sealed).read_bytes(), b'old backup')
        with override_settings(BACKUP_ENCRYPTION_KEY=KEY, BACKUP_DECRYPTION_KEYS=''):
            with self.assertRaises(crypto.BackupKeyError):
                crypto.decrypt_file(sealed)

    @override_settings(BACKUP_ENCRYPTION_KEY='too-short')
    def test_bad_key_is_refused(self):
        with self.assertRaises(crypto.BackupKeyError):
            crypto.encryption_key()

    @override_settings(BACKUP_ENCRYPTION_KEY='')
    def test_no_key_uploads_as_before(self):
        path = self._file(b'data')
        self.assertEqual(crypto.maybe_encrypt(path, 'backups/database/x.dump'), (path, 'backups/database/x.dump'))
        self.assertEqual(crypto.maybe_decrypt(path), path)

    @override_settings(BACKUP_ENCRYPTION_KEY=KEY, BACKUP_S3_BUCKET='b', BACKUP_S3_ACCESS_KEY='a',
                       BACKUP_S3_SECRET_KEY='s')
    def test_backup_run_uploads_encrypted_files(self):
        uploaded = {}

        def fake_upload(path, key):
            uploaded[key] = Path(path).read_bytes()
            return len(uploaded[key])

        with mock.patch.object(runner, 'dump_database', side_effect=lambda d, now=None: self._file(b'owner@example.com', 'yardway-db-2026-01-01T0200.dump')), \
             mock.patch.object(runner, 'archive_media', return_value=None), \
             mock.patch.object(runner.storage, 'upload', side_effect=fake_upload), \
             mock.patch.object(runner, 'prune', return_value=0):
            summary = runner.run()
        self.assertEqual(summary['keys'], ['backups/database/yardway-db-2026-01-01T0200.dump.enc'])
        sealed = next(iter(uploaded.values()))
        self.assertTrue(sealed.startswith(crypto.MAGIC))
        self.assertNotIn(b'owner@example.com', sealed)


class BackupConnectionTests(SimpleTestCase):
    def test_pg_dump_uses_the_same_sslmode(self):
        config = {'USER': 'u', 'PASSWORD': 'p', 'HOST': 'db.example', 'PORT': 5432, 'NAME': 'x',
                  'OPTIONS': {'sslmode': 'require'}}
        with mock.patch.object(runner, 'settings', SimpleNamespace(DATABASES={'default': config})):
            self.assertEqual(runner._dsn_from_settings(), 'postgresql://u:p@db.example:5432/x?sslmode=require')


class XeroErrorSummaryTests(SimpleTestCase):
    def _response(self, body):
        response = mock.Mock()
        if isinstance(body, (dict, list)):
            response.json.return_value = body
        else:
            response.json.side_effect = ValueError
        return response

    def test_keeps_only_the_reason(self):
        body = {
            'Type': 'ValidationException', 'Message': 'A validation exception occurred',
            'Elements': [{
                'Contact': {'Name': 'Siobhan', 'EmailAddress': 'owner@example.com', 'Addresses': ['1 Lane']},
                'ValidationErrors': [{'Message': 'Contact name already exists'}],
            }],
        }
        summary = error_summary(self._response(body))
        self.assertEqual(summary, 'A validation exception occurred; Contact name already exists')
        self.assertNotIn('owner@example.com', summary)

    def test_oauth_error_code(self):
        self.assertEqual(error_summary(self._response({'error': 'invalid_grant'})), 'invalid_grant')

    def test_not_json(self):
        self.assertEqual(error_summary(self._response('<html>owner@example.com</html>')), 'no detail')

    def test_length_is_capped(self):
        self.assertEqual(len(error_summary(self._response({'Message': 'x' * 5000}))), 300)

    def test_api_error_message_has_no_body(self):
        from xero_integration.client import XeroAPIError, XeroClient

        response = mock.Mock(status_code=400, headers={}, text=json.dumps({'Contact': 'owner@example.com'}))
        response.json.return_value = {'Message': 'Bad request', 'Contact': 'owner@example.com'}
        client = XeroClient.__new__(XeroClient)
        client.connection = mock.Mock(access_token='t', xero_tenant_id='tenant')
        with mock.patch.object(XeroClient, '_get_valid_token', return_value='t', create=True), \
             mock.patch.object(XeroClient, '_ensure_valid_token', return_value='t', create=True), \
             mock.patch('xero_integration.client.requests.request', return_value=response):
            with self.assertRaises(XeroAPIError) as caught:
                client._api_request('GET', 'Contacts')
        self.assertIn('Bad request', str(caught.exception))
        self.assertNotIn('owner@example.com', str(caught.exception))
        self.assertNotIn('owner@example.com', caught.exception.response_body)
