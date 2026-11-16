"""C-vet set-up, for the yard's administrator.

One page (``/c-vet/setup/``) that makes the practice, opens its link to
the yard, and shows which Vet logins are in it. Before this page, those
three were in the Django admin only, and nothing in C-gate said so: an
administrator gave a login the Vet role and the vet then saw "not set up
for C-vet" on every address.

The page holds set-up records only: the practice's name, its link, its
logins. It reads no clinical note, price or bill, so the rule of
docs/C_VET_PLAN.md, section 3 stays whole: the administrator decides
who works for the practice and does not read what they write.
"""

from django import forms
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from core.permissions import feature_required

from .access import cvet_enabled, vet_logins
from .models import Practice, PracticeMember, PracticeYardLink


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

    def __init__(self, *args, practice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.practice = practice
        self.fields['logins'].queryset = vet_logins()
        self.fields['logins'].label_from_instance = login_label

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
    return member.is_active and member.practice.is_active


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
    return {
        'practice': practice,
        'many': len(practices) > 1,
        'link_open': bool(link and link.is_open),
        'logins': len(opened),
        'left_out': len(logins) - len(opened),
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


def _initial(practice, link, logins, members):
    if practice is None:
        # A first set-up: the link open and every Vet login in.
        return {'name': '', 'link_open': True, 'logins': [user.pk for user in logins]}
    chosen = []
    for user in logins:
        member = members.get(user.pk)
        # No row yet: the login joins at its first sign-in, so it is in.
        if member is None or (member.is_active and member.practice_id == practice.pk):
            chosen.append(user.pk)
    return {'name': practice.name, 'link_open': bool(link and link.is_open), 'logins': chosen}


@transaction.atomic
def save(form, practice):
    """Make the records say what the form says. Returns the practice."""
    data = form.cleaned_data
    if practice is None:
        practice = Practice.objects.create(name=data['name'])
    elif practice.name != data['name']:
        practice.name = data['name']
        practice.save(update_fields=['name', 'updated_at'])

    today = timezone.localdate()
    link = PracticeYardLink.objects.filter(practice=practice).first()
    if data['link_open']:
        if link is None:
            PracticeYardLink.objects.create(practice=practice)
        elif not link.is_open:
            link.started_on = min(link.started_on, today)
            link.ended_on = None
            link.save(update_fields=['started_on', 'ended_on'])
    elif link is not None and link.is_open:
        link.ended_on = today
        link.save(update_fields=['ended_on'])

    chosen = {user.pk for user in data['logins']}
    logins = list(vet_logins())
    members = members_by_user(logins)
    for user in logins:
        member = members.get(user.pk)
        if user.pk in chosen:
            if member is None:
                PracticeMember.objects.create(practice=practice, user=user)
            elif not member.is_active or member.practice_id != practice.pk:
                member.practice = practice
                member.is_active = True
                member.save(update_fields=['practice', 'is_active'])
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
        form = SetupForm(request.POST, practice=practice)
        if form.is_valid():
            save(form, practice)
            messages.success(request, 'C-vet set-up saved.')
            return redirect('cvet:setup')
    else:
        form = SetupForm(practice=practice, initial=_initial(practice, link, logins, members_by_user(logins)))
    return render(request, 'cvet/setup.html', {
        **context, 'form': form, 'practice': practice, 'has_logins': bool(logins),
    })
