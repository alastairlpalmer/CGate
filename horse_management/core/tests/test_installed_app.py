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


class ManifestTests(TestCase):
    """D3: what a phone reads to install the app."""

    manifest = json.loads((STATIC / 'manifest.json').read_text())

    def test_it_has_an_id_so_a_new_start_address_is_still_the_same_app(self):
        self.assertEqual(self.manifest['id'], '/')
        self.assertEqual(self.manifest['start_url'], '/')
        self.assertEqual(self.manifest['scope'], '/')
        self.assertEqual(self.manifest['display'], 'standalone')

    def test_it_says_what_kind_of_app_it_is(self):
        self.assertEqual(self.manifest['categories'], ['business', 'productivity'])
        self.assertEqual(self.manifest['lang'], 'en-GB')

    def test_the_shortcuts_are_the_jobs_done_on_a_phone(self):
        shortcuts = [(item['name'], item['url']) for item in self.manifest['shortcuts']]
        self.assertEqual(shortcuts, [
            ('Find a horse', reverse('horse_list')),
            ('Log arrival', reverse('horse_new_arrival')),
            ('Health', reverse('health_dashboard')),
        ])

    def test_every_picture_it_names_exists_and_has_the_size_it_says(self):
        from PIL import Image

        pictures = self.manifest['icons'] + self.manifest['screenshots']
        pictures += [icon for item in self.manifest['shortcuts'] for icon in item['icons']]
        self.assertGreaterEqual(len(pictures), 10)
        for picture in pictures:
            with self.subTest(src=picture['src']):
                path = static_path(picture['src'])
                self.assertTrue(path.is_file(), picture['src'])
                with Image.open(path) as image:
                    self.assertEqual('{}x{}'.format(*image.size), picture['sizes'])

    def test_the_screenshots_are_phone_screens(self):
        shots = self.manifest['screenshots']
        self.assertEqual(len(shots), 3)
        for shot in shots:
            with self.subTest(src=shot['src']):
                self.assertEqual(shot['form_factor'], 'narrow')
                self.assertTrue(shot['label'])
                # Small enough to show at once in the install dialog.
                self.assertLess(static_path(shot['src']).stat().st_size, 200_000)


class StartImageTests(TestCase):
    """D3: an iPhone shows the app's picture while the app starts."""

    def test_there_is_a_picture_of_the_exact_size_of_each_screen(self):
        from PIL import Image

        from core import start_images

        self.assertGreaterEqual(len(start_images.SCREENS), 15)
        for width, height, ratio in start_images.SCREENS:
            with self.subTest(screen=(width, height, ratio)):
                path = STATIC / start_images.file_name(width, height, ratio)
                self.assertTrue(path.is_file(), path.name)
                with Image.open(path) as image:
                    self.assertEqual(image.size, (width * ratio, height * ratio))

    def test_no_two_screens_share_a_picture_or_a_rule(self):
        from core import start_images

        links = start_images.links()
        self.assertEqual(len({item['file'] for item in links}), len(links))
        self.assertEqual(len({item['media'] for item in links}), len(links))

    def test_the_pictures_are_small_files(self):
        total = sum(path.stat().st_size for path in (STATIC / 'start').glob('*.png'))
        self.assertLess(total, 400_000, f'{total} bytes')

    def assert_names_the_pictures(self, html):
        from core import start_images

        self.assertEqual(html.count('rel="apple-touch-startup-image"'), len(start_images.SCREENS))
        self.assertIn(
            '<link rel="apple-touch-startup-image" media="(device-width: 390px) and (device-height: 844px) '
            'and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="/static/start/start-1170x2532.png">',
            html)
        self.assertIn('<meta name="apple-mobile-web-app-capable" content="yes">', html)
        self.assertIn('<meta name="apple-mobile-web-app-status-bar-style" content="default">', html)

    def test_a_page_of_the_app_names_them(self):
        self.client.force_login(make_admin('boss'))
        self.assert_names_the_pictures(self.client.get(reverse('dashboard')).content.decode())

    def test_the_sign_in_page_names_them(self):
        self.assert_names_the_pictures(self.client.get(reverse('login')).content.decode())

    def test_a_tap_does_not_download_them_again(self):
        self.client.force_login(make_admin('boss'))
        html = self.client.get(reverse('dashboard'), HTTP_HX_REQUEST='true', HTTP_HX_BOOSTED='true').content.decode()
        self.assertNotIn('apple-touch-startup-image', html)


class HorseOwnerLineTests(TestCase):
    """Found with the manifest pictures: a long owner line broke each part's words."""

    def test_the_owner_line_wraps_as_whole_parts(self):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import Horse, Location, Owner, Placement, RateType

        self.client.force_login(make_admin('boss'))
        horse = Horse.objects.create(name='Bramble', sex='mare')
        Placement.objects.create(
            horse=horse, owner=Owner.objects.create(name='Helen Marsh', phone='07700 900101'),
            location=Location.objects.create(name='Top Field', site='Home Farm'),
            rate_type=RateType.objects.create(name='Grass', daily_rate=6),
            start_date=timezone.localdate() - timedelta(days=30),
        )
        html = self.client.get(reverse('horse_detail', args=[horse.pk])).content.decode()
        line = html.split('data-testid="header-owner"', 1)
        self.assertIn('flex flex-wrap items-center gap-x-1.5"', line[0][-120:])
        owner = line[1].split('</p>', 1)[0]
        self.assertIn('<span class="whitespace-nowrap">Billed to</span>', owner)
        self.assertIn('<span class="text-sage whitespace-nowrap">(from the stay)</span>', owner)
