"""The phone app, phase 3: key information first.

docs/MOBILE_APP_PLAN.md, items C1 to C12. The top bar itself is in
test_ui_review_fixes.PhoneTopBarTests.
"""

import json
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.roles_testutils import make_admin, make_user_with_access


def tab_bar(html):
    start = html.index('id="mobile-tab-bar"')
    return html[start:html.index('</nav>', start)]


def more_sheet(html):
    start = html.index('id="more-sheet"')
    return html[start:html.index('sheet-safe-bottom', start)]


class HealthTabTests(TestCase):
    """C2: with no Training tab, Health takes the free place on the bar."""

    def page(self, url='dashboard'):
        return self.client.get(reverse(url)).content.decode()

    @override_settings(TRAINING_ENABLED=False)
    def test_without_training_health_is_a_tab_and_not_in_more(self):
        self.client.force_login(make_admin('boss'))
        html = self.page()
        self.assertIn('id="tab-health"', tab_bar(html))
        self.assertNotIn('id="tab-training"', tab_bar(html))
        self.assertNotIn(reverse('health_dashboard') + '"', more_sheet(html))
        # Farrier and Breeding stay in More.
        self.assertIn(reverse('farrier_list'), more_sheet(html))
        self.assertIn(reverse('breeding_list'), more_sheet(html))

    @override_settings(TRAINING_ENABLED=True)
    def test_with_training_the_tab_is_training_and_health_is_in_more(self):
        self.client.force_login(make_admin('boss'))
        html = self.page()
        self.assertIn('id="tab-training"', tab_bar(html))
        self.assertNotIn('id="tab-health"', tab_bar(html))
        self.assertIn(reverse('health_dashboard') + '"', more_sheet(html))

    @override_settings(TRAINING_ENABLED=True)
    def test_a_role_with_no_training_gets_the_health_tab(self):
        self.client.force_login(make_user_with_access('groom', horses='view', health='view'))
        html = self.page('horse_list')
        self.assertIn('id="tab-health"', tab_bar(html))
        self.assertNotIn('id="tab-training"', tab_bar(html))

    @override_settings(TRAINING_ENABLED=False)
    def test_a_role_with_no_health_gets_no_fourth_tab(self):
        self.client.force_login(make_user_with_access('office', horses='view'))
        html = self.page('horse_list')
        self.assertNotIn('id="tab-health"', tab_bar(html))

    @override_settings(TRAINING_ENABLED=False)
    def test_the_tab_is_marked_on_health_pages_and_not_on_farrier_pages(self):
        self.client.force_login(make_admin('boss'))
        marked = 'class="tab-bar-item active"'
        on_health = tab_bar(self.page('health_dashboard'))
        tab = on_health[on_health.index('id="tab-health"'):]
        self.assertIn(marked, tab[:400])
        on_farrier = tab_bar(self.page('farrier_list'))
        tab = on_farrier[on_farrier.index('id="tab-health"'):]
        self.assertNotIn(marked, tab[:400])
        # The script that marks tabs after a tap has the same rule.
        self.assertIn('data-nav-skip="/health/farrier /health/breeding/"', on_farrier)


class TabletTests(SimpleTestCase):
    """C12: a tablet on a stand can turn, and gets no keyboard hint."""

    base = Path(settings.BASE_DIR)

    def test_the_installed_app_is_not_locked_to_portrait(self):
        manifest = json.loads((self.base / 'static' / 'manifest.json').read_text())
        self.assertNotIn('orientation', manifest)

    def test_the_press_slash_hint_hides_on_a_touch_screen(self):
        css = (self.base / 'static' / 'css' / 'input.css').read_text()
        self.assertRegex(css, r'@media \(pointer: coarse\) \{\s*\.search-key-hint \{ display: none !important; \}')
        search = (self.base / 'templates' / 'includes' / '_app_search.html').read_text()
        self.assertIn('class="search-key-hint ', search)


class HorsePagePhoneTests(TestCase):
    """C4: on a phone, a horse page says first what is due and what to know."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import Horse, Location, Owner, Placement, RateType

        self.today = timezone.localdate()
        self.client.force_login(make_admin('boss'))
        self.horse = Horse.objects.create(name='Belina', sex='mare')
        Placement.objects.create(
            horse=self.horse, owner=Owner.objects.create(name='Jo Bloggs'),
            location=Location.objects.create(name='Top Field', site='Main'),
            rate_type=RateType.objects.create(name='Full', daily_rate=10),
            start_date=self.today - timedelta(days=30),
        )

    def page(self):
        html = self.client.get(reverse('horse_detail', args=[self.horse.pk])).content.decode()
        return html.split('<main', 1)[1].split('</main>', 1)[0]

    def overdue_vaccination(self):
        from datetime import timedelta

        from health.models import Vaccination, VaccinationType

        flu = VaccinationType.objects.create(name='Flu', interval_months=12)
        Vaccination.objects.create(
            horse=self.horse, vaccination_type=flu, date_given=self.today - timedelta(days=400),
            next_due_date=self.today - timedelta(days=35),
        )

    def test_a_horse_with_nothing_to_say_has_no_lines(self):
        self.assertNotIn('data-testid="header-flags"', self.page())

    def test_what_is_due_is_a_line_under_the_name_that_goes_to_the_due_card(self):
        self.overdue_vaccination()
        body = self.page()
        line = body.split('data-testid="header-due"', 1)[1].split('</a>', 1)[0]
        self.assertIn('35 days overdue', line)
        self.assertIn('<a href="#due" class="hero-flag" data-testid="header-due"', body)
        self.assertIn('id="due"', body)
        # Before the buttons, so it is on the first screen.
        self.assertLess(body.index('data-testid="header-due"'), body.index('horse-hero-actions'))

    def test_a_condition_is_a_line_that_goes_to_the_notes_card(self):
        from health.models import MedicalCondition

        MedicalCondition.objects.create(horse=self.horse, name='Sweet itch', status='active')
        body = self.page()
        line = body.split('data-testid="header-alert"', 1)[1].split('</a>', 1)[0]
        self.assertIn('Sweet itch', line)
        self.assertIn('href="#notes-conditions"', body)
        self.assertIn('id="notes-conditions"', body)

    def test_a_note_alone_shows_the_line_too(self):
        self.horse.notes = 'Bites when rugged.'
        self.horse.save()
        line = self.page().split('data-testid="header-alert"', 1)[1].split('</a>', 1)[0]
        self.assertIn('Notes', line)

    def test_the_lines_are_for_a_phone_only(self):
        self.overdue_vaccination()
        self.assertIn('class="horse-hero-flags sm:hidden"', self.page())

    def test_notes_and_conditions_come_first_in_their_column_on_a_phone(self):
        self.horse.notes = 'Bites when rugged.'
        self.horse.save()
        self.assertIn('class="card order-first lg:order-none" id="notes-conditions"', self.page())

    def test_an_empty_pedigree_is_one_line_with_a_button_to_open_the_tree(self):
        body = self.page()
        self.assertIn('data-testid="pedigree-empty"', body)
        self.assertIn('data-testid="pedigree-toggle"', body)
        self.assertNotIn('data-testid="pedigree-line"', body)
        # The tree is still in the page: a computer shows it, a phone opens it.
        self.assertIn('class="hero-ped-body mt-2" id="pedigree-diagram"', body)

    def test_the_stylesheet_folds_the_tree_and_lays_the_buttons_in_one_row(self):
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'input.css').read_text()
        self.assertIn('.horse-hero-ped:not(.is-open) .hero-ped-body { display: none; }', css)
        self.assertIn('"photo head" "flags flags" "actions actions" "place place" "ped ped"', css)


class GroupedActionsTests(TestCase):
    """C6: one heading for one job, with one "Record for all"."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import Horse, Location, Owner, Placement, RateType
        from health.models import Vaccination, VaccinationType

        self.today = timezone.localdate()
        owner = Owner.objects.create(name='Jo Bloggs')
        field = Location.objects.create(name='Top Field', site='Main')
        rate = RateType.objects.create(name='Full', daily_rate=10)
        flu = VaccinationType.objects.create(name='Flu', interval_months=12)
        tetanus = VaccinationType.objects.create(name='Tetanus', interval_months=24)
        self.horses = []
        for name, days in (('Ace', 40), ('Bella', 30), ('Clover', 20)):
            horse = Horse.objects.create(name=name, sex='mare')
            Placement.objects.create(horse=horse, owner=owner, location=field, rate_type=rate,
                                     start_date=self.today - timedelta(days=500))
            Vaccination.objects.create(horse=horse, vaccination_type=flu,
                                       date_given=self.today - timedelta(days=400),
                                       next_due_date=self.today - timedelta(days=days))
            self.horses.append(horse)
        Vaccination.objects.create(horse=self.horses[2], vaccination_type=tetanus,
                                   date_given=self.today - timedelta(days=800),
                                   next_due_date=self.today - timedelta(days=5))

    def groups(self, html):
        return html.split('data-testid="action-group"')[1:]

    def overview(self):
        return self.client.get(reverse('health_dashboard')).content.decode()

    def test_entries_of_one_job_go_under_one_heading(self):
        from core.dashboard import attention

        entries = [
            {'type': 'Vaccination', 'detail': 'Flu', 'horse': self.horses[0]},
            {'type': 'Vaccination', 'detail': 'Tetanus', 'horse': self.horses[2]},
            {'type': 'Vaccination', 'detail': 'Flu', 'horse': self.horses[1]},
            {'type': 'Egg count', 'detail': '-', 'horse': self.horses[1]},
            {'type': 'Egg count', 'detail': '-', 'horse': self.horses[2]},
        ]
        groups = attention.group_health_entries(entries, can_record=True)
        # In the order of the first entry of each job.
        self.assertEqual([(g['type'], g['detail'], g['horse_count']) for g in groups],
                         [('Vaccination', 'Flu', 2), ('Vaccination', 'Tetanus', 1), ('Egg count', '', 2)])
        flu, tetanus, eggs = groups
        self.assertEqual(flu['bulk_url'], '{}?action_type=vaccination&horse_ids={}&horse_ids={}'.format(
            reverse('bulk_health_form'), self.horses[0].pk, self.horses[1].pk))
        self.assertEqual(flu['bulk_title'], 'Record Vaccination (2 horses)')
        # One horse has its own button; a job with no bulk form has none.
        self.assertEqual(tetanus['bulk_url'], '')
        self.assertEqual(eggs['bulk_url'], '')

    def test_a_role_that_cannot_record_gets_no_address(self):
        from core.dashboard import attention

        entries = [{'type': 'Vaccination', 'detail': 'Flu', 'horse': horse} for horse in self.horses]
        self.assertEqual(attention.group_health_entries(entries, can_record=False)[0]['bulk_url'], '')

    def test_health_shows_one_group_a_job_with_record_for_all(self):
        self.client.force_login(make_admin('boss'))
        groups = self.groups(self.overview())
        self.assertEqual(len(groups), 2)
        flu = next(g for g in groups if 'Flu' in g.split('</th>')[0])
        heading = flu.split('</th>')[0]
        self.assertIn('data-testid="group-count">3 horses<', heading)
        self.assertIn('data-testid="record-for-all"', heading)
        for horse in self.horses:
            self.assertIn(f'horse_ids={horse.pk}', heading)
            self.assertIn(horse.name, flu)
        self.assertIn('data-popup-title="Record Vaccination (3 horses)"', heading)
        # A job for one horse has only that horse's own button.
        tetanus = next(g for g in groups if 'Tetanus' in g.split('</th>')[0])
        self.assertNotIn('data-testid="record-for-all"', tetanus.split('</th>')[0])

    def test_the_address_opens_the_bulk_form_with_those_horses(self):
        from core.dashboard import attention

        self.client.force_login(make_admin('boss'))
        entries = [{'type': 'Vaccination', 'detail': 'Flu', 'horse': horse} for horse in self.horses]
        url = attention.group_health_entries(entries, can_record=True)[0]['bulk_url']
        response = self.client.get(url, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for horse in self.horses:
            self.assertIn(f'name="horse_ids" value="{horse.pk}"', html)

    def test_a_reader_sees_the_groups_and_no_record_for_all(self):
        self.client.force_login(make_user_with_access('reader', health='view', horses='view'))
        html = self.overview()
        self.assertEqual(len(self.groups(html)), 2)
        self.assertNotIn('data-testid="record-for-all"', html)


class CompactRowTests(TestCase):
    """C5: on a phone, each list shows a horse as one short row."""

    css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'input.css').read_text()

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import Horse, Location, Owner, Placement, RateType
        from health.models import Vaccination, VaccinationType

        self.today = timezone.localdate()
        self.client.force_login(make_admin('boss'))
        self.owner = Owner.objects.create(name='Jo Bloggs', phone='01234 567890', email='jo@example.com')
        self.field = Location.objects.create(name='Top Field', site='Main')
        rate = RateType.objects.create(name='Full', daily_rate=10)
        flu = VaccinationType.objects.create(name='Flu', interval_months=12)
        self.horses = []
        for name in ('Ace', 'Bella'):
            horse = Horse.objects.create(name=name, sex='mare')
            Placement.objects.create(horse=horse, owner=self.owner, location=self.field, rate_type=rate,
                                     start_date=self.today - timedelta(days=500))
            Vaccination.objects.create(horse=horse, vaccination_type=flu,
                                       date_given=self.today - timedelta(days=400),
                                       next_due_date=self.today - timedelta(days=35))
            self.horses.append(horse)

    def body(self, url, *args, **query):
        html = self.client.get(reverse(url, args=args), query).content.decode()
        return html.split('<main', 1)[1].split('</main>', 1)[0]

    def test_the_stylesheet_has_the_short_row(self):
        self.assertIn('.data-table-compact tbody td[data-slot="name"] {', self.css)
        self.assertIn('.data-table-compact tbody td[data-slot="meta"] ~ td[data-slot="meta"]::before { content: " · ";', self.css)
        self.assertIn('.data-table-compact tbody td[data-slot="hide"]', self.css)
        # A tick box in a row is a 44px target.
        self.assertIn('.check-hit { display: grid; place-items: center; width: 2.75rem; height: 2.75rem;', self.css)

    def test_health_uses_the_short_row(self):
        body = self.body('health_dashboard')
        self.assertIn('class="data-table data-table-compact"', body)
        row = body.split('data-testid="action-group"', 1)[1].split('</tbody>', 1)[0]
        self.assertIn('data-slot="name"', row)
        self.assertIn('data-slot="action"', row)
        # The button says "Record" on a phone and its full name on a computer.
        self.assertIn('<span class="sm:hidden">Record</span>', row)

    def test_the_vaccination_list_says_the_status_in_words(self):
        body = self.body('vaccination_list')
        self.assertIn('class="data-table data-table-compact" data-actions="top"', body)
        self.assertIn('overdue since ', body)
        self.assertIn('.data-table-compact[data-actions="top"] tbody td[data-slot="name"]', self.css)

    def test_a_location_page_uses_the_short_row_and_a_44px_tick_box(self):
        body = self.body('location_detail', self.field.pk)
        self.assertIn('data-table-compact', body)
        self.assertEqual(body.count('<label class="check-hit">'), len(self.horses))

    def test_an_owner_can_be_called_and_emailed_from_the_list(self):
        body = self.body('owner_list')
        self.assertIn('href="tel:01234 567890"', body)
        self.assertIn('aria-label="Call Jo Bloggs"', body)
        self.assertIn('href="mailto:jo@example.com"', body)

    def test_the_horse_list_row_is_two_lines_on_a_phone(self):
        # The tick box and the menu button stand the height of both lines.
        self.assertIn('grid-template-rows: auto auto;', self.css)
        # The name and its details share the first line.
        self.assertRegex(self.css, r'\.horse-rows tbody td\[data-col="name"\] \.horse-name \{\s*display: flex;\s*flex-wrap: wrap;')


class RowMenuTests(TestCase):
    """B4: the horse list has one More menu, not one for each horse."""

    def setUp(self):
        from core.models import Horse

        self.horses = [Horse.objects.create(name=f'Horse {n}', sex='mare') for n in range(6)]

    def body(self, **query):
        html = self.client.get(reverse('horse_list'), query).content.decode()
        return html.split('<main', 1)[1].split('</main>', 1)[0]

    def test_a_row_has_only_a_button(self):
        self.client.force_login(make_admin('boss'))
        body = self.body()
        self.assertEqual(body.count('x-data="rowMenu"'), 1)
        self.assertNotIn('x-data="moreMenu"', body.split('data-testid="row-menu"')[0].split('id="list-results"')[1])
        for horse in self.horses:
            self.assertIn(
                f'data-row-menu="{horse.pk}" data-name="{horse.name}"\n'
                f'                    aria-haspopup="menu" aria-expanded="false" aria-label="More actions for {horse.name}"',
                body)

    def test_the_menu_has_each_action_once_with_a_place_for_the_horse(self):
        self.client.force_login(make_admin('boss'))
        menu = self.body().split('data-testid="row-menu"', 1)[1]
        self.assertEqual(menu.count('role="menuitem"'), 4)
        self.assertIn('data-row-href="/horses/0/move/" data-row-get="/horses/0/move/" data-row-title="Move {name}"', menu)
        self.assertIn('data-row-href="/horses/0/edit/" data-row-get="/horses/0/quick-edit/" data-row-title="Edit {name}"', menu)
        self.assertIn('data-row-title="Add photos for {name}"', menu)
        self.assertIn('data-row-page role="menuitem"', menu)

    def test_add_photos_is_only_for_a_role_that_can_change_horses(self):
        self.client.force_login(make_user_with_access('groom', horses='view'))
        menu = self.body().split('data-testid="row-menu"', 1)[1]
        self.assertNotIn('Add photos', menu)

    def test_a_row_is_light(self):
        # A row was 4.6 KB when each had its own menu.
        self.client.force_login(make_admin('boss'))
        body = self.body()
        rows = body.split('data-horse-row')[1:]
        self.assertEqual(len(rows), len(self.horses))
        longest = max(len(row.split('</tr>')[0]) for row in rows)
        self.assertLess(longest, 3000, f'the longest row is {longest} bytes')

    def test_the_owner_table_uses_the_same_menu(self):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import Location, Owner, Placement, RateType

        self.client.force_login(make_admin('boss'))
        Placement.objects.create(
            horse=self.horses[0], owner=Owner.objects.create(name='Jo Bloggs'),
            location=Location.objects.create(name='Top Field', site='Main'),
            rate_type=RateType.objects.create(name='Full', daily_rate=10),
            start_date=timezone.localdate() - timedelta(days=30),
        )
        body = self.body(group_by='owner')
        self.assertEqual(body.count('x-data="rowMenu"'), 1)
        self.assertIn(f'data-row-menu="{self.horses[0].pk}"', body)


class PhoneDashboardTests(TestCase):
    """C3: a phone's dashboard starts with what to do in the next 7 days."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import Location, Owner, RateType
        from health.models import VaccinationType

        self.days = timedelta
        self.today = timezone.localdate()
        self.admin = make_admin('boss')
        self.client.force_login(self.admin)
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.field = Location.objects.create(name='Top Field', site='Main', capacity=20)
        self.rate = RateType.objects.create(name='Full', daily_rate=10)
        self.flu = VaccinationType.objects.create(name='Flu', interval_months=12)

    def horse(self, name, due=None):
        from core.models import Horse, Placement
        from health.models import Vaccination

        horse = Horse.objects.create(name=name, sex='mare')
        Placement.objects.create(horse=horse, owner=self.owner, location=self.field, rate_type=self.rate,
                                 start_date=self.today - self.days(days=500))
        if due is not None:
            Vaccination.objects.create(horse=horse, vaccination_type=self.flu,
                                       date_given=self.today - self.days(days=300),
                                       next_due_date=self.today + self.days(days=due))
        return horse

    def week(self, user=None):
        from core.dashboard import attention, upcoming

        user = user or self.admin
        items = attention.collect(user, today=self.today)
        return upcoming.build(items, user, self.today)['week']

    def dashboard(self):
        return self.client.get(reverse('dashboard')).content.decode()

    # ── the list ──

    def test_today_is_always_in_the_list_and_an_empty_day_is_not(self):
        self.horse('Ace', due=3)
        week = self.week()
        self.assertEqual([(day['label'], len(day['lines'])) for day in week],
                         [('Today', 0), ((self.today + self.days(days=3)).strftime('%A'), 1)])
        self.assertTrue(week[0]['is_today'])

    def test_the_list_is_today_and_the_next_7_days(self):
        self.horse('Ace', due=7)
        self.horse('Bella', due=8)
        self.horse('Clover', due=-2)
        dates = [day['date'] for day in self.week()]
        self.assertEqual(dates, [self.today, self.today + self.days(days=7)])

    def test_tomorrow_is_named(self):
        self.horse('Ace', due=1)
        self.assertEqual(self.week()[1]['label'], 'Tomorrow')

    def test_one_horse_is_a_line_with_its_name_and_its_own_button(self):
        horse = self.horse('Ace', due=0)
        line = self.week()[0]['lines'][0]
        self.assertEqual(line['title'], 'Ace')
        self.assertEqual(line['sub'], 'Vaccination · Flu · Top Field')
        self.assertEqual(line['horses'], [])
        self.assertEqual(line['url'], reverse('horse_detail', args=[horse.pk]))
        self.assertEqual(line['action'].label, 'Record vaccination')

    def test_one_job_for_many_horses_is_one_line_that_records_for_all(self):
        horses = [self.horse(name, due=2) for name in ('Ace', 'Bella', 'Clover')]
        lines = self.week()[1]['lines']
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line['title'], 'Vaccination · Flu')
        self.assertEqual(line['sub'], '3 horses')
        self.assertEqual(line['horses'], horses)
        self.assertEqual(line['action'].label, 'Record for 3')
        for horse in horses:
            self.assertIn(f'horse_ids={horse.pk}', line['action'].url)

    def test_a_role_that_cannot_record_gets_no_button_for_all(self):
        for name in ('Ace', 'Bella'):
            self.horse(name, due=2)
        reader = make_user_with_access('reader', dashboard='view', health='view', horses='view')
        line = self.week(reader)[1]['lines'][0]
        self.assertEqual(line['sub'], '2 horses')
        self.assertIsNone(line['action'])

    # ── the page ──

    def test_the_card_opens_as_the_list_and_keeps_the_month_for_a_wide_screen(self):
        self.horse('Ace', due=0)
        html = self.dashboard()
        self.assertIn('class="card overflow-hidden h-full flex flex-col is-week" id="dash-calendar"', html)
        self.assertIn('x-data="{ week: true }"', html)
        self.assertIn('data-testid="week-list"', html)
        self.assertIn('data-testid="calendar-view"', html)
        # The grid is still in the page: a wide screen shows it, "Month" opens it.
        self.assertIn('<div class="cal-wrap cal-in-month">', html)
        week = html.split('data-testid="week-list"', 1)[1].split('</section>', 1)[0]
        self.assertIn('<span class="week-title">Ace</span>', week)
        self.assertIn('Record vaccination', week)

    def test_a_job_for_many_horses_opens_to_name_them(self):
        for name in ('Ace', 'Bella', 'Clover'):
            self.horse(name, due=1)
        week = self.dashboard().split('data-testid="week-list"', 1)[1].split('</section>', 1)[0]
        self.assertIn('<li class="week-line" x-data="{ names: false }">', week)
        self.assertIn('<button type="button" class="week-main"', week)
        self.assertEqual(week.count('class="horse-chip"'), 3)
        self.assertIn('Record for 3', week)

    def test_an_empty_week_says_so(self):
        week = self.dashboard().split('data-testid="week-list"', 1)[1].split('</section>', 1)[0]
        self.assertIn('Nothing is due today.', week)
        self.assertIn('Nothing is due in the next 7 days.', week)

    def test_a_card_asked_for_from_the_grid_opens_as_the_grid(self):
        url = reverse('dashboard_calendar')
        month = self.today.strftime('%Y-%m')
        for query in ({'month': month}, {'month': month, 'day': self.today.isoformat()}):
            with self.subTest(query=query):
                html = self.client.get(url, query, HTTP_HX_REQUEST='true').content.decode()
                self.assertIn('class="card overflow-hidden h-full flex flex-col" id="dash-calendar"', html)
                self.assertIn('x-data="{ week: false }"', html)

    def test_the_stylesheet_shows_the_list_on_a_phone_only(self):
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'input.css').read_text()
        self.assertIn('.cal-in-week, .cal-phone { display: none; }', css)
        self.assertIn('#dash-calendar.is-week .cal-in-week { display: revert; }', css)
        self.assertIn('#dash-calendar.is-week .cal-in-month { display: none !important; }', css)
        # C10: the month arrows are a 44px target on a phone.
        self.assertIn('.cal-nav { @apply w-11 h-11; }', css)

    # ── the figures ──

    def test_the_figures_that_need_no_action_are_a_line_of_links_for_a_phone(self):
        self.horse('Ace')
        html = self.dashboard()
        counts = html.split('data-testid="yard-counts"', 1)[1].split('</nav>', 1)[0]
        self.assertIn('data-metric="horses"><b>1</b> Horses</a>', counts)
        self.assertIn('data-metric="owners"', counts)
        self.assertNotIn('data-metric="action"', counts)
        self.assertIn('class="yard-counts sm:hidden"', html)
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'input.css').read_text()
        self.assertIn('.metric-strip .metric:not(.is-alert), .metric-strip.is-quiet { display: none; }', css)

    def test_a_figure_that_needs_a_look_stays_a_tile_and_is_not_in_the_line(self):
        self.field.capacity = 1
        self.field.save()
        for name in ('Ace', 'Bella'):
            self.horse(name)
        html = self.dashboard()
        self.assertIn('<div class="metric-strip" role="list">', html)
        self.assertRegex(html, r'class="metric is-alert"\s+data-metric="ground"|class="metric is-alert" data-metric="ground"')
        counts = html.split('data-testid="yard-counts"', 1)[1].split('</nav>', 1)[0]
        self.assertNotIn('data-metric="ground"', counts)

    def test_with_no_figure_that_needs_a_look_the_strip_takes_no_room_on_a_phone(self):
        self.horse('Ace')
        self.assertIn('<div class="metric-strip is-quiet" role="list">', self.dashboard())

    # ── the Timeline ──

    def test_a_timeline_row_for_many_horses_folds_its_names_on_a_phone(self):
        from health.models import FarrierVisit

        for name in ('Ace', 'Bella', 'Clover', 'Dot'):
            FarrierVisit.objects.create(horse=self.horse(name), date=self.today - self.days(days=1), work_done='trim')
        html = self.client.get(reverse('dashboard_activity'), HTTP_HX_REQUEST='true').content.decode()
        self.assertIn('<div class="log-body is-many" x-data="{ names: false }"', html)
        self.assertIn('data-testid="timeline-names"><span x-show="!names">4 horses</span>', html)

    def test_a_timeline_row_for_a_few_horses_shows_them(self):
        from health.models import FarrierVisit

        for name in ('Ace', 'Bella'):
            FarrierVisit.objects.create(horse=self.horse(name), date=self.today - self.days(days=1), work_done='trim')
        html = self.client.get(reverse('dashboard_activity'), HTTP_HX_REQUEST='true').content.decode()
        self.assertNotIn('is-many', html)
        self.assertNotIn('data-testid="timeline-names"', html)


class ShortRecordSheetTests(TestCase):
    """C8: a Record sheet opens with the answers a usual record needs."""

    POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}

    def setUp(self):
        from core.models import Horse

        self.client.force_login(make_admin('boss'))
        self.horse = Horse.objects.create(name='Ace', sex='mare')

    def sheet(self, url_name, *args, **query):
        return self.client.get(reverse(url_name, args=args), query, **self.POPUP).content.decode()

    def parts(self, html):
        """(before "More details", inside it, the footer)."""
        before, rest = html.split('<details class="popup-more"', 1)
        inside, after = rest.split('</details>', 1)
        return before, inside, after.split('class="popup-footer"', 1)[1]

    def test_a_new_vaccination_shows_the_type_and_the_date_first(self):
        before, inside, footer = self.parts(self.sheet('vaccination_create', horse=self.horse.pk))
        for name in ('vaccination_type', 'date_given'):
            self.assertIn(f'name="{name}"', before)
        for name in ('service_item', 'new_type_name', 'course_stage', 'next_due_date', 'vet', 'batch_number', 'cost', 'notes'):
            with self.subTest(field=name):
                self.assertIn(f'name="{name}"', inside)
                self.assertNotIn(f'name="{name}"', before)
        # Closed for a new record.
        self.assertTrue(inside.startswith(' data-testid="more-details">'), inside[:60])

    def test_the_footer_is_cancel_and_save_and_add_another_is_in_more_details(self):
        before, inside, footer = self.parts(self.sheet('vaccination_create', horse=self.horse.pk))
        self.assertIn('Save &amp; add another', inside)
        self.assertNotIn('Save &amp; add another', footer)
        self.assertEqual(footer.count('<button'), 2)
        self.assertIn('>Cancel</button>', footer)
        self.assertIn('>Save</button>', footer)

    def test_the_closed_line_says_what_is_in_it(self):
        html = self.sheet('farrier_create', horse=self.horse.pk)
        self.assertIn('<span class="popup-more-hint">farrier, next due date, notes</span>', html)

    def test_a_field_that_must_be_filled_in_is_never_under_more_details(self):
        # A browser cannot show "fill in this field" on a box that is folded away.
        for url_name in ('farrier_create', 'vet_visit_create', 'worming_create', 'egg_count_create', 'vaccination_create'):
            with self.subTest(sheet=url_name):
                before, inside, footer = self.parts(self.sheet(url_name, horse=self.horse.pk))
                self.assertNotIn(' required', inside)

    def test_a_saved_record_opens_with_more_details_open(self):
        from django.utils import timezone

        from health.models import Vaccination, VaccinationType

        record = Vaccination.objects.create(
            horse=self.horse, vaccination_type=VaccinationType.objects.create(name='Flu', interval_months=12),
            date_given=timezone.localdate(),
        )
        html = self.sheet('vaccination_update', record.pk)
        self.assertIn('<details class="popup-more" open data-testid="more-details">', html)

    def test_an_error_in_more_details_opens_it(self):
        from django.utils import timezone

        today = timezone.localdate()
        response = self.client.post(
            reverse('vaccination_create') + f'?horse={self.horse.pk}',
            {'horse': self.horse.pk, 'date_given': today.isoformat(), 'next_due_date': 'not a date', 'cost': '0'},
            **self.POPUP,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('<details class="popup-more" open data-testid="more-details">', response.content.decode())

    def test_the_full_page_and_a_short_form_have_no_more_details(self):
        page = self.client.get(reverse('vaccination_create')).content.decode()
        self.assertNotIn('data-testid="more-details"', page)
        # A medical condition has five fields: all show, and the footer keeps its three buttons.
        condition = self.sheet('condition_create', horse=self.horse.pk)
        self.assertNotIn('data-testid="more-details"', condition)
        self.assertIn('popup-add-another', condition)

    def test_a_date_that_is_months_away_gets_its_own_quick_buttons(self):
        cases = (
            ('vaccination_create', 'next_due_date', '+6m +12m', 'date_given'),
            ('farrier_create', 'next_due_date', '+6w +8w', 'date'),
            ('worming_create', 'next_due_date', '+3m +6m', 'date'),
            ('vet_visit_create', 'follow_up_date', '+1w +2w', 'date'),
        )
        for url_name, field, spec, base in cases:
            with self.subTest(sheet=url_name):
                html = self.sheet(url_name, horse=self.horse.pk)
                box = html.split(f'name="{field}"', 1)[1].split('>', 1)[0]
                self.assertIn(f'data-date-quick="{spec}"', box)
                self.assertIn(f'data-date-quick-from="{base}"', box)

    def test_the_bulk_forms_have_them_too(self):
        html = self.client.get(
            reverse('bulk_health_form'), {'action_type': 'vaccination', 'horse_ids': self.horse.pk}, HTTP_HX_REQUEST='true',
        ).content.decode()
        self.assertIn('data-date-quick="+6m +12m"', html)


class PhoneTextTests(TestCase):
    """C7: text a phone can be read from in a yard."""

    POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}
    css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'input.css').read_text()

    def setUp(self):
        self.user = make_admin('reader', email='reader@example.com')
        self.client.force_login(self.user)

    def size(self):
        import re

        html = self.client.get(reverse('dashboard')).content.decode()
        return re.search(r'<html lang="en" data-ui-size="[a-z]+" data-phone-text="([a-z]+)">', html).group(1)

    def save(self, **extra):
        data = {'action': 'details', 'first_name': 'R', 'last_name': 'D', 'email': 'reader@example.com',
                'phone': '', 'display_size': 'compact'}
        data.update(extra)
        return self.client.post(reverse('profile'), data, **self.POPUP)

    def test_standard_is_the_default(self):
        self.assertEqual(self.size(), 'standard')

    def test_the_profile_offers_the_two_sizes(self):
        html = self.client.get(reverse('profile')).content.decode()
        self.assertIn('Text size on a phone', html)
        select = html.split('name="phone_text_size"', 1)[1].split('</select>', 1)[0]
        self.assertIn('value="standard"', select)
        self.assertIn('value="large"', select)

    def test_large_applies_at_once_and_is_kept(self):
        from core.models import UserProfile

        self.save(phone_text_size='large')
        self.assertEqual(UserProfile.objects.get(user=self.user).phone_text_size, 'large')
        self.assertEqual(self.size(), 'large')
        self.client.logout()
        self.client.force_login(self.user)
        self.assertEqual(self.size(), 'large')

    def test_a_form_sent_without_the_setting_keeps_the_size(self):
        from core.models import UserProfile

        self.save(phone_text_size='large')
        self.save()
        self.assertEqual(UserProfile.objects.get(user=self.user).phone_text_size, 'large')

    def test_an_unknown_value_falls_back_to_standard(self):
        session = self.client.session
        session['_phone_text_size'] = 'huge'
        session.save()
        self.assertEqual(self.size(), 'standard')

    def test_the_sign_in_page_is_standard(self):
        self.client.logout()
        html = self.client.get(reverse('dashboard'), follow=True).content.decode()
        self.assertNotIn('data-phone-text="large"', html)

    def test_large_is_one_eighth_bigger_below_lg_only(self):
        self.assertRegex(
            self.css,
            r'@media \(max-width: 63\.99rem\) \{\s*html\[data-phone-text="large"\] \{ font-size: 112\.5%; \}\s*\}')

    def test_no_text_class_is_under_12px_on_a_phone(self):
        block = self.css.split('/* ── Text on a phone (docs/MOBILE_APP_PLAN.md, C7)', 1)[1]
        # The block is the last in the file, so it has the last word.
        self.assertEqual(block.count('@media (max-width: 639px) {'), 1)
        self.assertTrue(block.rstrip().endswith('}'))
        first_rule = block.split('@media (max-width: 639px) {', 1)[1].split('}', 1)[0]
        for name in (r'.text-\[9px\]', r'.text-\[10px\]', r'.text-\[11px\]', '.tab-bar-label', '.cal-dow', '.log-day',
                     '.data-table-cards :is(tbody, tfoot) td[data-label]::before'):
            with self.subTest(selector=name):
                self.assertIn(name, first_rule)
        self.assertIn('font-size: 0.75rem;', first_rule)

    def test_small_grey_text_is_darker_on_a_phone(self):
        block = self.css.split('/* ── Text on a phone (docs/MOBILE_APP_PLAN.md, C7)', 1)[1]
        self.assertRegex(block, r'\.text-xs\.text-sage,[^{]*\{\s*color: var\(--color-charcoal-light, #4A4A4A\);')


class PreviewSheetTests(TestCase):
    """C9: the horse preview opens with who, what is due and whom to call."""

    PANE = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'horse-preview-body'}

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import Horse, Location, Owner, Placement, RateType

        self.today = timezone.localdate()
        self.days = timedelta
        self.client.force_login(make_admin('boss'))
        self.owner = Owner.objects.create(name='Jo Bloggs', phone='01234 567890')
        self.horse = Horse.objects.create(name='Belina', sex='mare')
        Placement.objects.create(
            horse=self.horse, owner=self.owner,
            location=Location.objects.create(name='Top Field', site='Main'),
            rate_type=RateType.objects.create(name='Full', daily_rate=10),
            start_date=self.today - timedelta(days=30),
        )

    def preview(self):
        return self.client.get(reverse('horse_preview', args=[self.horse.pk]), **self.PANE).content.decode()

    def overdue(self):
        from health.models import Vaccination, VaccinationType

        Vaccination.objects.create(
            horse=self.horse, vaccination_type=VaccinationType.objects.create(name='Flu', interval_months=12),
            date_given=self.today - self.days(days=400), next_due_date=self.today - self.days(days=35),
        )

    def test_with_no_photo_the_small_picture_is_beside_the_name(self):
        html = self.preview()
        who = html.split('data-testid="preview-avatar"', 1)[1].split('</h2>', 1)[0]
        self.assertIn('Belina', who)
        # A tap on it adds a photo.
        self.assertIn('data-popup-title="Add photos for Belina" aria-label="Add a photo of Belina"', who)
        # The large empty block is still there for the side pane, and a sheet hides it.
        self.assertIn('class="preview-hero is-empty"', html)
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'input.css').read_text()
        self.assertRegex(css, r'@media \(max-width: 1023px\) \{\s*\.preview-hero\.is-empty \{ display: none; \}')

    def test_a_role_that_cannot_change_horses_gets_the_picture_and_no_link(self):
        self.client.force_login(make_user_with_access('groom', horses='view'))
        who = self.preview().split('data-testid="preview-avatar"', 1)[1].split('</h2>', 1)[0]
        self.assertNotIn('Add a photo', who)

    def test_what_is_due_is_the_line_under_the_name(self):
        self.overdue()
        html = self.preview()
        line = html.split('data-testid="preview-due"', 1)[1].split('</p>', 1)[0]
        self.assertIn('>Flu<', line)
        self.assertIn('35 days overdue', line)
        # Before the status badges and the figures.
        self.assertLess(html.index('data-testid="preview-due"'), html.index('class="pane-stats"'))

    def test_a_horse_with_nothing_due_has_no_line(self):
        self.assertNotIn('data-testid="preview-due"', self.preview())

    def test_the_line_says_the_first_thing_due_and_how_many_more(self):
        from core import reminders
        from core.views.horses import preview_due

        # A new yard has the farrier reminder switched off.
        reminders.set_reminder('farrier', True)
        admin = make_admin('counter')
        day = self.days
        rows = [
            {'label': 'Passport', 'value': 'On record', 'tone': 'plain'},
            {'label': 'Flu', 'due': self.today + day(days=5), 'kind': 'vaccination'},
            {'label': 'Farrier', 'due': self.today - day(days=3), 'kind': 'farrier'},
            {'label': 'Worming', 'due': self.today + day(days=40), 'kind': 'worming'},
            {'label': 'Tetanus', 'due': None, 'kind': 'vaccination'},
        ]
        self.assertEqual(preview_due(rows, admin, self.today), {
            'label': 'Farrier', 'due_date': self.today - day(days=3), 'severity': 'overdue', 'more': 1,
        })
        # Due in 14 days counts; in 15 it does not.
        soon = [{'label': 'Flu', 'due': self.today + day(days=14), 'kind': 'vaccination'}]
        self.assertEqual(preview_due(soon, admin, self.today)['severity'], 'due')
        later = [{'label': 'Flu', 'due': self.today + day(days=15), 'kind': 'vaccination'}]
        self.assertIsNone(preview_due(later, admin, self.today))

    def test_a_reminder_the_yard_switched_off_is_not_in_the_line(self):
        from core import reminders
        from core.views.horses import preview_due

        rows = [{'label': 'Farrier', 'due': self.today - self.days(days=3), 'kind': 'farrier'}]
        admin = make_admin('counter')
        reminders.set_reminder('farrier', True)
        self.assertIsNotNone(preview_due(rows, admin, self.today))
        reminders.set_reminder('farrier', False)
        self.assertIsNone(preview_due(rows, admin, self.today))

    def test_a_role_with_no_health_gets_no_line(self):
        self.overdue()
        self.client.force_login(make_user_with_access('office', horses='view', owners='view'))
        self.assertNotIn('data-testid="preview-due"', self.preview())

    def test_a_horse_that_left_has_no_line(self):
        self.overdue()
        self.horse.is_active = False
        self.horse.save()
        self.assertNotIn('data-testid="preview-due"', self.preview())

    def test_the_owner_can_be_called_from_the_first_lines(self):
        html = self.preview()
        self.assertIn('href="tel:01234567890"', html)
        self.assertIn('aria-label="Call Jo Bloggs, 01234 567890"', html)
        self.assertLess(html.index('data-testid="preview-call"'), html.index('class="pane-stats"'))

    def test_no_call_link_without_a_number_or_without_the_right_to_see_owners(self):
        self.owner.phone = ''
        self.owner.save()
        self.assertNotIn('data-testid="preview-call"', self.preview())
        self.owner.phone = '01234 567890'
        self.owner.save()
        self.client.force_login(make_user_with_access('groom', horses='view'))
        self.assertNotIn('data-testid="preview-call"', self.preview())


class NotificationRowTests(TestCase):
    """C11: several notices of one kind are one row with one button."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.boss = make_admin('boss')
        self.client.force_login(self.boss)

    def over_capacity(self, *names):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import Horse, Location, Owner, Placement, RateType

        owner = Owner.objects.create(name='Jo Bloggs')
        rate = RateType.objects.create(name='Full', daily_rate=10)
        for name in names:
            field = Location.objects.create(name=name, site='Main', capacity=1)
            for n in range(2):
                Placement.objects.create(
                    horse=Horse.objects.create(name=f'{name} horse {n}', sex='mare'), owner=owner, location=field,
                    rate_type=rate, start_date=timezone.localdate() - timedelta(days=30),
                )

    def panel(self, tab='action'):
        return self.client.get(reverse('notification_panel'), {'tab': tab, 'fresh': '1'}).content.decode()

    def test_notices_of_one_kind_become_one_row_where_the_first_stood(self):
        from types import SimpleNamespace

        from notifications.centre_views import group_rows

        def notice(pk, kind, title, resolved=None, unread=False):
            return SimpleNamespace(pk=pk, kind=kind, title=title, resolved_at=resolved, is_unread=unread)

        rows = [
            notice(1, 'vaccination', 'Bella'),
            notice(2, 'capacity', 'Bottom Field · Colgate'),
            notice(3, 'departure', 'Oldie'),
            notice(4, 'capacity', 'Mare Paddock · California Farm', unread=True),
            notice(5, 'capacity', 'Paddock 1 · Somerford'),
            notice(6, 'capacity', 'Top Field · Colgate'),
            notice(7, 'capacity', 'Old Barn · Colgate', resolved='done'),
        ]
        out = group_rows(rows)
        self.assertEqual([getattr(row, 'pk', 'group') for row in out], [1, 'group', 3, 7])
        group = out[1]
        self.assertEqual(group['title'], '4 locations over capacity')
        self.assertEqual(group['summary'], 'Bottom Field, Mare Paddock, Paddock 1 and 1 more')
        self.assertEqual(group['ids'], [2, 4, 5, 6])
        self.assertTrue(group['is_unread'])
        self.assertEqual(group['url'], reverse('location_list'))

    def test_one_notice_of_a_kind_stays_a_row_of_its_own(self):
        from types import SimpleNamespace

        from notifications.centre_views import group_rows

        rows = [SimpleNamespace(pk=1, kind='capacity', title='Top Field · Main', resolved_at=None, is_unread=False)]
        self.assertEqual(group_rows(rows), rows)

    def test_the_panel_shows_the_group_with_one_button_and_the_places_inside(self):
        self.over_capacity('Top Field', 'Long Meadow', 'Home Paddock')
        html = self.panel()
        self.assertEqual(html.count('data-testid="notif-group"'), 1)
        # Only these notices are in the panel, so the group is the rest of the list.
        group = html.split('data-testid="notif-group"', 1)[1]
        self.assertIn('<p class="notif-title">3 locations over capacity</p>', group)
        self.assertIn('>See locations</a>', group)
        self.assertIn('Show all 3', group)
        for name in ('Top Field', 'Long Meadow', 'Home Paddock'):
            self.assertIn(f'{name} · Main</a>', group)
        # Each place can still be dismissed by itself.
        items = group.split('class="notif-group-items"', 1)[1]
        self.assertEqual(items.count('>Dismiss</button>'), 3)
        # The count on the tab is of notices, not rows.
        self.assertIn('Needs action <b>3</b>', html)

    def test_one_place_over_capacity_is_an_ordinary_row(self):
        self.over_capacity('Top Field')
        html = self.panel()
        self.assertNotIn('data-testid="notif-group"', html)
        self.assertIn('Top Field · Main', html)

    def test_the_needs_action_tab_does_not_repeat_its_name_on_each_row(self):
        self.over_capacity('Top Field')
        self.assertNotIn('<span>Needs action</span>', self.panel('action'))
        self.assertIn('<span>Needs action</span>', self.panel('all'))

    def test_the_when_badge_is_on_the_title_line(self):
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'input.css').read_text()
        self.assertIn('.notif-text > .notif-title { order: 1; flex: 1 1 8rem; min-width: 0; }', css)
        self.assertIn('.notif-text > .notif-meta { order: 2; flex: 0 0 auto;', css)
