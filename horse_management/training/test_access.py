"""Training phase 1: the switch, access levels, the Trainer role, and where
Training shows (docs/TRAINING_MODULE_PLAN.md, 4.3, 5.7, 8.0 and 9.1)."""

import importlib
from datetime import timedelta

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.help import GUIDES_BY_SLUG
from core.models import Horse, Owner, Role
from core.roles_testutils import administrator_role, assign_role, make_user_with_access, viewer_role

from .models import Activity, WorkType

User = get_user_model()
seed = importlib.import_module('training.migrations.0003_seed_trainer_role')


def make_trainer(username='trainer'):
    seed.seed_trainer_role(django_apps, None)
    user = User.objects.create_user(username=username, password='pw')
    assign_role(user, Role.objects.get(name='Trainer'))
    return user


class SwitchTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='pw')
        assign_role(self.admin, administrator_role())
        self.client.force_login(self.admin)

    @override_settings(TRAINING_ENABLED=False)
    def test_switched_off_hides_everything(self):
        self.assertEqual(self.client.get(reverse('training_log')).status_code, 404)
        self.assertEqual(self.client.get(reverse('training_home')).status_code, 404)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'href="/training/"')
        self.assertFalse(GUIDES_BY_SLUG['training'].visible_to(self.admin))

    def test_switched_on_shows_the_sidebar_item_and_the_guide(self):
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'href="/training/"')
        self.assertContains(response, '#i-stopwatch')
        self.assertTrue(GUIDES_BY_SLUG['training'].visible_to(self.admin))


class LevelTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.horse = Horse.objects.create(name='Ballybrook')
        self.row = Activity.objects.create(horse=self.horse, date=self.today, work_type=self.canter, status='done')

    def test_hidden_role_sees_no_training_on_the_horse_page(self):
        self.client.force_login(make_user_with_access('h', horses='view'))
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertIsNone(response.context['training_card'])
        self.assertFalse([e for e in response.context['timeline_events'] if e['type'] == 'training'])
        self.assertNotContains(response, 'Training for Ballybrook')
        self.assertEqual(self.client.get(reverse('training_log')).status_code, 302)

    def test_view_role_sees_the_card_and_log_but_cannot_change(self):
        self.client.force_login(make_user_with_access('v', horses='view', training='view'))
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertIsNotNone(response.context['training_card'])
        self.assertFalse(response.context['training_card']['can_change'])
        # The card shows the rows; the Timeline does not repeat them (horse page review A3).
        self.assertFalse([e for e in response.context['timeline_events'] if e['type'] == 'training'])
        self.assertNotContains(response, 'Record training')
        self.assertEqual(self.client.get(reverse('training_log')).status_code, 200)
        response = self.client.post(reverse('training_activity_mark', args=[self.row.pk]), {'action': 'done'})
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse('training_activity_create'), {})
        self.assertEqual(response.status_code, 403)


class TrainerRoleTests(TestCase):
    def test_seed_makes_the_role_with_exact_levels_and_gives_viewer_training(self):
        viewer = viewer_role()
        viewer.access = {k: v for k, v in viewer.access.items() if k != 'training'}
        viewer.save()
        seed.seed_trainer_role(django_apps, None)
        trainer = Role.objects.get(name='Trainer')
        self.assertEqual(trainer.access, seed.TRAINER_ACCESS)
        resolved = trainer.resolved_access()
        for key in ('dashboard', 'horses', 'locations', 'health', 'breeding', 'feed', 'training'):
            self.assertEqual(resolved[key], 'full', key)
        for key in ('owners', 'finances', 'invoices', 'costs', 'charges', 'xero', 'settings',
                    'users', 'farrier_portal', 'cvet'):
            self.assertEqual(resolved[key], 'hidden', key)
        viewer.refresh_from_db()
        self.assertEqual(viewer.access['training'], 'view')

    def test_a_yard_role_called_trainer_is_kept_and_a_viewer_choice_is_kept(self):
        Role.objects.create(name='Trainer', access={'training': 'view'})
        viewer = viewer_role()
        viewer.access = dict(viewer.access, training='full')
        viewer.save()
        seed.seed_trainer_role(django_apps, None)
        self.assertEqual(Role.objects.get(name='Trainer').access, {'training': 'view'})
        viewer.refresh_from_db()
        self.assertEqual(viewer.access['training'], 'full')

    def test_reverse_removes_the_role_only_when_nobody_holds_it(self):
        user = make_trainer()
        seed.unseed_trainer_role(django_apps, None)
        self.assertTrue(Role.objects.filter(name='Trainer').exists())
        user.role_assignment.delete()
        seed.unseed_trainer_role(django_apps, None)
        self.assertFalse(Role.objects.filter(name='Trainer').exists())


class TrainerUseTests(TestCase):
    def setUp(self):
        self.trainer = make_trainer()
        self.client.force_login(self.trainer)
        self.horse = Horse.objects.create(name='Ballybrook')
        self.owner = Owner.objects.create(name='Jo Bloggs')
        WorkType.objects.create(name='Canter', kind='ridden', effort=2)

    def test_sidebar_has_the_yard_areas_and_not_owners_money_or_settings(self):
        response = self.client.get(reverse('dashboard'))
        for url in ('horse_list', 'location_list', 'health_dashboard', 'farrier_list',
                    'breeding_list', 'feed_dashboard', 'training_home'):
            self.assertContains(response, f'href="{reverse(url)}"', msg_prefix=url)
        for url in ('owner_list', 'invoice_list', 'charge_list', 'finances'):
            self.assertNotContains(response, f'href="{reverse(url)}"', msg_prefix=url)

    def test_record_card_bulk_bar_and_add_menu(self):
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertContains(response, 'Training for Ballybrook')
        self.assertContains(response, 'Vaccination for Ballybrook')
        self.assertNotContains(response, 'Other charge for Ballybrook')
        self.assertContains(response, 'Training activity')
        response = self.client.get(reverse('horse_list'))
        self.assertContains(response, '<option value="training">Record Training</option>', html=True)
        self.assertNotContains(response, '<option value="charge">', html=False)

    def test_health_records_have_no_cost_for_a_trainer(self):
        response = self.client.get(reverse('vaccination_create') + f'?horse={self.horse.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('cost', response.context['form'].fields)

    def test_trainer_is_refused_owner_charge_and_settings_forms(self):
        self.assertEqual(self.client.post(reverse('owner_create'), {'name': 'X'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('charge_create'), {}).status_code, 403)
        self.assertEqual(self.client.get(reverse('owner_detail', args=[self.owner.pk])).status_code, 302)

    def test_trainer_records_training(self):
        response = self.client.post(reverse('training_activity_create'), {
            'horse': self.horse.pk, 'date': (timezone.localdate() - timedelta(days=1)).isoformat(),
            'work_type': WorkType.objects.get().pk, 'status': 'done',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Activity.objects.count(), 1)
