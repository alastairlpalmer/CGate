"""Seed a practice and a vet for the C-vet checks (docs/C_VET_PLAN.md).

Run after farrier_seed.py, against the same throwaway database:

    python manage.py shell < tools/qa/farrier_seed.py
    python manage.py shell < tools/qa/cvet_seed.py

Makes: the practice "Hill Vets" with an open link to the yard, and a vet
login ("vet" / "qa-pass-123") with the Vet role, a member of the practice.
"""

from django.contrib.auth import get_user_model

from core.models import Role
from core.roles_testutils import assign_role
from cvet.models import Practice, PracticeMember, PracticeYardLink

User = get_user_model()

practice, _ = Practice.objects.get_or_create(name='Hill Vets')
PracticeYardLink.objects.get_or_create(practice=practice)

vet, _ = User.objects.get_or_create(
    username='vet', defaults={'email': 'tim@example.com', 'first_name': 'Tim', 'last_name': 'Hill'},
)
vet.set_password('qa-pass-123')
vet.save()
assign_role(vet, Role.objects.get(name='Vet'))
PracticeMember.objects.get_or_create(practice=practice, user=vet)

print('C-vet seed done.')
