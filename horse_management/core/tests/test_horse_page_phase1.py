"""Horse page, phase 1 quick fixes (docs/HORSE_PAGE_UX_REVIEW.md section 7):
B1 permission checks, A4 passport and owner, C1 headings, C2 timeline
filters, C3 worming due, C4 messages, B5 scan check, G1 training queries,
G2 header photo."""

import importlib
import io
import re
from datetime import timedelta

from django.apps import apps
from django.contrib.messages import constants
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.models import Document, Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from health.models import BreedingRecord, WormingTreatment

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


def jpeg(name='scan.jpg'):
    buf = io.BytesIO()
    Image.new('RGB', (40, 40), (200, 200, 200)).save(buf, 'JPEG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/jpeg')


class Base(TestCase):

    def setUp(self):
        self.today = timezone.localdate()
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.location = Location.objects.create(name='Top Field', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)

    def page(self, horse, user=None):
        self.client.force_login(user or make_admin())
        return self.client.get(reverse('horse_detail', args=[horse.pk]))

    def placed(self, horse):
        Placement.objects.create(horse=horse, owner=self.owner, location=self.location,
                                 rate_type=self.rate, start_date=self.today - timedelta(days=30))
        return horse


class PermissionTests(Base):
    """B1: an action shows only for a role that can use it."""

    def test_view_only_role_sees_no_ownership_condition_or_arrival_actions(self):
        horse = Horse.objects.create(name='Plain', notes='Kicks')
        body = self.page(horse, make_user_with_access('v', horses='view', health='view')).content.decode()
        self.assertNotIn(reverse('horse_ownership', args=[horse.pk]), body)
        self.assertNotIn(reverse('condition_create'), body)
        self.assertNotIn(reverse('horse_arrive', args=[horse.pk]), body)
        self.assertIn('No current placement', body)

    def test_full_role_sees_them(self):
        horse = Horse.objects.create(name='Plain', notes='Kicks')
        body = self.page(horse).content.decode()
        self.assertIn(reverse('horse_ownership', args=[horse.pk]), body)
        self.assertIn(reverse('condition_create'), body)
        self.assertIn(reverse('horse_arrive', args=[horse.pk]), body)

    def test_condition_needs_full_health_not_full_horses(self):
        horse = Horse.objects.create(name='Plain', notes='Kicks')
        user = make_user_with_access('h', horses='full', health='view')
        body = self.page(horse, user).content.decode()
        self.assertNotIn(reverse('condition_create'), body)
        self.assertIn(reverse('horse_ownership', args=[horse.pk]), body)

    def test_breeding_is_hidden_without_breeding_access(self):
        mare = Horse.objects.create(name='Starlit Rose', sex='mare')
        BreedingRecord.objects.create(mare=mare, stallion_name='Old Flame', date_covered=self.today - timedelta(days=700),
                                      status='born')
        foal = Horse.objects.create(name='Starlit Foal', dam=mare)
        user = make_user_with_access('nob', horses='view', health='view')
        response = self.page(mare, user)
        body = response.content.decode()
        self.assertNotIn('Breeding History', body)
        self.assertNotIn('Old Flame', body)
        self.assertNotIn('<h2 class="section-title">Foals</h2>', body)
        self.assertIn(foal.name, body)  # the pedigree's "Family here" line is not a breeding record
        self.assertNotIn('breeding', [key for key, _label, _n in response.context['timeline_filters']])
        # With breeding access it is all there.
        user = make_user_with_access('yesb', horses='view', health='view', breeding='view')
        body = self.page(mare, user).content.decode()
        self.assertIn('Breeding History', body)
        self.assertIn('Old Flame', body)
        self.assertIn('<h2 class="section-title">Foals</h2>', body)

    def test_preview_pane_hides_breeding_too(self):
        mare = Horse.objects.create(name='Starlit Rose', sex='mare')
        BreedingRecord.objects.create(mare=mare, stallion_name='Old Flame', date_covered=self.today - timedelta(days=700))
        self.client.force_login(make_user_with_access('nob', horses='view', health='view'))
        response = self.client.get(reverse('horse_preview', args=[mare.pk]),
                                   HTTP_HX_REQUEST='true', HTTP_HX_TARGET='horse-preview-body')
        self.assertNotIn('Old Flame', response.content.decode())


class PassportTests(Base):
    """A4: the badge, the tick box and the number agree."""

    def test_no_positive_passport_badge(self):
        horse = Horse.objects.create(name='Plain')  # has_passport is True by default
        body = self.page(horse).content.decode()
        header = body.split('<h1', 1)[1].split('</h1>', 1)[1][:1500]
        self.assertNotIn('Passport', header)
        self.assertNotIn('No passport', body)

    def test_no_passport_badge_when_unticked(self):
        horse = Horse.objects.create(name='Plain', has_passport=False)
        self.assertContains(self.page(horse), '<span class="badge-warning">No passport</span>', html=True)

    def test_a_new_number_ticks_has_passport(self):
        horse = Horse.objects.create(name='Plain', has_passport=False)
        horse.passport_number = '826002'
        horse.save()
        horse.refresh_from_db()
        self.assertTrue(horse.has_passport)
        other = Horse.objects.create(name='New', has_passport=False, passport_number='P1')
        self.assertTrue(other.has_passport)

    def test_a_number_already_on_file_keeps_the_persons_choice(self):
        horse = Horse.objects.create(name='Lost passport', passport_number='826002')
        horse.has_passport = False
        horse.save()
        horse.refresh_from_db()
        self.assertFalse(horse.has_passport)
        horse.passport_issuer = 'Weatherbys'
        horse.save(update_fields=['passport_issuer'])
        horse.refresh_from_db()
        self.assertFalse(horse.has_passport)

    def test_a_passport_document_ticks_it_and_says_so(self):
        horse = Horse.objects.create(name='Foal', has_passport=False)
        self.client.force_login(make_admin())
        response = self.client.post(reverse('document_create') + f'?horse={horse.pk}', {
            'doc_type': 'passport', 'title': '', 'file': jpeg(),
        }, follow=True)
        horse.refresh_from_db()
        self.assertTrue(horse.has_passport)
        self.assertContains(response, 'now shows as having a passport')

    def test_a_deleted_or_other_document_does_not(self):
        horse = Horse.objects.create(name='Foal', has_passport=False)
        Document.objects.create(horse=horse, doc_type='insurance', file=ContentFile(b'x', 'a.pdf'))
        horse.refresh_from_db()
        self.assertFalse(horse.has_passport)

    def test_the_migration_fixes_old_rows(self):
        a = Horse.objects.create(name='Number', passport_number='P1')
        b = Horse.objects.create(name='Scan')
        c = Horse.objects.create(name='Nothing')
        Horse.objects.filter(pk__in=[a.pk, b.pk, c.pk]).update(has_passport=False)
        Document.objects.filter(horse=b).delete()
        doc = Document(horse=b, doc_type='passport', file=ContentFile(b'x', 'p.pdf'))
        Document.objects.bulk_create([doc])  # no save(): as in old data
        module = importlib.import_module('core.migrations.0069_passport_flag_follows_number')
        module.tick_where_known(apps, None)
        flags = dict(Horse.objects.filter(pk__in=[a.pk, b.pk, c.pk]).values_list('name', 'has_passport'))
        self.assertEqual(flags, {'Number': True, 'Scan': True, 'Nothing': False})

    def test_quick_edit_puts_the_tick_box_with_the_number(self):
        horse = Horse.objects.create(name='Plain')
        self.client.force_login(make_admin())
        body = self.client.get(reverse('horse_quick_edit', args=[horse.pk]), **POPUP).content.decode()
        self.assertLess(body.index('name="passport_issuer"'), body.index('name="has_passport"'))
        self.assertLess(body.index('name="has_passport"'), body.index('name="country_of_birth"'))


class OwnerTests(Base):
    """A4: a name from the stay is labelled as such."""

    def test_owner_from_the_stay_says_billed_to(self):
        horse = self.placed(Horse.objects.create(name='Plain'))
        body = self.page(horse).content.decode()
        line = body.split('data-testid="header-owner"', 1)[1].split('</p>', 1)[0]
        self.assertIn('Billed to', line)
        self.assertIn('(from the stay)', line)

    def test_owner_of_record_has_no_label(self):
        horse = self.placed(Horse.objects.create(name='Plain'))
        OwnershipShare.objects.create(horse=horse, owner=self.owner, share_percentage=100)
        body = self.page(horse).content.decode()
        line = body.split('data-testid="header-owner"', 1)[1].split('</p>', 1)[0]
        self.assertNotIn('Billed to', line)
        self.assertIn('Jo Bloggs', line)


class HeadingTests(Base):
    """C1: h1, then h2 for cards, then h3."""

    def test_no_skipped_levels(self):
        mare = Horse.objects.create(name='Starlit Rose', sex='mare', notes='Kicks')
        BreedingRecord.objects.create(mare=mare, stallion_name='Old Flame',
                                      date_covered=self.today - timedelta(days=100))
        body = self.page(mare).content.decode()
        main = body.split('<main', 1)[1].split('</main>', 1)[0]
        levels = [int(n) for n in re.findall(r'<h([1-6])[\s>]', main)]
        self.assertEqual(levels[0], 1)
        for before, after in zip(levels, levels[1:]):
            self.assertLessEqual(after, before + 1, levels)
        self.assertNotIn('<h3 class="section-title', main)

    def test_documents_card_keeps_h3_on_the_owner_page(self):
        self.client.force_login(make_admin())
        body = self.client.get(reverse('owner_detail', args=[self.owner.pk])).content.decode()
        self.assertIn('<h3 class="section-title">Documents</h3>', body)


class TimelineTests(Base):
    """C2 and C3: filters for the kinds there are, state for screen readers,
    and worming due in words."""

    def test_filters_list_only_kinds_with_entries_and_counts(self):
        horse = self.placed(Horse.objects.create(name='Plain'))
        WormingTreatment.objects.create(horse=horse, date=self.today - timedelta(days=10), product_name='Equest')
        response = self.page(horse)
        self.assertEqual(response.context['timeline_filters'], [('placement', 'Placements', 1), ('worming', 'Worming', 1)])
        body = response.content.decode()
        self.assertIn('aria-pressed="true"', body)
        self.assertIn(":aria-pressed=\"(activeFilter === 'worming').toString()\"", body)
        self.assertNotIn('Vaccinations', body.split('data-testid="timeline-filters"', 1)[1].split('</div>', 1)[0])
        self.assertNotIn('#2E86AB', body)

    def test_a_stable_stay_filters_as_a_placement(self):
        from core.views.horses import timeline_filters
        events = [{'type': 'stable'}, {'type': 'egg_count'}, {'type': 'farrier_note'}, {'type': 'vet_visit'}]
        pills, overdue = timeline_filters(events)
        self.assertEqual([e['filter'] for e in events], ['placement', 'worming', 'farrier', 'vet_visit'])
        self.assertEqual([p[0] for p in pills], ['placement', 'farrier', 'vet_visit', 'worming'])
        self.assertFalse(overdue)

    def test_worming_shows_next_due_and_overdue_in_words(self):
        horse = Horse.objects.create(name='Plain')
        WormingTreatment.objects.create(horse=horse, date=self.today - timedelta(days=100), product_name='Equest',
                                        next_due_date=self.today - timedelta(days=5))
        response = self.page(horse)
        body = response.content.decode()
        line = body.split('data-testid="worming-line"', 1)[1].split('</p>', 1)[0]
        self.assertIn('Next due: ', line)
        self.assertIn('Overdue', line)
        self.assertIn('<span class="sr-only">Overdue</span>', body)
        self.assertTrue(response.context['timeline_overdue'])
        self.assertIn('A rust dot marks', body)

    def test_no_overdue_legend_when_nothing_is_overdue(self):
        horse = Horse.objects.create(name='Plain')
        WormingTreatment.objects.create(horse=horse, date=self.today, product_name='Equest',
                                        next_due_date=self.today + timedelta(days=50))
        self.assertNotContains(self.page(horse), 'A rust dot marks')


class MessageTests(Base):
    """C4: messages are announced; they pause; Undo and errors stay."""

    def render_with(self, level, text, tags=''):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.template.loader import render_to_string
        from django.test import RequestFactory
        request = RequestFactory().get('/')
        request.user = make_admin()
        request.session = self.client.session
        storage = FallbackStorage(request)
        storage.add(level, text, extra_tags=tags)
        request._messages = storage
        return render_to_string('base.html', {'messages': storage}, request=request)

    def test_container_is_a_live_region(self):
        html = self.render_with(constants.SUCCESS, 'Saved Rosie.')
        self.assertIn('id="toast-container" role="status" aria-live="polite"', html)
        toast = html.split('id="toast-container"', 1)[1].split('<main', 1)[0]
        self.assertIn('@mouseenter="hold = true"', toast)
        self.assertIn('toast-countdown', toast)

    def test_error_is_an_alert_and_stays(self):
        toast = self.render_with(constants.ERROR, 'Not saved.').split('id="toast-container"', 1)[1].split('<main', 1)[0]
        self.assertIn('role="alert"', toast)
        self.assertNotIn('setInterval', toast)

    def test_undo_message_stays_until_closed(self):
        toast = self.render_with(constants.SUCCESS, 'Saved.', 'undo:/x/undo/').split(
            'id="toast-container"', 1)[1].split('<main', 1)[0]
        self.assertIn('Undo', toast)
        self.assertNotIn('setInterval', toast)
        self.assertNotIn('toast-countdown', toast)


class ScanCheckTests(Base):
    """B5: a box to scan again into; Check again is the main button."""

    def setUp(self):
        super().setUp()
        self.horse = Horse.objects.create(name='Rosie', microchip='372141000123456')
        self.url = reverse('horse_scan_check', args=[self.horse.pk])
        self.client.force_login(make_admin())

    def test_mismatch_shows_a_box_and_check_again(self):
        body = self.client.post(self.url, {'scanned': '372141000123457'}, **POPUP).content.decode()
        self.assertIn('name="scanned"', body.split('data-testid="scan-mismatch"', 1)[1])
        self.assertIn('name="last_scanned" value="372141000123457"', body)
        self.assertIn('>Check again</button>', body)
        self.assertIn('class="btn-secondary" formnovalidate>Save the scanned number', body)
        self.assertLess(body.index('>Check again<'), body.index('>Save the scanned number<'))

    def test_scanning_again_with_a_match_stamps_the_record(self):
        response = self.client.post(self.url, {'scanned': '372141000123456', 'last_scanned': '372141000123457',
                                               'action': 'check'}, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.horse.refresh_from_db()
        self.assertIsNotNone(self.horse.identity_checked_at)
        self.assertEqual(self.horse.microchip, '372141000123456')

    def test_save_uses_the_scanned_number_not_the_box(self):
        response = self.client.post(self.url, {'scanned': '', 'last_scanned': '372141000123457',
                                               'action': 'save'}, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.horse.refresh_from_db()
        self.assertEqual(self.horse.microchip, '372141000123457')


@override_settings(TRAINING_ENABLED=True)
class SpeedTests(Base):
    """G1 and G2."""

    def test_training_rows_do_not_add_queries(self):
        from training.models import Activity, Rider, Venue, WorkType
        horse = Horse.objects.create(name='Ballybrook')
        work = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        user = make_user_with_access('t', training='full', horses='view', health='view')

        def count():
            self.client.force_login(user)
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse('horse_detail', args=[horse.pk]))
            return len(ctx)

        def add(i):
            Activity.objects.create(horse=horse, date=self.today - timedelta(days=i), work_type=work,
                                    status='done', rider=Rider.objects.create(name=f'Rider {i}'),
                                    venue=Venue.objects.create(name=f'Gallop {i}'))
        add(1)
        count()  # the first load creates the yard settings row once
        one = count()
        for i in range(2, 6):
            add(i)
        self.assertEqual(count(), one)

    def test_header_photo_loads_first(self):
        horse = Horse.objects.create(name='Photo')
        horse.photo.save('p.jpg', ContentFile(jpeg().read()), save=True)
        body = self.page(horse).content.decode()
        self.assertIn('loading="eager" fetchpriority="high"', body)


class MoreMenuFocusTests(TestCase):
    """C6: the More menu items keep a visible focus ring."""

    def test_css_rule(self):
        from pathlib import Path

        from django.conf import settings
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'input.css').read_text()
        rule = next(line for line in css.splitlines() if line.strip().startswith('.more-menu-item {'))
        self.assertIn('focus-visible:ring-2', rule)
        self.assertIn('focus-visible:ring-forest', rule)

