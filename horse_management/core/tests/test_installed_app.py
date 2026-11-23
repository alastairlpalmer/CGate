"""The phone app, phase 4: the installed app.

docs/MOBILE_APP_PLAN.md, items D2 to D7. The service worker's choices are
tested in static/js/tests/app_worker.test.js; these tests hold what the
server sends it.
"""

import json
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from core.roles_testutils import make_admin
from core.templatetags.cache_bust import app_version
from core.views import app_shell

STATIC = Path(settings.BASE_DIR) / 'static'


def worker_config(body):
    first_line = body.split('\n', 1)[0]
    assert first_line.startswith('self.APP_SHELL = ') and first_line.endswith(';'), first_line[:60]
    return json.loads(first_line[len('self.APP_SHELL = '):-1])


def static_path(url):
    """/static/js/a.js?v=1 → the file under static/."""
    assert url.startswith(settings.STATIC_URL), url
    return STATIC / url[len(settings.STATIC_URL):].split('?', 1)[0]


class ServiceWorkerTests(TestCase):
    """D4: the worker keeps the app's files on the phone, and no page."""

    def worker(self):
        return self.client.get(reverse('service_worker'))

    def test_it_is_at_the_root_so_it_can_work_for_every_page(self):
        self.assertEqual(reverse('service_worker'), '/sw.js')

    def test_it_needs_no_sign_in_and_is_a_script_the_browser_does_not_keep(self):
        response = self.worker()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/javascript'))
        self.assertEqual(response['Cache-Control'], 'no-cache')

    def test_it_is_the_same_for_someone_signed_in(self):
        before = self.worker().content
        self.client.force_login(make_admin('boss'))
        self.assertEqual(self.worker().content, before)

    def test_only_get(self):
        self.assertEqual(self.client.post(reverse('service_worker')).status_code, 405)

    def test_it_is_told_the_version_and_the_page_for_no_signal(self):
        config = worker_config(self.worker().content.decode())
        self.assertEqual(config['version'], app_version())
        self.assertEqual(config['offline'], reverse('offline'))
        self.assertEqual(config['staticPrefix'], settings.STATIC_URL)
        self.assertEqual(config['skip'], ['/c-vet/'])

    def test_what_the_no_signal_page_needs_must_be_stored(self):
        needed = worker_config(self.worker().content.decode())['needed']
        self.assertEqual(needed[0], reverse('offline'))
        self.assertIn(f'/static/css/styles.css?v={app_version()}', needed)
        self.assertTrue(any(url.endswith('.woff2') for url in needed))

    def test_the_scripts_every_page_loads_are_stored_too(self):
        config = worker_config(self.worker().content.decode())
        wanted = config['wanted']
        self.assertGreater(len(wanted), 10)
        for name in ('htmx.min.js', 'alpine.min.js', 'connection.js', 'app_install.js'):
            with self.subTest(script=name):
                self.assertTrue(any(f'/js/{name}?v=' in url for url in wanted), wanted)
        # A file is in one list, not both.
        self.assertFalse(set(wanted) & set(config['needed']))

    def test_every_file_it_is_told_to_store_exists(self):
        config = worker_config(self.worker().content.decode())
        for url in config['needed'][1:] + config['wanted']:
            with self.subTest(url=url):
                self.assertTrue(static_path(url).is_file(), url)

    def test_the_list_is_read_from_the_page_so_a_new_script_is_stored(self):
        head = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text().split('</head>', 1)[0]
        in_page = set(re.findall(r"{% static_v '((?:js|css)/[^']+)' %}", head))
        self.assertEqual(set(app_shell.head_files()), in_page)

    def test_the_body_is_the_worker_file(self):
        body = self.worker().content.decode().split('\n', 1)[1]
        self.assertEqual(body, (STATIC / app_shell.WORKER_FILE).read_text())

    def test_the_worker_stores_no_answer_from_a_page(self):
        # The only places it stores something: the install, and a file
        # under /static/ ('kept'). A page goes to the network.
        source = (STATIC / app_shell.WORKER_FILE).read_text()
        self.assertEqual(source.count('cache.put('), 2)
        page = source.split('function page(event)', 1)[1].split('self.addEventListener', 1)[0]
        self.assertNotIn('cache.put', page)


class WorkerSwitchTests(TestCase):
    """D4: one setting takes the worker off every phone."""

    def setUp(self):
        self.client.force_login(make_admin('boss'))

    def test_a_page_names_the_worker_and_loads_the_script_that_registers_it(self):
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('<meta name="app-worker" content="/sw.js">', html)
        self.assertRegex(html, r'<script defer src="/static/js/app_install\.js\?v=[^"]+"></script>')

    @override_settings(APP_WORKER_ENABLED=False)
    def test_switched_off_a_page_names_no_worker(self):
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('<meta name="app-worker" content="">', html)

    @override_settings(APP_WORKER_ENABLED=False)
    def test_switched_off_the_worker_removes_itself_and_its_store(self):
        response = self.client.get(reverse('service_worker'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-cache')
        body = response.content.decode()
        self.assertNotIn('APP_SHELL', body)
        self.assertIn('self.registration.unregister()', body)
        self.assertIn("name.indexOf('app-shell-') === 0", body)
        self.assertNotIn("addEventListener('fetch'", body)

    def test_the_removal_worker_deletes_the_store_the_real_one_makes(self):
        source = (STATIC / app_shell.WORKER_FILE).read_text()
        self.assertIn("var PREFIX = 'app-shell-';", source)

    def test_a_tap_does_not_get_the_frame_so_it_does_not_name_the_worker(self):
        html = self.client.get(reverse('dashboard'), HTTP_HX_REQUEST='true', HTTP_HX_BOOSTED='true').content.decode()
        self.assertNotIn('name="app-worker"', html)


class OfflinePageTests(TestCase):
    """D4: the page the worker shows when a page cannot load."""

    def page(self):
        return self.client.get(reverse('offline'))

    def test_it_needs_no_sign_in(self):
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1 class="page-title mt-4">No signal</h1>')

    def test_try_again_is_a_link_to_the_same_address_and_needs_no_script(self):
        self.assertContains(self.page(), '<a href="" class="btn-primary mt-5 w-full" data-testid="offline-retry">Try again</a>')

    def test_it_asks_again_by_itself_when_the_signal_is_back(self):
        self.assertContains(self.page(), "window.addEventListener('online'")

    def test_it_says_nothing_about_the_person_or_the_yard(self):
        from core.models import BusinessSettings

        business = BusinessSettings.get_settings()
        business.business_name = 'Bigyard Livery'
        business.save()
        user = make_admin('zebedee', email='zebedee@example.com')
        self.client.force_login(user)
        html = self.page().content.decode()
        for word in ('zebedee', 'Bigyard', 'csrf'):
            with self.subTest(word=word):
                self.assertNotIn(word, html)

    def test_it_is_one_copy_for_everyone(self):
        signed_out = self.page().content
        self.client.force_login(make_admin('boss'))
        self.assertEqual(self.page().content, signed_out)

    def test_it_uses_only_files_the_worker_must_store(self):
        needed = app_shell.shell_config()['needed']
        html = self.page().content.decode()
        files = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
        self.assertGreaterEqual(len(files), 3)
        for url in files:
            with self.subTest(url=url):
                self.assertIn(url, needed)

    def test_it_is_not_the_app_frame(self):
        html = self.page().content.decode()
        self.assertNotIn('id="mobile-tab-bar"', html)
        self.assertNotIn('htmx', html)
