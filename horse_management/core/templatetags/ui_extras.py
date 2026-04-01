"""Custom template filters for UI improvements."""

from datetime import date

from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def days_until(value):
    """Return days until a date. Negative = overdue."""
    if not value:
        return None
    today = timezone.now().date() if not isinstance(value, date) else date.today()
    if isinstance(value, date):
        return (value - date.today()).days
    return None


@register.filter
def due_label(value):
    """Return a human-readable label for a due date: 'Today', 'Tomorrow', 'in 5 days', '3 days overdue'."""
    if not value:
        return ''
    today = date.today()
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
    today = date.today()
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
