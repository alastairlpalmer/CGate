"""A text box with its own suggestion list (a <datalist>).

It renders the list next to the box, so it works in any template: the
full breeding form, the covering pop-up and the generic pop-up form.
"""

from django import forms


class SuggestionInput(forms.TextInput):
    template_name = 'pedigree/widgets/suggestion_input.html'

    def __init__(self, attrs=None, *, list_id, options):
        attrs = {'autocomplete': 'off', **(attrs or {}), 'list': list_id}
        super().__init__(attrs)
        self.list_id = list_id
        self.options = options  # a callable, read at render time

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['widget']['list_id'] = self.list_id
        context['widget']['options'] = list(self.options())
        return context


def stallion_input(attrs=None):
    """The stallion box of a covering: suggests the stallions on record."""
    def options():
        from .services import stallion_suggestions
        return stallion_suggestions()
    return SuggestionInput(attrs, list_id='stallion-suggestions', options=options)
