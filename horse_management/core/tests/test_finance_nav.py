"""The Finance group of the sidebar: Owners and one Finances item, whose
four pages share a tab bar (includes/_finance_tabs.html)."""

import re

from django.test import TestCase
from django.urls import reverse

from core.roles_testutils import make_admin, make_user_with_access

FINANCE_PAGES = ('finances', 'invoice_list', 'charge_list', 'costs_list')


def _sidebar(html):
    start = html.index('<aside class="app-sidebar')
    return html[start:html.index('User footer', start)]


def _finance_link(sidebar):
    return re.search(r'<a href="([^"]+)"\s+data-nav-match="/finances/[^"]*"\s+class="([^"]+)" title="Finances">', sidebar)


class FinanceSidebarTests(TestCase):

    def setUp(self):
        self.admin = make_admin('moneyboss')
        self.client.force_login(self.admin)

    def test_finance_group_has_two_items(self):
        sidebar = _sidebar(self.client.get(reverse('horse_list')).content.decode())
        finance = sidebar[sidebar.index('>Finance<'):]
        self.assertEqual(finance.count('<a href='), 2)
        self.assertIn(f'href="{reverse("owner_list")}"', finance)
        self.assertIsNotNone(_finance_link(finance))
        for name in ('invoice_list', 'charge_list', 'costs_list'):
            self.assertNotIn(f'href="{reverse(name)}"', finance)

    def test_finances_is_marked_on_every_tab(self):
        for name in FINANCE_PAGES + ('aged_debtors',):
            with self.subTest(page=name):
                link = _finance_link(_sidebar(self.client.get(reverse(name)).content.decode()))
                self.assertEqual(link.group(2), 'nav-link-active')

    def test_finances_is_not_marked_elsewhere(self):
        link = _finance_link(_sidebar(self.client.get(reverse('horse_list')).content.decode()))
        self.assertEqual(link.group(2), 'nav-link')

    def test_phone_menu_has_one_finances_item(self):
        html = self.client.get(reverse('horse_list')).content.decode()
        sheet = html[html.index('id="more-sheet"'):]
        sheet = sheet[sheet.index('>Finance<'):sheet.index('<!-- Account -->')]
        self.assertEqual(sheet.count('<a href='), 2)


class FinanceTabTests(TestCase):

    def test_every_page_has_the_tabs_with_its_own_marked(self):
        self.client.force_login(make_admin('tabber'))
        pages = {'finances': 'Overview', 'invoice_list': 'Invoices', 'charge_list': 'Charges',
                 'costs_list': 'Costs', 'aged_debtors': 'Invoices'}
        for name, label in pages.items():
            with self.subTest(page=name):
                html = self.client.get(reverse(name)).content.decode()
                self.assertIn('<nav class="finance-tabs" aria-label="Finances">', html)
                self.assertRegex(html, rf'class="tab-underline-active[^"]*" aria-current="page">{label}</a>')

    def test_tabs_follow_the_role(self):
        user = make_user_with_access('bookkeeper', invoices='view', charges='view')
        self.client.force_login(user)
        html = self.client.get(reverse('invoice_list')).content.decode()
        tabs = html[html.index('class="finance-tabs"'):html.index('</nav>', html.index('class="finance-tabs"'))]
        self.assertIn(reverse('invoice_list'), tabs)
        self.assertIn(reverse('charge_list'), tabs)
        self.assertNotIn(reverse('finances'), tabs)
        self.assertNotIn(reverse('costs_list'), tabs)

    def test_link_opens_the_first_tab_the_role_can_see(self):
        cases = (
            (make_admin('all'), 'finances'),
            (make_user_with_access('inv', invoices='view'), 'invoice_list'),
            (make_user_with_access('chg', charges='view'), 'charge_list'),
            (make_user_with_access('cst', costs='view'), 'costs_list'),
        )
        for user, first in cases:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                html = self.client.get(reverse(first)).content.decode()
                self.assertEqual(_finance_link(_sidebar(html)).group(1), reverse(first))

    def test_no_finances_item_without_money_access(self):
        self.client.force_login(make_user_with_access('groom', horses='view'))
        html = self.client.get(reverse('horse_list')).content.decode()
        self.assertNotIn('title="Finances"', html)

    def test_invoices_tab_reaches_aged_debtors(self):
        self.client.force_login(make_admin('chaser'))
        self.assertContains(self.client.get(reverse('invoice_list')), f'href="{reverse("aged_debtors")}"')
