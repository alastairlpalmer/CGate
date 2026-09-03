"""Tests for archiving, restoring and deleting fields and sites.

Archiving retires a field but keeps every record attached to it. Deleting
is only for fields nothing points at — anything with placement or feed
history must be archived instead.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType

User = get_user_model()


class LocationArchiveTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User(
            username='yardboss',
            last_login=timezone.now(),
            date_joined=timezone.now(),
            is_active=True,
        )
        cls.user.set_password('x')
        cls.user.save()
        from core.roles_testutils import administrator_role, assign_role
        assign_role(cls.user, administrator_role())

        cls.owner = Owner.objects.create(name='Jo Bloggs')
        cls.rate = RateType.objects.create(name='Full', daily_rate=10)

    def setUp(self):
        self.client.force_login(self.user)

    # ── helpers ──────────────────────────────────────────────────────
    def _place(self, location, name, ended=False):
        today = timezone.localdate()
        horse = Horse.objects.create(name=name)
        return Placement.objects.create(
            horse=horse, owner=self.owner, location=location,
            rate_type=self.rate, start_date=today - timedelta(days=30),
            end_date=today - timedelta(days=1) if ended else None,
        )

    # ── archive ──────────────────────────────────────────────────────
    def test_archive_empty_field(self):
        loc = Location.objects.create(name='Top Field', site='Colgate')
        response = self.client.post(reverse('location_archive', args=[loc.pk]))
        self.assertEqual(response.status_code, 302)
        loc.refresh_from_db()
        self.assertTrue(loc.is_archived)
        self.assertIsNotNone(loc.archived_at)

    def test_archive_keeps_the_history(self):
        loc = Location.objects.create(name='Bottom Field', site='Colgate')
        self._place(loc, 'Old Timer', ended=True)
        self.client.post(reverse('location_archive', args=[loc.pk]))
        loc.refresh_from_db()
        self.assertTrue(loc.is_archived)
        self.assertEqual(loc.placements.count(), 1)

    def test_archive_is_blocked_while_horses_are_on_the_field(self):
        loc = Location.objects.create(name='Front Field', site='Colgate')
        self._place(loc, 'Dobbin')
        self.client.post(reverse('location_archive', args=[loc.pk]))
        loc.refresh_from_db()
        self.assertFalse(loc.is_archived)

    def test_archive_needs_a_post(self):
        loc = Location.objects.create(name='Top Field', site='Colgate')
        response = self.client.get(reverse('location_archive', args=[loc.pk]))
        self.assertEqual(response.status_code, 405)
        loc.refresh_from_db()
        self.assertFalse(loc.is_archived)

    def test_restore_puts_the_field_back(self):
        loc = Location.objects.create(
            name='Top Field', site='Colgate',
            is_archived=True, archived_at=timezone.now(),
        )
        self.client.post(reverse('location_restore', args=[loc.pk]))
        loc.refresh_from_db()
        self.assertFalse(loc.is_archived)
        self.assertIsNone(loc.archived_at)

    # ── delete ───────────────────────────────────────────────────────
    def test_delete_an_unused_field(self):
        loc = Location.objects.create(name='Typo Field', site='Colgate')
        self.client.post(reverse('location_delete', args=[loc.pk]))
        self.assertFalse(Location.objects.filter(pk=loc.pk).exists())

    def test_delete_is_blocked_by_placement_history(self):
        loc = Location.objects.create(name='Busy Field', site='Colgate')
        self._place(loc, 'Old Timer', ended=True)
        self.client.post(reverse('location_delete', args=[loc.pk]))
        self.assertTrue(Location.objects.filter(pk=loc.pk).exists())

    def test_delete_is_blocked_by_feed_records(self):
        from billing.models import FeedOut, FeedType

        loc = Location.objects.create(name='Hay Field', site='Colgate')
        FeedOut.objects.create(
            location=loc, date=timezone.localdate(), feed_type=FeedType.HAY,
        )
        self.client.post(reverse('location_delete', args=[loc.pk]))
        self.assertTrue(Location.objects.filter(pk=loc.pk).exists())

    def test_delete_needs_a_post(self):
        loc = Location.objects.create(name='Typo Field', site='Colgate')
        response = self.client.get(reverse('location_delete', args=[loc.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Location.objects.filter(pk=loc.pk).exists())

    # ── sites ────────────────────────────────────────────────────────
    def test_archive_a_whole_site(self):
        a = Location.objects.create(name='One', site='Little Tew')
        b = Location.objects.create(name='Two', site='Little Tew')
        other = Location.objects.create(name='Three', site='Colgate')
        self.client.post(reverse('site_archive'), {'site': 'Little Tew'})
        a.refresh_from_db(); b.refresh_from_db(); other.refresh_from_db()
        self.assertTrue(a.is_archived)
        self.assertTrue(b.is_archived)
        self.assertFalse(other.is_archived)

    def test_site_archive_skips_fields_that_still_have_horses(self):
        empty = Location.objects.create(name='Empty', site='Little Tew')
        busy = Location.objects.create(name='Busy', site='Little Tew')
        self._place(busy, 'Dobbin')
        self.client.post(reverse('site_archive'), {'site': 'Little Tew'})
        empty.refresh_from_db(); busy.refresh_from_db()
        self.assertTrue(empty.is_archived)
        self.assertFalse(busy.is_archived)

    def test_restore_a_whole_site(self):
        a = Location.objects.create(
            name='One', site='Little Tew',
            is_archived=True, archived_at=timezone.now(),
        )
        self.client.post(reverse('site_restore'), {'site': 'Little Tew'})
        a.refresh_from_db()
        self.assertFalse(a.is_archived)

    def test_delete_a_whole_site(self):
        Location.objects.create(name='One', site='Little Tew')
        Location.objects.create(name='Two', site='Little Tew')
        keep = Location.objects.create(name='Three', site='Colgate')
        self.client.post(reverse('site_delete'), {'site': 'Little Tew'})
        self.assertFalse(Location.objects.filter(site='Little Tew').exists())
        self.assertTrue(Location.objects.filter(pk=keep.pk).exists())

    def test_site_delete_is_all_or_nothing(self):
        clean = Location.objects.create(name='One', site='Little Tew')
        used = Location.objects.create(name='Two', site='Little Tew')
        self._place(used, 'Old Timer', ended=True)
        self.client.post(reverse('site_delete'), {'site': 'Little Tew'})
        self.assertEqual(Location.objects.filter(site='Little Tew').count(), 2)
        self.assertTrue(Location.objects.filter(pk=clean.pk).exists())

    # ── what archived fields drop out of ─────────────────────────────
    def test_archived_fields_leave_the_locations_tab(self):
        live = Location.objects.create(name='Live', site='Colgate')
        gone = Location.objects.create(
            name='Gone', site='Colgate',
            is_archived=True, archived_at=timezone.now(),
        )
        response = self.client.get(reverse('location_list'))
        pks = [
            loc.pk
            for _site, locs, _count in response.context['grouped_locations']
            for loc in locs
        ]
        self.assertIn(live.pk, pks)
        self.assertNotIn(gone.pk, pks)

    def test_archived_fields_leave_the_location_pickers(self):
        from core.forms import get_grouped_location_choices

        Location.objects.create(name='Live', site='Colgate')
        Location.objects.create(
            name='Gone', site='Colgate',
            is_archived=True, archived_at=timezone.now(),
        )
        rendered = str(get_grouped_location_choices())
        self.assertIn('Live', rendered)
        self.assertNotIn('Gone', rendered)

    def test_archived_field_refuses_new_arrivals(self):
        loc = Location.objects.create(
            name='Gone', site='Colgate',
            is_archived=True, archived_at=timezone.now(),
        )
        response = self.client.get(reverse('location_arrive', args=[loc.pk]))
        self.assertRedirects(
            response, reverse('location_detail', args=[loc.pk])
        )

    def test_settings_page_splits_active_from_archived(self):
        live = Location.objects.create(name='Live', site='Colgate')
        gone = Location.objects.create(
            name='Gone', site='Colgate',
            is_archived=True, archived_at=timezone.now(),
        )
        response = self.client.get(reverse('app_settings'))
        active_pks = [
            loc.pk
            for group in response.context['location_groups']
            for loc in group['locations']
        ]
        archived_pks = [
            loc.pk
            for group in response.context['archived_location_groups']
            for loc in group['locations']
        ]
        self.assertEqual(active_pks, [live.pk])
        self.assertEqual(archived_pks, [gone.pk])

    def test_settings_page_marks_which_fields_can_be_deleted(self):
        clean = Location.objects.create(name='Clean', site='Colgate')
        used = Location.objects.create(name='Used', site='Colgate')
        self._place(used, 'Old Timer', ended=True)
        response = self.client.get(reverse('app_settings'))
        by_pk = {
            loc.pk: loc
            for group in response.context['location_groups']
            for loc in group['locations']
        }
        self.assertTrue(by_pk[clean.pk].can_be_deleted)
        self.assertFalse(by_pk[used.pk].can_be_deleted)
        # One used field is enough to protect the whole site.
        group = response.context['location_groups'][0]
        self.assertFalse(group['can_be_deleted'])
