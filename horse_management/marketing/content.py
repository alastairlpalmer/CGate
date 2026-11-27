"""The words and prices on the marketing pages, in one place to edit.

Prices: per horse, per month (docs/market/vet-app-market-model.xlsx,
Assumptions section 9, uses £10 per horse; the owner asked for about £5
to £10, rising with the modules a yard uses). All prices exclude VAT.
"""

from decimal import ROUND_HALF_UP, Decimal

# Each feature has an anchor on the Features page (#key) and is in one module.
FEATURES = [
    {
        'key': 'horses', 'title': 'Horses and owners',
        'line': 'Every horse, owner and co-owner share in one record.',
        'points': [
            'Passport details, photos and documents for each horse',
            'Owners, co-owners and the share each one pays',
            'Arrivals and departures, with the full history kept',
        ],
    },
    {
        'key': 'locations', 'title': 'Stables, fields and maps',
        'line': 'See which horse is in which stable or field.',
        'points': [
            'Barns, stables and fields, with moves recorded',
            'Turnout, and rest periods for each field',
            'A map of your fields drawn on satellite images',
        ],
    },
    {
        'key': 'health', 'title': 'Health records and reminders',
        'line': 'Vaccinations, worming, farrier, dentist and vet visits, with reminders.',
        'points': [
            'A due list for the whole yard, so nothing is missed',
            'Email reminders to owners before a date is due',
            'Worm egg counts, conditions and vet visit notes',
        ],
    },
    {
        'key': 'phone', 'title': 'Works on a phone in the yard',
        'line': 'Add Cgate to the home screen. Record a job where it happens.',
        'points': [
            'Large buttons, made for use with one hand',
            'Quick find for any horse or owner',
            'A login for each member of staff, with the access you choose',
        ],
    },
    {
        'key': 'security', 'title': 'Security and backups',
        'line': 'Your data is safe, and it stays yours.',
        'points': [
            'Two-step sign-in',
            'Daily backups',
            'Export your horses and owners at any time',
        ],
    },
    {
        'key': 'invoicing', 'title': 'Invoicing and payments',
        'line': 'Monthly livery invoices in one click, from the stays you record.',
        'points': [
            'Livery, extra charges and co-owner splits on each invoice',
            'Payments, credit notes, statements and aged debtors',
            'Invoices sent to owners by email as a PDF',
        ],
    },
    {
        'key': 'xero', 'title': 'Xero accounting',
        'line': 'Invoices go to Xero. Payments come back and are matched.',
        'points': [
            'No typing the same invoice twice',
            'Payments made in Xero are recorded on the invoice in Cgate',
        ],
    },
    {
        'key': 'farrier', 'title': 'Farrier visit page',
        'line': 'The farrier scans a QR code at the yard and logs the visit.',
        'points': [
            'No account set-up for the farrier',
            'Their prices, and today\'s horses, on their phone',
            'Visits go to the yard to check, then onto invoices',
        ],
    },
    {
        'key': 'feed', 'title': 'Feed and costs',
        'line': 'Feed stock, feeding out and yard costs in one place.',
        'points': [
            'Stock levels, with a warning before you run out',
            'Yard costs and a priced list of services',
        ],
    },
    {
        'key': 'training', 'title': 'Training and exercise',
        'line': 'Plan and record the work for each horse.',
        'points': [
            'A training log for each horse and each rider',
            'Work charged to the owner when you choose',
        ],
    },
    {
        'key': 'breeding', 'title': 'Breeding and pedigree',
        'line': 'Breeding, foaling and pedigree records for studs.',
        'points': [
            'Coverings, scans and foaling dates',
            'A pedigree tree for each horse',
        ],
    },
    {
        'key': 'cvet', 'title': 'C-vet: the vet\'s app',
        'line': 'Your vets see the horses they treat, and share records with the yard.',
        'points': [
            'Visit notes and charges recorded at the stable door',
            'Health records go straight onto the horse in Cgate',
        ],
    },
]

_BY_KEY = {feature['key']: feature for feature in FEATURES}

# The six cards on the Home page.
HOME_FEATURES = [_BY_KEY[key] for key in ('horses', 'health', 'invoicing', 'xero', 'phone', 'farrier')]

EXAMPLE_HORSES = 20
# Over this many horses, or more than one yard: a price to suit.
LARGE_YARD_HORSES = 60

# Pricing is per horse, per month, and excludes VAT. Yard CRM is the base;
# every other module adds to the price per horse. All modules: £10.
MODULES = [
    {
        'key': 'core', 'name': 'Yard CRM', 'price': Decimal('5'), 'required': True,
        'line': 'Horses, owners, health records, feed and the phone app.',
        'features': ['horses', 'health', 'phone', 'feed', 'security'],
    },
    {
        'key': 'fields', 'name': 'Field management', 'price': Decimal('1'),
        'line': 'Stables, fields, turnout and maps.',
        'features': ['locations'],
    },
    {
        'key': 'invoicing', 'name': 'Invoicing and Xero', 'price': Decimal('2'),
        'line': 'Monthly livery invoices, payments and Xero.',
        'features': ['invoicing', 'xero'],
    },
    {
        'key': 'providers', 'name': 'Farrier and vet', 'price': Decimal('1'),
        'line': 'The farrier visit page and C-vet, the vet\'s app.',
        'features': ['farrier', 'cvet'],
    },
    {
        'key': 'training', 'name': 'Training', 'price': Decimal('0.50'),
        'line': 'Plan and record the work for each horse.',
        'features': ['training'],
    },
    {
        'key': 'breeding', 'name': 'Breeding', 'price': Decimal('0.50'), 'soon': True,
        'line': 'Coverings, foaling and pedigree, for studs.',
        'features': ['breeding'],
    },
]
_MODULES = {module['key']: module for module in MODULES}
# Ticked when the Pricing page opens.
DEFAULT_MODULES = ('core', 'fields', 'invoicing')

# Sign-up offers, for the first OFFER_MONTHS months. A yard that picks a
# module that is coming soon helps us build it, and gets the larger one.
OFFER_MONTHS = 6
OFFER_PERCENT = 25
PARTNER_PERCENT = 50


def money(amount):
    """£5, £7.50, £1,200: no pence when the amount is whole (marketing prices)."""
    amount = Decimal(amount).quantize(Decimal('0.01'))
    return f'£{amount:,.0f}' if amount == amount.to_integral_value() else f'£{amount:,.2f}'


for _module in MODULES:
    _module['price_label'] = money(_module['price'])
    _module['feature_list'] = [_BY_KEY[key] for key in _module['features']]
    for _key in _module['features']:
        _BY_KEY[_key]['module'] = _module


def quote(horses, keys):
    """The monthly price for ``horses`` with the modules in ``keys`` (Yard CRM always).

    The Pricing page's script (js/marketing.js) does the same sum; the
    server does it again for the quote sent with a Contact message.
    """
    try:
        horses = max(1, min(int(horses), 2000))
    except (TypeError, ValueError):
        horses = EXAMPLE_HORSES
    chosen = [module for module in MODULES if module.get('required') or module['key'] in set(keys)]
    per_horse = sum(module['price'] for module in chosen)
    monthly = per_horse * horses
    soon = [module['name'] for module in chosen if module.get('soon')]
    percent = PARTNER_PERCENT if soon else OFFER_PERCENT
    offer_monthly = (monthly * (100 - percent) / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    saving = (monthly - offer_monthly) * OFFER_MONTHS
    return {
        'monthly_label': money(monthly), 'per_horse_label': money(per_horse),
        'offer_label': money(offer_monthly), 'saving_label': money(saving),
        'horses': horses, 'modules': chosen, 'keys': [module['key'] for module in chosen],
        'per_horse': per_horse, 'monthly': monthly, 'percent': percent, 'partner': soon,
        'offer_monthly': offer_monthly, 'saving': saving,
        'large': horses >= LARGE_YARD_HORSES,
    }


def quote_from_spec(spec):
    """A quote from "horses=22&modules=fields,invoicing" (the Contact link), or None."""
    from urllib.parse import parse_qs

    values = parse_qs(spec or '')
    if 'horses' not in values:
        return None
    keys = [key for key in (values.get('modules') or [''])[0].split(',') if key in _MODULES]
    return quote(values['horses'][0], keys)


def quote_summary(q):
    """One line for the team's email and the admin."""
    names = ', '.join(module['name'] for module in q['modules'])
    offer = f"{q['percent']}% off for {OFFER_MONTHS} months ({money(q['offer_monthly'])} a month)"
    if q['partner']:
        offer += f" as a build partner for {', '.join(q['partner'])}"
    return (f"{q['horses']} horses · {names} · {money(q['monthly'])} a month + VAT "
            f"({money(q['per_horse'])} per horse) · Offer: {offer}")


PRICING_FAQ = [
    ('How do you count horses?',
     'We count the horses at your yard each month. A horse that leaves stops counting the next month.'),
    ('Can I change my modules?',
     'Yes. Add or remove modules at any time. The new price starts from your next monthly bill.'),
    ('Do the prices include VAT?',
     'No. All prices exclude VAT.'),
    ('How does the sign-up offer work?',
     f'Your first {OFFER_MONTHS} months are {OFFER_PERCENT}% off. If you choose a module that is coming soon, '
     f'you help us build it: your first {OFFER_MONTHS} months are {PARTNER_PERCENT}% off instead.'),
    ('Is there a contract?',
     'No long contract. You pay month by month.'),
    ('Can I take my data with me?',
     'Yes. You can export your horses and owners at any time.'),
    ('Do you help us set up?',
     'Yes. We help you bring in your horses and owners, and show your staff how it works.'),
]

# Home page lists.
YARD_LINKS = [
    ('horses', 'Horses and owners'),
    ('invoicing', 'Invoicing and Xero'),
    ('health', 'Health records'),
    ('cvet', 'Vets and farriers'),
]

STEPS = [
    ('Set up your yard', 'Add your horses, owners, stables and livery rates.'),
    ('Record the day', 'Moves, health jobs, visits and extras, on a phone in the yard.'),
    ('Send the invoices', 'Made each month from what you recorded, and sent to owners and to Xero.'),
]

AUDIENCES = [
    ('manager', 'Yard managers', 'The whole yard on one screen: horses, stables, money and what is due.'),
    ('staff', 'Staff', 'Their own login, with only the parts they need.'),
    ('owners', 'Owners', 'Clear invoices, and reminders before health dates are due.'),
    ('cvet', 'Vets and farriers', 'Their own phone page to record visits at the stable door.'),
]

BELIEFS = [
    ('invoicing', 'Record it once', 'A stay, a visit or a charge goes in once. The invoices, the reminders and the records follow from it.'),
    ('phone', 'Simple on a phone', 'The work happens in the yard, not at a desk. Cgate is made for one hand and a busy day.'),
    ('owners', 'Everyone connected', 'Yard, owners, vets and farriers see the same facts about each horse.'),
    ('security', 'Your data is yours', 'Safe, backed up every day, and yours to take with you.'),
]

CONTACT_STEPS = [
    ('We read your message', 'A person reads every message. No automatic sales emails.'),
    ('We reply by email', 'We answer your question, or find a time for a demo.'),
    ('You see Cgate on your yard', 'We show you the app with your own horses and owners.'),
]
