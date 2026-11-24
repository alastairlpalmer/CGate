"""Horse identity, phase 1a (docs/HORSE_IDENTITY_AND_PEDIGREE_PLAN.md).

The rules in core/identity.py, the model behaviour (typed age becomes a
foaling year, the ID-checked stamp, duplicates), the forms, Scan to
check, extra numbers and links, the horse page, search, New Arrival and
the data steps of migration 0067.
"""

import importlib
from datetime import date
from unittest.mock import patch

from django.apps import apps
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from core import identity
from core.forms import HorseIdentifierForm, QuickHorseForm
from core.models import Horse, HorseIdentifier, Location, Owner, RateType
from core.roles_testutils import make_admin, make_viewer

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}
TODAY = 'django.utils.timezone.localdate'


def on(day):
    """Run with ``timezone.localdate()`` returning ``day``."""
    return patch(TODAY, return_value=day)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

class MicrochipRuleTests(SimpleTestCase):

    def test_spaces_dashes_and_scanner_words_come_out(self):
        for raw in ('985 141 000 123 456', '985-141-000-123-456', 'FDX-B 985 141000123456\n',
                    'fdxb:985141000123456', 'ISO 11784 985141000123456', ' 985141000123456 '):
            self.assertEqual(identity.normalise_microchip(raw), '985141000123456', raw)

    def test_letters_in_an_old_chip_are_kept(self):
        self.assertEqual(identity.normalise_microchip('4a2b-3c4d5e'), '4A2B3C4D5E')

    def test_nothing_in_nothing_out(self):
        self.assertEqual(identity.normalise_microchip(''), '')
        self.assertEqual(identity.normalise_microchip(None), '')

    def test_warning_only_for_a_chip_that_is_not_15_digits(self):
        self.assertEqual(identity.microchip_warning('985141000123456'), '')
        self.assertEqual(identity.microchip_warning(''), '')
        self.assertIn('not the usual 15 digits', identity.microchip_warning('123456789'))
        self.assertIn('not the usual 15 digits', identity.microchip_warning('98514100012345A'))

    def test_hint_names_the_country_or_the_maker_code(self):
        self.assertEqual(identity.microchip_hint('372141000123456'), 'Irish chip')
        self.assertEqual(identity.microchip_hint('826098000123456'), 'UK chip')
        self.assertEqual(identity.microchip_hint('032098100222333'), 'Argentine chip')
        self.assertEqual(identity.microchip_hint('985141000123456'), 'Maker code chip')
        self.assertEqual(identity.microchip_hint('999141000123456'), '')
        self.assertEqual(identity.microchip_hint('123456789'), '')

    def test_checker_is_irish_for_an_irish_chip(self):
        self.assertEqual(identity.chip_checker_url('372141000123456'), identity.CHIP_CHECKER_IRELAND)
        self.assertEqual(identity.chip_checker_url('826098000123456'), identity.CHIP_CHECKER_UK)

    def test_digits_in_groups_of_three(self):
        self.assertEqual(identity.group_digits('985141000123456'), '985 141 000 123 456')
        self.assertEqual(identity.group_digits(''), '')


class UelnCountryHeightRuleTests(SimpleTestCase):

    def test_ueln_is_capitals_without_spaces(self):
        self.assertEqual(identity.normalise_ueln(' 826-002 000123456 '), '826002000123456')
        self.assertEqual(identity.normalise_ueln('250001a12345678'), '250001A12345678')

    def test_ueln_refuses_other_characters_and_warns_on_length(self):
        with self.assertRaises(ValidationError):
            identity.validate_ueln('826002/000123')
        identity.validate_ueln('826002000123456')
        self.assertEqual(identity.ueln_warning('826002000123456'), '')
        self.assertIn('14 characters', identity.ueln_warning('82600200012345'))
        self.assertEqual(identity.format_ueln('826002000123456'), '826002 000123456')
        self.assertEqual(identity.format_ueln('8260020001'), '8260020001')

    def test_country_suffix(self):
        self.assertEqual(identity.normalise_country('(ire)'), 'IRE')
        self.assertEqual(identity.normalise_country(' gb '), 'GB')
        self.assertEqual(identity.normalise_country(''), '')
        with self.assertRaises(ValidationError):
            identity.normalise_country('Ireland')
        with self.assertRaises(ValidationError):
            identity.normalise_country('G1')

    def test_suffix_split_from_a_registered_name(self):
        self.assertEqual(identity.split_suffix('Karma Avicii (GB)'), ('Karma Avicii', 'GB'))
        self.assertEqual(identity.split_suffix('Rey del Sur (arg) '), ('Rey del Sur', 'ARG'))
        self.assertEqual(identity.split_suffix('Plain Name'), ('Plain Name', ''))
        self.assertEqual(identity.split_suffix(''), ('', ''))

    def test_height_in_hands(self):
        self.assertEqual(identity.parse_height('16.2'), 66)
        self.assertEqual(identity.parse_height('15.3hh'), 63)
        self.assertEqual(identity.parse_height('16 hh'), 64)
        self.assertIsNone(identity.parse_height(''))
        self.assertIsNone(identity.parse_height(None))
        for bad in ('16.4', '25', '4.1', 'tall', '16.25'):
            with self.assertRaises(ValidationError, msg=bad):
                identity.parse_height(bad)
        self.assertEqual(identity.format_height(66), '16.2 hh')
        self.assertEqual(identity.format_height(None), '')


class AgeRuleTests(SimpleTestCase):
    """One age rule for all horses (plan decision 4)."""

    def test_northern_horse_is_a_year_older_on_1_january(self):
        foaled = date(2023, 4, 10)
        self.assertEqual(identity.age_on(date(2025, 12, 31), foaled), 2)
        self.assertEqual(identity.age_on(date(2026, 1, 1), foaled), 3)
        # Before its birthday it is already 3: it races as a 3-year-old.
        self.assertEqual(identity.age_on(date(2026, 3, 1), foaled), 3)

    def test_southern_horse_is_a_year_older_on_1_august(self):
        foaled = date(2015, 10, 10)
        self.assertEqual(identity.age_on(date(2026, 7, 31), foaled, southern=True), 10)
        self.assertEqual(identity.age_on(date(2026, 8, 1), foaled, southern=True), 11)
        # March 2016 is still the 2015 season in the south.
        self.assertEqual(identity.age_on(date(2026, 8, 1), date(2016, 3, 10), southern=True), 11)

    def test_year_only_date_counts_from_its_own_year(self):
        self.assertEqual(identity.age_on(date(2026, 8, 1), date(2015, 1, 1), exact=False, southern=True), 11)
        self.assertEqual(identity.age_on(date(2026, 3, 1), date(2011, 1, 1), exact=False), 15)

    def test_no_date_or_a_future_date_has_no_age(self):
        self.assertIsNone(identity.age_on(date(2026, 1, 1), None))
        self.assertIsNone(identity.age_on(date(2026, 1, 1), date(2026, 5, 1)))

    def test_a_typed_age_gives_the_same_age_back_and_then_goes_up(self):
        dob = identity.estimated_dob_from_age(15, date(2026, 3, 1))
        self.assertEqual(dob, date(2011, 1, 1))
        self.assertEqual(identity.age_on(date(2026, 3, 1), dob, exact=False), 15)
        self.assertEqual(identity.age_on(date(2027, 1, 1), dob, exact=False), 16)
        south = identity.estimated_dob_from_age(10, date(2026, 3, 1), southern=True)
        self.assertEqual(identity.age_on(date(2026, 3, 1), south, exact=False, southern=True), 10)
        self.assertEqual(identity.age_on(date(2026, 8, 1), south, exact=False, southern=True), 11)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class HorseAgeModelTests(TestCase):

    def test_a_typed_age_becomes_an_estimated_foaling_year(self):
        with on(date(2026, 3, 1)):
            horse = Horse.objects.create(name='Ballerina', age=15)
            horse.refresh_from_db()
            self.assertIsNone(horse.age)
            self.assertEqual(horse.date_of_birth, date(2011, 1, 1))
            self.assertEqual(horse.dob_precision, Horse.DobPrecision.ESTIMATED)
            self.assertEqual(horse.calculated_age, 15)
            self.assertEqual(horse.age_label, '15yo')
            self.assertEqual(horse.foaled_display, 'foaled c. 2011')
        with on(date(2027, 1, 2)):
            self.assertEqual(horse.calculated_age, 16)

    def test_a_date_wins_over_a_typed_age(self):
        horse = Horse.objects.create(name='Both', age=5, date_of_birth=date(2015, 5, 1))
        horse.refresh_from_db()
        self.assertIsNone(horse.age)
        self.assertEqual(horse.date_of_birth, date(2015, 5, 1))
        self.assertEqual(horse.dob_precision, Horse.DobPrecision.EXACT)

    def test_save_with_update_fields_still_converts(self):
        with on(date(2026, 3, 1)):
            horse = Horse.objects.create(name='Later')
            Horse.objects.filter(pk=horse.pk).update(age=7)
            horse.refresh_from_db()
            horse.save(update_fields=['name'])
            horse.refresh_from_db()
            self.assertEqual(horse.date_of_birth, date(2019, 1, 1))
            self.assertIsNone(horse.age)

    def test_foal_label_says_yearling_after_1_january(self):
        horse = Horse.objects.create(name='Foal', date_of_birth=date(2025, 11, 1))
        with on(date(2025, 12, 1)):
            self.assertEqual(horse.age_label, '4 weeks old')
            self.assertTrue(horse.is_youngster)
            self.assertEqual(horse.foaled_display, 'foaled 1 Nov 2025')
        with on(date(2026, 1, 10)):
            self.assertEqual(horse.age_label, '10 weeks old, yearling')
            self.assertEqual(horse.calculated_age, 1)

    def test_year_only_foal(self):
        horse = Horse.objects.create(name='Unknown day', date_of_birth=date(2026, 1, 1),
                                     dob_precision=Horse.DobPrecision.YEAR)
        with on(date(2026, 6, 1)):
            self.assertEqual(horse.age_label, 'foal')
            self.assertTrue(horse.is_youngster)
            self.assertEqual(horse.foaled_display, 'foaled 2026')
            self.assertIsNone(horse.days_old)

    def test_adult_with_an_exact_date(self):
        horse = Horse.objects.create(name='Adult', date_of_birth=date(2011, 5, 3))
        with on(date(2026, 3, 1)):
            self.assertEqual(horse.foaled_display, 'born 3 May 2011')
            self.assertEqual(horse.age_label, '15yo')

    def test_southern_country_uses_1_august(self):
        horse = Horse.objects.create(name='Paloma', date_of_birth=date(2015, 10, 10),
                                     country_of_birth='ARG')
        with on(date(2026, 7, 31)):
            self.assertEqual(horse.calculated_age, 10)
        with on(date(2026, 8, 1)):
            self.assertEqual(horse.calculated_age, 11)


class HorseIdentityModelTests(TestCase):

    def test_every_horse_gets_its_own_fixed_id(self):
        a = Horse.objects.create(name='A')
        b = Horse.objects.create(name='B')
        self.assertIsNotNone(a.uid)
        self.assertNotEqual(a.uid, b.uid)
        uid = a.uid
        a.name = 'A2'
        a.save()
        a.refresh_from_db()
        self.assertEqual(a.uid, uid)

    def _checked(self, **fields):
        horse = Horse.objects.create(name='Checked', microchip='985141000123456',
                                     ueln='826002000123456', passport_number='P1', **fields)
        horse.mark_identity_checked(None)
        horse.save()
        horse.refresh_from_db()
        self.assertTrue(horse.is_identity_checked)
        return horse

    def test_a_new_microchip_clears_the_check(self):
        horse = self._checked()
        horse.microchip = '985141000999999'
        horse.save()
        horse.refresh_from_db()
        self.assertFalse(horse.is_identity_checked)

    def test_a_new_ueln_or_passport_number_clears_the_check(self):
        for field, value in (('ueln', '826002000999999'), ('passport_number', 'P2')):
            horse = self._checked()
            setattr(horse, field, value)
            horse.save(update_fields=[field])
            horse.refresh_from_db()
            self.assertFalse(horse.is_identity_checked, field)
            horse.delete()

    def test_other_changes_keep_the_check(self):
        horse = self._checked()
        horse.name = 'Renamed'
        horse.notes = 'Needs rug'
        horse.save()
        horse.refresh_from_db()
        self.assertTrue(horse.is_identity_checked)
        horse.save(update_fields=['name'])
        horse.refresh_from_db()
        self.assertTrue(horse.is_identity_checked)

    def test_a_new_number_saved_with_the_stamp_keeps_it(self):
        horse = self._checked()
        horse.microchip = '985141000999999'
        horse.mark_identity_checked(None)
        horse.save(update_fields=['microchip', 'identity_checked_at', 'identity_checked_by'])
        horse.refresh_from_db()
        self.assertTrue(horse.is_identity_checked)
        self.assertEqual(horse.microchip, '985141000999999')

    def test_duplicates_are_found_both_ways(self):
        rosie = Horse.objects.create(name='Rosie', microchip='372141000123456', is_active=False)
        twin = Horse.objects.create(name='Rosie again', microchip='372141000123456',
                                    ueln='826002000123456')
        other = Horse.objects.create(name='Other', ueln='826002000123456')
        self.assertEqual([(lab, h.name) for lab, _v, h in twin.identity_duplicates()],
                         [('Microchip', 'Rosie'), ('UELN', 'Other')])
        self.assertEqual([h.name for _l, _v, h in rosie.identity_duplicates()], ['Rosie again'])
        warnings = twin.identity_warnings()
        self.assertIn('Microchip 372141000123456 is also on Rosie (departed). Is this the same horse?', warnings)
        self.assertIn('UELN 826002000123456 is also on Other. Is this the same horse?', warnings)
        self.assertEqual(other.identity_duplicates()[0][2], twin)

    def test_an_odd_chip_warns(self):
        horse = Horse.objects.create(name='Old chip', microchip='123456789')
        self.assertTrue(any('15 digits' in w for w in horse.identity_warnings()))

    def test_registered_label_and_when_it_shows(self):
        horse = Horse(name='Avicii', registered_name='Karma Avicii', country_of_birth='GB')
        self.assertEqual(horse.registered_label, 'Karma Avicii (GB)')
        self.assertTrue(horse.show_registered_name)
        same = Horse(name='Copper Rose', registered_name='copper rose')
        self.assertFalse(same.show_registered_name)

    def test_missing_lists_the_key_fields(self):
        horse = Horse(name='Bare')
        self.assertEqual(horse.missing_identity, ['microchip', 'UELN', 'passport number', 'foaling date'])
        horse.microchip = '985141000123456'
        self.assertNotIn('microchip', horse.missing_identity)

    def test_changes_are_kept_in_history(self):
        horse = Horse.objects.create(name='Tracked', microchip='985141000123456')
        horse.microchip = '985141000999999'
        horse.save()
        chips = list(horse.history.order_by('history_id').values_list('microchip', flat=True))
        self.assertEqual(chips, ['985141000123456', '985141000999999'])


class HorseIdentifierModelTests(TestCase):

    def setUp(self):
        self.horse = Horse.objects.create(name='Linked')

    def test_needs_a_number_or_a_link(self):
        ident = HorseIdentifier(horse=self.horse, kind='racing')
        with self.assertRaises(ValidationError):
            ident.full_clean()
        ident.url = 'https://www.racingpost.com/profile/horse/1/copper-rose'
        ident.full_clean()
        self.assertEqual(ident.link_host, 'racingpost.com')
        self.assertEqual(ident.label, 'Racing')
        ident.issuer = 'Racing Post'
        self.assertEqual(ident.label, 'Racing Post')

    def test_only_web_links(self):
        ident = HorseIdentifier(horse=self.horse, kind='other', url='ftp://example.com/file')
        with self.assertRaises(ValidationError):
            ident.full_clean()


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------

class HorseFormIdentityTests(TestCase):

    def setUp(self):
        self.horse = Horse.objects.create(name='Avicii', sex='gelding', color='bay')

    def _form(self, **data):
        base = {'name': 'Avicii', 'sex': 'gelding', 'color': 'bay', 'has_passport': 'on',
                'is_active': 'on'}
        base.update(data)
        return QuickHorseForm(base, instance=self.horse)

    def test_numbers_are_tidied(self):
        form = self._form(microchip='FDX-B 985 141 000 123 456', ueln='826-002 000123456')
        self.assertTrue(form.is_valid(), form.errors)
        horse = form.save()
        self.assertEqual(horse.microchip, '985141000123456')
        self.assertEqual(horse.ueln, '826002000123456')

    def test_bad_ueln_characters_are_refused(self):
        form = self._form(ueln='826002/000123456')
        self.assertFalse(form.is_valid())
        self.assertIn('ueln', form.errors)

    def test_suffix_in_the_registered_name_becomes_the_country(self):
        form = self._form(registered_name='Karma Avicii (GB)')
        self.assertTrue(form.is_valid(), form.errors)
        horse = form.save()
        self.assertEqual((horse.registered_name, horse.country_of_birth), ('Karma Avicii', 'GB'))

    def test_a_typed_country_wins_over_the_suffix(self):
        form = self._form(registered_name='Karma Avicii (GB)', country_of_birth='ire')
        self.assertTrue(form.is_valid(), form.errors)
        horse = form.save()
        self.assertEqual((horse.registered_name, horse.country_of_birth), ('Karma Avicii', 'IRE'))

    def test_year_only_and_a_guessed_year(self):
        form = self._form(foaled_year='2011')
        self.assertTrue(form.is_valid(), form.errors)
        horse = form.save()
        self.assertEqual((horse.date_of_birth, horse.dob_precision), (date(2011, 1, 1), 'year'))
        form = self._form(foaled_year='2011', foaled_year_estimated='on')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().dob_precision, 'estimated')

    def test_a_full_date_wins_over_a_year(self):
        form = self._form(date_of_birth='2012-05-03', foaled_year='2011')
        self.assertTrue(form.is_valid(), form.errors)
        horse = form.save()
        self.assertEqual((horse.date_of_birth, horse.dob_precision), (date(2012, 5, 3), 'exact'))

    def test_no_future_dates(self):
        future = timezone.localdate().replace(year=timezone.localdate().year + 1)
        form = self._form(date_of_birth=future.isoformat())
        self.assertFalse(form.is_valid())
        self.assertIn('date_of_birth', form.errors)
        form = self._form(foaled_year=str(future.year))
        self.assertFalse(form.is_valid())
        self.assertIn('foaled_year', form.errors)

    def test_clearing_the_date_clears_it(self):
        self.horse.date_of_birth = date(2011, 1, 1)
        self.horse.dob_precision = 'estimated'
        self.horse.save()
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)
        horse = form.save()
        self.assertIsNone(horse.date_of_birth)
        self.assertEqual(horse.dob_precision, 'exact')

    def test_height(self):
        form = self._form(height='16.2')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().height_inches, 66)
        form = self._form(height='16.5')
        self.assertFalse(form.is_valid())
        self.assertIn('height', form.errors)

    def test_initial_values_show_a_year_and_a_height(self):
        self.horse.date_of_birth = date(2011, 1, 1)
        self.horse.dob_precision = 'estimated'
        self.horse.height_inches = 66
        self.horse.save()
        form = QuickHorseForm(instance=self.horse)
        self.assertIsNone(form.initial['date_of_birth'])
        self.assertEqual(form.initial['foaled_year'], 2011)
        self.assertTrue(form.initial['foaled_year_estimated'])
        self.assertEqual(form.initial['height'], '16.2')

    def test_microchip_box_ignores_the_scanner_enter_key(self):
        html = str(QuickHorseForm(instance=self.horse)['microchip'])
        self.assertIn('@keydown.enter.prevent', html)


class QuickEditIdentityViewTests(TestCase):

    def test_save_warns_about_a_duplicate_chip_but_saves(self):
        Horse.objects.create(name='Rosie', microchip='372141000123456', is_active=False)
        horse = Horse.objects.create(name='New Rosie')
        self.client.force_login(make_admin())
        response = self.client.post(reverse('horse_quick_edit', args=[horse.pk]), {
            'name': 'New Rosie', 'microchip': '372 141 000 123 456', 'has_passport': 'on',
            'is_active': 'on',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        horse.refresh_from_db()
        self.assertEqual(horse.microchip, '372141000123456')
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertIn('Microchip 372141000123456 is also on Rosie (departed). Is this the same horse?', texts)

    def test_history_records_who_changed_the_chip(self):
        admin = make_admin()
        horse = Horse.objects.create(name='Tracked')
        self.client.force_login(admin)
        self.client.post(reverse('horse_quick_edit', args=[horse.pk]), {
            'name': 'Tracked', 'microchip': '985141000123456', 'has_passport': 'on',
            'is_active': 'on',
        }, **POPUP)
        latest = horse.history.latest()
        self.assertEqual(latest.microchip, '985141000123456')
        self.assertEqual(latest.history_user, admin)


# ---------------------------------------------------------------------------
# Scan to check
# ---------------------------------------------------------------------------

class ScanCheckTests(TestCase):

    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)
        self.horse = Horse.objects.create(name='Chipped', microchip='372141000123456')
        self.url = reverse('horse_scan_check', args=[self.horse.pk])

    def test_popup_shows_the_form_and_a_direct_visit_goes_to_the_horse(self):
        response = self.client.get(self.url, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '372 141 000 123 456')
        self.assertContains(response, 'hx-select="unset"')
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse('horse_detail', args=[self.horse.pk]))

    def test_a_match_stamps_the_record(self):
        response = self.client.post(self.url, {'scanned': 'FDX-B 372 141000123456'}, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.horse.refresh_from_db()
        self.assertTrue(self.horse.is_identity_checked)
        self.assertEqual(self.horse.identity_checked_by, self.admin)

    def test_a_different_number_is_shown_and_nothing_changes(self):
        response = self.client.post(self.url, {'scanned': '372141000123457'}, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="scan-mismatch"')
        self.assertContains(response, '372 141 000 123 457')
        self.assertContains(response, '372 141 000 123 456')
        self.horse.refresh_from_db()
        self.assertFalse(self.horse.is_identity_checked)
        self.assertEqual(self.horse.microchip, '372141000123456')

    def test_saving_the_scan_keeps_the_old_number(self):
        response = self.client.post(self.url, {'scanned': '372141000123457', 'action': 'save'}, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.horse.refresh_from_db()
        self.assertEqual(self.horse.microchip, '372141000123457')
        self.assertTrue(self.horse.is_identity_checked)
        old = self.horse.identifiers.get()
        self.assertEqual((old.kind, old.value), ('old_microchip', '372141000123456'))

    def test_no_chip_on_record_saves_the_scan(self):
        bare = Horse.objects.create(name='Bare')
        url = reverse('horse_scan_check', args=[bare.pk])
        response = self.client.post(url, {'scanned': '826098000123456'}, **POPUP)
        self.assertContains(response, "Save the scanned number as this horse's microchip?")
        response = self.client.post(url, {'scanned': '826098000123456', 'action': 'save'}, **POPUP)
        self.assertEqual(response.status_code, 204)
        bare.refresh_from_db()
        self.assertEqual(bare.microchip, '826098000123456')
        self.assertTrue(bare.is_identity_checked)
        self.assertFalse(bare.identifiers.exists())

    def test_an_empty_scan_is_an_error(self):
        response = self.client.post(self.url, {'scanned': '  - '}, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Scan the chip, or type the number.')

    def test_view_only_role_cannot_scan(self):
        self.client.force_login(make_viewer())
        response = self.client.post(self.url, {'scanned': '372141000123456'}, **POPUP)
        self.assertEqual(response.status_code, 403)
        self.horse.refresh_from_db()
        self.assertFalse(self.horse.is_identity_checked)


# ---------------------------------------------------------------------------
# Extra numbers and links
# ---------------------------------------------------------------------------

class IdentifierViewTests(TestCase):

    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)
        self.horse = Horse.objects.create(name='Copper Rose')
        self.add_url = reverse('horse_identifier_add', args=[self.horse.pk])

    def test_add_in_the_popup(self):
        response = self.client.post(self.add_url, {
            'kind': 'racing', 'issuer': 'Racing Post',
            'url': 'https://www.racingpost.com/profile/horse/1/copper-rose', 'checked': 'on',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        ident = self.horse.identifiers.get()
        self.assertTrue(ident.checked)
        self.assertEqual(ident.checked_by, self.admin)
        self.assertIsNotNone(ident.checked_at)

    def test_add_on_a_full_page(self):
        self.assertEqual(self.client.get(self.add_url).status_code, 200)
        response = self.client.post(self.add_url, {'kind': 'stud_book', 'issuer': 'Weatherbys GSB',
                                                   'value': 'GB123'})
        self.assertRedirects(response, reverse('horse_detail', args=[self.horse.pk]))

    def test_needs_a_number_or_a_link(self):
        response = self.client.post(self.add_url, {'kind': 'other'}, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Type a number, a link, or both.')
        self.assertFalse(self.horse.identifiers.exists())

    def test_old_microchip_is_tidied(self):
        form = HorseIdentifierForm({'kind': 'old_microchip', 'value': '985 141 000 123 456'},
                                   instance=HorseIdentifier(horse=self.horse))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().value, '985141000123456')

    def test_unticking_checked_clears_who_checked(self):
        ident = HorseIdentifier.objects.create(horse=self.horse, kind='stud_book', value='GB1',
                                               checked=True, checked_at=timezone.now(), checked_by=self.admin)
        url = reverse('horse_identifier_edit', args=[self.horse.pk, ident.pk])
        response = self.client.post(url, {'kind': 'stud_book', 'value': 'GB1'}, **POPUP)
        self.assertEqual(response.status_code, 204)
        ident.refresh_from_db()
        self.assertFalse(ident.checked)
        self.assertIsNone(ident.checked_at)
        self.assertIsNone(ident.checked_by)

    def test_delete_needs_post_and_the_right_horse(self):
        ident = HorseIdentifier.objects.create(horse=self.horse, kind='stud_book', value='GB1')
        url = reverse('horse_identifier_delete', args=[self.horse.pk, ident.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        other = Horse.objects.create(name='Other')
        wrong = reverse('horse_identifier_delete', args=[other.pk, ident.pk])
        self.assertEqual(self.client.post(wrong).status_code, 404)
        self.assertRedirects(self.client.post(url), reverse('horse_detail', args=[self.horse.pk]))
        self.assertFalse(HorseIdentifier.objects.exists())

    def test_view_only_role_cannot_add(self):
        self.client.force_login(make_viewer())
        response = self.client.post(self.add_url, {'kind': 'stud_book', 'value': 'GB1'}, **POPUP)
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Horse page, preview and quick view
# ---------------------------------------------------------------------------

class HorsePageIdentityTests(TestCase):

    def setUp(self):
        self.admin = make_admin()
        self.horse = Horse.objects.create(
            name='Avicii', registered_name='Karma Avicii', country_of_birth='GB',
            microchip='372141000123456', ueln='826002000123456', passport_number='826002',
            passport_issuer='Weatherbys', breed='Thoroughbred', height_inches=66,
            markings='Star, near hind sock', date_of_birth=date(2011, 1, 1), dob_precision='estimated',
        )
        HorseIdentifier.objects.create(horse=self.horse, kind='racing', issuer='Racing Post',
                                       url='https://www.racingpost.com/profile/horse/1/karma-avicii')
        self.url = reverse('horse_detail', args=[self.horse.pk])

    def test_the_identity_card(self):
        # 24 Nov 2026: the full identity is the pop-up behind "All details"
        # in the banner's facts.
        self.client.force_login(self.admin)
        response = self.client.get(reverse('horse_identity', args=[self.horse.pk]),
                                   HTTP_HX_REQUEST='true', HTTP_HX_TARGET='popup-body')
        # The registered name is on the Identity card, not in the header too
        # (horse page review A3).
        card = response.content.decode().split('id="identity"', 1)[1]
        self.assertIn('Karma Avicii (GB)', card)
        self.assertNotContains(response, 'Registered as <span', html=False)
        self.assertContains(response, '372 141 000 123 456')
        self.assertContains(response, 'data-copy="372141000123456"')
        self.assertContains(response, 'Irish chip')
        self.assertContains(response, identity.CHIP_CHECKER_IRELAND)
        self.assertContains(response, '826002 000123456')
        self.assertContains(response, 'Weatherbys')
        self.assertContains(response, '16.2 hh')
        self.assertContains(response, 'Star, near hind sock')
        self.assertIn('c. 2011', card)  # the foaling year, on the card (horse page review A3)
        self.assertContains(response, 'Open on racingpost.com')
        self.assertContains(response, 'Chip not checked yet.')
        self.assertContains(response, reverse('horse_scan_check', args=[self.horse.pk]))
        self.assertNotContains(response, 'Missing:')

    def test_checked_badge_and_stamp(self):
        self.horse.mark_identity_checked(self.admin)
        self.horse.save()
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertContains(response, 'ID checked')
        self.assertContains(response, '✓ Chip checked')
        details = self.client.get(reverse('horse_identity', args=[self.horse.pk]),
                                  HTTP_HX_REQUEST='true', HTTP_HX_TARGET='popup-body')
        self.assertContains(details, 'data-testid="identity-checked"')

    def test_missing_line_and_duplicate_warning(self):
        Horse.objects.create(name='Twin', microchip='372141000123456', is_active=False)
        bare = Horse.objects.create(name='Bare', microchip='372141000123456')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('horse_detail', args=[bare.pk]))
        self.assertContains(response, 'Missing: UELN, passport number, foaling date.')
        self.assertContains(response, 'Microchip also on')
        self.assertContains(response, '(departed). Is this the same horse?')

    def test_view_only_role_sees_no_edit_buttons(self):
        self.client.force_login(make_viewer())
        response = self.client.get(self.url)
        self.assertContains(response, '372 141 000 123 456')
        self.assertNotContains(response, reverse('horse_scan_check', args=[self.horse.pk]))
        self.assertNotContains(response, reverse('horse_identifier_add', args=[self.horse.pk]))

    def test_notes_card_no_longer_repeats_the_passport_number(self):
        self.horse.notes = 'Needs rug'
        self.horse.save()
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertContains(response, 'Notes &amp; conditions')
        self.assertNotContains(response, '>Passport number<')

    def test_preview_and_quick_view_show_the_chip(self):
        self.client.force_login(self.admin)
        preview = self.client.get(reverse('horse_preview', args=[self.horse.pk]),
                                  HTTP_HX_REQUEST='true', HTTP_HX_TARGET='horse-preview-body')
        self.assertContains(preview, '372 141 000 123 456')
        self.assertContains(preview, '826002 000123456')
        quick = self.client.get(reverse('horse_quick_view', args=[self.horse.pk]), **POPUP)
        self.assertContains(quick, '372 141 000 123 456')


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class IdentitySearchTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())
        self.horse = Horse.objects.create(name='Avicii', registered_name='Karma Avicii',
                                          microchip='372141000123456', ueln='826002000123456')
        Horse.objects.create(name='Someone else')

    def test_horse_list_finds_by_registered_name_chip_and_ueln(self):
        for query in ('Karma', '372 141 000', '826002000123456'):
            response = self.client.get(reverse('horse_list'), {'search': query})
            names = [h.name for h in response.context['horses']]
            self.assertEqual(names, ['Avicii'], query)

    def test_app_search_by_ueln_and_registered_name(self):
        response = self.client.get(reverse('quick_find'), {'q': '826002000'})
        self.assertContains(response, 'Avicii')
        self.assertContains(response, 'UELN')
        response = self.client.get(reverse('quick_find'), {'q': 'Karma Avi'})
        self.assertContains(response, 'Avicii')


class AgeSortTests(TestCase):
    """The database sort follows the same age rule as the page."""

    def test_sort_matches_calculated_age(self):
        with on(date(2026, 3, 1)):
            Horse.objects.create(name='North 2023', date_of_birth=date(2023, 4, 10))      # 3
            Horse.objects.create(name='South 2022', date_of_birth=date(2022, 10, 1),
                                 country_of_birth='ARG')                                    # 3
            Horse.objects.create(name='South 2023', date_of_birth=date(2023, 9, 1),
                                 country_of_birth='ARG')                                    # 2
            Horse.objects.create(name='Guess 2020', age=6)                                  # 6
            Horse.objects.create(name='No date')
            from core.views.horses import _age_sort_expression
            rows = Horse.objects.annotate(sort_age=_age_sort_expression())
            by_db = {h.name: h.sort_age for h in rows}
            by_page = {h.name: h.calculated_age for h in Horse.objects.all()}
            self.assertEqual(by_db, by_page)
            self.assertEqual(by_page, {'North 2023': 3, 'South 2022': 3, 'South 2023': 2,
                                       'Guess 2020': 6, 'No date': None})


# ---------------------------------------------------------------------------
# New arrival
# ---------------------------------------------------------------------------

class NewArrivalChipTests(TestCase):

    def test_chip_is_saved_tidy_and_a_returning_horse_is_flagged(self):
        Horse.objects.create(name='Rosie', microchip='372141000123456', is_active=False)
        owner = Owner.objects.create(name='Jo Bloggs')
        location = Location.objects.create(name='Top Field', site='Main')
        rate = RateType.objects.create(name='Full', daily_rate=10)
        self.client.force_login(make_admin())
        data = {
            'name': 'Rosie 2', 'owner': owner.pk, 'location': location.pk, 'rate_type': rate.pk,
            'arrival_date': timezone.localdate().isoformat(), 'microchip': 'FDX-B 372 141 000 123 456',
        }
        # The chip is on a horse that was here before: the save stops once
        # and offers to bring Rosie back (core/returning.py).
        response = self.client.post(reverse('horse_new_arrival'), data)
        self.assertContains(response, 'This horse was here before')
        self.assertFalse(Horse.objects.filter(name='Rosie 2').exists())
        # "Make a new record anyway" goes on, and the new record says why.
        response = self.client.post(reverse('horse_new_arrival'), {**data, 'new_record': '1'}, follow=True)
        horse = Horse.objects.get(name='Rosie 2')
        self.assertEqual(horse.microchip, '372141000123456')
        self.assertContains(response, 'is also on Rosie (departed). Is this the same horse?')


# ---------------------------------------------------------------------------
# Migration 0067 data steps
# ---------------------------------------------------------------------------

class MigrationDataStepTests(TestCase):

    def setUp(self):
        self.migration = importlib.import_module('core.migrations.0067_horse_identity')

    def _legacy(self, name, age, made, **fields):
        horse = Horse.objects.create(name=name, **fields)
        Horse.objects.filter(pk=horse.pk).update(age=age, date_of_birth=fields.get('date_of_birth'),
                                                 created_at=timezone.make_aware(
                                                     timezone.datetime(made.year, made.month, made.day)))
        return horse

    def test_ages_become_foaling_years_and_back(self):
        typed = self._legacy('Typed', 15, date(2026, 3, 1))
        both = self._legacy('Both', 4, date(2026, 3, 1), date_of_birth=date(2015, 5, 1))
        self.migration.ages_to_foaling_years(apps, None)
        typed.refresh_from_db()
        both.refresh_from_db()
        self.assertEqual((typed.date_of_birth, typed.dob_precision, typed.age),
                         (date(2011, 1, 1), 'estimated', None))
        self.assertEqual((both.date_of_birth, both.dob_precision, both.age),
                         (date(2015, 5, 1), 'exact', None))
        self.migration.foaling_years_to_ages(apps, None)
        typed.refresh_from_db()
        self.assertEqual((typed.age, typed.date_of_birth), (15, None))

    def test_microchips_lose_spaces_and_dashes(self):
        horse = Horse.objects.create(name='Spaced')
        Horse.objects.filter(pk=horse.pk).update(microchip='985 141-000 123456')
        self.migration.tidy_microchips(apps, None)
        horse.refresh_from_db()
        self.assertEqual(horse.microchip, '985141000123456')

    def test_the_id_step_leaves_existing_ids_alone(self):
        # The column is unique and not null by now, so the step can only
        # be tested for not touching rows that already have an ID. The
        # real three-step run is checked against a database in the PR.
        a = Horse.objects.create(name='A')
        before = a.uid
        self.migration.fill_uids(apps, None)
        self.assertEqual(Horse.objects.get(pk=a.pk).uid, before)
