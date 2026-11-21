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
        return body.split('data-testid="horse-hero"', 1)[1].split('data-testid="ownership-identity"', 1)[0]


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


class OwnershipAndIdentityTests(Base):

    def test_one_band_under_the_banner_then_the_timeline(self):
        self.place()
        OwnershipShare.objects.create(horse=self.horse, owner=self.owner, share_percentage=100)
        body = main(self.page())
        band = body.split('data-testid="ownership-identity"', 1)[1].split('<!-- Grid Layout -->', 1)[0]
        self.assertIn('Ownership and identity', band)
        self.assertIn('id="ownership"', band)
        self.assertIn('Jo Bloggs', band)
        self.assertIn('100%', band)
        self.assertIn('>Identity</h3>', band)
        # The old Ownership card in the side column is gone.
        self.assertNotIn('<h2 class="section-title">Ownership</h2>', body)
        # The Timeline is the first card after the band.
        after = body.split('<!-- Grid Layout -->', 1)[1]
        first_card = after.split('<div class="card', 2)[1]
        self.assertIn('>Timeline</h2>', first_card)
        self.assertLess(after.index('>Timeline</h2>'), after.index('Breeding</h2>'))


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
