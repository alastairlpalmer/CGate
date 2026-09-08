"""Verify invoice math against expectations. python manage.py shell < verify_invoices.py"""
from datetime import date
from decimal import Decimal
from core.models import Owner
from invoicing.services import InvoiceService

start, end = date(2026,6,1), date(2026,6,30)

print("=== INVOICE PREVIEWS (June 2026) ===")
for owner in Owner.objects.order_by('name'):
    p = InvoiceService.calculate_invoice_preview(owner, start, end)
    if not p['all_charges']:
        print(f"\n{owner.name}: (no charges) total=£{p['total']}")
        continue
    print(f"\n{owner.name}: subtotal=£{p['subtotal']} total=£{p['total']}")
    for c in p['all_charges']:
        print(f"    [{c['line_type']:8s}] {c['description'][:70]}")
        print(f"             days={c['days']} rate={c['daily_rate']} full={c['full_amount']} amount=£{c['amount']} ({c['share_percentage']}%)")

# Check sum-of-splits reconciliation for shared items
print("\n=== RECONCILIATION: do owner splits sum to the full charge? ===")
from core.models import OwnershipShare, Placement, Horse
for horse in Horse.objects.all():
    shares = OwnershipShare.objects.filter(horse=horse)
    if shares.count() < 2:
        continue
    for pl in Placement.objects.filter(horse=horse):
        days = pl.get_days_in_period(start, end)
        if days <= 0:
            continue
        full = pl.calculate_charge(start, end)
        split_sum = sum((full * s.share_fraction).quantize(Decimal('0.01')) for s in shares)
        flag = "OK" if split_sum == full else f"MISMATCH diff={full-split_sum}"
        print(f"  {horse.name} placement {pl.rate_type.name}: full={full} split_sum={split_sum} [{flag}]")

# Generate-monthly dry check: which owners get invoices
print("\n=== get_owners_for_billing ===")
owners = InvoiceService.get_owners_for_billing(start, end)
print("  Billed:", sorted(o.name for o in owners))
print("  NOT billed:", sorted(set(o.name for o in Owner.objects.all()) - set(o.name for o in owners)))
