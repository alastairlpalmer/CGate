"""The board redesign (docs/TRAINING_BOARD_REDESIGN_PLAN.md): Done on a row
and its Undo, the record sheet, How it went, the instruction, notes about a
horse, the training profile, the Plan tab, the note for the day and Share."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Horse
from core.roles_testutils import make_user_with_access

from . import board, plan, profile, services, share
from .models import (
    Activity, DayNote, HorseTraining, Lot, LotWeekPlan, Rider, TrainingNote, TrainingSettings, Venue, WorkType,
)

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.canter = WorkType.objects.create(name='Canter', kind='ridden', effort=2)
        self.walk = WorkType.objects.create(name='Walk', kind='ridden', effort=1)
        self.first = Lot.objects.create(name='1st lot', usual_work_type=self.canter, position=0)
        self.second = Lot.objects.create(name='2nd lot', usual_work_type=self.canter, position=1)
        self.user = make_user_with_access('trainer', training='full', horses='view')
        self.viewer = make_user_with_access('viewer', training='view')
        self.client.force_login(self.user)
        self.kestrel, self.quill = (Horse.objects.create(name=n) for n in ('Kestrel', 'Quill'))
        services.add_to_lot(self.first, [self.kestrel, self.quill])

    def start(self, day=None):
        return board.start_board(day or self.today, user=self.user, source='lots')

    def row(self, horse=None, day=None):
        return Activity.objects.get(horse=horse or self.kestrel, date=day or self.today)

    def mark(self, row, action, **extra):
        return self.client.post(reverse('training_activity_mark', args=[row.pk]),
                                {'action': action, 'next': reverse('training_board'), **extra})


class RowDoneTests(Base):
    """B1 and B2: Done on one row, its Undo, and Did not work in the menu."""

    def test_a_planned_row_has_done_and_one_tap_marks_it(self):
        self.start()
        row = self.row()
        response = self.client.get(reverse('training_board'))
        self.assertContains(response, f'aria-label="Mark {self.kestrel.name} done"')
        self.mark(row, 'done')
        row.refresh_from_db()
        self.assertEqual(row.status, Activity.Status.DONE)

    def test_a_future_board_has_no_done(self):
        tomorrow = self.today + timedelta(days=1)
        self.start(tomorrow)
        response = self.client.get(reverse('training_board_day', args=[tomorrow.isoformat()]))
        self.assertNotContains(response, 'aria-label="Mark Kestrel done"')

    def test_undo_done_makes_it_planned_and_clears_how_it_went(self):
        self.start()
        row = self.row()
        services.mark_done([row.pk], user=self.user)
        Activity.objects.filter(pk=row.pk).update(rating=Activity.Rating.GOOD)
        self.assertContains(self.client.get(reverse('training_board')), 'Undo done')
        self.mark(row, 'undo_done')
        row.refresh_from_db()
        self.assertEqual((row.status, row.rating, row.lot_action), (Activity.Status.PLANNED, None, ''))
        self.assertEqual(row.history.first().status, Activity.Status.PLANNED)     # saved with history

    def test_undo_done_changes_only_done_rows(self):
        self.start()
        row = self.row()
        services.mark_not_done([row.pk], Activity.Reason.UNSOUND, user=self.user)
        result, billed = services.unmark_done([row.pk], user=self.user)
        row.refresh_from_db()
        self.assertEqual((result.changed, result.other, billed), (0, 1, None))
        self.assertEqual(row.status, Activity.Status.NOT_DONE)

    def test_did_not_work_is_in_the_menu_with_every_reason(self):
        self.start()
        response = self.client.get(reverse('training_board'))
        self.assertContains(response, 'Why Kestrel did not work')
        for value, _ in Activity.Reason.choices:
            self.assertContains(response, f'name="reason" value="{value}"')

    def test_the_lot_header_counts_the_done_horses(self):
        self.start()
        services.mark_done([self.row().pk], user=self.user)
        self.assertContains(self.client.get(reverse('training_board')), '1 of 2 done')

    def test_a_view_role_sees_the_board_with_no_buttons_that_change(self):
        self.start()
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('training_board'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'aria-label="Mark Kestrel done"')
        self.assertNotContains(response, 'Undo done')
        self.assertNotContains(response, reverse('training_day_note', args=[self.today.isoformat()]))
        self.assertContains(response, reverse('training_horse_profile', args=[self.kestrel.pk]))
        self.assertContains(response, 'Share this lot')         # reading and sharing need View only
        self.assertIn(self.mark(self.row(), 'done').status_code, (302, 403))
        self.assertEqual(self.row().status, Activity.Status.PLANNED)


class RecordSheetTests(Base):
    """B4, C1, A2 and A3: the answer first, How it went, the instruction."""

    def sheet(self, row):
        return self.client.get(reverse('training_activity_update', args=[row.pk]), **POPUP)

    def post(self, row, **data):
        payload = {
            'horse': row.horse_id, 'date': row.date.isoformat(), 'work_type': row.work_type_id,
            'status': row.status, 'who': '', 'gear': '', 'notes': '', 'instructions': '', 'rating': '',
        }
        payload.update(data)
        return self.client.post(reverse('training_activity_update', args=[row.pk]), payload, **POPUP)

    def test_status_and_the_reasons_are_buttons(self):
        self.start()
        response = self.sheet(self.row())
        self.assertContains(response, 'class="choice-seg')
        for value in ('planned', 'done', 'not_done'):
            self.assertContains(response, f'name="status" value="{value}"')
        self.assertContains(response, 'name="reason" value="unsound"')
        self.assertContains(response, 'How it went')
        self.assertContains(response, 'Work details')

    def test_how_it_went_and_the_note_save_with_done(self):
        self.start()
        row = self.row()
        response = self.post(row, status='done', rating='1', notes='Blew hard after.')
        self.assertEqual(response.status_code, 204)
        row.refresh_from_db()
        self.assertEqual((row.status, row.rating, row.notes), ('done', Activity.Rating.POOR, 'Blew hard after.'))
        self.assertEqual(row.rating_label, 'Poor')

    def test_how_it_went_goes_only_with_done(self):
        self.start()
        row = self.row()
        self.post(row, status='not_done', reason='unsound', rating='3')
        row.refresh_from_db()
        self.assertEqual((row.status, row.rating), ('not_done', None))

    def test_the_instruction_shows_on_the_board_and_does_not_stop_the_lot_work(self):
        self.start()
        row = self.row()
        self.post(row, instructions='Sit in behind Quill')
        row.refresh_from_db()
        self.assertEqual(row.instructions, 'Sit in behind Quill')
        self.assertTrue(row.follows_lot)        # an instruction is not the horse's own work
        self.assertContains(self.client.get(reverse('training_board')), 'Sit in behind Quill')

    def test_a_planned_record_after_today_shows_the_plan_first(self):
        tomorrow = self.today + timedelta(days=1)
        self.start(tomorrow)
        body = self.sheet(self.row(day=tomorrow)).content.decode()
        self.assertLess(body.index('name="instructions"'), body.index('name="status"'))
        self.assertNotIn('How it went', body)

    def test_a_new_record_starts_with_the_work(self):
        body = self.client.get(reverse('training_activity_create') + f'?horse={self.kestrel.pk}', **POPUP).content.decode()
        self.assertLess(body.index('name="work_type"'), body.index('name="status"'))
        self.assertIn('More details', body)

    def test_the_board_row_shows_how_it_went_and_the_note(self):
        self.start()
        row = self.row()
        services.mark_done([row.pk], user=self.user)
        Activity.objects.filter(pk=row.pk).update(rating=Activity.Rating.GOOD, notes='Strong to the line.')
        response = self.client.get(reverse('training_board'))
        self.assertContains(response, 'Strong to the line.')
        self.assertContains(response, '>Good<')

    def test_the_log_csv_has_how_it_went_and_the_instruction(self):
        self.start()
        Activity.objects.filter(pk=self.row().pk).update(
            status='done', rating=Activity.Rating.OK, instructions='Trot only')
        body = self.client.get(reverse('training_log') + '?format=csv').content.decode()
        self.assertIn('How it went,Instruction', body)
        self.assertIn('OK,Trot only', body)


class ShareTests(Base):
    """D1 to D4: the lots as text, the note for the day, the sheets."""

    def setUp(self):
        super().setUp()
        self.start()
        self.sam = Rider.objects.create(name='Sam')
        self.gallop = Venue.objects.create(name='Woodchip gallop', surface='woodchip')

    def lot_days(self):
        lot_days = board.board_days(self.today)
        for lot_day in lot_days:
            lot_day.rows = list(lot_day.activities.select_related('horse', 'work_type', 'rider').order_by('position', 'pk'))
        return lot_days

    def test_the_text_of_a_lot(self):
        Activity.objects.filter(pk=self.row().pk).update(rider=self.sam, gear='Boots', instructions='Sit in behind Quill')
        Activity.objects.filter(pk=self.row(self.quill).pk).update(work_type=self.walk, follows_lot=False)
        lot_day = self.lot_days()[0]
        lot_day.notes, lot_day.venue, lot_day.venue_id = '2 canters, steady.', self.gallop, self.gallop.pk
        self.assertEqual(share.lot_text(lot_day).split('\n'), [
            '1st lot. Canter, Woodchip gallop',
            '2 canters, steady.',
            '1. Kestrel - Sam (Boots). Sit in behind Quill',
            '2. Quill. Walk',
        ])

    def test_a_horse_that_does_not_go_out_is_on_the_last_line(self):
        services.mark_not_done([self.row(self.quill).pk], Activity.Reason.FARRIER, user=self.user)
        lines = share.lot_text(self.lot_days()[0]).split('\n')
        self.assertEqual(lines[1:], ['1. Kestrel', 'Not going out: Quill (shoe off or farrier)'])

    def test_the_board_text_has_the_date_the_note_and_each_lot(self):
        text = share.board_text(self.today, self.lot_days(), 'Farrier here at 10:00.')
        self.assertTrue(text.startswith(f'Lots, {self.today:%a} {self.today.day} {self.today:%b}\nFarrier here at 10:00.\n\n1st lot'))
        self.assertIn('\n\n2nd lot. Canter\nNo horses.', text)

    def test_the_board_carries_the_text_and_the_sheets(self):
        response = self.client.get(reverse('training_board'))
        self.assertContains(response, 'id="share-board"')
        self.assertContains(response, 'data-share-text="#share-board"')
        self.assertContains(response, '1. Kestrel')
        self.assertContains(response, reverse('training_board_sheet', args=[self.today.isoformat(), 'print']))
        self.assertContains(response, reverse('training_board_sheet', args=[self.today.isoformat(), 'tv']))

    def test_the_note_for_the_day_saves_shows_and_goes(self):
        url = reverse('training_day_note', args=[self.today.isoformat()])
        self.assertContains(self.client.get(url, **POPUP), 'Note for the day')
        self.assertEqual(self.client.post(url, {'text': 'Farrier here at 10:00.'}, **POPUP).status_code, 204)
        self.assertEqual(DayNote.objects.get(date=self.today).updated_by, self.user)
        for page in (reverse('training_board'), reverse('training_home'), reverse('training_plan'),
                     reverse('training_board_sheet', args=[self.today.isoformat(), 'print'])):
            self.assertContains(self.client.get(page), 'Farrier here at 10:00.')
        self.client.post(url, {'text': '  '}, **POPUP)
        self.assertFalse(DayNote.objects.exists())

    def test_a_view_role_cannot_change_the_note_for_the_day(self):
        self.client.force_login(self.viewer)
        response = self.client.post(reverse('training_day_note', args=[self.today.isoformat()]), {'text': 'x'})
        self.assertIn(response.status_code, (302, 403))
        self.assertFalse(DayNote.objects.exists())

    def test_the_sheet_shows_the_instruction(self):
        Activity.objects.filter(pk=self.row().pk).update(instructions='Trot only, 20 minutes')
        response = self.client.get(reverse('training_board_sheet', args=[self.today.isoformat(), 'tv']))
        self.assertContains(response, 'Trot only, 20 minutes')


class ProfileTests(Base):
    """5.3 and C3: the training profile, and notes about a horse."""

    def url(self, **params):
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        return reverse('training_horse_profile', args=[self.kestrel.pk]) + (f'?{query}' if query else '')

    def work(self, back, status='done', **values):
        return Activity.objects.create(
            horse=self.kestrel, date=self.today - timedelta(days=back), work_type=self.canter, status=status, **values)

    def test_the_page_shows_the_facts_the_weeks_and_every_note_in_full(self):
        long_note = 'Hung left up the gallop. ' * 12
        self.work(0, notes=long_note, rating=Activity.Rating.POOR)
        self.work(1)
        TrainingNote.objects.create(horse=self.kestrel, date=self.today, text='Coming to hand.', created_by=self.user)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual([f['value'] for f in response.context['facts']], [2, 0, 'Today', 'None yet'])
        self.assertEqual(len(response.context['calendar']), profile.WEEKS)
        self.assertEqual(len(response.context['calendar'][0]['cells']), 7)
        self.assertContains(response, long_note.strip())       # not cut
        self.assertContains(response, 'Coming to hand.')
        self.assertContains(response, 'Poor')
        self.assertEqual(response.context['rated'], [('Good', 0), ('OK', 0), ('Poor', 1)])

    def test_the_list_is_newest_first_with_notes_between_the_work(self):
        self.work(2)
        TrainingNote.objects.create(horse=self.kestrel, date=self.today - timedelta(days=1), text='A note.')
        self.work(0)
        kinds = [entry['kind'] for entry in self.client.get(self.url()).context['entries']]
        self.assertEqual(kinds, ['work', 'note', 'work'])

    def test_notes_only_and_did_not_work(self):
        self.work(0, notes='With a note.')
        self.work(1)
        self.work(2, status='not_done', reason='unsound')
        TrainingNote.objects.create(horse=self.kestrel, date=self.today, text='A note.')
        notes = self.client.get(self.url(only='notes')).context['entries']
        self.assertEqual(sorted(e['kind'] for e in notes), ['note', 'work'])
        missed = self.client.get(self.url(only='missed')).context['entries']
        self.assertEqual([e['obj'].status for e in missed], ['not_done'])

    def test_planned_work_after_today_is_coming_up_not_history(self):
        Activity.objects.create(horse=self.kestrel, date=self.today + timedelta(days=1), work_type=self.canter,
                                status='planned', instructions='Easy day')
        response = self.client.get(self.url())
        self.assertEqual(len(response.context['coming_up']), 1)
        self.assertEqual(response.context['entries'], [])
        self.assertContains(response, 'Easy day')

    def test_thirty_to_a_page(self):
        for back in range(profile.PAGE_SIZE + 5):
            self.work(back)
        first = self.client.get(self.url())
        self.assertEqual(len(first.context['entries']), profile.PAGE_SIZE)
        self.assertEqual(len(self.client.get(self.url(page=2)).context['entries']), 5)

    def test_the_page_makes_no_training_row(self):
        self.client.get(reverse('training_horse_profile', args=[Horse.objects.create(name='Wren').pk]))
        self.assertFalse(HorseTraining.objects.filter(horse__name='Wren').exists())     # principle 5.3

    def test_a_view_role_reads_and_cannot_add(self):
        TrainingNote.objects.create(horse=self.kestrel, date=self.today, text='A note.')
        self.client.force_login(self.viewer)
        response = self.client.get(self.url())
        self.assertContains(response, 'A note.')
        self.assertNotContains(response, 'Add note')
        self.assertNotContains(response, 'Record training')
        create = self.client.post(reverse('training_note_create', args=[self.kestrel.pk]),
                                  {'date': self.today.isoformat(), 'text': 'No.'})
        self.assertIn(create.status_code, (302, 403))
        self.assertEqual(TrainingNote.objects.count(), 1)

    def test_a_role_with_training_hidden_cannot_open_it(self):
        self.client.force_login(make_user_with_access('nobody', horses='view'))
        self.assertIn(self.client.get(self.url()).status_code, (302, 403))

    def test_add_change_and_delete_a_note(self):
        add = reverse('training_note_create', args=[self.kestrel.pk])
        self.assertContains(self.client.get(add, **POPUP), 'name="text"')
        self.assertEqual(self.client.post(add, {'date': self.today.isoformat(), 'text': 'Scoped clean.'}, **POPUP).status_code, 204)
        note = TrainingNote.objects.get()
        self.assertEqual((note.horse, note.created_by, note.author), (self.kestrel, self.user, self.user.username))
        self.client.post(reverse('training_note_update', args=[note.pk]),
                         {'date': self.today.isoformat(), 'text': 'Scoped clean. Back in work Monday.'}, **POPUP)
        note.refresh_from_db()
        self.assertEqual(note.text, 'Scoped clean. Back in work Monday.')
        self.assertEqual(note.history.count(), 2)
        self.assertContains(self.client.get(reverse('training_note_delete', args=[note.pk]), **POPUP), 'Delete this note?')
        self.client.post(reverse('training_note_delete', args=[note.pk]), **POPUP)
        self.assertFalse(TrainingNote.objects.exists())

    def test_a_note_cannot_have_a_date_after_today(self):
        response = self.client.post(reverse('training_note_create', args=[self.kestrel.pk]),
                                    {'date': (self.today + timedelta(days=1)).isoformat(), 'text': 'Later.'}, **POPUP)
        self.assertContains(response, 'A note cannot have a date after today.')
        self.assertFalse(TrainingNote.objects.exists())

    def test_the_horse_page_card_links_to_the_profile_and_shows_the_newest_note(self):
        self.work(0)
        TrainingNote.objects.create(horse=self.kestrel, date=self.today, text='Coming to hand.')
        response = self.client.get(reverse('horse_detail', args=[self.kestrel.pk]))
        self.assertContains(response, reverse('training_horse_profile', args=[self.kestrel.pk]))
        self.assertContains(response, 'Coming to hand.')


class PlanTests(Base):
    """A1: the week by lot."""

    def test_a_day_with_a_board_shows_it_and_a_day_with_none_shows_the_usual_work(self):
        self.start()
        data = plan.build(self.today, today=self.today)
        self.assertEqual(len(data['columns']), 7)
        today_col = next(c for c in data['columns'] if c['is_today'])
        cell = today_col['cells'][0]
        self.assertEqual((cell['work'], cell['state'], cell['total']), ('Canter', 'Planned', 2))
        self.assertTrue(today_col['started'])
        later = [c for c in data['columns'] if c['date'] > self.today]
        for col in later:
            self.assertFalse(col['started'])
            self.assertTrue(col['can_start'])
            self.assertTrue(col['cells'][0]['usual'])
            self.assertEqual(col['cells'][0]['total'], 2)       # the horses in the standing lot

    def test_the_states_of_a_lot(self):
        self.start()
        lot_day = board.board_days(self.today)[0]
        services.mark_done([self.row().pk], user=self.user)
        state = lambda: next(c for c in plan.build(self.today, today=self.today)['columns'] if c['is_today'])
        self.assertEqual(state()['cells'][0]['state'], '1 of 2 done')
        board.mark_lot_done(lot_day, user=self.user)
        self.assertEqual(state()['cells'][0]['state'], 'Done')
        self.assertEqual(state()['state'], '1 of 2 lots done')
        # Tomorrow, the same board is in the past with one lot still open.
        tomorrow = self.today + timedelta(days=1)
        col = next(c for c in plan.build(self.today, today=tomorrow)['columns'] if c['date'] == self.today)
        self.assertEqual((col['state'], col['cells'][1]['state']), ('Not confirmed', 'Not confirmed'))

    def test_the_week_plan_shows_when_the_yard_starts_boards_from_it(self):
        settings_row = TrainingSettings.load()
        settings_row.board_source = TrainingSettings.BoardSource.WEEK_PLAN
        settings_row.save()
        sunday = plan.week_days(self.today + timedelta(days=7))[6]
        friday = sunday - timedelta(days=2)
        LotWeekPlan.objects.create(lot=self.first, weekday=4, work_type=self.walk)
        for lot in (self.first, self.second):
            LotWeekPlan.objects.create(lot=lot, weekday=6, no_lot=True)
        columns = {c['date']: c for c in plan.build(sunday, today=self.today)['columns']}
        self.assertEqual(columns[friday]['cells'][0]['work'], 'Walk')
        self.assertEqual(columns[friday]['cells'][1]['work'], 'Canter')
        self.assertEqual((columns[sunday]['state'], columns[sunday]['can_start']), ('No lots', False))

    def test_the_week_plan_is_not_shown_when_boards_start_from_the_lots(self):
        friday = plan.week_days(self.today + timedelta(days=7))[4]
        LotWeekPlan.objects.create(lot=self.first, weekday=4, work_type=self.walk)
        columns = {c['date']: c for c in plan.build(friday, today=self.today)['columns']}
        self.assertEqual(columns[friday]['cells'][0]['work'], 'Canter')

    def test_the_page_and_start_from_it(self):
        response = self.client.get(reverse('training_plan'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-current="page">Plan</a>')
        self.assertContains(response, reverse('training_board_start', args=[self.today.isoformat()]))
        start = self.client.post(reverse('training_board_start', args=[self.today.isoformat()]),
                                 {'next': reverse('training_plan')})
        self.assertRedirects(start, reverse('training_plan'), fetch_redirect_response=False)
        self.assertEqual(Activity.objects.filter(date=self.today).count(), 2)

    def test_showing_the_plan_writes_nothing(self):
        self.client.get(reverse('training_plan_week', args=[(self.today + timedelta(days=14)).isoformat()]))
        self.assertFalse(Activity.objects.exists())             # principle 5.3
        self.assertFalse(TrainingSettings.objects.exists())

    def test_a_view_role_sees_the_plan_with_no_start(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('training_plan'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse('training_board_start', args=[self.today.isoformat()]))

    def test_the_queries_do_not_grow_with_the_yard(self):
        self.start()
        with self.assertNumQueries(6):
            plan.build(self.today, today=self.today)
        services.add_to_lot(self.second, [Horse.objects.create(name=f'Horse {i}') for i in range(30)])
        self.start(self.today + timedelta(days=1))
        with self.assertNumQueries(6):
            plan.build(self.today, today=self.today)


class SwitchTests(Base):
    @override_settings(TRAINING_ENABLED=False)
    def test_the_new_pages_are_404_with_training_off(self):
        note = TrainingNote.objects.create(horse=self.kestrel, date=self.today, text='A note.')
        for url in (
            reverse('training_plan'),
            reverse('training_horse_profile', args=[self.kestrel.pk]),
            reverse('training_note_create', args=[self.kestrel.pk]),
            reverse('training_note_update', args=[note.pk]),
            reverse('training_note_delete', args=[note.pk]),
            reverse('training_day_note', args=[self.today.isoformat()]),
        ):
            self.assertEqual(self.client.get(url).status_code, 404, url)


class SheetInputTests(Base):
    def test_the_sheet_sends_how_it_went_one_time(self):
        self.start()
        row = Activity.objects.get(horse=self.kestrel, date=self.today)
        body = self.client.get(reverse('training_activity_update', args=[row.pk]), **POPUP).content.decode()
        self.assertEqual(body.count('name="rating"'), 1)


class PlanChangeTests(Base):
    """A1: with Full access, the pencil on an open lot in the Plan tab."""

    def test_an_open_lot_has_change_and_a_done_lot_has_not(self):
        self.start()
        first, second = board.board_days(self.today)
        board.mark_lot_done(first, user=self.user)
        response = self.client.get(reverse('training_plan'))
        self.assertContains(response, reverse('training_lot_day_change', args=[second.pk]))
        self.assertNotContains(response, reverse('training_lot_day_change', args=[first.pk]))
        self.assertContains(response, f'#lot-day-{first.pk}')        # the cell still opens the board

    def test_a_view_role_has_no_change(self):
        self.start()
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('training_plan'))
        self.assertNotContains(response, reverse('training_lot_day_change', args=[board.board_days(self.today)[0].pk]))

    def test_the_change_saves_from_the_pop_up_and_the_plan_shows_it(self):
        self.start()
        lot_day = board.board_days(self.today)[0]
        url = reverse('training_lot_day_change', args=[lot_day.pk])
        self.assertContains(self.client.get(url, **POPUP), 'name="work_type"')
        saved = self.client.post(url, {'work_type': self.walk.pk, 'venue': '', 'start_time': '', 'notes': ''}, **POPUP)
        self.assertEqual(saved.status_code, 204)
        cell = next(c for c in plan.build(self.today, today=self.today)['columns'] if c['is_today'])['cells'][0]
        self.assertEqual(cell['work'], 'Walk')


class LotsTabLinkTests(Base):
    def test_a_horse_name_on_the_lots_tab_opens_its_training_profile(self):
        services.set_status(Horse.objects.create(name='Wren'), HorseTraining.Status.IN_WORK)     # in work, no lot
        gone = Horse.objects.create(name='Alder')
        services.add_to_lot(self.second, [gone])
        Horse.objects.filter(pk=gone.pk).update(is_active=False)                                 # left the yard
        response = self.client.get(reverse('training_lots'))
        for horse in Horse.objects.all():
            self.assertContains(response, reverse('training_horse_profile', args=[horse.pk]))
            self.assertNotContains(response, reverse('horse_detail', args=[horse.pk]))
