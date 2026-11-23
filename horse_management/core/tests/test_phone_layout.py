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
