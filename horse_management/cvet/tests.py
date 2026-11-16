"""The C-vet offline prototype (docs/C_VET_PLAN.md, phase 1).

The page is a test tool, but it runs on the live site, so the gate, the
upload checks and the "stored once" rule are tested like any other code.
"""

import hashlib
import io
import shutil
import tempfile
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access

from .models import PrototypeEntry

User = get_user_model()

APP = '/c-vet/'
WORKER = '/c-vet/sw.js'
SESSION = '/c-vet/api/session/'
BOOTSTRAP = '/c-vet/api/bootstrap/'
ENTRIES = '/c-vet/api/entries/'

def png_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buffer, 'PNG')
    return buffer.getvalue()


def entry_data(**extra):
    data = {
        'client_id': str(uuid.uuid4()),
        'horse_name': 'Bramble',
        'note': 'Mild heat left fore.',
        'device_created_at': timezone.now().isoformat(),
        'attempts': '1',
        'client_info': '{"standalone": true}',
    }
    data.update(extra)
    return data


class TurnedOffTests(TestCase):
    """With the setting off, which is the default, nothing is there."""

    def test_every_address_is_a_404(self):
        self.client.force_login(make_admin())
        for url in (APP, WORKER, SESSION, BOOTSTRAP):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.post(ENTRIES, entry_data()).status_code, 404)
        self.assertFalse(PrototypeEntry.objects.exists())


@override_settings(CVET_PROTOTYPE_ENABLED=True)
class GateTests(TestCase):
    def test_the_page_sends_a_signed_out_person_to_sign_in(self):
        response = self.client.get(APP)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_the_api_answers_a_signed_out_phone_in_json(self):
        # A redirect to the sign-in page would look like a failure to the
        # phone. 401 tells it to keep its notes and ask for a sign-in.
        for url in (SESSION, BOOTSTRAP):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 401, url)
            self.assertEqual(response.json(), {'error': 'signed_out'})
        response = self.client.post(ENTRIES, entry_data())
        self.assertEqual(response.status_code, 401)

    def test_health_view_only_is_not_enough(self):
        self.client.force_login(make_user_with_access('viewer', health='view', dashboard='full'))
        self.assertEqual(self.client.get(APP).status_code, 302)
        self.assertEqual(self.client.get(BOOTSTRAP).status_code, 403)
        self.assertEqual(self.client.post(ENTRIES, entry_data()).status_code, 403)
        self.assertFalse(PrototypeEntry.objects.exists())

    def test_a_farrier_login_cannot_open_it(self):
        self.client.force_login(make_user_with_access('farrier', farrier_portal='full'))
        self.assertRedirects(
            self.client.get(BOOTSTRAP), '/farrier/', fetch_redirect_response=False,
        )

    def test_health_full_opens_the_page(self):
        self.client.force_login(make_user_with_access('vet', health='full'))
        response = self.client.get(APP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-entries-url="/c-vet/api/entries/"')

    def test_the_page_holds_no_token_and_no_name(self):
        # The service worker stores this page and opens it for whoever
        # holds the phone, signed in or not.
        user = make_user_with_access('vet', health='full')
        user.first_name, user.last_name = 'Tim', 'Vetson'
        user.save()
        self.client.force_login(user)
        body = self.client.get(APP).content.decode()
        self.assertNotIn('csrfmiddlewaretoken', body)
        self.assertNotIn('Vetson', body)

    def test_only_the_page_allows_the_microphone(self):
        self.client.force_login(make_user_with_access('vet', health='full'))
        self.assertIn('microphone=(self)', self.client.get(APP)['Permissions-Policy'])
        for url in (SESSION, '/_health/'):
            self.assertIn('microphone=()', self.client.get(url)['Permissions-Policy'], url)

    def test_post_needs_a_csrf_token(self):
        user = make_user_with_access('vet', health='full')
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(user)
        self.assertEqual(strict.post(ENTRIES, entry_data()).status_code, 403)
        token = strict.get(SESSION).json()['csrf']
        response = strict.post(ENTRIES, entry_data(), headers={'X-CSRFToken': token})
        self.assertEqual(response.status_code, 201)


@override_settings(CVET_PROTOTYPE_ENABLED=True)
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
        self.client.force_login(make_user_with_access('vet', health='full'))
        page = self.client.get(APP).content.decode()
        for name in ('cvet/proto.css', 'cvet/proto_core.js', 'cvet/proto.js', 'cvet/manifest.json'):
            # The same address, version and all, in both places: a file
            # the page asks for under another address is not in the store.
            start = page.index(f'/static/{name}')
            address = page[start:page.index('"', start)]
            self.assertIn('?v=', address)
            self.assertIn(f'"{address}"', worker, name)


@override_settings(CVET_PROTOTYPE_ENABLED=True)
class BootstrapTests(TestCase):
    def setUp(self):
        owner = Owner.objects.create(name='Jo Smith')
        field = Location.objects.create(name='Top Field', site='Home')
        self.horse = Horse.objects.create(
            name='Bramble', microchip='985100012345678', passport_number='826002',
        )
        Placement.objects.create(
            horse=self.horse, owner=owner, location=field, start_date=timezone.localdate(),
            rate_type=RateType.objects.create(name='Grass', daily_rate=5),
        )
        Horse.objects.create(name='Gone', is_active=False)

    def test_lists_active_horses_with_identity_and_place(self):
        self.client.force_login(make_user_with_access('vet', health='full', owners='view'))
        data = self.client.get(BOOTSTRAP).json()
        self.assertEqual(data['horses'], [{
            'id': self.horse.pk,
            'name': 'Bramble',
            'microchip': '985100012345678',
            'passport': '826002',
            'location': 'Top Field',
            'owner': 'Jo Smith',
        }])
        self.assertEqual(data['user']['name'], 'vet')

    def test_owner_names_need_access_to_owners(self):
        self.client.force_login(make_user_with_access('vet', health='full'))
        horse = self.client.get(BOOTSTRAP).json()['horses'][0]
        self.assertEqual(horse['owner'], '')
        self.assertEqual(horse['location'], 'Top Field')

    def test_is_never_stored_by_a_browser(self):
        self.client.force_login(make_user_with_access('vet', health='full'))
        self.assertIn('no-store', self.client.get(BOOTSTRAP)['Cache-Control'])


@override_settings(CVET_PROTOTYPE_ENABLED=True)
class EntryTests(TestCase):
    def setUp(self):
        # A folder of its own for each test, so a count of stored files
        # sees only this test's files. The hourly limit counts in the
        # cache, which the tests share: start each one from zero.
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        self.enterContext(override_settings(MEDIA_ROOT=media))
        cache.clear()
        self.user = make_user_with_access('vet', health='full')
        self.client.force_login(self.user)
        self.horse = Horse.objects.create(name='Bramble')

    def test_a_note_is_stored(self):
        created = timezone.now() - timezone.timedelta(hours=2)
        response = self.client.post(ENTRIES, entry_data(
            horse_id=str(self.horse.pk), device_created_at=created.isoformat(), attempts='3',
        ))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['status'], 'created')
        entry = PrototypeEntry.objects.get()
        self.assertEqual(entry.user, self.user)
        self.assertEqual(entry.horse, self.horse)
        self.assertEqual(entry.note, 'Mild heat left fore.')
        self.assertEqual(entry.attempts, 3)
        self.assertEqual(entry.client_info, {'standalone': True})
        self.assertGreater(entry.waited.total_seconds(), 2 * 3600 - 5)

    def test_the_same_note_sent_twice_is_stored_once(self):
        data = entry_data()
        first = self.client.post(ENTRIES, data)
        second = self.client.post(ENTRIES, dict(data, attempts='2'))
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['status'], 'duplicate')
        self.assertEqual(second.json()['id'], first.json()['id'])
        entry = PrototypeEntry.objects.get()
        # The count of tries is the one thing a repeat updates.
        self.assertEqual(entry.attempts, 2)
        self.assertEqual(entry.note, 'Mild heat left fore.')

    def test_a_repeat_with_files_adds_no_second_file(self):
        photo = png_bytes()
        data = entry_data()
        for _ in range(2):
            self.client.post(ENTRIES, dict(data, photo=SimpleUploadedFile('p', photo, 'image/png')))
        entry = PrototypeEntry.objects.get()
        folder = entry.photo.storage.listdir('cvet_prototype/photos')[1]
        self.assertEqual(len(folder), 1)

    def test_another_login_cannot_learn_about_a_note_by_its_id(self):
        data = entry_data()
        self.client.post(ENTRIES, data)
        self.client.force_login(make_user_with_access('other', health='full'))
        response = self.client.post(ENTRIES, data)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'error': 'client_id_in_use'})
        self.assertEqual(PrototypeEntry.objects.count(), 1)

    def test_bad_ids_and_dates_are_refused(self):
        for change in ({'client_id': 'not-a-uuid'}, {'device_created_at': 'yesterday'}):
            response = self.client.post(ENTRIES, entry_data(**change))
            self.assertEqual(response.status_code, 400, change)
        self.assertEqual(
            self.client.post(ENTRIES, entry_data(note='x' * 5001)).json(),
            {'error': 'note_too_long'},
        )
        self.assertFalse(PrototypeEntry.objects.exists())

    def test_a_horse_that_is_gone_does_not_lose_the_note(self):
        response = self.client.post(ENTRIES, entry_data(horse_id='999999'))
        self.assertEqual(response.status_code, 201)
        entry = PrototypeEntry.objects.get()
        self.assertIsNone(entry.horse)
        self.assertEqual(entry.horse_name, 'Bramble')

    def test_a_photo_is_stored_under_our_name_and_checked_against_its_hash(self):
        photo = png_bytes()
        response = self.client.post(ENTRIES, entry_data(
            photo=SimpleUploadedFile('../../evil.php', photo, 'image/png'),
            photo_sha256=hashlib.sha256(photo).hexdigest(),
        ))
        self.assertEqual(response.status_code, 201)
        self.assertIs(response.json()['photo_intact'], True)
        entry = PrototypeEntry.objects.get()
        self.assertRegex(entry.photo.name, r'^cvet_prototype/photos/[0-9a-f]{32}\.png$')
        self.assertEqual(entry.photo_bytes, len(photo))
        self.assertEqual(entry.photo.read(), photo)

    def test_a_photo_that_changed_on_the_way_is_marked(self):
        response = self.client.post(ENTRIES, entry_data(
            photo=SimpleUploadedFile('p', png_bytes(), 'image/png'),
            photo_sha256='0' * 64,
        ))
        self.assertIs(response.json()['photo_intact'], False)

    def test_a_file_that_is_not_a_photo_is_refused(self):
        response = self.client.post(ENTRIES, entry_data(
            photo=SimpleUploadedFile('p.jpg', b'<script>alert(1)</script>', 'image/jpeg'),
        ))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'photo_not_an_image'})
        self.assertFalse(PrototypeEntry.objects.exists())

    def test_a_recording_is_stored_with_an_extension_from_our_list(self):
        audio = b'\x00\x00\x00\x1cftypM4A fake recording'
        response = self.client.post(ENTRIES, entry_data(
            audio=SimpleUploadedFile('voice.html', audio, 'audio/mp4;codecs=mp4a.40.2'),
            audio_sha256=hashlib.sha256(audio).hexdigest(),
        ))
        self.assertEqual(response.status_code, 201)
        entry = PrototypeEntry.objects.get()
        self.assertRegex(entry.audio.name, r'^cvet_prototype/audio/[0-9a-f]{32}\.m4a$')
        self.assertIs(entry.audio_intact, True)

    def test_a_recording_of_another_type_is_refused_and_leaves_no_file(self):
        response = self.client.post(ENTRIES, entry_data(
            photo=SimpleUploadedFile('p', png_bytes(), 'image/png'),
            audio=SimpleUploadedFile('a', b'<html>', 'text/html'),
        ))
        self.assertEqual(response.json(), {'error': 'audio_type_not_allowed'})
        self.assertFalse(PrototypeEntry.objects.exists())
        storage = PrototypeEntry._meta.get_field('photo').storage
        if storage.exists('cvet_prototype/photos'):
            self.assertEqual(storage.listdir('cvet_prototype/photos')[1], [])

    def test_a_phone_that_sends_in_a_loop_is_slowed_down(self):
        from unittest import mock

        with mock.patch('cvet.views.ENTRIES_PER_HOUR', 2):
            codes = [self.client.post(ENTRIES, entry_data()).status_code for _ in range(3)]
        self.assertEqual(codes, [201, 201, 429])

    def test_deleting_the_row_deletes_its_files(self):
        self.client.post(ENTRIES, entry_data(photo=SimpleUploadedFile('p', png_bytes(), 'image/png')))
        entry = PrototypeEntry.objects.get()
        name, storage = entry.photo.name, entry.photo.storage
        self.assertTrue(storage.exists(name))
        PrototypeEntry.objects.all().delete()
        self.assertFalse(storage.exists(name))
