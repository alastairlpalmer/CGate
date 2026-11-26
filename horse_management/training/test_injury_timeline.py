"""An injury on the timelines (docs/TRAINING_BOARD_REDESIGN_PLAN.md, 9.5):
conditions and training status changes on the horse page's Timeline, and
status changes, conditions and vet visits in the training profile's list."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Horse
from core.roles_testutils import make_user_with_access
from core.views.horses import build_horse_timeline, timeline_filters
from health.models import MedicalCondition, VetVisit

from . import horse as training_horse
from . import services
from .models import Activity, HorseTraining, WorkType


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.kestrel = Horse.objects.create(name='Kestrel')
        self.user = make_user_with_access('trainer', training='full', horses='view', health='full')
        self.client.force_login(self.user)

    def day(self, back):
        return self.today - timedelta(days=back)

    def injure(self):
        """In work, then lame: a condition, a vet visit, box rest, back in work."""
        services.set_status(self.kestrel, HorseTraining.Status.IN_WORK, self.day(30))
        Activity.objects.create(horse=self.kestrel, date=self.day(10), work_type=self.canter,
                                status='not_done', reason='unsound', notes='Short behind.')
        self.condition = MedicalCondition.objects.create(
            horse=self.kestrel, name='Tendon, near fore', diagnosed_date=self.day(10), notes='Heat and swelling.')
        self.visit = VetVisit.objects.create(
            horse=self.kestrel, date=self.day(9), reason='Lame near fore', diagnosis='Tendon strain',
            treatment='Box rest, cold hosing', follow_up_date=self.day(-5), cost=85)
        services.set_status(self.kestrel, HorseTraining.Status.BOX_REST, self.day(9),
                            notes='Scan on review.', review_on=self.day(-5))


class StatusChangeTests(Base):
    def test_each_change_of_status_is_one_entry_oldest_first(self):
        self.injure()
        services.set_status(self.kestrel, HorseTraining.Status.LIGHT, self.day(1))
        changes = training_horse.status_changes(self.kestrel)
        self.assertEqual([(c.label, c.date, c.is_rest) for c in changes], [
            ('In work', self.day(30), False), ('Box rest', self.day(9), True), ('Light work', self.day(1), False),
        ])
        self.assertEqual((changes[1].notes, changes[1].review_on), ('Scan on review.', self.day(-5)))

    def test_a_save_that_keeps_the_status_is_not_a_change(self):
        services.set_status(self.kestrel, HorseTraining.Status.BOX_REST, self.day(9))
        # A new note, a review date and a corrected start date: three saves, one status.
        services.set_status(self.kestrel, HorseTraining.Status.BOX_REST, notes='Scan booked.')
        services.set_status(self.kestrel, HorseTraining.Status.BOX_REST, review_on=self.day(-3))
        services.set_status(self.kestrel, HorseTraining.Status.BOX_REST, since=self.day(11))
        changes = training_horse.status_changes(self.kestrel)
        self.assertEqual(len(changes), 1)
        self.assertEqual((changes[0].date, changes[0].notes, changes[0].review_on),
                         (self.day(11), 'Scan booked.', self.day(-3)))

    def test_the_same_status_for_the_same_day_shows_one_time(self):
        services.set_status(self.kestrel, HorseTraining.Status.IN_WORK, self.day(5))
        services.set_status(self.kestrel, HorseTraining.Status.BOX_REST, self.day(3))
        services.set_status(self.kestrel, HorseTraining.Status.IN_WORK, self.day(5))     # undone: back as it was
        changes = training_horse.status_changes(self.kestrel)
        self.assertEqual([(c.label, c.date) for c in changes], [('Box rest', self.day(3)), ('In work', self.day(5))])

    def test_a_horse_that_was_never_in_training_has_no_change(self):
        services.set_days_per_week(self.kestrel, 5)      # makes the row, with Not in training
        self.assertEqual(training_horse.status_changes(self.kestrel), [])
        self.assertEqual(training_horse.status_changes(Horse.objects.create(name='Wren')), [])

    def test_a_role_that_cannot_see_training_gets_none(self):
        self.injure()
        nobody = make_user_with_access('office', horses='view')
        self.assertEqual(training_horse.timeline_status_changes(nobody, self.kestrel), [])
        self.assertEqual(len(training_horse.timeline_status_changes(self.user, self.kestrel)), 2)
        self.assertEqual(training_horse.timeline_status_changes(self.user, self.kestrel, 1)[0].label, 'Box rest')


class HorseTimelineTests(Base):
    def test_the_timeline_takes_a_condition_and_a_status_change(self):
        self.injure()
        undated = MedicalCondition.objects.create(horse=self.kestrel, name='Sweet itch')
        events = build_horse_timeline(
            placements=[], vaccinations=[], farrier_visits=[], worming_treatments=[], egg_counts=[],
            vet_visits=[self.visit], conditions=[self.condition, undated],
            status_changes=training_horse.status_changes(self.kestrel),
        )
        by_type = {}
        for event in events:
            by_type.setdefault(event['type'], []).append(event['date'])
        self.assertEqual(by_type['condition'], [self.today, self.day(10)])    # no diagnosis date: the day recorded
        self.assertEqual(by_type['training_status'], [self.day(9), self.day(30)])
        pills, _ = timeline_filters(events)
        self.assertEqual([(key, count) for key, _, count in pills],
                         [('vet_visit', 1), ('condition', 2), ('training', 2)])

    def test_the_horse_page_shows_the_injury(self):
        self.injure()
        response = self.client.get(reverse('horse_detail', args=[self.kestrel.pk]))
        types = [event['type'] for event in response.context['timeline_events']]
        self.assertEqual(types.count('condition'), 1)
        self.assertEqual(types.count('training_status'), 2)
        self.assertContains(response, 'Tendon, near fore')
        self.assertContains(response, 'Training status')
        self.assertContains(response, 'Scan on review.')
        self.assertContains(response, reverse('condition_update', args=[self.condition.pk]))

    def test_the_horse_page_hides_the_status_from_a_role_with_no_training(self):
        self.injure()
        self.client.force_login(make_user_with_access('office', horses='view'))
        response = self.client.get(reverse('horse_detail', args=[self.kestrel.pk]))
        types = [event['type'] for event in response.context['timeline_events']]
        self.assertNotIn('training_status', types)
        self.assertIn('condition', types)                # the page already shows conditions to this role

    @override_settings(TRAINING_ENABLED=False)
    def test_with_training_off_the_timeline_has_no_status(self):
        self.injure()
        response = self.client.get(reverse('horse_detail', args=[self.kestrel.pk]))
        self.assertNotIn('training_status', [e['type'] for e in response.context['timeline_events']])


class ProfileTests(Base):
    def url(self, **params):
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        return reverse('training_horse_profile', args=[self.kestrel.pk]) + (f'?{query}' if query else '')

    def kinds(self, **params):
        return [entry['kind'] for entry in self.client.get(self.url(**params)).context['entries']]

    def test_the_list_tells_the_story_in_order(self):
        self.injure()
        Activity.objects.create(horse=self.kestrel, date=self.day(12), work_type=self.canter, status='done')
        # Newest first: the vet visit and box rest (day 9), the condition and
        # the day off (day 10), the work before it, the start in work.
        self.assertEqual(sorted(self.kinds()[:2]), ['status', 'vet'])
        self.assertEqual(sorted(self.kinds()[2:4]), ['condition', 'work'])
        self.assertEqual(self.kinds()[4:], ['work', 'status'])

    def test_the_page_shows_the_condition_the_visit_and_the_rest(self):
        self.injure()
        response = self.client.get(self.url())
        for text in ('Tendon, near fore', 'Heat and swelling.', 'Lame near fore', 'Diagnosis: Tendon strain',
                     'Treatment: Box rest, cold hosing', 'Scan on review.', 'Health and rest'):
            self.assertContains(response, text)
        self.assertContains(response, reverse('condition_update', args=[self.condition.pk]))
        self.assertContains(response, reverse('vet_visit_update', args=[self.visit.pk]))
        self.assertNotContains(response, '85.00')        # no money on a Training page (D11)
        self.assertNotContains(response, '£')

    def test_health_and_rest_shows_what_happened_to_the_horse(self):
        self.injure()
        Activity.objects.create(horse=self.kestrel, date=self.day(12), work_type=self.canter, status='done')
        self.assertEqual(sorted(self.kinds(only='health')), ['condition', 'status', 'status', 'vet', 'work'])
        self.assertEqual(self.kinds(only='missed'), ['work'])
        self.assertEqual(self.kinds(only='notes'), ['work'])     # the day off has a note

    def test_a_role_with_no_health_sees_the_rest_and_not_the_health_records(self):
        self.injure()
        self.client.force_login(make_user_with_access('rider', training='view'))
        response = self.client.get(self.url())
        self.assertEqual(sorted(set(self.kinds())), ['status', 'work'])
        self.assertContains(response, 'Scan on review.')
        self.assertNotContains(response, 'Tendon, near fore')
        self.assertNotContains(response, 'Lame near fore')

    def test_a_role_with_health_view_reads_and_cannot_edit(self):
        self.injure()
        self.client.force_login(make_user_with_access('lad', training='view', health='view'))
        response = self.client.get(self.url())
        self.assertContains(response, 'Tendon, near fore')
        self.assertNotContains(response, reverse('condition_update', args=[self.condition.pk]))
