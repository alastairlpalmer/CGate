"""The invoice list's preview pane, its sheet on a phone, and the PDF it
shows.

Picking an invoice on the list loads invoicing/partials/pane.html into the
pane beside it (invoicing.views.invoice_pane) instead of navigating, as
the horse and owner lists do. The pane has three views: the invoice, the
owner it bills, and the PDF in a frame.

Covered here: that only the pane's own request gets the partial, what each
view says, that the Owner view follows access to Owners, that the PDF can
be framed by this site and by nothing else, that a send from the pane comes
back to the list, and that the list carries the hooks
static/js/split_view.js needs.
"""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from invoicing.models import Invoice, Payment
from invoicing.services import InvoiceService

AUGUST = (date(2026, 8, 1), date(2026, 8, 31))
PANE = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'invoice-preview-body'}
BOOSTED = {
    'HTTP_HX_REQUEST': 'true',
    'HTTP_HX_BOOSTED': 'true',
    'HTTP_HX_TARGET': 'main-content',
}


class InvoicePaneFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(
            name='Roz Tobin', email='roz@example.com', phone='07700 900456',
            address='2 Farm Road\nCrawley',
        )
        location = Location.objects.create(site='Colgate', name='Top Field')
        rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))
        cls.horse = Horse.objects.create(name='Ida')
        OwnershipShare.objects.create(
            horse=cls.horse, owner=cls.owner, share_percentage=Decimal('100'),
            is_primary_contact=True,
        )
        Placement.objects.create(
            horse=cls.horse, owner=cls.owner, location=location,
            rate_type=rate, start_date=date(2026, 7, 23),
        )
        ExtraCharge.objects.create(
            horse=cls.horse, owner=cls.owner, charge_type='vet',
            date=date(2026, 8, 10), description='Checkup',
            amount=Decimal('50.00'), split_by_ownership=False,
        )
        # 31 days at £5.00 and the £50.00 checkup: £205.00.
        cls.invoice = InvoiceService.create_invoice(cls.owner, *AUGUST)
        cls.url = reverse('invoice_pane', args=[cls.invoice.pk])

    def pane(self, **headers):
        return self.client.get(self.url, **{**PANE, **headers}).content.decode()


class InvoicePaneAccessTests(InvoicePaneFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def test_only_the_pane_gets_the_partial(self):
        page = reverse('invoice_detail', args=[self.invoice.pk])
        self.assertRedirects(self.client.get(self.url), page)
        self.assertRedirects(self.client.get(self.url, **BOOSTED), page)

    def test_the_pane_request_renders_the_partial(self):
        response = self.client.get(self.url, **PANE)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'invoicing/partials/pane.html')
        self.assertNotIn(b'<html', response.content)

    def test_the_partial_carries_the_id_the_swap_selects_on(self):
        self.assertIn('id="invoice-preview-content"', self.pane())

    def test_a_missing_invoice_is_a_404(self):
        response = self.client.get(reverse('invoice_pane', args=[99999]), **PANE)
        self.assertEqual(response.status_code, 404)

    def test_a_role_without_invoices_cannot_open_it(self):
        self.client.force_login(make_user_with_access('yard', owners='view', horses='view'))
        self.assertNotEqual(self.client.get(self.url, **PANE).status_code, 200)


class InvoicePaneTabsTests(InvoicePaneFixture):
    def setUp(self):
        self.client.force_login(make_admin())
        self.body = self.pane()

    def test_it_has_the_three_views(self):
        for tab in ('invoice', 'owner', 'pdf'):
            self.assertIn(f'data-pane-tab="{tab}"', self.body)
            self.assertIn(f'data-pane-view="{tab}"', self.body)
        self.assertIn("paneTabs('invoice', ['invoice', 'owner', 'pdf'])", self.body)

    def test_the_tabs_and_views_carry_no_id(self):
        """After a swap htmx puts back the attributes of every element
        whose id was in the old content too. That undid the class and the
        display Alpine had just set: the rail showed one tab, the pane
        another. Only the root, which the swap selects on, has an id."""
        self.assertEqual(self.body.count(' id="'), 1)

    def test_a_row_s_owner_link_can_switch_the_view(self):
        self.assertIn('@split-tab.window="show($event.detail)"', self.body)


class InvoiceViewTests(InvoicePaneFixture):
    def setUp(self):
        self.client.force_login(make_admin())

    def test_it_names_the_invoice_its_owner_and_its_status(self):
        body = self.pane()
        self.assertIn(f'Invoice {self.invoice.invoice_number}', body)
        self.assertIn('Roz Tobin', body)
        self.assertIn('Draft', body)

    def test_it_lists_the_lines_under_their_horse_with_the_totals(self):
        body = self.pane()
        self.assertIn('Ida', body)
        self.assertIn('Checkup', body)
        self.assertIn('£50.00', body)
        self.assertIn('£205.00', body)
        self.assertIn('Net total', body)

    def test_a_draft_offers_send_and_comes_back_to_the_list(self):
        body = self.pane()
        self.assertIn(reverse('invoice_send', args=[self.invoice.pk]), body)
        self.assertIn(f'name="next" value="{reverse("invoice_list")}"', body)

    def test_send_comes_back_to_the_filtered_list_it_was_opened_from(self):
        current = 'http://testserver' + reverse('invoice_list') + '?status=draft&page=2'
        body = self.pane(HTTP_HX_CURRENT_URL=current)
        self.assertIn('name="next" value="/invoicing/?status=draft&amp;page=2"', body)

    def test_a_current_url_on_another_site_or_page_is_not_used(self):
        for current in ('https://evil.test/invoicing/?x=1', 'http://testserver/owners/'):
            body = self.pane(HTTP_HX_CURRENT_URL=current)
            self.assertIn(f'name="next" value="{reverse("invoice_list")}"', body)

    def test_a_sent_invoice_offers_record_payment_and_shows_what_is_paid(self):
        self.invoice.mark_as_sent()
        Payment.objects.create(
            invoice=self.invoice, amount=Decimal('55.00'), date=date(2026, 9, 2),
            method=Payment.Method.BANK_TRANSFER, reference='BACS 0209',
        )
        body = self.pane()
        self.assertIn(reverse('payment_create', args=[self.invoice.pk]), body)
        self.assertNotIn(reverse('invoice_send', args=[self.invoice.pk]), body)
        self.assertIn('£55.00', body)
        self.assertIn('BACS 0209', body)
        self.assertIn('£150.00', body)      # balance due
        self.assertIn('Balance due', body)

    def test_a_view_only_role_gets_no_actions_that_change_the_invoice(self):
        self.client.force_login(make_user_with_access('reader', invoices='view'))
        body = self.pane()
        self.assertIn(f'Invoice {self.invoice.invoice_number}', body)
        for url in ('invoice_send', 'invoice_update', 'payment_create'):
            self.assertNotIn(reverse(url, args=[self.invoice.pk]), body)
        self.assertIn(reverse('invoice_pdf', args=[self.invoice.pk]), body)


class OwnerViewTests(InvoicePaneFixture):
    def test_with_owners_access_it_is_the_owner_s_record(self):
        self.client.force_login(make_admin())
        body = self.pane()
        owner_view = body.split('data-pane-view="owner"')[1].split('data-pane-view="pdf"')[0]
        for text in ('Roz Tobin', 'roz@example.com', '07700 900456', '2 Farm Road',
                     'Ida', 'Open owner page', self.invoice.invoice_number):
            self.assertIn(text, owner_view)
        self.assertIn(reverse('owner_detail', args=[self.owner.pk]), owner_view)

    def test_the_owner_s_invoices_do_not_replace_the_invoice_s_own_parts(self):
        """Both views are in one response, and both have things called
        invoices and payments."""
        self.client.force_login(make_admin())
        response = self.client.get(self.url, **PANE)
        self.assertEqual(response.context['invoice'], self.invoice)
        self.assertEqual(list(response.context['owner_invoices']), [self.invoice])

    def test_without_owners_access_it_says_only_what_the_invoice_says(self):
        self.client.force_login(make_user_with_access('books', invoices='view'))
        body = self.pane()
        owner_view = body.split('data-pane-view="owner"')[1].split('data-pane-view="pdf"')[0]
        self.assertIn('Roz Tobin', owner_view)
        self.assertIn('2 Farm Road', owner_view)      # on the invoice itself
        self.assertNotIn('07700 900456', owner_view)  # the owner's record only
        self.assertNotIn(reverse('owner_detail', args=[self.owner.pk]), owner_view)


class PdfViewTests(InvoicePaneFixture):
    def setUp(self):
        self.client.force_login(make_admin())
        self.pdf_url = reverse('invoice_pdf', args=[self.invoice.pk])

    def test_the_pane_frames_the_inline_pdf_only_when_the_tab_is_open(self):
        body = self.pane()
        self.assertIn(f'src="{self.pdf_url}?inline=1', body)
        # Inside a <template x-if>: no PDF is built until the tab is picked.
        frame_at = body.index('<iframe')
        self.assertIn("<template x-if=\"tab === 'pdf'\">", body[:frame_at])
        self.assertLess(body.rindex('<template', 0, frame_at), frame_at)

    def test_inline_is_the_same_file_for_the_browser_to_show(self):
        response = self.client.get(self.pdf_url + '?inline=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response['Content-Disposition'].startswith('inline;'))
        self.assertTrue(response.content.startswith(b'%PDF'))
        self.assertIn('no-store', response['Cache-Control'])

    def test_inline_can_be_framed_by_this_site(self):
        response = self.client.get(self.pdf_url + '?inline=1')
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        policy = response['Content-Security-Policy-Report-Only']
        self.assertIn("frame-ancestors 'self'", policy)

    def test_the_download_is_still_an_attachment_nothing_can_frame(self):
        response = self.client.get(self.pdf_url)
        self.assertTrue(response['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(response['X-Frame-Options'], 'DENY')
        self.assertIn("frame-ancestors 'none'", response['Content-Security-Policy-Report-Only'])

    def test_every_other_page_still_refuses_to_be_framed(self):
        response = self.client.get(reverse('invoice_list'))
        self.assertEqual(response['X-Frame-Options'], 'DENY')
        policy = response['Content-Security-Policy-Report-Only']
        self.assertIn("frame-ancestors 'none'", policy)
        # The page may frame this site (the PDF) and no other.
        self.assertIn("frame-src 'self'", policy)

    def test_the_enforced_policy_gets_the_same_allowance(self):
        from django.conf import settings

        with override_settings(
            CONTENT_SECURITY_POLICY=settings.CONTENT_SECURITY_POLICY_REPORT_ONLY,
            CONTENT_SECURITY_POLICY_REPORT_ONLY=None,
        ):
            response = self.client.get(self.pdf_url + '?inline=1')
            self.assertIn("frame-ancestors 'self'", response['Content-Security-Policy'])

    def test_a_role_without_invoices_cannot_fetch_it(self):
        self.client.force_login(make_user_with_access('yard', owners='view'))
        self.assertNotEqual(self.client.get(self.pdf_url + '?inline=1').status_code, 200)


class SendFromThePaneTests(InvoicePaneFixture):
    def setUp(self):
        self.client.force_login(make_admin())
        self.send_url = reverse('invoice_send', args=[self.invoice.pk])

    def _send(self, **data):
        with mock.patch('invoicing.views.send_invoice_email_task', return_value='sent', create=True), \
             mock.patch('invoicing.tasks.send_invoice_email_task', return_value='sent'):
            return self.client.post(self.send_url, data)

    def test_with_a_next_it_comes_back_to_the_list(self):
        response = self._send(next='/invoicing/?status=draft')
        self.assertRedirects(response, '/invoicing/?status=draft', fetch_redirect_response=False)

    def test_without_a_next_it_still_lands_on_the_invoice(self):
        response = self._send()
        self.assertRedirects(
            response, reverse('invoice_detail', args=[self.invoice.pk]),
            fetch_redirect_response=False,
        )

    def test_an_off_site_next_is_refused(self):
        response = self._send(next='https://evil.test/')
        self.assertRedirects(
            response, reverse('invoice_detail', args=[self.invoice.pk]),
            fetch_redirect_response=False,
        )

    def test_a_refused_send_also_comes_back_to_the_list(self):
        Owner.objects.filter(pk=self.owner.pk).update(email='')
        response = self.client.post(self.send_url, {'next': '/invoicing/'})
        self.assertRedirects(response, '/invoicing/', fetch_redirect_response=False)


class InvoiceListSplitViewTests(InvoicePaneFixture):
    def setUp(self):
        self.client.force_login(make_admin())
        self.body = self.client.get(reverse('invoice_list')).content.decode()

    def test_the_list_carries_the_pane_and_its_url_template(self):
        self.assertIn('data-split-view', self.body)
        self.assertIn('data-split-pane="invoice-preview"', self.body)
        self.assertIn('id="invoice-preview-body"', self.body)
        self.assertIn(f'data-preview-url="{reverse("invoice_pane", args=[0])}"', self.body)

    def test_every_row_names_its_invoice_and_keeps_its_link(self):
        self.assertIn(f'data-split-pk="{self.invoice.pk}"', self.body)
        self.assertIn('data-split-link', self.body)
        self.assertIn(reverse('invoice_detail', args=[self.invoice.pk]), self.body)

    def test_the_owner_link_opens_the_owner_view_and_is_still_a_link(self):
        owner_page = reverse('owner_detail', args=[self.owner.pk])
        self.assertIn(f'<a href="{owner_page}" class="link tap-pad" data-split-tab="owner">', self.body)

    def test_the_pane_is_hidden_until_an_invoice_is_picked(self):
        self.assertIn('<aside id="invoice-preview" class="split-view-pane" hidden', self.body)

    def test_the_pane_can_be_resized(self):
        self.assertIn('data-resize-host="invoice-list"', self.body)
        self.assertIn('data-resize-panel="#invoice-preview"', self.body)
        self.assertIn('data-resize-bar', self.body)

    def test_the_columns_the_pane_repeats_are_marked(self):
        self.assertEqual(self.body.count('<th class="split-optional">'), 2)
        self.assertIn('<th class="whitespace-nowrap split-optional">Due date</th>', self.body)

    def test_the_bulk_tick_boxes_are_still_there(self):
        self.assertIn('data-invoice-cb', self.body)


class InvoicePageBackLinkTests(InvoicePaneFixture):
    def test_the_invoice_page_links_back_to_the_list(self):
        self.client.force_login(make_admin())
        body = self.client.get(reverse('invoice_detail', args=[self.invoice.pk])).content.decode()
        self.assertIn(f'<a href="{reverse("invoice_list")}"', body)
        self.assertIn('All Invoices', body)
