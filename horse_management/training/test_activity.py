"""Training phase 1: records, their status rules, and the Log.

docs/TRAINING_MODULE_PLAN.md, sections 5.12, 7.2 and 9.1.
"""

from datetime import timedelta

from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse
from core.roles_testutils import make_user_with_access

from . import services
from .forms import ActivityForm
from .models import Activity, WorkType

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2, default_minutes=40)
        self.rest = WorkType.objects.create(name='Rest day', kind='rest', effort=0)
        self.horse = Horse.objects.create(name='Ballybrook')
        self.user = make_user_with_access('trainer', training='full', horses='view')
        self.client.force_login(self.user)

    def activity(self, days=0, status='done', **kwargs):
        reason = kwargs.pop('reason', 'unsound' if status == 'not_done' else '')
        return Activity.objects.create(
            horse=kwargs.pop('horse', self.horse), date=self.today + timedelta(days=days),
            work_type=kwargs.pop('work_type', self.canter), status=status, reason=reason, **kwargs,
        )

    def form(self, **data):
        base = {
            'horse': self.horse.pk, 'date': self.today.isoformat(), 'work_type': self.canter.pk,
            'status': 'done', 'reason': '', 'minutes': '', 'effort': '', 'notes': '',
        }
        base.update(data)
        return ActivityForm(data=base)


class StatusRuleTests(Base):
    def test_today_is_done_by_default_and_tomorrow_is_planned(self):
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['status'], 'done')
        form = self.form(date=(self.today + timedelta(days=1)).isoformat())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['status'], 'planned')

    def test_done_chosen_for_tomorrow_is_refused(self):
        # The user changed the status to Done on purpose: refuse, do not guess.
        form = ActivityForm(
            data={'horse': self.horse.pk, 'date': (self.today + timedelta(days=1)).isoformat(),
                  'work_type': self.canter.pk, 'status': 'done'},
            initial={'status': 'planned'},
        )
        self.assertFalse(form.is_valid())
        self.assertIn('status', form.errors)

    def test_did_not_work_needs_a_reason(self):
        form = self.form(status='not_done')
        self.assertFalse(form.is_valid())
        self.assertIn('reason', form.errors)
        form = self.form(status='not_done', reason='weather')
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_reason_with_done_is_not_saved(self):
        form = self.form(status='done', reason='weather')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().reason, '')

    def test_rest_has_no_effort(self):
        form = self.form(work_type=self.rest.pk, effort='3')
        self.assertFalse(form.is_valid())
        self.assertIn('effort', form.errors)

    def test_minutes_default_to_the_work_types_usual_minutes(self):
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().minutes, 40)

    def test_the_database_refuses_a_reason_without_did_not_work(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            Activity.objects.create(horse=self.horse, date=self.today, work_type=self.canter,
                                    status='done', reason='weather')


class MarkTests(Base):
    def test_mark_done_changes_planned_rows_of_today_or_earlier_only(self):
        past = self.activity(-1, 'planned')
        future = self.activity(1, 'planned')
        done = self.activity(-2, 'done')
        result = services.mark_done([past, future, done], user=self.user)
        self.assertEqual((result.changed, result.future, result.other), (1, 1, 1))
        past.refresh_from_db()
        future.refresh_from_db()
        self.assertEqual(past.status, 'done')
        self.assertEqual(past.updated_by, self.user)
        self.assertEqual(future.status, 'planned')
        self.assertEqual(past.history.count(), 2)

    def test_a_second_mark_done_changes_nothing_and_raises_nothing(self):
        past = self.activity(-1, 'planned')
        self.assertEqual(services.mark_done([past], user=self.user).changed, 1)
        self.assertEqual(services.mark_done([past], user=self.user).changed, 0)

    def test_mark_not_done_needs_a_known_reason(self):
        row = self.activity(-1, 'planned')
        with self.assertRaises(ValueError):
            services.mark_not_done([row], 'bored', user=self.user)
        services.mark_not_done([row], 'weather', user=self.user)
        row.refresh_from_db()
        self.assertEqual((row.status, row.reason), ('not_done', 'weather'))

    def test_past_planned_shows_as_not_confirmed(self):
        row = self.activity(-1, 'planned')
        self.assertTrue(row.is_not_confirmed)
        self.assertEqual(row.status_label, 'Not confirmed')
        self.assertFalse(self.activity(0, 'planned').is_not_confirmed)


class ViewTests(Base):
    def test_record_from_the_popup(self):
        url = reverse('training_activity_create') + f'?horse={self.horse.pk}'
        response = self.client.get(url, **POPUP)
        self.assertContains(response, 'x-data="{ status:')
        response = self.client.post(url, {
            'horse': self.horse.pk, 'date': self.today.isoformat(), 'work_type': self.canter.pk,
            'status': 'done', 'notes': 'Went well',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        activity = Activity.objects.get()
        self.assertEqual((activity.source, activity.created_by), ('horse', self.user))

    def test_mark_one_row_from_the_horse_card(self):
        row = self.activity(-1, 'planned')
        response = self.client.post(reverse('training_activity_mark', args=[row.pk]), {'action': 'done'})
        self.assertRedirects(response, reverse('horse_detail', args=[self.horse.pk]), fetch_redirect_response=False)
        row.refresh_from_db()
        self.assertEqual(row.status, 'done')

    def test_log_filters_and_marks_ticked_rows(self):
        a = self.activity(-1, 'planned')
        b = self.activity(-2, 'planned')
        self.activity(-3, 'done')
        response = self.client.get(reverse('training_log') + '?status=not_confirmed')
        self.assertEqual(len(response.context['activities']), 2)
        self.client.post(reverse('training_log_mark'), {'ids': [a.pk, b.pk], 'action': 'not_done', 'reason': 'weather'})
        self.assertEqual(Activity.objects.filter(status='not_done').count(), 2)

    def test_delete_from_the_popup(self):
        row = self.activity()
        response = self.client.post(reverse('training_activity_delete', args=[row.pk]), **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Activity.objects.exists())


class WorkTypeTests(Base):
    def test_archived_work_type_leaves_the_form_and_old_records_keep_it(self):
        old = self.activity(-5)
        self.client.post(reverse('training_work_type_archive', args=[self.canter.pk]))
        self.canter.refresh_from_db()
        self.assertFalse(self.canter.is_active)
        self.assertNotIn(self.canter, ActivityForm().fields['work_type'].queryset)
        self.assertIn(self.canter, ActivityForm(instance=old).fields['work_type'].queryset)
        response = self.client.get(reverse('training_log'))
        self.assertContains(response, 'Canter')

    def test_a_work_type_in_use_cannot_be_deleted(self):
        self.activity()
        with self.assertRaises(ProtectedError):
            self.canter.delete()

    def test_rest_work_type_gets_no_effort(self):
        response = self.client.post(reverse('training_work_type_create'), {
            'name': 'Paddock rest', 'kind': 'rest', 'effort': '2', 'position': '0',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(WorkType.objects.get(name='Paddock rest').effort, 0)

    def test_ridden_work_type_needs_an_effort(self):
        response = self.client.post(reverse('training_work_type_create'), {
            'name': 'Gallop', 'kind': 'ridden', 'effort': '0', 'position': '0',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkType.objects.filter(name='Gallop').exists())
