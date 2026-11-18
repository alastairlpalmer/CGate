"""What the yard's Settings pages show about C-vet set-up.

The yard's templates load this library and get counts and a yes or no:
no clinical record, and no model of this app, reaches them.
"""

from django import template

from .. import setup

register = template.Library()


@register.simple_tag
def cvet_setup_status():
    """The practice, its number of logins and how many are left out. None when C-vet is off."""
    return setup.status()


@register.simple_tag
def cvet_login_gap(user):
    """True when this login has the Vet role and C-vet will refuse it."""
    return setup.login_gap(user)
