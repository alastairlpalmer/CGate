"""Phase 1d (plan §6.5, §7.6, §7.7): reading a spreadsheet, the back-fill
text rules, matching rows to horses, proposed changes, the review screen
and Apply."""

from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from core.models import AuditEvent, Horse, Location, Owner, RateType
from core.roles_testutils import make_admin, make_viewer
from pedigree import backfill, imports
from pedigree.matching import find_horse
from pedigree.models import ImportBatch, ImportRow, PedigreeHorse, ProposedChange
from pedigree.sheet import SheetError, parse_foaled, read

HEAD = ('Yard name,Registered name,Country,Foaled,Sex,Colour,Breed,Sire,Dam,Dam\'s sire,'
        'Microchip,UELN,Passport number,Passport issuer,Markings,Height\n')


def csv_bytes(*lines, head=HEAD):
    return (head + '\n'.join(lines) + '\n').encode('utf-8')


def rec(name, sex='', country='', year=None, **kw):
    r = PedigreeHorse(name=name, sex=sex, country=country, year_of_birth=year, **kw)
    r.save()
    return r


def own(horse):
    return PedigreeHorse.objects.get(horse=horse)


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------

class FoaledTests(TestCase):

    def test_forms(self):
        today = date(2026, 11, 20)
        self.assertEqual(parse_foaled('12/04/2023', today), (date(2023, 4, 12), 'exact'))
        self.assertEqual(parse_foaled('2023-04-12', today), (date(2023, 4, 12), 'exact'))
        self.assertEqual(parse_foaled('12 Apr 2023', today), (date(2023, 4, 12), 'exact'))
        self.assertEqual(parse_foaled('2011', today), (date(2011, 1, 1), 'year'))
        self.assertEqual(parse_foaled('c. 2011', today), (date(2011, 1, 1), 'estimated'))
        self.assertEqual(parse_foaled('2011?', today), (date(2011, 1, 1), 'estimated'))
        for bad in ('2027', '1/1/2030', 'spring', '1890'):
            with self.assertRaises(ValueError):
                parse_foaled(bad, today)


class ReadTests(TestCase):

    def test_template_columns_and_tidy_values(self):
        rows, unknown = read(csv_bytes(
            'Rosie 23,Copper Rose (GB),,12/04/2023,M,ch,Thoroughbred,Copper Ridge (GB),Starlit Rose,'
            'Unknown,FDX-B 826 098 000 777 111,826-004-000000001,P1,Weatherbys,Star,16.2'))
        self.assertEqual(unknown, [])
        values = rows[0].values
        self.assertEqual(values['registered_name'], 'Copper Rose')
        self.assertEqual(values['country'], 'GB')
        self.assertEqual(values['foaled'], (date(2023, 4, 12), 'exact'))
        self.assertEqual(values['sex'], 'mare')
        self.assertEqual(values['colour'], 'chestnut')
        self.assertEqual(values['microchip'], '826098000777111')
        self.assertEqual(values['ueln'], '826004000000001')
        self.assertEqual(values['height'], 66)
        self.assertEqual(values['DS'], 'Unknown')
        self.assertEqual(rows[0].number, 2)

    def test_racecard_letters_for_sex(self):
        rows, _ = read(csv_bytes(*[f'H{i},,,,{letter},,,,,,,,,,,' for i, letter in enumerate('mfcgh')]))
        self.assertEqual([r.values['sex'] for r in rows], ['mare', 'filly', 'colt', 'gelding', 'stallion'])

    def test_other_headings_semicolons_and_excel_encoding(self):
        raw = 'Name;DOB;Chip;Damsire;Notes\nBeech;2015;372141000123456;Moonlit Bay;x\n'.encode('cp1252')
        rows, unknown = read(raw)
        self.assertEqual(rows[0].values['name'], 'Beech')
        self.assertEqual(rows[0].values['microchip'], '372141000123456')
        self.assertEqual(rows[0].values['DS'], 'Moonlit Bay')
        self.assertEqual(unknown, ['Notes'])

    def test_a_bad_value_is_a_problem_not_a_failure(self):
        rows, _ = read(csv_bytes('Beech,,,spring,dragon,,,,,,,,,,,99.9', ',,,,,,,,,,,,,,,'))
        self.assertEqual(len(rows), 1)  # the empty line is left out
        self.assertEqual(rows[0].values, {'name': 'Beech'})
        self.assertEqual(len(rows[0].problems), 3)

    def test_no_name(self):
        rows, _ = read(csv_bytes(',,GB,2011,,,,,,,,,,,,'))
        self.assertIn('No yard name or registered name', rows[0].problems[-1])

    def test_whole_file_refusals(self):
        with self.assertRaises(SheetError):
            read(b'')
        with self.assertRaises(SheetError):
            read(b'Colour,Breed\nbay,Cob\n')
        with self.assertRaises(SheetError):
            read(b'x' * (2 * 1024 * 1024 + 1))


class BackfillTextTests(TestCase):

    def test_breeding_text(self):
        cases = {
            'By Galileo out of Starlit Rose': ('Galileo', 'Starlit Rose'),
            'Galileo (IRE) x Starlit Rose (GB)': ('Galileo (IRE)', 'Starlit Rose (GB)'),
            'Galileo ex Starlit Rose': ('Galileo', 'Starlit Rose'),
            'Galileo / Starlit Rose': ('Galileo', 'Starlit Rose'),
            'by Sea The Stars x Moon River.\nBought at Goffs': ('Sea The Stars', 'Moon River'),
        }
        for text, (sire, dam) in cases.items():
            found = backfill.parse_breeding(text)
            self.assertEqual((found.sire, found.dam), (sire, dam), text)
        for text in ('', None, 'Bought at the sales from a breeder in Kildare by the owner',
                     'Lovely mare, good to shoe'):
            self.assertIsNone(backfill.parse_breeding(text), text)

    def test_registered_name_in_brackets(self):
        self.assertEqual(backfill.registered_from_name('Avicii (Karma Avicii)'), ('Avicii', 'Karma Avicii'))
        self.assertIsNone(backfill.registered_from_name('Avicii (GB)'))
        self.assertIsNone(backfill.registered_from_name('Avicii'))

    def test_markings_after_a_dash(self):
        self.assertEqual(backfill.markings_from_name('Beech - white sock'), ('Beech', 'white sock'))
        self.assertIsNone(backfill.markings_from_name('Beech - the grey one'))


# ---------------------------------------------------------------------------
# Matching and proposals
# ---------------------------------------------------------------------------

class MatchTests(TestCase):

    def test_order(self):
        rosie = Horse.objects.create(name='Rosie', microchip='826098000777111', ueln='826004000000001',
                                     registered_name='Copper Rose', country_of_birth='GB',
                                     date_of_birth=date(2023, 4, 12))
        self.assertEqual(find_horse(microchip='826 098 000 777 111').how, 'microchip')
        self.assertEqual(find_horse(ueln='826004000000001').horse, rosie)
        found = find_horse(registered_name='COPPER ROSE', country='gb', year=2023)
        self.assertEqual((found.horse, found.how), (rosie, 'registered'))
        found = find_horse(registered_name='Copper Rose', country='GB')  # no year: only possible
        self.assertIsNone(found.horse)
        self.assertEqual(found.possible, [rosie])
        found = find_horse(yard_name='rosie')
        self.assertIsNone(found.horse)
        self.assertEqual(found.possible, [rosie])
        self.assertIn('Possible match: Rosie', found.note)

    def test_a_chip_on_two_horses_is_never_chosen(self):
        Horse.objects.create(name='A', microchip='826098000777111')
        Horse.objects.create(name='B', microchip='826098000777111')
        found = find_horse(microchip='826098000777111')
        self.assertIsNone(found.horse)
        self.assertEqual(len(found.possible), 2)


class ProposalTests(TestCase):

    def setUp(self):
        self.user = make_admin()
        self.rosie = Horse.objects.create(name='Rosie', microchip='826098000777111', passport_number='OLD')

    def batch(self, *lines):
        rows, _ = read(csv_bytes(*lines))
        return imports.batch_from_sheet(rows, user=self.user, file_name='horses.csv')

    def test_only_empty_values_are_ticked(self):
        batch = self.batch('Rosie,Copper Rose,GB,2023,,,,,,,826098000777111,,NEW,,,')
        row = batch.rows.get()
        self.assertEqual(row.horse, self.rosie)
        changes = {c.target: c for c in row.changes.all()}
        self.assertNotIn('microchip', changes)  # the same value
        self.assertNotIn('name', changes)       # the same value
        self.assertTrue(changes['registered_name'].accept)
        self.assertTrue(changes['foaled'].accept)
        self.assertEqual(changes['foaled'].proposed_display, '2023')
        self.assertFalse(changes['passport_number'].accept)
        self.assertEqual(changes['passport_number'].current_display, 'OLD')

    def test_pedigree_names(self):
        rec('Shadow', 'M', 'GB', 2001)
        rec('Shadow', 'M', 'IRE', 2005)
        galileo = rec('Galileo', 'M', 'IRE', 1998)
        batch = self.batch(',,,,,,,,,,826098000777111,,,,,', )
        self.assertFalse(batch.rows.get().changes.exists())
        batch = self.batch('Rosie,,,,,,,Shadow,Starlit Rose,Galileo (IRE) 1998,826098000777111,,,,,')
        changes = {c.target: c for c in batch.rows.get().changes.all()}
        self.assertEqual(changes['pedigree.S'].choice, '')
        self.assertEqual(len(changes['pedigree.S'].choice_ids), 2)
        self.assertFalse(changes['pedigree.S'].accept)
        self.assertEqual(changes['pedigree.D'].choice, 'new')
        self.assertTrue(changes['pedigree.D'].accept)
        self.assertEqual(changes['pedigree.DS'].choice, str(galileo.pk))

    def test_new_and_possible_rows(self):
        batch = self.batch('Beech,,,,,,,,,,,,,,,', 'ROSIE,,,,,,,,,,,,,,,', ',,GB,,,,,,,,,,,,,')
        beech, rosie_like, empty = batch.rows.order_by('number')
        self.assertIsNone(beech.horse)
        self.assertTrue(beech.create_new)
        self.assertIsNone(rosie_like.horse)
        self.assertFalse(rosie_like.create_new)
        self.assertEqual(rosie_like.possible_ids, [self.rosie.pk])
        self.assertTrue(empty.result.startswith('Left out'))


class ApplyTests(TestCase):

    def setUp(self):
        self.user = make_admin()
        self.rosie = Horse.objects.create(name='Rosie', microchip='826098000777111')

    def batch(self, *lines, **placement):
        rows, _ = read(csv_bytes(*lines))
        return imports.batch_from_sheet(rows, user=self.user, file_name='horses.csv', placement=placement)

    def test_apply_writes_ticked_changes_with_a_reason(self):
        batch = self.batch('Rosie,Copper Rose (GB),,c. 2023,F,Bay,,Copper Ridge (GB) 2014,Starlit Rose,'
                           'Moonlit Bay (FR),826098000777111,,,,,15.3')
        counts = imports.apply_batch(batch, self.user)
        self.assertEqual(counts, {'rows': 1, 'new': 0, 'failed': 0})
        self.rosie.refresh_from_db()
        self.assertEqual(self.rosie.registered_name, 'Copper Rose')
        self.assertEqual(self.rosie.country_of_birth, 'GB')
        self.assertEqual(self.rosie.dob_precision, Horse.DobPrecision.ESTIMATED)
        self.assertEqual(self.rosie.height_inches, 63)
        self.assertEqual(self.rosie.color, 'bay')
        record = own(self.rosie)
        self.assertEqual(record.sire.label, 'Copper Ridge (GB) 2014')
        self.assertEqual(record.sire.source, PedigreeHorse.Source.SPREADSHEET)
        self.assertEqual(record.dam.sire.short_label, 'Moonlit Bay (FR)')
        self.assertTrue(self.rosie.history.filter(history_change_reason__startswith='Spreadsheet import').exists())
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.APPLIED)
        self.assertEqual(AuditEvent.objects.get(action='records_imported').actor, self.user)
        with self.assertRaises(imports.ApplyRefused):
            imports.apply_batch(batch, self.user)

    def test_a_value_changed_since_is_skipped(self):
        batch = self.batch('Rosie,,,,,,Cob,,,,826098000777111,,P9,,,')
        Horse.objects.filter(pk=self.rosie.pk).update(passport_number='TYPED')
        imports.apply_batch(batch, self.user)
        self.rosie.refresh_from_db()
        self.assertEqual(self.rosie.passport_number, 'TYPED')
        self.assertEqual(self.rosie.breed, 'Cob')
        change = ProposedChange.objects.get(target='passport_number')
        self.assertIn('changed after the file was read', change.result)

    def test_unticked_changes_are_not_written(self):
        batch = self.batch('Rosie,,,,,,Cob,,,,826098000777111,,,,,')
        ProposedChange.objects.filter(row__batch=batch).update(accept=False)
        imports.apply_batch(batch, self.user)
        self.rosie.refresh_from_db()
        self.assertEqual(self.rosie.breed, '')

    def test_new_horses_with_and_without_a_placement(self):
        location = Location.objects.create(name='Top Field', site='Main')
        owner = Owner.objects.create(name='Jo Bloggs')
        rate = RateType.objects.create(name='Full', daily_rate=10)
        batch = self.batch('Beech,,IRE,2018,,,,Galileo,,,,,,,,', location=location, owner=owner, rate_type=rate,
                           arrival_date=date(2026, 11, 1))
        counts = imports.apply_batch(batch, self.user)
        self.assertEqual(counts['new'], 1)
        beech = Horse.objects.get(name='Beech')
        self.assertEqual(beech.country_of_birth, 'IRE')
        self.assertEqual(beech.current_placement.location, location)
        self.assertEqual(own(beech).sire.name, 'Galileo')
        batch = self.batch('Oak,,,,,,,,,,,,,,,')
        imports.apply_batch(batch, self.user)
        self.assertIsNone(Horse.objects.get(name='Oak').current_placement)

    def test_a_possible_match_is_not_added_unless_ticked(self):
        batch = self.batch('rosie,,,,,,Cob,,,,,,,,,')
        imports.apply_batch(batch, self.user)
        self.assertEqual(Horse.objects.filter(name__iexact='rosie').count(), 1)
        self.assertIn('Not added', batch.rows.get().result)

    def test_a_pedigree_name_to_choose_is_skipped(self):
        rec('Shadow', 'M', 'GB', 2001)
        rec('Shadow', 'M', 'IRE', 2005)
        batch = self.batch('Rosie,,,,,,,Shadow,,,826098000777111,,,,,')
        ProposedChange.objects.filter(row__batch=batch).update(accept=True)
        imports.apply_batch(batch, self.user)
        self.assertIsNone(own(self.rosie).sire)
        self.assertIn('choose the record first', ProposedChange.objects.get().result)

    def test_a_mares_name_as_sire_waits_for_a_person(self):
        rec('Starlit Rose', 'F', 'IRE', 2015)
        batch = self.batch('Rosie,,,,,,,Starlit Rose (IRE) 2015,,,826098000777111,,,,,')
        change = ProposedChange.objects.get(row__batch=batch)
        self.assertFalse(change.accept)
        self.assertEqual(change.choice, '')
        self.assertIn('is on file as female, so it cannot be a sire', change.note)
        ProposedChange.objects.filter(pk=change.pk).update(accept=True)
        imports.apply_batch(batch, self.user)
        self.assertIsNone(own(self.rosie).sire)
        self.assertIn('choose the record first', ProposedChange.objects.get().result)

    def test_dams_sire_needs_a_dam(self):
        batch = self.batch('Rosie,,,,,,,,,Moonlit Bay,826098000777111,,,,,')
        imports.apply_batch(batch, self.user)
        self.assertIn('fill in the dam first', ProposedChange.objects.get().result)

    def test_a_bad_pedigree_does_not_stop_the_identity_or_other_rows(self):
        loop = rec('Loop Horse', 'M', 'GB', 2010)
        loop.sire = own(self.rosie)  # Rosie is Loop Horse's sire, so Loop Horse cannot be hers
        loop.save()
        batch = self.batch('Rosie,,,,,,Cob,Loop Horse (GB) 2010,,,826098000777111,,,,,',
                           'Beech,,,,,,,,,,,,,,,')
        counts = imports.apply_batch(batch, self.user)
        self.rosie.refresh_from_db()
        self.assertEqual(self.rosie.breed, 'Cob')
        self.assertIsNone(own(self.rosie).sire)
        self.assertIn('Not saved: This would make the horse its own ancestor.',
                      ProposedChange.objects.get(target='pedigree.S').result)
        self.assertTrue(Horse.objects.filter(name='Beech').exists())
        self.assertEqual(counts['new'], 1)


class BackfillBatchTests(TestCase):

    def test_proposals_from_text_and_names(self):
        user = make_admin()
        a = Horse.objects.create(name='Avicii (Karma Avicii)', breeding='By Galileo out of Starlit Rose')
        Horse.objects.create(name='Beech - white sock')
        Horse.objects.create(name='Plain', breeding='Nice horse, good to box')
        batch = imports.backfill_batch(user)
        self.assertEqual(batch.source, ImportBatch.Source.BACKFILL)
        rows = {row.horse.name: row for row in batch.rows.all()}
        self.assertEqual(set(rows), {'Avicii (Karma Avicii)', 'Beech - white sock'})
        changes = {c.target: c for c in rows['Avicii (Karma Avicii)'].changes.all()}
        self.assertTrue(changes['registered_name'].accept)
        self.assertFalse(changes['name'].accept)  # the yard name is never pre-ticked
        self.assertEqual(changes['pedigree.S'].choice, 'new')
        imports.apply_batch(batch, user)
        a.refresh_from_db()
        self.assertEqual(a.registered_name, 'Karma Avicii')
        self.assertEqual(a.name, 'Avicii (Karma Avicii)')
        self.assertEqual(own(a).sire.source, PedigreeHorse.Source.BACKFILL)
        self.assertTrue(a.history.filter(history_change_reason__startswith='Back-fill').exists())

    def test_nothing_found(self):
        Horse.objects.create(name='Plain')
        self.assertIsNone(imports.backfill_batch(None))
        self.assertFalse(ImportBatch.objects.exists())


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

class ScreenTests(TestCase):

    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)
        self.rosie = Horse.objects.create(name='Rosie', microchip='826098000777111')
        self.url = reverse('records_imports')

    def upload(self, raw, name='horses.csv', follow=False, **extra):
        return self.client.post(self.url, {'file': SimpleUploadedFile(name, raw, 'text/csv'), **extra},
                                follow=follow)

    def test_page_template_and_tab(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Import a spreadsheet')
        self.assertContains(response, 'Import a file')
        response = self.client.get(reverse('records_import_template'))
        self.assertEqual(response.content.decode('utf-8-sig').strip(), HEAD.strip().replace("Dam's", "Dam's"))

    def test_upload_review_save_and_apply(self):
        response = self.upload(csv_bytes('Rosie,Copper Rose,GB,,,,,,,,826098000777111,,,,,',
                                         'Beech,,,,,,,,,,,,,,,'))
        batch = ImportBatch.objects.get()
        self.assertRedirects(response, reverse('records_import_review', args=[batch.pk]))
        review = reverse('records_import_review', args=[batch.pk])
        response = self.client.get(review)
        self.assertContains(response, 'Matches Rosie by microchip')
        self.assertContains(response, 'New horse')
        self.assertEqual(len(response.context['rows']), 2)

        rosie_row, beech_row = batch.rows.order_by('number')
        reg = rosie_row.changes.get(target='registered_name')
        country = rosie_row.changes.get(target='country')
        data = {'action': 'save', f'accept_{reg.pk}': 'on', f'create_{beech_row.pk}': 'on'}
        response = self.client.post(review, data, follow=True)
        self.assertContains(response, 'Your choices are saved.')
        country.refresh_from_db()
        self.assertFalse(country.accept)

        response = self.client.post(review, {**data, 'action': 'apply'}, follow=True)
        self.assertContains(response, 'Applied. 2 horses changed, 1 new.')
        self.rosie.refresh_from_db()
        self.assertEqual(self.rosie.registered_name, 'Copper Rose')
        self.assertEqual(self.rosie.country_of_birth, '')
        self.assertTrue(Horse.objects.filter(name='Beech').exists())
        self.assertNotContains(response, 'Apply ticked changes')

    def test_choosing_a_possible_match(self):
        self.upload(csv_bytes('ROSIE,,,,,,Cob,,,,,,,,,'))
        batch = ImportBatch.objects.get()
        row = batch.rows.get()
        review = reverse('records_import_review', args=[batch.pk])
        self.assertContains(self.client.get(review), 'This row is')
        response = self.client.post(review, {'action': 'apply', f'match_{row.pk}': str(self.rosie.pk)},
                                    follow=True)
        self.assertContains(response, 'The match changed for row 2.')
        row.refresh_from_db()
        self.assertEqual(row.horse, self.rosie)
        breed = row.changes.get(target='breed')
        self.assertTrue(breed.accept)
        self.client.post(review, {'action': 'apply', f'match_{row.pk}': str(self.rosie.pk),
                                  f'accept_{breed.pk}': 'on'})
        self.rosie.refresh_from_db()
        self.assertEqual(self.rosie.breed, 'Cob')
        self.assertEqual(Horse.objects.count(), 1)

    def test_a_pedigree_choice_is_needed_before_apply(self):
        a = rec('Shadow', 'M', 'GB', 2001)
        rec('Shadow', 'M', 'IRE', 2005)
        self.upload(csv_bytes('Rosie,,,,,,,Shadow,,,826098000777111,,,,,'))
        batch = ImportBatch.objects.get()
        change = ProposedChange.objects.get()
        review = reverse('records_import_review', args=[batch.pk])
        response = self.client.post(review, {'action': 'apply', f'accept_{change.pk}': 'on'}, follow=True)
        self.assertContains(response, 'Choose the record for 1 pedigree name first')
        self.client.post(review, {'action': 'apply', f'accept_{change.pk}': 'on', f'choice_{change.pk}': str(a.pk)})
        self.assertEqual(own(self.rosie).sire, a)

    def test_choosing_new_for_a_name_on_file_as_the_other_sex(self):
        rec('Starlit Rose', 'F', 'IRE', 2015)
        self.upload(csv_bytes('Rosie,,,,,,,Starlit Rose (IRE) 2015,,,826098000777111,,,,,'))
        batch = ImportBatch.objects.get()
        change = ProposedChange.objects.get()
        review = reverse('records_import_review', args=[batch.pk])
        self.assertContains(self.client.get(review), 'is on file as female')
        self.client.post(review, {'action': 'apply', f'accept_{change.pk}': 'on', f'choice_{change.pk}': 'new'})
        sire = own(self.rosie).sire
        self.assertEqual((sire.label, sire.sex), ('Starlit Rose (IRE) 2015', 'M'))

    def test_discard(self):
        self.upload(csv_bytes('Rosie,,,,,,Cob,,,,826098000777111,,,,,'))
        batch = ImportBatch.objects.get()
        response = self.client.post(reverse('records_import_review', args=[batch.pk]), {'action': 'discard'},
                                    follow=True)
        self.assertContains(response, 'Discarded. Nothing was changed.')
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.DISCARDED)
        self.rosie.refresh_from_db()
        self.assertEqual(self.rosie.breed, '')

    def test_upload_errors(self):
        response = self.upload(b'Colour\nbay\n')
        self.assertContains(response, 'The first line must have the column headings')
        response = self.upload(b'Yard name\nRosie\n', name='horses.xlsx')
        self.assertContains(response, 'Use a CSV file.')
        response = self.upload(b'Yard name\nRosie\n', location=Location.objects.create(name='X', site='Y').pk)
        self.assertContains(response, 'choose a location, an owner and a rate, or none of them')
        self.assertFalse(ImportBatch.objects.exists())

    def test_unknown_columns_are_named(self):
        response = self.upload(b'Yard name,Notes\nRosie,hello\n', follow=True)
        self.assertContains(response, 'These columns were not read: Notes.')

    def test_backfill_button(self):
        Horse.objects.create(name='Avicii (Karma Avicii)')
        response = self.client.post(reverse('records_backfill'))
        batch = ImportBatch.objects.get()
        self.assertRedirects(response, reverse('records_import_review', args=[batch.pk]))
        response = self.client.post(reverse('records_backfill'))  # the open one again
        self.assertRedirects(response, reverse('records_import_review', args=[batch.pk]))
        self.assertEqual(ImportBatch.objects.count(), 1)
        self.assertContains(self.client.get(self.url), 'Continue the review')

    def test_backfill_with_nothing_to_find(self):
        response = self.client.post(reverse('records_backfill'), follow=True)
        self.assertContains(response, 'Nothing new was found')

    def test_view_only_role_cannot_import(self):
        self.client.force_login(make_viewer())
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
        self.assertNotEqual(self.client.post(reverse('records_backfill')).status_code, 302)
        response = self.client.get(reverse('records_horses'))
        self.assertNotContains(response, 'Import a file')
        self.assertFalse(ImportRow.objects.exists())
