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


class InstallHelpTests(TestCase):
    """D2: help to put the app on a phone."""

    def setUp(self):
        self.client.force_login(make_admin('boss'))

    def card(self):
        html = self.client.get(reverse('dashboard')).content.decode()
        return html.split('data-install-card', 1)[1].split('</section>', 1)[0]

    def test_the_dashboard_holds_the_card_and_it_is_hidden_until_the_script_shows_it(self):
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertEqual(html.count('data-install-card'), 1)
        self.assertIn('<section class="install-card" data-install-card hidden', html)

    def test_the_card_has_a_button_for_android_and_two_steps_for_an_iphone(self):
        card = self.card()
        self.assertIn('<div data-install-part="button" hidden>', card)
        self.assertIn('data-install-now>Install the app</button>', card)
        self.assertIn('<div data-install-part="steps" hidden>', card)
        steps = card.split('data-install-part="steps"', 1)[1].split('</ol>', 1)[0]
        self.assertEqual(steps.count('<li>'), 2)
        self.assertLess(steps.index('<strong>Share</strong>'), steps.index('<strong>Add to Home Screen</strong>'))

    def test_the_card_can_be_closed_and_points_to_help(self):
        card = self.card()
        self.assertIn('data-install-close aria-label="Close. The steps are in Help."', card)
        self.assertIn(f'href="{reverse("help_article", args=["install-the-app"])}"', card)

    def test_the_card_is_on_the_dashboard_only(self):
        for name in ('horse_list', 'health_dashboard', 'owner_list'):
            with self.subTest(page=name):
                self.assertNotIn('data-install-card', self.client.get(reverse(name)).content.decode())

    def test_a_card_that_is_hidden_stays_hidden_with_its_flex_style(self):
        css = (STATIC / 'css' / 'input.css').read_text()
        self.assertIn('.install-card[hidden] { display: none; }', css)

    def test_help_has_the_steps_for_both_kinds_of_phone(self):
        response = self.client.get(reverse('help_article', args=['install-the-app']))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for text in ('Install on an iPhone or iPad', 'Install on an Android phone', '<strong>Add to Home Screen</strong>',
                     '<strong>Install the app</strong>', 'No signal'):
            with self.subTest(text=text):
                self.assertIn(text, html)

    def test_every_role_sees_the_guide_and_search_finds_it(self):
        from core import help as guides
        from core.roles_testutils import make_user_with_access

        groom = make_user_with_access('groom', horses='view')
        guide = guides.GUIDES_BY_SLUG['install-the-app']
        self.assertTrue(guide.visible_to(groom))
        for query in ('install', 'home screen', 'iphone', 'android'):
            with self.subTest(query=query):
                self.assertIn(guide, guides.search(groom, query))

    def test_getting_started_points_to_it(self):
        html = self.client.get(reverse('help_article', args=['getting-started'])).content.decode()
        self.assertIn(f'href="{reverse("help_article", args=["install-the-app"])}"', html)


class SharePdfTests(TestCase):
    """D6: on a phone an invoice's PDF goes to the share sheet."""

    def setUp(self):
        from datetime import date
        from decimal import Decimal

        from core.models import Owner
        from invoicing.models import Invoice

        self.client.force_login(make_admin('boss'))
        self.owner = Owner.objects.create(name='Helen Marsh', email='helen@example.com')
        self.invoice = Invoice.objects.create(
            owner=self.owner, invoice_number='INV00042',
            period_start=date(2026, 10, 1), period_end=date(2026, 10, 31),
            subtotal=Decimal('186.00'), total=Decimal('186.00'), status=Invoice.Status.SENT,
        )
        self.pdf = reverse('invoice_pdf', args=[self.invoice.pk])
        self.marks = 'data-share-file="INV00042.pdf" data-share-title="Invoice INV00042"'

    def links(self, html):
        """Each <a> to this invoice's PDF, as its opening tag."""
        return re.findall(r'<a href="%s[^"]*"[^>]*>' % re.escape(self.pdf), html)

    def test_the_invoice_page_marks_its_pdf_link(self):
        html = self.client.get(reverse('invoice_detail', args=[self.invoice.pk])).content.decode()
        links = self.links(html)
        self.assertGreaterEqual(len(links), 1)
        self.assertTrue(all(self.marks in link for link in links), links)

    def test_the_invoice_list_marks_its_pdf_links(self):
        html = self.client.get(reverse('invoice_list')).content.decode()
        links = self.links(html)
        self.assertGreaterEqual(len(links), 1)
        self.assertTrue(all(self.marks in link for link in links), links)

    def test_the_preview_marks_download_and_leaves_open_in_a_new_tab(self):
        pane = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'invoice-preview-body'}
        html = self.client.get(reverse('invoice_pane', args=[self.invoice.pk]), {'view': 'pdf'}, **pane).content.decode()
        links = self.links(html)
        self.assertGreaterEqual(len(links), 2)
        for link in links:
            with self.subTest(link=link[:70]):
                if '?inline=1' in link:
                    # A new tab has its own way back.
                    self.assertNotIn('data-share-file', link)
                else:
                    self.assertIn(self.marks, link)

    def test_the_statement_marks_its_pdf_link(self):
        html = self.client.get(reverse('owner_statement', args=[self.owner.pk])).content.decode()
        link = html.split(f'href="{reverse("owner_statement_pdf", args=[self.owner.pk])}"', 1)[1].split('>', 1)[0]
        self.assertIn('data-share-file="Statement - Helen Marsh.pdf"', link)
        self.assertIn('data-share-title="Statement for Helen Marsh"', link)

    def test_the_link_still_works_with_no_script(self):
        # The marks add to the link; its address and hx-boost="false" stay.
        html = self.client.get(reverse('invoice_detail', args=[self.invoice.pk])).content.decode()
        link = self.links(html)[0]
        self.assertIn('hx-boost="false"', link)
        response = self.client.get(self.pdf)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_every_page_loads_the_script(self):
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertRegex(html, r'<script defer src="/static/js/share_file\.js\?v=[^"]+"></script>')


@override_settings(POSTHOG_API_KEY='phc_test', POSTHOG_SEND_USERNAMES=False)
class PhoneEventTests(TestCase):
    """D7: the usage figures can tell the installed app from a browser tab."""

    def setUp(self):
        from unittest.mock import MagicMock, patch

        from core import analytics

        analytics._client = None
        patcher = patch.object(analytics, 'get_client', return_value=MagicMock())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: setattr(analytics, '_client', None))
        self.client.force_login(make_admin('boss'))

    def test_each_event_says_installed_or_browser(self):
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn("ph.register({ app_display: standalone ? 'installed' : 'browser' });", html)

    def test_the_events_are_named_in_the_scripts(self):
        connection = (STATIC / 'js' / 'connection.js').read_text()
        for name in ('app request failed', 'app slow tap', 'app no signal'):
            with self.subTest(event=name):
                self.assertIn(f"Yardway.track('{name}'", connection)
        self.assertIn("track('app installed'", (STATIC / 'js' / 'app_install.js').read_text())

    def test_an_event_takes_its_address_through_the_pattern(self):
        # No event may hold the number of a horse or an owner.
        connection = (STATIC / 'js' / 'connection.js').read_text()
        self.assertEqual(connection.count('path: pathPattern(config.path)'), 2)
        self.assertNotIn('path: config.path', connection)


class FailedBackgroundRequestTests(TestCase):
    """Found with the events: a preview that failed was also an unhandled error."""

    def test_each_htmx_ajax_call_handles_its_failure(self):
        for name in ('split_view.js', 'locations_board.js', 'locations_mobile.js', 'notifications.js'):
            with self.subTest(script=name):
                source = (STATIC / 'js' / name).read_text()
                calls = re.findall(r'htmx\.ajax\(', source.split('*/', 1)[-1])
                code_calls = [m for m in re.finditer(r"^\s*(?:window\.)?htmx\.ajax\('GET'", source, re.M)]
                self.assertGreaterEqual(len(code_calls), 1, calls)
                self.assertEqual(source.count('.catch(function () { /* said by js/connection.js */ })'), len(code_calls))
