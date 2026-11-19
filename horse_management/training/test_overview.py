"""The Training Overview, phase A (docs/TRAINING_DASHBOARD_PLAN.md):
the figures, Today, Needs attention, Not in work, the review date, the
large-yard folds, the Stable Staff migration and the dashboard item."""

import importlib
from datetime import timedelta
from types import SimpleNamespace

from django.apps import apps as django_apps
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Role
from core.roles_testutils import assign_role, make_user_with_access
from health.models import MedicalCondition, VetVisit

from . import attention, board, overview, services
from .models import Activity, HorseTraining, Lot, LotDay, TrainingSettings, WorkType

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}
stable_staff = importlib.import_module('training.migrations.0009_stable_staff_training')


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.first = Lot.objects.create(name='1st lot', usual_work_type=self.canter, position=0)
        self.second = Lot.objects.create(name='2nd lot', usual_work_type=self.canter, position=1)
        self.user = make_user_with_access('trainer', training='full', horses='view', health='view')
        self.client.force_login(self.user)
        TrainingSettings.load().save()   # the yard chose a board source

    def horses(self, names, lot=None):
        made = [Horse.objects.create(name=n) for n in names]
        services.add_to_lot(lot or self.first, made)
        return made

    def rest(self, name, status=HorseTraining.Status.BOX_REST, days=3, review_on=None):
        horse = Horse.objects.create(name=name)
        services.set_status(horse, status, self.today - timedelta(days=days), review_on=review_on)
        return horse

    def page(self, **params):
        return self.client.get(reverse('training_home'), params)


class PageTests(Base):
    def test_no_board_shows_the_start_form(self):
        self.horses(['Ballybrook'])
        response = self.page()
        self.assertContains(response, "board is not started.")
        self.assertContains(response, reverse('training_board_start', args=[self.today.isoformat()]))
        self.assertContains(response, 'Board not started')

    def test_view_role_sees_no_buttons_that_change(self):
        self.client.force_login(make_user_with_access('viewer', training='view'))
        self.rest('Juniper')
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse('training_board_start', args=[self.today.isoformat()]))
        self.assertNotContains(response, reverse('training_horse_edit', args=[Horse.objects.get(name='Juniper').pk]))
        self.assertNotContains(response, 'Record training')

    def test_hidden_role_gets_403(self):
        self.client.force_login(make_user_with_access('nobody', horses='view'))
        self.assertIn(self.page().status_code, (302, 403))

    def test_today_counts_and_not_going_out(self):
        ballybrook, red_kite, _ = self.horses(['Ballybrook', 'Red Kite', 'Juniper'])
        board.start_board(self.today, user=self.user, source='lots')
        Activity.objects.filter(horse=red_kite, date=self.today).update(
            status=Activity.Status.NOT_DONE, reason=Activity.Reason.UNSOUND,
        )
        response = self.page()
        lot_day = response.context['today_data']['lot_days'][0]
        self.assertEqual((lot_day.total, lot_day.planned, lot_day.not_done), (3, 2, 1))
        self.assertContains(response, 'Not going out today')
        self.assertContains(response, 'Lame or sore')
        self.assertEqual(response.context['today_data']['done_count'], 0)

    def test_no_price_shows(self):
        self.horses(['Ballybrook'])
        board.start_board(self.today, user=self.user, source='lots')
        self.rest('Juniper')
        self.assertNotContains(self.page(), '£')

    def test_overview_is_the_first_tab(self):
        response = self.page()
        content = response.content.decode()
        self.assertLess(content.index('>Overview</a>'), content.index('>Board</a>'))
        self.assertContains(response, 'aria-current="page">Overview')


class NotInWorkTests(Base):
    def test_badges(self):
        self.rest('Box no date', HorseTraining.Status.BOX_REST, days=3)
        self.rest('Resting short', HorseTraining.Status.RESTING, days=5)
        self.rest('Resting long', HorseTraining.Status.RESTING, days=40)
        self.rest('Due today', HorseTraining.Status.RESTING, days=10, review_on=self.today)
        self.rest('Overdue', HorseTraining.Status.RESTING, days=10, review_on=self.today - timedelta(days=3))
        self.rest('Later', HorseTraining.Status.BOX_REST, days=10, review_on=self.today + timedelta(days=20))
        rest = self.page().context['rest']
        badges = {r.horse.name: r.badge[1] for r in rest['action']}
        self.assertEqual(badges, {
            'Overdue': 'Review overdue 3 days',
            'Due today': 'Review due today',
            'Box no date': 'No review date',
            'Resting long': 'No review date',
        })
        self.assertEqual([r.horse.name for r in rest['action']][:2], ['Overdue', 'Due today'])
        others = {label: [r.horse.name for r in rows] for label, rows in rest['others']}
        self.assertEqual(others, {'Box rest': ['Later'], 'Resting': ['Resting short']})
        self.assertEqual(rest['count'], 6)

    def test_not_in_training_does_not_show(self):
        # T6: broodmares, youngstock and retired horses stay out.
        self.rest('Broodmare', HorseTraining.Status.NOT_IN_TRAINING)
        self.assertEqual(self.page().context['rest']['count'], 0)

    def test_health_facts_need_health_access(self):
        horse = self.rest('Juniper', HorseTraining.Status.RESTING, days=2, review_on=self.today + timedelta(days=30))
        MedicalCondition.objects.create(horse=horse, name='Tendon', status=MedicalCondition.Status.ACTIVE)
        VetVisit.objects.create(horse=horse, date=self.today, reason='Scan', follow_up_date=self.today + timedelta(days=2))
        response = self.page()
        self.assertContains(response, 'Tendon')
        self.assertContains(response, 'Vet follow-up')
        self.client.force_login(make_user_with_access('staff', training='full'))
        response = self.page()
        self.assertNotContains(response, 'Tendon')
        self.assertNotContains(response, 'Vet follow-up')

    def test_review_date_saved_from_the_status_popup_and_cleared_in_work(self):
        horse = self.rest('Juniper', HorseTraining.Status.BOX_REST, days=3)
        url = reverse('training_horse_edit', args=[horse.pk])
        later = self.today + timedelta(days=14)
        response = self.client.post(url, {
            'status': 'box_rest', 'status_since': (self.today - timedelta(days=3)).isoformat(),
            'review_on': later.isoformat(), 'lot': '', 'notes': '',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(HorseTraining.objects.get(horse=horse).review_on, later)
        services.set_status(horse, HorseTraining.Status.IN_WORK, self.today)
        self.assertIsNone(HorseTraining.objects.get(horse=horse).review_on)

    def test_set_status_without_review_keeps_it(self):
        later = self.today + timedelta(days=5)
        horse = self.rest('Juniper', HorseTraining.Status.RESTING, review_on=later)
        services.set_status(horse, HorseTraining.Status.BOX_REST, self.today)
        self.assertEqual(HorseTraining.objects.get(horse=horse).review_on, later)


class AttentionTests(Base):
    def test_no_work_horse_and_review_due_show(self):
        (kestrel,) = self.horses(['Kestrel'])
        HorseTraining.objects.filter(horse=kestrel).update(status_since=self.today - timedelta(days=10))
        self.rest('Juniper', HorseTraining.Status.RESTING, review_on=self.today)
        data = self.page().context['attention']
        kinds = {g.kind: [e.name for e in g.entries] for g in data['groups']}
        self.assertEqual(kinds['no_work'], ['Kestrel'])
        self.assertEqual(kinds['review'], ['Juniper'])
        self.assertFalse(data['grouped'])

    def test_many_rows_group_by_kind(self):
        made = self.horses([f'Horse {i:02}' for i in range(10)])
        HorseTraining.objects.filter(horse__in=made).update(status_since=self.today - timedelta(days=10))
        response = self.page()
        self.assertTrue(response.context['attention']['grouped'])
        self.assertContains(response, '10 horses')

    def test_unconfirmed_lot_shows_first(self):
        self.horses(['Ballybrook'])
        yesterday = self.today - timedelta(days=1)
        board.start_board(yesterday, user=self.user, source='lots')
        data = self.page().context['attention']
        self.assertEqual(data['groups'][0].kind, 'unconfirmed')
        self.assertEqual(data['unconfirmed_lots'], 1)

    def test_main_dashboard_gets_review_due(self):
        self.rest('Juniper', HorseTraining.Status.RESTING, review_on=self.today - timedelta(days=1))
        self.rest('Pebble', HorseTraining.Status.RESTING, review_on=self.today + timedelta(days=4))
        ctx = SimpleNamespace(today=self.today, placements={})
        items = [i for i in attention.collect(ctx) if i.kind == 'training_review']
        self.assertEqual([(i.title, i.severity) for i in items], [('Juniper', 'overdue')])


class LargeYardTests(Base):
    def test_small_yard_has_no_lot_filter_and_no_folds(self):
        self.horses(['Ballybrook'])
        self.rest('Juniper', HorseTraining.Status.RESTING, days=2)
        response = self.page()
        self.assertFalse(response.context['large'])
        self.assertNotContains(response, 'All lots')
        self.assertFalse(response.context['rest']['fold'])

    def test_large_yard_gets_lot_filter_and_folds(self):
        self.horses([f'A {i:02}' for i in range(15)], self.first)
        self.horses([f'B {i:02}' for i in range(15)], self.second)
        self.rest('Juniper', HorseTraining.Status.RESTING, days=2)
        response = self.page()
        self.assertTrue(response.context['large'])
        self.assertContains(response, 'All lots')
        self.assertTrue(response.context['rest']['fold'])
        self.assertEqual(response.context['in_work_total'], 30)
        response = self.page(lot=self.second.pk)
        self.assertEqual(response.context['in_work_total'], 15)
        self.assertEqual(response.context['lot'], self.second)

    def test_queries_do_not_grow_with_the_yard(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.page()
            return len(ctx.captured_queries)
        # Both sizes are large yards, so both show the lot filter (4.3).
        self.horses([f'A {i:02}' for i in range(30)])
        self.rest('R0', HorseTraining.Status.RESTING, review_on=self.today)
        board.start_board(self.today, user=self.user, source='lots')
        small = count()
        for lot in (self.first, self.second):
            self.horses([f'{lot.pk} {i:02}' for i in range(40)], lot)
        for i in range(10):
            self.rest(f'R{i + 1}', HorseTraining.Status.BOX_REST)
        Activity.objects.all().delete()
        from .models import LotDay
        LotDay.objects.all().delete()
        board.start_board(self.today, user=self.user, source='lots')
        self.assertEqual(count(), small)
        self.assertLessEqual(small, 40)


class StableStaffMigrationTests(TestCase):
    def run_migration(self):
        stable_staff.give_training(django_apps, None)

    def test_match_by_name_any_case(self):
        role = Role.objects.create(name='  stable STAFF ', access={'horses': 'view'})
        self.run_migration()
        role.refresh_from_db()
        self.assertEqual(role.access, {'horses': 'view', 'training': 'full'})

    def test_chosen_value_stays(self):
        role = Role.objects.create(name='Stable Staff', access={'training': 'view'})
        self.run_migration()
        role.refresh_from_db()
        self.assertEqual(role.access, {'training': 'view'})

    def test_no_match_changes_nothing(self):
        role = Role.objects.create(name='Stable hands', access={'horses': 'view'})
        self.run_migration()
        role.refresh_from_db()
        self.assertEqual(role.access, {'horses': 'view'})

    def test_stable_staff_opens_the_overview_and_marks_a_lot_done(self):
        role = Role.objects.create(name='Stable Staff', access={'horses': 'view', 'dashboard': 'full'})
        self.run_migration()
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user(username='groom', password='pw')
        assign_role(user, Role.objects.get(pk=role.pk))
        self.client.force_login(user)
        canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        lot = Lot.objects.create(name='1st lot', usual_work_type=canter)
        services.add_to_lot(lot, [Horse.objects.create(name='Ballybrook')])
        today = timezone.localdate()
        board.start_board(today, user=user, source='lots')
        self.assertEqual(self.client.get(reverse('training_home')).status_code, 200)
        lot_day = lot.days.get(date=today)
        self.client.post(reverse('training_lot_day_action', args=[lot_day.pk, 'done']))
        lot_day.refresh_from_db()
        self.assertEqual(lot_day.status, 'done')


class RestBadgeTests(TestCase):
    def test_vet_follow_up_words(self):
        today = timezone.localdate()
        row = SimpleNamespace(review_on=today + timedelta(days=30), status='resting', status_since=today)
        self.assertEqual(overview.rest_badge(row, today, today + timedelta(days=1))[1], 'Vet follow-up tomorrow')
        self.assertEqual(overview.rest_badge(row, today, today - timedelta(days=1))[1], 'Vet follow-up overdue')
        self.assertIsNone(overview.rest_badge(row, today, None))


class WeekTests(Base):
    """Phase B: This week, Tomorrow, Riders today."""

    def test_week_cells_match_the_horse_page(self):
        (ballybrook,) = self.horses(['Ballybrook'])
        board.start_board(self.today, user=self.user, source='lots')
        board.mark_lot_done(self.first.days.get(date=self.today), user=self.user, today=self.today)
        week = self.page().context['week']
        self.assertFalse(week['summary_rows'])
        horse = week['groups'][0]['horses'][0]
        cell = horse['cells'][self.today.weekday()]
        self.assertEqual(cell['tone'], 'steady')
        self.assertEqual(horse['days'], 1)
        from .load import rows_for, strip
        self.assertEqual(cell, strip(rows_for([ballybrook.pk], self.today)[ballybrook.pk], self.today)[-1])

    def test_groups_follow_lots_and_horses_with_no_lot_come_last(self):
        self.horses(['Ballybrook'], self.second)
        self.horses(['Kestrel'], self.first)
        loose = Horse.objects.create(name='Loose')
        services.set_status(loose, HorseTraining.Status.IN_WORK, self.today)
        names = [g['name'] for g in self.page().context['week']['groups']]
        self.assertEqual(names, ['1st lot', '2nd lot', 'Not in a lot'])

    def test_large_yard_gets_lot_summary_rows(self):
        made = self.horses([f'A {i:02}' for i in range(30)])
        board.start_board(self.today - timedelta(days=1), user=self.user, source='lots')
        Activity.objects.filter(horse__in=made[:5], date=self.today - timedelta(days=1)).update(
            status=Activity.Status.NOT_DONE, reason=Activity.Reason.WEATHER,
        )
        Activity.objects.filter(horse__in=made[5:]).update(status=Activity.Status.DONE)
        week = self.page().context['week']
        self.assertTrue(week['summary_rows'])
        group = week['groups'][0]
        self.assertEqual(len(group['shown']), 10)
        self.assertEqual(group['more'], 20)
        if self.today.weekday() > 0:
            yesterday = group['summary'][self.today.weekday() - 1]
            self.assertEqual((yesterday['text'], yesterday['tone']), ('25/30', 'low'))
        self.assertEqual(group['summary'][self.today.weekday()]['tone'], 'today')
        # A lot chip opens the whole lot.
        week = self.page(lot=self.first.pk).context['week']
        self.assertFalse(week['summary_rows'])
        self.assertEqual(len(week['groups'][0]['shown']), 30)

    def test_tomorrow_not_started_offers_start(self):
        self.horses(['Ballybrook'])
        response = self.page()
        self.assertContains(response, reverse('training_board_start', args=[(self.today + timedelta(days=1)).isoformat()]))
        self.assertContains(response, 'Start from the lots')
        board.start_board(self.today + timedelta(days=1), user=self.user, source='lots')
        tomorrow = self.page().context['tomorrow']
        self.assertEqual(tomorrow['horses'], 1)

    def test_riders_today(self):
        from .models import Rider
        sam = Rider.objects.create(name='Sam')
        made = self.horses(['Ballybrook', 'Red Kite', 'Juniper'])
        board.start_board(self.today, user=self.user, source='lots')
        Activity.objects.filter(horse__in=made[:2], date=self.today).update(rider=sam)
        Activity.objects.filter(horse=made[2], date=self.today).update(rider_is_owner=True)
        riders = self.page().context['today_data']['riders']
        self.assertEqual(riders, [('Sam', ['Ballybrook', 'Red Kite']), ('The owner', ['Juniper'])])


class ChecklistTests(Base):
    """Phase C: the first-run checklist (plan section 5)."""

    def test_new_yard_sees_the_checklist_in_place_of_the_figures(self):
        TrainingSettings.objects.all().delete()
        response = self.page()
        steps = {step['key']: step['done'] for step in response.context['setup_steps']}
        self.assertEqual(steps, {'work_types': True, 'lots': True, 'horses': False, 'source': False, 'board': False})
        self.assertContains(response, 'Set up Training')
        self.assertNotContains(response, 'metric-strip')

    def test_checklist_goes_when_steps_two_to_four_are_done(self):
        self.horses(['Ballybrook'])
        response = self.page()
        self.assertIsNone(response.context['setup_steps'])
        self.assertContains(response, 'metric-strip')
        self.assertFalse(LotDay.objects.exists())

    def test_a_started_board_counts_as_a_chosen_source(self):
        TrainingSettings.objects.all().delete()
        self.horses(['Ballybrook'])
        board.start_board(self.today, user=self.user, source='lots')
        self.assertIsNone(self.page().context['setup_steps'])

class HeaderTests(Base):
    """Phase C: one header on every tab, with one main button (C1, C2)."""

    def test_each_tab_has_the_header_and_its_main_button(self):
        pages = {
            'training_home': "Start today's board",
            'training_board': 'Record training',
            'training_lots': 'Add lot',
            'training_log': 'Record training',
            'training_setup': 'Add work type',
        }
        for name, button in pages.items():
            with self.subTest(name):
                response = self.client.get(reverse(name))
                self.assertTemplateUsed(response, 'training/_header.html')
                self.assertContains(response, button)
