"""Horse page, phase 2 (docs/HORSE_PAGE_UX_REVIEW.md section 7): A1 the Due
card, A2 the card order, A6 the Timeline limit, A7 the phone row and the
owner's call link, A5 the departed state."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from health.models import Vaccination, VaccinationType, VetVisit

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class Base(TestCase):

    def setUp(self):
        self.today = timezone.localdate()
        self.owner = Owner.objects.create(name='Jo Bloggs', phone='07700 900123')
        self.location = Location.objects.create(name='Top Field', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)
        self.flu = VaccinationType.objects.create(name='Flu', interval_months=12)

    def horse(self, name='Avicii', placed=True, **kw):
        horse = Horse.objects.create(name=name, **kw)
        if placed:
            Placement.objects.create(horse=horse, owner=self.owner, location=self.location,
                                     rate_type=self.rate, start_date=self.today - timedelta(days=30))
        return horse

    def page(self, horse, user=None):
        self.client.force_login(user or make_admin())
        return self.client.get(reverse('horse_detail', args=[horse.pk]))

    def overdue_flu(self, horse, days=15):
        Vaccination.objects.create(horse=horse, vaccination_type=self.flu,
                                   date_given=self.today - timedelta(days=380),
                                   next_due_date=self.today - timedelta(days=days))


class DueTests(Base):
    """A1."""

    def test_overdue_item_with_its_record_button(self):
        horse = self.horse()
        self.overdue_flu(horse)
        response = self.page(horse)
        self.assertEqual(response.context['due_flag']['severity'], 'overdue')
        body = response.content.decode()
        card = body.split('data-testid="due-strip"', 1)[1].split('</section>', 1)[0]
        self.assertIn('Flu', card)
        self.assertIn('15 days overdue', card)
        self.assertIn(reverse('vaccination_create'), card)
        self.assertNotIn('btn-primary', card)  # the header keeps the only main button

    def test_nothing_due_means_no_card(self):
        horse = self.horse()
        Vaccination.objects.create(horse=horse, vaccination_type=self.flu, date_given=self.today,
                                   next_due_date=self.today + timedelta(days=300))
        response = self.page(horse)
        self.assertIsNone(response.context['due_flag'])
        self.assertNotContains(response, 'data-testid="due-strip"')

    def test_departed_horse_and_role_without_health_have_no_card(self):
        horse = self.horse()
        self.overdue_flu(horse)
        user = make_user_with_access('nohealth', horses='view')
        self.assertNotContains(self.page(horse, user), 'data-testid="due-strip"')
        gone = self.horse('Gone', placed=False, is_active=False)
        self.overdue_flu(gone)
        self.assertNotContains(self.page(gone), 'data-testid="due-strip"')


class OrderTests(Base):
    """A2: daily information first, reference data after."""

    def test_card_order(self):
        horse = self.horse(notes='Bites when rugged')
        self.overdue_flu(horse)
        body = self.page(horse).content.decode()
        main = body.split('<main', 1)[1]
        places = [main.index(marker) for marker in (
            'data-testid="due-strip"', 'Current placement', 'Notes &amp; conditions',
            '>Timeline</h2>', 'id="identity"', 'id="pedigree"')]
        self.assertEqual(places, sorted(places))


class TimelineTests(Base):
    """A6: 10 entries, Show older, and an honest end."""

    def test_more_than_a_page(self):
        horse = self.horse()
        for i in range(12):
            VetVisit.objects.create(horse=horse, date=self.today - timedelta(days=i + 1), reason=f'Check {i}')
        response = self.page(horse)
        events = response.context['timeline_events']
        self.assertEqual(len(events), 11)  # 10 vet visits (the view's limit) and the placement
        body = response.content.decode()
        self.assertIn('data-testid="timeline-list"', body)
        self.assertIn('data-testid="timeline-more"', body)
        self.assertIn('vet_visit: 10', body)
        # The 11th entry waits for Alpine, so the page does not jump.
        self.assertEqual(body.count(' x-cloak>') >= 1, True)
        self.assertIn("(activeFilter === 'all' && 10 < limit)", body)
        # The view cut vet visits off: the end says so, with a link.
        self.assertIn('Showing the last 10 vet visits.', body)
        self.assertIn(f"{reverse('vet_visit_list')}?horse={horse.pk}", body)

    def test_positions(self):
        from core.views.horses import timeline_filters
        events = [{'type': 'vet_visit'}, {'type': 'placement'}, {'type': 'vet_visit'}]
        timeline_filters(events)
        self.assertEqual([(e['pos'], e['kpos']) for e in events], [(0, 0), (1, 0), (2, 1)])

    def test_empty(self):
        horse = self.horse(placed=False)
        body = self.page(horse).content.decode()
        self.assertIn('Nothing recorded yet.', body)
        self.assertNotIn('data-testid="timeline-list"', body)


class PhoneTests(Base):
    """A7: All opens every Record tile in the sheet; the owner's call link."""

    def test_all_opens_the_sheet_and_the_card_is_desktop_only(self):
        horse = self.horse()
        body = self.page(horse).content.decode()
        url = reverse('horse_record_actions', args=[horse.pk])
        self.assertIn(f'hx-get="{url}"', body)
        self.assertIn('class="card hidden lg:block" id="quick-actions"', body)
        response = self.client.get(url, **POPUP)
        self.assertContains(response, 'data-testid="record-actions"')
        self.assertContains(response, reverse('vaccination_create'))
        self.assertRedirects(self.client.get(url), reverse('horse_detail', args=[horse.pk]) + '#quick-actions',
                             fetch_redirect_response=False)

    def test_record_sheet_needs_horse_access(self):
        horse = self.horse()
        self.client.force_login(make_user_with_access('nohorse', health='full'))
        response = self.client.get(reverse('horse_record_actions', args=[horse.pk]), **POPUP)
        self.assertNotEqual(response.status_code, 200)

    def test_call_link_for_roles_that_see_owners(self):
        horse = self.horse()
        OwnershipShare.objects.create(horse=horse, owner=self.owner, share_percentage=100)
        body = self.page(horse).content.decode()
        self.assertIn('href="tel:07700900123"', body)
        user = make_user_with_access('noowners', horses='view')
        self.assertNotIn('tel:', self.page(horse, user).content.decode())


class DepartedTests(Base):
    """A5: when and where it left; no badge for a horse on the yard."""

    def test_departed_horse(self):
        horse = Horse.objects.create(name='Gone', is_active=False)
        Placement.objects.create(horse=horse, owner=self.owner, location=self.location, rate_type=self.rate,
                                 start_date=self.today - timedelta(days=90), end_date=self.today - timedelta(days=10))
        body = self.page(horse).content.decode()
        left = (self.today - timedelta(days=10))
        stamp = f'{left.day} {left:%b %Y}'
        self.assertIn(f'Departed {stamp}', body.split('data-testid="departed-badge"', 1)[1].split('</span>', 1)[0])
        line = body.split('data-testid="placement-empty"', 1)[1].split('<!-- Notes', 1)[0]
        self.assertIn(f'Departed {stamp}', line)
        self.assertIn('Left Top Field.', line)
        self.assertNotIn('Inactive', body)

    def test_horse_on_the_yard_has_no_status_badge(self):
        body = self.page(self.horse()).content.decode()
        header = body.split('<h1', 1)[1].split('</h1>', 1)[1][:800]
        self.assertNotIn('Active', header)
        self.assertNotIn('departed-badge', body)
