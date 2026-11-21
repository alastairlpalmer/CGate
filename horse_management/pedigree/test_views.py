"""Pedigree screens, phase 1b: the pop-up, the diagram views, the horse
page card and byline, foaling from the breeding screens, and the
back-fill migration."""

import importlib
from datetime import date, timedelta

from django.apps import apps
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse
from core.roles_testutils import make_admin, make_viewer
from health.models import BreedingRecord
from health.services import record_foaling
from pedigree import services
from pedigree.models import PedigreeHorse
from pedigree.services import Resolution

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}
HTMX = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'pedigree-diagram'}


def rec(name, sex='', country='', year=None, **kw):
    r = PedigreeHorse(name=name, sex=sex, country=country, year_of_birth=year, **kw)
    r.save()
    return r


class EditFormTests(TestCase):

    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)
        self.horse = Horse.objects.create(name='Copper Rose', sex='filly')
        self.url = reverse('pedigree_edit', args=[self.horse.pk])

    def post(self, **data):
        base = {code: self.initial(code) for code in ('S', 'D', 'DS', 'SS', 'SD', 'DD')}
        base.update(data)
        return self.client.post(self.url, base, **POPUP)

    def initial(self, code):
        form = self.client.get(self.url, **POPUP).context['form']
        return form.fields[code].initial or ''

    def test_popup_and_full_page(self):
        response = self.client.get(self.url, **POPUP)
        self.assertTemplateUsed(response, 'pedigree/partials/edit_form.html')
        self.assertContains(response, 'hx-select="unset"')
        self.assertContains(response, 'id="pedigree-males"')
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, 'pedigree/edit_page.html')

    def test_new_names_make_records_and_link(self):
        response = self.post(S='Copper Ridge (GB) 2014', D='Starlit Rose (IRE)', DS='Moonlit Bay (FR)')
        self.assertEqual(response.status_code, 204)
        record = PedigreeHorse.objects.get(horse=self.horse)
        self.assertEqual(record.sire.label, 'Copper Ridge (GB) 2014')
        self.assertEqual(record.dam.short_label, 'Starlit Rose (IRE)')
        self.assertEqual(record.dam.sire.short_label, 'Moonlit Bay (FR)')
        self.horse.refresh_from_db()
        self.assertEqual(self.horse.sire_name, 'Copper Ridge (GB)')

    def test_a_mares_name_in_the_sire_box_asks_first(self):
        mare = rec('Starlit Rose', 'F', 'IRE', 2015)
        response = self.post(S=mare.label)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Starlit Rose (IRE) 2015 is on file as female, so it cannot be a sire.')
        self.assertContains(response, 'name="S_choice" value="new"')
        self.assertIsNone(PedigreeHorse.objects.get(horse=self.horse).sire)
        self.assertEqual(PedigreeHorse.objects.filter(name_key='starlitrose').count(), 1)
        # A different horse with the same name: New makes a male record.
        self.assertEqual(self.post(S=mare.label, S_choice='new').status_code, 204)
        sire = PedigreeHorse.objects.get(horse=self.horse).sire
        self.assertEqual((sire.label, sire.sex), ('Starlit Rose (IRE) 2015', 'M'))

    def test_a_name_new_to_the_system_is_not_checked(self):
        # The app cannot know the sex of a horse it has never seen.
        self.assertEqual(self.post(S='Starlit Rose (IRE) 2015').status_code, 204)
        self.assertEqual(PedigreeHorse.objects.get(horse=self.horse).sire.sex, 'M')

    def test_a_stallions_name_in_the_dam_box_asks_first(self):
        rec('Galileo', 'M', 'IRE', 1998)
        response = self.post(D='Galileo (IRE) 1998')
        self.assertContains(response, 'is on file as male, so it cannot be a dam.')

    def test_a_picked_suggestion_links_to_the_shared_record(self):
        ridge = rec('Copper Ridge', 'M', 'GB', 2014)
        self.assertEqual(self.post(S=ridge.label).status_code, 204)
        self.assertEqual(PedigreeHorse.objects.get(horse=self.horse).sire, ridge)
        self.assertEqual(PedigreeHorse.objects.filter(name_key='copperridge').count(), 1)

    def test_a_name_on_file_asks_which_one(self):
        ridge = rec('Copper Ridge', 'M', 'GB', 2014)
        response = self.post(S='Copper Ridge')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'matches a record already on file')
        self.assertContains(response, f'value="{ridge.pk}"')
        self.assertContains(response, 'New record')
        self.assertIsNone(PedigreeHorse.objects.get(horse=self.horse).sire)
        response = self.post(S='Copper Ridge', S_choice=str(ridge.pk))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(PedigreeHorse.objects.get(horse=self.horse).sire, ridge)

    def test_new_on_purpose(self):
        rec('Rosie', 'F')
        self.post(D='Rosie', D_choice='new')
        self.assertEqual(PedigreeHorse.objects.filter(name_key='rosie').count(), 2)

    def test_unchanged_boxes_are_not_written(self):
        self.post(S='Copper Ridge (GB)', D='Starlit Rose (IRE)')
        record = PedigreeHorse.objects.get(horse=self.horse)
        dam = record.dam
        self.assertEqual(self.post().status_code, 204)
        record.refresh_from_db()
        self.assertEqual(record.dam, dam)

    def test_a_grandparent_needs_its_parent(self):
        response = self.post(DS='Moonlit Bay (FR)')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fill in the dam first.')

    def test_a_checked_parent_needs_a_tick_to_change(self):
        self.post(D='Starlit Rose (IRE)')
        dam = PedigreeHorse.objects.get(horse=self.horse).dam
        dam.checked = True
        dam.save()
        response = self.post(DS='Moonlit Bay (FR)')
        self.assertContains(response, 'is marked as checked')
        self.assertContains(response, 'Yes, change the checked record')
        response = self.post(DS='Moonlit Bay (FR)', confirm_checked='on')
        self.assertEqual(response.status_code, 204)

    def test_the_shared_note_counts_other_horses(self):
        self.post(D='Starlit Rose (IRE)')
        dam = PedigreeHorse.objects.get(horse=self.horse).dam
        sister = Horse.objects.create(name='Sister', sex='filly')
        services.set_parents(sister, {'D': Resolution('x', record=dam)})
        response = self.client.get(self.url, **POPUP)
        self.assertContains(response, 'Changing it changes it for 1 other horse too.')

    def test_unknown(self):
        self.post(S='Unknown')
        self.assertTrue(PedigreeHorse.objects.get(horse=self.horse).sire_unknown)

    def test_a_loop_shows_an_error(self):
        mare = Horse.objects.create(name='Mum', sex='mare')
        services.set_parents(mare, {'D': Resolution('x', record=PedigreeHorse.objects.get(horse=self.horse))})
        # A plain name always asks which record; choose the mare's.
        response = self.post(D=mare.pedigree.label, D_choice=str(mare.pedigree.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'its own ancestor')

    def test_view_only_role_cannot_edit(self):
        self.client.force_login(make_viewer())
        self.assertEqual(self.client.post(self.url, {'S': 'X'}, **POPUP).status_code, 403)


class TreeViewTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())
        self.horse = Horse.objects.create(name='Copper Rose', sex='filly')
        self.ridge = rec('Copper Ridge', 'M', 'GB', 2014, sire=rec('Highland Star', 'M'))
        services.set_parents(self.horse, {'S': Resolution('x', record=self.ridge)})

    def test_an_ancestor_tree_with_a_way_back(self):
        url = reverse('pedigree_tree', args=[self.ridge.pk])
        back = self.horse.pedigree.pk
        response = self.client.get(f'{url}?horse={self.horse.pk}&back={back}', **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '← Back to Copper Rose')
        self.assertContains(response, 'Highland Star')
        self.assertContains(response, 'Pedigree record')
        self.assertNotContains(response, '+ Add')

    def test_three_generations(self):
        url = reverse('pedigree_tree', args=[self.horse.pedigree.pk])
        response = self.client.get(f'{url}?horse={self.horse.pk}&generations=3', **HTMX)
        self.assertContains(response, 'ped-g3')

    def test_bad_parameters_are_safe(self):
        url = reverse('pedigree_tree', args=[self.horse.pedigree.pk])
        response = self.client.get(f'{url}?horse=abc&generations=x&back=y', **HTMX)
        self.assertEqual(response.status_code, 200)
        orphan = rec('Nobody', 'M')
        response = self.client.get(reverse('pedigree_tree', args=[orphan.pk]), **HTMX)
        self.assertEqual(response.status_code, 404)

    def test_a_direct_visit_goes_to_the_horse(self):
        url = reverse('pedigree_tree', args=[self.horse.pedigree.pk])
        self.assertRedirects(self.client.get(f'{url}?horse={self.horse.pk}'),
                             reverse('horse_detail', args=[self.horse.pk]))


class HorsePageTests(TestCase):

    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)
        hs = rec('Highland Star', 'M', 'IRE', 2005)
        self.mare = Horse.objects.create(name='Starlit Rose', sex='mare', country_of_birth='IRE')
        fair = rec('Fair Lady', 'F', 'GB', 2010, sire=hs)
        services.set_parents(self.mare, {'S': services.resolve('Moonlit Bay (FR)', 'M'),
                                         'D': Resolution('x', record=fair)})
        ridge = rec('Copper Ridge', 'M', 'GB', 2014, sire=hs)
        self.horse = Horse.objects.create(name='Rosie 23', registered_name='Copper Rose', sex='filly')
        services.set_parents(self.horse, {'S': Resolution('x', record=ridge),
                                          'D': Resolution('x', record=self.mare.pedigree)})
        self.url = reverse('horse_detail', args=[self.horse.pk])

    def test_the_tree_in_the_banner(self):
        response = self.client.get(self.url)
        page = response.content.decode()
        banner = page.split('data-testid="horse-hero"', 1)[1].split('id="identity"', 1)[0]
        self.assertIn('id="pedigree"', banner)
        # The tree is in the banner, so the "by … out of …" line under the
        # name is gone: the parents show once (21 Nov 2026 layout).
        self.assertNotIn('data-testid="pedigree-byline"', page)
        self.assertIn('Copper Ridge', banner)
        self.assertIn('Starlit Rose', banner)
        self.assertContains(response, 'class="ped-gap ped-gap0"')
        self.assertContains(response, 'Show 3 generations')
        self.assertContains(response, 'Family here')
        self.assertContains(response, 'Starlit Rose')

    def test_inbreeding_shows_at_three_generations(self):
        url = reverse('pedigree_tree', args=[self.horse.pedigree.pk])
        response = self.client.get(f'{url}?horse={self.horse.pk}&generations=3', **HTMX)
        self.assertContains(response, 'data-testid="inbreeding"')
        self.assertContains(response, 'Highland Star (IRE) 2×3')

    def test_missing_boxes_offer_add_to_staff_only(self):
        bare = Horse.objects.create(name='Bare')
        response = self.client.get(reverse('horse_detail', args=[bare.pk]))
        self.assertContains(response, '+ Add')
        self.assertContains(response, 'Not entered')
        self.client.force_login(make_viewer())
        response = self.client.get(reverse('horse_detail', args=[bare.pk]))
        self.assertNotContains(response, '+ Add')

    def test_the_page_does_not_query_per_box(self):
        self.client.get(self.url)  # warm caches
        with self.assertNumQueries(self._count()):
            self.client.get(self.url)

    def _count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(self.url)
        return len(ctx.captured_queries)


class BreedingFormTests(TestCase):

    def test_covering_suggests_stallions_on_record(self):
        rec('Copper Ridge', 'M', 'GB', 2014)
        self.client.force_login(make_admin())
        mare = Horse.objects.create(name='Starlit Rose', sex='mare')
        response = self.client.get(reverse('breeding_create') + f'?mare={mare.pk}')
        self.assertContains(response, 'id="stallion-suggestions"')
        self.assertContains(response, '<option value="Copper Ridge (GB) 2014">')

    def test_recording_a_foaling_gives_the_foal_its_tree(self):
        mare = Horse.objects.create(name='Starlit Rose', sex='mare')
        services.set_parents(mare, {'S': services.resolve('Moonlit Bay (FR)', 'M')})
        ridge = rec('Copper Ridge', 'M', 'GB', 2014)
        covered = timezone.localdate() - timedelta(days=345)
        br = BreedingRecord.objects.create(mare=mare, stallion_name='Copper Ridge (GB) 2014',
                                           date_covered=covered, status='confirmed')
        foal = record_foaling(br, foal_dob=timezone.localdate(), foal_sex='colt', foal_name='Rose colt')
        record = PedigreeHorse.objects.get(horse=foal)
        self.assertEqual(record.sire, ridge)
        self.assertEqual(record.dam, mare.pedigree)
        self.assertEqual(record.source, PedigreeHorse.Source.FOALING)
        self.assertEqual(record.dam.sire.name, 'Moonlit Bay')


class BackfillMigrationTests(TestCase):

    def setUp(self):
        self.migration = importlib.import_module('pedigree.migrations.0002_backfill')

    def test_old_names_become_shared_records(self):
        mare = Horse.objects.create(name='Starlit Rose', sex='mare')
        a = Horse.objects.create(name='A', sex='filly')
        b = Horse.objects.create(name='B', sex='colt')
        c = Horse.objects.create(name='C', sex='colt')
        pony = Horse.objects.create(name='Pony', sex='mare', date_of_birth=date(2015, 1, 1))
        # Old data: text names, as before this phase, and no records yet.
        Horse.objects.filter(pk__in=[a.pk, b.pk]).update(sire_name='Kodiak Star', dam_id=mare.pk)
        Horse.objects.filter(pk=c.pk).update(sire_name='Kodiak Star (IRE)', dam_name='Rosie')
        Horse.objects.filter(pk=pony.pk).update(sire_name='Unknown')
        PedigreeHorse.objects.all().delete()

        self.migration.backfill(apps, None)

        self.assertEqual(PedigreeHorse.objects.filter(horse__isnull=False).count(), 5)
        ra, rb, rc = (PedigreeHorse.objects.get(horse=h) for h in (a, b, c))
        self.assertEqual(ra.sire_id, rb.sire_id)                  # one record for one name
        self.assertNotEqual(ra.sire_id, rc.sire_id)                # a different spelling stays apart
        self.assertEqual(rc.sire.country, 'IRE')
        self.assertEqual(ra.dam, PedigreeHorse.objects.get(horse=mare))
        self.assertEqual(rc.dam.name, 'Rosie')
        self.assertEqual(rc.dam.sex, 'F')
        self.assertEqual(ra.sire.source, 'backfill')
        rpony = PedigreeHorse.objects.get(horse=pony)
        self.assertTrue(rpony.sire_unknown)
        self.assertEqual(rpony.year_of_birth, 2015)

    def test_running_twice_adds_no_own_records(self):
        Horse.objects.create(name='Once')
        self.migration.backfill(apps, None)
        self.assertEqual(PedigreeHorse.objects.filter(horse__isnull=False).count(), 1)
