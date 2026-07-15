"""Regression tests for review batch 3: the smaller confirmed findings.

1. An admin resetting their own password stays signed in.
2. The placement form's Cancel link never renders an unvalidated ?next=.
3. Bulk-bar gates align with the single-horse equivalents (horses=full for
   placement actions, health=full for records) and are enforced on both the
   form fetch and the apply endpoint.
4. role_delete survives a malformed reassign_to value.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType, Role, UserRole
from core.roles_testutils import (
    make_admin,
    make_user_with_access,
    make_viewer,
)


class SelfPasswordResetTests(TestCase):
    def test_admin_resetting_own_password_stays_signed_in(self):
        admin = make_admin(username='self-reset')
        self.client.force_login(admin)
        response = self.client.post(
            reverse('user_update', args=[admin.pk]),
            {
                'set_password': '1',
                'password1': 'brand-new-passphrase-9',
                'password2': 'brand-new-passphrase-9',
            },
        )
        self.assertRedirects(response, reverse('app_settings'))
        # The session must survive the hash change — a follow-up request is
        # still authenticated instead of bouncing to the login page.
        follow_up = self.client.get(reverse('app_settings'))
        self.assertEqual(follow_up.status_code, 200)
        admin.refresh_from_db()
        self.assertTrue(admin.check_password('brand-new-passphrase-9'))

    def test_resetting_another_users_password_leaves_own_session_alone(self):
        admin = make_admin(username='resetter')
        other = make_viewer(username='resettee')
        self.client.force_login(admin)
        response = self.client.post(
            reverse('user_update', args=[other.pk]),
            {
                'set_password': '1',
                'password1': 'brand-new-passphrase-9',
                'password2': 'brand-new-passphrase-9',
            },
        )
        self.assertRedirects(response, reverse('app_settings'))
        other.refresh_from_db()
        self.assertTrue(other.check_password('brand-new-passphrase-9'))


class PlacementCancelLinkTests(TestCase):
    def setUp(self):
        self.client.force_login(make_admin(username='cancel-admin'))
        today = timezone.now().date()
        owner = Owner.objects.create(name='Jo')
        location = Location.objects.create(name='Top', site='Main')
        rate = RateType.objects.create(name='Full', daily_rate=10)
        horse = Horse.objects.create(name='CANCELTEST')
        self.placement = Placement.objects.create(
            horse=horse, owner=owner, location=location, rate_type=rate,
            start_date=today - timedelta(days=10),
        )
        self.horse = horse

    def test_offsite_next_never_reaches_the_cancel_href(self):
        response = self.client.get(
            reverse('placement_update', args=[self.placement.pk])
            + '?next=javascript:alert(1)'
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'javascript:alert(1)')
        self.assertContains(response, reverse('placement_list'))

    def test_onsite_next_still_used_for_cancel(self):
        next_url = reverse('horse_detail', args=[self.horse.pk])
        response = self.client.get(
            reverse('placement_update', args=[self.placement.pk])
            + f'?next={next_url}'
        )
        self.assertContains(response, f'href="{next_url}"')


class BulkGateAlignmentTests(TestCase):
    def setUp(self):
        today = timezone.now().date()
        self.owner = Owner.objects.create(name='Jo')
        self.location = Location.objects.create(name='Top', site='Main')
        self.other_location = Location.objects.create(name='Bottom', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)
        self.horse = Horse.objects.create(name='GATETEST')
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.location,
            rate_type=self.rate, start_date=today - timedelta(days=10),
        )
        self.today = today

    def test_horses_full_user_can_bulk_move_without_health_or_locations(self):
        user = make_user_with_access(username='mover', horses='full')
        self.client.force_login(user)
        form = self.client.get(
            reverse('bulk_health_form') + '?action_type=move'
        )
        self.assertEqual(form.status_code, 200)
        response = self.client.post(
            reverse('bulk_health_apply'),
            {
                'action_type': 'move',
                'horse_ids': [self.horse.pk],
                'new_location': self.other_location.pk,
                'move_date': self.today.isoformat(),
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            self.horse.placements.get(end_date__isnull=True).location,
            self.other_location,
        )

    def test_horses_full_user_cannot_bulk_record_health(self):
        user = make_user_with_access(username='mover2', horses='full')
        self.client.force_login(user)
        form = self.client.get(
            reverse('bulk_health_form') + '?action_type=vaccination'
        )
        self.assertEqual(form.status_code, 403)

    def test_viewer_gets_403_on_move_form_not_a_hanging_modal(self):
        # Viewer: health=full, horses=view — sees no Move option now, and a
        # crafted fetch is refused at the form endpoint too.
        self.client.force_login(make_viewer(username='gate-viewer'))
        form = self.client.get(
            reverse('bulk_health_form') + '?action_type=move'
        )
        self.assertEqual(form.status_code, 403)
        vax_form = self.client.get(
            reverse('bulk_health_form') + '?action_type=vaccination'
        )
        self.assertEqual(vax_form.status_code, 200)

    def test_bulk_bar_hides_ungated_options(self):
        self.client.force_login(make_viewer(username='bar-viewer'))
        response = self.client.get(reverse('horse_list'))
        self.assertNotContains(response, 'value="move"')
        self.assertContains(response, 'value="vaccination"')


class RoleDeleteMalformedInputTests(TestCase):
    def test_non_numeric_reassign_to_is_a_friendly_error_not_500(self):
        admin = make_admin(username='role-admin')
        self.client.force_login(admin)
        doomed = Role.objects.create(name='Doomed', access={})
        member = make_viewer(username='member')
        UserRole.objects.update_or_create(user=member, defaults={'role': doomed})
        response = self.client.post(
            reverse('role_delete', args=[doomed.pk]),
            {'reassign_to': 'abc'},
        )
        self.assertRedirects(
            response, reverse('role_update', args=[doomed.pk])
        )
        self.assertTrue(Role.objects.filter(pk=doomed.pk).exists())
