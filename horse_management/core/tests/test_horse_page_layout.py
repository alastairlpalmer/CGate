"""Horse page layout of 21 Nov 2026: a deeper banner with the photo, the
current placement and the pedigree tree; one Ownership and identity band
under it; the Timeline next; the Training card only once the horse has a
training record."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin
from health.models import BreedingRecord
from training.models import Activity, WorkType


def main(response):
    return response.content.decode().split('<main', 1)[1].split('</main>', 1)[0]


class Base(TestCase):

    def setUp(self):
        self.today = timezone.localdate()
        self.client.force_login(make_admin())
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.field = Location.objects.create(name='Top Field', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)
        self.horse = Horse.objects.create(name='Belina', sex='mare')

    def place(self):
        Placement.objects.create(horse=self.horse, owner=self.owner, location=self.field,
                                 rate_type=self.rate, start_date=self.today - timedelta(days=30))

    def page(self):
        return self.client.get(reverse('horse_detail', args=[self.horse.pk]))

    @staticmethod
    def banner(body):
        return body.split('data-testid="horse-hero"', 1)[1].split('<!-- Grid Layout -->', 1)[0]


class BannerTests(Base):

    def test_photo_placement_and_tree_are_in_the_banner(self):
        self.place()
        body = main(self.page())
        banner = self.banner(body)
        self.assertIn('horse-hero-img', banner)
        self.assertIn('data-testid="hero-placement"', banner)
        self.assertIn('Top Field', banner)
        self.assertIn('£10.00/day', banner)
        self.assertIn('id="pedigree"', banner)
        self.assertIn('class="horse-hero-ped', banner)
        # Each shows once on the page.
        self.assertEqual(body.count('Current placement'), 1)
        self.assertEqual(body.count('id="pedigree"'), 1)

    def test_no_stay_shows_the_empty_state_in_the_banner(self):
        banner = self.banner(main(self.page()))
        self.assertIn('data-testid="placement-empty"', banner)
        self.assertIn('No current placement', banner)
        self.assertIn('Log arrival', banner)


class FactsTests(Base):
    """24 Nov 2026: ownership and the key identity facts in the banner,
    under the buttons; no separate Ownership and identity band; the rest
    in the identity pop-up ("All details")."""

    def facts(self):
        return main(self.page()).split('data-testid="hero-facts"', 1)[1].split('</section>', 1)[0]

    def test_no_band_and_the_timeline_comes_first(self):
        self.place()
        body = main(self.page())
        self.assertNotIn('data-testid="ownership-identity"', body)
        self.assertNotIn('<h2 class="section-title">Ownership</h2>', body)
        after = body.split('<!-- Grid Layout -->', 1)[1]
        first_card = after.split('<div class="card', 2)[1]
        self.assertIn('>Timeline</h2>', first_card)

    def test_facts_sit_under_the_buttons(self):
        self.place()
        side = main(self.page()).split('class="horse-hero-side"', 1)[1].split('data-testid="hero-placement"', 1)[0]
        self.assertLess(side.index('class="horse-hero-actions'), side.index('data-testid="hero-facts"'))

    def test_one_owner_is_named_once(self):
        self.place()
        OwnershipShare.objects.create(horse=self.horse, owner=self.owner, share_percentage=100)
        self.assertNotIn('data-testid="facts-owners"', self.facts())
        self.assertIn('data-testid="header-owner"', main(self.page()))

    def test_two_owners_with_their_shares(self):
        other = Owner.objects.create(name='Sam Hine')
        OwnershipShare.objects.create(horse=self.horse, owner=self.owner, share_percentage=60, is_primary_contact=True)
        OwnershipShare.objects.create(horse=self.horse, owner=other, share_percentage=40)
        facts = self.facts()
        self.assertIn('data-testid="facts-owners"', facts)
        self.assertIn('Jo Bloggs', facts)
        self.assertIn('60%', facts)
        self.assertIn('Sam Hine', facts)
        self.assertIn('40%', facts)

    def test_numbers_missing_line_and_all_details(self):
        Horse.objects.filter(pk=self.horse.pk).update(microchip='826098000123456', breed='Thoroughbred')
        facts = self.facts()
        self.assertIn('826 098 000 123 456', facts)
        self.assertIn('data-copy="826098000123456"', facts)
        self.assertIn('Thoroughbred', facts)
        self.assertIn('Missing: UELN, passport number, foaling date.', facts)
        self.assertIn(reverse('horse_quick_edit', args=[self.horse.pk]), facts)
        self.assertIn(reverse('horse_identity', args=[self.horse.pk]), facts)
        self.assertIn(reverse('horse_scan_check', args=[self.horse.pk]), facts)

    def test_a_duplicate_chip_is_warned_in_the_banner(self):
        Horse.objects.filter(pk=self.horse.pk).update(microchip='826098000123456')
        Horse.objects.create(name='Twin', microchip='826098000123456', is_active=False)
        self.assertIn('(departed). Is this the same horse?', self.facts())


class IdentityPopupTests(Base):

    def test_popup_and_page(self):
        Horse.objects.filter(pk=self.horse.pk).update(markings='Star', breeder='Moyglare Stud')
        url = reverse('horse_identity', args=[self.horse.pk])
        popup = self.client.get(url, HTTP_HX_REQUEST='true', HTTP_HX_TARGET='popup-body').content.decode()
        self.assertIn('data-testid="identity-details"', popup)
        self.assertIn('Moyglare Stud', popup)
        self.assertNotIn('<html', popup)
        self.assertIn(reverse('horse_identifier_add', args=[self.horse.pk]), popup)
        page = self.client.get(url)
        self.assertContains(page, 'Identity of Belina')
        self.assertContains(page, 'Moyglare Stud')

    def test_view_only(self):
        from core.roles_testutils import make_viewer
        self.client.force_login(make_viewer())
        popup = self.client.get(reverse('horse_identity', args=[self.horse.pk]),
                                HTTP_HX_REQUEST='true', HTTP_HX_TARGET='popup-body').content.decode()
        self.assertNotIn(reverse('horse_identifier_add', args=[self.horse.pk]), popup)
        self.assertNotIn(reverse('horse_quick_edit', args=[self.horse.pk]), popup)


class TrainingCardTests(Base):

    def setUp(self):
        super().setUp()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)

    def shown(self):
        response = self.page()
        self.assertIsNotNone(response.context['training_card'])
        return response.context['show_training_card'], 'id="training"' in main(response)

    def test_no_records_no_card(self):
        self.assertEqual(self.shown(), (False, False))

    def test_no_records_the_card_is_not_built(self):
        # The page then keeps one answer: may this role change training
        # (the Record card and the Timeline ask). The card itself costs
        # eight queries (docs/MOBILE_APP_PLAN.md, item B6).
        self.assertEqual(set(self.page().context['training_card']), {'can_change'})

    def test_a_horse_with_no_training_record_costs_fewer_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.page()  # the first load fills caches that both counts share
        with CaptureQueriesContext(connection) as without:
            self.page()
        Activity.objects.create(horse=self.horse, date=self.today - timedelta(days=3),
                                work_type=self.canter, status='done')
        with CaptureQueriesContext(connection) as with_card:
            self.page()
        self.assertGreaterEqual(len(with_card) - len(without), 6)

    def test_planned_work_alone_is_not_a_record(self):
        Activity.objects.create(horse=self.horse, date=self.today + timedelta(days=2),
                                work_type=self.canter, status='planned')
        self.assertEqual(self.shown(), (False, False))

    def test_a_done_record_shows_the_card(self):
        Activity.objects.create(horse=self.horse, date=self.today - timedelta(days=3),
                                work_type=self.canter, status='done')
        self.assertEqual(self.shown(), (True, True))

    def test_did_not_work_counts_too(self):
        Activity.objects.create(horse=self.horse, date=self.today - timedelta(days=1),
                                work_type=self.canter, status='not_done', reason='vet')
        self.assertEqual(self.shown(), (True, True))


class BannerAreasTests(Base):
    """23 Nov 2026: the tree sits between the name and the buttons on a wide
    screen, so the buttons have their own area and the page horse's own
    box is left out of the banner's tree."""

    def test_buttons_have_their_own_area(self):
        self.place()
        banner = self.banner(main(self.page()))
        head = banner.split('class="horse-hero-head"', 1)[1].split('class="horse-hero-actions', 1)[0]
        self.assertIn('<h1', head)
        self.assertNotIn('Move', head)
        actions = banner.split('class="horse-hero-actions', 1)[1].split('data-testid="hero-placement"', 1)[0]
        self.assertIn('Move', actions)
        self.assertIn('class="ped is-page-horse"', banner)


class PhoneTablesTests(Base):
    """On a phone, Breeding history and Foals stack into labelled cards
    (data-table-cards), so Actions is no longer off the screen."""

    def test_both_tables_use_cards(self):
        foal = Horse.objects.create(name='Little', sex='colt', dam=self.horse,
                                    date_of_birth=self.today - timedelta(days=60))
        BreedingRecord.objects.create(mare=self.horse, stallion_name='Hip Hop', foal=foal, status='born',
                                      date_covered=self.today - timedelta(days=400),
                                      foal_dob=self.today - timedelta(days=60))
        body = main(self.page())
        history = body.split('<!-- Breeding history -->', 1)[1].split('<!-- Foals -->', 1)[0]
        foals = body.split('<!-- Foals -->', 1)[1].split('<!-- History:', 1)[0]
        for table, labels in ((history, ('Season', 'Stallion', 'Coverings', 'Scans', 'Outcome', 'Foal')),
                              (foals, ('Name', 'Foaled', 'Age', 'Sire', 'Status'))):
            self.assertIn('class="data-table data-table-cards"', table)
            for label in labels:
                self.assertIn(f'data-label="{label}"', table)
