"""Seed a practice and a vet for the C-vet checks (docs/C_VET_PLAN.md).

Run after farrier_seed.py, against the same throwaway database:

    python manage.py shell < tools/qa/farrier_seed.py
    python manage.py shell < tools/qa/cvet_seed.py

Makes: the practice "Hill Vets" with an open link to the yard, a vet
login ("vet" / "qa-pass-123") with the Vet role, a member of the practice,
three prices, and vaccination dates: Willow overdue, Fudge due in 5 days.
"""

from django.contrib.auth import get_user_model

from core.models import BusinessSettings, Role
from core.roles_testutils import assign_role
from cvet.models import Practice, PracticeMember, PracticeYardLink

User = get_user_model()

# The yard's own name: the vet's client.
yard_settings = BusinessSettings.get_settings()
yard_settings.business_name = 'Palmer Farm'
yard_settings.save()

practice, _ = Practice.objects.get_or_create(name='Hill Vets')
PracticeYardLink.objects.get_or_create(practice=practice)

vet, _ = User.objects.get_or_create(
    username='vet', defaults={'email': 'tim@example.com', 'first_name': 'Tim', 'last_name': 'Hill'},
)
vet.set_password('qa-pass-123')
vet.save()
assign_role(vet, Role.objects.get(name='Vet'))
PracticeMember.objects.get_or_create(practice=practice, user=vet)



# Phase 4: a price list, and vaccination dates so some horses are due.
from datetime import timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402

from django.utils import timezone  # noqa: E402

from core.models import Horse  # noqa: E402
from cvet.models import PriceItem  # noqa: E402
from health.models import Vaccination, VaccinationType  # noqa: E402

for name, price in (('Flu/Tet booster', '65.00'), ('Sedation', '27.50'), ('Visit fee', '40.00')):
    PriceItem.objects.get_or_create(practice=practice, name=name, defaults={'price_inc_vat': Decimal(price)})

today = timezone.localdate()
flu, _ = VaccinationType.objects.get_or_create(name='Flu/Tet')
# The booster is a vaccination: giving it records the dose at the yard.
PriceItem.objects.filter(practice=practice, name='Flu/Tet booster').update(
    vaccination_type=flu, category='Vaccination',
)
if not Vaccination.objects.exists():
    for name, days in (('Willow', -10), ('Fudge', 5), ('Hazel', 200)):
        Vaccination.objects.create(
            horse=Horse.objects.get(name=name), vaccination_type=flu,
            date_given=today + timedelta(days=days - 365), next_due_date=today + timedelta(days=days),
        )
print('C-vet phase 4 seed done.')
