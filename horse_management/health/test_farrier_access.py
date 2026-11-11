"""Farrier page, step 2: the gate, the fence and the scope.

docs/FARRIER_PAGE_PLAN.md, sections 4, 8 and 9. A farrier login must
land on the farrier page, must not reach any staff page (even one that
only checks for a signed-in user), and must never see another farrier's
visits or prices.
"""

import importlib
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge, ServiceItem, ServiceProvider
from core.models import Horse, Owner, Role
from core.permissions import is_provider_only
from core.roles_testutils import assign_role, make_admin, make_user_with_access
from health.models import FarrierVisit, ProviderVisit

User = get_user_model()
seed_migration = importlib.import_module('core.migrations.0053_seed_farrier_role')

TODAY_URL = '/farrier/'


def farrier_role():
    seed_migration.seed_farrier_role(django_apps, None)
    return Role.objects.get(name='Farrier')


def make_farrier(username='farrier', name='Sam Shoe', linked=True):
    user = User.objects.create_user(username=username, password='pw')
    assign_role(user, farrier_role())
    provider = ServiceProvider.objects.create(
        name=name, provider_type=ServiceProvider.ProviderType.FARRIER,
        user=user if linked else None,
    )
    return user, provider


class FenceTests(TestCase):
    """A farrier login is kept on the farrier page."""

    def setUp(self):
        self.user, self.provider = make_farrier()
        self.client.force_login(self.user)

    def test_farrier_role_is_provider_only(self):
        self.assertTrue(is_provider_only(self.user))

    def test_sign_in_lands_on_the_farrier_page(self):
        response = self.client.get('/')
        self.assertRedirects(response, TODAY_URL)

    def test_staff_pages_send_the_farrier_back(self):
        horse = Horse.objects.create(name='Bramble')
        owner = Owner.objects.create(name='Jo Bloggs')
        for url in (
            '/horses/',
            f'/horses/{horse.pk}/',
            f'/owners/{owner.pk}/',
            '/invoicing/',
            '/billing/costs/',
            '/health/',
            '/health/farrier/',
            '/health/vet-visits/',
            '/settings/',
            '/settings/users/add/',
            '/accounts/security/',
            reverse('admin:index'),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302, url)
                self.assertEqual(response['Location'], TODAY_URL, url)

    def test_login_only_endpoints_are_fenced_too(self):
        # These check only that someone is signed in, so the fence is what
        # keeps a farrier out of them.
        response = self.client.get(reverse('bulk_health_form'), {'action_type': 'farrier'})
        self.assertEqual(response['Location'], TODAY_URL)

    def test_htmx_requests_get_a_client_redirect(self):
        response = self.client.get('/horses/', HTTP_HX_REQUEST='true')
        self.assertEqual(response['HX-Redirect'], TODAY_URL)

    def test_a_post_to_a_staff_url_does_not_run(self):
        horse = Horse.objects.create(name='Bramble')
        owner = Owner.objects.create(name='Jo Bloggs')
        response = self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'charge', 'horse_ids': [horse.pk],
            'date': timezone.localdate().isoformat(), 'description': 'Sneaky',
            'amount': '10.00', 'charge_type': 'other', 'owner': owner.pk,
        })
        self.assertEqual(response['Location'], TODAY_URL)
        self.assertFalse(ExtraCharge.objects.exists())

    def test_uploaded_documents_are_fenced(self):
        for url in ('/media/documents/2026/01/passport.pdf', '/media/receipts/2026/01/r.pdf'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response['Location'], TODAY_URL)

    def test_account_pages_the_farrier_needs_stay_open(self):
        response = self.client.get(reverse('password_change'))
        self.assertEqual(response.status_code, 200)

    def test_sign_out_works(self):
        response = self.client.post(reverse('logout'))
        self.assertNotEqual(response.get('Location'), TODAY_URL)
        self.assertNotIn('_auth_user_id', self.client.session)


class StaffAreNotFencedTests(TestCase):
    def test_admin_keeps_the_dashboard_and_can_open_the_farrier_page(self):
        admin = make_admin(username='boss')
        self.client.force_login(admin)
        self.assertFalse(is_provider_only(admin))
        self.assertEqual(self.client.get('/').status_code, 200)
        # No provider linked to this login: a page that says how to test
        # the farrier page, not an error or "ask the yard".
        response = self.client.get(TODAY_URL)
        self.assertContains(response, 'You are signed in as staff')
        self.assertContains(response, reverse('provider_list'))
        self.assertContains(response, reverse('provider_visit_list'))
        self.assertNotContains(response, 'Ask the yard')

    def test_superuser_without_a_role_gets_the_staff_page_too(self):
        # Reported on the live site: a superuser opening the QR code
        # address saw "Not linked yet" and read it as no permission.
        superuser = User.objects.create_superuser('root', 'root@example.com', 'pw')
        self.client.force_login(superuser)
        for name in ('today', 'visit_list', 'price_list'):
            with self.subTest(page=name):
                response = self.client.get(reverse(f'farrier:{name}'))
                self.assertContains(response, 'You are signed in as staff')
        # A POST does nothing for a staff login with no farrier.
        horse = Horse.objects.create(name='Bramble')
        self.client.post(reverse('farrier:horse_add', args=[horse.pk]), {'price': '10'})
        self.assertFalse(ProviderVisit.objects.exists())

    def test_staff_without_the_farrier_page_are_denied_it(self):
        user = make_user_with_access(username='groom', dashboard='full', horses='full')
        self.client.force_login(user)
        response = self.client.get(TODAY_URL)
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

    def test_anonymous_users_go_to_sign_in(self):
        response = self.client.get(TODAY_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])


class GateTests(TestCase):
    def test_farrier_without_a_linked_provider_sees_a_clear_page(self):
        user, _ = make_farrier(linked=False)
        self.client.force_login(user)
        response = self.client.get(TODAY_URL)
        self.assertContains(response, 'not linked to a farrier', status_code=403)
        # Still fenced from the staff pages.
        self.assertEqual(self.client.get('/horses/')['Location'], TODAY_URL)

    def test_inactive_provider_cannot_use_the_page(self):
        user, provider = make_farrier()
        provider.is_active = False
        provider.save()
        self.client.force_login(user)
        self.assertEqual(self.client.get(TODAY_URL).status_code, 403)

    def test_farrier_pages_open_for_a_linked_farrier(self):
        user, _ = make_farrier()
        self.client.force_login(user)
        for name in ('today', 'visit_list', 'price_list'):
            with self.subTest(page=name):
                response = self.client.get(reverse(f'farrier:{name}'))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Sam Shoe')

    def test_farrier_page_never_links_to_staff_pages(self):
        user, _ = make_farrier()
        self.client.force_login(user)
        response = self.client.get(TODAY_URL)
        for staff_url in ('/horses/', '/owners/', '/invoicing/', '/settings/'):
            self.assertNotContains(response, f'href="{staff_url}')


class ScopeTests(TestCase):
    """One farrier never sees another farrier's visits or prices."""

    def setUp(self):
        self.user, self.provider = make_farrier()
        self.other_user, self.other = make_farrier(username='rival', name='Rival Forge')
        self.horse = Horse.objects.create(name='Bramble')
        self.mine = ProviderVisit.objects.create(provider=self.provider)
        FarrierVisit.objects.create(
            horse=self.horse, date=self.mine.date, service_provider=self.provider,
            work_done='full_set', cost=Decimal('95.00'), provider_visit=self.mine,
        )
        self.theirs = ProviderVisit.objects.create(provider=self.other)
        FarrierVisit.objects.create(
            horse=self.horse, date=self.theirs.date, service_provider=self.other,
            work_done='trim', cost=Decimal('31.00'), provider_visit=self.theirs,
        )
        ServiceItem.objects.create(name='My full set', category='farrier', price=Decimal('95.00'), provider=self.provider)
        ServiceItem.objects.create(name='Rival trim', category='farrier', price=Decimal('31.00'), provider=self.other)
        ServiceItem.objects.create(name='Yard trim', category='farrier', price=Decimal('30.00'))
        self.client.force_login(self.user)

    def test_another_farriers_visit_is_not_found(self):
        response = self.client.get(reverse('farrier:visit_detail', args=[self.theirs.pk]))
        self.assertEqual(response.status_code, 404)

    def test_own_visit_shows_its_lines(self):
        response = self.client.get(reverse('farrier:visit_detail', args=[self.mine.pk]))
        self.assertContains(response, 'Bramble')
        self.assertContains(response, '95.00')
        self.assertNotContains(response, '31.00')

    def test_visit_list_holds_only_own_visits(self):
        response = self.client.get(reverse('farrier:visit_list'))
        self.assertEqual(list(response.context['visits']), [self.mine])

    def test_prices_hold_only_own_items(self):
        pages = {
            'My prices': self.client.get(reverse('farrier:price_list')),
            'price panel': self.client.get(
                reverse('farrier:horse_panel', args=[self.horse.pk]), HTTP_HX_REQUEST='true',
            ),
        }
        for page, response in pages.items():
            with self.subTest(page=page):
                self.assertContains(response, 'My full set')
                self.assertNotContains(response, 'Rival trim')
                self.assertNotContains(response, 'Yard trim')

    def test_today_shows_only_own_open_visit(self):
        response = self.client.get(TODAY_URL)
        self.assertEqual(response.context['visit'], self.mine)

    def test_farrier_pages_never_show_owners(self):
        owner = Owner.objects.create(name='Private Owner Name', email='owner@example.com')
        self.horse.ownership_shares.create(owner=owner, share_percentage=100, is_primary_contact=True)
        for name in ('today', 'visit_list', 'price_list'):
            response = self.client.get(reverse(f'farrier:{name}'))
            self.assertNotContains(response, 'Private Owner Name')
        response = self.client.get(reverse('farrier:visit_detail', args=[self.mine.pk]))
        self.assertNotContains(response, 'Private Owner Name')
        self.assertNotContains(response, 'owner@example.com')


class StaySignedInTests(TestCase):
    """Section 9: the farrier should not type a password in a field."""

    def test_farrier_session_idles_for_the_longer_window(self):
        from django.conf import settings

        user, _ = make_farrier()
        self.client.force_login(user)
        self.client.get(TODAY_URL)
        self.assertEqual(self.client.session.get_expiry_age(), settings.PROVIDER_SESSION_IDLE_AGE)
        self.assertGreater(settings.PROVIDER_SESSION_IDLE_AGE, settings.SESSION_COOKIE_AGE)

    def test_staff_session_keeps_the_normal_window(self):
        from django.conf import settings

        self.client.force_login(make_admin('boss'))
        self.client.get('/')
        self.assertEqual(self.client.session.get_expiry_age(), settings.SESSION_COOKIE_AGE)

    def test_the_absolute_cap_still_signs_the_farrier_out(self):
        import time

        from django.conf import settings

        from core.middleware import SESSION_STARTED_KEY

        user, _ = make_farrier()
        self.client.force_login(user)
        session = self.client.session
        session[SESSION_STARTED_KEY] = int(time.time()) - settings.SESSION_ABSOLUTE_AGE - 60
        session.save()
        response = self.client.get(TODAY_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])
