"""The notification centre (notifications/centre.py, centre_views.py).

docs/SIDEBAR_AND_NOTIFICATIONS_PLAN.md, WP3.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import AttentionDismissal, Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from core.services import PlacementService
from invoicing.models import Invoice

from . import centre
from .models import Notification, NotificationPreference

A = Notification.Category.ACTION
MOVE = Notification.Category.MOVEMENT
MONEY = Notification.Category.MONEY


def _invoice(owner, number, total='100.00', status=Invoice.Status.SENT, **extra):
    return Invoice.objects.create(
        owner=owner, invoice_number=number,
        period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        subtotal=Decimal(total), total=Decimal(total), status=status, **extra,
    )


class CentreTestBase(TestCase):

    def setUp(self):
        cache.clear()
        self.owner = Owner.objects.create(name='Roz Tobin', email='roz@example.com')
        self.field = Location.objects.create(site='Colgate', name='Top Paddock')
        self.other_field = Location.objects.create(site='Colgate', name='Long Meadow')
        self.rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))
        self.boss = make_admin('boss', email='boss@example.com')
        self.groom = make_user_with_access('groom', horses='view')
        self.bookkeeper = make_user_with_access('books', invoices='view')
        for user, address in ((self.groom, 'groom@example.com'), (self.bookkeeper, 'books@example.com')):
            user.email = address
            user.save(update_fields=['email'])

    def _arrive(self, name='Ida', day=None):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=self.field, rate_type=self.rate,
            start_date=day or timezone.localdate() - timedelta(days=30),
        )
        return horse

    def _of(self, user, category=None):
        rows = Notification.objects.filter(recipient=user)
        if category:
            rows = rows.filter(category=category)
        return list(rows)


class PreferenceTests(CentreTestBase):

    def test_defaults_are_in_app_on_email_off(self):
        self.assertEqual(centre.preferences(self.groom), {A: (True, False), MOVE: (True, False), MONEY: (True, False)})

    def test_save(self):
        centre.save_preferences(self.groom, {'movement_in_app': 'on', 'movement_email': 'on', 'action_email': 'on'})
        prefs = centre.preferences(self.groom)
        self.assertEqual(prefs[MOVE], (True, True))
        self.assertEqual(prefs[A], (False, True))
        self.assertEqual(prefs[MONEY], (False, False))

    def test_turning_off_clears_the_badge_for_that_category(self):
        Notification.objects.create(recipient=self.groom, category=MOVE, kind='arrived', title='x', dedupe_key='k')
        self.assertEqual(centre.unread_count(self.groom), 1)
        centre.save_preferences(self.groom, {})
        self.assertEqual(centre.unread_count(self.groom), 0)

    def test_rows_only_offer_categories_the_role_can_see(self):
        categories = [row['category'] for row in centre.preference_rows(self.groom)]
        self.assertEqual(categories, [A, MOVE])
        categories = [row['category'] for row in centre.preference_rows(self.bookkeeper)]
        self.assertEqual(categories, [A, MONEY])


class MovementTests(CentreTestBase):

    def test_people_who_can_see_horses_are_told(self):
        horse = self._arrive()
        centre.horses_moved('moved', [horse], location=self.other_field)
        self.assertEqual(len(self._of(self.groom, MOVE)), 1)
        self.assertEqual(len(self._of(self.boss, MOVE)), 1)
        self.assertEqual(self._of(self.bookkeeper), [])
        note = self._of(self.groom, MOVE)[0]
        self.assertEqual(note.title, 'Ida moved')
        self.assertIn('To Long Meadow, Colgate', note.body)
        self.assertEqual(note.url, reverse('horse_detail', args=[horse.pk]))

    def test_the_person_who_did_it_is_not_told(self):
        horse = self._arrive()
        centre.horses_moved('arrived', [horse], location=self.field, actor=self.groom)
        self.assertEqual(self._of(self.groom), [])
        self.assertEqual(len(self._of(self.boss)), 1)

    def test_same_event_twice_makes_one_notification(self):
        horse = self._arrive()
        centre.horses_moved('arrived', [horse], location=self.field)
        centre.horses_moved('arrived', [horse], location=self.field)
        self.assertEqual(len(self._of(self.groom)), 1)

    def test_several_horses_make_one_notification(self):
        horses = [self._arrive(name) for name in ('Ida', 'Bo', 'Cy', 'Di')]
        centre.horses_moved('departed', horses, location=self.field)
        note = self._of(self.groom)[0]
        self.assertEqual(note.title, '4 horses left the yard')
        self.assertIn('Ida, Bo, Cy and 1 more', note.body)

    def test_in_app_off_makes_nothing(self):
        NotificationPreference.objects.create(user=self.groom, category=MOVE, in_app=False)
        centre.horses_moved('arrived', [self._arrive()], location=self.field)
        self.assertEqual(self._of(self.groom), [])

    def test_email_when_asked_for(self):
        NotificationPreference.objects.create(user=self.groom, category=MOVE, in_app=True, email=True)
        with self.captureOnCommitCallbacks(execute=True):
            centre.horses_moved('arrived', [self._arrive()], location=self.field)
        self.assertEqual([m.to for m in mail.outbox], [['groom@example.com']])
        self.assertEqual(mail.outbox[0].subject, 'Ida arrived')

    def test_a_failure_never_breaks_the_caller(self):
        with mock.patch.object(centre, '_notify', side_effect=RuntimeError('boom')):
            self.assertEqual(centre.horses_moved('arrived', [self._arrive()], location=self.field), 0)

    def test_moving_a_horse_tells_the_yard_after_commit(self):
        horse = self._arrive()
        with self.captureOnCommitCallbacks(execute=True):
            PlacementService.move_horse(horse, new_location=self.other_field, move_date=timezone.localdate())
        self.assertEqual([n.title for n in self._of(self.groom)], ['Ida moved'])

    def test_departing_a_horse_tells_the_yard(self):
        horse = self._arrive()
        with self.captureOnCommitCallbacks(execute=True):
            PlacementService.depart_horse(horse, timezone.localdate())
        self.assertEqual([n.title for n in self._of(self.groom)], ['Ida left the yard'])

    def test_a_future_departure_is_not_a_movement(self):
        horse = self._arrive()
        with self.captureOnCommitCallbacks(execute=True):
            PlacementService.depart_horse(horse, timezone.localdate() + timedelta(days=5))
        self.assertEqual(self._of(self.groom), [])


class MoneyTests(CentreTestBase):

    def test_marking_paid_tells_people_who_see_invoices(self):
        invoice = _invoice(self.owner, 'INV-1')
        with self.captureOnCommitCallbacks(execute=True):
            invoice.mark_as_paid()
        notes = self._of(self.bookkeeper, MONEY)
        self.assertEqual([n.title for n in notes], ['Invoice INV-1 paid'])
        self.assertIn('Roz Tobin', notes[0].body)
        self.assertIn('£100.00', notes[0].body)
        self.assertEqual(self._of(self.groom), [])

    def test_paid_once_is_told_once(self):
        invoice = _invoice(self.owner, 'INV-1')
        with self.captureOnCommitCallbacks(execute=True):
            invoice.mark_as_paid()
            invoice.mark_as_paid()
        self.assertEqual(len(self._of(self.bookkeeper, MONEY)), 1)

    def test_new_overdue_invoices_are_told(self):
        from notifications.tasks import check_invoice_status
        _invoice(self.owner, 'INV-2', due_date=timezone.localdate() - timedelta(days=3))
        check_invoice_status()
        self.assertEqual([n.title for n in self._of(self.bookkeeper)], ['Invoice INV-2 is overdue'])

    def test_many_overdue_invoices_make_one_notification(self):
        from notifications.tasks import check_invoice_status
        for i in range(centre.GROUP_OVER + 1):
            _invoice(self.owner, f'INV-{i}', due_date=timezone.localdate() - timedelta(days=3))
        check_invoice_status()
        titles = [n.title for n in self._of(self.bookkeeper)]
        self.assertEqual(titles, [f'{centre.GROUP_OVER + 1} invoices are now overdue'])

    def test_xero_problems_go_to_people_who_see_xero(self):
        centre.xero_problem('the connection has expired', 'Reconnect it.')
        self.assertEqual(len(self._of(self.boss, MONEY)), 1)
        self.assertEqual(self._of(self.bookkeeper), [])


class ActionSyncTests(CentreTestBase):
    """Needs-action notifications follow the dashboard's attention list.
    An expected departure due today is the simplest row to make."""

    def setUp(self):
        super().setUp()
        self.horse = self._arrive()
        Placement.objects.filter(horse=self.horse).update(expected_departure=timezone.localdate())

    def test_sync_copies_the_list(self):
        centre.sync_actions(self.groom, force=True)
        notes = self._of(self.groom, A)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].title, 'Ida')
        self.assertTrue(notes[0].is_unread)
        self.assertEqual(notes[0].due_date, timezone.localdate())

    def test_a_role_without_horses_gets_no_horse_rows(self):
        centre.sync_actions(self.bookkeeper, force=True)
        self.assertEqual(self._of(self.bookkeeper, A), [])

    def test_a_second_sync_changes_nothing(self):
        centre.sync_actions(self.groom, force=True)
        first = self._of(self.groom, A)[0]
        centre.sync_actions(self.groom, force=True)
        self.assertEqual([n.pk for n in self._of(self.groom, A)], [first.pk])

    def test_done_rows_close_by_themselves(self):
        centre.sync_actions(self.groom, force=True)
        Placement.objects.filter(horse=self.horse).update(expected_departure=None)
        centre.sync_actions(self.groom, force=True)
        note = self._of(self.groom, A)[0]
        self.assertIsNotNone(note.resolved_at)
        self.assertEqual(centre.unread_count(self.groom), 0)

    def test_sync_is_throttled(self):
        self.assertTrue(centre.sync_actions(self.groom))
        self.assertFalse(centre.sync_actions(self.groom))
        centre.resync_soon(self.groom)
        self.assertTrue(centre.sync_actions(self.groom))

    def test_in_app_off_makes_no_rows(self):
        NotificationPreference.objects.create(user=self.groom, category=A, in_app=False)
        centre.sync_actions(self.groom, force=True)
        self.assertEqual(self._of(self.groom, A), [])

    def test_dismissed_rows_close_and_come_back_on_restore(self):
        from core.dashboard import attention, dismissals
        centre.sync_actions(self.boss, force=True)
        item = attention.collect(self.boss)[0]
        dismissals.dismiss([dismissals.token(item)], self.boss)
        centre.sync_actions(self.boss, force=True)
        self.assertEqual(centre.unread_count(self.boss), 0)
        dismissals.restore_all()
        centre.sync_actions(self.boss, force=True)
        self.assertEqual(centre.unread_count(self.boss), 1)

    def test_hourly_task_syncs_everyone(self):
        from notifications.tasks import sync_action_notifications
        sync_action_notifications()
        self.assertEqual(len(self._of(self.groom, A)), 1)
        self.assertEqual(len(self._of(self.boss, A)), 1)

    def test_morning_digest_goes_to_people_who_asked(self):
        from notifications.tasks import send_action_digests
        NotificationPreference.objects.create(user=self.groom, category=A, in_app=True, email=True)
        send_action_digests()
        self.assertEqual([m.to for m in mail.outbox], [['groom@example.com']])
        self.assertEqual(mail.outbox[0].subject, '1 thing needs doing today')
        self.assertIn('Ida', mail.outbox[0].alternatives[0][0])

    def test_digest_works_with_in_app_off(self):
        from notifications.tasks import send_action_digests
        NotificationPreference.objects.create(user=self.groom, category=A, in_app=False, email=True)
        send_action_digests()
        self.assertEqual(len(mail.outbox), 1)


class PurgeTests(CentreTestBase):

    def test_read_notifications_go_after_90_days(self):
        old = timezone.now() - timedelta(days=91)
        Notification.objects.create(recipient=self.groom, category=MOVE, kind='x', title='old', dedupe_key='a', read_at=old)
        Notification.objects.create(recipient=self.groom, category=MOVE, kind='x', title='unread', dedupe_key='b')
        Notification.objects.create(recipient=self.groom, category=MOVE, kind='x', title='recent', dedupe_key='c', read_at=timezone.now())
        self.assertEqual(centre.purge_old(), 1)
        self.assertEqual(sorted(n.title for n in self._of(self.groom)), ['recent', 'unread'])


class PanelViewTests(CentreTestBase):

    def setUp(self):
        super().setUp()
        self.client.force_login(self.groom)
        self.note = Notification.objects.create(
            recipient=self.groom, category=MOVE, kind='arrived', title='Ida arrived',
            url=reverse('horse_list'), dedupe_key='m1', tags='movement',
        )

    def test_signed_out_is_sent_to_sign_in(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('notification_panel')).status_code, 302)

    def test_panel_lists_new_notifications(self):
        response = self.client.get(reverse('notification_panel'))
        self.assertContains(response, 'Ida arrived')
        self.assertContains(response, 'Mark all as read')

    def test_chip_filters(self):
        response = self.client.get(reverse('notification_panel'), {'chip': 'money'})
        self.assertNotContains(response, 'Ida arrived')

    def test_someone_else_s_notifications_never_show(self):
        Notification.objects.create(recipient=self.boss, category=MOVE, kind='x', title='Secret', dedupe_key='s')
        self.assertNotContains(self.client.get(reverse('notification_panel'), {'tab': 'all'}), 'Secret')

    def test_badge_updates_both_counts(self):
        response = self.client.get(reverse('notification_badge'))
        self.assertContains(response, 'id="notif-count-side" hx-swap-oob="true" class="notif-count">1<')
        self.assertContains(response, 'id="notif-count-phone"')

    def test_badge_hides_at_zero(self):
        centre.mark_read(self.groom)
        response = self.client.get(reverse('notification_badge'))
        self.assertContains(response, 'class="notif-count hidden"')

    def test_mark_one_read(self):
        response = self.client.post(reverse('notification_read'), {'id': self.note.pk})
        self.assertEqual(response['HX-Trigger'], 'notif:changed')
        self.note.refresh_from_db()
        self.assertIsNotNone(self.note.read_at)

    def test_mark_all_read(self):
        Notification.objects.create(recipient=self.groom, category=MOVE, kind='x', title='two', dedupe_key='m2')
        self.client.post(reverse('notification_read'))
        self.assertEqual(centre.unread_count(self.groom), 0)

    def test_cannot_mark_someone_else_s_read(self):
        theirs = Notification.objects.create(recipient=self.boss, category=MOVE, kind='x', title='t', dedupe_key='t')
        self.client.post(reverse('notification_read'), {'id': theirs.pk})
        theirs.refresh_from_db()
        self.assertIsNone(theirs.read_at)

    def test_open_marks_read_and_goes_there(self):
        response = self.client.get(reverse('notification_open', args=[self.note.pk]))
        self.assertRedirects(response, reverse('horse_list'), fetch_redirect_response=False)
        self.note.refresh_from_db()
        self.assertIsNotNone(self.note.read_at)

    def test_open_never_goes_off_site(self):
        self.note.url = 'https://evil.example.com/'
        self.note.save()
        response = self.client.get(reverse('notification_open', args=[self.note.pk]))
        self.assertEqual(response['Location'], '/')

    def test_open_someone_else_s_is_404(self):
        theirs = Notification.objects.create(recipient=self.boss, category=MOVE, kind='x', title='t', dedupe_key='t')
        self.assertEqual(self.client.get(reverse('notification_open', args=[theirs.pk])).status_code, 404)

    def test_only_administrators_dismiss(self):
        self.assertEqual(self.client.post(reverse('notification_dismiss'), {'item': 'x|'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('notification_restore')).status_code, 403)

    def test_administrator_dismiss(self):
        self.client.force_login(self.boss)
        self.client.post(reverse('notification_dismiss'), {'item': 'departure-expected-1|2026-01-01'})
        self.assertEqual(AttentionDismissal.objects.count(), 1)


class SidebarAndProfileTests(CentreTestBase):

    def test_every_page_has_the_bell_and_the_drawer(self):
        self.client.force_login(self.groom)
        html = self.client.get(reverse('horse_list')).content.decode()
        self.assertIn('id="notif-drawer"', html)
        self.assertIn('id="notif-count-side"', html)
        self.assertIn('id="notif-count-phone"', html)
        self.assertIn(f'hx-get="{reverse("notification_badge")}"', html)

    def test_profile_notifications_tab_saves(self):
        self.client.force_login(self.groom)
        response = self.client.post(
            reverse('profile'), {'action': 'notifications', 'movement_in_app': 'on', 'movement_email': 'on'},
            HTTP_HX_REQUEST='true', HTTP_HX_TARGET='popup-body',
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(centre.preferences(self.groom)[MOVE], (True, True))
        self.assertEqual(centre.preferences(self.groom)[A], (False, False))

    def test_profile_shows_the_notifications_tab(self):
        self.client.force_login(self.groom)
        response = self.client.get(reverse('profile') + '?tab=notifications', HTTP_HX_REQUEST='true', HTTP_HX_TARGET='popup-body')
        self.assertContains(response, 'name="movement_email"')
        self.assertNotContains(response, 'name="money_email"')


class HourlyClaimTests(TestCase):

    @override_settings(HOURLY_SCHEDULED_TASKS={'some.task'})
    def test_hourly_tasks_claim_per_hour(self):
        from core.jobs import _run_key
        self.assertEqual(_run_key('some.task'), timezone.localtime().strftime('%Y-%m-%dT%H'))
        self.assertEqual(_run_key('other.task'), timezone.localdate().isoformat())
