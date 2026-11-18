"""C-vet's page and API (docs/C_VET_PLAN.md, sections 4 and 5).

The gate, the yard download, and the note upload with its sync rules:
stored once, nothing a vet saved is refused for a reason the vet could
not see at the yard, and files are served to their practice only.
The ownership rules are in tests_ownership.py.
"""

import hashlib
import io
import shutil
import tempfile
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from core.models import BusinessSettings, Horse, Location, Owner, Placement, RateType
from core.roles_testutils import assign_role, make_admin, make_user_with_access

from . import access
from .admin import delete_test_notes
from .models import ClinicalFile, ClinicalNote, PracticeMember, PracticeYardLink, SyncReceipt, Visit
from .testutils import make_practice, make_vet, vet_role

User = get_user_model()

APP = '/c-vet/'
WORKER = '/c-vet/sw.js'
SESSION = '/c-vet/api/session/'
YARD = '/c-vet/api/yard/'
NOTES = '/c-vet/api/notes/'


def png_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buffer, 'PNG')
    return buffer.getvalue()


def note_data(**extra):
    data = {
        'client_id': str(uuid.uuid4()),
        'horse_name': 'Bramble',
        'note': 'Mild heat left fore.',
        'device_created_at': timezone.now().isoformat(),
        'attempts': '1',
        'client_info': '{"standalone": true, "openMs": 240}',
    }
    data.update(extra)
    return data


def photo_upload(data=None):
    return SimpleUploadedFile('photo', data or png_bytes(), 'image/png')


@override_settings(CVET_ENABLED=False)
class TurnedOffTests(TestCase):
    """With the setting off, which is the default, nothing is there."""

    def test_every_address_is_a_404(self):
        self.client.force_login(make_vet())
        for url in (APP, WORKER, SESSION, YARD, NOTES, f'/c-vet/api/files/{uuid.uuid4()}/'):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.post(NOTES, note_data()).status_code, 404)
        self.assertFalse(ClinicalNote.objects.exists())


@override_settings(CVET_ENABLED=True)
class WideScreenFilesTests(TestCase):
    """The files of the wide screen (plan, section 7h) are not part of the phone's download."""

    def test_the_worker_stores_them_only_when_a_page_asks(self):
        script = self.client.get(WORKER).content.decode()
        assets = script.split('const ASSETS = ')[1].split(';')[0]
        wide = script.split('const WIDE = ')[1].split(';')[0]
        self.assertNotIn('desk.', assets)
        self.assertIn('/static/cvet/desk.css', wide)
        self.assertIn('/static/cvet/desk.js', wide)
        # And what needs a signal: fetched on first use.
        self.assertNotIn('online.js', assets)
        self.assertIn('/static/cvet/online.js', wide)

    def test_the_page_names_them_and_does_not_load_them(self):
        self.client.force_login(make_vet())
        page = self.client.get(APP).content.decode()
        self.assertIn('data-desk-js="/static/cvet/desk.js', page)
        self.assertIn('data-desk-css="/static/cvet/desk.css', page)
        self.assertNotIn('src="/static/cvet/desk.js', page)
        self.assertNotIn('href="/static/cvet/desk.css', page)


@override_settings(CVET_ENABLED=True)
class GateTests(TestCase):
    def test_the_page_sends_a_signed_out_person_to_sign_in(self):
        response = self.client.get(APP)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_the_api_answers_a_signed_out_phone_in_json(self):
        # A redirect to the sign-in page would look like a failure to the
        # phone. 401 tells it to keep its notes and ask for a sign-in.
        for url in (SESSION, YARD, NOTES):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 401, url)
            self.assertEqual(response.json(), {'error': 'signed_out'})
        self.assertEqual(self.client.post(NOTES, note_data()).status_code, 401)

    def test_yard_staff_cannot_open_it(self):
        # Full access to the yard's health pages is not access to C-vet.
        self.client.force_login(make_user_with_access('staff', health='full', dashboard='full'))
        self.assertEqual(self.client.get(APP).status_code, 302)
        self.assertEqual(self.client.get(YARD).status_code, 403)
        self.assertEqual(self.client.get(NOTES).status_code, 403)
        self.assertEqual(self.client.post(NOTES, note_data()).status_code, 403)
        self.assertFalse(ClinicalNote.objects.exists())

    def test_the_vet_role_alone_names_no_practice(self):
        user = User.objects.create_user('newvet', password='pw')
        assign_role(user, vet_role())
        self.client.force_login(user)
        response = self.client.get(APP)
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'not set up for C-vet', status_code=403)
        self.assertEqual(self.client.get(SESSION).json(), {'error': 'no_access'})

    def test_an_administrator_is_not_a_member_of_the_practice(self):
        make_practice()
        self.client.force_login(make_admin())
        self.assertEqual(self.client.get(APP).status_code, 403)
        self.assertEqual(self.client.get(YARD).status_code, 403)
        self.assertEqual(self.client.get(NOTES).status_code, 403)

    def test_a_member_who_is_made_inactive_loses_access_at_once(self):
        # How a lost phone is cut off: its sign-in is still valid, and
        # the next request is refused.
        user = make_vet()
        self.client.force_login(user)
        self.assertEqual(self.client.get(SESSION).status_code, 200)
        PracticeMember.objects.filter(user=user).update(is_active=False)
        self.assertEqual(self.client.get(SESSION).status_code, 403)
        self.assertEqual(self.client.post(NOTES, note_data()).status_code, 403)

    def test_a_closed_practice_loses_access(self):
        user = make_vet()
        user.practice_membership.practice.__class__.objects.update(is_active=False)
        self.client.force_login(user)
        self.assertEqual(self.client.get(SESSION).status_code, 403)

    def test_a_farrier_login_cannot_open_it(self):
        self.client.force_login(make_user_with_access('farrier', farrier_portal='full'))
        self.assertRedirects(self.client.get(YARD), '/farrier/', fetch_redirect_response=False)

    def test_a_vet_of_the_practice_opens_the_page(self):
        self.client.force_login(make_vet())
        response = self.client.get(APP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-notes-url="/c-vet/api/notes/"')

    def test_the_page_holds_no_token_and_no_name(self):
        # The service worker stores this page and opens it for whoever
        # holds the phone, signed in or not.
        self.client.force_login(make_vet(first_name='Tim', last_name='Vetson'))
        body = self.client.get(APP).content.decode()
        self.assertNotIn('csrfmiddlewaretoken', body)
        self.assertNotIn('Vetson', body)
        self.assertNotIn('Hill Vets', body)

    def test_only_the_page_allows_the_microphone(self):
        self.client.force_login(make_vet())
        self.assertIn('microphone=(self)', self.client.get(APP)['Permissions-Policy'])
        for url in (SESSION, '/_health/'):
            self.assertIn('microphone=()', self.client.get(url)['Permissions-Policy'], url)

    def test_post_needs_a_csrf_token(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(make_vet())
        self.assertEqual(strict.post(NOTES, note_data()).status_code, 403)
        token = strict.get(SESSION).json()['csrf']
        response = strict.post(NOTES, note_data(), headers={'X-CSRFToken': token})
        self.assertEqual(response.status_code, 201)

    def test_the_session_says_who_which_practice_and_test_mode(self):
        self.client.force_login(make_vet(first_name='Tim', last_name='Hill'))
        data = self.client.get(SESSION).json()
        self.assertEqual(data['user']['name'], 'Tim Hill')
        self.assertEqual(data['practice'], 'Hill Vets')
        self.assertIs(data['test_mode'], True)
        with override_settings(CVET_TEST_MODE=False):
            self.assertIs(self.client.get(SESSION).json()['test_mode'], False)


@override_settings(CVET_ENABLED=True)
class ServiceWorkerTests(TestCase):
    def test_it_is_javascript_served_under_its_scope(self):
        # No sign-in: the browser fetches it again on its own, and it
        # holds only addresses.
        response = self.client.get(WORKER)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/javascript'))
        self.assertEqual(response['Cache-Control'], 'no-cache')

    def test_it_stores_the_page_and_every_file_the_page_loads(self):
        worker = self.client.get(WORKER).content.decode()
        self.assertIn('const SHELL = "/c-vet/";', worker)
        self.client.force_login(make_vet())
        page = self.client.get(APP).content.decode()
        for name in ('cvet/app.css', 'cvet/app_core.js', 'cvet/app.js', 'cvet/manifest.json'):
            # The same address, version and all, in both places: a file
            # the page asks for under another address is not in the store.
            start = page.index(f'/static/{name}')
            address = page[start:page.index('"', start)]
            self.assertIn('?v=', address)
            self.assertIn(f'"{address}"', worker, name)


class YardCase(TestCase):
    """A yard with one placed horse, and a vet signed in."""

    def setUp(self):
        # A folder of its own for each test, so a count of stored files
        # sees only this test's files. The hourly limit counts in the
        # cache, which the tests share: start each one from zero.
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        self.enterContext(override_settings(MEDIA_ROOT=media, CVET_ENABLED=True))
        cache.clear()
        BusinessSettings.objects.create(business_name='Palmer Farm')
        owner = Owner.objects.create(name='Jo Smith')
        field = Location.objects.create(name='Top Field', site='Home')
        self.horse = Horse.objects.create(
            name='Bramble', microchip='985100012345678', passport_number='826002',
        )
        Placement.objects.create(
            horse=self.horse, owner=owner, location=field, start_date=timezone.localdate(),
            rate_type=RateType.objects.create(name='Grass', daily_rate=5),
        )
        self.vet = make_vet()
        self.practice = self.vet.practice_membership.practice
        self.client.force_login(self.vet)

    def stored_files(self):
        storage = ClinicalFile._meta.get_field('file').storage
        found = []
        for kind in ('photo', 'audio'):
            if storage.exists(f'cvet/{kind}'):
                found += storage.listdir(f'cvet/{kind}')[1]
        return found


class YardDownloadTests(YardCase):
    def test_lists_active_horses_with_identity_owner_and_place(self):
        Horse.objects.create(name='Gone', is_active=False)
        data = self.client.get(YARD).json()
        # The yard is the practice's first client, and the horses are its horses.
        yard_client = data['clients'][0]
        self.assertEqual(
            (yard_client['name'], yard_client['kind'], yard_client['is_yard']), ('Palmer Farm', 'yard', True),
        )
        self.assertEqual(data['horses'], [{
            'id': self.horse.pk,
            'client_id': yard_client['id'],
            'name': 'Bramble',
            'microchip': '985100012345678',
            'passport': '826002',
            'location': 'Top Field',
            'owner': 'Jo Smith',
            'payers': [{'name': 'Jo Smith', 'share': '100.00'}],
            'due': [],
        }])
        self.assertEqual(data['prices'], [])
        self.assertEqual(data['practice'], 'Hill Vets')
        # The practice's client is the yard, by its own name in C-gate.
        self.assertEqual(data['yard'], {'name': 'Palmer Farm', 'bill_to': 'yard'})
        self.assertIs(data['test_mode'], True)

    def test_no_horses_without_a_link_to_the_yard(self):
        self.client.force_login(make_vet('far', practice=make_practice('Far Vets', linked=False)))
        # The practice can still have clients of its own, so the download
        # works: it holds no yard, no yard client and no horse.
        data = self.client.get(YARD).json()
        self.assertEqual((data['yard'], data['clients'], data['horses']), (None, [], []))

    def test_no_horses_after_the_link_ends(self):
        self.assertEqual(len(self.client.get(YARD).json()['horses']), 1)
        PracticeYardLink.objects.update(ended_on=timezone.localdate())
        data = self.client.get(YARD).json()
        self.assertEqual((data['yard'], data['clients'], data['horses']), (None, [], []))

    def test_is_never_stored_by_a_browser(self):
        self.assertIn('no-store', self.client.get(YARD)['Cache-Control'])


class NoteUploadTests(YardCase):
    def test_a_note_becomes_a_clinical_note_of_the_practice(self):
        written = timezone.now() - timezone.timedelta(hours=2)
        visit_id = str(uuid.uuid4())
        response = self.client.post(NOTES, note_data(
            horse_id=str(self.horse.pk), device_created_at=written.isoformat(),
            attempts='3', visit_id=visit_id,
        ))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['status'], 'created')
        note = ClinicalNote.objects.get()
        self.assertEqual(note.practice, self.practice)
        self.assertEqual(note.author, self.vet)
        self.assertEqual(note.horse, self.horse)
        self.assertEqual(note.findings, 'Mild heat left fore.')
        self.assertEqual(note.seen_on, timezone.localtime(written).date())
        # The horse's numbers come from the yard's record, not the phone.
        self.assertEqual(note.horse_microchip, '985100012345678')
        self.assertEqual(note.horse_passport, '826002')
        self.assertEqual(str(note.visit.client_id), visit_id)
        self.assertEqual(note.visit.practice, self.practice)

    def test_the_receipt_holds_the_measures_and_no_clinical_text(self):
        written = timezone.now() - timezone.timedelta(hours=2)
        self.client.post(NOTES, note_data(device_created_at=written.isoformat(), attempts='3'))
        receipt = SyncReceipt.objects.get()
        self.assertEqual(receipt.attempts, 3)
        self.assertEqual(receipt.text_length, len('Mild heat left fore.'))
        self.assertEqual(receipt.client_info, {'standalone': True, 'openMs': 240})
        self.assertGreater(receipt.waited.total_seconds(), 2 * 3600 - 5)
        values = ' '.join(str(getattr(receipt, f.name)) for f in SyncReceipt._meta.fields if f.name != 'note')
        self.assertNotIn('heat', values)

    def test_the_same_note_sent_twice_is_stored_once(self):
        data = note_data(visit_id=str(uuid.uuid4()))
        first = self.client.post(NOTES, data)
        second = self.client.post(NOTES, dict(data, attempts='2', note='Changed on a repeat.'))
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['status'], 'duplicate')
        self.assertEqual(second.json()['id'], first.json()['id'])
        note = ClinicalNote.objects.get()
        # The count of tries is the one thing a repeat updates.
        self.assertEqual(note.findings, 'Mild heat left fore.')
        self.assertEqual(note.receipt.attempts, 2)
        self.assertEqual(Visit.objects.count(), 1)
        self.assertEqual(note.history.count(), 1)

    def test_a_repeat_with_files_adds_no_second_file(self):
        data = note_data(photo_id=str(uuid.uuid4()))
        for _ in range(2):
            self.client.post(NOTES, dict(data, photo=photo_upload()))
        self.assertEqual(ClinicalFile.objects.count(), 1)
        self.assertEqual(len(self.stored_files()), 1)

    def test_notes_of_one_day_share_a_visit(self):
        visit_id = str(uuid.uuid4())
        for _ in range(3):
            self.client.post(NOTES, note_data(visit_id=visit_id))
        visit = Visit.objects.get()
        self.assertEqual(visit.notes.count(), 3)
        self.assertEqual(visit.author_name, 'vet')

    def test_another_practice_cannot_learn_about_a_note_by_its_id(self):
        data = note_data(visit_id=str(uuid.uuid4()))
        self.client.post(NOTES, data)
        self.client.force_login(make_vet('other', practice=make_practice('Other Vets')))
        response = self.client.post(NOTES, data)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'error': 'client_id_in_use'})
        self.assertEqual(ClinicalNote.objects.count(), 1)

    def test_another_practices_visit_id_does_not_join_their_visit(self):
        visit_id = str(uuid.uuid4())
        self.client.post(NOTES, note_data(visit_id=visit_id))
        other = make_practice('Other Vets')
        self.client.force_login(make_vet('other', practice=other))
        self.assertEqual(self.client.post(NOTES, note_data(visit_id=visit_id)).status_code, 201)
        theirs = ClinicalNote.objects.get(practice=other)
        self.assertIsNone(theirs.visit)
        self.assertEqual(Visit.objects.get().notes.count(), 1)

    def test_bad_ids_dates_and_empty_notes_are_refused(self):
        for change, error in (
            ({'client_id': 'not-a-uuid'}, 'client_id_invalid'),
            ({'device_created_at': 'yesterday'}, 'device_created_at_invalid'),
            ({'note': 'x' * 5001}, 'note_too_long'),
            ({'note': '   '}, 'note_empty'),
            ({'horse_name': ''}, 'horse_missing'),
        ):
            response = self.client.post(NOTES, note_data(**change))
            self.assertEqual(response.status_code, 400, change)
            self.assertEqual(response.json(), {'error': error})
        self.assertFalse(ClinicalNote.objects.exists())
        self.assertFalse(SyncReceipt.objects.exists())

    def test_a_horse_that_has_gone_does_not_lose_the_note(self):
        response = self.client.post(NOTES, note_data(horse_id='999999'))
        self.assertEqual(response.status_code, 201)
        note = ClinicalNote.objects.get()
        self.assertIsNone(note.horse)
        self.assertEqual(note.horse_name, 'Bramble')

    def test_a_note_saved_before_the_link_ended_is_still_stored(self):
        # The vet saved it at the yard with no signal. The link ended
        # before the phone had a signal. The note is the practice's
        # record of work done: keep it, without a link to the yard's horse.
        PracticeYardLink.objects.update(ended_on=timezone.localdate())
        response = self.client.post(NOTES, note_data(horse_id=str(self.horse.pk)))
        self.assertEqual(response.status_code, 201)
        note = ClinicalNote.objects.get()
        self.assertIsNone(note.horse)
        self.assertEqual(note.horse_name, 'Bramble')
        self.assertEqual(note.horse_microchip, '')

    def test_test_mode_marks_the_note_and_real_mode_does_not(self):
        self.client.post(NOTES, note_data(visit_id=str(uuid.uuid4())))
        with override_settings(CVET_TEST_MODE=False):
            self.client.post(NOTES, note_data(visit_id=str(uuid.uuid4())))
        self.assertEqual(sorted(ClinicalNote.objects.values_list('is_test', flat=True)), [False, True])
        self.assertEqual(sorted(Visit.objects.values_list('is_test', flat=True)), [False, True])
        self.assertEqual(sorted(SyncReceipt.objects.values_list('is_test', flat=True)), [False, True])

    def test_a_phone_that_sends_in_a_loop_is_slowed_down(self):
        with mock.patch('cvet.views.NOTES_PER_HOUR', 2):
            codes = [self.client.post(NOTES, note_data()).status_code for _ in range(3)]
        self.assertEqual(codes, [201, 201, 429])


class NoteFileTests(YardCase):
    def test_a_photo_is_stored_under_our_name_and_checked_against_its_hash(self):
        photo = png_bytes()
        file_id = str(uuid.uuid4())
        response = self.client.post(NOTES, note_data(
            photo=SimpleUploadedFile('../../evil.php', photo, 'image/png'),
            photo_id=file_id, photo_sha256=hashlib.sha256(photo).hexdigest(),
        ))
        self.assertEqual(response.status_code, 201)
        self.assertIs(response.json()['photo_intact'], True)
        record = ClinicalFile.objects.get()
        self.assertEqual(str(record.client_id), file_id)
        self.assertEqual(record.practice, self.practice)
        self.assertEqual(record.kind, 'photo')
        self.assertEqual(record.content_type, 'image/png')
        self.assertRegex(record.file.name, r'^cvet/photo/[0-9a-f]{32}\.png$')
        self.assertEqual(record.file.read(), photo)
        self.assertIs(SyncReceipt.objects.get().photo_intact, True)

    def test_a_photo_that_changed_on_the_way_is_marked(self):
        response = self.client.post(NOTES, note_data(photo=photo_upload(), photo_sha256='0' * 64))
        self.assertIs(response.json()['photo_intact'], False)

    def test_a_note_can_be_a_photo_with_no_text(self):
        self.assertEqual(self.client.post(NOTES, note_data(note='', photo=photo_upload())).status_code, 201)
        self.assertEqual(ClinicalNote.objects.get().findings, '')

    def test_a_file_that_is_not_a_photo_is_refused(self):
        response = self.client.post(NOTES, note_data(
            photo=SimpleUploadedFile('p.jpg', b'<script>alert(1)</script>', 'image/jpeg'),
        ))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'photo_not_an_image'})
        self.assertFalse(ClinicalNote.objects.exists())

    def test_a_recording_is_stored_with_an_extension_from_our_list(self):
        audio = b'\x00\x00\x00\x1cftypM4A fake recording'
        response = self.client.post(NOTES, note_data(
            audio=SimpleUploadedFile('voice.html', audio, 'audio/mp4;codecs=mp4a.40.2'),
            audio_sha256=hashlib.sha256(audio).hexdigest(),
        ))
        self.assertEqual(response.status_code, 201)
        record = ClinicalFile.objects.get()
        self.assertRegex(record.file.name, r'^cvet/audio/[0-9a-f]{32}\.m4a$')
        self.assertEqual(record.content_type, 'audio/mp4')
        self.assertIs(SyncReceipt.objects.get().audio_intact, True)

    def test_a_recording_of_another_type_is_refused_and_leaves_no_file(self):
        response = self.client.post(NOTES, note_data(
            photo=photo_upload(), audio=SimpleUploadedFile('a', b'<html>', 'text/html'),
        ))
        self.assertEqual(response.json(), {'error': 'audio_type_not_allowed'})
        self.assertFalse(ClinicalNote.objects.exists())
        self.assertEqual(self.stored_files(), [])

    def test_a_file_id_that_is_in_use_stores_nothing_and_leaves_no_file(self):
        file_id = str(uuid.uuid4())
        self.client.post(NOTES, note_data(photo=photo_upload(), photo_id=file_id))
        response = self.client.post(NOTES, note_data(photo=photo_upload(), photo_id=file_id))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'error': 'id_in_use'})
        self.assertEqual(ClinicalNote.objects.count(), 1)
        self.assertEqual(len(self.stored_files()), 1)

    def test_deleting_the_note_deletes_its_files(self):
        self.client.post(NOTES, note_data(photo=photo_upload()))
        self.assertEqual(len(self.stored_files()), 1)
        ClinicalNote.objects.get().delete()
        self.assertEqual(self.stored_files(), [])

    def _file_url(self):
        photo = png_bytes()
        self.client.post(NOTES, note_data(horse_id=str(self.horse.pk), photo=photo_upload(photo)))
        note = self.client.get(NOTES).json()['notes'][0]
        return note['files'][0]['url'], photo

    def test_the_practice_downloads_its_file(self):
        url, photo = self._file_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), photo)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn('no-store', response['Cache-Control'])

    def test_nobody_else_downloads_it(self):
        url, _ = self._file_url()
        self.client.force_login(make_vet('other', practice=make_practice('Other Vets')))
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(make_admin())
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(User.objects.create_superuser('root', 'root@example.com', 'pw'))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 401)


class NoteListTests(YardCase):
    def test_a_practice_lists_its_own_notes_only(self):
        self.client.post(NOTES, note_data(horse_id=str(self.horse.pk), note='Ours.'))
        other = make_vet('other', practice=make_practice('Other Vets'))
        self.client.force_login(other)
        self.client.post(NOTES, note_data(horse_id=str(self.horse.pk), note='Theirs.'))
        self.assertEqual([n['findings'] for n in self.client.get(NOTES).json()['notes']], ['Theirs.'])
        self.client.force_login(self.vet)
        notes = self.client.get(NOTES).json()['notes']
        self.assertEqual([n['findings'] for n in notes], ['Ours.'])
        self.assertEqual(notes[0]['author'], 'vet')
        self.assertIs(notes[0]['is_test'], True)

    def test_filters_by_horse(self):
        second = Horse.objects.create(name='Clover')
        self.client.post(NOTES, note_data(horse_id=str(self.horse.pk), note='Bramble note.'))
        self.client.post(NOTES, note_data(horse_id=str(second.pk), horse_name='Clover', note='Clover note.'))
        notes = self.client.get(f'{NOTES}?horse={second.pk}').json()['notes']
        self.assertEqual([n['findings'] for n in notes], ['Clover note.'])
        self.assertEqual(self.client.get(f'{NOTES}?horse=abc').status_code, 400)


class TestNotesCleanUpTests(YardCase):
    """An administrator deletes test notes without reading them."""

    def test_only_test_notes_go_with_their_files_and_history(self):
        self.client.post(NOTES, note_data(photo=photo_upload(), visit_id=str(uuid.uuid4())))
        with override_settings(CVET_TEST_MODE=False):
            self.client.post(NOTES, note_data(note='A real note.', visit_id=str(uuid.uuid4())))
        self.assertEqual(delete_test_notes(self.practice), 1)
        self.assertEqual([n.findings for n in access.notes_for(self.practice)], ['A real note.'])
        self.assertEqual(self.stored_files(), [])
        self.assertEqual(list(ClinicalNote.history.values_list('findings', flat=True)), ['A real note.'])
        self.assertEqual(Visit.objects.count(), 1)
        # The measures of the test outlive the test notes.
        self.assertEqual(SyncReceipt.objects.count(), 2)

    def test_another_practices_test_notes_stay(self):
        self.client.post(NOTES, note_data())
        other = make_practice('Other Vets')
        self.assertEqual(delete_test_notes(other), 0)
        self.assertEqual(ClinicalNote.objects.count(), 1)


@override_settings(CVET_ENABLED=True, CVET_SESSION_IDLE_AGE=90 * 86400, CVET_SESSION_ABSOLUTE_AGE=90 * 86400)
class LongSignInTests(TestCase):
    """A vet's phone stays signed in: there is no signal at the yard to sign in with."""

    DAY = 86400

    def _sign_in(self, user):
        client = Client()
        user.set_password('pw')
        user.save()
        self.assertTrue(client.login(username=user.username, password='pw'))
        return client

    def _age_session(self, client, days):
        from core.middleware import SESSION_STARTED_KEY
        session = client.session
        session[SESSION_STARTED_KEY] -= days * self.DAY
        session.save()

    def test_a_vet_is_still_signed_in_after_60_days(self):
        client = self._sign_in(make_vet())
        self.assertEqual(client.get(SESSION).status_code, 200)
        self._age_session(client, 60)
        self.assertEqual(client.get(SESSION).status_code, 200)
        self.assertEqual(client.session.get_expiry_age(), 90 * self.DAY)

    def test_a_vet_is_signed_out_after_90_days(self):
        client = self._sign_in(make_vet())
        self._age_session(client, 91)
        self.assertEqual(client.get(SESSION).status_code, 401)

    def test_yard_staff_keep_the_30_day_limit(self):
        client = self._sign_in(make_user_with_access('staff', dashboard='full', health='full'))
        self.assertEqual(client.get('/health/').status_code, 200)
        self._age_session(client, 31)
        self.assertEqual(client.get('/health/').status_code, 302)

    def test_a_login_that_stops_being_a_vet_loses_the_long_sign_in(self):
        from core.middleware import SESSION_ABSOLUTE_AGE_KEY
        user = make_vet()
        client = self._sign_in(user)
        self.assertEqual(client.session[SESSION_ABSOLUTE_AGE_KEY], 90 * self.DAY)
        staff_role = make_user_with_access('template', dashboard='full').role_assignment.role
        assign_role(User.objects.get(pk=user.pk), staff_role)
        client.get('/')
        self.assertNotIn(SESSION_ABSOLUTE_AGE_KEY, client.session)
        self._age_session(client, 31)
        self.assertEqual(client.get('/').status_code, 302)
        self.assertIn('/accounts/login/', client.get('/')['Location'])
