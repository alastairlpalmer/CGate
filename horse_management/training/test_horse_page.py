"""Training on the horse page, the list preview and the bulk bar
(docs/TRAINING_MODULE_PLAN.md, 6 "Horse page history", 8.4, 8.5)."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse
from core.roles_testutils import make_user_with_access
from health.models import Vaccination, VaccinationType

from .horse import timeline_activities
from .models import Activity, WorkType

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class HorsePageTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.horse = Horse.objects.create(name='Ballybrook')
        self.client.force_login(make_user_with_access('t', training='full', horses='view', health='view'))

    def test_coming_up_holds_later_plans_and_the_timeline_does_not(self):
        later = Activity.objects.create(horse=self.horse, date=self.today + timedelta(days=1),
                                        work_type=self.canter, status='planned')
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        # A plan alone does not show the Training card, and the page builds
        # the card only when it shows, so ask for the card itself.
        from training.horse import training_card

        card = training_card(response.wsgi_request.user, self.horse)
        self.assertEqual(card['coming_up'], [later])
        self.assertFalse([e for e in response.context['timeline_events'] if e['type'] == 'training'])

    def test_timeline_holds_at_most_10_rows_and_the_preview_5(self):
        for i in range(30):
            Activity.objects.create(horse=self.horse, date=self.today - timedelta(days=i),
                                    work_type=self.canter, status='done')
        jab = VaccinationType.objects.create(name='Flu', interval_months=12)
        Vaccination.objects.create(horse=self.horse, vaccination_type=jab,
                                   date_given=self.today - timedelta(days=200))
        # The horse page has the Training card, so its Timeline does not
        # repeat the rows (horse page review A3); the rule still holds 10.
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertFalse([e for e in response.context['timeline_events'] if e['type'] == 'training'])
        self.assertEqual(len(timeline_activities(response.wsgi_request.user, self.horse, 10)), 10)
        response = self.client.get(reverse('horse_preview', args=[self.horse.pk]),
                                   HTTP_HX_REQUEST='true', HTTP_HX_TARGET='horse-preview-body')
        events = response.context['timeline_events']
        self.assertEqual(len([e for e in events if e['type'] == 'training']), 5)
        self.assertTrue([e for e in events if e['type'] == 'vaccination'])

    def test_planned_rows_never_show_on_the_timeline(self):
        Activity.objects.create(horse=self.horse, date=self.today - timedelta(days=1),
                                work_type=self.canter, status='planned')
        Activity.objects.create(horse=self.horse, date=self.today - timedelta(days=2),
                                work_type=self.canter, status='not_done', reason='weather')
        response = self.client.get(reverse('horse_preview', args=[self.horse.pk]),
                                   HTTP_HX_REQUEST='true', HTTP_HX_TARGET='horse-preview-body')
        statuses = [e['obj'].status for e in response.context['timeline_events'] if e['type'] == 'training']
        self.assertEqual(statuses, ['not_done'])
        self.assertContains(self.client.get(reverse('horse_detail', args=[self.horse.pk])), 'Not confirmed')


class BulkTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.horses = [Horse.objects.create(name=f'Horse {i}') for i in range(5)]
        self.horses[2].is_active = False
        self.horses[2].save()
        self.client.force_login(make_user_with_access('t', training='full', horses='view'))

    def test_bulk_form_names_the_horses_and_the_skipped_one(self):
        response = self.client.get(reverse('bulk_health_form'), {
            'action_type': 'training', 'horse_ids': [h.pk for h in self.horses],
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Record for 4 horses')
        self.assertContains(response, 'Horse 2')

    def test_bulk_apply_makes_one_record_for_each_active_horse(self):
        response = self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'training', 'horse_ids': [h.pk for h in self.horses],
            'date': self.today.isoformat(), 'work_type': self.canter.pk, 'status': 'done',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(Activity.objects.count(), 4)
        self.assertFalse(Activity.objects.filter(horse=self.horses[2]).exists())
        self.assertEqual(set(Activity.objects.values_list('source', flat=True)), {'bulk'})
        response = self.client.get(reverse('training_log'))
        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('Horse 2' in m for m in messages), messages)

    def test_bulk_is_refused_without_full_training(self):
        self.client.force_login(make_user_with_access('h', horses='full', health='full', training='view'))
        response = self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'training', 'horse_ids': [self.horses[0].pk],
            'date': self.today.isoformat(), 'work_type': self.canter.pk, 'status': 'done',
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Activity.objects.exists())

    def test_health_bulk_actions_still_work(self):
        self.client.force_login(make_user_with_access('h2', horses='view', health='full'))
        response = self.client.get(reverse('bulk_health_form'), {
            'action_type': 'condition', 'horse_ids': [self.horses[0].pk],
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
