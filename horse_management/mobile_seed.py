"""Add mobile-stress data on top of seed_qa. python manage.py shell < mobile_seed.py"""
from datetime import date
from decimal import Decimal
from core.models import Owner, Location, Horse, RateType, Placement, OwnershipShare
from billing.models import ExtraCharge, ServiceProvider
from invoicing.services import InvoiceService

grass = RateType.objects.get(name="Grass")
prem = RateType.objects.get(name="Premium")
loc = Location.objects.first()
loc2 = Location.objects.all()[1]

# Long-named owner with long address (stress headers/labels on mobile)
big, _ = Owner.objects.get_or_create(
    name="Bigyard Syndicate Holdings & Partners (Cheshire) LLP",
    defaults={'email': 'accounts.department@bigyard-syndicate-holdings.example.co.uk',
              'phone': '+44 7700 900123',
              'address': 'The Old Stables, Wettenhall Road, Near Winsford, Cheshire, CW7 4DE, United Kingdom'})

vet, _ = ServiceProvider.objects.get_or_create(name="Downland Equine Vets", defaults={'provider_type':'vet'})

# 6 horses owned by big, each placed full June -> long line-item invoice
for i in range(1, 7):
    name = f"Thunderbolt Magnificent Champion of Wettenhall {i}"
    h, created = Horse.objects.get_or_create(name=name, defaults={'sex':'gelding','color':'bay'})
    if created:
        OwnershipShare.objects.create(horse=h, owner=big, share_percentage=100, is_primary_contact=True)
        Placement(horse=h, owner=big, location=loc if i % 2 else loc2,
                  rate_type=grass if i % 2 else prem, start_date=date(2026,5,1)).save()
        ExtraCharge.objects.get_or_create(horse=h, owner=big, charge_type='vet',
            date=date(2026,6,i+1), description=f"Vaccination and dental check for horse {i}",
            amount=Decimal("65.00"), split_by_ownership=False, defaults={'service_provider': vet})

# Generate the big invoice (many line items) if not already present
existing = InvoiceService.check_for_overlapping_invoices(big, date(2026,6,1), date(2026,6,30))
if not existing:
    inv = InvoiceService.create_invoice(big, date(2026,6,1), date(2026,6,30))
    print("Created big invoice", inv.invoice_number, "with", inv.line_items.count(), "line items, total £", inv.total)
else:
    print("Big invoice already exists:", existing.invoice_number, existing.line_items.count(), "items")

print("Owners:", Owner.objects.count(), "Horses:", Horse.objects.count())
