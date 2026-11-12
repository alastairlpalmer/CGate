"""Custom template filters for UI improvements."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

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


@register.filter
def stay_length(value):
    """How long a stay has run, from its start date: '1 week 4 days'.

    The horse list's preview pane prints this beside the arrival date, so
    "how long has this one been here" is answered without arithmetic.
    """
    if not value:
        return ''
    days = (timezone.localdate() - value).days
    if days < 0:
        return 'Not yet arrived'
    if days == 0:
        return 'Today'
    if days < 7:
        return f'{days} day{"s" if days != 1 else ""}'
    if days < 56:
        weeks, rest = divmod(days, 7)
        label = f'{weeks} week{"s" if weeks != 1 else ""}'
        if rest:
            label += f' {rest} day{"s" if rest != 1 else ""}'
        return label
    if days < 365:
        months, rest = divmod(days, 30)
        label = f'{months} month{"s" if months != 1 else ""}'
        weeks = rest // 7
        if weeks:
            label += f' {weeks} week{"s" if weeks != 1 else ""}'
        return label
    years, rest = divmod(days, 365)
    label = f'{years} year{"s" if years != 1 else ""}'
    months = rest // 30
    if months:
        label += f' {months} month{"s" if months != 1 else ""}'
    return label


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
    'scan': 'stethoscope',
    'foal_check': 'foal',
    'foal_passport': 'document',
    'document': 'document',
    'departure': 'gate',
    'departure_expected': 'gate',
    'invoice': 'pound',
    'feed': 'hay',
    'rate': 'pound',
    # activity log kinds
    'arrival': 'gate',
    'move': 'gate',
    'worming': 'wormer',
    'charge': 'pound',
    'payment': 'pound',
}


@register.filter
def kind_icon(kind):
    return KIND_ICONS.get(kind, 'horse')


@register.filter
def gbp(value):
    """``value|gbp`` → ``£1,234.50``. None and blanks render as £0.00.

    Rounds half up, the same as ``floatformat:2``, so a total never moves
    by a penny when a template changes from one to the other.
    """
    try:
        amount = Decimal(str(value) if isinstance(value, float) else (value or 0))
        amount = amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
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
        return name or 'Cgate'
    try:
        return cache.get_or_set(BUSINESS_NAME_CACHE_KEY, _load, 300)
    except Exception:  # noqa: BLE001 — a cache or DB hiccup must not take the shell down
        return 'Cgate'


@register.filter
def undo_url(message):
    """The restore URL on a message made with core.views._undo.undo_tag."""
    from core.views._undo import undo_url_from_tags
    return undo_url_from_tags(getattr(message, 'extra_tags', ''))


@register.inclusion_tag('horses/_stable_line.html', takes_context=True)
def stable_line(context, horse, compact=False):
    """``· Stable 7`` after a horse's location, with a way to change it.

    One query for the horse's open stay. For a page about one horse (its
    profile, the preview pane, the quick view); a list uses
    ``core.stables.annotate_stable`` instead.
    """
    from django.conf import settings

    stay = barn = None
    if settings.BARNS_ENABLED and horse is not None:
        from core.models import Placement
        from core.stables import open_stay

        stay = open_stay(horse)
        if stay is None:
            placement = (
                Placement.objects.filter(horse=horse, end_date__isnull=True)
                .select_related('location').first()
            )
            if placement is not None and placement.location.is_barn:
                barn = placement.location
        else:
            barn = stay.stable.location
    return {
        'horse': horse,
        'stay': stay,
        'barn': barn,
        'has_stables': bool(barn and (stay or barn.stables.filter(is_gap=False, in_use=True).exists())),
        'compact': compact,
        'feature_access': context.get('feature_access'),
        'request': context.get('request'),
    }

