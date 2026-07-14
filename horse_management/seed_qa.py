"""QA seed script. Run: python manage.py shell < seed_qa.py"""
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from core.models import (Owner, Location, Horse, RateType, Placement,
                         OwnershipShare, BusinessSettings)
from billing.models import ServiceProvider, ExtraCharge
from health.models import VaccinationType, Vaccination, FarrierVisit

User = get_user_model()

# --- Users ---
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@example.com', 'AdminPass123!')
    print("created superuser admin / AdminPass123!")
if not User.objects.filter(username='viewer').exists():
    from core.models import Role, UserRole
    v = User.objects.create_user('viewer', 'viewer@example.com', 'ViewPass123!')
    viewer_role = Role.objects.filter(name='Viewer').first()
    if viewer_role:
        UserRole.objects.get_or_create(user=v, defaults={'role': viewer_role})
    print("created viewer viewer / ViewPass123! (Viewer role)")

# --- Business settings ---
bs = BusinessSettings.get_settings()
bs.business_name = "Meadowbrook Livery Yard"
bs.address = "Meadowbrook Farm, Colgate Lane, West Sussex, RH12 4SX"
bs.phone = "01403 555123"
bs.email = "office@meadowbrook.example"
bs.bank_details = "Sort 01-02-03  Acct 12345678"
bs.invoice_prefix = "INV"
bs.default_payment_terms = 30
bs.save()

# --- Rate types ---
rates = {}
for name, amt in [("Grass", "5.00"), ("Grazing", "6.00"), ("Premium", "7.00"),
                  ("Mare+Foal", "10.00"), ("Stabled", "24.00")]:
    rt, _ = RateType.objects.get_or_create(name=name, defaults={'daily_rate': Decimal(amt)})
    rates[name] = rt

# --- Locations ---
locs = {}
for site, name in [("Colgate", "Top Field"), ("Colgate", "Bottom Field"),
                   ("Somerford", "Stables Block A"), ("Somerford", "Paddock 1"),
                   ("California Farm", "Mare Paddock")]:
    l, _ = Location.objects.get_or_create(site=site, name=name, defaults={'capacity': 6})
    locs[name] = l

# --- Owners ---
def owner(name, email):
    o, _ = Owner.objects.get_or_create(name=name, defaults={'email': email,
        'phone': '07700 900000', 'address': '1 Test Lane'})
    return o

alice = owner("Alice Anderson", "alice@example.com")
bob = owner("Bob Brown", "bob@example.com")
carol = owner("Carol Clark", "carol@example.com")
dave = owner("Dave Davies", "dave@example.com")
emma = owner("Emma Evans", "emma@example.com")
grace = owner("Grace Green", "grace@example.com")
fiona = owner("Fiona Foster", "fiona@example.com")  # zero activity
noeml = owner("Nigel NoEmail", "")  # owner without email for send test

JUN = (date(2026, 6, 1), date(2026, 6, 30))

def make_horse(name, **kw):
    h, _ = Horse.objects.get_or_create(name=name, defaults=kw)
    return h

def share(horse, owner, pct, primary=False):
    return OwnershipShare.objects.get_or_create(horse=horse, owner=owner,
        defaults={'share_percentage': Decimal(str(pct)), 'is_primary_contact': primary})[0]

def placement(horse, owner, loc, rate, start, end=None):
    p = Placement(horse=horse, owner=owner, location=loc, rate_type=rate,
                  start_date=start, end_date=end)
    p.save()
    return p

EXPECT = []

# 1. Thunder — Alice 100% — Grass full June (start May 1, open) => 30*5 = 150
thunder = make_horse("Thunder", sex="gelding", color="bay")
share(thunder, alice, 100, True)
placement(thunder, alice, locs["Top Field"], rates["Grass"], date(2026,5,1))
EXPECT.append(("Alice livery Thunder", "30 * 5.00", Decimal("150.00")))

# 2. Bella — Bob 100% — mid-month move: Grass Jun1-14, Premium Jun15-open
bella = make_horse("Bella", sex="mare", color="grey")
share(bella, bob, 100, True)
placement(bella, bob, locs["Bottom Field"], rates["Grass"], date(2026,6,1), date(2026,6,14))
placement(bella, bob, locs["Paddock 1"], rates["Premium"], date(2026,6,15))
EXPECT.append(("Bob livery Bella move", "14*5 + 16*7 = 70+112", Decimal("182.00")))

# 3. Star — Carol 60 / Dave 40 — Stabled full June => 720; Carol 432, Dave 288
star = make_horse("Star", sex="gelding", color="black")
share(star, carol, 60, True)
share(star, dave, 40)
placement(star, carol, locs["Stables Block A"], rates["Stabled"], date(2026,5,15))
EXPECT.append(("Carol livery Star 60%", "720 * 0.60", Decimal("432.00")))
EXPECT.append(("Dave livery Star 40%", "720 * 0.40", Decimal("288.00")))

# 4. Ghost — Emma via direct placement, NO ownership share (billing-gap test)
ghost = make_horse("Ghost", sex="gelding", color="grey")
placement(ghost, emma, locs["Top Field"], rates["Grass"], date(2026,5,1))
EXPECT.append(("Emma livery Ghost (NO share)", "SHOULD be 150 but expect 0 -> BUG", Decimal("0.00")))

# 5. Misty (mare) + foal — Grace — Mare+Foal full June => 300
misty = make_horse("Misty", sex="mare", color="chestnut")
share(misty, grace, 100, True)
placement(misty, grace, locs["Mare Paddock"], rates["Mare+Foal"], date(2026,6,1))
EXPECT.append(("Grace livery Misty M+F", "30 * 10", Decimal("300.00")))

# 6. Fiona — horse that departed in May (placement ended 2026-05-20) => June £0
oldie = make_horse("Oldie", sex="gelding")
share(oldie, fiona, 100, True)
placement(oldie, fiona, locs["Bottom Field"], rates["Grass"], date(2026,4,1), date(2026,5,20))
EXPECT.append(("Fiona June (departed May)", "no overlap", Decimal("0.00")))

# 7. Trio horse — 3-way rounding test: 33.34/33.33/33.33 of Premium full June (210)
trio = make_horse("Trio", sex="gelding")
share(trio, alice, Decimal("33.34"), True)
share(trio, bob, Decimal("33.33"))
share(trio, carol, Decimal("33.33"))
placement(trio, alice, locs["Paddock 1"], rates["Premium"], date(2026,6,1))
EXPECT.append(("Trio rounding", "210*.3334=70.01; 210*.3333=69.99 x2; sum=210.00", Decimal("210.00")))

# --- Service providers ---
vet, _ = ServiceProvider.objects.get_or_create(name="Downland Equine Vets",
    defaults={'provider_type': 'vet'})
farrier, _ = ServiceProvider.objects.get_or_create(name="J. Smith Farrier",
    defaults={'provider_type': 'farrier'})

# --- Extra charges (June) ---
# Direct vet charge to Alice on Thunder (no split) => 120 to Alice
ExtraCharge.objects.get_or_create(horse=thunder, owner=alice, charge_type='vet',
    date=date(2026,6,10), description="Lameness workup", amount=Decimal("120.00"),
    split_by_ownership=False, defaults={'service_provider': vet})
EXPECT.append(("Alice extra vet", "direct", Decimal("120.00")))

# Split farrier charge on Star (Carol/Dave) 81.00 -> Carol 48.60 Dave 32.40
ExtraCharge.objects.get_or_create(horse=star, owner=carol, charge_type='farrier',
    date=date(2026,6,12), description="Full set shoes", amount=Decimal("81.00"),
    split_by_ownership=True, defaults={'service_provider': farrier})
EXPECT.append(("Star farrier split Carol", "81*.60", Decimal("48.60")))
EXPECT.append(("Star farrier split Dave", "81*.40", Decimal("32.40")))

# Direct feed charge to Bob on Bella
ExtraCharge.objects.get_or_create(horse=bella, owner=bob, charge_type='feed',
    date=date(2026,6,5), description="Hard feed", amount=Decimal("45.00"),
    split_by_ownership=False)
EXPECT.append(("Bob extra feed", "direct", Decimal("45.00")))

# --- Health records for reminder testing ---
vt, _ = VaccinationType.objects.get_or_create(name="Equine Influenza",
    defaults={'interval_months': 12, 'reminder_days_before': 30})
# Vaccination due soon (given ~11 months ago -> due ~ 1 month out)
Vaccination.objects.get_or_create(horse=thunder, vaccination_type=vt,
    date_given=date(2025,7,20), defaults={})
# Overdue vaccination
Vaccination.objects.get_or_create(horse=bella, vaccination_type=vt,
    date_given=date(2025,1,1), defaults={})
# Farrier visit due soon
FarrierVisit.objects.get_or_create(horse=star, date=date(2026,6,12),
    defaults={'work_done': 'Full set', 'next_due_date': date(2026,7,10),
              'cost': Decimal('81.00')})

print("=== SEED COMPLETE ===")
print(f"Owners: {Owner.objects.count()}, Horses: {Horse.objects.count()}, "
      f"Placements: {Placement.objects.count()}, Shares: {OwnershipShare.objects.count()}")
print("=== GROUND TRUTH EXPECTATIONS (June 2026) ===")
for label, calc, val in EXPECT:
    print(f"  {label:35s} {calc:45s} = £{val}")
