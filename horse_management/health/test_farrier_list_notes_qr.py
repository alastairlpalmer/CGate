"""Farrier page: the yard's list, farrier notes, and the QR sign-in.

docs/FARRIER_PAGE_PLAN.md, sections 14 to 16.
- The yard makes a list for a visit date; the farrier sees it in Due now.
- The farrier can leave a note on a horse with no work and no charge.
- Scanning the QR code, a farrier picks their name; no password.
"""

import importlib
from datetime import timedelta
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import BACKEND_SESSION_KEY, get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge, ServiceItem, ServiceProvider
from core.models import AuditEvent, Horse, Location, Owner, OwnershipShare, Placement, RateType, Role
from core.roles_testutils import administrator_role, assign_role, make_admin, make_user_with_access
from health import farrier_yard
from health.models import FarrierJob, FarrierNote, FarrierVisit, ProviderVisit

User = get_user_model()
seed_migration = importlib.import_module('core.migrations.0053_seed_farrier_role')
TODAY_URL = '/farrier/'


def make_farrier(username='farrier', name='Sam Shoe', linked=True, active=True):
    seed_migration.seed_farrier_role(django_apps, None)
    user = User.objects.create_user(username=username, password='pw')
    assign_role(user, Role.objects.get(name='Farrier'))
    provider = ServiceProvider.objects.create(
        name=name, provider_type='farrier', user=user if linked else None, is_active=active,
    )
    return user, provider


class Yard(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.user, self.provider = make_farrier()
        self.full_set = ServiceItem.objects.create(
            name='Full set', category='farrier', price=Decimal('95.00'), farrier_work='full_set', provider=self.provider,
        )
        self.trim = ServiceItem.objects.create(
            name='Trim', category='farrier', price=Decimal('35.00'), farrier_work='trim', provider=self.provider,
        )
        owner = Owner.objects.create(name='Jo Bloggs')
        rate = RateType.objects.create(name='Grass', daily_rate=5)
        field = Location.objects.create(name='Top Field', site='Main')
        self.horses = {}
        for name in ('Bramble', 'Clover', 'Pickle'):
            horse = Horse.objects.create(name=name)
            OwnershipShare.objects.create(horse=horse, owner=owner, share_percentage=100, is_primary_contact=True)
            Placement.objects.create(horse=horse, owner=owner, location=field, rate_type=rate,
                                     start_date=self.today - timedelta(days=60))
            self.horses[name] = horse
        # Bramble is overdue by date; Pickle was done last week (not due).
        FarrierVisit.objects.create(horse=self.horses['Bramble'], date=self.today - timedelta(weeks=8), work_done='full_set')
        FarrierVisit.objects.create(horse=self.horses['Pickle'], date=self.today - timedelta(weeks=1), work_done='trim')
        self.admin = make_admin('boss')


class YardListPageTests(Yard):
    def list_url(self, day=None):
        return f"{reverse('farrier_job_list')}?provider={self.provider.pk}&date={(day or self.today).isoformat()}"

    def add(self, horse, work='trim', note='', day=None):
        return self.client.post(reverse('farrier_job_list'), {
            'provider': self.provider.pk, 'date': (day or self.today).isoformat(),
            'horse': horse.pk, 'work_needed': work, 'note': note,
        })

    def test_yard_adds_and_removes_an_item(self):
        self.client.force_login(self.admin)
        response = self.add(self.horses['Pickle'], 'full_set', 'Lost a shoe in the field')
        self.assertRedirects(response, self.list_url(), fetch_redirect_response=False)
        job = FarrierJob.objects.get()
        self.assertEqual((job.horse.name, job.work_needed, job.note, job.created_by),
                         ('Pickle', 'full_set', 'Lost a shoe in the field', self.admin))
        page = self.client.get(self.list_url())
        self.assertContains(page, 'Lost a shoe in the field')
        self.assertContains(page, 'To do')
        self.client.post(reverse('farrier_job_remove', args=[job.pk]))
        self.assertFalse(FarrierJob.objects.exists())

    def test_adding_the_same_horse_again_changes_its_item(self):
        self.client.force_login(self.admin)
        self.add(self.horses['Pickle'], 'trim')
        self.add(self.horses['Pickle'], 'full_set', 'Pads on fronts')
        job = FarrierJob.objects.get()
        self.assertEqual((job.work_needed, job.note), ('full_set', 'Pads on fronts'))

    def test_view_only_staff_see_the_list_but_cannot_change_it(self):
        FarrierJob.objects.create(provider=self.provider, date=self.today, horse=self.horses['Pickle'])
        self.client.force_login(make_user_with_access('groom', health='view'))
        page = self.client.get(self.list_url())
        self.assertContains(page, 'Pickle')
        self.assertNotContains(page, 'data-job-add')
        self.add(self.horses['Clover'])
        self.assertEqual(FarrierJob.objects.count(), 1)
        job = FarrierJob.objects.get()
        self.client.post(reverse('farrier_job_remove', args=[job.pk]))
        self.assertTrue(FarrierJob.objects.filter(pk=job.pk).exists())

    def test_no_health_access_no_list(self):
        self.client.force_login(make_user_with_access('bookkeeper', charges='view'))
        self.assertNotEqual(self.client.get(self.list_url()).status_code, 200)

    def test_bad_work_and_a_departed_horse_are_refused(self):
        self.client.force_login(self.admin)
        self.add(self.horses['Pickle'], 'nonsense')
        gone = self.horses['Clover']
        gone.is_active = False
        gone.save()
        self.add(gone)
        self.assertFalse(FarrierJob.objects.exists())

    def test_list_is_a_tab_on_the_farrier_page(self):
        self.client.force_login(self.admin)
        for url in (reverse('farrier_list'), reverse('provider_visit_list')):
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), 'data-farrier-tab="list"')

    def test_status_to_do_done_and_missed(self):
        FarrierJob.objects.create(provider=self.provider, date=self.today, horse=self.horses['Pickle'], work_needed='trim')
        FarrierJob.objects.create(provider=self.provider, date=self.today, horse=self.horses['Clover'])
        FarrierJob.objects.create(provider=self.provider, date=self.today - timedelta(days=3), horse=self.horses['Clover'])
        self.client.force_login(self.user)
        self.client.post(reverse('farrier:horse_add', args=[self.horses['Pickle'].pk]), {'item': self.trim.pk})
        rows = {r.job.horse.name: r for r in farrier_yard.job_rows(self.provider, self.today)}
        self.assertEqual(rows['Pickle'].status, 'done')
        self.assertEqual(rows['Clover'].status, 'to_do')
        old = farrier_yard.job_rows(self.provider, self.today - timedelta(days=3))
        self.assertEqual(old[0].status, 'missed')

    def test_a_staff_record_with_the_provider_counts_as_done(self):
        FarrierJob.objects.create(provider=self.provider, date=self.today, horse=self.horses['Clover'])
        FarrierVisit.objects.create(horse=self.horses['Clover'], date=self.today, work_done='trim',
                                    service_provider=self.provider)
        self.assertEqual(farrier_yard.job_rows(self.provider, self.today)[0].status, 'done')


class FarrierSeesTheListTests(Yard):
    def setUp(self):
        super().setUp()
        FarrierJob.objects.create(provider=self.provider, date=self.today, horse=self.horses['Pickle'],
                                  work_needed='full_set', note='Pads on fronts')
        self.client.force_login(self.user)

    def test_list_horses_are_in_due_now_and_first(self):
        response = self.client.get(TODAY_URL)
        self.assertEqual([h.name for h in response.context['due_horses']], ['Pickle', 'Bramble'])
        self.assertEqual([h.name for h in response.context['other_horses']], ['Clover'])
        self.assertContains(response, 'On the list')
        self.assertContains(response, "1 on the yard")
        self.assertContains(response, 'Pads on fronts')

    def test_panel_shows_what_the_yard_asked_for(self):
        response = self.client.get(reverse('farrier:horse_panel', args=[self.horses['Pickle'].pk]), HTTP_HX_REQUEST='true')
        self.assertContains(response, 'data-yard-request')
        self.assertContains(response, 'Full Set')
        self.assertContains(response, 'border-saddle border-2')

    def test_another_farrier_and_another_day_do_not_see_it(self):
        FarrierJob.objects.create(provider=self.provider, date=self.today + timedelta(days=1), horse=self.horses['Clover'])
        rival_user, _ = make_farrier(username='rival', name='Rival Forge')
        self.client.force_login(rival_user)
        response = self.client.get(TODAY_URL)
        self.assertNotContains(response, 'On the list')
        self.client.force_login(self.user)
        response = self.client.get(TODAY_URL)
        self.assertNotIn('Clover', [h.name for h in response.context['due_horses']])


class FarrierNoteTests(Yard):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.pickle = self.horses['Pickle']

    def note(self, text, htmx=True):
        headers = {'HTTP_HX_REQUEST': 'true'} if htmx else {}
        return self.client.post(reverse('farrier:horse_note', args=[self.pickle.pk]), {'text': text}, **headers)

    def test_a_note_with_no_work_makes_no_charge_and_keeps_the_due_date(self):
        due_before = FarrierVisit.objects.filter(horse=self.pickle).latest('date').next_due_date
        response = self.note('Heat in near fore, suggest the vet looks')
        self.assertContains(response, 'Heat in near fore')
        note = FarrierNote.objects.get()
        self.assertEqual((note.horse, note.provider, note.date), (self.pickle, self.provider, self.today))
        self.assertFalse(ExtraCharge.objects.exists())
        self.assertEqual(FarrierVisit.objects.filter(horse=self.pickle).count(), 1)
        self.assertEqual(FarrierVisit.objects.filter(horse=self.pickle).latest('date').next_due_date, due_before)

    def test_an_empty_note_is_refused(self):
        response = self.note('   ')
        self.assertContains(response, 'Write the note first')
        self.assertFalse(FarrierNote.objects.exists())

    def test_note_goes_with_the_visit_and_the_yard_sees_it(self):
        self.note('Thin soles, go gently on the stones')
        self.client.post(reverse('farrier:horse_add', args=[self.horses['Bramble'].pk]), {'item': self.full_set.pk})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('farrier:visit_finish'))
        visit = ProviderVisit.objects.get()
        self.assertEqual(FarrierNote.objects.get().provider_visit, visit)
        self.client.force_login(self.admin)
        page = self.client.get(reverse('provider_visit_detail', args=[visit.pk]))
        self.assertContains(page, 'data-visit-notes')
        self.assertContains(page, 'Thin soles, go gently on the stones')

    def test_notes_show_on_the_horse_record(self):
        self.note('Suggest a hoof supplement')
        self.client.post(reverse('farrier:horse_add', args=[self.horses['Pickle'].pk]),
                         {'item': self.trim.pk, 'note': 'Trimmed short: flaring on the outside wall'})
        self.client.force_login(self.admin)
        page = self.client.get(reverse('horse_detail', args=[self.pickle.pk]))
        self.assertContains(page, 'Farrier note')
        self.assertContains(page, 'Suggest a hoof supplement')
        self.assertContains(page, 'Trimmed short: flaring on the outside wall')

    def test_a_farrier_sees_only_their_own_notes(self):
        self.note('Mine')
        rival_user, _ = make_farrier(username='rival', name='Rival Forge')
        self.client.force_login(rival_user)
        response = self.client.get(reverse('farrier:horse_panel', args=[self.pickle.pk]), HTTP_HX_REQUEST='true')
        self.assertNotContains(response, 'data-farrier-note')


class QRSignInTests(TestCase):
    def setUp(self):
        self.user, self.provider = make_farrier()

    def test_signed_out_qr_code_opens_the_name_picker(self):
        response = self.client.get(TODAY_URL, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], reverse('farrier:who'))
        self.assertContains(response, 'Who is on the yard today?')
        self.assertContains(response, 'Sam Shoe')
        self.assertNotContains(response, 'Sign out')

    def test_picking_a_name_signs_in_with_no_password(self):
        response = self.client.post(reverse('farrier:who'), {'provider': self.provider.pk})
        self.assertRedirects(response, TODAY_URL, fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)
        self.assertEqual(self.client.session[BACKEND_SESSION_KEY], 'core.auth_backends.FarrierQRBackend')
        self.assertEqual(self.client.get(TODAY_URL).status_code, 200)
        event = AuditEvent.objects.filter(action=AuditEvent.Action.SIGNED_IN).latest('pk')
        self.assertEqual(event.detail, 'QR code, name picked')

    def test_staff_logins_are_never_offered(self):
        admin = make_admin('boss')
        staff_provider = ServiceProvider.objects.create(name='Office Farrier', provider_type='farrier', user=admin)
        page = self.client.get(reverse('farrier:who'))
        self.assertNotContains(page, 'Office Farrier')
        self.client.post(reverse('farrier:who'), {'provider': staff_provider.pk})
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_inactive_and_non_farrier_providers_are_not_offered(self):
        make_farrier(username='old', name='Retired Forge', active=False)
        vet_user = User.objects.create_user('vet', password='pw')
        assign_role(vet_user, Role.objects.get(name='Farrier'))
        ServiceProvider.objects.create(name='Downland Vets', provider_type='vet', user=vet_user)
        off_user, _ = make_farrier(username='off', name='Switched Off Forge')
        off_user.is_active = False
        off_user.save()
        page = self.client.get(reverse('farrier:who'))
        for name in ('Retired Forge', 'Downland Vets', 'Switched Off Forge'):
            self.assertNotContains(page, name)

    def test_a_farrier_with_no_login_is_offered(self):
        ServiceProvider.objects.create(name='New Forge', provider_type='farrier', email='new@example.com')
        page = self.client.get(reverse('farrier:who'))
        self.assertContains(page, 'New Forge')
        self.assertNotContains(page, 'No farrier is set up yet')

    def test_picking_a_farrier_with_no_login_makes_a_farrier_only_login(self):
        provider = ServiceProvider.objects.create(name='New Forge', provider_type='farrier', email='new@example.com')
        response = self.client.post(reverse('farrier:who'), {'provider': provider.pk})
        self.assertRedirects(response, TODAY_URL, fetch_redirect_response=False)
        provider.refresh_from_db()
        user = provider.user
        self.assertIsNotNone(user)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.email, 'new@example.com')
        self.assertEqual(user.role_assignment.role.name, 'Farrier')
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.assertEqual(self.client.get(TODAY_URL).status_code, 200)
        self.assertRedirects(self.client.get('/owners/'), TODAY_URL, fetch_redirect_response=False)
        # The next pick uses the same login, not a new one.
        self.client.logout()
        self.client.post(reverse('farrier:who'), {'provider': provider.pk})
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.assertEqual(User.objects.filter(username__startswith='farrier-new-forge').count(), 1)

    def test_no_login_is_made_if_the_farrier_role_reaches_staff_pages(self):
        role = Role.objects.get(name='Farrier')
        role.access = {**role.access, 'owners': 'full'}
        role.save()
        provider = ServiceProvider.objects.create(name='New Forge', provider_type='farrier')
        response = self.client.post(reverse('farrier:who'), {'provider': provider.pk})
        self.assertContains(response, 'cannot sign in from the QR code')
        self.assertNotIn('_auth_user_id', self.client.session)
        provider.refresh_from_db()
        self.assertIsNone(provider.user)

    def test_an_unknown_choice_is_refused(self):
        response = self.client.post(reverse('farrier:who'), {'provider': '99999'})
        self.assertContains(response, 'Choose your name from the list')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_a_qr_session_still_cannot_reach_staff_pages(self):
        self.client.post(reverse('farrier:who'), {'provider': self.provider.pk})
        self.assertRedirects(self.client.get('/owners/'), TODAY_URL, fetch_redirect_response=False)

    def test_a_qr_session_ends_if_the_login_becomes_staff(self):
        self.client.post(reverse('farrier:who'), {'provider': self.provider.pk})
        assign_role(self.user, administrator_role())
        response = self.client.get('/owners/')
        self.assertNotEqual(response.status_code, 200)
        self.assertRedirects(self.client.get(TODAY_URL), reverse('farrier:who'), fetch_redirect_response=False)

    def test_signed_in_users_skip_the_picker(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse('farrier:who')), TODAY_URL, fetch_redirect_response=False)
