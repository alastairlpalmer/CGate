"""Final probes. python manage.py shell < verify_final.py"""
import io
from datetime import date
from core.models import Owner
from invoicing.services import InvoiceService
from invoicing.models import Invoice
from invoicing.utils import write_xero_csv

start, end = date(2026,6,1), date(2026,6,30)

print("=== TEST F: manual create_invoice for owner with ZERO activity ===")
emma = Owner.objects.get(name="Emma Evans")
before = Invoice.objects.count()
try:
    inv = InvoiceService.create_invoice(emma, date(2026,3,1), date(2026,3,31))
    print(f"  Created invoice {inv.invoice_number} total=£{inv.total} line_items={inv.line_items.count()}")
    print("  => Empty £0 invoice created and invoice number CONSUMED (manual path skips zero-total check)")
except Exception as e:
    print("  Blocked:", e)

print("\n=== TEST G: unbilled-charge KPI over-counts partially billed split charge ===")
from billing.models import ExtraCharge
from django.db.models import Sum
far = ExtraCharge.objects.filter(charge_type='farrier').first()
from invoicing.models import InvoiceLineItem
billed_portion = InvoiceLineItem.objects.filter(charge=far).aggregate(s=Sum('line_total'))['s']
kpi = ExtraCharge.objects.filter(invoiced=False).aggregate(s=Sum('amount'))['s']
print(f"  Farrier charge £{far.amount}, invoiced flag={far.invoiced}, already-billed via line items=£{billed_portion}")
print(f"  Dashboard 'unbilled' KPI (sum of amount where invoiced=False) = £{kpi}")
print("  => KPI counts the full £81 farrier as unbilled though £48.60 is already invoiced to Carol.")

print("\n=== TEST H: CSV export sanity ===")
inv1 = Invoice.objects.get(invoice_number="INV00001")
out = io.StringIO()
write_xero_csv(inv1, out)
lines = out.getvalue().splitlines()
print(f"  CSV rows: {len(lines)} (header + line items)")
for l in lines[:6]:
    print("   ", l[:120])
