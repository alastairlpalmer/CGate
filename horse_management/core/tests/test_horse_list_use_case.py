"""Tests for the use-case controls in the horse list's Location sort menu.

The Location grouping prints one card per field. Its menu can order the
fields by use case inside each site, and limit the fields shown to a set
of use cases — one tick per case. Both live in the Sort pop-out, next to
"Show empty" and the land-use window.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import administrator_role, assign_role

User = get_user_model()


class HorseListUseCaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User(
            username='grazier',
            last_login=timezone.now(),
            date_joined=timezone.now(),
            is_active=True,
        )
        cls.user.set_password('x')
        cls.user.save()
        assign_role(cls.user, administrator_role())

        cls.rate = RateType.objects.create(name='Grass livery', daily_rate=10)
        cls.owner = Owner.objects.create(name='Olive Orchard')

        Usage = Location.Usage
        # One site, four fields with different use cases. Name order and
        # use-case order disagree on purpose.
        cls.arable = Location.objects.create(
            name='Arable Acre', site='Colgate', usage=Usage.ARABLE,
        )
        cls.big = Location.objects.create(
            name='Big Field', site='Colgate', usage=Usage.HORSES,
        )
        cls.mixed = Location.objects.create(
            name='Mixed Meadow', site='Colgate', usage=Usage.MIXED,
        )
        cls.rested = Location.objects.create(
            name='Rested Rise', site='Colgate', usage=Usage.RESTED,
        )
        # A second site, so "in site" can be checked.
        cls.other_mixed = Location.objects.create(
            name='Other Mixed', site='Alicky', usage=Usage.MIXED,
        )

        start = timezone.localdate() - timedelta(days=30)
        cls.dobbin = cls._horse('Dobbin', cls.big, start)
        cls.ember = cls._horse('Ember', cls.mixed, start)
        cls.flint = cls._horse('Flint', cls.other_mixed, start)
        # A horse arriving on an empty field marks it Horses, so the
        # mixed grazing is set after the horses are on it — the manual
        # state an occupied field keeps.
        Location.objects.filter(
            pk__in=[cls.mixed.pk, cls.other_mixed.pk],
        ).update(usage=Usage.MIXED)

    @classmethod
    def _horse(cls, name, location, start):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=cls.owner, location=location,
            rate_type=cls.rate, start_date=start,
        )
        return horse

    def setUp(self):
        self.client.force_login(self.user)

    def _get(self, **params):
        return self.client.get(reverse('horse_list'), params)

    def _groups(self, **params):
        params.setdefault('group_by', 'location')
        return self._get(**params).context['grouped_horses']

    def _names(self, **params):
        return [g['name'] for g in self._groups(**params)]

    def _horse_names(self, **params):
        return [h.name for g in self._groups(**params) for h in g['horses']]

    # ── Order groups by use case ─────────────────────────────────────

    def test_use_case_order_is_offered_on_the_location_axis_only(self):
        keys = [
            o['key'] for o in
            self._get(group_by='location').context['group_sort_options']
        ]
        self.assertIn('usage', keys)
        for axis in ('site', 'owner'):
            keys = [
                o['key'] for o in
                self._get(group_by=axis).context['group_sort_options']
            ]
            self.assertNotIn('usage', keys, axis)

    def test_use_case_order_label_says_in_site(self):
        labels = {
            o['key']: o['label'] for o in
            self._get(group_by='location').context['group_sort_options']
        }
        self.assertEqual(labels['usage'], 'Use case, in site')

    def test_groups_order_by_use_case_inside_each_site(self):
        # Horses, then Mixed, Rested, Arable — the order the use cases
        # are declared in — and each site's fields stay together.
        self.assertEqual(
            self._names(gsort='usage'),
            ['Other Mixed',
             'Big Field', 'Mixed Meadow', 'Rested Rise', 'Arable Acre'],
        )

    def test_use_case_order_falls_back_off_the_location_axis(self):
        response = self._get(group_by='owner', gsort='usage')
        self.assertEqual(response.context['group_sort'], 'name')

    # ── Filter by use case ───────────────────────────────────────────

    def test_no_filter_by_default(self):
        response = self._get(group_by='location')
        self.assertEqual(response.context['use_filter'], [])
        self.assertEqual(len(self._names()), 5)

    def test_one_use_case_limits_the_fields_shown(self):
        self.assertEqual(
            self._names(use='mixed'), ['Other Mixed', 'Mixed Meadow'],
        )

    def test_several_use_cases_are_ticks_not_a_pick_one(self):
        names = self._names(use=['horses', 'rested'])
        self.assertEqual(names, ['Big Field', 'Rested Rise'])

    def test_horses_on_fields_left_out_are_not_listed(self):
        # Dobbin stands on the Horses field, so a list limited to mixed
        # grazing leaves him out.
        self.assertEqual(self._horse_names(use='mixed'), ['Flint', 'Ember'])

    def test_filter_hides_empty_fields_too_when_asked(self):
        self.assertEqual(
            self._names(use=['mixed', 'arable'], show_empty='0'),
            ['Other Mixed', 'Mixed Meadow'],
        )

    def test_unknown_use_case_is_dropped(self):
        response = self._get(group_by='location', use=['mixed', 'lunar'])
        self.assertEqual(response.context['use_filter'], ['mixed'])
        self.assertEqual(response.status_code, 200)

    def test_filter_is_ignored_off_the_location_axis(self):
        response = self._get(group_by='owner', use='mixed')
        self.assertEqual(response.context['use_filter'], [])
        self.assertIsNone(response.context['use_filter_options'])
        self.assertIn('Dobbin', [
            h.name for g in response.context['grouped_horses']
            for h in g['horses']
        ])

    # ── The menu ─────────────────────────────────────────────────────

    def test_menu_offers_one_tick_per_use_case(self):
        options = self._get(group_by='location').context['use_filter_options']
        self.assertEqual(
            [o['key'] for o in options],
            ['horses', 'mixed', 'rested', 'hay', 'arable', 'other'],
        )
        self.assertEqual(options[1]['label'], 'Mixed Grazing')
        self.assertFalse(any(o['active'] for o in options))

    def test_each_row_toggles_its_own_use_case(self):
        options = {
            o['key']: o for o in
            self._get(group_by='location', use='mixed').context['use_filter_options']
        }
        self.assertTrue(options['mixed']['active'])
        # Clicking the ticked row clears the last value, so no filter.
        self.assertIsNone(options['mixed']['values'])
        # Clicking another row adds it, keeping the declared order.
        self.assertEqual(options['horses']['values'], ['horses', 'mixed'])

    def test_menu_renders_the_ticks_and_the_hrefs(self):
        body = self._get(group_by='location', use='mixed').content.decode()
        self.assertIn('Use case', body)
        self.assertIn('All use cases', body)
        self.assertIn('use=horses&amp;use=mixed', body)
        # The filter survives a change of the Location/Owner filter form.
        self.assertIn('<input type="hidden" name="use" value="mixed">', body)
        # The active toggle says how many use cases it is limited to.
        self.assertIn('· 1', body)

    def test_leaving_the_location_axis_drops_the_filter_from_the_link(self):
        body = self._get(group_by='location', use='mixed').content.decode()
        self.assertIn('group_by=owner', body)
        self.assertNotIn('group_by=owner&amp;use=mixed', body)
        self.assertNotIn('use=mixed&amp;group_by=owner', body)
