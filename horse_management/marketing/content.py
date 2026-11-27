"""The words and prices on the marketing pages, in one place to edit.

Prices: one price per horse, per month, for each plan (Core, Pro), plus
add-ons that are not out yet. All prices exclude VAT. Background: the
market model (docs/market/vet-app-market-model.xlsx) uses £10 per horse.
"""

from decimal import ROUND_HALF_UP, Decimal

# The plans, their features in groups, and the add-ons. One price per horse,
# per month, excluding VAT. Pro = Core + maps and fields (+£1) + invoicing
# and Xero (+£1). Each group has an anchor on the Features page (#key).
CORE_GROUPS = [
    {'key': 'horses', 'icon': 'horses', 'title': 'Horses and owners',
     'line': 'Every horse, owner and co-owner in one record.',
     'points': ['Horse records and passports', 'Photos and documents', 'Owners and co-owner shares',
                'Arrivals and departures, with history']},
    {'key': 'health', 'icon': 'health', 'title': 'Health, farrier and vet',
     'line': 'Every health job, visit and due date for each horse.',
     'points': ['Vaccinations and worming', 'Farrier tracking', 'Vet tracking', 'Dentist and worm egg counts',
                'A due list for the whole yard', 'Email reminders to owners']},
    {'key': 'yard', 'icon': 'phone', 'title': 'Built for the yard',
     'line': 'Record a job on a phone, where it happens.',
     'points': ['Phone, tablet and computer', 'Quick find for any horse or owner', 'Feed stock and feeding',
                'A login for each member of staff, with set access']},
    {'key': 'support', 'icon': 'security', 'title': 'Onboarding and security',
     'line': 'We set you up, and keep your data safe.',
     'points': ['Onboarding included', 'Two-step sign-in', 'Daily backups', 'Export your data at any time']},
]
PRO_GROUPS = [
    {'key': 'maps', 'icon': 'locations', 'title': 'Maps, stables and fields',
     'line': 'See where every horse is, on an interactive map.',
     'points': ['Interactive map tracking of horses', 'Stables, barns and fields', 'Turnout and field rest',
                'Every move recorded, with history']},
    {'key': 'invoicing', 'icon': 'invoicing', 'title': 'Invoicing and payments',
     'line': 'Monthly livery invoices in one click, from what you record.',
     'points': ['Livery invoices in one click', 'Extra charges and co-owner splits',
                'Payments, credit notes and statements', 'Aged debtors and a finance overview']},
    {'key': 'xero', 'icon': 'xero', 'title': 'Xero accounting',
     'line': 'Invoices go to Xero. Payments come back and are matched.',
     'points': ['Invoices sent to Xero', 'Payments matched back', 'No typing an invoice twice']},
]

PLANS = [
    {'key': 'core', 'name': 'Core', 'price': Decimal('5'), 'for': 'Run the yard day to day',
     'groups': CORE_GROUPS},
    {'key': 'pro', 'name': 'Pro', 'price': Decimal('7'), 'for': 'Run the yard and its money',
     'includes': 'Everything in Core, plus', 'groups': PRO_GROUPS, 'recommended': True},
]
_PLANS = {plan['key']: plan for plan in PLANS}
DEFAULT_PLAN = 'pro'

# Modules that are not out yet. A yard that adds one helps us build it.
ADDONS = [
    {'key': 'training', 'icon': 'training', 'name': 'Training', 'price': Decimal('1.50'),
     'status': 'Coming next', 'call': 'Help develop it',
     'line': 'Plan and record the work for each horse and rider.',
     'points': ['A training log for each horse', 'Riders and work types', 'Work charged to the owner']},
    {'key': 'breeding', 'icon': 'breeding', 'name': 'Breeding', 'price': Decimal('0.50'),
     'status': 'Coming soon', 'call': 'Help shape it',
     'line': 'Coverings, scans, foaling and pedigree, for studs.',
     'points': ['Coverings and scans', 'Foaling dates', 'A pedigree tree for each horse']},
]
_ADDONS = {addon['key']: addon for addon in ADDONS}

# Groups and add-ons by key, for the Home and Features pages.
FEATURES = CORE_GROUPS + PRO_GROUPS
_BY_KEY = {group['key']: group for group in FEATURES}
HOME_FEATURES = [_BY_KEY[key] for key in ('horses', 'health', 'maps', 'invoicing', 'xero', 'support')]

EXAMPLE_HORSES = 20
# Over this many horses, or more than one yard: a price to suit.
LARGE_YARD_HORSES = 60

# Sign-up offers, for the first OFFER_MONTHS months. A yard that adds a
# module that is not out yet helps us build it, and gets the larger one.
OFFER_MONTHS = 6
OFFER_PERCENT = 25
PARTNER_PERCENT = 50


def money(amount):
    """£5, £7.50, £1,200: no pence when the amount is whole (marketing prices)."""
    amount = Decimal(amount).quantize(Decimal('0.01'))
    return f'£{amount:,.0f}' if amount == amount.to_integral_value() else f'£{amount:,.2f}'


for _item in PLANS + ADDONS:
    _item['price_label'] = money(_item['price'])


def quote(horses, plan='pro', addons=()):
    """The monthly price for ``horses`` on ``plan`` with ``addons``.

    The Pricing page's script (js/marketing.js) does the same sum; the
    server does it again for the quote sent with a Contact message.
    """
    try:
        horses = max(1, min(int(horses), 2000))
    except (TypeError, ValueError):
        horses = EXAMPLE_HORSES
    chosen_plan = _PLANS.get(plan) or _PLANS[DEFAULT_PLAN]
    chosen = [addon for addon in ADDONS if addon['key'] in set(addons)]
    per_horse = chosen_plan['price'] + sum(addon['price'] for addon in chosen)
    monthly = per_horse * horses
    partner = [addon['name'] for addon in chosen]
    percent = PARTNER_PERCENT if partner else OFFER_PERCENT
    offer_monthly = (monthly * (100 - percent) / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    saving = (monthly - offer_monthly) * OFFER_MONTHS
    return {
        'horses': horses, 'plan': chosen_plan, 'addons': chosen, 'addon_keys': [a['key'] for a in chosen],
        'names': [chosen_plan['name']] + [addon['name'] for addon in chosen],
        'per_horse': per_horse, 'monthly': monthly, 'percent': percent, 'partner': partner,
        'offer_monthly': offer_monthly, 'saving': saving, 'large': horses >= LARGE_YARD_HORSES,
        'monthly_label': money(monthly), 'per_horse_label': money(per_horse),
        'offer_label': money(offer_monthly), 'saving_label': money(saving),
    }


def quote_from_spec(spec):
    """A quote from "horses=22&plan=pro&addons=training" (the Contact link), or None."""
    from urllib.parse import parse_qs

    values = parse_qs(spec or '')
    if 'horses' not in values:
        return None
    addons = [key for key in (values.get('addons') or [''])[0].split(',') if key in _ADDONS]
    return quote(values['horses'][0], (values.get('plan') or [DEFAULT_PLAN])[0], addons)


def quote_summary(q):
    """One line for the team's email and the admin."""
    offer = f"{q['percent']}% off for {OFFER_MONTHS} months ({money(q['offer_monthly'])} a month)"
    if q['partner']:
        offer += f" as a build partner for {', '.join(q['partner'])}"
    return (f"{q['horses']} horses · {' + '.join(q['names'])} · {money(q['monthly'])} a month + VAT "
            f"({money(q['per_horse'])} per horse) · Offer: {offer}")


PRICING_FAQ = [
    ('How do you count horses?',
     'We count the horses at your yard each month. A horse that leaves stops counting the next month.'),
    ('Can I change plan?',
     'Yes. Move between Core and Pro, or add a module, at any time. The new price starts from your next monthly bill.'),
    ('Do the prices include VAT?',
     'No. All prices exclude VAT.'),
    ('How does the sign-up offer work?',
     f'Your first {OFFER_MONTHS} months are {OFFER_PERCENT}% off. If you add a module that is coming next or '
     f'coming soon, you help us build it: your first {OFFER_MONTHS} months are {PARTNER_PERCENT}% off instead.'),
    ('Is there a contract?',
     'No long contract. You pay month by month.'),
    ('Can I take my data with me?',
     'Yes. You can export your horses and owners at any time.'),
    ('Do you help us set up?',
     'Yes. Onboarding is included. We help you bring in your horses and owners, and show your staff how it works.'),
]

# Home page lists.
YARD_LINKS = [
    ('horses', 'horses', 'Horses and owners'),
    ('health', 'health', 'Health, farrier and vet'),
    ('maps', 'locations', 'Interactive horse maps'),
    ('invoicing', 'invoicing', 'Invoicing and Xero'),
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
    ('cvet', 'Vets and farriers', 'Every visit and treatment recorded against the horse.'),
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
