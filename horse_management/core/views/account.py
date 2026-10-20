"""The signed-in user's own account security, and the password reset flow.

Replaces two of Django's stock auth views (mounted ahead of
``django.contrib.auth.urls`` with the same URL names):

* password reset request — rate limited, because the form is public;
* password reset confirm — also clears a sign-in lockout, so a person
  locked out by someone guessing at their address can always get back in
  through their own mailbox.
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST

from .. import audit, ratelimit
from ..account_security import clear_lockout, sign_out_everywhere
from ..models import AuditEvent

logger = logging.getLogger(__name__)


class RateLimitedPasswordResetView(auth_views.PasswordResetView):
    def form_valid(self, form):
        email = form.cleaned_data['email'].strip().lower()
        ip = self.request.META.get('REMOTE_ADDR', '')
        window = settings.PASSWORD_RESET_LIMIT_WINDOW
        within_limit = ratelimit.allow(
            'password-reset-email', email, settings.PASSWORD_RESET_LIMIT_PER_EMAIL, window,
        ) and ratelimit.allow(
            'password-reset-ip', ip, settings.PASSWORD_RESET_LIMIT_PER_IP, window,
        )
        if not within_limit:
            # Same response as a sent email. A different page would tell a
            # stranger that the limit for this address was reached, which
            # says somebody uses it.
            logger.warning('Password reset rate limit reached; no email sent.')
            return HttpResponseRedirect(self.get_success_url())
        return super().form_valid(form)


class UnlockingPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    audit_action = AuditEvent.Action.PASSWORD_RESET

    def form_valid(self, form):
        response = super().form_valid(form)
        clear_lockout(form.user)
        audit.record(self.audit_action, request=self.request, actor=form.user, target=form.user)
        return response


class AuditedPasswordChangeView(auth_views.PasswordChangeView):
    def form_valid(self, form):
        response = super().form_valid(form)
        audit.record(AuditEvent.Action.PASSWORD_CHANGED, request=self.request, target=self.request.user)
        return response


@login_required
def account_security(request):
    """The user's own security page: password, two-step sign-in, devices."""
    from ..mfa import mfa_status

    return render(request, 'account/security.html', {
        'mfa': mfa_status(request.user),
    })


@login_required
@require_POST
def sign_out_other_devices(request):
    ended = sign_out_everywhere(request.user, keep_session_key=request.session.session_key)
    audit.record(
        AuditEvent.Action.SIGNED_OUT_EVERYWHERE, request=request, target=request.user,
        detail=f"{ended} other session{'s' if ended != 1 else ''} ended",
    )
    if ended:
        messages.success(request, f"Signed out of {ended} other session{'s' if ended != 1 else ''}.")
    else:
        messages.info(request, "You were not signed in anywhere else.")
    return redirect('account_security')


# ── Invitations ──────────────────────────────────────────────────────────────

class InviteTokenGenerator(PasswordResetTokenGenerator):
    """Like the reset token, with its own salt and a longer life.

    The salt means a reset link cannot be used as an invite link or the
    other way round. The token still stops working once a password is set,
    because the password hash is part of it.
    """

    key_salt = 'core.views.account.InviteTokenGenerator'

    def check_token(self, user, token):
        from django.utils.crypto import constant_time_compare
        from django.utils.http import base36_to_int

        if not (user and token):
            return False
        try:
            ts = base36_to_int(token.split('-')[0])
        except ValueError:
            return False
        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(self._make_token_with_timestamp(user, ts, secret), token):
                break
        else:
            return False
        return (self._num_seconds(self._now()) - ts) <= settings.INVITE_TIMEOUT


invite_token_generator = InviteTokenGenerator()


def send_invite(request, user):
    """Email ``user`` a link to choose their first password."""
    from django.contrib.auth.forms import PasswordResetForm
    from django.contrib.sites.shortcuts import get_current_site
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    site = get_current_site(request)
    context = {
        'email': user.email,
        'domain': site.domain,
        'site_name': site.name,
        'uid': urlsafe_base64_encode(force_bytes(user.pk)),
        'user': user,
        'token': invite_token_generator.make_token(user),
        'protocol': 'https' if request.is_secure() else 'http',
        'invited_by': request.user.get_full_name() or request.user.email,
        'valid_days': max(1, settings.INVITE_TIMEOUT // (24 * 60 * 60)),
    }
    PasswordResetForm().send_mail(
        'registration/invite_subject.txt',
        'registration/invite_email.txt',
        context,
        None,
        user.email,
    )


class InviteConfirmView(UnlockingPasswordResetConfirmView):
    audit_action = AuditEvent.Action.INVITE_ACCEPTED
    token_generator = invite_token_generator
    success_url = reverse_lazy('password_reset_complete')
    extra_context = {'invite': True}


# ── Two-step sign-in ─────────────────────────────────────────────────────────

PENDING_DEVICE_KEY = '_mfa_pending_device'


def _safe_next(request, default='dashboard'):
    from django.utils.http import url_has_allowed_host_and_scheme

    target = request.POST.get('next') or request.GET.get('next')
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return target
    return reverse(default)


@login_required
def mfa_verify(request):
    """Second step of sign-in: a code from the app, or a recovery code."""
    from django_otp import login as otp_login

    from .. import mfa

    if request.user.is_verified():
        return redirect(_safe_next(request))
    if not mfa.remember_enrolment(request):
        # Turned off since this session signed in (for example by the user
        # on another device). Nothing to verify.
        return redirect(_safe_next(request))

    error = None
    if request.method == 'POST':
        device = mfa.verify(request.user, request.POST.get('code'))
        if device is not None:
            otp_login(request, device)
            if device.__class__.__name__ == 'StaticDevice':
                left = mfa.recovery_codes_left(request.user)
                audit.record(
                    AuditEvent.Action.MFA_RECOVERY_CODE_USED, request=request, target=request.user,
                    detail=f'{left} left',
                )
                messages.warning(
                    request,
                    f"You used a recovery code. {left} left. "
                    "Make new codes, or set up your app again, on your Account security page.",
                )
            return redirect(_safe_next(request))
        error = (
            "That code did not work. Check that the time on your phone is set "
            "automatically, wait for a new code, and try again. "
            "Lost your phone? Enter one of your recovery codes."
        )
    return render(request, 'account/mfa_verify.html', {
        'error': error,
        'next': request.POST.get('next') or request.GET.get('next', ''),
    })


@login_required
def mfa_setup(request):
    """Add (or replace) the authenticator app, then show recovery codes."""
    from .. import mfa

    error = None
    replacing = mfa.has_mfa(request.user)
    device = mfa.pending_device(request.user, request.session.get(PENDING_DEVICE_KEY))
    if request.method == 'POST' and device is not None:
        codes = mfa.confirm_setup(request.user, device, request.POST.get('code'))
        if codes is not None:
            from django_otp import login as otp_login

            request.session.pop(PENDING_DEVICE_KEY, None)
            otp_login(request, device)
            mfa.remember_enrolment(request)
            # A session elsewhere, perhaps on the device that made the user
            # turn this on, would otherwise carry on without a code.
            sign_out_everywhere(request.user, keep_session_key=request.session.session_key)
            audit.record(
                AuditEvent.Action.MFA_ENABLED, request=request, target=request.user,
                detail='New phone' if replacing else '',
            )
            return render(request, 'account/mfa_recovery_codes.html', {
                'codes': codes,
                'next': _safe_next(request),
                'first_setup': True,
            })
        error = (
            "That code did not work. Scan the QR code again, or check the key "
            "you typed, then enter the code the app shows now."
        )
    if device is None:
        device = mfa.start_setup(request.user)
        request.session[PENDING_DEVICE_KEY] = device.pk

    return render(request, 'account/mfa_setup.html', {
        'qr_svg': mfa.qr_code_svg(device),
        'secret': mfa.secret_for_display(device),
        'error': error,
        'required': mfa.mfa_required(request.user) and not replacing,
        'replacing': replacing,
        'next': request.POST.get('next') or request.GET.get('next', ''),
    })


@login_required
@require_POST
def mfa_new_recovery_codes(request):
    from .. import mfa

    if not mfa.has_mfa(request.user):
        return redirect('account_security')
    codes = mfa.new_recovery_codes(request.user)
    audit.record(AuditEvent.Action.MFA_RECOVERY_CODES_RENEWED, request=request, target=request.user)
    return render(request, 'account/mfa_recovery_codes.html', {
        'codes': codes,
        'next': reverse('account_security'),
        'first_setup': False,
    })


@login_required
@require_POST
def mfa_disable(request):
    """Turn two-step sign-in off. Needs a current code, not just a session."""
    from .. import mfa

    if mfa.mfa_required(request.user):
        messages.error(request, "Your role needs two-step sign-in, so it can't be turned off.")
    elif mfa.verify(request.user, request.POST.get('code')) is None:
        messages.error(request, "That code did not work, so two-step sign-in is still on.")
    else:
        mfa.disable(request.user)
        mfa.remember_enrolment(request)
        audit.record(AuditEvent.Action.MFA_DISABLED, request=request, target=request.user)
        messages.success(request, "Two-step sign-in is off.")
    return redirect('account_security')
