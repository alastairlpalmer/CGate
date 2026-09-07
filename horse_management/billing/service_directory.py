"""The starter services directory.

Seeded once by billing.migrations.0016_seed_service_items on an empty
directory; after that the list is the user's to edit in Settings →
Services & Prices. Kept out of the migration so tests (which run with
migrations disabled) and a future reseed command can share it.

Each row: (category, name, price, extras) — extras are optional field
values such as the farrier work type a farrier item is recorded as.
"""

from decimal import Decimal

DEFAULT_SERVICE_ITEMS = (
    ('vaccination', 'Vaccination - Flu', Decimal('50.00'), {}),
    ('egg_count', 'Worm test (egg count)', Decimal('12.00'), {}),
    ('worming', 'Wormer - Equimax', Decimal('17.00'), {}),
    ('worming', 'VET Wormers - Aloquantel', Decimal('18.00'), {}),
    ('worming', 'VET Wormers - Equest', Decimal('20.00'), {}),
    ('worming', 'VET Wormers - Panacur', Decimal('21.50'), {}),
    ('worming', 'VET Wormers - Pramox', Decimal('29.00'), {}),
    ('worming', 'Strongid P (pyrantel)', Decimal('15.00'), {}),
    ('farrier', 'Trim', Decimal('45.00'), {'farrier_work': 'trim'}),
    ('farrier', 'Front shoes', Decimal('45.00'), {'farrier_work': 'front_shoes'}),
    ('farrier', 'Shoes all', Decimal('108.00'), {'farrier_work': 'full_set'}),
    ('transport', 'Transport', Decimal('100.00'), {}),
)


def seed_default_services(ServiceItem, VaccinationType=None):
    """Create the starter list when the directory is empty.

    Takes the model classes as arguments so a migration can pass its
    historical models. The flu vaccination is linked to an existing "flu"
    vaccination type when one exists, so picking it also fills the type.
    Returns the number of items created (0 when the directory had rows).
    """
    if ServiceItem.objects.exists():
        return 0
    flu_type = None
    if VaccinationType is not None:
        flu_type = VaccinationType.objects.filter(name__icontains='flu').order_by('pk').first()
    created = 0
    for order, (category, name, price, extras) in enumerate(DEFAULT_SERVICE_ITEMS):
        fields = dict(extras)
        if category == 'vaccination' and flu_type is not None:
            fields['vaccination_type'] = flu_type
        ServiceItem.objects.create(
            category=category, name=name, price=price, sort_order=order, **fields,
        )
        created += 1
    return created
