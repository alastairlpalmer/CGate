"""The help centre's guides (docs/SIDEBAR_AND_NOTIFICATIONS_PLAN.md, WP5).

Each guide is a template fragment in templates/help/articles/<slug>.html.
This list gives each one its title, a one-line summary, search words, and
the area a person's role must see for the guide to show: a guide about
Invoices is no help to a role that cannot open Invoices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape

from django.template.loader import render_to_string

from .permissions import has_feature_access

SUPPORT_EMAIL = 'help@c-gate.app'


@dataclass(frozen=True)
class Guide:
    slug: str
    title: str
    summary: str
    group: str
    # The person must see at least one of these areas. Empty: everyone.
    features: tuple = ()
    level: str = 'view'
    keywords: tuple = field(default_factory=tuple)

    @property
    def template(self):
        return f'help/articles/{self.slug}.html'

    def visible_to(self, user):
        if not self.features:
            return True
        return any(has_feature_access(user, f, self.level) for f in self.features)


GROUPS = ('Start here', 'The yard', 'Health', 'Money', 'Administration')

GUIDES = [
    Guide('getting-started', 'Getting started', 'The sidebar, the dashboard and its calendar, and the Add button.',
          'Start here', keywords=('dashboard', 'calendar', 'sidebar', 'menu', 'add', 'phone', 'collapse')),
    Guide('search', 'Search', 'Find a horse, owner, location, invoice or health record from any page.',
          'Start here', keywords=('find', 'look up', 'microchip', 'passport number', 'shortcut')),
    Guide('notifications', 'Notifications', 'What needs doing, horse movements and money, in one place.',
          'Start here', keywords=('needs action', 'bell', 'badge', 'email', 'overdue', 'dismiss', 'reminder')),
    Guide('profile-and-security', 'Your profile and security', 'Your name, email, phone, photo, password and two-step sign-in.',
          'Start here', keywords=('account', 'password', 'two-step', 'mfa', 'authenticator', 'photo', 'email', 'google')),
    Guide('health', 'Health', 'Vaccinations, worming, egg counts and vet visits.',
          'Health', features=('health',), keywords=('vaccination', 'flu', 'tetanus', 'worming', 'egg count', 'vet')),
    Guide('farrier', 'Farrier', 'Farrier visits and when each horse is next due.',
          'Health', features=('health', 'charges'), keywords=('shoes', 'trim', 'farrier visit')),
    Guide('charges-and-invoices', 'Charges and invoices', 'Extra charges, monthly invoices, payments and overdue invoices.',
          'Money', features=('invoices', 'charges'), keywords=('bill', 'invoice', 'payment', 'paid', 'overdue', 'charge', 'debtors')),
    Guide('costs-and-finances', 'Costs and finances', 'Yard costs and the Finances overview.',
          'Money', features=('costs', 'finances'), keywords=('cost', 'expense', 'profit', 'finances')),
    Guide('xero', 'Xero', 'Connect Xero, send invoices and keep payments in step.',
          'Money', features=('xero',), keywords=('accounting', 'sync', 'connect', 'reconnect')),
    Guide('users-and-roles', 'Users and roles', 'Invite people and choose what each role can see and change.',
          'Administration', features=('users',), level='full',
          keywords=('invite', 'user', 'role', 'access', 'permission', 'administrator', 'deactivate')),
]

GUIDES_BY_SLUG = {g.slug: g for g in GUIDES}


def guides_for(user):
    return [g for g in GUIDES if g.visible_to(user)]


def grouped_for(user):
    shown = guides_for(user)
    return [
        {'name': name, 'guides': [g for g in shown if g.group == name]}
        for name in GROUPS
        if any(g.group == name for g in shown)
    ]


_TEXT_CACHE: dict[str, str] = {}
_TAGS = re.compile(r'<[^>]+>')


def _text(guide):
    """The guide's words, lower case, for search. Templates do not change
    while the app runs, so each is rendered once."""
    if guide.slug not in _TEXT_CACHE:
        try:
            html = render_to_string(guide.template)
        except Exception:
            html = ''
        _TEXT_CACHE[guide.slug] = ' '.join(unescape(_TAGS.sub(' ', html)).split()).lower()
    return _TEXT_CACHE[guide.slug]


def search(user, query):
    """The guides that match every word of ``query``, best match first:
    a match in the title, then in the summary or search words, then in
    the text."""
    words = [w for w in query.lower().split() if w]
    if not words:
        return []
    scored = []
    for guide in guides_for(user):
        title = guide.title.lower()
        meta = ' '.join((guide.summary, *guide.keywords)).lower()
        body = _text(guide)
        if not all(w in title or w in meta or w in body for w in words):
            continue
        score = sum(3 if w in title else 2 if w in meta else 1 for w in words)
        scored.append((-score, GUIDES.index(guide), guide))
    return [guide for _, _, guide in sorted(scored)]
