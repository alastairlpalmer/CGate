"""Shared pedigree records, phase 1b (docs/HORSE_IDENTITY_AND_PEDIGREE_PLAN.md).

Names, the model rules, matching (§6.4), writing parents, foaling, family
on this yard, and the tree's grid and lines (§8.3).
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from core.models import Horse
from pedigree import names, services
from pedigree.models import PedigreeHorse
from pedigree.services import Resolution, resolve
from pedigree.tree import KNOWN, MISSING, UNKNOWN, build, curve, index_of, middle, relation


def rec(name, sex='', country='', year=None, sire=None, dam=None, **kw):
    r = PedigreeHorse(name=name, sex=sex, country=country, year_of_birth=year, sire=sire, dam=dam, **kw)
    r.save()
    return r


class NameTests(SimpleTestCase):

    def test_parse_name_suffix_and_year(self):
        self.assertEqual(names.parse('Copper Ridge (GB) 2004'), names.ParsedName('Copper Ridge', 'GB', 2004))
        self.assertEqual(names.parse('Moonlit Bay (fr)'), names.ParsedName('Moonlit Bay', 'FR'))
        self.assertEqual(names.parse('Mill Reef 1968'), names.ParsedName('Mill Reef', '', 1968))
        self.assertEqual(names.parse('Number 9'), names.ParsedName('Number 9'))
        self.assertIsNone(names.parse('   '))
        self.assertEqual(names.parse('(GB)').name, '(GB)')

    def test_key_ignores_case_spaces_and_punctuation(self):
        self.assertEqual(names.name_key('Sea The Stars'), names.name_key('SEA-the-stars'))
        self.assertEqual(names.name_key("O'Reilly"), 'oreilly')

    def test_unknown_words(self):
        for text in ('Unknown', ' unknown ', 'Not known', '?'):
            self.assertTrue(names.is_unknown(text), text)
        self.assertFalse(names.is_unknown('Unknown Soldier'))

    def test_label(self):
        self.assertEqual(names.label('Rosie', 'IRE', 2015), 'Rosie (IRE) 2015')
        self.assertEqual(names.label('Rosie'), 'Rosie')


class ModelRuleTests(TestCase):

    def test_name_key_and_labels_on_save(self):
        r = rec('  Copper Ridge ', 'M', 'gb', 2014)
        self.assertEqual((r.name, r.country, r.name_key), ('Copper Ridge', 'GB', 'copperridge'))
        self.assertEqual(r.label, 'Copper Ridge (GB) 2014')
        self.assertEqual(r.short_label, 'Copper Ridge (GB)')

    def test_a_sire_cannot_be_a_mare_and_a_dam_cannot_be_male(self):
        mare, stallion = rec('Rosie', 'F'), rec('Copper Ridge', 'M')
        foal = rec('Foal', sire=mare)
        with self.assertRaises(ValidationError):
            foal.full_clean()
        foal = rec('Foal 2', dam=stallion)
        with self.assertRaises(ValidationError):
            foal.full_clean()

    def test_no_horse_is_its_own_ancestor(self):
        grand = rec('Grand', 'M')
        parent = rec('Parent', 'M', sire=grand)
        grand.sire = parent
        with self.assertRaises(ValidationError):
            grand.full_clean()
        me = rec('Me', 'M')
        me.sire = me
        with self.assertRaises(ValidationError):
            me.full_clean()


class HorseRecordTests(TestCase):

    def test_every_horse_gets_its_own_record_kept_in_step(self):
        horse = Horse.objects.create(name='Avicii', sex='gelding', color='bay')
        r = horse.pedigree
        self.assertEqual((r.name, r.sex, r.colour), ('Avicii', 'M', 'Bay'))
        horse.registered_name = 'Karma Avicii'
        horse.country_of_birth = 'GB'
        horse.date_of_birth = date(2019, 4, 12)
        horse.save()
        r.refresh_from_db()
        self.assertEqual(r.label, 'Karma Avicii (GB) 2019')
        self.assertEqual(r.name_key, 'karmaavicii')

    def test_setting_parents_keeps_a_newer_name(self):
        # The record copy held since the horse was made has the old name;
        # writing parents must not put it back (found on the screenshots).
        horse = Horse.objects.create(name='Rosie 23', sex='filly')
        horse.registered_name = 'Copper Rose'
        horse.country_of_birth = 'GB'
        horse.save()
        services.set_parents(horse, {'S': resolve('Copper Ridge (GB) 2014', 'M')})
        self.assertEqual(PedigreeHorse.objects.get(horse=horse).short_label, 'Copper Rose (GB)')
        mare = Horse.objects.create(name='Starlit Rose', sex='mare')
        mare.country_of_birth = 'IRE'
        mare.save()
        foal = Horse.objects.create(name='Foal', sex='colt')
        services.link_foal(foal, mare, 'Copper Ridge (GB) 2014')
        self.assertEqual(PedigreeHorse.objects.get(horse=mare).short_label, 'Starlit Rose (IRE)')
        self.assertEqual(PedigreeHorse.objects.get(horse=foal).dam.short_label, 'Starlit Rose (IRE)')

    def test_a_deleted_horse_keeps_its_record(self):
        horse = Horse.objects.create(name='Gone', sex='mare')
        pk = horse.pedigree.pk
        horse.delete()
        self.assertIsNone(PedigreeHorse.objects.get(pk=pk).horse)


class MatchingTests(TestCase):
    """A typed name joins a record only when a person chose it (§6.4)."""

    def setUp(self):
        self.ridge = rec('Copper Ridge', 'M', 'GB', 2014)

    def test_empty_and_unknown(self):
        self.assertTrue(resolve('', 'M').empty)
        self.assertTrue(resolve('Unknown', 'M').unknown)

    def test_a_picked_suggestion_links(self):
        self.assertEqual(resolve('Copper Ridge (GB) 2014', 'M').record, self.ridge)

    def test_the_name_alone_asks(self):
        res = resolve('Copper Ridge', 'M')
        self.assertTrue(res.needs_choice)
        self.assertEqual(res.choices, [self.ridge])

    def test_a_choice_settles_it(self):
        self.assertEqual(resolve('Copper Ridge', 'M', chosen=str(self.ridge.pk)).record, self.ridge)
        self.assertIsNotNone(resolve('Copper Ridge', 'M', chosen='new').new)

    def test_a_choice_for_another_name_is_ignored(self):
        other = rec('Other', 'M')
        res = resolve('Copper Ridge', 'M', chosen=str(other.pk))
        self.assertTrue(res.needs_choice)

    def test_no_match_makes_a_new_record(self):
        res = resolve('Highland Star (IRE) 2005', 'M')
        self.assertEqual(res.new, names.ParsedName('Highland Star', 'IRE', 2005))

    def test_the_other_sex_is_not_offered(self):
        rec('Rosie', 'F')
        self.assertIsNotNone(resolve('Rosie', 'M').new)
        self.assertTrue(resolve('Rosie', 'F').needs_choice)

    def test_two_polo_ponies_called_rosie_stay_apart(self):
        a, b = rec('Rosie', 'F'), rec('Rosie', 'F', year=2012)
        res = resolve('Rosie', 'F')
        self.assertEqual(set(res.choices), {a, b})
        self.assertEqual(resolve('Rosie 2012', 'F').record, b)

    def test_the_current_value_stays(self):
        self.assertEqual(resolve('Copper Ridge (GB)', 'M', current=self.ridge).record, self.ridge)


class SetParentsTests(TestCase):

    def setUp(self):
        self.foal = Horse.objects.create(name='Copper Rose', sex='filly')
        self.mare = Horse.objects.create(name='Starlit Rose', sex='mare', country_of_birth='IRE')

    def test_parents_and_the_horse_copies(self):
        mare_rec = self.mare.pedigree
        services.set_parents(self.foal, {
            'S': resolve('Copper Ridge (GB) 2014', 'M'),
            'D': Resolution('Starlit Rose', record=mare_rec),
        })
        self.foal.refresh_from_db()
        r = self.foal.pedigree
        self.assertEqual(r.sire.label, 'Copper Ridge (GB) 2014')
        self.assertEqual(r.sire.sex, 'M')
        self.assertEqual(r.dam, mare_rec)
        self.assertEqual(self.foal.sire_name, 'Copper Ridge (GB)')
        self.assertEqual(self.foal.dam_name, 'Starlit Rose (IRE)')
        self.assertEqual(self.foal.dam, self.mare)

    def test_a_grandparent_is_written_on_the_parent_record(self):
        mare_rec = self.mare.pedigree
        services.set_parents(self.foal, {'D': Resolution('x', record=mare_rec)})
        services.set_parents(self.foal, {'DS': resolve('Moonlit Bay (FR)', 'M')})
        mare_rec.refresh_from_db()
        self.assertEqual(mare_rec.sire.short_label, 'Moonlit Bay (FR)')
        # The mare's own tree shows the same sire: one record.
        self.assertEqual(self.mare.pedigree.sire, mare_rec.sire)

    def test_unknown_parent(self):
        services.set_parents(self.foal, {'S': resolve('Unknown', 'M')})
        r = PedigreeHorse.objects.get(horse=self.foal)
        self.assertTrue(r.sire_unknown)
        self.assertIsNone(r.sire)

    def test_a_loop_is_refused(self):
        services.set_parents(self.foal, {'D': Resolution('x', record=self.mare.pedigree)})
        with self.assertRaises(ValidationError):
            services.set_parents(self.mare, {'D': Resolution('x', record=self.foal.pedigree)})

    def test_a_record_used_by_others(self):
        services.set_parents(self.foal, {'D': Resolution('x', record=self.mare.pedigree)})
        sister = Horse.objects.create(name='Sister', sex='filly')
        services.set_parents(sister, {'D': Resolution('x', record=self.mare.pedigree)})
        self.assertEqual(services.other_users_of(self.mare.pedigree, self.foal), 1)


class FoalingTests(TestCase):
    """A foal born here gets its tree with no typing (§7.4)."""

    def test_foal_inherits_its_grandparents(self):
        mare = Horse.objects.create(name='Starlit Rose', sex='mare')
        services.set_parents(mare, {'S': resolve('Moonlit Bay (FR)', 'M'), 'D': resolve('Fair Lady (GB)', 'F')})
        stallion = rec('Copper Ridge', 'M', 'GB', 2014)
        stallion.sire = rec('Highland Star', 'M', 'IRE')
        stallion.save()
        foal = Horse.objects.create(name='Starlit Rose 2026 colt', sex='colt')
        services.link_foal(foal, mare, 'Copper Ridge (GB) 2014')
        tree = build(PedigreeHorse.objects.get(horse=foal), 2)
        self.assertEqual(tree.box('S').record, stallion)
        self.assertEqual(tree.box('SS').record.name, 'Highland Star')
        self.assertEqual(tree.box('DS').record.name, 'Moonlit Bay')
        self.assertEqual(tree.box('DD').record.name, 'Fair Lady')
        foal.refresh_from_db()
        self.assertEqual(foal.dam, mare)
        self.assertEqual(foal.sire_name, 'Copper Ridge (GB)')

    def test_one_stallion_by_name_is_him_several_make_a_new_record(self):
        one = rec('Copper Ridge', 'M', 'GB')
        self.assertEqual(services.stallion_for('Copper Ridge'), one)
        rec('Copper Ridge', 'M', 'IRE')
        new = services.stallion_for('Copper Ridge')
        self.assertNotIn(new.pk, [one.pk])
        self.assertEqual(new.source, PedigreeHorse.Source.FOALING)

    def test_a_dam_changed_by_hand_stays(self):
        donor = Horse.objects.create(name='Donor', sex='mare')
        recipient = Horse.objects.create(name='Recipient', sex='mare')
        foal = Horse.objects.create(name='ET foal', sex='filly')
        services.set_parents(foal, {'D': Resolution('x', record=donor.pedigree)})
        services.link_foal(foal, recipient, 'Copper Ridge')
        self.assertEqual(PedigreeHorse.objects.get(horse=foal).dam, donor.pedigree)


class FamilyTests(TestCase):

    def test_siblings_progeny_and_parents_on_the_yard(self):
        mare = Horse.objects.create(name='Starlit Rose', sex='mare')
        ridge = rec('Copper Ridge', 'M')
        other_sire = rec('Other Sire', 'M')
        a = Horse.objects.create(name='A', sex='filly')
        b = Horse.objects.create(name='B', sex='colt')
        c = Horse.objects.create(name='C', sex='colt')
        for horse, sire in ((a, ridge), (b, ridge), (c, other_sire)):
            services.set_parents(horse, {'S': Resolution('x', record=sire),
                                         'D': Resolution('x', record=mare.pedigree)})
        fam = services.family(PedigreeHorse.objects.get(horse=a))
        self.assertEqual(fam['dam'], mare)
        self.assertEqual(fam['full'], [b])
        self.assertEqual(fam['half'], [c])
        self.assertEqual(set(services.family(mare.pedigree)['progeny']), {a, b, c})


class TreeLayoutTests(SimpleTestCase):

    def test_position_index_middle_and_relation(self):
        self.assertEqual([index_of(c) for c in ('SS', 'SD', 'DS', 'DD')], [0, 1, 2, 3])
        self.assertEqual(middle(''), 50)
        self.assertEqual((middle('S'), middle('D')), (25, 75))
        self.assertEqual(middle('DS'), 62.5)
        self.assertEqual(relation('SD'), "Sire's dam")
        self.assertEqual(relation('DSD'), "Dam's sire's dam")
        self.assertEqual(relation(''), 'This horse')

    def test_curve_runs_from_the_child_to_the_parent(self):
        self.assertEqual(curve('', 'S'), 'M0 50.000 C55 50.000 45 25.000 100 25.000')
        self.assertEqual(curve('D', 'DD'), 'M0 75.000 C55 75.000 45 87.500 100 87.500')


class TreeBuildTests(TestCase):

    def setUp(self):
        self.hs = rec('Highland Star', 'M', 'IRE', 2005)
        self.ridge = rec('Copper Ridge', 'M', 'GB', 2014, sire=self.hs, dam=rec('Silver Brook', 'F'))
        fair = rec('Fair Lady', 'F', 'GB', 2010, sire=self.hs)
        self.starlit = rec('Starlit Rose', 'F', 'IRE', 2015, sire=rec('Moonlit Bay', 'M'), dam=fair)
        self.root = rec('Copper Rose', 'F', 'GB', 2023, sire=self.ridge, dam=self.starlit)

    def test_two_generations_one_query_each(self):
        with self.assertNumQueries(2):
            tree = build(self.root, 2)
        self.assertEqual(tree.rows, 4)
        self.assertEqual([b.code for b in tree.boxes], ['S', 'D', 'SS', 'SD', 'DS', 'DD'])
        self.assertTrue(all(b.state == KNOWN for b in tree.boxes))
        self.assertTrue(tree.has_more)
        self.assertEqual(tree.inbreeding, [])

    def test_grid_places(self):
        tree = build(self.root, 2)
        places = {p['box'].code: (p['box'].column, p['row'], p['span']) for p in tree.placed()}
        self.assertEqual(places['S'], (3, 1, 2))
        self.assertEqual(places['D'], (3, 3, 2))
        self.assertEqual(places['DS'], (5, 3, 1))
        # Family order: the sire line, then the dam line.
        self.assertEqual([p['box'].code for p in tree.placed()], ['S', 'SS', 'SD', 'D', 'DS', 'DD'])
        self.assertEqual([column for column, _g, _lines in tree.gap_list], [2, 4])

    def test_three_generations_find_the_inbreeding(self):
        tree = build(self.root, 3)
        self.assertEqual(tree.rows, 8)
        self.assertEqual(tree.twins, {self.hs.pk: '2×3'})
        self.assertEqual(tree.inbreeding, [(self.hs, '2×3')])
        self.assertEqual({b.code for b in tree.boxes if b.twin}, {'SS', 'DDS'})

    def test_unknown_and_missing_and_no_children_under_them(self):
        pony = rec('Paloma', 'F', sire=rec('Rey del Sur', 'M', dam_unknown=True), dam_unknown=True)
        tree = build(pony, 2)
        states = {b.code: b.state for b in tree.boxes}
        self.assertEqual(states, {'S': KNOWN, 'D': UNKNOWN, 'SS': MISSING, 'SD': UNKNOWN})
        self.assertEqual([b.relation for b in tree.missing], ["Sire's sire"])
        soft = {line.code: line.soft for lines in tree.gaps for line in lines}
        self.assertEqual(soft, {'S': False, 'D': True, 'SS': True, 'SD': True})
        self.assertFalse(tree.has_more)

    def test_generations_are_kept_between_1_and_3(self):
        self.assertEqual(build(self.root, 9).generations, 3)
        self.assertEqual(build(self.root, 0).generations, 1)
