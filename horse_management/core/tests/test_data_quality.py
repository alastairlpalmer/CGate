"""PDF font, download file names, CSV byte-order mark, import encodings and
dry runs, and the database check constraints.
docs/LAUNCH_READINESS_PLAN.md, P1 work package 9."""

import tempfile
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.apps import apps as django_apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.csv_import import read_csv_text
from core.data_quality import refuse_if_violated, report
from core.downloads import attachment_header, safe_filename
from core.models import BusinessSettings, Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin
from invoicing import pdf
from invoicing.models import Invoice, InvoiceLineItem, Payment


def _invoice(owner, number='UNI-1'):
    return Invoice.objects.create(
        owner=owner, invoice_number=number, period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        subtotal=Decimal('10'), total=Decimal('10'), status=Invoice.Status.SENT,
    )


class PdfFontTests(TestCase):
    def test_letters_helvetica_lacks_are_drawable(self):
        self.assertEqual(pdf._drawable('Siobhán Łucja Ørsted Жанна €£'), 'Siobhán Łucja Ørsted Жанна €£')

    def test_characters_the_font_lacks_become_a_question_mark(self):
        self.assertEqual(pdf._drawable('Horse 中'), 'Horse ?')
        self.assertEqual(pdf._esc('A & B 中'), 'A &amp; B ?')

    def test_styles_use_the_unicode_font(self):
        styles = pdf._styles()
        self.assertEqual(styles['Normal'].fontName, pdf.FONT)
        self.assertEqual(styles['Heading1'].fontName, pdf.FONT_BOLD)

    def test_invoice_pdf_embeds_the_font(self):
        BusinessSettings.get_settings()
        owner = Owner.objects.create(name='Łucja Wójcik', address='ul. Żółta 5\nKraków')
        invoice = _invoice(owner)
        InvoiceLineItem.objects.create(
            invoice=invoice, line_type=InvoiceLineItem.LineType.LIVERY, description='Livery – März',
            quantity=Decimal('1'), unit_price=Decimal('10'),
        )
        data = pdf.generate_invoice_pdf(invoice).read()
        self.assertTrue(data.startswith(b'%PDF'))
        self.assertIn(b'DejaVuSans', data)


class DownloadNameTests(SimpleTestCase):
    def test_plain_names_stay_simple(self):
        self.assertEqual(attachment_header('INV00012.pdf'), 'attachment; filename="INV00012.pdf"')

    def test_accents_and_quotes(self):
        header = attachment_header("Statement - Siobhán O'Brien - 2026-10-20.pdf")
        self.assertIn('filename="Statement - Siobhan O-Brien - 2026-10-20.pdf"', header)
        self.assertIn("filename*=UTF-8''Statement%20-%20Siobh%C3%A1n%20O%27Brien%20-%202026-10-20.pdf", header)

    def test_header_breaking_characters_are_removed(self):
        self.assertEqual(safe_filename('a"b;c/d\\e\r\nf.pdf'), 'a-b-c-d-e-f.pdf')
        self.assertNotIn('"x', attachment_header('evil"; filename="x.exe'))
        self.assertEqual(safe_filename('...'), 'download')

    def test_inline(self):
        self.assertTrue(attachment_header('a.pdf', inline=True).startswith('inline;'))


class DownloadViewTests(TestCase):
    def setUp(self):
        BusinessSettings.get_settings()
        self.client.force_login(make_admin('uniadmin'))
        self.owner = Owner.objects.create(name="Siobhán O'Brien", account_code='SOB')

    def test_statement_file_name_keeps_the_owner_name(self):
        response = self.client.get(reverse('owner_statement_pdf', args=[self.owner.pk]))
        self.assertIn("filename*=UTF-8''Statement%20-%20Siobh%C3%A1n%20O%27Brien", response['Content-Disposition'])

    def test_xero_csv_has_a_byte_order_mark(self):
        invoice = _invoice(self.owner)
        InvoiceLineItem.objects.create(
            invoice=invoice, line_type=InvoiceLineItem.LineType.LIVERY, description='Livery',
            quantity=Decimal('1'), unit_price=Decimal('10'),
        )
        response = self.client.get(reverse('invoice_csv', args=[invoice.pk]))
        self.assertTrue(response.content.startswith('﻿'.encode()))
        self.assertIn('charset=utf-8', response['Content-Type'])
        self.assertIn('Siobhán', response.content.decode('utf-8-sig'))


class CsvEncodingTests(SimpleTestCase):
    def _file(self, data):
        path = Path(tempfile.mkdtemp()) / 'horses.csv'
        path.write_bytes(data)
        return path

    def test_utf8_with_and_without_bom(self):
        self.assertEqual(read_csv_text(self._file('Name\nSiobhán\n'.encode())), ('Name\nSiobhán\n', 'utf-8-sig'))
        self.assertEqual(read_csv_text(self._file('﻿Name\n'.encode()))[0], 'Name\n')

    def test_excel_windows_file_reads_as_cp1252(self):
        text, encoding = read_csv_text(self._file('Rate\n£5 “grass”\n'.encode('cp1252')))
        self.assertEqual(encoding, 'cp1252')
        self.assertIn('£5 “grass”', text)

    def test_bytes_cp1252_lacks_fall_back_to_latin1(self):
        self.assertEqual(read_csv_text(self._file(b'Name\n\x81\xe9\n'))[1], 'latin-1')

    def test_old_importer_uses_the_same_order(self):
        from core.management.commands.load_csv_data import Command

        command = Command(stdout=StringIO())
        rows = command._read_csv(str(self._file('Horse,Rate\nBramble,£5\n'.encode('cp1252'))))
        self.assertEqual(rows, [{'Horse': 'Bramble', 'Rate': '£5'}])


class ImportDryRunTests(TestCase):
    def test_dry_run_saves_nothing(self):
        from health.models import VaccinationType

        before = VaccinationType.objects.count()
        call_command('import_data', '--dry-run', stdout=StringIO())
        self.assertEqual(VaccinationType.objects.count(), before)
        call_command('import_data', stdout=StringIO())
        self.assertGreater(VaccinationType.objects.count(), before)


class CheckConstraintTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name='Rule Owner')
        self.horse = Horse.objects.create(name='Rule Horse')

    def _refused(self, create):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create()

    def test_payment_must_be_positive(self):
        invoice = _invoice(self.owner)
        self._refused(lambda: Payment.objects.create(invoice=invoice, date=date.today(), amount=Decimal('0')))

    def test_invoice_period_order(self):
        self._refused(lambda: Invoice.objects.create(
            owner=self.owner, invoice_number='BAD-1', period_start=date(2026, 3, 31), period_end=date(2026, 3, 1),
            subtotal=Decimal('0'), total=Decimal('0'),
        ))

    def test_placement_cannot_end_before_it_starts(self):
        # Placement.save() checks this too, so go round it as a script would.
        location = Location.objects.create(name='Paddock 1', site='Rule Site')
        rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5'))
        placement = Placement.objects.create(
            horse=self.horse, owner=self.owner, location=location, rate_type=rate, start_date=date(2026, 3, 10),
        )
        self._refused(lambda: Placement.objects.filter(pk=placement.pk).update(end_date=date(2026, 3, 1)))

    def test_rate_and_share_ranges(self):
        self._refused(lambda: RateType.objects.create(name='Negative', daily_rate=Decimal('-1')))
        share = OwnershipShare.objects.create(horse=self.horse, owner=self.owner, share_percentage=Decimal('50'))
        self._refused(lambda: OwnershipShare.objects.filter(pk=share.pk).update(share_percentage=Decimal('150')))
        self._refused(lambda: OwnershipShare.objects.filter(pk=share.pk).update(share_percentage=Decimal('0')))

    def test_charge_amount_not_negative(self):
        from billing.models import ExtraCharge

        self._refused(lambda: ExtraCharge.objects.create(
            horse=self.horse, owner=self.owner, charge_type='vet', date=date.today(),
            description='Vet', amount=Decimal('-5'),
        ))


class DataQualityReportTests(TestCase):
    def test_command_passes_on_clean_data(self):
        out = StringIO()
        call_command('check_data_quality', stdout=out)
        self.assertIn('Every row follows the data rules', out.getvalue())

    def test_report_names_the_rows(self):
        owner = Owner.objects.create(name='Report Owner')
        invoice = _invoice(owner)
        payment = Payment.objects.create(invoice=invoice, date=date.today(), amount=Decimal('5'))
        lines = report([(Payment, 'example_rule', Q(amount__gt=100))])
        self.assertEqual(lines, [f'invoicing.Payment (example_rule): 1 row(s), ids {payment.pk}'])

    def test_migration_step_refuses_with_the_list(self):
        owner = Owner.objects.create(name='Migration Owner')
        _invoice(owner)
        with self.assertRaisesMessage(RuntimeError, 'invoicing.Invoice (example_rule): 1 row(s)'):
            refuse_if_violated(django_apps, [('invoicing', 'invoice', 'example_rule', Q(total__gt=100))])

    def test_command_fails_when_a_rule_is_broken(self):
        from unittest import mock

        with mock.patch('core.management.commands.check_data_quality.report', return_value=['x: 1 row(s)']):
            with self.assertRaises(CommandError):
                call_command('check_data_quality', stdout=StringIO())
