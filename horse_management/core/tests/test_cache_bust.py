"""The static cache-busting version (core.templatetags.cache_bust).

With WHITENOISE_MAX_AGE at a year, the `?v=` on every stylesheet and script
URL is what makes a browser fetch a new one after a deploy. Hosts that set
a commit variable get that; any other host gets a digest of the static
files, which changes whenever one of them does.
"""
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

from core.templatetags import cache_bust


class StaticDigestTests(SimpleTestCase):

    def test_digest_follows_the_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'css').mkdir()
            (root / 'css' / 'styles.css').write_text('.a{color:red}')
            (root / 'app.js').write_text('one')
            first = cache_bust.static_digest([root])
            self.assertRegex(first, r'^[0-9a-f]{40}$')
            self.assertEqual(cache_bust.static_digest([root]), first, 'same files, same version')
            (root / 'app.js').write_text('two')
            self.assertNotEqual(cache_bust.static_digest([root]), first, 'a changed script changes the version')

    def test_empty_or_missing_roots_give_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cache_bust.static_digest([Path(tmp)]), '')
            self.assertEqual(cache_bust.static_digest([Path(tmp) / 'missing']), '')

    def test_version_prefers_the_hosts_commit_then_the_digest_then_dev(self):
        clean = {k: v for k, v in os.environ.items() if k not in ('VERCEL_GIT_COMMIT_SHA', 'RAILWAY_GIT_COMMIT_SHA')}
        with mock.patch.dict(os.environ, {**clean, 'RAILWAY_GIT_COMMIT_SHA': 'abcdef1234567890'}, clear=True):
            self.assertEqual(cache_bust.version(), 'abcdef12')
        with mock.patch.dict(os.environ, clean, clear=True):
            # The project's own static directory is hashed: never "dev" here.
            self.assertRegex(cache_bust.version(), r'^[0-9a-f]{8}$')
            with tempfile.TemporaryDirectory() as tmp, override_settings(STATICFILES_DIRS=[tmp], STATIC_ROOT=None):
                self.assertEqual(cache_bust.version(), 'dev')

    def test_static_v_carries_the_version(self):
        url = cache_bust.static_v('css/styles.css')
        self.assertTrue(url.startswith('/static/css/styles.css?v='))
        self.assertEqual(url.rsplit('=', 1)[1], cache_bust._VERSION)
        self.assertNotEqual(cache_bust._VERSION, 'dev', 'the repo has static files, so the fallback digest applies')
