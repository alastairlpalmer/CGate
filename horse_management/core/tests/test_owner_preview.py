"""The owner list's preview pane, its sheet on a phone, and the way back
from an owner's page.

Picking an owner on the list loads owners/partials/preview.html into the
pane beside it (core.views.owners.owner_preview) instead of navigating,
as the horse list does (core/tests/test_horse_preview.py). The same
partial is the body of the bottom sheet on a narrow screen.

Covered here: that only the pane's own request gets the partial, what the
pane says, that money follows the role, that the list carries the hooks
static/js/split_view.js needs, and that an owner's page has a link back
to the list.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import Document, Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from invoicing.models import Invoice

PANE = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'owner-preview-body'}
BOOSTED = {
    'HTTP_HX_REQUEST': 'true',
    'HTTP_HX_BOOSTED': 'true',
    'HTTP_HX_TARGET': 'main-content',
}


class OwnerPaneFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.owner = Owner.objects.create(
            name='Milly Hine', email='milly@example.com', phone='07700 900123',
            address='1 Mill Lane\nHorsham', notes='Pays by BACS.',
        )
        cls.location = Location.objects.create(name='Grain store paddock', site='Colgate Farm')
        cls.rate = RateType.objects.create(name='Grass Livery', daily_rate=Decimal('5.50'))
        cls.horse = Horse.objects.create(
            name='Antoinette', color=Horse.Color.BAY, sex=Horse.Sex.MARE,
        )
        OwnershipShare.objects.create(
            horse=cls.horse, owner=cls.owner, share_percentage=Decimal('100'),
            is_primary_contact=True,
        )
        Placement.objects.create(
            horse=cls.horse, owner=cls.owner, location=cls.location,
            rate_type=cls.rate, start_date=cls.today - timedelta(days=40),
        )
        cls.charge = ExtraCharge.objects.create(
            horse=cls.horse, owner=cls.owner,
            charge_type=ExtraCharge.ChargeType.VET, date=cls.today - timedelta(days=3),
            description='Dental check', amount=Decimal('64.00'),
            split_by_ownership=False,
        )
        cls.invoice = Invoice.objects.create(
            owner=cls.owner, invoice_number='INV00042',
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
            due_date=cls.today + timedelta(days=10),
            subtotal=Decimal('170.50'), total=Decimal('170.50'),
            status=Invoice.Status.SENT,
        )
        cls.url = reverse('owner_preview', args=[cls.owner.pk])

    def pane(self, owner=None):
        url = reverse('owner_preview', args=[(owner or self.owner).pk])
        return self.client.get(url, **PANE).content.decode()


class OwnerPaneAccessTests(OwnerPaneFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def test_only_the_pane_gets_the_partial(self):
        """A direct visit, or a boosted navigation, lands on the owner's
        own page, so every row on the list is still an ordinary link."""
        page = reverse('owner_detail', args=[self.owner.pk])
        self.assertRedirects(self.client.get(self.url), page)
        self.assertRedirects(self.client.get(self.url, **BOOSTED), page)

    def test_the_pane_request_renders_the_partial(self):
        response = self.client.get(self.url, **PANE)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'owners/partials/preview.html')
        self.assertNotIn(b'<html', response.content)

    def test_the_partial_carries_the_id_the_swap_selects_on(self):
        self.assertIn('id="owner-preview-content"', self.pane())

    def test_a_missing_owner_is_a_404(self):
        response = self.client.get(reverse('owner_preview', args=[99999]), **PANE)
        self.assertEqual(response.status_code, 404)

    def test_a_role_without_owners_cannot_open_it(self):
        self.client.force_login(make_user_with_access('noowners', owners='hidden', horses='view'))
        self.assertNotEqual(self.client.get(self.url, **PANE).status_code, 200)


class OwnerPaneContentTests(OwnerPaneFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def test_it_names_the_owner_and_how_to_reach_them(self):
        body = self.pane()
        for text in ('Milly Hine', 'milly@example.com', '07700 900123', '1 Mill Lane', 'Pays by BACS.'):
            self.assertIn(text, body)

    def test_an_owner_with_no_contact_details_says_so(self):
        bare = Owner.objects.create(name='Nigel Noemail')
        self.assertIn('No contact details recorded.', self.pane(bare))

    def test_it_lists_the_horses_on_the_yard_and_where_they_are(self):
        body = self.pane()
        self.assertIn('Antoinette', body)
        self.assertIn('Grain store paddock', body)
        self.assertIn(reverse('horse_detail', args=[self.horse.pk]), body)

    def test_a_departed_horse_is_counted_not_listed(self):
        gone = Horse.objects.create(name='Gata', is_active=False)
        OwnershipShare.objects.create(
            horse=gone, owner=self.owner, share_percentage=Decimal('100'),
        )
        body = self.pane()
        self.assertIn('1 departed horse', body)
        self.assertNotIn(reverse('horse_detail', args=[gone.pk]), body)

    def test_a_horse_with_no_colour_has_no_stray_separator(self):
        plain = Horse.objects.create(name='Plain', sex=Horse.Sex.GELDING)
        OwnershipShare.objects.create(
            horse=plain, owner=self.owner, share_percentage=Decimal('100'),
        )
        self.assertNotIn('>· ', self.pane())

    def test_it_shows_what_is_owed_and_what_is_unbilled(self):
        body = self.pane()
        self.assertIn('£170.50', body)     # outstanding: the sent invoice
        self.assertIn('£64.00', body)      # unbilled: the dental check
        self.assertIn('Dental check', body)
        self.assertIn('INV00042', body)

    def test_an_overdue_invoice_is_flagged(self):
        Invoice.objects.filter(pk=self.invoice.pk).update(
            status=Invoice.Status.OVERDUE, due_date=self.today - timedelta(days=5),
        )
        self.assertIn('Some is overdue', self.pane())

    def test_documents_are_listed_and_an_empty_shelf_says_so(self):
        self.assertIn('Nothing on file.', self.pane())
        Document.objects.create(
            owner=self.owner, doc_type=Document.DocType.OTHER,
            title='Livery agreement', file='documents/2026/09/agreement.pdf',
        )
        self.assertIn('Livery agreement', self.pane())

    def test_an_archived_owner_is_marked_and_cannot_be_invoiced(self):
        Owner.objects.filter(pk=self.owner.pk).update(is_archived=True)
        body = self.pane()
        self.assertIn('Archived', body)
        self.assertNotIn('Create invoice', body)

    def test_no_photo_or_avatar(self):
        self.assertNotIn('<img', self.pane())


class OwnerPaneHorseRowsTests(OwnerPaneFixture):
    """The horses section: one short row each (name, age, colour, sex,
    location), and over 6 horses a scroll box with a search bar."""

    def setUp(self):
        self.client.force_login(make_admin())

    def add_horses(self, count):
        for i in range(count):
            horse = Horse.objects.create(name=f'Extra {i}')
            OwnershipShare.objects.create(horse=horse, owner=self.owner, share_percentage=Decimal('100'))

    def test_a_row_has_the_columns(self):
        self.horse.date_of_birth = self.today.replace(year=self.today.year - 8) - timedelta(days=1)
        self.horse.save()
        body = self.pane()
        self.assertIn('data-horse-name="antoinette"', body)
        row = body[body.index('data-horse-name="antoinette"'):][:2500]
        for text in ('8yo', 'Bay', 'Mare', 'Grain store paddock'):
            self.assertIn(text, row)

    def test_every_section_is_boxed(self):
        self.assertEqual(self.pane().count('pane-section is-boxed'), 5)

    def test_six_horses_have_no_search_and_no_scroll_box(self):
        self.add_horses(5)
        body = self.pane()
        self.assertNotIn('data-horse-search', body)
        self.assertNotIn('pane-scroll', body)
        self.assertNotIn('x-data="paneFilter"', body)

    def test_seven_horses_get_the_search_and_the_scroll_box(self):
        self.add_horses(6)
        body = self.pane()
        self.assertIn('data-horse-search', body)
        self.assertIn('placeholder="Search 7 horses"', body)
        self.assertIn('class="pane-scroll"', body)
        self.assertIn('x-data="paneFilter"', body)
        self.assertEqual(body.count('x-show="match($el.dataset.horseName)"'), 7)

    def test_the_filter_is_registered_before_alpine_starts(self):
        response = self.client.get(reverse('owner_list'))
        self.assertContains(response, "Alpine.data('paneFilter'")


class OwnerPaneMoneyTests(OwnerPaneFixture):
    """Money follows the role, here as on the owner's page."""

    def test_without_invoices_or_charges_access_no_money_shows(self):
        self.client.force_login(make_user_with_access('yard', owners='view', horses='view'))
        body = self.pane()
        self.assertIn('Milly Hine', body)
        for hidden in ('£170.50', '£64.00', 'INV00042', 'Dental check',
                       'Outstanding</dt>', 'Unbilled</dt>', 'Create invoice', 'Statement'):
            self.assertNotIn(hidden, body)

    def test_charges_access_alone_shows_only_the_unbilled_charges(self):
        self.client.force_login(make_user_with_access('feed', owners='view', charges='view'))
        body = self.pane()
        self.assertIn('£64.00', body)
        self.assertNotIn('£170.50', body)
        self.assertNotIn('INV00042', body)

    def test_a_view_only_role_gets_no_edit_button(self):
        self.client.force_login(make_user_with_access('reader', owners='view'))
        self.assertNotIn(reverse('owner_update', args=[self.owner.pk]), self.pane())


class OwnerListSplitViewTests(OwnerPaneFixture):
    def setUp(self):
        self.client.force_login(make_admin())
        self.body = self.client.get(reverse('owner_list')).content.decode()

    def test_the_list_carries_the_pane_and_its_url_template(self):
        self.assertIn('data-split-view', self.body)
        self.assertIn('data-split-pane="owner-preview"', self.body)
        self.assertIn('id="owner-preview-body"', self.body)
        # A 0 stands in for the owner's key; split_view.js swaps it.
        self.assertIn(f'data-preview-url="{reverse("owner_preview", args=[0])}"', self.body)

    def test_the_pane_has_the_parts_the_script_finds_by_id(self):
        for part in ('counter', 'prev', 'next', 'open-full'):
            self.assertIn(f'id="owner-preview-{part}"', self.body)

    def test_every_row_names_its_owner_and_keeps_its_link(self):
        self.assertIn(f'data-split-pk="{self.owner.pk}"', self.body)
        self.assertIn('data-split-link', self.body)
        self.assertIn(reverse('owner_detail', args=[self.owner.pk]), self.body)

    def test_the_pane_is_hidden_until_an_owner_is_picked(self):
        self.assertIn('<aside id="owner-preview" class="split-view-pane" hidden', self.body)

    def test_the_pane_can_be_resized(self):
        self.assertIn('data-resize-host="owner-list"', self.body)
        self.assertIn('data-resize-panel="#owner-preview"', self.body)
        self.assertIn('data-resize-bar', self.body)

    def test_the_columns_the_pane_repeats_are_marked(self):
        self.assertIn('split-optional', self.body)

    def test_links_inside_the_pane_navigate_as_the_rest_of_the_page_does(self):
        """The pane turns hx-select and hx-push-url off for its own
        request. Its body turns them back on, or a link in the record put
        the next page inside this one and left the address bar alone."""
        for page in (self.body, self.client.get(reverse('horse_list')).content.decode()):
            body_tag = page.split('class="split-view-body"')[1].split('>')[0]
            self.assertIn('hx-select="#main-content"', body_tag)
            self.assertIn('hx-push-url="true"', body_tag)

    def test_the_phone_card_s_contact_links_do_not_cover_the_name(self):
        """tap-pad grew the email link upward over the owner's name."""
        card = self.body.split('class="list-card"')[1].split('</li>')[0]
        self.assertIn('mailto:milly@example.com', card)
        self.assertNotIn('tap-pad', card)


class OwnerPageBackLinkTests(OwnerPaneFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def test_the_owner_page_links_back_to_the_list(self):
        body = self.client.get(reverse('owner_detail', args=[self.owner.pk])).content.decode()
        self.assertIn(f'<a href="{reverse("owner_list")}"', body)
        self.assertIn('All Owners', body)

    def test_an_archived_owner_goes_back_to_the_archived_list(self):
        Owner.objects.filter(pk=self.owner.pk).update(is_archived=True)
        body = self.client.get(reverse('owner_detail', args=[self.owner.pk])).content.decode()
        self.assertIn(f'<a href="{reverse("owner_list")}?archived=1"', body)
        self.assertIn('Archived Owners', body)
