"""Custom template filters for UI improvements."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django import template
from django.core.cache import cache
from django.utils import timezone

register = template.Library()


@register.filter
def days_until(value):
    """Return days until a date. Negative = overdue."""
    if not value:
        return None
    if isinstance(value, date):
        return (value - timezone.localdate()).days
    return None


@register.filter
def due_label(value):
    """Return a human-readable label for a due date: 'Today', 'Tomorrow', 'in 5 days', '3 days overdue'."""
    if not value:
        return ''
    today = timezone.localdate()
    delta = (value - today).days
    if delta < -1:
        return f'{abs(delta)} days overdue'
    elif delta == -1:
        return '1 day overdue'
    elif delta == 0:
        return 'Today'
    elif delta == 1:
        return 'Tomorrow'
    elif delta <= 14:
        return f'in {delta} days'
    elif delta <= 60:
        weeks = delta // 7
        return f'in {weeks} week{"s" if weeks != 1 else ""}'
    else:
        months = delta // 30
        return f'in {months} month{"s" if months != 1 else ""}'


@register.filter
def ago_label(value):
    """Return a human-readable label for a past date: 'Today', 'Yesterday', '5 days ago'."""
    if not value:
        return ''
    today = timezone.localdate()
    delta = (today - value).days
    if delta == 0:
        return 'Today'
    elif delta == 1:
        return 'Yesterday'
    elif delta <= 14:
        return f'{delta} days ago'
    elif delta <= 60:
        weeks = delta // 7
        return f'{weeks} week{"s" if weeks != 1 else ""} ago'
    elif delta <= 365:
        months = delta // 30
        return f'{months} month{"s" if months != 1 else ""} ago'
    else:
        years = delta // 365
        return f'{years} year{"s" if years != 1 else ""} ago'


# ── Dashboard and identity helpers ─────────────────────────────────────────

# Coat colour → (fill, text). A horse with no photo gets its coat colour, not
# a grey initial; compact lists get a 6px dot. Unknown or "other" falls back
# to sage so the palette never breaks.
COAT = {
    'bay': ('#7A4A2B', '#FFFFFF'),
    'chestnut': ('#A0522D', '#FFFFFF'),
    'grey': ('#B7B7B7', '#2C2C2C'),
    'black': ('#2C2C2C', '#FFFFFF'),
    'brown': ('#5C3B22', '#FFFFFF'),
    'palomino': ('#D9B36A', '#2C2C2C'),
    'skewbald': ('#8B5A3C', '#FFFFFF'),
    'piebald': ('#4A4A4A', '#FFFFFF'),
    'roan': ('#A88F8A', '#2C2C2C'),
    'dun': ('#C2A46B', '#2C2C2C'),
    'cream': ('#E8DCC2', '#2C2C2C'),
}
COAT_DEFAULT = ('#9CB2B8', '#2C2C2C')


@register.filter
def coat_colour(code):
    """``horse.color|coat_colour`` → ``(fill, text)`` hex pair."""
    return COAT.get(code or '', COAT_DEFAULT)


# Attention item kind → symbol id in templates/includes/_icons.html.
KIND_ICONS = {
    'vaccination': 'syringe',
    'ehv': 'syringe',
    'farrier': 'horseshoe',
    'vet': 'stethoscope',
    'egg_count': 'egg',
    'foal': 'foal',
    'document': 'document',
    'departure': 'gate',
    'departure_expected': 'gate',
    'invoice': 'pound',
    'feed': 'hay',
    # activity log kinds
    'arrival': 'gate',
    'move': 'gate',
    'worming': 'egg',
    'charge': 'pound',
    'payment': 'pound',
}


@register.filter
def kind_icon(kind):
    return KIND_ICONS.get(kind, 'horse')


@register.filter
def gbp(value):
    """``value|gbp`` → ``£1,234.50``. None and blanks render as £0.00."""
    try:
        amount = Decimal(value or 0)
    except (InvalidOperation, TypeError, ValueError):
        return value
    sign = '-' if amount < 0 else ''
    return f'{sign}£{abs(amount):,.2f}'


BUSINESS_NAME_CACHE_KEY = 'business_name'


@register.simple_tag
def business_name():
    """The yard's own name for the sidebar and top bar, falling back to the
    product name. Cached; core.signals clears it when Business Settings
    save."""
    def _load():
        from core.models import BusinessSettings
        name = (BusinessSettings.get_settings().business_name or '').strip()
        return name or 'Yardway'
    try:
        return cache.get_or_set(BUSINESS_NAME_CACHE_KEY, _load, 300)
    except Exception:  # noqa: BLE001 — a cache or DB hiccup must not take the shell down
        return 'Yardway'
