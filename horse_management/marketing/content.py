"""The words and prices on the marketing pages, in one place to edit.

Prices: per horse, per month (docs/market/vet-app-market-model.xlsx,
Assumptions section 9, uses £10 per horse; the owner asked for about £5
to £10, rising with the modules a yard uses).
"""

from decimal import Decimal

# Each feature has an anchor on the Features page (#key) and is in one plan.
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

PLANS = [
    {
        'key': 'essentials', 'name': 'Essentials', 'price': Decimal('5'),
        'for': 'Run the yard day to day',
        'features': ['horses', 'locations', 'health', 'phone', 'security'],
    },
    {
        'key': 'professional', 'name': 'Professional', 'price': Decimal('7.50'),
        'for': 'Run the yard and its money',
        'features': ['invoicing', 'xero', 'farrier', 'feed'],
        'includes': 'Everything in Essentials, plus:',
        'popular': True,
    },
    {
        'key': 'complete', 'name': 'Complete', 'price': Decimal('10'),
        'for': 'For competition yards and studs',
        'features': ['training', 'breeding', 'cvet'],
        'includes': 'Everything in Professional, plus:',
    },
]

EXAMPLE_HORSES = 20


def money(amount):
    """£5, £7.50, £1,200: no pence when the amount is whole (marketing prices)."""
    return f'£{amount:,.0f}' if amount == amount.to_integral_value() else f'£{amount:,.2f}'


for _plan in PLANS:
    _plan['price_label'] = money(_plan['price'])
    _plan['example'] = money(_plan['price'] * EXAMPLE_HORSES)
    _plan['feature_list'] = [_BY_KEY[key] for key in _plan['features']]
    for _key in _plan['features']:
        _BY_KEY[_key]['plan'] = _plan['name']

# Over this many horses, or more than one yard: a price to suit.
LARGE_YARD_HORSES = 60

PRICING_FAQ = [
    ('How do you count horses?',
     'We count the horses at your yard each month. A horse that leaves stops counting the next month.'),
    ('Can I change plan?',
     'Yes. Move up or down at any time. The new price starts from your next monthly bill.'),
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
