"""Horse page, phase 4 (docs/HORSE_PAGE_UX_REVIEW.md section 7): D1 the live
chip and UELN check, E1 the list the user came from, E2 the History panel,
C5 focus, C8 facts not only in tooltips, C9 touch targets."""

from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, HorseIdentifier, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from health.models import VetVisit

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}
CHIP = '826098000123456'


class Base(TestCase):

    def setUp(self):
        self.today = timezone.localdate()
        self.admin = make_admin()
        self.client.force_login(self.admin)
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.field = Location.objects.create(name='Top Field', site='Main')
        self.barn = Location.objects.create(name='Back Barn', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)

    def horse(self, name, location=None, **kw):
        horse = Horse.objects.create(name=name, **kw)
        Placement.objects.create(horse=horse, owner=self.owner, location=location or self.field,
                                 rate_type=self.rate, start_date=self.today - timedelta(days=30))
        return horse

    def page(self, horse, query=''):
        url = reverse('horse_detail', args=[horse.pk])
        return self.client.get(url + (f'?from={quote(query)}' if query else ''))


class LiveCheckTests(Base):
    """D1: the line under the chip and UELN boxes while they are typed."""

    def check(self, **params):
        return self.client.get(reverse('horse_identity_check'), params)

    def test_a_standard_chip(self):
        response = self.check(microchip='826 098-000123456')
        self.assertContains(response, '826 098 000 123 456')
        self.assertContains(response, '15 digits')
        self.assertContains(response, 'UK chip')

    def test_a_short_chip(self):
        response = self.check(microchip='12345')
        self.assertContains(response, '5 characters, not the usual 15')

    def test_also_on_another_horse_but_not_on_itself(self):
        rosie = Horse.objects.create(name='Rosie', microchip=CHIP, is_active=False)
        mine = Horse.objects.create(name='Mine', microchip=CHIP)
        response = self.check(microchip=CHIP, horse=mine.pk)
        self.assertContains(response, 'Also on')
        self.assertContains(response, 'Rosie</a> (departed)')
        self.assertNotContains(response, 'Mine</a>')
        response = self.check(microchip=CHIP, horse=rosie.pk)
        self.assertContains(response, 'Mine</a>')

    def test_ueln(self):
        self.assertContains(self.check(ueln='826002 000123456'), '826002 000123456')
        self.assertContains(self.check(ueln='826002 000123456'), '15 characters')
        self.assertContains(self.check(ueln='82600200'), 'Check it against the passport')

    def test_empty_and_no_field_answer_nothing(self):
        self.assertEqual(self.check(microchip='').content, b'')
        self.assertEqual(self.check().content, b'')

    def test_needs_full_horse_access(self):
        Horse.objects.create(name='Rosie', microchip=CHIP)
        self.client.force_login(make_user_with_access('v', horses='view'))
        self.assertNotContains(self.check(microchip=CHIP), 'Rosie', status_code=self.check(microchip=CHIP).status_code)

    def test_the_quick_edit_boxes_ask_while_typing(self):
        horse = self.horse('Avicii')
        body = self.client.get(reverse('horse_quick_edit', args=[horse.pk]), **POPUP).content.decode()
        chip = body.split('name="microchip"', 1)[1].split('>', 1)[0]
        self.assertIn(f'hx-get="{reverse("horse_identity_check")}"', chip)
        self.assertIn('inputmode="numeric"', chip)
        self.assertIn(f'hx-vals="{{&quot;horse&quot;: {horse.pk}}}"', chip)
        ueln = body.split('name="ueln"', 1)[1].split('>', 1)[0]
        self.assertIn('autocapitalize="characters"', ueln)
        self.assertEqual(body.count('class="id-check'), 2)


class ListTests(Base):
    """E1: back and previous / next stay inside the list the user came from."""

    def setUp(self):
        super().setUp()
        self.a = self.horse('Alpha', self.barn)
        self.b = self.horse('Bravo')
        self.c = self.horse('Charlie', self.barn)
        self.d = self.horse('Delta', self.barn)

    def test_a_filtered_list_links_with_its_query_and_the_page_steps_inside_it(self):
        query = f'location={self.barn.pk}'
        body = self.client.get(reverse('horse_list') + '?' + query).content.decode()
        self.assertIn(f'{reverse("horse_detail", args=[self.c.pk])}?from={quote(query, safe="")}', body)
        response = self.page(self.c, query)
        self.assertEqual(response.context['previous_horse'], self.a)  # Bravo is not in the barn
        self.assertEqual(response.context['next_horse'], self.d)
        body = response.content.decode()
        self.assertIn(f'{reverse("horse_list")}?{query}', body.split('data-testid="back-to-list"', 1)[0][-200:])
        self.assertIn('Back to the list', body)
        self.assertIn('>2 of 3<', body)
        self.assertIn(f'{reverse("horse_detail", args=[self.d.pk])}?from={quote(query, safe="")}', body)

    def test_the_last_horse_has_no_next_and_keeps_its_place(self):
        query = f'location={self.barn.pk}'
        self.client.get(reverse('horse_list') + '?' + query)
        response = self.page(self.d, query)
        self.assertIsNone(response.context['next_horse'])
        self.assertContains(response, '>3 of 3<')

    def test_no_list_means_all_horses_a_to_z(self):
        response = self.page(self.c)
        self.assertEqual((response.context['previous_horse'], response.context['next_horse']), (self.b, self.d))
        self.assertContains(response, 'All horses')
        self.assertNotContains(response, 'data-testid="list-place"')

    def test_a_list_the_session_does_not_hold_falls_back_but_keeps_the_way_back(self):
        query = f'location={self.barn.pk}'
        response = self.page(self.c, query)  # the list was never opened in this session
        self.assertEqual(response.context['next_horse'], self.d)
        self.assertEqual(response.context['previous_horse'], self.b)
        self.assertContains(response, 'Back to the list')

    def test_needs_action_list(self):
        for horse in (self.b, self.d):
            VetVisit.objects.create(horse=horse, date=self.today - timedelta(days=20), reason='Check',
                                    follow_up_date=self.today - timedelta(days=3))
        self.client.get(reverse('horse_list') + '?needs=1')
        response = self.page(self.b, 'needs=1')
        # Only the two horses that need something, not Charlie between them.
        self.assertEqual(response.context['list_place'], (1, 2))
        self.assertEqual(response.context['next_horse'], self.d)

    def test_the_arrow_key_script_is_loaded(self):
        self.assertContains(self.page(self.c), 'js/horse_step.js')


class HistoryTests(Base):
    """E2: who changed the record, and what."""

    def test_panel_lists_horse_and_number_changes(self):
        horse = self.horse('Avicii')
        horse.markings = 'Star'
        horse.save()
        ident = HorseIdentifier.objects.create(horse=horse, kind='stud_book', value='GB1')
        ident.delete()
        response = self.page(horse)
        self.assertGreaterEqual(len(response.context['history_entries']), 3)
        body = response.content.decode()
        panel = body.split('data-testid="history-panel"', 1)[1].split('</details>', 1)[0]
        self.assertIn('<h2 class="section-title">History</h2>', panel)
        self.assertIn('Deleted horse number or link', panel)
        self.assertIn('Markings', panel)


class FocusTests(Base):
    """C5: one toggle for the generations; focus has a place after a swap."""

    def test_one_generations_toggle_and_focus_targets(self):
        from pedigree.models import PedigreeHorse

        def rec(name, sex, sire=None, dam=None, horse=None):
            return PedigreeHorse.objects.create(name=name, sex=sex, sire=sire, dam=dam, horse=horse)

        horse = self.horse('Avicii')
        sire = rec('Galileo', 'M', sire=rec('Sadlers Wells', 'M', sire=rec('Northern Dancer', 'M')),
                   dam=rec('Urban Sea', 'F'))
        dam = rec('Fair Lady', 'F', sire=rec('Highland Star', 'M'))
        root = PedigreeHorse.objects.get(horse=horse)  # made with the horse
        root.sire, root.dam = sire, dam
        root.save()
        body = self.page(horse).content.decode()
        self.assertNotIn('Show 2 generations', body)
        self.assertEqual(body.count('data-testid="pedigree-generations"'), 1)
        toggle = body.split('data-testid="pedigree-generations"', 1)[0].rsplit('<button', 1)[1]
        self.assertIn('aria-pressed="false"', toggle)
        # The horse's own tree takes the focus after "Back to …".
        self.assertIn('tabindex="-1" data-focus-after-swap', body)
        # An ancestor's tree puts it on "Back to …".
        partial = self.client.get(reverse('pedigree_tree', args=[sire.pk]) + f'?horse={horse.pk}',
                                  HTTP_HX_REQUEST='true').content.decode()
        self.assertIn('data-focus-after-swap', partial.split('Back to', 1)[0].rsplit('<button', 1)[1])
        self.assertNotIn('tabindex="-1"', partial)


class TooltipTests(Base):
    """C8: the date of each Timeline entry, and help text, on the page."""

    def test_timeline_shows_the_date(self):
        horse = self.horse('Avicii')
        visit = self.today - timedelta(days=40)
        VetVisit.objects.create(horse=horse, date=visit, reason='Check')
        body = self.page(horse).content.decode()
        self.assertIn(f'<time datetime="{visit:%Y-%m-%d}">{visit.day} {visit:%b %Y}</time>', body)

    def test_id_checked_date_for_screen_readers(self):
        horse = self.horse('Avicii', microchip=CHIP)
        horse.mark_identity_checked(self.admin)
        horse.save()
        stamp = timezone.localdate()
        self.assertContains(self.page(horse), f'ID checked<span class="sr-only"> on {stamp.day} {stamp:%b %Y}</span>')

    def test_other_charge_says_what_it_is_for(self):
        horse = self.horse('Avicii')
        response = self.client.get(reverse('charge_create') + f'?horse={horse.pk}', **POPUP)
        self.assertContains(response, 'Bill something that has no health record')


class TouchTargetTests(Base):
    """C9: 44px targets on a touch screen, at any width."""

    def test_coarse_pointer_rule(self):
        css = Path(settings.BASE_DIR, 'static', 'css', 'input.css').read_text()
        block = css.split('@media (pointer: coarse) {\n        .btn-xs, .btn-sm { min-height: 44px; }', 1)
        self.assertEqual(len(block), 2)
