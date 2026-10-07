"""Vaccination course stages, reminder windows and free-typed types.

A flu/tetanus course has set gaps: V1 → V2 in 21–60 days, V2 → V3 in
120–180 days, then a booster within the type's interval. The stage picked
on the record sets the window for the next dose, and the owner reminder
goes out when that window opens.

The vaccination type is optional on the form: blank files the record as
Flu (every 12 months), and a typed name makes a new type.
"""

from datetime import date, timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Owner
from core.roles_testutils import make_admin
from health.forms import VaccinationForm
from health.models import Vaccination, VaccinationType

GIVEN = date(2026, 3, 10)


class CourseWindowTests(TestCase):

    def setUp(self):
        self.horse = Horse.objects.create(name='Dobbin')
        self.flu = VaccinationType.objects.create(name='Flu', interval_months=12)

    def _vax(self, stage, **kwargs):
        return Vaccination.objects.create(
            horse=self.horse, vaccination_type=self.flu,
            date_given=GIVEN, course_stage=stage, **kwargs,
        )

    def test_v1_window_is_21_to_60_days(self):
        vax = self._vax(Vaccination.CourseStage.PRIMARY_1)
        self.assertEqual(vax.due_from, GIVEN + timedelta(days=21))
        self.assertEqual(vax.next_due_date, GIVEN + timedelta(days=60))

    def test_v2_window_is_120_to_180_days(self):
        vax = self._vax('primary_2')
        self.assertEqual(vax.due_from, GIVEN + timedelta(days=120))
        self.assertEqual(vax.next_due_date, GIVEN + timedelta(days=180))

    def test_boosters_use_the_type_interval(self):
        for stage in ('first_booster', 'booster'):
            vax = self._vax(stage)
            self.assertEqual(vax.due_from, date(2027, 2, 10))
            self.assertEqual(vax.next_due_date, date(2027, 3, 10))

    def test_six_month_type_gives_a_five_to_six_month_window(self):
        self.flu.interval_months = 6
        self.flu.save()
        vax = self._vax('booster')
        self.assertEqual(vax.due_from, date(2026, 8, 10))
        self.assertEqual(vax.next_due_date, date(2026, 9, 10))

    def test_no_stage_keeps_the_old_behaviour(self):
        vax = self._vax('')
        self.assertIsNone(vax.due_from)
        self.assertEqual(vax.next_due_date, date(2027, 3, 10))

    def test_typed_due_date_before_the_window_drops_the_window(self):
        vax = self._vax('primary_1', next_due_date=GIVEN + timedelta(days=14))
        self.assertIsNone(vax.due_from)

    def test_reminder_goes_out_when_the_window_opens(self):
        vax = self._vax('primary_1')
        self.assertEqual(vax.reminder_date, GIVEN + timedelta(days=21))
        plain = self._vax('')
        self.assertEqual(plain.reminder_date, date(2027, 3, 10) - timedelta(days=30))

    def test_due_soon_inside_the_window(self):
        vax = self._vax('primary_1')
        with mock.patch('django.utils.timezone.localdate', return_value=GIVEN + timedelta(days=20)):
            self.assertFalse(vax.is_due_soon)
        with mock.patch('django.utils.timezone.localdate', return_value=GIVEN + timedelta(days=30)):
            self.assertTrue(vax.is_due_soon)

    def test_reminder_task_waits_for_the_window(self):
        from notifications.tasks import send_vaccination_reminders
        owner = Owner.objects.create(name='Sue', email='sue@example.com')
        today = timezone.localdate()
        with mock.patch.object(Horse, 'current_owner', new=owner, create=True), \
                mock.patch('notifications.tasks.send_vaccination_digest', return_value=True) as send:
            Vaccination.objects.create(
                horse=self.horse, vaccination_type=self.flu,
                date_given=today - timedelta(days=10), course_stage='primary_1',
            )
            send_vaccination_reminders()
            send.assert_not_called()
            Vaccination.objects.all().delete()
            Vaccination.objects.create(
                horse=self.horse, vaccination_type=self.flu,
                date_given=today - timedelta(days=25), course_stage='primary_1',
            )
            send_vaccination_reminders()
            send.assert_called_once()


class OptionalTypeFormTests(TestCase):

    def setUp(self):
        self.horse = Horse.objects.create(name='Dobbin')

    def _form(self, **data):
        base = {'horse': self.horse.pk, 'date_given': '2026-03-10', 'cost': ''}
        base.update(data)
        return VaccinationForm(data=base)

    def test_blank_type_files_as_flu_every_12_months(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)
        vax = form.save()
        self.assertEqual(vax.vaccination_type.name, 'Flu')
        self.assertEqual(vax.vaccination_type.interval_months, 12)
        self.assertEqual(vax.next_due_date, date(2027, 3, 10))

    def test_blank_type_reuses_an_existing_flu_type(self):
        flu = VaccinationType.objects.create(name='flu', interval_months=12)
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().vaccination_type, flu)
        self.assertEqual(VaccinationType.objects.count(), 1)

    def test_typed_name_makes_a_new_type(self):
        form = self._form(new_type_name='Tetanus')
        self.assertTrue(form.is_valid(), form.errors)
        vax = form.save()
        self.assertEqual(vax.vaccination_type.name, 'Tetanus')
        self.assertEqual(vax.vaccination_type.interval_months, 12)

    def test_typed_name_matching_a_type_reuses_it(self):
        tet = VaccinationType.objects.create(name='Tetanus', interval_months=24)
        form = self._form(new_type_name='tetanus')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().vaccination_type, tet)

    def test_invalid_form_makes_no_type(self):
        form = self._form(new_type_name='Tetanus', date_given='')
        self.assertFalse(form.is_valid())
        self.assertFalse(VaccinationType.objects.exists())

    def test_picked_type_wins_over_typed_name(self):
        tet = VaccinationType.objects.create(name='Tetanus', interval_months=24)
        form = self._form(vaccination_type=tet.pk, new_type_name='Other')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().vaccination_type, tet)
        self.assertEqual(VaccinationType.objects.count(), 1)

    def test_course_stage_saves_the_window(self):
        form = self._form(course_stage='primary_1')
        self.assertTrue(form.is_valid(), form.errors)
        vax = form.save()
        self.assertEqual(vax.next_due_date, GIVEN + timedelta(days=60))
        self.assertEqual(vax.due_from, GIVEN + timedelta(days=21))

    def test_changing_the_stage_recalculates_the_due_date(self):
        form = self._form()
        vax = form.save() if form.is_valid() else None
        edit = VaccinationForm(instance=vax, data={
            'horse': self.horse.pk, 'vaccination_type': vax.vaccination_type.pk,
            'date_given': '2026-03-10', 'course_stage': 'primary_2',
            'next_due_date': vax.next_due_date.isoformat(), 'cost': '',
        })
        self.assertTrue(edit.is_valid(), edit.errors)
        vax = edit.save()
        self.assertEqual(vax.next_due_date, GIVEN + timedelta(days=180))

    def test_popup_form_shows_the_new_fields(self):
        self.client.force_login(make_admin())
        response = self.client.get(
            reverse('vaccination_create') + f'?horse={self.horse.pk}',
            HTTP_HX_REQUEST='true', HTTP_HX_TARGET='popup-body',
        )
        self.assertContains(response, 'Or type a new vaccination')
        self.assertContains(response, 'Course stage')
        self.assertContains(response, 'Primary course: 1st (V1)')


class HorseTimelineTests(TestCase):
    """The horse page timeline: every record has Edit, and rust marks only
    the newest record of a kind that is overdue."""

    def setUp(self):
        from health.models import WormingTreatment
        self.client.force_login(make_admin())
        self.horse = Horse.objects.create(name='Dobbin')
        flu = VaccinationType.objects.create(name='Flu', interval_months=12)
        today = timezone.localdate()
        self.old = Vaccination.objects.create(
            horse=self.horse, vaccination_type=flu,
            date_given=today - timedelta(days=400),
        )
        self.new = Vaccination.objects.create(
            horse=self.horse, vaccination_type=flu,
            date_given=today - timedelta(days=10),
        )
        self.worming = WormingTreatment.objects.create(
            horse=self.horse, date=today - timedelta(days=5), product_name='Equest',
        )

    def test_records_have_edit_links(self):
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        self.assertContains(response, reverse('worming_update', args=[self.worming.pk]))
        self.assertContains(response, reverse('vaccination_update', args=[self.new.pk]))

    def test_superseded_record_is_not_marked_overdue(self):
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        events = {e['obj'].pk: e for e in response.context['timeline_events'] if e['type'] == 'vaccination'}
        self.assertTrue(self.old.is_overdue)
        self.assertFalse(events[self.old.pk]['attention'])
        self.assertFalse(events[self.new.pk]['attention'])

    def test_newest_overdue_record_is_marked(self):
        self.new.delete()
        response = self.client.get(reverse('horse_detail', args=[self.horse.pk]))
        events = {e['obj'].pk: e for e in response.context['timeline_events'] if e['type'] == 'vaccination'}
        self.assertTrue(events[self.old.pk]['attention'])
