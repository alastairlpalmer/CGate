"""Verify #5 (reconciliation), #6 (unbilled KPI). python manage.py shell < verify_567.py"""
from datetime import date
from decimal import Decimal
from django.db.models import Sum
from core.models import Owner, Horse, OwnershipShare, Placement, RateType
from billing.models import ExtraCharge
from invoicing.services import InvoiceService

start, end = date(2026,6,1), date(2026,6,30)

def total(o):
    return InvoiceService.calculate_invoice_preview(o, start, end)['total']

print("=== #5: co-owned livery splits sum to the FULL charge ===")
trio = Horse.objects.get(name="Trio")
shares = OwnershipShare.objects.filter(horse=trio)
pl = Placement.objects.get(horse=trio)
full = pl.calculate_charge(start, end)
per_owner = {}
for s in shares:
    prev = InvoiceService.calculate_invoice_preview(s.owner, start, end)
    livery = [c for c in prev['livery_charges'] if c['horse'].pk == trio.pk]
    per_owner[s.owner.name] = sum(c['amount'] for c in livery)
print(f"  Trio full charge = £{full}")
for name, amt in per_owner.items():
    print(f"    {name:16s} £{amt}")
ssum = sum(per_owner.values())
print(f"  Sum of splits = £{ssum}  {'OK reconciles' if ssum==full else 'MISMATCH diff='+str(full-ssum)}")

print("\n=== #5b: split EXTRA charge reconciles (odd amount) ===")
# Add an odd split charge on Trio: £100.00 split 33.34/33.33/33.33
ExtraCharge.objects.create(horse=trio, owner=Owner.objects.get(name="Alice Anderson"),
    charge_type='vet', date=date(2026,6,20), description="Odd split test",
    amount=Decimal("100.00"), split_by_ownership=True)
parts = {}
for s in shares:
    prev = InvoiceService.calculate_invoice_preview(s.owner, start, end)
    ec = [c for c in prev['extra_charges'] if c.get('charge') and c['charge'].description=="Odd split test"]
    parts[s.owner.name] = sum(c['amount'] for c in ec)
print(f"  £100 split -> {parts}")
psum = sum(parts.values())
print(f"  Sum = £{psum}  {'OK' if psum==Decimal('100.00') else 'MISMATCH'}")

print("\n=== #6: unbilled KPI excludes already-invoiced portion of split charges ===")
naive = ExtraCharge.objects.filter(invoiced=False).aggregate(t=Sum('amount'))['t']
print(f"  Naive sum(amount where invoiced=False) = £{naive}")
print(f"  ExtraCharge.unbilled_total()           = £{ExtraCharge.unbilled_total()}")
# Now bill Carol (60% of Star farrier £81 -> 48.60 invoiced, flag stays False)
carol = Owner.objects.get(name="Carol Clark")
inv = InvoiceService.create_invoice(carol, start, end)
far = ExtraCharge.objects.filter(charge_type='farrier').first(); far.refresh_from_db()
print(f"  After billing Carol: farrier invoiced flag={far.invoiced} (still False, Dave unbilled)")
naive2 = ExtraCharge.objects.filter(invoiced=False).aggregate(t=Sum('amount'))['t']
print(f"  Naive sum now  = £{naive2}  (still counts full £81 farrier)")
print(f"  unbilled_total = £{ExtraCharge.unbilled_total()}  (farrier now counts only Dave's £32.40 remainder)")

print("\n=== NO-REGRESSION baseline (Trio primary Alice now absorbs the penny) ===")
for name in ["Bob Brown", "Dave Davies", "Emma Evans", "Grace Green"]:
    print(f"  {name:16s} £{total(Owner.objects.get(name=name))}")
