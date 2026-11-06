"""Shared form widgets."""
from django import forms


class MoneyInput(forms.NumberInput):
    """A number box with "£" inside it, on the left (UX plan F1).

    The pound sign is drawn, not typed, so the value stays a plain decimal.
    Template: templates/core/widgets/money_input.html.
    """

    template_name = 'core/widgets/money_input.html'
