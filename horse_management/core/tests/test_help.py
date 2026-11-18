"""The help centre (core/help.py, core/views/help.py, templates/help/).

docs/SIDEBAR_AND_NOTIFICATIONS_PLAN.md, WP5.
"""

import re
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core import help as guides
from core.roles_testutils import make_admin, make_user_with_access

ARTICLES = Path(settings.BASE_DIR) / 'templates' / 'help' / 'articles'


class GuideFileTests(SimpleTestCase):
    """Every guide in the list has a file, every file is in the list, and
    each file keeps the shape the page frame expects."""

    def test_every_guide_has_a_file_and_every_file_a_guide(self):
        files = {p.stem for p in ARTICLES.glob('*.html')}
        self.assertEqual(files, set(guides.GUIDES_BY_SLUG))

    def test_each_guide_renders_and_keeps_the_shape(self):
        for guide in guides.GUIDES:
            with self.subTest(guide=guide.slug):
                source = (ARTICLES / f'{guide.slug}.html').read_text()
                self.assertNotIn('{% extends', source)
                self.assertNotIn('<h1', source)
                self.assertNotIn('<script', source)
                self.assertNotIn('style=', source)
                html = render_to_string(guide.template)
                self.assertIn('class="help-lead"', html)
                ids = re.findall(r'<h2 id="([a-z0-9-]+)"', html)
                self.assertGreaterEqual(len(ids), 3)
                self.assertEqual(len(ids), len(set(ids)), 'h2 ids must be unique')
                self.assertEqual(len(ids), html.count('<h2'), 'every h2 needs an id')
                self.assertIn('class="help-faq"', html)


class HelpPageTests(TestCase):

    def setUp(self):
        self.admin = make_admin('helper')
        self.groom = make_user_with_access('groom', horses='view', locations='view')

    def test_needs_sign_in(self):
        self.assertEqual(self.client.get(reverse('help_index')).status_code, 302)

    def test_index_lists_the_guides_a_role_can_use(self):
        self.client.force_login(self.groom)
        response = self.client.get(reverse('help_index'))
        self.assertContains(response, reverse('help_article', args=['getting-started']))
        for guide in guides.GUIDES:
            if guide.visible_to(self.groom):
                self.assertContains(response, reverse('help_article', args=[guide.slug]))
            else:
                self.assertNotContains(response, reverse('help_article', args=[guide.slug]))

    def test_administrator_sees_every_guide(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('help_index'))
        for guide in guides.GUIDES:
            self.assertContains(response, reverse('help_article', args=[guide.slug]))

    def test_a_guide_for_a_hidden_area_is_404(self):
        self.client.force_login(self.groom)
        hidden = [g for g in guides.GUIDES if not g.visible_to(self.groom)]
        for guide in hidden:
            self.assertEqual(self.client.get(reverse('help_article', args=[guide.slug])).status_code, 404)
        self.assertEqual(self.client.get(reverse('help_article', args=['no-such-guide'])).status_code, 404)

    def test_guide_page_has_its_text_and_the_contact(self):
        self.client.force_login(self.groom)
        response = self.client.get(reverse('help_article', args=['search']))
        self.assertContains(response, '<h1 id="help-title" class="page-title">Search</h1>', html=False)
        self.assertContains(response, 'class="help-lead"')
        self.assertContains(response, 'mailto:help@c-gate.app')
        self.assertContains(response, 'aria-current="page"')

    def test_search_finds_by_title_keyword_and_text(self):
        self.client.force_login(self.admin)
        for query, slug in (('notifications', 'notifications'), ('microchip', 'search'), ('recovery codes', 'profile-and-security')):
            with self.subTest(query=query):
                results = self.client.get(reverse('help_index'), {'q': query}).context['results']
                self.assertIn(guides.GUIDES_BY_SLUG[slug], results)

    def test_search_keeps_to_the_role(self):
        self.client.force_login(self.groom)
        results = self.client.get(reverse('help_index'), {'q': 'invoice'}).context['results']
        self.assertTrue(all(g.visible_to(self.groom) for g in results))

    def test_search_with_no_match_says_so(self):
        self.client.force_login(self.groom)
        response = self.client.get(reverse('help_index'), {'q': 'zzzqqq'})
        self.assertContains(response, 'No guide has these words.')
        self.assertContains(response, 'mailto:help@c-gate.app')

    def test_sidebar_support_and_phone_menu_open_help(self):
        self.client.force_login(self.groom)
        html = self.client.get(reverse('horse_list')).content.decode()
        self.assertIn(f'href="{reverse("help_index")}" class="nav-link" title="Support"', html)
        self.assertIn('Help and support', html)
