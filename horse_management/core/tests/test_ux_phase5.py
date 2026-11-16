"""Shorter forms and a settings page in groups (docs/UX_REVIEW_PLAN.md,
phase 5)."""
from django import forms
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.roles_testutils import make_admin, make_user_with_access
from core.widgets import MoneyInput
from health.forms import FarrierVisitForm, VaccinationForm


class BlankOptionTests(SimpleTestCase):
    def test_django_dashes_read_as_choose(self):
        field = forms.ChoiceField(choices=[('', '---------'), ('a', 'A')])
        html = field.widget.render('x', '')
        self.assertIn('>Choose…</option>', html)
        self.assertNotIn('---------', html)

    def test_a_label_of_its_own_is_kept(self):
        field = forms.ChoiceField(choices=[('', 'Pick one'), ('a', 'A')])
        self.assertIn('>Pick one</option>', field.widget.render('x', ''))


class MoneyInputTests(SimpleTestCase):
    def test_pound_sign_is_drawn_not_typed(self):
        html = MoneyInput(attrs={'class': 'form-input'}).render('cost', '12.50')
        self.assertIn('class="money-input-prefix" aria-hidden="true">£</span>', html)
        self.assertIn('type="number"', html)
        self.assertIn('value="12.50"', html)


class HealthFormTests(TestCase):
    def test_horse_comes_first_with_its_own_empty_label(self):
        for form_class in (VaccinationForm, FarrierVisitForm):
            with self.subTest(form=form_class.__name__):
                form = form_class()
                self.assertEqual(next(iter(form.fields)), 'horse')
                self.assertEqual(form.fields['horse'].empty_label, 'Choose a horse…')

    def test_cost_is_a_money_box(self):
        self.assertIsInstance(VaccinationForm().fields['cost'].widget, MoneyInput)


class SettingsSectionTests(TestCase):
    def test_every_group_is_a_section_with_a_menu_link(self):
        self.client.force_login(make_admin())
        body = self.client.get(reverse('app_settings')).content.decode()
        self.assertIn('x-data="settingsSections"', body)
        for sid in ('business', 'people', 'yard', 'billing', 'you', 'data'):
            with self.subTest(section=sid):
                self.assertIn(f'<section id="{sid}" data-settings-section', body)
                self.assertIn(f'href="#{sid}" class="settings-nav-link"', body)
        self.assertIn('class="settings-save-bar"', body)

    def test_menu_lists_only_the_groups_the_user_can_see(self):
        self.client.force_login(make_user_with_access('plain', horses='view'))
        body = self.client.get(reverse('app_settings')).content.decode()
        self.assertIn('href="#you" class="settings-nav-link"', body)
        self.assertNotIn('href="#business"', body)
        self.assertNotIn('href="#billing"', body)


class SettingsCompactTests(TestCase):
    """The compact Settings page: a menu that reaches each card, sites that
    fold, a way back from each page opened from it, and forms that return."""

    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def page(self):
        return self.client.get(reverse('app_settings')).content.decode()

    def test_each_menu_sublink_points_at_a_card_on_the_page(self):
        import re
        body = self.page()
        targets = re.findall(r'href="#([\w-]+)" class="settings-nav-sublink"', body)
        self.assertGreaterEqual(len(targets), 9)
        for target in targets:
            with self.subTest(target=target):
                self.assertIn(f'id="{target}"', body)

    def test_a_yard_with_one_site_opens_on_its_locations(self):
        from core.models import Location
        Location.objects.create(name='Top Field', site='Home')
        body = self.page()
        self.assertIn('<details class="settings-fold" x-data="settingsFold" data-fold-key="site:Home" open>', body)
        self.assertIn('1 location</span>', body)

    def test_with_more_sites_each_site_is_folded(self):
        from core.models import Location
        for site in ('Home', 'Away'):
            for name in ('Top Field', 'Low Field'):
                Location.objects.create(name=name, site=site)
        body = self.page()
        for site in ('Home', 'Away'):
            self.assertIn(f'<details class="settings-fold" x-data="settingsFold" data-fold-key="site:{site}">', body)
        self.assertIn('2 locations</span>', body)
        # One tick box, one action set: every location is on the page once.
        self.assertEqual(body.count('aria-label="Edit Top Field"'), 2)

    def test_a_site_name_is_escaped_in_the_fold_key(self):
        from core.models import Location
        Location.objects.create(name='Top Field', site='Alicky\'s "A" <b>')
        body = self.page()
        self.assertNotIn('<b>', body.split('data-fold-key="site:')[1].split('>')[0])
        self.assertIn('data-fold-key="site:Alicky&#x27;s &quot;A&quot; &lt;b&gt;"', body)

    def test_pages_opened_from_settings_have_the_way_back(self):
        from core.models import RateType
        rate = RateType.objects.create(name='Grass', daily_rate=5)
        pages = {
            reverse('user_create'): 'set-users',
            reverse('user_update', args=[self.admin.pk]): 'set-users',
            reverse('role_create'): 'set-roles',
            reverse('rate_type_create'): 'set-rates',
            reverse('rate_type_update', args=[rate.pk]): 'set-rates',
            reverse('service_item_create'): 'services',
            reverse('audit_log'): 'set-system',
            reverse('background_tasks'): 'set-system',
            reverse('database_check'): 'set-system',
            reverse('recently_deleted'): 'you',
            reverse('account_security'): 'you',
            reverse('feed_stock_clear'): 'data',
        }
        settings_page = self.page()
        for address, group in pages.items():
            with self.subTest(address=address):
                response = self.client.get(address)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'href="/settings/#{group}" class="settings-back')
                # The place it goes back to is on the Settings page.
                self.assertIn(f'id="{group}"', settings_page)

    def test_a_form_opened_from_settings_returns_to_settings(self):
        from billing.models import ServiceProvider
        from core.models import Location
        from health.models import VaccinationType
        provider = ServiceProvider.objects.create(name='Hill Vets', provider_type='vet')
        vaccine = VaccinationType.objects.create(name='Flu', interval_months=6, reminder_days_before=14)
        field = Location.objects.create(name='Top Field', site='Home')
        cases = [
            (reverse('provider_update', args=[provider.pk]),
             {'name': 'Hill Vets', 'provider_type': 'vet', 'is_active': 'on'},
             '/settings/#set-providers', reverse('provider_list')),
            (reverse('vaccination_type_update', args=[vaccine.pk]),
             {'name': 'Flu', 'interval_months': 6, 'reminder_days_before': 14, 'is_active': 'on'},
             '/settings/#set-vaccinations', reverse('vaccination_type_list')),
            (reverse('location_update', args=[field.pk]),
             {'name': 'Top Field', 'site': 'Home', 'usage': field.usage},
             '/settings/#set-locations', reverse('location_detail', args=[field.pk])),
        ]
        settings_page = self.page()
        for address, data, back, usual in cases:
            with self.subTest(address=address):
                # The Settings page links to the form with the marker.
                self.assertIn(f'href="{address}?from=settings"', settings_page)
                opened = self.client.get(address + '?from=settings')
                self.assertContains(opened, f'href="{back}" class="settings-back')
                self.assertContains(opened, f'<a href="{back}" class="btn-secondary">Cancel</a>')
                self.assertRedirects(
                    self.client.post(address + '?from=settings', data), back, fetch_redirect_response=False)
                # Opened from its own list, it goes where it always went.
                self.assertNotContains(self.client.get(address), 'settings-back')
                self.assertRedirects(self.client.post(address, data), usual, fetch_redirect_response=False)
                # The marker is a fixed word. An address in its place does nothing.
                self.assertRedirects(
                    self.client.post(address + '?from=https://example.com/', data), usual,
                    fetch_redirect_response=False)

    def test_the_built_stylesheet_has_the_classes_the_page_uses(self):
        # static/css/styles.css is built from input.css and committed. A
        # class in the template that is not in the built file draws nothing:
        # .btn-sm was used on 40 buttons and defined nowhere.
        from pathlib import Path

        from django.conf import settings
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'styles.css').read_text()
        for name in ('btn-sm', 'settings-fold', 'settings-row-main', 'settings-nav-sublink',
                     'settings-back', 'settings-label', 'settings-row-state', 'settings-group'):
            with self.subTest(name=name):
                self.assertIn(f'.{name}', css)

    def test_the_forms_opened_from_settings_are_compact(self):
        # .settings-page gives a page the small Settings heading and compact
        # cards (static/css/input.css). A provider and a vaccination type
        # are Settings records, so their forms always carry it. A location
        # has a page of its own: its form carries it only when it is opened
        # from Settings. The fields themselves are compact in every form
        # (CompactFormTests).
        from pathlib import Path

        from django.conf import settings

        from core.models import Location
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'styles.css').read_text()
        # One rule serves the Settings pages and every form with a footer.
        self.assertIn(':is(.settings-page,form:has(.form-footer,.popup-footer)) label[for]', css)
        field = Location.objects.create(name='Top Field', site='Home')
        always = [reverse('user_create'), reverse('role_create'), reverse('rate_type_create'),
                  reverse('service_item_create'), reverse('provider_create'),
                  reverse('vaccination_type_create'), reverse('account_security')]
        for address in always:
            with self.subTest(address=address):
                self.assertContains(self.client.get(address), 'settings-page"')
        edit = reverse('location_update', args=[field.pk])
        self.assertContains(self.client.get(edit + '?from=settings'), 'settings-page"')
        self.assertNotContains(self.client.get(edit), 'settings-page')
        # A page that is not a Settings page keeps its own heading.
        self.assertNotContains(self.client.get(reverse('horse_create')), 'settings-page')


class CompactFormTests(TestCase):
    """Every form with a footer is compact (static/css/input.css, "Compact
    forms"): the stylesheet sets the field and the label, the template sets
    the spacing."""

    LOOSE = ('sm:p-5', 'sm:px-5', 'sm:py-5', 'space-y-5', 'gap-5')

    def form_templates(self):
        from pathlib import Path

        from django.conf import settings
        base = Path(settings.BASE_DIR) / 'templates'
        for path in sorted(base.rglob('*.html')):
            text = path.read_text()
            if 'form-footer' in text or 'popup-footer' in text or 'includes/form_footer.html' in text:
                yield path.relative_to(base), text

    def test_the_built_stylesheet_has_the_rules(self):
        from pathlib import Path

        from django.conf import settings
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'styles.css').read_text()
        self.assertIn('form:has(.form-footer,.popup-footer)', css)
        self.assertIn('label[for]', css)

    def test_no_form_keeps_the_loose_spacing(self):
        # A new form is most often a copy of an old one. This fails if the
        # copy brings back 20px padding or 20px gaps: use px-4 py-3,
        # space-y-3 and gap-x-4 gap-y-3.
        found = 0
        for name, text in self.form_templates():
            found += 1
            for number, line in enumerate(text.splitlines(), 1):
                for loose in self.LOOSE:
                    with self.subTest(template=str(name), line=number):
                        self.assertNotIn(loose, line)
        self.assertGreater(found, 40)

    def test_forms_in_each_area_have_a_footer(self):
        # The rules hang on the footer. A form page that loses its footer
        # class loses the compact fields with it.
        self.client.force_login(make_admin())
        for name in ('horse_create', 'owner_create', 'charge_create', 'yard_cost_create',
                     'vaccination_create', 'vet_visit_create', 'farrier_create',
                     'placement_create', 'location_create', 'invoice_generate'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'form-footer')


class PageTitleTests(SimpleTestCase):
    """The page title is 20px on every screen (static/css/input.css)."""

    def test_the_title_rule_is_one_size(self):
        import re
        from pathlib import Path

        from django.conf import settings
        source = (Path(settings.BASE_DIR) / 'static' / 'css' / 'input.css').read_text()
        rule = re.search(r'^\s*\.page-title \{ @apply ([^;]+); \}', source, re.M).group(1).split()
        self.assertIn('text-xl', rule)
        # No larger size for a wide screen: that was the 30px heading.
        self.assertEqual([name for name in rule if 'text-2xl' in name or 'text-3xl' in name], [])

    def test_no_page_heading_sets_a_larger_size_of_its_own(self):
        import re
        from pathlib import Path

        from django.conf import settings
        base = Path(settings.BASE_DIR) / 'templates'
        # Pages with no sidebar keep their own heading: sign-in, the public
        # privacy page, the farrier's phone page and C-vet.
        apart = ('registration', 'account/_auth_layout.html', 'core/privacy.html', 'farrier', 'cvet', 'emails')
        large = re.compile(r'<h1[^>]*class="[^"]*\btext-(2xl|3xl|4xl|\[2[1-9]px\]|\[[3-9]\dpx\])')
        offenders = []
        for path in sorted(base.rglob('*.html')):
            name = str(path.relative_to(base))
            if name.startswith(apart):
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if large.search(line):
                    offenders.append(f'{name}:{number}')
        self.assertEqual(offenders, [], 'Use class="page-title" for a page heading.')


class LocationSiteFoldTests(TestCase):
    """Each site on the Locations page folds, as on the Settings page."""

    def setUp(self):
        from django.utils import timezone

        from core.models import Horse, Location, Owner, Placement, RateType
        self.client.force_login(make_admin())
        self.busy = Location.objects.create(name='Top Field', site='Home')
        Location.objects.create(name='Far Field', site='Away')
        Location.objects.create(name='Low Field', site='Away')
        Placement.objects.create(
            horse=Horse.objects.create(name='Bramble'), owner=Owner.objects.create(name='Jo Smith'),
            location=self.busy, start_date=timezone.localdate(),
            rate_type=RateType.objects.create(name='Grass', daily_rate=5),
        )

    def fold(self, body, site):
        import re
        return re.search(r'<details class="group-fold" x-data="settingsFold" data-fold-key="locations:%s"( open)?>' % site, body)

    def test_a_site_with_horses_opens_and_an_empty_site_is_one_line(self):
        body = self.client.get(reverse('location_list')).content.decode()
        self.assertEqual(self.fold(body, 'Home').group(1), ' open')
        self.assertIsNone(self.fold(body, 'Away').group(1))
        self.assertIn('1 horse &middot; 1 location', body)
        self.assertIn('0 horses &middot; 2 locations', body)
        # The cards of a closed site are still on the page, for the reader who opens it.
        self.assertIn('Far Field', body)

    def test_a_search_opens_every_site_it_shows(self):
        body = self.client.get(reverse('location_list'), {'search': 'Far'}).content.decode()
        self.assertEqual(self.fold(body, 'Away').group(1), ' open')

    def test_a_yard_with_one_site_opens_it(self):
        # One site, with no horse on it: still open, there is nothing else to show.
        from core.models import Location
        Location.objects.filter(site='Home').update(site='Away')
        from core.models import Placement
        Placement.objects.all().delete()
        body = self.client.get(reverse('location_list')).content.decode()
        self.assertEqual(self.fold(body, 'Away').group(1), ' open')
        self.assertIn('0 horses &middot; 3 locations', body)


class YardBoardFoldTests(LocationSiteFoldTests):
    """On the dashboard an empty site is one line; a site with horses shows its tiles."""

    def board(self):
        return self.client.get(reverse('dashboard')).content.decode()

    def test_an_empty_site_is_one_line_that_opens(self):
        body = self.board()
        self.assertIn('<details class="board-fold" x-data="settingsFold" data-fold-key="board:Away">', body)
        self.assertIn('0 horses &middot; 2 locations', body)
        # Its fields are in the page, for the reader who opens it.
        self.assertIn('Far Field', body)
        # The site with a horse is not folded.
        self.assertNotIn('data-fold-key="board:Home"', body)
        self.assertEqual(body.count('<div class="board-site">'), 1)

    def test_one_site_is_never_folded(self):
        from core.models import Location, Placement
        Location.objects.filter(site='Home').update(site='Away')
        Placement.objects.all().delete()
        body = self.board()
        self.assertNotIn('board-fold', body)
        self.assertEqual(body.count('<div class="board-site">'), 1)


class ListRowTests(LocationSiteFoldTests):
    """Horse rows are one line, and the filters above a list fold on a phone."""

    def test_a_horse_with_no_age_colour_or_sex_has_no_stray_dots(self):
        from core.models import Horse
        Horse.objects.filter(name='Bramble').update(color='', sex='')
        body = self.client.get(reverse('horse_list')).content.decode()
        detail = body.split('<div class="horse-name-detail">')[1].split('</div>')[0]
        self.assertNotIn('·', detail)

    def test_the_details_follow_the_name(self):
        body = self.client.get(reverse('horse_list')).content.decode()
        self.assertIn('<div class="horse-name">', body)
        self.assertIn('md:w-6 md:h-6', body)

    def test_a_list_has_filters_that_fold(self):
        for name in ('vaccination_list', 'worming_list', 'vet_visit_list', 'egg_count_list', 'condition_list'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertContains(response, 'class="filter-toggle"')
                self.assertContains(response, 'class="filter-bar"')


class DensityTests(SimpleTestCase):
    """The shared parts are the compact size (static/css/input.css)."""

    def rule(self, selector):
        import re
        from pathlib import Path

        from django.conf import settings
        source = (Path(settings.BASE_DIR) / 'static' / 'css' / 'input.css').read_text()
        return re.search(r'^\s*%s \{ @apply ([^;]+); \}' % re.escape(selector), source, re.M).group(1).split()

    def test_card_headers_tables_and_tiles(self):
        self.assertIn('py-2', self.rule('.section-header'))
        self.assertIn('text-sm', self.rule('.section-title'))
        self.assertIn('py-2', self.rule('.data-table td'))
        self.assertIn('text-2xl', self.rule('.kpi-value'))

    def test_table_rows_have_no_stripes(self):
        from pathlib import Path

        from django.conf import settings
        source = (Path(settings.BASE_DIR) / 'static' / 'css' / 'input.css').read_text()
        self.assertNotIn('.data-table tbody tr:nth-child(even)', source)
        self.assertNotIn('.list-card:nth-child(even)', source)
