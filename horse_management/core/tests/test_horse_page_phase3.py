"""Horse page, phase 3 (docs/HORSE_PAGE_UX_REVIEW.md section 7): B2 one rust
button, B3 one name for each task, B4 Undo in place of confirm, B6 condition
edit and fold, B7 the Charges card, B8 the photo actions menu."""

import io
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import Horse, HorseIdentifier, HorsePhoto, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from health.models import BreedingRecord, Covering, MedicalCondition, PregnancyScan

TEMP_MEDIA = tempfile.mkdtemp(prefix='cgate-phase3-tests-')


def _photo(name='snap.jpg'):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (64, 64), 'red').save(buf, 'JPEG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/jpeg')


def _main(response):
    body = response.content.decode()
    return body.split('<main', 1)[1].split('</main>', 1)[0]


def _undo_urls(response):
    from core.views._undo import undo_url_from_tags
    return [undo_url_from_tags(m.extra_tags) for m in get_messages(response.wsgi_request)]


class Base(TestCase):

    def setUp(self):
        self.today = timezone.localdate()
        self.admin = make_admin()
        self.client.force_login(self.admin)
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.location = Location.objects.create(name='Top Field', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)

    def horse(self, name='Avicii', placed=True, **kw):
        horse = Horse.objects.create(name=name, **kw)
        if placed:
            Placement.objects.create(horse=horse, owner=self.owner, location=self.location,
                                     rate_type=self.rate, start_date=self.today - timedelta(days=30))
        return horse

    def page(self, horse, user=None):
        if user is not None:
            self.client.force_login(user)
        return self.client.get(reverse('horse_detail', args=[horse.pk]))


class OnePrimaryButtonTests(Base):
    """B2: rust only for the header's Move or Log Arrival."""

    def test_covered_mare_with_a_scan_due(self):
        mare = self.horse('Clover', sex='mare')
        BreedingRecord.objects.create(mare=mare, stallion_name='Hip Hop',
                                      date_covered=self.today - timedelta(days=16),
                                      status=BreedingRecord.Status.COVERED)
        main = _main(self.page(mare))
        self.assertEqual(main.count('btn-primary'), 1)
        self.assertIn('Scan result</a>', main)

    def test_confirmed_mare_foaling_is_secondary(self):
        mare = self.horse('Vivian', sex='mare')
        BreedingRecord.objects.create(mare=mare, stallion_name='Hip Hop',
                                      date_covered=self.today - timedelta(days=330),
                                      status=BreedingRecord.Status.CONFIRMED)
        main = _main(self.page(mare))
        self.assertEqual(main.count('btn-primary'), 1)
        self.assertIn('btn-secondary btn-xs">Record foaling</a>', main)

    def test_foal_to_wean_and_no_ownership(self):
        mare = self.horse('Dam', sex='mare')
        foal = self.horse('Little', sex='colt', date_of_birth=self.today - timedelta(days=200))
        BreedingRecord.objects.create(mare=mare, stallion_name='Hip Hop', foal=foal,
                                      date_covered=self.today - timedelta(days=540),
                                      foal_dob=self.today - timedelta(days=200),
                                      status=BreedingRecord.Status.BORN)
        main = _main(self.page(foal))
        self.assertEqual(main.count('btn-primary'), 1)
        self.assertIn('class="btn-secondary btn-xs">Wean</a>', main)
        self.assertIn('Manage ownership', main)
        self.assertNotIn('Set Ownership', main)

    def test_horse_not_placed_has_one_log_arrival(self):
        horse = self.horse(placed=False)
        main = _main(self.page(horse))
        self.assertEqual(main.count('btn-primary'), 1)
        self.assertEqual(main.count('Log Arrival'), 1)

    def test_stranded_horse(self):
        horse = self.horse()
        Horse.objects.filter(pk=horse.pk).update(is_active=False)  # flagged departed, stay still open
        main = _main(self.page(horse))
        self.assertIn('Mark as active', main)
        self.assertEqual(main.count('btn-primary'), 1)


class NamesTests(Base):
    """B3: one verb and noun for each task."""

    def test_mare_page_names(self):
        mare = self.horse('Clover', sex='mare')
        foal = Horse.objects.create(name='Little', sex='filly', dam=mare,
                                    date_of_birth=self.today - timedelta(days=400))
        BreedingRecord.objects.create(mare=mare, stallion_name='Hip Hop', foal=foal,
                                      date_covered=self.today - timedelta(days=740),
                                      foal_dob=self.today - timedelta(days=400),
                                      status=BreedingRecord.Status.BORN)
        BreedingRecord.objects.create(mare=mare, stallion_name='Hip Hop',
                                      date_covered=self.today - timedelta(days=16),
                                      status=BreedingRecord.Status.COVERED)
        main = _main(self.page(mare))
        for name in ('Record covering', 'See all records', 'Edit foal details', 'Manage ownership'):
            self.assertIn(name, main)
        # The Record tiles keep short nouns ("Foaling") under the "Record" heading.
        for old in ('+ Covering', 'btn-xs">Foaling<', '>Details<', '>Manage<', 'All records &rarr;', '+ Condition'):
            self.assertNotIn(old, main)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class UndoTests(Base):
    """B4: Undo in place of a confirm step, where the item can come back."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def test_number_remove_and_undo(self):
        horse = self.horse()
        ident = HorseIdentifier.objects.create(horse=horse, kind='stud_book', value='GB123',
                                               issuer='Weatherbys GSB', checked=True)
        added = ident.created_at
        body = self.page(horse).content.decode()
        self.assertNotIn('x-data="{ sure: false }"', body)
        response = self.client.post(reverse('horse_identifier_delete', args=[horse.pk, ident.pk]))
        self.assertFalse(HorseIdentifier.objects.exists())
        undo = reverse('horse_identifier_restore', args=[horse.pk, ident.pk])
        self.assertIn(undo, _undo_urls(response))
        response = self.client.post(undo, {'next': reverse('horse_detail', args=[horse.pk])})
        self.assertRedirects(response, reverse('horse_detail', args=[horse.pk]), fetch_redirect_response=False)
        back = HorseIdentifier.objects.get()
        self.assertEqual((back.pk, back.value, back.checked, back.created_at), (ident.pk, 'GB123', True, added))
        # A second click is harmless.
        self.assertEqual(self.client.post(undo).status_code, 302)
        self.assertEqual(HorseIdentifier.objects.count(), 1)

    def test_number_undo_only_for_a_removed_number_of_this_horse(self):
        horse, other = self.horse(), self.horse('Other')
        ident = HorseIdentifier.objects.create(horse=horse, value='X1')
        self.client.post(reverse('horse_identifier_delete', args=[horse.pk, ident.pk]))
        self.assertEqual(self.client.post(
            reverse('horse_identifier_restore', args=[other.pk, ident.pk])).status_code, 404)
        self.assertEqual(self.client.post(
            reverse('horse_identifier_restore', args=[horse.pk, 9999])).status_code, 404)
        viewer = make_user_with_access('v', horses='view')
        self.client.force_login(viewer)
        self.client.post(reverse('horse_identifier_restore', args=[horse.pk, ident.pk]))
        self.assertFalse(HorseIdentifier.objects.exists())

    def test_photo_delete_and_undo(self):
        horse = self.horse()
        photo = HorsePhoto.objects.create(horse=horse, image=_photo())
        response = self.client.post(reverse('horse_photo_delete', args=[photo.pk]))
        undo = reverse('horse_photo_restore', args=[photo.pk])
        self.assertIn(undo, _undo_urls(response))
        self.assertNotIn(f'data-lightbox-src="{photo.image.url}"', self.page(horse).content.decode())
        # Recently Deleted lists it.
        self.assertContains(self.client.get(reverse('recently_deleted')), 'photo of Avicii')
        self.client.post(undo)
        self.assertTrue(HorsePhoto.objects.filter(pk=photo.pk).exists())
        self.assertContains(self.page(horse), f'data-lightbox-src="{photo.image.url}"')

    def test_photo_restore_needs_full_horse_access(self):
        horse = self.horse()
        photo = HorsePhoto.objects.create(horse=horse, image=_photo())
        photo.soft_delete(self.admin)
        self.client.force_login(make_user_with_access('v', horses='view'))
        self.client.post(reverse('horse_photo_restore', args=[photo.pk]))
        self.assertFalse(HorsePhoto.objects.exists())

    def test_purge_job_removes_old_photos(self):
        from core.tasks import purge_soft_deleted
        horse = self.horse()
        photo = HorsePhoto.objects.create(horse=horse, image=_photo())
        photo.soft_delete(self.admin)
        HorsePhoto.all_objects.filter(pk=photo.pk).update(deleted_at=timezone.now() - timedelta(days=400))
        counts = purge_soft_deleted()
        self.assertEqual(counts['HorsePhoto'], 1)
        self.assertFalse(HorsePhoto.all_objects.exists())

    def test_depart_has_undo(self):
        horse = self.horse()
        page = reverse('horse_detail', args=[horse.pk])
        response = self.client.post(reverse('horse_depart', args=[horse.pk]),
                                    {'departure_date': self.today.isoformat()})
        undo = reverse('cancel_departure', args=[horse.pk])
        self.assertIn(undo, _undo_urls(response))
        horse.refresh_from_db()
        self.assertFalse(horse.is_active)
        response = self.client.post(undo, {'next': page})
        self.assertRedirects(response, page, fetch_redirect_response=False)
        horse.refresh_from_db()
        self.assertTrue(horse.is_active)
        self.assertEqual(horse.placements.get().end_date, None)

    def test_scheduled_depart_has_no_undo(self):
        horse = self.horse()
        response = self.client.post(reverse('horse_depart', args=[horse.pk]),
                                    {'departure_date': (self.today + timedelta(days=5)).isoformat()})
        self.assertEqual([u for u in _undo_urls(response) if u], [])

    def test_covering_and_scan_use_the_styled_confirm(self):
        mare = self.horse('Clover', sex='mare')
        record = BreedingRecord.objects.create(mare=mare, stallion_name='Hip Hop',
                                               date_covered=self.today - timedelta(days=16),
                                               status=BreedingRecord.Status.COVERED)
        Covering.objects.create(record=record, date=self.today - timedelta(days=18))
        Covering.objects.create(record=record, date=self.today - timedelta(days=16))
        PregnancyScan.objects.create(record=record, date=self.today, scan_type='14_day', result='inconclusive')
        main = _main(self.page(mare))
        self.assertNotIn('return confirm(', main)
        self.assertIn('hx-confirm="Remove the covering on', main)
        self.assertIn('hx-confirm="Remove the 14-day scan on', main)

    def test_document_delete_has_no_confirm(self):
        from core.models import Document
        horse = self.horse()
        Document.objects.create(horse=horse, title='Insurance', doc_type='insurance',
                                file=SimpleUploadedFile('i.pdf', b'%PDF-1.4', content_type='application/pdf'))
        main = _main(self.page(horse))
        self.assertIn(reverse('document_delete', args=[horse.documents.get().pk]), main)
        self.assertNotIn('hx-confirm="Delete', main)


class ConditionTests(Base):
    """B6: Edit on each condition; open ones first; resolved ones folded."""

    def test_order_edit_and_fold(self):
        horse = self.horse()
        old = MedicalCondition.objects.create(horse=horse, name='Mud fever', status='resolved')
        itch = MedicalCondition.objects.create(horse=horse, name='Sweet itch', status='active')
        lami = MedicalCondition.objects.create(horse=horse, name='Laminitis', status='monitoring')
        main = _main(self.page(horse))
        block = main.split('data-testid="conditions"', 1)[1]
        folded = block.split('data-testid="resolved-conditions"', 1)
        self.assertIn('Sweet itch', folded[0])
        self.assertIn('Laminitis', folded[0])
        self.assertNotIn('Mud fever', folded[0])
        self.assertIn('1 resolved', folded[1])
        self.assertIn('Mud fever', folded[1])
        for c in (old, itch, lami):
            self.assertIn(reverse('condition_update', args=[c.pk]), block)

    def test_only_resolved_and_a_view_role(self):
        horse = self.horse()
        cond = MedicalCondition.objects.create(horse=horse, name='Mud fever', status='resolved')
        main = _main(self.page(horse))
        self.assertIn('No open conditions.', main)
        self.assertIn('1 resolved', main)
        user = make_user_with_access('v', horses='view', health='view')
        self.assertNotIn(reverse('condition_update', args=[cond.pk]), _main(self.page(horse, user)))

    def test_edit_in_the_sheet_says_saved(self):
        horse = self.horse()
        cond = MedicalCondition.objects.create(horse=horse, name='Sweet itch', status='active')
        response = self.client.post(reverse('condition_update', args=[cond.pk]),
                                    {'horse': horse.pk, 'name': 'Sweet itch', 'status': 'resolved'},
                                    HTTP_HX_REQUEST='true', HTTP_HX_TARGET='popup-body')
        self.assertEqual(response.status_code, 204)
        cond.refresh_from_db()
        self.assertEqual(cond.status, 'resolved')
        self.assertIn('Sweet itch updated.', [str(m) for m in get_messages(response.wsgi_request)])


class ChargesTests(Base):
    """B7: an "Other charge" shows on the page after its save."""

    def charge(self, i=0, **kw):
        return ExtraCharge.objects.create(
            horse=self.h, owner=self.owner, date=self.today - timedelta(days=i),
            description=kw.pop('description', f'Transport {i}'), amount=Decimal('45.00'), **kw)

    def setUp(self):
        super().setUp()
        self.h = self.horse()

    def test_card_lists_the_latest_charges(self):
        for i in range(7):
            self.charge(i)
        self.charge(8, description='Old invoiced', invoiced=True)
        main = _main(self.page(self.h))
        card = main.split('data-testid="charges-card"', 1)[1].split('</section>', 1)[0]
        self.assertIn('Transport 0', card)
        self.assertIn('Transport 4', card)
        self.assertNotIn('Transport 5', card)
        self.assertIn('£45.00', card)
        self.assertIn('Showing the latest 5 of 8.', card)
        self.assertIn(f"{reverse('charge_list')}?horse={self.h.pk}", card)
        self.assertIn(reverse('charge_update', args=[ExtraCharge.objects.get(description='Transport 0').pk]), card)

    def test_invoiced_charge_has_no_edit(self):
        c = self.charge(description='Billed', invoiced=True)
        card = _main(self.page(self.h)).split('data-testid="charges-card"', 1)[1]
        self.assertIn('Invoiced', card)
        self.assertNotIn(reverse('charge_update', args=[c.pk]), card)

    def test_hidden_without_money_access_and_without_charges(self):
        self.assertNotIn('data-testid="charges-card"', _main(self.page(self.h)))
        self.charge()
        user = make_user_with_access('v', horses='full')
        self.assertNotIn('data-testid="charges-card"', _main(self.page(self.h, user)))
        viewer = make_user_with_access('cv', horses='view', charges='view')
        card = _main(self.page(self.h, viewer)).split('data-testid="charges-card"', 1)[1]
        self.assertNotIn('charge_update', card)
        self.assertNotIn('/edit/', card)

    def test_a_deleted_charge_is_not_shown(self):
        c = self.charge(description='Gone')
        c.soft_delete(self.admin)
        self.assertNotIn('data-testid="charges-card"', _main(self.page(self.h)))


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class PhotoActionTests(Base):
    """B8: one actions menu per photo, the profile photo marked, and no
    default category label."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def test_menu_mark_and_labels(self):
        horse = self.horse()
        plain = HorsePhoto.objects.create(horse=horse, image=_photo('a.jpg'))
        injury = HorsePhoto.objects.create(horse=horse, image=_photo('b.jpg'), category='injury')
        self.client.post(reverse('horse_photo_set_profile', args=[plain.pk]))
        main = _main(self.page(horse))
        grid = main.split('>Photos</h2>', 1)[1]
        self.assertEqual(grid.count('data-testid="profile-mark"'), 1)
        self.assertEqual(grid.count('class="photo-menu-btn'), 2)
        # The profile photo has no "Use as profile picture"; the other one has.
        self.assertNotIn(reverse('horse_photo_set_profile', args=[plain.pk]), grid)
        self.assertIn(reverse('horse_photo_set_profile', args=[injury.pk]), grid)
        self.assertIn('>Injury</span>', grid)
        self.assertNotIn('>Condition</span>', grid)
        # Delete has Undo, so no confirm.
        self.assertIn(reverse('horse_photo_delete', args=[injury.pk]), grid)
        self.assertNotIn('hx-confirm', grid)

    def test_view_role_has_no_menu(self):
        horse = self.horse()
        HorsePhoto.objects.create(horse=horse, image=_photo())
        user = make_user_with_access('v', horses='view')
        self.assertNotIn('photo-menu-btn', _main(self.page(horse, user)))
