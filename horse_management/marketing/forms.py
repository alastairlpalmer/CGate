from django import forms

from .models import ContactMessage

INPUT = ('block w-full rounded-lg border border-sand bg-white px-4 py-3 text-base text-charcoal '
         'focus:border-forest focus:outline-none focus:ring-2 focus:ring-forest/30')


class ContactForm(forms.ModelForm):
    # A field people never see. A bot that fills every field fills this one.
    website = forms.CharField(required=False, label='Leave this empty')
    # "horses=22&modules=fields,invoicing" from the Pricing page. The price
    # is worked out again from it (content.quote_from_spec), never trusted.
    quote_spec = forms.CharField(required=False, max_length=200, widget=forms.HiddenInput)
    consent = forms.BooleanField(
        label='You can contact me about this message.',
        error_messages={'required': 'Tick this box, so we can reply to you.'},
    )

    class Meta:
        model = ContactMessage
        fields = ['topic', 'name', 'email', 'phone', 'yard_name', 'horses', 'message']
        labels = {'name': 'Your name', 'email': 'Email', 'phone': 'Phone (optional)',
                  'yard_name': 'Yard name (optional)', 'horses': 'Number of horses (optional)',
                  'message': 'Message (optional)', 'topic': 'What can we help with?'}
        widgets = {
            'topic': forms.RadioSelect,
            'message': forms.Textarea(attrs={'rows': 5}),
            'horses': forms.NumberInput(attrs={'min': 1, 'max': 2000, 'inputmode': 'numeric'}),
            'email': forms.EmailInput(attrs={'autocomplete': 'email'}),
            'name': forms.TextInput(attrs={'autocomplete': 'name'}),
            'phone': forms.TextInput(attrs={'autocomplete': 'tel', 'inputmode': 'tel'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name in ('topic', 'consent', 'quote_spec'):
                continue
            field.widget.attrs.setdefault('class', INPUT)
        self.fields['website'].widget.attrs.update({'tabindex': '-1', 'autocomplete': 'off'})
        self.fields['horses'].validators.append(_max_horses)
        self.fields['name'].error_messages['required'] = 'Enter your name.'
        self.fields['email'].error_messages.update({
            'required': 'Enter your email address.', 'invalid': 'Enter an email address like name@example.com.',
        })
        self.fields['horses'].error_messages['invalid'] = 'Enter a whole number.'

    def full_clean(self):
        super().full_clean()
        _mark_invalid(self)

    def is_bot(self):
        return bool(self.data.get('website'))


def _max_horses(value):
    if value is not None and value > 2000:
        raise forms.ValidationError('Enter a number of horses up to 2,000.')


def _mark_invalid(form):
    """Tie each error to its field for screen readers (the ids are in _field.html)."""
    for name in form.errors:
        if name in form.fields:
            attrs = form.fields[name].widget.attrs
            attrs['aria-invalid'] = 'true'
            attrs['aria-describedby'] = f'{form[name].id_for_label}-error'

