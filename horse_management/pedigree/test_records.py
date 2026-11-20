"""Fill in records, phase 1c (plan §7.5 and §4.4 cases 25 to 29): the
completeness count, the Horses tab with Save and next, versions, undo,
the Pedigree records tab, possible duplicates, merge and undo merge,
and Download as CSV."""

import csv
import io
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_viewer
from pedigree import records as rules
from pedigree.models import PedigreeHorse, PedigreeMerge
from pedigree.records import MergeRefused, duplicate_groups, merge, merge_problem, undo_merge


def rec(name, sex='', country='', year=None, **kw):
    r = PedigreeHorse(name=name, sex=sex, country=country, year_of_birth=year, **kw)
    r.save()
    return r


def own(horse):
    return PedigreeHorse.objects.get(horse=horse)


class Yard:
    """A location, an owner and a rate, for horses that are on the yard."""

    def __init__(self):
        self.location = Location.objects.create(name='Top Field', site='Main')
        self.other_location = Location.objects.create(name='Barn', site='Main')
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)

    def horse(self, name, location=None, **kw):
        horse = Horse.objects.create(name=name, **kw)
        Placement.objects.create(horse=horse, owner=self.owner, location=location or self.location,
                                 rate_type=self.rate, start_date=date(2025, 1, 1))
        return horse


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

class CompletenessTests(TestCase):

    def test_nothing_filled(self):
        horse = Horse.objects.create(name='Blank')
        count, missing = rules.completeness(horse, own(horse))
        self.assertEqual(count, 0)
        self.assertEqual(len(missing), 9)

    def test_all_filled_with_unknown_parents(self):
        horse = Horse.objects.create(
            name='Full', registered_name='Full House', country_of_birth='GB',
            date_of_birth=date(2015, 4, 1), microchip='826098100000001', ueln='826004000000001',
            passport_number='P1')
        record = own(horse)
        record.sire_unknown = record.dam_unknown = True
        record.save()
        self.assertEqual(rules.completeness(horse, record), (9, []))

    def test_dams_sire_needs_the_dams_own_record(self):
        horse = Horse.objects.create(name='Rosie')
        record = own(horse)
        record.dam = rec('Starlit Rose', 'F')
        record.save()
        state = rules.filled(horse, record)
        self.assertTrue(state['dam'])
        self.assertFalse(state['dam_sire'])
        record.dam.sire_unknown = True
        record.dam.save()
        self.assertTrue(rules.filled(horse, PedigreeHorse.objects.get(pk=record.pk))['dam_sire'])

    def test_missing_filters(self):
        horse = Horse.objects.create(name='Guess', date_of_birth=date(2011, 1, 1),
                                     dob_precision=Horse.DobPrecision.ESTIMATED)
        record = own(horse)
        self.assertTrue(rules.matches_missing(horse, record, 'chip'))
        self.assertTrue(rules.matches_missing(horse, record, 'pedigree'))
        self.assertFalse(rules.matches_missing(horse, record, 'foaled'))
        self.assertTrue(rules.matches_missing(horse, record, 'estimated'))


class NextAfterTests(TestCase):

    def test_next_even_when_the_saved_horse_left_the_list(self):
        a, b, c = (Horse.objects.create(name=n) for n in ('Avicii', 'Beech', 'Copper'))
        rows = [{'horse': a}, {'horse': c}]
        self.assertEqual(rules.next_after(rows, b), c)
        self.assertEqual(rules.next_after(rows, a), c)
        self.assertIsNone(rules.next_after(rows, c))


class DuplicateTests(TestCase):

    def test_same_key_and_one_letter_apart(self):
        a = rec('Moonlit Bay', 'M', 'FR')
        b = rec('Moonlight Bay', 'M')
        c = rec('Moonlit Bay', 'M')
        rec('Galileo', 'M')
        groups = duplicate_groups(PedigreeHorse.objects.all())
        self.assertEqual(len(groups), 1)
        self.assertEqual({r.pk for r in groups[0]}, {a.pk, b.pk, c.pk})

    def test_one_letter_apart_for_middle_length_names(self):
        rec('Galileo', 'M')
        rec('Gallileo', 'M')
        rec('Gallilleo', 'M')  # one apart from Gallileo, so in the same group
        groups = duplicate_groups(PedigreeHorse.objects.all())
        self.assertEqual([[r.name for r in g] for g in groups], [['Galileo', 'Gallileo', 'Gallilleo']])

    def test_short_names_need_the_same_key(self):
        rec('Rosa', 'F')
        rec('Rose', 'F')
        self.assertEqual(duplicate_groups(PedigreeHorse.objects.all()), [])

    def test_a_male_and_a_female_are_not_a_pair(self):
        rec('Shadow Play', 'M')
        rec('Shadow Play', 'F')
        self.assertEqual(duplicate_groups(PedigreeHorse.objects.all()), [])

    def test_two_horses_on_the_system_are_not_a_pair(self):
        Horse.objects.create(name='Rosie')
        Horse.objects.create(name='Rosie')
        self.assertEqual(duplicate_groups(PedigreeHorse.objects.all()), [])


class MergeTests(TestCase):

    def setUp(self):
        self.user = make_admin()
        self.right = rec('Moonlit Bay', 'M', 'FR', 2001)
        self.typo = rec('Moonlight Bay', 'M', '', None, colour='Bay')
        self.typo.sire = rec('Old Sire', 'M')
        self.typo.save()
        self.foal_horse = Horse.objects.create(name='Foal')
        foal = own(self.foal_horse)
        foal.sire = self.typo
        foal.save()
        self.other = rec('Other Foal', 'F')
        self.other.sire = self.typo
        self.other.save()

    def test_merge_moves_links_and_fills_blanks(self):
        row = merge(self.right, self.typo, user=self.user)
        self.assertFalse(PedigreeHorse.objects.filter(pk=self.typo.pk).exists())
        self.assertEqual(own(self.foal_horse).sire, self.right)
        self.assertEqual(PedigreeHorse.objects.get(pk=self.other.pk).sire, self.right)
        self.right.refresh_from_db()
        self.assertEqual(self.right.colour, 'Bay')
        self.assertEqual(self.right.sire.name, 'Old Sire')
        self.foal_horse.refresh_from_db()
        self.assertEqual(self.foal_horse.sire_name, 'Moonlit Bay (FR)')
        self.assertEqual(row.removed_label, 'Moonlight Bay')
        self.assertEqual(row.merged_by, self.user)

    def test_undo_merge_puts_everything_back(self):
        row = merge(self.right, self.typo)
        restored = undo_merge(row)
        self.assertEqual(restored.pk, self.typo.pk)
        self.assertEqual(restored.name, 'Moonlight Bay')
        self.assertEqual(own(self.foal_horse).sire_id, self.typo.pk)
        self.assertEqual(PedigreeHorse.objects.get(pk=self.other.pk).sire_id, self.typo.pk)
        self.right.refresh_from_db()
        self.assertEqual(self.right.colour, '')
        self.assertIsNone(self.right.sire)
        self.foal_horse.refresh_from_db()
        self.assertEqual(self.foal_horse.sire_name, 'Moonlight Bay')
        with self.assertRaises(MergeRefused):
            undo_merge(PedigreeMerge.objects.get(pk=row.pk))

    def test_undo_merge_keeps_a_later_change(self):
        row = merge(self.right, self.typo)
        self.right.refresh_from_db()
        self.right.colour = 'Grey'
        self.right.save()
        undo_merge(row)
        self.right.refresh_from_db()
        self.assertEqual(self.right.colour, 'Grey')

    def test_undo_merge_when_the_removed_records_sire_is_gone(self):
        row = merge(self.right, self.typo)
        old_sire = PedigreeHorse.objects.get(name='Old Sire')
        self.right.refresh_from_db()
        self.right.sire = None
        self.right.save()
        old_sire.delete()
        restored = undo_merge(row)
        self.assertIsNone(restored.sire_id)

    def test_merge_moves_the_horse_link(self):
        horse = Horse.objects.create(name='Rosie', sex='mare')
        stray = rec('Rosie', 'F', 'IRE', 2015)
        merge(stray, own(horse))
        stray.refresh_from_db()
        self.assertEqual(stray.horse, horse)
        row = PedigreeMerge.objects.get()
        undo_merge(row)
        self.assertEqual(own(horse).name, 'Rosie')
        self.assertIsNone(PedigreeHorse.objects.get(pk=stray.pk).horse)

    def test_refusals(self):
        a = own(Horse.objects.create(name='A'))
        b = own(Horse.objects.create(name='B'))
        self.assertIn('two horse records', merge_problem(a, b))
        mare = rec('Moonlit Bay', 'F')
        self.assertIn('male and the other is female', merge_problem(self.right, mare))
        self.assertIn('ancestor', merge_problem(self.typo, self.typo.sire))
        self.assertIn('two different', merge_problem(self.right, self.right))
        with self.assertRaises(MergeRefused):
            merge(a, b)


# ---------------------------------------------------------------------------
# Horses tab
# ---------------------------------------------------------------------------

class HorsesTabTests(TestCase):

    def setUp(self):
        self.yard = Yard()
        self.admin = make_admin()
        self.client.force_login(self.admin)
        self.avicii = self.yard.horse('Avicii', registered_name='Karma Avicii', country_of_birth='GB')
        self.beech = self.yard.horse('Beech')
        self.copper = self.yard.horse('Copper Rose', location=self.yard.other_location)
        self.url = reverse('records_horses')

    def form_data(self, horse, **data):
        response = self.client.get(self.url, {'horse': horse.pk})
        base = {'version': response.context['version']}
        for name, field in response.context['id_form'].fields.items():
            value = response.context['id_form'].initial.get(name, field.initial)
            base[name] = '' if value is None else value
        for code in ('S', 'D', 'DS', 'SS', 'SD', 'DD'):
            base[code] = response.context['form'].fields[code].initial or ''
        base.pop('foaled_year_estimated', None)
        base.update(data)
        return base

    def test_list_with_counts(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        names = [row['horse'].name for row in response.context['rows']]
        self.assertEqual(names, ['Avicii', 'Beech', 'Copper Rose'])
        self.assertContains(response, '2 of 9')
        self.assertContains(response, 'Missing: ')

    def test_filters(self):
        Horse.objects.create(name='Departed Dan', is_active=False)
        get = lambda **q: [r['horse'].name for r in self.client.get(self.url, q).context['rows']]  # noqa: E731
        self.assertEqual(get(location=self.yard.other_location.pk), ['Copper Rose'])
        self.assertEqual(get(q='karma'), ['Avicii'])
        self.assertIn('Departed Dan', get(status='all'))
        self.assertNotIn('Departed Dan', get())
        self.assertEqual(get(location='abc'), ['Avicii', 'Beech', 'Copper Rose'])

    def test_full_horses_leave_the_gaps_list(self):
        Horse.objects.filter(pk=self.beech.pk).update(
            registered_name='Beech Tree', country_of_birth='IRE', date_of_birth=date(2018, 5, 1),
            microchip='372141000123456', ueln='372004000000001', passport_number='P2')
        record = own(self.beech)
        record.sire_unknown = record.dam_unknown = True
        record.save()
        names = [r['horse'].name for r in self.client.get(self.url).context['rows']]
        self.assertNotIn('Beech', names)
        names = [r['horse'].name for r in self.client.get(self.url, {'show': 'all'}).context['rows']]
        self.assertIn('Beech', names)

    def test_pane_shows_both_forms(self):
        response = self.client.get(self.url, {'horse': self.copper.pk})
        self.assertContains(response, 'Save and next')
        self.assertContains(response, 'name="version"')
        self.assertContains(response, 'id="pedigree-males"')
        self.assertContains(response, 'country-suggestions')
        self.assertEqual(response.context['position'], 3)

    def test_save_identity_and_pedigree(self):
        data = self.form_data(self.beech, registered_name='Beech Tree (IRE)', microchip='372 141 000 123 456',
                              S='Galileo (IRE) 1998', D='Starlit Rose', DS='Unknown')
        response = self.client.post(f'{self.url}?horse={self.beech.pk}', data)
        self.assertRedirects(response, f'{self.url}?horse={self.beech.pk}', fetch_redirect_response=False)
        self.beech.refresh_from_db()
        self.assertEqual(self.beech.registered_name, 'Beech Tree')
        self.assertEqual(self.beech.country_of_birth, 'IRE')
        self.assertEqual(self.beech.microchip, '372141000123456')
        record = own(self.beech)
        self.assertEqual(record.sire.label, 'Galileo (IRE) 1998')
        self.assertTrue(record.dam.sire_unknown)
        self.assertEqual(self.beech.sire_name, 'Galileo (IRE)')
        self.assertEqual(self.beech.history.first().history_change_reason, 'Records editor')

    def test_save_and_next_keeps_the_filters(self):
        data = self.form_data(self.avicii, microchip='826098100000001', save_next='1')
        url = f'{self.url}?show=all&horse={self.avicii.pk}'
        response = self.client.post(url, data)
        self.assertRedirects(response, f'{self.url}?show=all&horse={self.beech.pk}', fetch_redirect_response=False)

    def test_save_and_next_on_the_last_horse(self):
        data = self.form_data(self.copper, save_next='1')
        response = self.client.post(f'{self.url}?horse={self.copper.pk}', data, follow=True)
        self.assertContains(response, 'That was the last horse in this list.')

    def test_a_name_with_two_records_must_be_chosen(self):
        rec('Shadow', 'M', 'GB', 2001)
        rec('Shadow', 'M', 'IRE', 2005)
        data = self.form_data(self.beech, S='Shadow')
        response = self.client.post(f'{self.url}?horse={self.beech.pk}', data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Choose it below, or choose New.')
        self.assertIsNone(own(self.beech).sire)

    def test_a_changed_record_stops_the_save(self):
        data = self.form_data(self.beech, microchip='372141000999999')
        other = Horse.objects.get(pk=self.beech.pk)
        other.passport_number = 'CHANGED'
        other.save()
        response = self.client.post(f'{self.url}?horse={self.beech.pk}', data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'was changed while you had it open')
        self.beech.refresh_from_db()
        self.assertEqual(self.beech.microchip, '')
        self.assertEqual(self.beech.passport_number, 'CHANGED')
        self.assertEqual(response.context['id_form'].initial['passport_number'], 'CHANGED')

    def test_undo_puts_the_horse_back(self):
        Horse.objects.filter(pk=self.beech.pk).update(passport_number='OLD')
        dam = rec('Old Dam', 'F')
        record = own(self.beech)
        record.dam = dam
        record.save()
        data = self.form_data(self.beech, passport_number='NEW', microchip='372141000123456',
                              S='Brand New Sire', D='Unknown')
        response = self.client.post(f'{self.url}?horse={self.beech.pk}', data, follow=True)
        self.assertContains(response, 'Saved Beech.')
        self.assertContains(response, reverse('records_undo'))
        self.assertTrue(PedigreeHorse.objects.filter(name='Brand New Sire').exists())

        response = self.client.post(reverse('records_undo'), follow=True)
        self.assertContains(response, 'Undone. Beech is back as it was.')
        self.beech.refresh_from_db()
        self.assertEqual(self.beech.passport_number, 'OLD')
        self.assertEqual(self.beech.microchip, '')
        record = own(self.beech)
        self.assertEqual(record.dam, dam)
        self.assertIsNone(record.sire)
        self.assertFalse(record.dam_unknown)
        self.assertEqual(self.beech.dam_name, 'Old Dam')
        self.assertFalse(PedigreeHorse.objects.filter(name='Brand New Sire').exists())
        response = self.client.post(reverse('records_undo'), follow=True)
        self.assertContains(response, 'There is nothing to undo.')

    def test_undo_restores_a_grandparent_on_a_newly_chosen_parent(self):
        shared_dam = rec('Fair Lady', 'F', 'GB', 2010)
        other = Horse.objects.create(name='Other')
        other_record = own(other)
        other_record.dam = shared_dam
        other_record.save()
        data = self.form_data(self.beech, D=shared_dam.label, DS='New Grandsire', SS='')
        self.client.post(f'{self.url}?horse={self.beech.pk}', data)
        shared_dam.refresh_from_db()
        self.assertEqual(shared_dam.sire.name, 'New Grandsire')
        self.client.post(reverse('records_undo'))
        shared_dam.refresh_from_db()
        self.assertIsNone(shared_dam.sire)
        self.assertIsNone(own(self.beech).dam)
        self.assertFalse(PedigreeHorse.objects.filter(name='New Grandsire').exists())

    def test_undo_deletes_a_new_parent_and_its_new_parent(self):
        data = self.form_data(self.beech, S='Brand New Sire', SS='Brand New Grandsire')
        self.client.post(f'{self.url}?horse={self.beech.pk}', data)
        self.assertEqual(own(self.beech).sire.sire.name, 'Brand New Grandsire')
        self.client.post(reverse('records_undo'))
        self.assertFalse(PedigreeHorse.objects.filter(name__startswith='Brand New').exists())

    def test_undo_refused_after_another_change(self):
        data = self.form_data(self.beech, passport_number='NEW')
        self.client.post(f'{self.url}?horse={self.beech.pk}', data)
        horse = Horse.objects.get(pk=self.beech.pk)
        horse.breed = 'Cob'
        horse.save()
        response = self.client.post(reverse('records_undo'), follow=True)
        self.assertContains(response, 'was changed again after that save')
        self.beech.refresh_from_db()
        self.assertEqual(self.beech.passport_number, 'NEW')

    def test_undo_too_late(self):
        data = self.form_data(self.beech, passport_number='NEW')
        self.client.post(f'{self.url}?horse={self.beech.pk}', data)
        session = self.client.session
        snap = session[rules.UNDO_SESSION_KEY]
        snap['taken_at'] = (timezone.now() - timedelta(minutes=rules.UNDO_MINUTES + 1)).isoformat()
        session[rules.UNDO_SESSION_KEY] = snap
        session.save()
        response = self.client.post(reverse('records_undo'), follow=True)
        self.assertContains(response, 'too late to undo')

    def test_undo_keeps_the_identity_check_stamp(self):
        Horse.objects.filter(pk=self.beech.pk).update(
            microchip='372141000123456', identity_checked_at=timezone.now(), identity_checked_by=self.admin)
        data = self.form_data(self.beech, microchip='372141000999999')
        self.client.post(f'{self.url}?horse={self.beech.pk}', data)
        self.beech.refresh_from_db()
        self.assertIsNone(self.beech.identity_checked_at)
        self.client.post(reverse('records_undo'))
        self.beech.refresh_from_db()
        self.assertEqual(self.beech.microchip, '372141000123456')
        self.assertIsNotNone(self.beech.identity_checked_at)
        self.assertEqual(self.beech.identity_checked_by, self.admin)

    def test_save_warns_about_a_duplicate_chip(self):
        Horse.objects.filter(pk=self.avicii.pk).update(microchip='826098100000001')
        data = self.form_data(self.beech, microchip='826098100000001')
        response = self.client.post(f'{self.url}?horse={self.beech.pk}', data, follow=True)
        self.assertContains(response, 'is also on Avicii')

    def test_view_only_role_sees_the_report_only(self):
        self.client.force_login(make_viewer())
        response = self.client.get(self.url, {'horse': self.beech.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['horse'])
        self.assertContains(response, 'cannot change horse records')
        self.assertNotContains(response, 'Save and next')
        response = self.client.post(f'{self.url}?horse={self.beech.pk}', {'microchip': '1'})
        self.beech.refresh_from_db()
        self.assertEqual(self.beech.microchip, '')
        self.assertEqual(self.client.post(reverse('records_undo')).status_code, 403)

    def test_horse_list_has_the_button(self):
        response = self.client.get(reverse('horse_list'), {'location': self.yard.location.pk})
        self.assertContains(response, f'{self.url}?location={self.yard.location.pk}')


class ExportTests(TestCase):

    def test_download_as_csv(self):
        yard = Yard()
        self.client.force_login(make_viewer())
        horse = yard.horse('Avicii', registered_name='Karma Avicii', country_of_birth='GB',
                           date_of_birth=date(2011, 1, 1), dob_precision=Horse.DobPrecision.ESTIMATED,
                           microchip='826098100000001', height_inches=66)
        record = own(horse)
        record.sire = rec('Galileo', 'M', 'IRE')
        record.dam = rec('Starlit Rose', 'F')
        record.dam.sire_unknown = True
        record.dam.save()
        record.save()
        yard.horse('=cmd()')
        response = self.client.get(reverse('records_export'), {'show': 'all'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('horse-records.csv', response['Content-Disposition'])
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(rows[0][:4], ['Yard name', 'Registered name', 'Country', 'Foaled'])
        avicii = next(r for r in rows if r[0] == 'Avicii')
        self.assertEqual(avicii[3], 'c. 2011')
        self.assertEqual(avicii[7:10], ['Galileo (IRE)', 'Starlit Rose', 'Unknown'])
        self.assertEqual(avicii[10], '826098100000001')
        self.assertEqual(avicii[15], '16.2')
        self.assertTrue(any(r[0].startswith("'=") for r in rows))


# ---------------------------------------------------------------------------
# Pedigree records tab and merge screens
# ---------------------------------------------------------------------------

class RecordsTabTests(TestCase):

    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)
        self.url = reverse('records_pedigree')
        self.sire = rec('Moonlit Bay', 'M', 'FR', 2001)
        self.typo = rec('Moonlight Bay', 'M')
        self.horse = Horse.objects.create(name='Copper Rose')
        record = own(self.horse)
        record.sire = self.typo
        record.save()

    def test_list_and_search(self):
        response = self.client.get(self.url, {'q': 'moonl'})
        labels = [r.label for r in response.context['rows']]
        self.assertEqual(labels, ['Moonlight Bay', 'Moonlit Bay (FR) 2001'])

    def test_duplicates_filter(self):
        response = self.client.get(self.url, {'dupes': '1'})
        self.assertEqual(len(response.context['rows']), 1)
        self.assertContains(response, 'Compare and merge')
        self.assertContains(response, f'?a={self.typo.pk}&amp;b={self.sire.pk}')

    def test_record_pane_and_edit(self):
        response = self.client.get(self.url, {'record': self.typo.pk})
        self.assertContains(response, 'in 1 pedigree as a parent')
        response = self.client.post(f'{self.url}?record={self.typo.pk}', {
            'name': 'Moonlit Bay', 'country': 'fr', 'year_of_birth': '2001', 'sex': 'M', 'colour': '',
            'S': 'Sea The Stars (IRE)', 'D': '',
        })
        self.assertEqual(response.status_code, 302)
        self.typo.refresh_from_db()
        self.assertEqual(self.typo.label, 'Moonlit Bay (FR) 2001')
        self.assertEqual(self.typo.sire.short_label, 'Sea The Stars (IRE)')
        self.horse.refresh_from_db()
        self.assertEqual(self.horse.sire_name, 'Moonlit Bay (FR)')

    def test_a_shared_record_asks_again(self):
        other = rec('Other Foal', 'F')
        other.sire = self.typo
        other.save()
        data = {'name': 'Moonlight Bay', 'country': 'GB', 'year_of_birth': '', 'sex': 'M', 'colour': '',
                'S': '', 'D': ''}
        response = self.client.post(f'{self.url}?record={self.typo.pk}', data)
        self.assertContains(response, 'This record is in 2 pedigrees.')
        self.typo.refresh_from_db()
        self.assertEqual(self.typo.country, '')
        response = self.client.post(f'{self.url}?record={self.typo.pk}', {**data, 'confirm_shared': 'on'})
        self.assertEqual(response.status_code, 302)
        self.typo.refresh_from_db()
        self.assertEqual(self.typo.country, 'GB')

    def test_sex_cannot_change_under_progeny(self):
        response = self.client.post(f'{self.url}?record={self.typo.pk}', {
            'name': 'Moonlight Bay', 'country': '', 'year_of_birth': '', 'sex': 'F', 'colour': '',
            'S': '', 'D': ''})
        self.assertContains(response, 'cannot be female')

    def test_own_parent_refused(self):
        response = self.client.post(f'{self.url}?record={self.sire.pk}', {
            'name': 'Moonlit Bay', 'country': 'FR', 'year_of_birth': '2001', 'sex': 'M', 'colour': '',
            'S': self.sire.label, 'D': ''})
        self.assertContains(response, 'cannot be its own parent')

    def test_yard_record_has_no_form(self):
        response = self.client.get(self.url, {'record': own(self.horse).pk})
        self.assertIsNone(response.context['form'])
        self.assertContains(response, 'Fill in this horse')

    def test_checked_stamp(self):
        self.client.post(f'{self.url}?record={self.sire.pk}', {
            'name': 'Moonlit Bay', 'country': 'FR', 'year_of_birth': '2001', 'sex': 'M', 'colour': '',
            'checked': 'on', 'S': '', 'D': ''})
        self.sire.refresh_from_db()
        self.assertTrue(self.sire.checked)
        self.assertEqual(self.sire.checked_by, self.admin)

    def test_merge_screen_then_merge_then_undo(self):
        merge_url = reverse('records_merge')
        response = self.client.get(merge_url, {'a': self.sire.pk, 'b': self.typo.label})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Are these the same horse?')
        self.assertEqual(len(response.context['sides']), 2)
        response = self.client.post(f'{merge_url}?a={self.sire.pk}&b={self.typo.pk}',
                                    {'keep': self.sire.pk}, follow=True)
        self.assertContains(response, 'Merged Moonlight Bay into Moonlit Bay (FR) 2001.')
        self.assertEqual(own(self.horse).sire, self.sire)
        row = PedigreeMerge.objects.get()
        self.assertContains(response, reverse('records_merge_undo', args=[row.pk]))
        response = self.client.post(reverse('records_merge_undo', args=[row.pk]), follow=True)
        self.assertContains(response, 'Merge undone.')
        self.assertEqual(own(self.horse).sire_id, self.typo.pk)

    def test_merge_needs_a_choice_and_refuses_two_yard_horses(self):
        merge_url = reverse('records_merge')
        response = self.client.post(f'{merge_url}?a={self.sire.pk}&b={self.typo.pk}', {}, follow=True)
        self.assertContains(response, 'Choose the record to keep.')
        other = Horse.objects.create(name='Other')
        response = self.client.get(merge_url, {'a': own(self.horse).pk, 'b': own(other).pk})
        self.assertContains(response, 'two horse records')
        self.assertNotContains(response, 'Keep this one')
        response = self.client.get(merge_url, {'a': self.sire.pk, 'b': 'No Such Horse'}, follow=True)
        self.assertContains(response, 'Choose two records to compare.')

    def test_merge_keeps_the_yard_record(self):
        horse = Horse.objects.create(name='Moonlit Bay', sex='stallion')
        merge_url = reverse('records_merge')
        self.client.post(f'{merge_url}?a={self.sire.pk}&b={own(horse).pk}', {'keep': self.sire.pk})
        self.assertFalse(PedigreeHorse.objects.filter(pk=self.sire.pk).exists())
        self.assertEqual(own(horse).country, 'FR')

    def test_view_only_role(self):
        self.client.force_login(make_viewer())
        response = self.client.get(self.url, {'record': self.typo.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['form'])
        self.assertNotContains(response, 'Merge with another record')
        response = self.client.get(reverse('records_merge'), {'a': self.sire.pk, 'b': self.typo.pk})
        self.assertEqual(response.status_code, 302)
