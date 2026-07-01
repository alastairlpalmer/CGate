"""Edge-case probes. python manage.py shell < verify_edge.py"""
from datetime import date, timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from core.models import Owner, Location, Horse, RateType, Placement, OwnershipShare
from core.services import PlacementService
from invoicing.services import InvoiceService, DuplicateInvoiceError

start, end = date(2026,6,1), date(2026,6,30)
grass = RateType.objects.get(name="Grass")
loc = Location.objects.first()

print("=== TEST A: ownership shares total < 100%  (under-billing?) ===")
o1 = Owner.objects.create(name="Half Owner", email="half@example.com")
h = Horse.objects.create(name="HalfHorse")
try:
    s = OwnershipShare(horse=h, owner=o1, share_percentage=Decimal("50.00"))
    s.save()
    print("  Created a single 50% share (total 50%). clean() allowed it:",
          OwnershipShare.objects.filter(horse=h).count(), "share(s)")
except ValidationError as e:
    print("  BLOCKED:", e)
Placement(horse=h, owner=o1, location=loc, rate_type=grass, start_date=date(2026,5,1)).save()
prev = InvoiceService.calculate_invoice_preview(o1, start, end)
print(f"  Full livery should be 30*5=150. Owner billed: £{prev['total']}  "
      f"=> {'UNDER-BILLED, 50% (£75) never invoiced to anyone' if prev['total']<150 else 'ok'}")

print("\n=== TEST B: move_horse to a NEW owner — does the ownership share follow? ===")
oOld = Owner.objects.create(name="Old Owner", email="old@example.com")
oNew = Owner.objects.create(name="New Owner", email="new@example.com")
loc2 = Location.objects.all()[1]
horse2, pl = PlacementService.create_new_arrival(
    name="MoveHorse", owner=oOld, location=loc, rate_type=grass,
    arrival_date=date(2026,6,1))
print("  After arrival, share owner:", [str(s.owner) for s in OwnershipShare.objects.filter(horse=horse2)])
PlacementService.move_horse(horse2, new_location=loc2, move_date=date(2026,6,16), new_owner=oNew)
print("  After move to New Owner, placement owners:",
      [ (p.owner.name, p.start_date, p.end_date) for p in horse2.placements.all()])
print("  Ownership share owner now:", [str(s.owner) for s in OwnershipShare.objects.filter(horse=horse2)])
pOld = InvoiceService.calculate_invoice_preview(oOld, start, end)['total']
pNew = InvoiceService.calculate_invoice_preview(oNew, start, end)['total']
print(f"  Old owner billed £{pOld} ; New owner billed £{pNew}")
print("  => New owner should pay for 15-30 Jun but likely £0; old owner billed for whole month.")

print("\n=== TEST C: placement overlap validation ===")
oc = Owner.objects.create(name="Overlap Owner")
hc = Horse.objects.create(name="OverlapHorse")
OwnershipShare(horse=hc, owner=oc, share_percentage=100).save()
Placement(horse=hc, owner=oc, location=loc, rate_type=grass, start_date=date(2026,6,1), end_date=date(2026,6,20)).save()
try:
    Placement(horse=hc, owner=oc, location=loc2, rate_type=grass, start_date=date(2026,6,10), end_date=date(2026,6,25)).save()
    print("  BUG: overlapping placement ALLOWED")
except ValidationError as e:
    print("  OK: overlap blocked:", str(e)[:80])
# end before start
try:
    Placement(horse=hc, owner=oc, location=loc, rate_type=grass, start_date=date(2026,7,10), end_date=date(2026,7,1)).save()
    print("  BUG: end-before-start ALLOWED")
except ValidationError as e:
    print("  OK: end-before-start blocked")

print("\n=== TEST D: duplicate / overlapping invoice prevention ===")
alice = Owner.objects.get(name="Alice Anderson")
inv1 = InvoiceService.create_invoice(alice, start, end)
print("  Created invoice", inv1.invoice_number, "total £", inv1.total)
try:
    InvoiceService.create_invoice(alice, date(2026,6,15), date(2026,7,15))
    print("  BUG: overlapping invoice ALLOWED")
except DuplicateInvoiceError as e:
    print("  OK: overlapping invoice blocked")
# verify line items persisted with correct totals
print("  Line items:", inv1.line_items.count(), "subtotal recalc:", inv1.subtotal)

print("\n=== TEST E: re-billing already-invoiced extra charges? ===")
# Alice's vet charge should now be invoiced=True
from billing.models import ExtraCharge
vet = ExtraCharge.objects.filter(owner=alice, charge_type='vet').first()
print("  Alice vet charge invoiced flag:", vet.invoiced, "-> attached to", vet.invoice_id)
# Split farrier on Star: Carol invoiced but Dave not yet -> should NOT be marked invoiced
carol = Owner.objects.get(name="Carol Clark")
invc = InvoiceService.create_invoice(carol, start, end)
far = ExtraCharge.objects.filter(charge_type='farrier').first()
far.refresh_from_db()
print(f"  After billing Carol only, farrier split invoiced={far.invoiced} (should be False until Dave billed)")
