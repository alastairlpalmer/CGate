"""C-vet set-up: a Vet login must not be left on "not set up".

The fault this guards against: an administrator gave a login the Vet
role in Settings, the vet signed in, and every address showed "This
login is not set up for C-vet". The second step (a place in a practice)
was in the Django admin only, and no page said so.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from core.roles_testutils import assign_role, make_admin, make_user_with_access

from . import access, setup
from .models import Practice, PracticeMember, PracticeYardLink
from .testutils import make_practice, vet_role

User = get_user_model()

APP = '/c-vet/'
SESSION = '/c-vet/api/session/'
SETUP = '/c-vet/setup/'


def vet_login(username='tim', **fields):
    """A login with the Vet role and nothing else: what Settings, Users makes."""
    user = User.objects.create_user(username, password='pw', email=f'{username}@example.com', **fields)
    return assign_role(user, vet_role())


@override_settings(CVET_ENABLED=True)
class JoinTests(TestCase):
    def test_a_vet_login_opens_cvet_with_no_second_step(self):
        practice = make_practice()
        user = vet_login()
        self.client.force_login(user)
        self.assertEqual(self.client.get(APP).status_code, 200)
        member = PracticeMember.objects.get(user=user)
        self.assertEqual(member.practice, practice)
        self.assertTrue(member.is_active)

    def test_the_phone_api_joins_too(self):
        make_practice()
        self.client.force_login(vet_login())
        self.assertEqual(self.client.get(SESSION).status_code, 200)
        self.assertEqual(PracticeMember.objects.count(), 1)

    def test_no_practice_gives_the_page_that_says_where_to_go(self):
        self.client.force_login(vet_login())
        response = self.client.get(APP)
        self.assertContains(response, 'not set up for C-vet', status_code=403)
        self.assertContains(response, 'C-vet set-up', status_code=403)
        # A vet cannot open the set-up page, so it gets no link to it.
        self.assertNotContains(response, SETUP, status_code=403)
        self.assertFalse(PracticeMember.objects.exists())

    def test_two_practices_need_a_person_to_choose(self):
        make_practice()
        make_practice('Other Vets')
        self.client.force_login(vet_login())
        self.assertEqual(self.client.get(APP).status_code, 403)
        self.assertEqual(self.client.get(SESSION).status_code, 403)
        self.assertFalse(PracticeMember.objects.exists())

    def test_a_closed_practice_does_not_count(self):
        practice = make_practice()
        Practice.objects.create(name='Closed Vets', is_active=False)
        user = vet_login()
        self.client.force_login(user)
        self.assertEqual(self.client.get(APP).status_code, 200)
        self.assertEqual(PracticeMember.objects.get(user=user).practice, practice)

    def test_a_login_that_was_taken_out_stays_out(self):
        practice = make_practice()
        user = vet_login()
        PracticeMember.objects.create(practice=practice, user=user, is_active=False)
        self.client.force_login(user)
        self.assertEqual(self.client.get(APP).status_code, 403)
        self.assertEqual(self.client.get(SESSION).status_code, 403)
        self.assertFalse(PracticeMember.objects.get(user=user).is_active)

    def test_an_administrator_is_never_put_in_the_practice(self):
        make_practice()
        for user in (make_admin(), User.objects.create_superuser('root', password='pw')):
            self.client.force_login(user)
            response = self.client.get(APP)
            self.assertEqual(response.status_code, 403)
            # The administrator gets the link to the set-up page.
            self.assertContains(response, SETUP, status_code=403)
            self.assertEqual(self.client.get(SESSION).status_code, 403)
        self.assertFalse(PracticeMember.objects.exists())

    def test_yard_staff_are_never_put_in_the_practice(self):
        make_practice()
        staff = make_user_with_access('staff', health='full', dashboard='full')
        self.assertIsNone(access.join_the_only_practice(staff))
        self.assertFalse(PracticeMember.objects.exists())


@override_settings(CVET_ENABLED=True)
class SetupPageTests(TestCase):
    def setUp(self):
        self.admin = make_admin(email='admin@example.com')
        self.client.force_login(self.admin)

    def post(self, **data):
        data.setdefault('name', 'Hill Vets')
        data.setdefault('bill_to', 'yard')
        return self.client.post(SETUP, data)

    def test_only_a_login_that_manages_users_opens_it(self):
        self.assertEqual(self.client.get(SETUP).status_code, 200)
        self.client.force_login(make_user_with_access('staff', health='full', dashboard='full'))
        self.assertEqual(self.client.get(SETUP).status_code, 302)
        self.assertEqual(self.post().status_code, 403)
        self.client.force_login(vet_login())
        self.assertEqual(self.client.get(SETUP).status_code, 302)
        self.assertEqual(self.post().status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(SETUP).status_code, 302)
        self.assertFalse(Practice.objects.exists())

    @override_settings(CVET_ENABLED=False)
    def test_it_is_not_there_when_cvet_is_off(self):
        self.assertEqual(self.client.get(SETUP).status_code, 404)

    def test_the_first_set_up_makes_the_practice_the_link_and_the_logins(self):
        tim = vet_login('tim', first_name='Tim', last_name='Vet')
        response = self.client.get(SETUP)
        self.assertContains(response, 'Make the practice')
        self.assertContains(response, 'Tim Vet (tim@example.com)')
        # A first set-up starts with the link open and every Vet login in.
        self.assertEqual(response.context['form'].initial['logins'], [tim.pk])
        self.assertTrue(response.context['form'].initial['link_open'])

        self.assertRedirects(self.post(link_open='on', logins=[tim.pk]), SETUP)
        practice = Practice.objects.get()
        self.assertEqual(practice.name, 'Hill Vets')
        self.assertTrue(practice.yard_link.is_open)
        self.assertEqual(access.practice_for(tim), practice)
        self.client.force_login(tim)
        self.assertEqual(self.client.get(APP).status_code, 200)

    def test_an_unticked_login_is_stopped_and_stays_stopped(self):
        practice = make_practice()
        tim, ann = vet_login('tim'), vet_login('ann')
        PracticeMember.objects.create(practice=practice, user=tim)
        # Ann has no row yet. Unticked, she must not join at sign-in.
        self.post(link_open='on', logins=[])
        self.assertFalse(PracticeMember.objects.get(user=tim).is_active)
        self.assertFalse(PracticeMember.objects.get(user=ann).is_active)
        for user in (tim, ann):
            self.client.force_login(user)
            self.assertEqual(self.client.get(APP).status_code, 403)
            self.assertEqual(self.client.get(SESSION).status_code, 403)
        # Ticked again, she is back.
        self.client.force_login(self.admin)
        self.post(link_open='on', logins=[ann.pk])
        self.assertEqual(access.practice_for(ann), practice)
        self.assertIsNone(access.practice_for(User.objects.get(pk=tim.pk)))

    def test_the_page_shows_the_state_that_is_true(self):
        practice = make_practice()
        tim, ann, bob = vet_login('tim'), vet_login('ann'), vet_login('bob')
        PracticeMember.objects.create(practice=practice, user=tim)
        PracticeMember.objects.create(practice=practice, user=ann, is_active=False)
        initial = self.client.get(SETUP).context['form'].initial
        # Bob has no row: he joins at his first sign-in, so he is ticked.
        self.assertEqual(sorted(initial['logins']), sorted([tim.pk, bob.pk]))
        self.assertEqual(initial['name'], 'Hill Vets')
        self.assertTrue(initial['link_open'])

    def test_the_link_closes_and_opens_again(self):
        practice = make_practice()
        self.post(logins=[])
        link = PracticeYardLink.objects.get(practice=practice)
        self.assertFalse(link.is_open)
        self.assertEqual(link.ended_on, timezone.localdate())
        self.assertFalse(access.horses_for(practice).exists())
        self.post(link_open='on')
        link.refresh_from_db()
        self.assertTrue(link.is_open)
        self.assertIsNone(link.ended_on)

    def test_a_practice_made_without_a_link_gets_one(self):
        practice = make_practice(linked=False)
        self.assertFalse(self.client.get(SETUP).context['form'].initial['link_open'])
        self.post(link_open='on')
        self.assertTrue(PracticeYardLink.objects.get(practice=practice).is_open)

    def test_the_name_changes_and_must_not_be_another_practices_name(self):
        make_practice()
        Practice.objects.create(name='Closed Vets', is_active=False)
        self.post(name='  Valley   Vets ', link_open='on')
        self.assertTrue(Practice.objects.filter(name='Valley Vets', is_active=True).exists())
        response = self.post(name='closed vets', link_open='on')
        self.assertContains(response, 'There is a practice with this name already')
        self.assertContains(self.post(name=''), 'This field is required')
        self.assertEqual(Practice.objects.count(), 2)

    def test_a_login_that_is_not_a_vet_login_cannot_be_put_in(self):
        make_practice()
        response = self.post(link_open='on', logins=[self.admin.pk])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PracticeMember.objects.exists())

    def test_more_than_one_practice_is_sent_to_the_admin(self):
        make_practice()
        make_practice('Other Vets')
        self.assertContains(self.client.get(SETUP), 'more than one practice')

    def test_the_page_does_not_break_for_a_superuser_with_a_practice(self):
        make_practice()
        self.client.force_login(User.objects.create_superuser('root', password='pw'))
        self.assertContains(self.client.get(SETUP), 'Django admin')
        # A login that is not staff gets no link to the admin.
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(SETUP), 'Django admin')


@override_settings(CVET_ENABLED=True)
class SetupDetailsTests(TestCase):
    """The agreement with the yard and the details on the practice's bills:
    all of the set-up, with no step in the Django admin."""

    def setUp(self):
        self.admin = make_admin(email='admin@example.com')
        self.client.force_login(self.admin)

    def post(self, **data):
        data.setdefault('name', 'Hill Vets')
        data.setdefault('bill_to', 'yard')
        data.setdefault('link_open', 'on')
        return self.client.post(SETUP, data)

    def test_a_first_set_up_starts_with_what_a_new_link_has(self):
        initial = self.client.get(SETUP).context['form'].initial
        self.assertEqual(initial['bill_to'], 'yard')
        self.assertTrue(initial['sends_charges'])
        self.assertTrue(initial['sends_vaccination_dates'])
        self.assertTrue(initial['sends_follow_up_dates'])

    def test_the_details_on_the_bills_are_saved(self):
        self.assertRedirects(self.post(
            email='office@hillvets.example', phone='01234 567890', address='1 High Street\nTown',
            vat_number='GB123456789', bill_prefix=' hv ', bank_details='Sort 00-00-00\nAccount 12345678',
        ), SETUP)
        practice = Practice.objects.get()
        self.assertEqual(practice.email, 'office@hillvets.example')
        self.assertEqual(practice.phone, '01234 567890')
        self.assertEqual(practice.address, '1 High Street\nTown')
        self.assertEqual(practice.vat_number, 'GB123456789')
        self.assertEqual(practice.bill_prefix, 'HV')
        self.assertEqual(practice.bank_details, 'Sort 00-00-00\nAccount 12345678')
        # The page shows them again.
        initial = self.client.get(SETUP).context['form'].initial
        self.assertEqual(initial['bill_prefix'], 'HV')
        self.assertEqual(initial['vat_number'], 'GB123456789')

    def test_the_letters_of_a_bill_number_are_letters_and_numbers(self):
        for bad in ('H-V', 'H V', 'HV/1', 'ÉV!'):
            with self.subTest(prefix=bad):
                self.assertContains(self.post(bill_prefix=bad), 'Use letters and numbers only')
        self.assertFalse(Practice.objects.exists())
        self.assertContains(self.post(bill_prefix='TOOLONGPREFIX'), 'at most 10 characters')

    def test_the_bill_prefix_is_used_on_the_next_bill(self):
        from . import billing
        self.post(bill_prefix='hv')
        practice = Practice.objects.get()
        self.assertTrue(billing._next_number(practice, is_test=False).startswith('HV-'))

    def test_the_agreement_is_saved_on_the_link(self):
        from billing.models import ServiceProvider
        vets = ServiceProvider.objects.create(name='Hill Vets', provider_type='vet')
        self.post(bill_to='owners', sends_vaccination_dates='on', service_provider=vets.pk)
        link = PracticeYardLink.objects.get()
        self.assertEqual(link.bill_to, 'owners')
        self.assertFalse(link.sends_charges)
        self.assertTrue(link.sends_vaccination_dates)
        self.assertFalse(link.sends_follow_up_dates)
        self.assertEqual(link.service_provider, vets)
        # Changed again, and the provider taken off.
        self.post(bill_to='yard', sends_charges='on', sends_follow_up_dates='on')
        link.refresh_from_db()
        self.assertEqual(link.bill_to, 'yard')
        self.assertTrue(link.sends_charges)
        self.assertFalse(link.sends_vaccination_dates)
        self.assertIsNone(link.service_provider)

    def test_when_the_practice_bills_is_not_changed_here(self):
        # The practice chooses that on its Bills page.
        practice = make_practice()
        PracticeYardLink.objects.filter(practice=practice).update(bill_when='month')
        self.post()
        self.assertEqual(PracticeYardLink.objects.get().bill_when, 'month')

    def test_only_a_vet_of_the_yard_can_be_the_practice(self):
        from billing.models import ServiceProvider
        farrier = ServiceProvider.objects.create(name='Sam Shoe', provider_type='farrier')
        response = self.post(service_provider=farrier.pk)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select a valid choice')
        self.assertFalse(Practice.objects.exists())

    def test_a_vet_that_is_another_practices_is_not_offered(self):
        from billing.models import ServiceProvider
        vets = ServiceProvider.objects.create(name='Hill Vets', provider_type='vet')
        other = Practice.objects.create(name='Closed Vets', is_active=False)
        PracticeYardLink.objects.create(practice=other, service_provider=vets)
        form = self.client.get(SETUP).context['form']
        self.assertNotIn(vets, form.fields['service_provider'].queryset)
        self.assertContains(self.post(service_provider=vets.pk), 'Select a valid choice')

    def test_no_link_and_none_asked_for_keeps_no_agreement(self):
        self.post(link_open='', bill_to='owners')
        self.assertTrue(Practice.objects.exists())
        self.assertFalse(PracticeYardLink.objects.exists())

    def test_the_agreement_changes_while_the_link_stays_closed(self):
        practice = make_practice()
        self.post(link_open='')
        self.post(link_open='', bill_to='owners')
        link = PracticeYardLink.objects.get(practice=practice)
        self.assertFalse(link.is_open)
        self.assertEqual(link.bill_to, 'owners')

    def test_the_page_sends_nobody_to_the_django_admin_for_set_up(self):
        make_practice()
        self.client.force_login(User.objects.create_superuser('root', password='pw'))
        body = self.client.get(SETUP).content.decode()
        for label in ('The practice bills', 'What the yard takes from the practice', 'VAT number',
                      'Letters before a bill number', 'How to pay'):
            self.assertIn(label, body)
        self.assertNotIn('bank details and what the practice sends to the yard are in the', body)
        # The one thing left there is the delete of the test notes.
        self.assertIn('Delete the test notes', body)


class SettingsPagesTests(TestCase):
    """Where the administrator sees that a Vet login is left out."""

    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    @override_settings(CVET_ENABLED=True)
    def test_settings_says_when_a_vet_login_cannot_open_cvet(self):
        tim = vet_login()
        response = self.client.get('/settings/')
        self.assertContains(response, 'No practice yet')
        self.assertContains(response, '1 login with the Vet role cannot open C-vet')
        self.assertContains(response, SETUP)
        self.assertContains(self.client.get(f'/settings/users/{tim.pk}/edit/'), 'it is not in a practice')

        make_practice()
        response = self.client.get('/settings/')
        self.assertContains(response, 'Hill Vets')
        self.assertContains(response, '1 login &middot;')
        self.assertNotContains(response, 'cannot open C-vet')
        self.assertNotContains(self.client.get(f'/settings/users/{tim.pk}/edit/'), 'it is not in a practice')

        PracticeMember.objects.create(practice=Practice.objects.get(), user=tim, is_active=False)
        self.assertContains(self.client.get('/settings/'), '1 login with the Vet role cannot open C-vet')
        self.assertContains(self.client.get(f'/settings/users/{tim.pk}/edit/'), 'it is not in a practice')

    @override_settings(CVET_ENABLED=True)
    def test_a_yard_login_gets_no_warning(self):
        make_practice()
        self.assertNotContains(self.client.get(f'/settings/users/{self.admin.pk}/edit/'), 'it is not in a practice')
        self.assertFalse(setup.login_gap(self.admin))

    @override_settings(CVET_ENABLED=False)
    def test_nothing_shows_when_cvet_is_off(self):
        tim = vet_login()
        self.assertNotContains(self.client.get('/settings/'), SETUP)
        self.assertNotContains(self.client.get(f'/settings/users/{tim.pk}/edit/'), 'it is not in a practice')
