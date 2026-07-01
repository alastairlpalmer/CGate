"""Verify #3 (move-to-new-owner) and #4 (sub-100% shares). Also no-regression."""
from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from core.models import Owner, Location, Horse, RateType, Placement, OwnershipShare
from core.services import PlacementService
from invoicing.services import InvoiceService

start, end = date(2026,6,1), date(2026,6,30)
grass = RateType.objects.get(name="Grass")
locs = list(Location.objects.all())

def total(o):
    return InvoiceService.calculate_invoice_preview(o, start, end)['total']

print("=== NO-REGRESSION: seeded owners (expect unchanged from PR#22 baseline) ===")
expect = {"Alice Anderson": "150 (Thunder) + 70.01 (Trio 33.34%) + 120 vet",
          "Bob Brown": "182 (Bella) + 69.99 (Trio) + 45 feed",
          "Carol Clark": "432 (Star 60%) + 69.99 (Trio) + 48.60 farrier",
          "Dave Davies": "288 (Star 40%) + 32.40 farrier",
          "Emma Evans": "150 (Ghost, no share -> placement owner)",
          "Grace Green": "300 (Misty)",
          "Fiona Foster": "0 (departed May)"}
for name, note in expect.items():
    o = Owner.objects.get(name=name)
    print(f"  {name:16s} £{total(o):>8}   [{note}]")

print("\n=== #3: move horse to a NEW owner ===")
oOld = Owner.objects.create(name="Move Old", email="mo@example.com")
oNew = Owner.objects.create(name="Move New", email="mn@example.com")
h, _ = PlacementService.create_new_arrival(
    name="Mover", owner=oOld, location=locs[0], rate_type=grass,
    arrival_date=date(2026,6,1))
PlacementService.move_horse(h, new_location=locs[1], move_date=date(2026,6,16), new_owner=oNew)
h.refresh_from_db()
print(f"  Placements: {[(p.owner.name, p.start_date, p.end_date) for p in h.placements.order_by('start_date')]}")
print(f"  Ownership share now: {[s.owner.name for s in h.ownership_shares.all()]}  (expect ['Move New'])")
# current_owner uses cached_property; reload fresh instance
h2 = Horse.objects.get(pk=h.pk)
print(f"  current_owner: {h2.current_owner}  (expect Move New)")
print(f"  Old owner billed £{total(oOld)}  (expect 75: 1-15 Jun)")
print(f"  New owner billed £{total(oNew)}  (expect 75: 16-30 Jun)")

print("\n=== #4a: single share < 100% -> billed 100% to placement owner (no loss) ===")
o1 = Owner.objects.create(name="Half Single")
hs = Horse.objects.create(name="HalfSingle")
OwnershipShare(horse=hs, owner=o1, share_percentage=Decimal("50.00")).save()
Placement(horse=hs, owner=o1, location=locs[0], rate_type=grass, start_date=date(2026,5,1)).save()
print(f"  Owner billed £{total(o1)}  (expect 150 full, not 75)")

print("\n=== #4b: co-owned shares total 90% -> remainder billed to primary ===")
oa = Owner.objects.create(name="CoA")
ob = Owner.objects.create(name="CoB")
hc = Horse.objects.create(name="CoHorse")
OwnershipShare(horse=hc, owner=oa, share_percentage=Decimal("60.00"), is_primary_contact=True).save()
OwnershipShare(horse=hc, owner=ob, share_percentage=Decimal("30.00")).save()
Placement(horse=hc, owner=oa, location=locs[0], rate_type=grass, start_date=date(2026,5,1)).save()
# full = 30*5 = 150. Primary (CoA) should get 60% + 10% remainder = 70% = 105; CoB 30% = 45. Sum 150.
ta, tb = total(oa), total(ob)
print(f"  CoA (primary 60%+10% remainder) £{ta}  (expect 105)")
print(f"  CoB (30%) £{tb}  (expect 45)")
print(f"  Sum £{ta+tb}  (expect 150 full, nothing lost)")

print("\n=== #4c: formset-level validation via OwnershipShareFormSet ==100% ===")
from django.forms import inlineformset_factory
from core.forms import OwnershipShareForm, BaseOwnershipShareFormSet
FS = inlineformset_factory(Horse, OwnershipShare, form=OwnershipShareForm,
                           formset=BaseOwnershipShareFormSet, extra=0)
hz = Horse.objects.create(name="FormsetHorse")
def run_formset(pcts):
    data = {'ownership_shares-TOTAL_FORMS': str(len(pcts)),
            'ownership_shares-INITIAL_FORMS': '0',
            'ownership_shares-MIN_NUM_FORMS': '0',
            'ownership_shares-MAX_NUM_FORMS': '1000'}
    for i, (own, pct) in enumerate(pcts):
        data[f'ownership_shares-{i}-owner'] = str(own.pk)
        data[f'ownership_shares-{i}-share_percentage'] = str(pct)
    fs = FS(data, instance=hz)
    return fs.is_valid(), fs.non_form_errors()
ok90, err90 = run_formset([(oa, "60"), (ob, "30")])
print(f"  60+30=90%: valid={ok90} (expect False) errors={list(err90)}")
ok100, _ = run_formset([(oa, "60"), (ob, "40")])
print(f"  60+40=100%: valid={ok100} (expect True)")
ok0, _ = run_formset([])
print(f"  no shares: valid={ok0} (expect True - allowed)")
