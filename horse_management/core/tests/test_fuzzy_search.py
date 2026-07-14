"""Tests for typo-tolerant horse search (core.search)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.search import fuzzy_horse_ids, is_fuzzy_match

User = get_user_model()


class IsFuzzyMatchTests(SimpleTestCase):
    def test_exact_substring_matches(self):
        self.assertTrue(is_fuzzy_match('hunt', 'ALIHUNTER'))

    def test_missing_letter_matches(self):
        self.assertTrue(is_fuzzy_match('alihnter', 'ALIHUNTER'))

    def test_extra_letter_matches(self):
        self.assertTrue(is_fuzzy_match('alihunteer', 'ALIHUNTER'))

    def test_swapped_letters_match(self):
        self.assertTrue(is_fuzzy_match('alihunetr', 'ALIHUNTER'))

    def test_partial_with_typo_matches(self):
        # Part-way through typing the name, with one typo.
        self.assertTrue(is_fuzzy_match('alihnt', 'ALIHUNTER'))

    def test_single_word_of_multiword_name_matches(self):
        self.assertTrue(is_fuzzy_match('mitchel', 'Sarah Mitchell'))

    def test_unrelated_text_does_not_match(self):
        self.assertFalse(is_fuzzy_match('zzzqqq', 'ALIHUNTER'))

    def test_short_queries_require_exact_substring(self):
        self.assertTrue(is_fuzzy_match('al', 'ALIHUNTER'))
        self.assertFalse(is_fuzzy_match('ax', 'ALIHUNTER'))

    def test_empty_values_do_not_match(self):
        self.assertFalse(is_fuzzy_match('', 'ALIHUNTER'))
        self.assertFalse(is_fuzzy_match('ali', None))


class HorseFuzzySearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User(
            username='searcher',
            last_login=timezone.now(),
            date_joined=timezone.now(),
            is_active=True,
        )
        cls.user.set_password('x')
        cls.user.save()
        from core.roles_testutils import administrator_role, assign_role
        assign_role(cls.user, administrator_role())

        cls.owner = Owner.objects.create(
            name='Sarah Mitchell', email='sarah@example.com', phone='07700111222'
        )
        cls.location = Location.objects.create(name='Rough Grounds', site='California Farm')
        cls.rate = RateType.objects.create(name='Full livery', daily_rate=30)

        cls.alihunter = Horse.objects.create(name='ALIHUNTER')
        cls.dobbin = Horse.objects.create(name='Dobbin')
        Placement.objects.create(
            horse=cls.alihunter, owner=cls.owner, location=cls.location,
            rate_type=cls.rate, start_date=date(2026, 1, 1),
        )

    def _search(self, query):
        self.client.force_login(self.user)
        response = self.client.get(reverse('horse_list'), {'search': query})
        self.assertEqual(response.status_code, 200)
        return list(response.context['horses'])

    def test_exact_search_still_works(self):
        self.assertIn(self.alihunter, self._search('ALIHUNTER'))

    def test_typo_finds_horse(self):
        self.assertIn(self.alihunter, self._search('alihnter'))

    def test_extra_letter_finds_horse(self):
        self.assertIn(self.alihunter, self._search('alihunterr'))

    def test_owner_typo_finds_horse(self):
        self.assertIn(self.alihunter, self._search('mitchel'))

    def test_location_typo_finds_horse(self):
        self.assertIn(self.alihunter, self._search('rough gronds'))

    def test_unrelated_query_finds_nothing(self):
        self.assertEqual(self._search('zzzqqq'), [])

    def test_fuzzy_horse_ids_scopes_to_matches(self):
        ids = fuzzy_horse_ids('alihnter')
        self.assertIn(self.alihunter.pk, ids)
        self.assertNotIn(self.dobbin.pk, ids)
