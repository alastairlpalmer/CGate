"""Horse page, phase 5 (docs/HORSE_PAGE_UX_REVIEW.md section 7): A3 one place
for each fact, C7 contrast, C10 small accessibility items, D2 no Active box
in the quick edit, D3 the shared error box, F1 to F8 consistency."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin
from health.models import BreedingRecord, MedicalCondition, VetVisit, WormEggCount

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


def main(response):
    return response.content.decode().split('<main', 1)[1].split('</main>', 1)[0]


class Base(TestCase):

    def setUp(self):
        self.today = timezone.localdate()
        self.client.force_login(make_admin())
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.field = Location.objects.create(name='Top Field', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)

    def horse(self, name='Avicii', placed=True, days=30, **kw):
        horse = Horse.objects.create(name=name, **kw)
        if placed:
            Placement.objects.create(horse=horse, owner=self.owner, location=self.field,
                                     rate_type=self.rate, start_date=self.today - timedelta(days=days))
        return horse

    def page(self, horse):
        return self.client.get(reverse('horse_detail', args=[horse.pk]))


class OnePlaceTests(Base):
    """A3: the header keeps the summary; each fact has one place."""

    def test_header_summary(self):
        horse = self.horse(registered_name='Karma Avicii', country_of_birth='GB', sex='gelding',
                           color='bay', date_of_birth=self.today - timedelta(days=7 * 365))
        body = main(self.page(horse))
        header = body.split('<h1', 1)[1].split('id="quick-actions"', 1)[0].split('Current placement', 1)[0]
        self.assertNotIn('Registered as', header)
        self.assertNotIn('born ', header)
        self.assertIn('Gelding', header)
        # The registered name is in the identity pop-up ("All details").
        details = self.client.get(reverse('horse_identity', args=[horse.pk]), **POPUP).content.decode()
        self.assertIn('Karma Avicii (GB)', details)

    def test_lineage_notes_label(self):
        body = main(self.page(self.horse(breeding='By Galileo\nout of Urban Sea')))
        self.assertIn('Lineage notes', body)
        self.assertIn('By Galileo<br>out of Urban Sea', body)

    def test_no_training_or_breeding_twice(self):
        mare = self.horse('Clover', sex='mare')
        BreedingRecord.objects.create(mare=mare, stallion_name='Hip Hop',
                                      date_covered=self.today - timedelta(days=16), status='covered')
        response = self.page(mare)
        kinds = {e['type'] for e in response.context['timeline_events']}
        self.assertNotIn('breeding', kinds)
        self.assertNotIn('training', kinds)

    def test_foal_record_has_no_sex_or_chip_column(self):
        mare = self.horse('Dam', sex='mare')
        foal = self.horse('Little', sex='colt', microchip='826098000123456',
                          date_of_birth=self.today - timedelta(days=60))
        BreedingRecord.objects.create(mare=mare, stallion_name='Hip Hop', foal=foal, status='born',
                                      date_covered=self.today - timedelta(days=400),
                                      foal_dob=self.today - timedelta(days=60), foal_microchip='826098000123456')
        card = main(self.page(foal)).split('id="foal-record"', 1)[1].split('First year', 1)[0]
        self.assertNotIn('Sex &amp; colour', card)
        self.assertNotIn('Chip at birth', card)  # the same chip as the horse's: shown once


class AccessibilityTests(Base):
    """C7 and C10."""

    def test_phone_row_is_a_group_and_edit_labels_read_well(self):
        horse = self.horse()
        VetVisit.objects.create(horse=horse, date=self.today - timedelta(days=3), reason='Check')
        body = main(self.page(horse))
        self.assertIn('<div class="qa-strip lg:hidden" role="group"', body)
        self.assertNotIn('<nav class="qa-strip', body)
        self.assertIn('aria-label="Edit vet visit record, ', body)
        self.assertNotIn('vetvisit', body)

    def test_help_text_is_linked_to_its_box(self):
        horse = self.horse()
        body = self.client.get(reverse('horse_quick_edit', args=[horse.pk]), **POPUP).content.decode()
        for name in ('date_of_birth', 'foaled_year', 'ueln', 'height'):
            box = body.split(f'name="{name}"', 1)[1].split('>', 1)[0]
            self.assertIn(f'aria-describedby="id_{name}_helptext"', box)
            self.assertIn(f'id="id_{name}_helptext"', body)

    def test_copy_says_copied_to_screen_readers(self):
        body = main(self.page(self.horse(microchip='826098000123456')))
        self.assertIn('<span class="sr-only" role="status" x-text="done"></span>', body)

    def test_photo_label_contrast(self):
        from core.tests.test_horse_page_phase3 import _photo  # noqa: F401  (same helper)
        self.assertIn('bg-charcoal/80', open('templates/horses/_photo_grid_card.html').read())


class QuickEditTests(Base):
    """D2 and D3."""

    def test_no_active_box_and_shared_error_box(self):
        horse = self.horse()
        body = self.client.get(reverse('horse_quick_edit', args=[horse.pk]), **POPUP).content.decode()
        self.assertNotIn('name="is_active"', body)
        self.assertIn('Save changes', body)
        response = self.client.post(reverse('horse_identifier_add', args=[horse.pk]),
                                    {'kind': 'other', 'value': '', 'url': ''}, **POPUP)
        self.assertContains(response, '<div class="alert-error" role="alert">')


class ConsistencyTests(Base):
    """F1 to F8."""

    def test_sentence_case_and_no_capital_labels(self):
        body = main(self.page(self.horse()))
        self.assertIn('Current placement', body)
        self.assertNotIn('Current Placement', body)
        self.assertNotIn('uppercase tracking-wider', body)

    def test_money_and_stay_length(self):
        horse = self.horse(days=10)
        body = main(self.page(horse))
        self.assertIn('£10.00/day', body)
        self.assertIn('1 week 3 days on site', body)
        self.assertNotIn('&pound;', body)

    def test_attention_not_error_colours(self):
        horse = self.horse()
        MedicalCondition.objects.create(horse=horse, name='Sweet itch', status='active')
        WormEggCount.objects.create(horse=horse, date=self.today - timedelta(days=2), count=1500)
        body = main(self.page(horse))
        self.assertIn('<span class="badge-warning">Active</span>', body)
        self.assertNotIn('badge-danger">Active', body)
        timeline = body.split('data-testid="timeline-list"', 1)[1]
        self.assertIn('text-saddle', timeline.split('1500 EPG', 1)[0][-200:])
        self.assertIn('<span class="badge-neutral">Lost</span>',
                      open('templates/health/partials/breeding_status_badge.html').read())

    def test_sprite_icons(self):
        body = main(self.page(self.horse()))
        self.assertIn('<use href="#i-gate"/>', body)
        self.assertIn('<use href="#i-pin"/>', body)

    def test_wording(self):
        horse = self.horse()
        Horse.objects.filter(pk=horse.pk).update(is_active=False)
        body = main(self.page(horse))
        self.assertIn('select <strong>More</strong>, then <strong>Depart…</strong>', body)
        mare = self.horse('Clover', sex='mare')
        BreedingRecord.objects.create(mare=mare, stallion_name='Hip Hop', status='barren',
                                      date_covered=self.today - timedelta(days=200))
        body = main(self.page(mare))
        self.assertIn('14-day:', body)
        self.assertIn('Heartbeat:', body)
        self.assertNotIn('14d:', body)

    def test_empty_states(self):
        horse = self.horse(placed=False)
        body = main(self.page(horse))
        placement = body.split('data-testid="placement-empty"', 1)[1][:1500]
        self.assertIn('class="empty-state empty-state-compact"', placement)
        self.assertIn('No current placement', placement)
        self.assertIn('data-testid="facts-owners"', body)  # no owners: the banner says so
        self.assertIn('Set owners', body)
        self.assertIn('Manage ownership', body)
        self.assertIn('Nothing recorded yet.', body)
        self.assertIn(f'hx-get="{reverse("horse_record_actions", args=[horse.pk])}"', body)
        # One rust button: the empty states use secondary buttons.
        self.assertEqual(body.count('btn-primary'), 1)

    def test_owner_card_with_shares_is_unchanged(self):
        horse = self.horse()
        OwnershipShare.objects.create(horse=horse, owner=self.owner, share_percentage=100)
        self.assertNotIn('No owners set', main(self.page(horse)))
