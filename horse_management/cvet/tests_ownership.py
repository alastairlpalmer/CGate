"""Phase 2, the ownership seam (docs/C_VET_PLAN.md, sections 3 and 6).

The phase is done when "a yard login cannot read a clinical note by any
route". Three kinds of test hold that:

* **The sweep.** A yard administrator requests every GET address the
  site has, with the ids of a real horse, owner and note, and with the
  note's text as a search. The note's text must come back nowhere.
* **The tripwires.** No code outside this app may name the clinical
  models. A person who adds the first yard page that reads them gets a
  failing test that points here, before the page ships.
* **The rules.** Scope by practice, the vet's fence, and what happens to
  a note when the yard changes or deletes the horse.
"""

import io
import re
import zipfile
from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from django.test import Client, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

from core import audit, gdpr
from core.features import FEATURES
from core.models import Horse, Location, Owner, Placement, RateType
from core.permissions import is_provider_only
from core.roles_testutils import make_admin, make_user_with_access

from . import access
from .models import ClinicalFile, ClinicalNote, Practice, PracticeMember, PracticeYardLink, SyncReceipt, Visit
from .testutils import VET_ACCESS, make_practice, make_vet, vet_role

User = get_user_model()

# Text that is in no template, fixture or other record.
MARKER = 'zqxjkv-laminitis-marker'
TREATMENT_MARKER = 'zqxjkv-phenylbutazone-marker'


def make_note(practice, horse, author=None, **fields):
    data = {
        'practice': practice,
        'horse': horse,
        'horse_name': horse.name,
        'horse_microchip': horse.microchip,
        'author': author,
        'findings': f'Mild heat left fore. {MARKER}',
        'treatment': f'Rest. {TREATMENT_MARKER}',
        'follow_up_date': timezone.localdate() + timezone.timedelta(days=14),
    }
    data.update(fields)
    return ClinicalNote.objects.create(**data)


class YardFixture(TestCase):
    """A yard with one horse and owner, and a practice with a note on that horse."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(name='Jo Smith', email='jo@example.com')
        field = Location.objects.create(name='Top Field', site='Home')
        cls.horse = Horse.objects.create(name='Bramble', microchip='985100012345678')
        Placement.objects.create(
            horse=cls.horse, owner=cls.owner, location=field, start_date=timezone.localdate(),
            rate_type=RateType.objects.create(name='Grass', daily_rate=5),
        )
        cls.practice = make_practice()
        cls.vet = make_vet()
        cls.note = make_note(cls.practice, cls.horse, author=cls.vet)
        # A change, so the history table holds the text too.
        cls.note.treatment += ' Recheck.'
        cls.note.save()
        # How it arrived: the one C-vet record the yard's administrator
        # does read, in the Django admin.
        cls.receipt = SyncReceipt.objects.create(
            practice=cls.practice, user=cls.vet, note=cls.note, note_client_id=cls.note.client_id,
            device_created_at=timezone.now(), text_length=len(cls.note.findings),
        )


def _response_text(response):
    """Everything a response carries, with ZIP files opened."""
    body = b''.join(response.streaming_content) if response.streaming else response.content
    parts = [body]
    if body[:2] == b'PK':
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                parts += [archive.read(name) for name in archive.namelist()]
        except zipfile.BadZipFile:
            pass
    parts.append(repr(sorted(response.headers.items())).encode())
    return b'\n'.join(parts).decode('utf-8', 'replace')


def _every_route(patterns=None, prefix=''):
    """Each URL pattern of the site as a route string, admin included."""
    if patterns is None:
        patterns = get_resolver().url_patterns
    for pattern in patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield from _every_route(pattern.url_patterns, route)
        elif isinstance(pattern, URLPattern):
            yield route


PARAMETER = re.compile(r'<(?:(?P<kind>\w+):)?\w+>')


def _addresses(route, ids):
    """Real addresses for one route: one for each id when it takes ids."""
    if '(' in route or '^' in route or '$' in route:
        return  # a regular expression (the media view): not swept here
    kinds = {match.group('kind') or 'str' for match in PARAMETER.finditer(route)}
    if not kinds:
        yield '/' + route
        return
    if kinds - {'int'}:
        return  # tokens, slugs and file paths: no id of ours fits
    for value in ids:
        yield '/' + PARAMETER.sub(str(value), route)


@override_settings(CVET_ENABLED=True, LOCATION_MAPS_ENABLED=True)
class YardCannotReadClinicalNotesTests(YardFixture):
    """The sweep."""

    def _sweep(self, user, stop_at_first=False):
        client = Client(raise_request_exception=False)
        client.force_login(user)
        ids = sorted({self.horse.pk, self.owner.pk, self.note.pk, self.practice.pk})
        seen, leaks = 0, []
        for route in _every_route():
            for address in _addresses(route, ids):
                for query in ('', f'?q={MARKER}', f'?search={MARKER}'):
                    if query and '<' in route:
                        continue
                    response = client.get(address + query)
                    seen += 1
                    text = _response_text(response)
                    # With a search, the page may print the search text
                    # back. The treatment text was never sent, so it can
                    # only come from the note.
                    if TREATMENT_MARKER in text or (not query and MARKER in text):
                        leaks.append(f'{address}{query} ({response.status_code})')
                        if stop_at_first:
                            return seen, leaks
        return seen, leaks

    def test_a_yard_administrator_finds_the_note_on_no_page(self):
        # A superuser: every yard page, and every screen of the Django
        # admin, which no role but this one reaches.
        root = User.objects.create_superuser('root', 'root@example.com', 'pw')
        seen, leaks = self._sweep(root)
        self.assertGreater(seen, 150, 'the sweep did not walk the site')
        self.assertEqual(leaks, [], 'a yard page shows a practice\'s clinical note')

    def test_the_sweep_can_see_a_leak(self):
        # The same sweep, pointed at text the yard does show. If this
        # finds nothing, the test above proves nothing.
        Horse.objects.filter(pk=self.horse.pk).update(notes=f'yard note {TREATMENT_MARKER}')
        root = User.objects.create_superuser('root', 'root@example.com', 'pw')
        _, leaks = self._sweep(root, stop_at_first=True)
        self.assertTrue(leaks, 'the sweep missed text that a yard page shows')

    def test_the_receipt_screen_shows_how_a_note_arrived_and_not_the_note(self):
        root = User.objects.create_superuser('root', 'root@example.com', 'pw')
        self.client.force_login(root)
        base = f'/{settings.ADMIN_URL}cvet/syncreceipt/'
        for url in (base, f'{base}{self.receipt.pk}/change/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            text = response.content.decode()
            self.assertNotIn(MARKER, text)
            # Not the note's name or id either: no way from here to it.
            self.assertNotIn('Clinical note', text)
            self.assertNotIn('clinicalnote', text)

    def test_the_owner_data_export_does_not_hold_it(self):
        # A subject access request goes to the yard. The practice answers
        # for its own records.
        text = _response_text(_Zip(gdpr.export_owner(self.owner)))
        self.assertNotIn(MARKER, text)
        self.assertNotIn(TREATMENT_MARKER, text)


class _Zip:
    """Enough of a response for ``_response_text`` to open a ZIP."""

    streaming = False
    headers = {}

    def __init__(self, content):
        self.content = content


class TripwireTests(TestCase):
    """By construction: the yard's code has no way to the notes."""

    CLINICAL = [ClinicalNote, ClinicalNote.history.model, ClinicalFile, Visit]

    def test_the_django_admin_does_not_register_clinical_notes(self):
        for model in self.CLINICAL:
            self.assertNotIn(model, admin.site._registry, f'{model.__name__} must not be in the admin')

    def test_a_horse_has_no_way_back_to_its_notes(self):
        for relation in Horse._meta.get_fields(include_hidden=True):
            if getattr(relation, 'related_model', None) in self.CLINICAL and relation.auto_created:
                self.assertTrue(
                    relation.hidden,
                    'ClinicalNote.horse must keep related_name="+": horse.<notes> would '
                    'hand the notes to every yard page that holds a horse',
                )
        self.assertFalse(hasattr(Horse(), 'clinicalnote_set'))
        self.assertFalse(hasattr(Horse(), 'clinical_notes'))

    def test_the_yard_audit_trail_does_not_track_them(self):
        tracked = ' '.join(path for _, _, path in audit.TRACKED_MODELS).lower()
        self.assertNotIn('cvet', tracked)

    def test_no_code_outside_cvet_names_the_clinical_models(self):
        # Allowed outside the app: the app's registration in settings and
        # the URL include. Nothing else may reach in.
        base = Path(settings.BASE_DIR)
        forbidden = re.compile(r'ClinicalNote|ClinicalFile|cvet\.models|cvet\.access|clinical_notes|from cvet|import cvet')
        offenders = []
        for path in list(base.rglob('*.py')) + list((base / 'templates').rglob('*.html')):
            relative = path.relative_to(base)
            top = relative.parts[0]
            if top in {'cvet', 'staticfiles', 'node_modules', 'venv', '.venv'}:
                continue
            if relative.parts[:2] == ('templates', 'cvet'):
                continue
            if relative.parts[:2] == ('tools', 'qa'):
                continue  # seed scripts for a throwaway database, not app code
            for number, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
                if forbidden.search(line):
                    offenders.append(f'{relative}:{number}: {line.strip()[:80]}')
        self.assertEqual(
            offenders, [],
            'Code outside cvet/ reaches the practice\'s clinical records. Read them only '
            'through cvet.access, inside the cvet app (docs/C_VET_PLAN.md, section 3).',
        )


class PracticeFilesTests(TestCase):
    """A practice's photos and recordings are not served by the yard's media view."""

    def setUp(self):
        import os
        import shutil
        import tempfile

        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        for folder in ('cvet/photo', 'horses'):
            os.makedirs(os.path.join(self.media, folder), exist_ok=True)
            with open(os.path.join(self.media, folder, 'x.jpg'), 'wb') as handle:
                handle.write(MARKER.encode())

    def _get(self, *paths):
        # The media view exists only with SERVE_MEDIA on, and is added
        # when the URL module loads (core/tests/test_access_fixes_nov.py).
        import importlib

        from django.urls import clear_url_caches

        import horse_management.urls as urls
        with override_settings(SERVE_MEDIA=True, DEBUG=False, MEDIA_ROOT=self.media):
            importlib.reload(urls)
            clear_url_caches()
            try:
                return [self.client.get(path).status_code for path in paths]
            finally:
                importlib.reload(urls)
                clear_url_caches()

    def test_a_superuser_cannot_fetch_a_clinical_file(self):
        self.client.force_login(User.objects.create_superuser('root', 'root@example.com', 'pw'))
        self.assertEqual(
            self._get('/media/cvet/photo/x.jpg', '/media/horses/../cvet/photo/x.jpg', '/media/horses/x.jpg'),
            [404, 404, 200],
        )

    def test_an_administrator_cannot_fetch_a_clinical_file(self):
        self.client.force_login(make_admin())
        self.assertEqual(self._get('/media/cvet/photo/x.jpg'), [404])


class ScopeTests(YardFixture):
    """A practice reads its own notes, and only those."""

    def test_a_practice_reads_its_own_notes(self):
        self.assertEqual(list(access.notes_for(self.practice)), [self.note])

    def test_another_practice_reads_none_of_them(self):
        other = make_practice('Other Vets')
        make_note(other, self.horse, findings='Theirs.')
        self.assertEqual(list(access.notes_for(self.practice)), [self.note])
        self.assertEqual([n.findings for n in access.notes_for(other)], ['Theirs.'])

    def test_visits_and_files_are_scoped_the_same_way(self):
        visit = Visit.objects.create(practice=self.practice)
        other = make_practice('Other Vets')
        Visit.objects.create(practice=other)
        self.assertEqual(list(access.visits_for(self.practice)), [visit])
        self.assertFalse(access.visits_for(None).exists())
        self.assertFalse(access.files_for(None).exists())
        self.assertFalse(access.files_for(other).exists())

    def test_no_practice_reads_no_notes(self):
        self.assertFalse(access.notes_for(None).exists())
        self.assertFalse(access.notes_for(access.practice_for(make_admin())).exists())

    def test_practice_for_needs_the_role_and_the_membership(self):
        self.assertEqual(access.practice_for(self.vet), self.practice)
        # Membership with no role: a login whose Vet role was taken away.
        former = make_user_with_access('former', health='full', dashboard='full')
        PracticeMember.objects.create(practice=self.practice, user=former)
        self.assertIsNone(access.practice_for(former))
        # Role with no membership.
        from core.roles_testutils import assign_role
        fresh = assign_role(User.objects.create_user('fresh', password='pw'), vet_role())
        self.assertIsNone(access.practice_for(fresh))
        self.assertIsNone(access.practice_for(make_admin('boss')))

    def test_horses_follow_the_link_to_the_yard(self):
        self.assertEqual(list(access.horses_for(self.practice)), [self.horse])
        unlinked = make_practice('Far Vets', linked=False)
        self.assertFalse(access.horses_for(unlinked).exists())
        PracticeYardLink.objects.filter(practice=self.practice).update(ended_on=timezone.localdate())
        self.practice.refresh_from_db()
        self.assertFalse(access.horses_for(self.practice).exists())

    def test_a_link_that_starts_tomorrow_is_not_open(self):
        later = make_practice('Later Vets', linked=False)
        PracticeYardLink.objects.create(
            practice=later, started_on=timezone.localdate() + timezone.timedelta(days=1),
        )
        self.assertIsNone(access.open_link(later))


class NoteRecordTests(YardFixture):
    """The note stays whole whatever the yard does to the horse."""

    def test_deleting_the_horse_keeps_the_note_and_its_name(self):
        Placement.objects.all().delete()
        self.horse.delete()
        note = ClinicalNote.objects.get(pk=self.note.pk)
        self.assertIsNone(note.horse)
        self.assertEqual(note.horse_name, 'Bramble')
        self.assertEqual(note.horse_microchip, '985100012345678')

    def test_renaming_the_horse_does_not_change_the_note(self):
        Horse.objects.filter(pk=self.horse.pk).update(name='Renamed')
        self.assertEqual(ClinicalNote.objects.get(pk=self.note.pk).horse_name, 'Bramble')

    def test_a_practice_with_notes_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.practice.delete()

    def test_a_change_keeps_the_text_before_it(self):
        versions = list(self.note.history.order_by('history_date').values_list('treatment', flat=True))
        self.assertEqual(len(versions), 2)
        self.assertNotIn('Recheck.', versions[0])
        self.assertIn('Recheck.', versions[1])

    def test_the_name_of_a_note_holds_no_clinical_text(self):
        # str() reaches logs and error pages.
        self.assertNotIn('heat', str(self.note))
        self.assertNotIn(MARKER, repr(self.note))


@override_settings(CVET_ENABLED=True)
class VetFenceTests(TestCase):
    """A practice login stays on C-vet."""

    def setUp(self):
        self.vet = make_vet()
        self.client.force_login(self.vet)

    def test_the_vet_role_opens_cvet_and_nothing_else(self):
        levels = vet_role().resolved_access()
        self.assertEqual(levels.pop('cvet'), 'full')
        self.assertEqual(set(levels.values()), {'hidden'})
        self.assertEqual(set(VET_ACCESS), {feature['key'] for feature in FEATURES})

    def test_sign_in_lands_on_cvet(self):
        self.assertRedirects(self.client.get('/'), '/c-vet/', fetch_redirect_response=False)

    def test_yard_pages_send_the_vet_back(self):
        horse = Horse.objects.create(name='Bramble')
        for url in (
            '/health/', '/health/vet-visits/', f'/horses/{horse.pk}/', '/owners/',
            '/invoicing/', '/settings/', f'/{settings.ADMIN_URL}', '/media/documents/x.pdf',
            '/farrier/',
        ):
            self.assertRedirects(
                self.client.get(url), '/c-vet/', fetch_redirect_response=False, msg_prefix=url,
            )

    def test_a_post_to_a_yard_page_never_runs(self):
        response = self.client.post('/owners/add/', {'name': 'Made by the vet'})
        self.assertRedirects(response, '/c-vet/', fetch_redirect_response=False)
        self.assertFalse(Owner.objects.filter(name='Made by the vet').exists())

    def test_the_vet_can_change_the_password_and_sign_out(self):
        self.assertEqual(self.client.get('/accounts/password_change/').status_code, 200)
        self.assertEqual(self.client.post('/accounts/logout/').status_code, 302)

    def test_a_vet_is_not_taken_for_a_farrier_nor_a_farrier_for_a_vet(self):
        farrier = make_user_with_access('farrier', farrier_portal='full')
        self.assertTrue(access.is_vet_only(self.vet))
        self.assertFalse(is_provider_only(self.vet))
        self.assertTrue(is_provider_only(farrier))
        self.assertFalse(access.is_vet_only(farrier))

    def test_staff_who_also_have_cvet_keep_the_yard_pages(self):
        admin_user = make_admin()
        self.assertFalse(access.is_vet_only(admin_user))
        self.client.force_login(admin_user)
        self.assertEqual(self.client.get('/health/').status_code, 200)


class SetUpScreenTests(TestCase):
    """The admin screen that sets a practice up shows who, not what."""

    def test_a_superuser_sets_up_a_practice_in_the_admin(self):
        root = User.objects.create_superuser('root', 'root@example.com', 'pw')
        self.client.force_login(root)
        practice = make_practice()
        horse = Horse.objects.create(name='Bramble')
        make_note(practice, horse)
        base = f'/{settings.ADMIN_URL}cvet/practice/'
        for url in (base, f'{base}{practice.pk}/change/', f'{base}add/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertNotContains(response, MARKER)
        self.assertEqual(Practice.objects.count(), 1)
