"""C-vet set-up, for the yard's administrator.

One page (``/c-vet/setup/``) that makes the practice, opens its link to
the yard, and shows which Vet logins are in it. Before this page, those
three were in the Django admin only, and nothing in C-gate said so: an
administrator gave a login the Vet role and the vet then saw "not set up
for C-vet" on every address.

The page also holds what the yard and the practice agree (who is billed,
what the yard takes from the practice) and what is printed on the
practice's bills (address, VAT number, the letters of a bill number, how
to pay). With these, an administrator needs the Django admin for nothing
before the first visit.

The page holds set-up records only: the practice's details, its link, its
logins. It reads no clinical note, price or bill, so the rule of
docs/C_VET_PLAN.md, section 3 stays whole: the administrator decides
who works for the practice and does not read what they write.
"""

import re

from django import forms
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from core.permissions import feature_required

from .access import cvet_enabled, new_member, vet_logins
from billing.models import ServiceProvider

from .models import Practice, PracticeMember, PracticeYardLink
from .starter_prices import add_starter_prices


def login_label(user):
    name = user.get_full_name()
    address = user.email or user.username
    return f'{name} ({address})' if name else address


class SetupForm(forms.Form):
    name = forms.CharField(
        max_length=200, label='Practice name',
        widget=forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'off'}),
    )
    link_open = forms.BooleanField(
        required=False, label="The practice can see this yard's horses",
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )
    logins = forms.ModelMultipleChoiceField(
        queryset=None, required=False, label='Logins in the practice',
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-checkbox'}),
    )

    # What the yard and the practice agree. Kept on the link.
    bill_to = forms.ChoiceField(
        choices=PracticeYardLink.BillTo.choices, label='The practice bills',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    sends_charges = forms.BooleanField(
        required=False, label="Charges: each horse's part of a bill becomes a charge in C-gate",
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )
    sends_vaccination_dates = forms.BooleanField(
        required=False, label='Vaccination dates and next due dates',
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )
    sends_follow_up_dates = forms.BooleanField(
        required=False, label='Follow-up dates, with no reason',
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )
    service_provider = forms.ModelChoiceField(
        queryset=None, required=False, label="The practice in the yard's list of service providers",
        empty_label='Not in the list', widget=forms.Select(attrs={'class': 'form-select'}),
    )

    # What is printed on the practice's bills. Kept on the practice.
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={'class': 'form-input', 'autocomplete': 'off'}))
    phone = forms.CharField(required=False, max_length=50, widget=forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'off'}))
    address = forms.CharField(required=False, widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}))
    vat_number = forms.CharField(
        required=False, max_length=30, label='VAT number',
        widget=forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'off'}),
    )
    bill_prefix = forms.CharField(
        required=False, max_length=10, label='Letters before a bill number',
        widget=forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'off', 'placeholder': 'CV'}),
    )
    bank_details = forms.CharField(
        required=False, label='How to pay',
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
    )

    PRACTICE_FIELDS = ('email', 'phone', 'address', 'vat_number', 'bill_prefix', 'bank_details')
    LINK_FIELDS = ('bill_to', 'sends_charges', 'sends_vaccination_dates', 'sends_follow_up_dates', 'service_provider')

    def __init__(self, *args, practice=None, link=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.practice = practice
        self.link = link
        self.fields['logins'].queryset = vet_logins()
        self.fields['logins'].label_from_instance = login_label
        # The yard's vets. One that is another practice's is not offered.
        taken = PracticeYardLink.objects.exclude(service_provider=None)
        if link is not None:
            taken = taken.exclude(pk=link.pk)
        self.fields['service_provider'].queryset = (
            ServiceProvider.objects.filter(provider_type='vet')
            .exclude(pk__in=taken.values('service_provider'))
            .order_by('name')
        )

    def clean_bill_prefix(self):
        prefix = self.cleaned_data['bill_prefix'].strip().upper()
        if prefix and not re.fullmatch(r'[A-Z0-9]+', prefix):
            raise forms.ValidationError('Use letters and numbers only, for example HV.')
        return prefix

    def clean_address(self):
        return self.cleaned_data['address'].strip()

    def clean_bank_details(self):
        return self.cleaned_data['bank_details'].strip()

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        others = Practice.objects.filter(name__iexact=name)
        if self.practice is not None:
            others = others.exclude(pk=self.practice.pk)
        if others.exists():
            raise forms.ValidationError('There is a practice with this name already. Use a different name.')
        return name


def members_by_user(logins):
    return {
        member.user_id: member
        for member in PracticeMember.objects.filter(user__in=logins).select_related('practice')
    }


def can_open(user, member, practices):
    """True if this Vet login gets into C-vet: the test the gate makes.

    ``practices`` is the active practices (two at most are needed). A
    login with no membership row joins the practice when there is one.
    """
    if member is None:
        return len(practices) == 1
    return member.is_active and member.practice.is_active and member.is_approved


def status():
    """What the yard's Settings page shows about C-vet. None when it is off."""
    if not settings.CVET_ENABLED:
        return None
    practices = list(Practice.objects.filter(is_active=True)[:2])
    logins = list(vet_logins())
    members = members_by_user(logins)
    practice = practices[0] if len(practices) == 1 else None
    link = PracticeYardLink.objects.filter(practice=practice).first() if practice else None
    opened = [user for user in logins if can_open(user, members.get(user.pk), practices)]
    waiting = sum(
        1 for user in logins
        if (m := members.get(user.pk)) is not None and m.is_active and not m.is_approved
    )
    return {
        'waiting': waiting,
        'practice': practice,
        'many': len(practices) > 1,
        'link_open': bool(link and link.is_open),
        'logins': len(opened),
        'left_out': len(logins) - len(opened) - waiting,
        'test_mode': settings.CVET_TEST_MODE,
    }


def login_gap(user):
    """True when ``user`` is a Vet login that C-vet will refuse."""
    if not settings.CVET_ENABLED or user is None or not user.is_active:
        return False
    if not vet_logins().filter(pk=user.pk).exists():
        return False
    practices = list(Practice.objects.filter(is_active=True)[:2])
    return not can_open(user, members_by_user([user]).get(user.pk), practices)


def _link_initial(link):
    """The agreement as it is, or as a new link starts (the model's defaults)."""
    source = link if link is not None else PracticeYardLink()
    data = {name: getattr(source, name) for name in SetupForm.LINK_FIELDS if name != 'service_provider'}
    data['service_provider'] = link.service_provider_id if link is not None else None
    return data


def _initial(practice, link, logins, members):
    if practice is None:
        # A first set-up: the link open and every Vet login in.
        return {'name': '', 'link_open': True, 'logins': [user.pk for user in logins], **_link_initial(None)}
    chosen = []
    for user in logins:
        member = members.get(user.pk)
        # No row yet: the login joins at its first sign-in, so it is in.
        if member is None or (member.is_active and member.practice_id == practice.pk):
            chosen.append(user.pk)
    return {
        'name': practice.name, 'link_open': bool(link and link.is_open), 'logins': chosen,
        **{name: getattr(practice, name) for name in SetupForm.PRACTICE_FIELDS},
        **_link_initial(link),
    }


@transaction.atomic
def save(form, practice):
    """Make the records say what the form says. Returns the practice."""
    data = form.cleaned_data
    details = {name: data[name] for name in SetupForm.PRACTICE_FIELDS}
    if practice is None:
        practice = Practice.objects.create(name=data['name'], **details)
        # The item list is not empty on the first day.
        add_starter_prices(practice)
    else:
        changed = [name for name, value in {'name': data['name'], **details}.items() if getattr(practice, name) != value]
        if changed:
            for name in changed:
                setattr(practice, name, data[name])
            practice.save(update_fields=[*changed, 'updated_at'])

    today = timezone.localdate()
    agreement = {name: data[name] for name in SetupForm.LINK_FIELDS}
    link = PracticeYardLink.objects.filter(practice=practice).first()
    if link is None:
        # No link and none asked for: there is no agreement to keep yet.
        if data['link_open']:
            PracticeYardLink.objects.create(practice=practice, **agreement)
    else:
        changed = [name for name, value in agreement.items() if getattr(link, name) != value]
        for name in changed:
            setattr(link, name, agreement[name])
        if data['link_open'] and not link.is_open:
            link.started_on = min(link.started_on, today)
            link.ended_on = None
            changed += ['started_on', 'ended_on']
        elif not data['link_open'] and link.is_open:
            link.ended_on = today
            changed.append('ended_on')
        if changed:
            link.save(update_fields=changed)

    chosen = {user.pk for user in data['logins']}
    logins = list(vet_logins())
    members = members_by_user(logins)
    for user in logins:
        member = members.get(user.pk)
        if user.pk in chosen:
            # The administrator can put a login in, but a member of the
            # practice lets it read (cvet.access.approve). A login put
            # back in after it was taken out waits again.
            if member is None:
                new_member(practice, user)
            elif not member.is_active or member.practice_id != practice.pk:
                first = not PracticeMember.objects.filter(
                    practice=practice, is_active=True, approved_at__isnull=False,
                ).exists()
                member.practice = practice
                member.is_active = True
                member.approved_at = timezone.now() if first else None
                member.approved_by = None
                member.save(update_fields=['practice', 'is_active', 'approved_at', 'approved_by'])
        elif member is None:
            # An inactive row, so that the login does not join by itself
            # at its next sign-in.
            PracticeMember.objects.create(practice=practice, user=user, is_active=False)
        elif member.is_active and member.practice_id == practice.pk:
            member.is_active = False
            member.save(update_fields=['is_active'])
    return practice


@cvet_enabled
@feature_required('users')
def setup(request):
    """The set-up page. For a login with Full on Users & Roles."""
    practices = list(Practice.objects.filter(is_active=True)[:2])
    context = {'test_mode': settings.CVET_TEST_MODE, 'address': f'{request.get_host()}/c-vet/'}
    if len(practices) > 1:
        # More than one practice: a login must be put in the right one by
        # hand. That is not a case this yard has; the admin covers it.
        return render(request, 'cvet/setup.html', {**context, 'many': True})

    practice = practices[0] if practices else None
    link = PracticeYardLink.objects.filter(practice=practice).first() if practice else None
    logins = list(vet_logins())
    if request.method == 'POST':
        form = SetupForm(request.POST, practice=practice, link=link)
        if form.is_valid():
            save(form, practice)
            messages.success(request, 'C-vet set-up saved.')
            return redirect('cvet:setup')
    else:
        form = SetupForm(practice=practice, link=link, initial=_initial(practice, link, logins, members_by_user(logins)))
    return render(request, 'cvet/setup.html', {
        **context, 'form': form, 'practice': practice, 'has_logins': bool(logins),
    })
