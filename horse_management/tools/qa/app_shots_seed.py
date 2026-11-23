"""A small, tidy yard for the pictures in the app's manifest.

static/manifest.json names three screenshots (docs/MOBILE_APP_PLAN.md,
item D3). Android shows them when it offers to install the app. They are
made from this yard, so no real horse, owner or yard is in them. Every
name here is made up.

Run on a new, empty database, then take the pictures:

    export DATABASE_URL=sqlite:////tmp/app-shots.sqlite3 DEBUG=True MFA_REQUIRED_FOR_ADMINS=False AXES_ENABLED=False
    python manage.py migrate
    python manage.py shell < tools/qa/app_shots_seed.py
    python manage.py runserver 127.0.0.1:8770 --noreload &
    node tools/qa/app_shots.js http://127.0.0.1:8770 static/screenshots
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import BusinessSettings, Horse, Location, Owner, Placement, RateType
from health.models import Vaccination, VaccinationType, WormingTreatment

User = get_user_model()
today = timezone.localdate()

if not User.objects.filter(username='sam').exists():
    User.objects.create_superuser('sam', 'sam@example.com', 'ShotsPass123!', first_name='Sam', last_name='Carter')

business = BusinessSettings.get_settings()
business.business_name = 'Oakfield Livery'
business.save()

grass = RateType.objects.get_or_create(name='Grass livery', defaults={'daily_rate': Decimal('6.00')})[0]
stabled = RateType.objects.get_or_create(name='Full livery', defaults={'daily_rate': Decimal('24.00')})[0]

places = {}
for site, name, capacity in (
    ('Home Farm', 'Top Field', 8), ('Home Farm', 'Long Meadow', 8),
    ('Home Farm', 'Stable Yard', 10), ('Mill Lane', 'River Paddock', 6),
):
    places[name] = Location.objects.get_or_create(site=site, name=name, defaults={'capacity': capacity})[0]

owners = {}
for name, phone in (
    ('Helen Marsh', '07700 900101'), ('James Whitlock', '07700 900102'),
    ('Priya Nair', '07700 900103'), ('Tom Ellery', '07700 900104'),
):
    owners[name] = Owner.objects.get_or_create(name=name, defaults={
        'phone': phone, 'email': name.split()[0].lower() + '@example.com'})[0]

flu = VaccinationType.objects.get_or_create(name='Flu and tetanus', defaults={'interval_months': 12})[0]

# name, colour, sex, year of birth, owner, place, rate, flu due in N days (None: not due soon)
HORSES = (
    ('Bramble', 'bay', 'mare', 2015, 'Helen Marsh', 'Top Field', grass, 0),
    ('Captain', 'grey', 'gelding', 2012, 'James Whitlock', 'Stable Yard', stabled, 1),
    ('Clover', 'chestnut', 'mare', 2018, 'Priya Nair', 'Top Field', grass, 1),
    ('Domino', 'piebald', 'gelding', 2014, 'Tom Ellery', 'Long Meadow', grass, 1),
    ('Fern', 'dun', 'mare', 2019, 'Helen Marsh', 'Long Meadow', grass, 4),
    ('Jasper', 'black', 'gelding', 2011, 'James Whitlock', 'Stable Yard', stabled, 4),
    ('Maple', 'palomino', 'mare', 2016, 'Priya Nair', 'River Paddock', grass, 150),
    ('Monty', 'bay', 'gelding', 2010, 'Tom Ellery', 'Stable Yard', stabled, -6),
    ('Poppy', 'chestnut', 'mare', 2017, 'Helen Marsh', 'River Paddock', grass, 200),
    ('Rowan', 'brown', 'gelding', 2013, 'James Whitlock', 'Top Field', grass, 90),
    ('Storm', 'grey', 'gelding', 2015, 'Priya Nair', 'Long Meadow', grass, 240),
    ('Willow', 'roan', 'mare', 2020, 'Tom Ellery', 'River Paddock', grass, 120),
)

for name, colour, sex, year, owner, place, rate, due in HORSES:
    horse, made = Horse.objects.get_or_create(name=name, defaults={
        'color': colour, 'sex': sex, 'date_of_birth': date(year, 4, 1), 'has_passport': True,
        'passport_number': f'826{year}{len(name):02d}{ord(name[0]):03d}',
    })
    if not made:
        continue
    Placement.objects.create(
        horse=horse, owner=owners[owner], location=places[place], rate_type=rate,
        start_date=today - timedelta(days=200),
    )
    Vaccination.objects.create(
        horse=horse, vaccination_type=flu, date_given=today - timedelta(days=365 - due),
        next_due_date=today + timedelta(days=due),
    )
    WormingTreatment.objects.create(
        horse=horse, date=today - timedelta(days=30), product_name='Equest',
        next_due_date=today + timedelta(days=60),
    )

print('horses', Horse.objects.count(), 'owners', Owner.objects.count(), 'locations', Location.objects.count())
