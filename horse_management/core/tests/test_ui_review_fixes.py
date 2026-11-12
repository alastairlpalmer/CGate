"""Fixes from the November UI review.

The dashboard's site choice folds into a menu from three sites, the horse
page steps to the next horse, and its Details card does not draw an
empty block above the conditions.
"""

from django.test import TestCase
from django.urls import reverse

from core.models import Horse, Location
from core.roles_testutils import make_admin
from core.views.horses import horse_neighbours
from health.models import MedicalCondition


class SitePickerTests(TestCase):
    def setUp(self):
        self.client.force_login(make_admin(username='picker-admin'))

    def sites(self, *names):
        for name in names:
            Location.objects.create(name=f'{name} field', site=name)

    def test_one_site_has_no_choice_to_show(self):
        self.sites('Home')
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'site-switch')
        self.assertNotContains(response, 'site-menu-button')

    def test_two_sites_are_a_switch(self):
        self.sites('Home', 'Hill')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'class="site-switch"')
        self.assertNotContains(response, 'site-menu-button')

    def test_three_sites_fold_into_a_menu(self):
        self.sites('Home', 'Hill', 'River')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'site-menu-button')
        self.assertNotContains(response, 'class="site-switch"')
        self.assertContains(response, 'class="site-menu-item', count=4)  # All sites + three

    def test_the_menu_names_the_chosen_site_and_remembers_it(self):
        self.sites('Home', 'Hill', 'River')
        response = self.client.get(reverse('dashboard'), {'site': 'Hill'})
        self.assertContains(response, 'aria-label="Site: Hill. Change site"')
        # No ?site= the next time: the choice was saved.
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'aria-label="Site: Hill. Change site"')

    def test_the_tools_render_once(self):
        # One copy, moved between the page header and the Near you card.
        self.sites('Home', 'Hill', 'River')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'data-site-tools ', count=1)
        self.assertContains(response, 'data-site-tools-home')


class HorsePageTests(TestCase):
    def setUp(self):
        self.client.force_login(make_admin(username='horse-page-admin'))
        self.apollo = Horse.objects.create(name='Apollo')
        self.bramble = Horse.objects.create(name='Bramble')
        self.conker = Horse.objects.create(name='Conker')
        self.gone = Horse.objects.create(name='Biscuit', is_active=False)

    def test_neighbours_follow_the_name_order(self):
        self.assertEqual(horse_neighbours(self.bramble), (self.apollo, self.conker))
        self.assertEqual(horse_neighbours(self.apollo), (None, self.bramble))
        self.assertEqual(horse_neighbours(self.conker), (self.bramble, None))

    def test_departed_horses_are_stepped_apart(self):
        # Biscuit sorts between Apollo and Bramble but has left the yard.
        self.assertEqual(horse_neighbours(self.apollo)[1], self.bramble)
        self.assertEqual(horse_neighbours(self.gone), (None, None))

    def test_same_name_is_stepped_by_id(self):
        twin = Horse.objects.create(name='Bramble')
        self.assertEqual(horse_neighbours(self.bramble)[1], twin)
        self.assertEqual(horse_neighbours(twin), (self.bramble, self.conker))

    def test_the_page_links_to_both_neighbours(self):
        response = self.client.get(reverse('horse_detail', args=[self.bramble.pk]))
        self.assertContains(response, 'data-testid="horse-stepper"')
        self.assertContains(response, f'href="{reverse("horse_detail", args=[self.apollo.pk])}" class="horse-step" rel="prev"')
        self.assertContains(response, f'href="{reverse("horse_detail", args=[self.conker.pk])}" class="horse-step" rel="next"')

    def test_a_lone_horse_has_no_stepper(self):
        response = self.client.get(reverse('horse_detail', args=[self.gone.pk]))
        self.assertNotContains(response, 'data-testid="horse-stepper"')

    def test_details_card_with_only_conditions_has_no_empty_block(self):
        MedicalCondition.objects.create(horse=self.apollo, name='Laminitis', status='active')
        response = self.client.get(reverse('horse_detail', args=[self.apollo.pk]))
        self.assertContains(response, 'Laminitis')
        self.assertNotContains(response, '<dl class="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">')
        self.assertNotContains(response, 'mt-5 pt-5 border-t border-light-sage')

    def test_details_card_with_notes_and_conditions_keeps_the_divider(self):
        self.apollo.notes = 'Kicks the farrier'
        self.apollo.save()
        MedicalCondition.objects.create(horse=self.apollo, name='Laminitis', status='active')
        response = self.client.get(reverse('horse_detail', args=[self.apollo.pk]))
        self.assertContains(response, 'Kicks the farrier')
        self.assertContains(response, 'mt-5 pt-5 border-t border-light-sage')


class SearchReachTests(TestCase):
    """The app bar's search also finds invoices, health records and a
    horse by the number on its microchip or passport."""

    def setUp(self):
        from datetime import date
        from core.models import Owner
        from health.models import VetVisit
        from invoicing.models import Invoice

        self.admin = make_admin(username='search-admin')
        self.client.force_login(self.admin)
        self.horse = Horse.objects.create(name='Apollo', microchip='981000012345678', passport_number='826ABC777')
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.invoice = Invoice.objects.create(
            owner=self.owner, invoice_number='INV00042',
            period_start=date(2026, 10, 1), period_end=date(2026, 10, 31),
        )
        MedicalCondition.objects.create(horse=self.horse, name='Laminitis', status='active')
        VetVisit.objects.create(horse=self.horse, date=date(2026, 11, 1), reason='Colic check')

    def find(self, q):
        return self.client.get(reverse('quick_find'), {'q': q})

    def test_invoice_by_number(self):
        response = self.find('INV0004')
        self.assertContains(response, reverse('invoice_detail', args=[self.invoice.pk]))
        self.assertContains(response, 'Jo Bloggs')

    def test_a_name_search_does_not_look_for_invoices(self):
        self.assertNotContains(self.find('inv'), 'Invoices')

    def test_condition_and_vet_visit_open_the_horse(self):
        for q, kind in (('lamin', 'Condition'), ('colic', 'Vet visit')):
            with self.subTest(q=q):
                response = self.find(q)
                self.assertContains(response, 'Health records')
                self.assertContains(response, kind)
                self.assertContains(response, reverse('horse_detail', args=[self.horse.pk]))

    def test_horse_by_microchip_and_passport(self):
        self.assertContains(self.find('98100001'), 'Microchip')
        self.assertContains(self.find('826abc7'), 'Passport')
        self.assertContains(self.find('9810 0001'), 'Apollo')

    def test_groups_follow_the_role(self):
        from core.roles_testutils import make_user_with_access
        self.client.force_login(make_user_with_access('yard-only', horses='view'))
        self.assertNotContains(self.find('INV0004'), 'INV00042')
        self.assertNotContains(self.find('lamin'), 'Laminitis')
        self.assertContains(self.find('98100001'), 'Apollo')


class HorsePageRecordTests(TestCase):
    def test_one_set_of_record_buttons_first_in_its_column(self):
        self.client.force_login(make_admin(username='record-admin'))
        horse = Horse.objects.create(name='Apollo')
        body = self.client.get(reverse('horse_detail', args=[horse.pk])).content.decode()
        self.assertEqual(body.count('id="quick-actions"'), 1)
        self.assertLess(body.index('id="quick-actions"'), body.index('Ownership Shares'))
        # The Timeline header no longer repeats four of them.
        self.assertNotIn('+ Vaccination', body)
