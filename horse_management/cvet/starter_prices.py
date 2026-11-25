"""The starter price list of a practice.

A new practice starts with the items of normal equine work, under their
categories, so the vet's item list is not empty on the first day. The
prices are typical ones, VAT included at 20%: the practice checks each
one and changes it in C-vet (Prices), or with the price list file.

An item is added only when the practice has no item of that name, so a
price the practice has set is never changed here.
"""

from decimal import Decimal

STARTER_PRICES = (
    ('Visit', (
        ('Visit fee', '48.00'),
        ('Shared visit fee', '24.00'),
        ('Out of hours visit', '150.00'),
        ('Emergency call-out', '180.00'),
    )),
    ('Examination', (
        ('Clinical examination', '55.00'),
        ('Lameness examination', '95.00'),
        ('Eye examination', '60.00'),
        ('Pre-purchase examination (2 stage)', '180.00'),
        ('Pre-purchase examination (5 stage)', '330.00'),
    )),
    ('Vaccination', (
        ('Flu vaccination', '48.00'),
        ('Flu/Tet vaccination', '62.00'),
        ('Tetanus vaccination', '38.00'),
        ('EHV vaccination', '55.00'),
    )),
    ('Sedation', (
        ('Sedation (standard)', '38.00'),
        ('Sedation (heavy)', '55.00'),
    )),
    ('Dental', (
        ('Routine dental', '75.00'),
        ('Wolf tooth extraction', '60.00'),
    )),
    ('Diagnostics', (
        ('Blood sample: routine profile', '85.00'),
        ('Worm egg count', '15.00'),
        ('X-rays (first 2 views)', '120.00'),
        ('X-ray (each more view)', '35.00'),
        ('Ultrasound scan', '95.00'),
        ('Nerve block', '35.00'),
        ('Endoscopy', '110.00'),
    )),
    ('Reproduction', (
        ('Pregnancy scan', '45.00'),
        ('Mare swab', '40.00'),
        ('Artificial insemination', '90.00'),
    )),
    ('Procedure', (
        ('Wound: clean and dress', '45.00'),
        ('Wound: suture', '95.00'),
        ('Joint injection', '85.00'),
        ('Castration (standing)', '320.00'),
        ('Microchip', '30.00'),
        ('Passport: draw the markings', '35.00'),
        ('Euthanasia', '180.00'),
    )),
    ('Medication', (
        ('Antibiotic injection', '28.00'),
        ('Anti-inflammatory injection', '26.00'),
        ('Phenylbutazone (10 sachets)', '18.00'),
        ('Wormer', '18.00'),
    )),
)


def add_starter_prices(practice, price_model=None):
    """Add the starter items the practice does not have. Returns how many.

    ``price_model``: the model of a migration, when one calls this.
    """
    if price_model is None:
        from .models import PriceItem as price_model
    have = {name.lower() for name in price_model.objects.filter(practice=practice).values_list('name', flat=True)}
    new = [
        price_model(practice=practice, name=name, category=category, price_inc_vat=Decimal(price))
        for category, items in STARTER_PRICES
        for name, price in items
        if name.lower() not in have
    ]
    price_model.objects.bulk_create(new)
    return len(new)
