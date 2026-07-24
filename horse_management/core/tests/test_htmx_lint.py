"""Lint-style guard for the htmx attribute-inheritance bug class.

The boosted <body> in base.html sets hx-select="#main-content",
hx-swap="outerHTML show:window:top" and hx-push-url="true", and htmx
inherits all three onto every descendant element that issues its own
hx-get/hx-post request. An element that swaps in a partial response but
doesn't override them blanks its target (a partial has no #main-content
to select) and rewrites the address bar to the partial/POST endpoint's
URL — the create-invoice blank-page bug, which also existed on the
health dashboard tabs/filters/pagination, the pending-departures
confirms, the bulk health form and the Xero status badge.

Rule enforced here: every template element carrying hx-get or hx-post
must make an explicit choice for
- hx-select ("unset" for partial responses, a real selector for
  full-page responses) whenever it targets anything other than
  #main-content, and
- hx-push-url ("false" for widget refreshes and POSTs; "true" or a
  concrete URL only when that URL also works as a full page load).
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / 'templates'

# A start tag, tolerating ">" inside quoted attribute values (Alpine
# expressions like @input="... length >= 2" would otherwise truncate it).
TAG_RE = re.compile(r"<\w[\w-]*(?:[^<>\"']|\"[^\"]*\"|'[^']*')*>", re.S)

HX_VERB_RE = re.compile(r'\bhx-(?:get|post|put|patch|delete)=')
HX_TARGET_RE = re.compile(r'hx-target="([^"]*)"')


class HtmxInheritanceLintTests(SimpleTestCase):

    def _hx_tags(self):
        for path in sorted(TEMPLATES_DIR.rglob('*.html')):
            source = path.read_text()
            for tag in TAG_RE.finditer(source):
                text = tag.group(0)
                if HX_VERB_RE.search(text):
                    yield path.relative_to(TEMPLATES_DIR), text

    def test_all_templates_scanned(self):
        """The scan must actually find the known htmx call sites."""
        tags = list(self._hx_tags())
        self.assertGreaterEqual(
            len(tags), 20,
            'htmx tag scan found suspiciously few call sites — '
            'has the template layout or the tag regex broken?'
        )

    def test_partial_swapping_elements_override_hx_select(self):
        """Anything targeting a non-#main-content element must set
        hx-select explicitly, or the inherited "#main-content" selects
        nothing from a partial response and the swap blanks the target."""
        offenders = []
        for path, text in self._hx_tags():
            m = HX_TARGET_RE.search(text)
            if m and m.group(1) != '#main-content' and 'hx-select' not in text:
                offenders.append(f'{path}: {" ".join(text.split())[:120]}')
        self.assertEqual(
            offenders, [],
            'htmx elements inheriting hx-select="#main-content" from the '
            'boosted <body> (add hx-select="unset" for partial responses, '
            'or a real selector for full-page responses):\n'
            + '\n'.join(offenders)
        )

    def test_every_hx_element_decides_push_url(self):
        """Without an explicit hx-push-url, the body's hx-push-url="true"
        is inherited and the address bar is rewritten to the partial or
        POST endpoint URL (CSRF tokens included, for GET-serialized
        forms). Every hx-get/hx-post element must decide explicitly."""
        offenders = []
        for path, text in self._hx_tags():
            if 'hx-push-url' not in text:
                offenders.append(f'{path}: {" ".join(text.split())[:120]}')
        self.assertEqual(
            offenders, [],
            'htmx elements inheriting hx-push-url="true" from the boosted '
            '<body> (add hx-push-url="false", or "true"/a URL only if that '
            'URL works as a full-page load):\n'
            + '\n'.join(offenders)
        )
