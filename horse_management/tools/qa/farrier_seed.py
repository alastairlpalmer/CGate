"""Seed a yard for the farrier phone check (docs/FARRIER_PAGE_PLAN.md, step 7).

Run against a throwaway database, never a real one:

    DATABASE_URL=sqlite:////path/to/qa.sqlite3 python manage.py migrate
    DATABASE_URL=sqlite:////path/to/qa.sqlite3 python manage.py shell < tools/qa/farrier_seed.py

Makes: a staff admin ("yard" / "qa-pass-123"), a farrier login
("farrier" / "qa-pass-123") linked to "Sam Shoe Farriery", a price list,
24 horses over 3 fields (some co-owned, one with no owner, some due for
the farrier, some with long names) and one finished visit last week.
"""

import importlib
from datetime import timedelta
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.utils import timezone

from billing.models import ServiceItem, ServiceProvider
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType, Role
from core.roles_testutils import assign_role, make_admin
from health.farrier import finish_visit, record_line

User = get_user_model()
PASSWORD = 'qa-pass-123'
today = timezone.localdate()

importlib.import_module('core.migrations.0053_seed_farrier_role').seed_farrier_role(django_apps, None)

if not User.objects.filter(username='yard').exists():
    admin = make_admin('yard')
    admin.set_password(PASSWORD)
    admin.email = 'yard@example.com'
    admin.save()

farrier, _ = User.objects.get_or_create(username='farrier', defaults={'email': 'sam@example.com', 'first_name': 'Sam'})
farrier.set_password(PASSWORD)
farrier.save()
assign_role(farrier, Role.objects.get(name='Farrier'))
provider, _ = ServiceProvider.objects.get_or_create(
    name='Sam Shoe Farriery', defaults={'provider_type': 'farrier', 'email': 'sam@example.com', 'user': farrier},
)

for order, (name, work, price) in enumerate([
    ('Full set', 'full_set', '95'),
    ('Front shoes', 'front_shoes', '65'),
    ('Trim', 'trim', '35'),
    ('Remedial set', 'remedial', '140'),
    ('Lost shoe refit', '', '25'),
]):
    ServiceItem.objects.get_or_create(
        name=name, provider=provider,
        defaults={'category': 'farrier', 'farrier_work': work, 'price': Decimal(price), 'sort_order': order},
    )

rate, _ = RateType.objects.get_or_create(name='Grass', defaults={'daily_rate': 5})
fields = [
    Location.objects.get_or_create(name=name, defaults={'site': 'Main yard', 'capacity': 12})[0]
    for name in ('Top Field', 'Bottom Paddock', 'Stable Block A (the long barn by the arena)')
]
owners = [Owner.objects.get_or_create(name=n)[0] for n in ('Jo Bloggs', 'Ali Khan', 'Sarah Thompson-Whitfield')]

names = [
    'Bramble', 'Clover', 'Dancer', 'Echo', 'Fudge', 'Ginger', 'Hazel', 'Indigo',
    'Juniper', 'Kestrel', 'Lark', 'Maple', 'Nutmeg', 'Olive', 'Pepper', 'Quill',
    'Rosie', 'Saffron', 'Tansy', 'Umber', 'Velvet', 'Willow',
    'Thunderbolt Magnificent Champion of Wettenhall', 'No Owner Ned',
]
for i, name in enumerate(names):
    horse, created = Horse.objects.get_or_create(name=name)
    if not created or name == 'No Owner Ned':
        continue
    owner = owners[i % 3]
    if i % 5 == 0:  # co-owned
        OwnershipShare.objects.create(horse=horse, owner=owner, share_percentage=50, is_primary_contact=True)
        OwnershipShare.objects.create(horse=horse, owner=owners[(i + 1) % 3], share_percentage=50)
    else:
        OwnershipShare.objects.create(horse=horse, owner=owner, share_percentage=100, is_primary_contact=True)
    Placement.objects.create(
        horse=horse, owner=owner, location=fields[i % 3], rate_type=rate,
        start_date=today - timedelta(days=90),
    )

# Staff records from earlier visits, so some horses are due or overdue.
from health.models import FarrierVisit  # noqa: E402

if not FarrierVisit.objects.exists():
    for i, name in enumerate(('Echo', 'Fudge', 'Ginger', 'Hazel', 'Indigo', 'Willow', 'Thunderbolt Magnificent Champion of Wettenhall')):
        FarrierVisit.objects.create(
            horse=Horse.objects.get(name=name), date=today - timedelta(days=40 + 4 * i),
            work_done='full_set' if i % 2 else 'trim', cost=Decimal('0'),
        )

# One finished visit last week, so My visits and the yard pages have history.
if not provider.visits.exists():
    trim = ServiceItem.objects.get(provider=provider, name='Trim')
    full = ServiceItem.objects.get(provider=provider, name='Full set')
    for name, item in (('Bramble', full), ('Clover', trim), ('Dancer', trim), ('No Owner Ned', trim)):
        record_line(provider, farrier, Horse.objects.get(name=name), item=item)
    visit, _ = finish_visit(provider, farrier)
    visit.date = today - timedelta(days=7)
    visit.save(update_fields=['date'])
    visit.farrier_visits.update(date=visit.date)
print('Seed done.')
