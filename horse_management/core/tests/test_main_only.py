"""The pop-up's refresh gets only the parts it swaps (pre-launch review:
141 KB for each save where about 32 KB is enough)."""

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.middleware import extract_element
from core.roles_testutils import make_admin


class ExtractElementTests(SimpleTestCase):
    def test_nested_elements_of_the_same_tag(self):
        html = '<div id="a"><div><div>x</div></div><br><img src="y"></div><div>after</div>'
        self.assertEqual(extract_element(html, 'a'), '<div id="a"><div><div>x</div></div><br><img src="y"></div>')

    def test_over_several_lines(self):
        html = '<body>\n<main id="m" class="c">\n  <p>one</p>\n  <p>two</p>\n</main>\n<footer></footer></body>'
        self.assertEqual(extract_element(html, 'm'), '<main id="m" class="c">\n  <p>one</p>\n  <p>two</p>\n</main>')

    def test_missing(self):
        self.assertIsNone(extract_element('<div id="b"></div>', 'a'))


class MainOnlyTests(TestCase):
    def setUp(self):
        self.client.force_login(make_admin('mainonly'))

    def test_the_refresh_gets_the_title_messages_and_main_only(self):
        full = self.client.get(reverse('owner_list'))
        part = self.client.get(reverse('owner_list'), HTTP_X_MAIN_ONLY='1')
        body = part.content.decode()
        self.assertIn('<title>', body)
        self.assertIn('id="main-content"', body)
        self.assertIn('id="toast-container"', body)
        self.assertNotIn('app-sidebar', body)
        self.assertLess(len(part.content), len(full.content) / 2)
        self.assertIn('X-Main-Only', part['Vary'])

    def test_a_normal_page_load_is_unchanged(self):
        self.assertIn('app-sidebar', self.client.get(reverse('owner_list')).content.decode())

    def test_a_redirect_is_left_alone(self):
        self.client.logout()
        response = self.client.get(reverse('owner_list'), HTTP_X_MAIN_ONLY='1')
        self.assertEqual(response.status_code, 302)
