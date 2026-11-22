"""Yard-size data for the phone review (docs/MOBILE_APP_PLAN.md).

Run after seed_qa.py and mobile_seed.py, on a throwaway database:

    python manage.py shell < tools/qa/phone_yard_seed.py

It adds 62 horses with a placement, a vaccination, a farrier visit and a
worming record each. Some records are overdue, so the lists that show
what is due have a real length.
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from billing.models import ServiceProvider
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from health.models import FarrierVisit, MedicalCondition, Vaccination, VaccinationType, WormingTreatment

random.seed(7)
today = timezone.localdate()
NAMES = [
    "Avicii", "Rosie", "Jasper", "Willow", "Murphy", "Poppy", "Dexter", "Luna", "Archie", "Maisie",
    "Rupert", "Daisy", "Finn", "Tilly", "Barney", "Ruby", "Monty", "Bramble", "Ziggy", "Pepper",
    "Toby", "Holly", "Storm", "Flora", "Hector", "Ivy", "Mango", "Nutmeg", "Oscar", "Pearl",
    "Quest", "Rolo", "Sable", "Teddy", "Una", "Vince", "Wren", "Xena", "Yogi", "Zara",
    "Ace", "Biscuit", "Cleo", "Duke", "Echo", "Fable", "Gus", "Hazel", "Indy", "Juno",
    "Kip", "Lark", "Moss", "Nell", "Otis", "Pip", "Quill", "Rex", "Skye", "Tango", "Uno", "Vera",
]
owners = list(Owner.objects.all())
locations = list(Location.objects.all())
rates = list(RateType.objects.all())
vaccination_type = VaccinationType.objects.first()
vet = ServiceProvider.objects.filter(provider_type='vet').first()
farrier = ServiceProvider.objects.filter(provider_type='farrier').first()

made = 0
for i, name in enumerate(NAMES):
    horse, created = Horse.objects.get_or_create(name=name, defaults={
        'sex': random.choice(['gelding', 'mare']),
        'color': random.choice(['bay', 'grey', 'chestnut', 'black']),
    })
    if not created:
        continue
    made += 1
    owner = owners[i % len(owners)]
    OwnershipShare.objects.create(horse=horse, owner=owner, share_percentage=100, is_primary_contact=True)
    Placement(
        horse=horse, owner=owner, location=locations[i % len(locations)],
        rate_type=rates[i % len(rates)], start_date=today - timedelta(days=30 + i * 3),
    ).save()
    if vaccination_type:
        given = today - timedelta(days=random.choice([100, 200, 340, 380, 400]))
        Vaccination.objects.create(
            horse=horse, vaccination_type=vaccination_type, date_given=given,
            next_due_date=given + timedelta(days=365), vet=vet,
        )
    shod = today - timedelta(days=random.choice([10, 30, 45, 60]))
    FarrierVisit.objects.create(
        horse=horse, date=shod, service_provider=farrier, work_done='trim',
        next_due_date=shod + timedelta(days=42), cost=Decimal('35.00'),
    )
    wormed = today - timedelta(days=random.choice([20, 80, 100]))
    WormingTreatment.objects.create(
        horse=horse, date=wormed, next_due_date=wormed + timedelta(days=90), product_name='Equest',
    )
    if i % 9 == 0:
        MedicalCondition.objects.create(horse=horse, name='Sweet itch', status='active')

print('made', made, 'horses; total', Horse.objects.count())
