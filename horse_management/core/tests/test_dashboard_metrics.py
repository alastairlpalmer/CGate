"""The dashboard's top figures and its Timeline, per role.

An administrator sees every figure and every kind on the Timeline. A
role sees only the figures and Timeline kinds of the areas it can view.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.dashboard import activity, metrics
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from health.models import FarrierVisit
from invoicing.models import Invoice


class YardTestCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.admin = make_admin(username='metrics-admin')
        self.rate = RateType.objects.create(name='Grass', daily_rate=5)
        self.jo = Owner.objects.create(name='Jo')
        self.sam = Owner.objects.create(name='Sam')
        Owner.objects.create(name='Former')
        self.top = Location.objects.create(name='Top', site='Colgate', capacity=2)
        self.low = Location.objects.create(name='Low', site='Somerford', capacity=4)
        Location.objects.create(name='Rest', site='Somerford', usage=Location.Usage.RESTED)
        self.place('A', self.top, self.jo)
        self.place('B', self.top, self.jo)
        self.place('C', self.top, self.sam)
        self.horse_d = self.place('D', self.low, self.sam)

    def place(self, name, location, owner):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=owner, location=location, rate_type=self.rate,
            start_date=self.today - timedelta(days=3),
        )
        return horse


class MetricsTests(YardTestCase):
    def tiles(self, user, **kwargs):
        return {t['key']: t for t in metrics.build(user, today=self.today, **kwargs)}

    def test_an_administrator_sees_every_figure(self):
        Invoice.objects.create(
            owner=self.jo, invoice_number='INV1',
            period_start=self.today - timedelta(days=60), period_end=self.today - timedelta(days=31),
            subtotal=Decimal('120'), total=Decimal('120'),
            status=Invoice.Status.SENT, due_date=self.today - timedelta(days=1),
        )
        tiles = self.tiles(self.admin, needs_action=3)
        self.assertEqual(list(tiles), ['horses', 'owners', 'ground', 'action', 'outstanding'])
        self.assertEqual(tiles['horses']['value'], 4)
        self.assertEqual(tiles['horses']['meta'], 'Colgate 3 · Somerford 1')
        self.assertEqual(tiles['owners']['value'], 2)
        self.assertEqual(tiles['owners']['meta'], '3 on the books')
        self.assertEqual(tiles['ground']['value'], '4/6')
        self.assertEqual(tiles['ground']['meta'], '1 over capacity · 1 resting')
        self.assertEqual(tiles['ground']['tone'], 'alert')
        self.assertEqual(tiles['action']['value'], 3)
        self.assertEqual(tiles['outstanding']['value'], '£120.00')
        self.assertEqual(tiles['outstanding']['meta'], '1 overdue')

    def test_a_role_sees_only_its_areas(self):
        groom = make_user_with_access(username='groom', dashboard='full', horses='view', health='full')
        self.assertEqual(list(self.tiles(groom)), ['horses', 'action'])
        office = make_user_with_access(username='office', dashboard='full', owners='view', invoices='view')
        self.assertEqual(list(self.tiles(office)), ['owners', 'action', 'outstanding'])

    def test_a_chosen_site_narrows_the_figures(self):
        tiles = self.tiles(self.admin, site='Somerford')
        self.assertEqual(tiles['horses']['value'], 1)
        self.assertEqual(tiles['horses']['meta'], 'Somerford')
        self.assertEqual(tiles['ground']['value'], '1/4')

    def test_the_strip_is_on_the_dashboard_across_the_top(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('dashboard'))
        body = response.content
        self.assertContains(response, 'data-testid="metrics-zone"')
        self.assertContains(response, 'data-metric="horses"')
        self.assertLess(body.index(b'data-testid="metrics-zone"'), body.index(b'data-testid="main-grid"'))


class TimelineTests(YardTestCase):
    def setUp(self):
        super().setUp()
        FarrierVisit.objects.create(
            horse=self.horse_d, date=self.today - timedelta(days=1),
            next_due_date=self.today + timedelta(days=40), cost=Decimal('60'),
        )

    def groups(self, user):
        days = activity.recent(user, today=self.today)
        return [g['key'] for g in activity.groups(days)]

    def test_an_administrator_gets_every_group(self):
        self.assertEqual(self.groups(self.admin), ['yard', 'health'])

    def test_a_role_gets_only_its_groups(self):
        groom = make_user_with_access(username='groom2', dashboard='full', horses='view')
        self.assertEqual(self.groups(groom), ['yard'])
        vet = make_user_with_access(username='vet', dashboard='full', health='view')
        self.assertEqual(self.groups(vet), ['health'])

    def test_the_card_is_called_timeline_and_has_chips(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('dashboard_activity'))
        self.assertContains(response, '>Timeline</h2>')
        self.assertNotContains(response, 'What changed')
        self.assertContains(response, 'class="timeline-chip" data-group="health"')
        self.assertContains(response, 'data-group="yard"')

    def test_one_group_shows_no_chips(self):
        groom = make_user_with_access(username='groom3', dashboard='full', horses='view')
        self.client.force_login(groom)
        response = self.client.get(reverse('dashboard_activity'))
        self.assertContains(response, '>Timeline</h2>')
        self.assertNotContains(response, 'timeline-chip')
