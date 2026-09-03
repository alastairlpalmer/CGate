"""Guards for phase 4: where Owners sits, and what we call a Location.

"Location" is the model name and the word the interface uses. "Field" is
wrong the moment the record is Big Barn or Stables Block A, and it crept
in once already through the archive work — so it is worth a lint.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.roles_testutils import make_admin, make_user_with_access

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / 'templates'

# "field" is also Django's own word — form fields, update_fields, model
# field classes — so matching the bare word finds mostly framework noise.
# Match the fault instead: a determiner in front of it is what makes it a
# noun for a place ("this field", "every archived field").
FAULT = re.compile(
    r'\b(a|an|the|this|that|these|those|its|their|our|every|each|'
    r'archived|empty|resting|full|no)\s+fields?\b',
    re.I,
)
# Real place names are not the vocabulary fault — Top Field is a name.
PLACE_NAME = re.compile(r'(top|bottom|front|back|home|mare|hay)\s+fields?', re.I)
# The generic form-field renderer documents Django's "field" throughout.
SKIP_FILES = {'includes/form_field.html'}


class LocationVocabularyLintTests(SimpleTestCase):
    """No template should call a Location a "field"."""

    def _offending_lines(self):
        for path in sorted(TEMPLATES_DIR.rglob('*.html')):
            relative = str(path.relative_to(TEMPLATES_DIR))
            if relative in SKIP_FILES:
                continue
            for number, line in enumerate(path.read_text().split('\n'), 1):
                if not FAULT.search(line) or PLACE_NAME.search(line):
                    continue
                yield f"{relative}:{number}: {line.strip()}"

    def test_scan_reaches_the_templates(self):
        """A lint that matches nothing proves nothing."""
        self.assertGreater(len(list(TEMPLATES_DIR.rglob('*.html'))), 50)

    def test_the_rule_catches_the_wording_it_is_meant_to(self):
        self.assertTrue(FAULT.search('This field is out of use.'))
        self.assertTrue(FAULT.search('Restore every archived field on Colgate'))
        self.assertFalse(FAULT.search('{% for field in form %}'))
        self.assertFalse(FAULT.search('class="field-invalid"'))
        self.assertFalse(FAULT.search('Horses in Top Field'))

    def test_no_template_calls_a_location_a_field(self):
        offenders = list(self._offending_lines())
        self.assertEqual(
            offenders, [],
            'Say "location", not "field" — a Location can be a barn or a '
            'stable block:\n  ' + '\n  '.join(offenders),
        )


class TemplateCommentLintTests(SimpleTestCase):
    """`{# … #}` only comments out the rest of ONE line.

    Wrap it over two and Django renders the text to the page. It has
    happened three times in this work and once before it, so catch it in
    the source rather than one rendered page at a time.
    """

    def _unclosed_comments(self):
        for path in sorted(TEMPLATES_DIR.rglob('*.html')):
            for number, line in enumerate(path.read_text().split('\n'), 1):
                if '{#' not in line:
                    continue
                if '#}' in line.split('{#', 1)[1]:
                    continue
                yield (
                    f"{path.relative_to(TEMPLATES_DIR)}:{number}: "
                    f"{line.strip()[:70]}"
                )

    def test_no_template_comment_runs_past_its_line(self):
        offenders = list(self._unclosed_comments())
        self.assertEqual(
            offenders, [],
            'A {# #} comment must open and close on one line, or Django '
            'renders it as body text. Use {% comment %} for several '
            'lines:\n  ' + '\n  '.join(offenders),
        )

    def test_the_rule_reads_a_real_comment_correctly(self):
        self.assertIn('{# ok #}', '{# ok #}')
        one_line = '<div>{# fine #}</div>'
        wrapped = '{# not fine'
        self.assertNotIn('#}', wrapped.split('{#', 1)[1])
        self.assertIn('#}', one_line.split('{#', 1)[1])


class OwnersInFinanceTests(TestCase):
    """Owners belongs to Finance: its page is contacts and billing."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_admin('navver')

    def setUp(self):
        self.client.force_login(self.user)

    def _sidebar(self):
        html = self.client.get(reverse('horse_list')).content.decode()
        return html[:html.index('User footer')]

    def test_owners_sits_under_the_finance_heading(self):
        sidebar = self._sidebar()
        finance = sidebar.index('>Finance<')
        owners = sidebar.index(reverse('owner_list'))
        manage = sidebar.index('>Manage<')
        self.assertLess(manage, finance)
        self.assertLess(finance, owners, 'Owners should follow the Finance heading')

    def test_manage_still_holds_horses_and_locations(self):
        sidebar = self._sidebar()
        manage = sidebar.index('>Manage<')
        finance = sidebar.index('>Finance<')
        for name in ('horse_list', 'location_list'):
            with self.subTest(name=name):
                position = sidebar.index(reverse(name))
                self.assertLess(manage, position)
                self.assertLess(position, finance)

    def test_finance_heading_shows_for_an_owners_only_role(self):
        """Owners alone must still get its group heading."""
        user = make_user_with_access('ownersonly', horses='view', owners='view')
        self.client.force_login(user)
        html = self.client.get(reverse('horse_list')).content.decode()
        self.assertIn('>Finance<', html)
        self.assertIn(reverse('owner_list'), html)

    def test_manage_heading_hidden_when_only_owners_is_visible(self):
        """Owners no longer keeps the Manage heading alive on its own."""
        user = make_user_with_access('financeonly', owners='view')
        self.client.force_login(user)
        html = self.client.get(reverse('owner_list')).content.decode()
        sidebar = html[:html.index('User footer')]
        self.assertNotIn('>Manage<', sidebar)
        self.assertIn('>Finance<', sidebar)
