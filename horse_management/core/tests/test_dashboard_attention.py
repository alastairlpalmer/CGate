"""Tests for the dashboard's data layer (core.dashboard.*).

The collectors are the single source of truth for "needs attention", so
these tests pin down the rules: latest record only, overdue before due,
today in the inbox and later on the strip, shared visits grouped, rows
gated by feature access and narrowed by site.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.dashboard import activity, attention, board, breeding, money, upcoming
from core.models import (
    Document, Horse, Location, LocationUsagePeriod, Owner, Placement, RateType,
)
from core.roles_testutils import make_admin, make_user_with_access
from health.models import (
    BreedingRecord, FarrierVisit, Vaccination, VaccinationType, VetVisit,
    WormEggCount, WormingTreatment,
)
from invoicing.models import Invoice, Payment


class DashboardDataTestCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.admin = make_admin(username='data-admin')
        self.owner = Owner.objects.create(name='Jo Bloggs', email='jo@example.com')
        self.rate = RateType.objects.create(name='Grass', daily_rate=5)
        self.somerford = Location.objects.create(name='Sandhills', site='Somerford', capacity=8)
        self.colgate = Location.objects.create(name='Bottom Barn', site='Colgate', capacity=6)
        self.flu = VaccinationType.objects.create(name='Flu')

    def horse(self, name, location=None, **kwargs):
        horse = Horse.objects.create(name=name, **kwargs)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=location or self.somerford,
            rate_type=self.rate, start_date=self.today - timedelta(days=100),
        )
        return horse

    def vaccination(self, horse, due, given_days_ago=300):
        return Vaccination.objects.create(
            horse=horse, vaccination_type=self.flu,
            date_given=self.today - timedelta(days=given_days_ago),
            next_due_date=self.today + timedelta(days=due),
        )

    def farrier(self, horse, due, visited_days_ago=56):
        return FarrierVisit.objects.create(
            horse=horse, date=self.today - timedelta(days=visited_days_ago),
            next_due_date=self.today + timedelta(days=due),
        )

    def invoice(self, number, total, due, status=Invoice.Status.SENT):
        return Invoice.objects.create(
            owner=self.owner, invoice_number=number,
            period_start=self.today - timedelta(days=60),
            period_end=self.today - timedelta(days=31),
            subtotal=Decimal(total), total=Decimal(total),
            status=status, due_date=self.today + timedelta(days=due),
        )

    def collect(self, user=None, **kwargs):
        return attention.collect(user or self.admin, today=self.today, **kwargs)

    def kinds(self, items):
        return [(i.kind, i.title) for i in items]


class SeverityAndSplitTests(DashboardDataTestCase):
    def test_overdue_then_today_then_later(self):
        a = self.horse('Late')
        b = self.horse('Today')
        c = self.horse('Soon')
        self.vaccination(a, -3)
        self.vaccination(b, 0)
        self.vaccination(c, 5)
        items = self.collect()
        self.assertEqual([i.title for i in items], ['Late', 'Today', 'Soon'])
        self.assertEqual([i.severity for i in items], ['overdue', 'due', 'due'])
        self.assertEqual([i.delta for i in items], [-3, 0, 5])

        inbox, later = attention.split(items, self.today)
        self.assertEqual([i.title for i in inbox], ['Late', 'Today'])
        # Today is on the strip too, so the two views agree about today.
        self.assertEqual([i.title for i in later], ['Today', 'Soon'])

    def test_horizon_bounds_the_collectors(self):
        self.vaccination(self.horse('Far'), 20)
        self.assertEqual(self.collect(), [])
        self.assertEqual(len(self.collect(horizon_days=30)), 1)

    def test_superseded_vaccination_is_ignored(self):
        horse = self.horse('Booster')
        self.vaccination(horse, -400, given_days_ago=765)   # last year's record
        self.vaccination(horse, 200, given_days_ago=165)    # re-vaccinated
        self.assertEqual(self.collect(horizon_days=30), [])

    def test_superseded_farrier_visit_is_ignored(self):
        horse = self.horse('Shod')
        self.farrier(horse, -30, visited_days_ago=72)
        self.farrier(horse, 12, visited_days_ago=30)
        items = self.collect()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].delta, 12)

    def test_inactive_horses_are_skipped(self):
        horse = self.horse('Gone')
        self.vaccination(horse, -3)
        Horse.objects.filter(pk=horse.pk).update(is_active=False)
        self.assertEqual(self.collect(), [])

    def test_items_know_where_the_horse_stands(self):
        horse = self.horse('Beech', self.colgate)
        self.vaccination(horse, -1)
        item = self.collect()[0]
        self.assertEqual((item.location, item.site), ('Bottom Barn', 'Colgate'))
        self.assertEqual(item.url, reverse('horse_detail', args=[horse.pk]))


class ActionTests(DashboardDataTestCase):
    def test_full_access_gets_a_popup_action_primary_when_overdue(self):
        horse = self.horse('Late')
        self.vaccination(horse, -3)
        [item] = self.collect()
        [action] = item.actions
        self.assertEqual(action.url, reverse('vaccination_create') + f'?horse={horse.pk}')
        self.assertEqual(action.popup_title, 'Record vaccination for Late')
        self.assertEqual(action.style, 'primary')

    def test_view_only_gets_no_actions(self):
        horse = self.horse('Late')
        self.vaccination(horse, -3)
        viewer = make_user_with_access('viewer', dashboard='full', health='view', horses='view')
        [item] = self.collect(user=viewer)
        self.assertEqual(item.actions, [])

    def test_hidden_feature_skips_its_collector(self):
        horse = self.horse('Late')
        self.vaccination(horse, -3)
        self.invoice('INV1', '100.00', -10)
        no_money = make_user_with_access('nomoney', dashboard='full', health='full', horses='view')
        self.assertEqual([i.kind for i in self.collect(user=no_money)], ['vaccination'])
        no_health = make_user_with_access('nohealth', dashboard='full', invoices='view')
        self.assertEqual([i.kind for i in self.collect(user=no_health)], ['invoice'])


class RowGroupingTests(DashboardDataTestCase):
    def test_two_horses_same_day_same_kind_become_one_visit_row(self):
        a = self.horse('Huella')
        b = self.horse('True')
        self.farrier(a, -14)
        self.farrier(b, -14)
        inbox, _ = attention.split(self.collect(), self.today)
        rows = attention.rows(inbox, self.admin)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.kind, 'visit')
        self.assertEqual(row.severity, 'overdue')
        self.assertEqual({h.pk for h in row.horses}, {a.pk, b.pk})
        self.assertEqual(row.action.label, 'Record for 2')
        self.assertIn(f'horse_ids={a.pk}', row.action.url)
        self.assertIn(f'horse_ids={b.pk}', row.action.url)
        self.assertIn('action_type=farrier', row.action.url)
        self.assertIn('popup=1', row.action.url)

    def test_visit_row_has_no_bulk_action_for_view_only(self):
        a = self.horse('Huella')
        b = self.horse('True')
        self.farrier(a, -14)
        self.farrier(b, -14)
        viewer = make_user_with_access('viewer2', dashboard='full', health='view', horses='view')
        inbox, _ = attention.split(self.collect(user=viewer), self.today)
        [row] = attention.rows(inbox, viewer)
        self.assertEqual(row.kind, 'visit')
        self.assertIsNone(row.action)

    def test_one_horse_with_two_items_is_one_row(self):
        horse = self.horse('Punk Rock')
        self.farrier(horse, -14)
        self.vaccination(horse, 0)
        inbox, _ = attention.split(self.collect(), self.today)
        rows = attention.rows(inbox, self.admin)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kind, 'horse')
        self.assertEqual([i.kind for i in rows[0].items], ['farrier', 'vaccination'])
        self.assertEqual(rows[0].severity, 'overdue')
        self.assertEqual(rows[0].subtitle, 'Sandhills · Somerford')

    def test_rows_sort_overdue_first_then_by_date(self):
        late = self.horse('Late')
        today = self.horse('Today')
        self.vaccination(today, 0)
        self.vaccination(late, -5)
        self.invoice('INV1', '100.00', -30)
        inbox, _ = attention.split(self.collect(), self.today)
        rows = attention.rows(inbox, self.admin)
        self.assertEqual([r.title for r in rows], ['INV1', 'Late', 'Today'])

    def test_summary_counts(self):
        self.vaccination(self.horse('Late'), -5)
        self.vaccination(self.horse('Today'), 0)
        self.vaccination(self.horse('Soon'), 6)
        self.invoice('INV1', '100.00', -30)
        items = self.collect()
        inbox, later = attention.split(items, self.today)
        rows = attention.rows(inbox, self.admin)
        summary = attention.summary(rows, later, self.today)
        self.assertEqual(summary['things'], 3)
        self.assertEqual(summary['overdue'], 2)
        self.assertEqual(summary['today'], 1)
        self.assertEqual(summary['upcoming'], 1)
        self.assertEqual(summary['money'], 1)
        self.assertEqual(summary['health'], 2)


class SiteFilterTests(DashboardDataTestCase):
    def test_site_keeps_that_sites_horses_and_siteless_items(self):
        self.vaccination(self.horse('Beech', self.somerford), -1)
        self.vaccination(self.horse('Pridie', self.colgate), -1)
        self.invoice('INV1', '100.00', -10)
        titles = sorted(i.title for i in self.collect(site='Colgate'))
        self.assertEqual(titles, ['INV1', 'Pridie'])
        self.assertEqual(len(self.collect()), 3)


class OtherCollectorTests(DashboardDataTestCase):
    def test_vet_follow_up(self):
        horse = self.horse('Sore')
        VetVisit.objects.create(
            horse=horse, date=self.today - timedelta(days=10), reason='Lame off fore',
            follow_up_date=self.today - timedelta(days=1),
        )
        [item] = self.collect()
        self.assertEqual(item.kind, 'vet')
        self.assertEqual(item.severity, 'overdue')
        self.assertIn('Lame off fore', item.detail)
        self.assertEqual(item.actions[0].url, reverse('vet_visit_create') + f'?horse={horse.pk}')

    def test_high_egg_count_until_wormed(self):
        horse = self.horse('Wormy')
        WormEggCount.objects.create(horse=horse, date=self.today - timedelta(days=5), count=450)
        [item] = self.collect()
        self.assertEqual(item.kind, 'egg_count')
        self.assertEqual(item.severity, 'info')
        self.assertIn('450 EPG', item.detail)
        self.assertTrue(attention.is_inbox(item, self.today))
        self.assertEqual(item.actions[0].url, reverse('worming_create') + f'?horse={horse.pk}')

        WormingTreatment.objects.create(horse=horse, date=self.today - timedelta(days=2), product_name='Equest')
        self.assertEqual(self.collect(), [])

    def test_low_and_old_egg_counts_are_not_items(self):
        horse = self.horse('Clean')
        WormEggCount.objects.create(horse=horse, date=self.today - timedelta(days=5), count=150)
        WormEggCount.objects.create(horse=horse, date=self.today - timedelta(days=120), count=900)
        self.assertEqual(self.collect(), [])

    def test_documents_expired_and_expiring(self):
        horse = self.horse('Insured')
        Document.objects.create(
            horse=horse, doc_type='insurance', title='Cover note', file='documents/a.pdf',
            expiry_date=self.today - timedelta(days=2),
        )
        Document.objects.create(
            owner=self.owner, doc_type='other', title='Livery agreement', file='documents/b.pdf',
            expiry_date=self.today + timedelta(days=20),
        )
        Document.objects.create(
            horse=horse, doc_type='passport', title='Passport', file='documents/c.pdf',
        )
        items = [i for i in self.collect() if i.kind == 'document']
        self.assertEqual([(i.title, i.severity) for i in items],
                         [('Insured', 'overdue'), ('Jo Bloggs', 'due')])
        self.assertEqual(items[0].detail, 'Insurance Certificate expired · Cover note')
        self.assertEqual(items[1].url, reverse('owner_detail', args=[self.owner.pk]))
        self.assertTrue(all(attention.is_inbox(i, self.today) for i in items))

    def test_departure_to_confirm_only_when_no_open_placement(self):
        leaver = Horse.objects.create(name='Leaver')
        Placement.objects.create(
            horse=leaver, owner=self.owner, location=self.somerford, rate_type=self.rate,
            start_date=self.today - timedelta(days=30), end_date=self.today - timedelta(days=1),
        )
        mover = self.horse('Mover')
        Placement.objects.create(  # closed history from a move; still placed
            horse=mover, owner=self.owner, location=self.colgate, rate_type=self.rate,
            start_date=self.today - timedelta(days=200), end_date=self.today - timedelta(days=101),
        )
        items = [i for i in self.collect() if i.kind == 'departure']
        self.assertEqual([i.title for i in items], ['Leaver'])
        self.assertEqual([a.label for a in items[0].actions], ['Confirm departed', 'Cancel departure'])
        self.assertEqual(items[0].actions[0].method, 'post')

    def test_expected_departure_is_dated_not_inbox(self):
        horse = self.horse('Leaving')
        Placement.objects.filter(horse=horse).update(expected_departure=self.today + timedelta(days=3))
        [item] = self.collect()
        self.assertEqual(item.kind, 'departure_expected')
        self.assertFalse(attention.is_inbox(item, self.today))
        self.assertEqual(item.due_date, self.today + timedelta(days=3))

    def test_invoice_balance_counts_payments(self):
        inv = self.invoice('INV1', '100.00', -10)
        Payment.objects.create(invoice=inv, date=self.today, amount=Decimal('40.00'), method='bank_transfer')
        paid = self.invoice('INV2', '50.00', -10)
        Payment.objects.create(invoice=paid, date=self.today, amount=Decimal('50.00'), method='cash')
        self.invoice('INV3', '20.00', 5)  # due next week: strip only
        self.invoice('INV4', '20.00', -5, status=Invoice.Status.DRAFT)
        items = [i for i in self.collect() if i.kind == 'invoice']
        self.assertEqual([(i.title, i.severity) for i in items], [('INV1', 'overdue'), ('INV3', 'due')])
        self.assertEqual(items[0].amount, Decimal('60.00'))
        self.assertIn('£60.00 of £100.00 outstanding', items[0].detail)
        self.assertEqual([a.label for a in items[0].actions], ['Record payment', 'Mark paid'])
        self.assertFalse(attention.is_inbox(items[1], self.today))

    def test_breeding_ehv_window_and_foal_due(self):
        mare = self.horse('Mummy', sex='mare')
        covered = self.today - timedelta(days=335)  # foal due in 5 days
        record = BreedingRecord.objects.create(
            mare=mare, stallion_name='Sire', date_covered=covered,
            status=BreedingRecord.Status.CONFIRMED,
        )
        items = self.collect()
        foal = [i for i in items if i.kind == 'foal']
        self.assertEqual(len(foal), 1)
        self.assertEqual(foal[0].due_date, record.date_foal_due)
        self.assertIn('Foal due', foal[0].detail)
        # EHV at month 9 is 275-ish days after covering: outside its window now.
        self.assertEqual([i for i in items if i.kind == 'ehv'], [])

        record.date_covered = self.today - timedelta(days=150)  # month 5 just now
        record.save()
        ehv = [i for i in self.collect() if i.kind == 'ehv']
        self.assertEqual(len(ehv), 1)
        self.assertIn('month 5', ehv[0].detail)


class HealthListsTests(DashboardDataTestCase):
    def test_health_page_windows(self):
        self.vaccination(self.horse('Overdue'), -3)
        self.vaccination(self.horse('Vax20'), 20)      # in: 30-day window
        self.farrier(self.horse('Farrier20'), 20)      # out: 14-day window
        self.farrier(self.horse('Farrier10'), 10)      # in
        action_required, coming_up = attention.health_lists(self.admin, today=self.today)
        self.assertEqual([e['horse'].name for e in action_required], ['Overdue'])
        self.assertEqual(action_required[0]['type'], 'Vaccination')
        self.assertEqual(action_required[0]['detail'], 'Flu')
        self.assertEqual(action_required[0]['action_label'], 'Re-vaccinate')
        self.assertEqual(sorted(e['horse'].name for e in coming_up), ['Farrier10', 'Vax20'])


class UpcomingTests(DashboardDataTestCase):
    def test_strip_and_visits(self):
        a = self.horse('Beech')
        b = self.horse('Cockey')
        self.farrier(a, 10)
        self.farrier(b, 10)
        self.vaccination(self.horse('Pridie'), 3)
        self.vaccination(self.horse('Old'), -4)
        items = self.collect()
        data = upcoming.build(items, self.admin, self.today)
        self.assertEqual(len(data['days']), 14)
        self.assertTrue(data['days'][0]['is_today'])
        self.assertEqual(data['days'][10]['count'], 2)
        self.assertEqual(data['days'][10]['dots'], ['farrier'])
        self.assertEqual(data['days'][3]['dots'], ['vaccination'])
        self.assertEqual(data['total'], 3)  # the overdue one is the inbox's business
        self.assertEqual([(v['kind'], len(v['horses'])) for v in data['visits']],
                         [('vaccination', 1), ('farrier', 2)])
        self.assertEqual(data['visits'][1]['action'].label, 'Record for 2')
        self.assertIsNotNone(data['visits'][0]['single_action'])
        self.assertEqual([e['kind'] for e in data['legend']], ['farrier', 'vaccination'])


class BoardTests(DashboardDataTestCase):
    def test_sites_tiles_rings_and_rest_days(self):
        self.horse('Beech')
        self.horse('Pridie')
        rested = Location.objects.create(name='The Banks', site='Somerford', usage=Location.Usage.RESTED)
        LocationUsagePeriod.objects.create(
            location=rested, usage=Location.Usage.RESTED,
            start_date=self.today - timedelta(days=10),
            source=LocationUsagePeriod.Source.MANUAL,
        )
        bands = board.sites_overview(today=self.today, flagged={('Somerford', 'Sandhills')})
        self.assertEqual([b['name'] for b in bands], ['Colgate', 'Somerford'])
        somerford = bands[1]
        self.assertEqual(somerford['horses'], 2)
        self.assertEqual(somerford['capacity'], 8)
        self.assertEqual(somerford['resting'], 1)
        self.assertEqual(somerford['flagged'], 1)
        tiles = {t['location'].name: t for t in somerford['tiles']}
        self.assertEqual(tiles['Sandhills']['count'], 2)
        self.assertEqual(tiles['Sandhills']['availability'], 6)
        self.assertEqual(tiles['Sandhills']['pct'], 25)
        self.assertTrue(tiles['Sandhills']['flagged'])
        # Days rested so far: clipped at today, never counting the future.
        self.assertEqual(tiles['The Banks']['rest_days'], 10)
        self.assertFalse(tiles['The Banks']['holds_horses'])

    def test_site_filter_and_archived_locations(self):
        Location.objects.create(name='Old Barn', site='Colgate', is_archived=True)
        bands = board.sites_overview(today=self.today, site='Colgate')
        self.assertEqual([b['name'] for b in bands], ['Colgate'])
        self.assertEqual([t['location'].name for t in bands[0]['tiles']], ['Bottom Barn'])
        self.assertEqual(board.site_names(), ['Colgate', 'Somerford'])


class MoneyTests(DashboardDataTestCase):
    def test_snapshot(self):
        inv = self.invoice('INV1', '100.00', -45)
        Payment.objects.create(invoice=inv, date=self.today - timedelta(days=3), amount=Decimal('40.00'), method='card')
        self.invoice('INV2', '80.00', 10)
        self.invoice('INV3', '30.00', -5, status=Invoice.Status.DRAFT)
        Invoice.objects.filter(invoice_number='INV2').update(send_error='SMTP timeout')
        data = money.snapshot(self.admin, today=self.today)
        inv_data = data['invoices']
        self.assertEqual(inv_data['drafts'], 1)
        self.assertEqual(inv_data['drafts_total'], Decimal('30.00'))
        self.assertEqual(inv_data['outstanding'], Decimal('140.00'))
        self.assertEqual(inv_data['overdue_total'], Decimal('60.00'))
        self.assertEqual(inv_data['overdue_count'], 1)
        self.assertEqual(inv_data['received_30d'], Decimal('40.00'))
        self.assertEqual(inv_data['send_errors'], 1)
        aged = {b['key']: b['amount'] for b in inv_data['aged']}
        self.assertEqual(aged['current'], Decimal('80.00'))
        self.assertEqual(aged['d60'], Decimal('60.00'))
        self.assertEqual(data['unbilled'], Decimal('0.00'))
        self.assertEqual(data['xero']['configured'], False)
        self.assertTrue(data['any'])

    def test_snapshot_respects_access(self):
        self.invoice('INV1', '100.00', -45)
        user = make_user_with_access('groom', dashboard='full', health='full')
        data = money.snapshot(user, today=self.today)
        self.assertIsNone(data['invoices'])
        self.assertIsNone(data['unbilled'])
        self.assertIsNone(data['xero'])
        self.assertFalse(data['any'])


class ActivityTests(DashboardDataTestCase):
    def test_recent_is_merged_by_date_not_per_type(self):
        from billing.models import FeedOut
        horse = self.horse('Busy')
        for n in range(25):
            Vaccination.objects.create(
                horse=horse, vaccination_type=self.flu,
                date_given=self.today - timedelta(days=n),
            )
        FeedOut.objects.create(
            location=self.somerford, date=self.today - timedelta(days=150),
            feed_type='hay', quantity='2 bales',
        )
        days = activity.recent(self.admin, today=self.today)
        texts = [e.text for d in days for e in d['events']]
        self.assertEqual(len(texts), activity.DEFAULT_LIMIT)
        self.assertFalse(any('Hay' in t for t in texts))
        self.assertEqual(days[0]['label'], 'Today')
        self.assertEqual(days[1]['label'], 'Yesterday')

    def test_move_merges_departure_and_arrival(self):
        from core.services import PlacementService
        horse = self.horse('Mover')
        PlacementService.move_horse(horse, new_location=self.colgate, move_date=self.today - timedelta(days=2))
        days = activity.recent(self.admin, today=self.today)
        texts = [e.text for d in days for e in d['events']]
        self.assertIn('Mover moved from Sandhills to Bottom Barn', texts)
        self.assertNotIn('Mover left Sandhills', texts)
        self.assertEqual(sum(1 for t in texts if t.startswith('Mover arrived')), 1)

    def test_site_filter_and_access(self):
        self.horse('Here', self.somerford)
        self.horse('There', self.colgate)
        days = activity.recent(self.admin, today=self.today, site='Colgate')
        texts = [e.text for d in days for e in d['events']]
        self.assertEqual(texts, ['There arrived at Bottom Barn'])
        finance_only = make_user_with_access('fin', dashboard='full', invoices='view')
        self.assertEqual(activity.recent(finance_only, today=self.today), [])


class BreedingBlockTests(DashboardDataTestCase):
    def test_in_foal_entries(self):
        mare = self.horse('Mummy', sex='mare')
        BreedingRecord.objects.create(
            mare=mare, stallion_name='Sire', date_covered=self.today - timedelta(days=170),
            status=BreedingRecord.Status.CONFIRMED,
        )
        BreedingRecord.objects.create(  # not confirmed: not in foal
            mare=self.horse('Maybe', sex='mare'), stallion_name='Sire',
            date_covered=self.today - timedelta(days=20),
        )
        entries = breeding.in_foal(self.admin, today=self.today)
        self.assertEqual([e['mare'].name for e in entries], ['Mummy'])
        entry = entries[0]
        self.assertEqual(entry['day_of'], 170)
        self.assertEqual(entry['progress'], 50)
        self.assertEqual(entry['days_to_go'], 170)
        self.assertEqual(entry['next_ehv']['month'], 7)
        no_breeding = make_user_with_access('nb', dashboard='full', health='full')
        self.assertEqual(breeding.in_foal(no_breeding, today=self.today), [])
