"""Sign in with Google: the views.

Three addresses (docs/GOOGLE_SIGN_IN_PLAN.md, section 5):

* ``google_start`` makes the secrets for one attempt and sends the browser
  to Google;
* ``google_callback`` is where Google sends it back. It checks the reply,
  then signs the person in or links the Google account;
* ``google_disconnect`` removes the signed-in user's own link.

Google sign-in never creates a user. The Google account must already be
linked to a user that an Administrator made. There are two ways to link:

* ``mode=invite``: a new user opens the invitation link from their email
  and selects "Continue with Google" (flow A). The link proves the
  mailbox; Google proves the account. Both must name the same address.
* ``mode=connect``: a signed-in user links their own account from Account
  Security (flow C).

The protocol steps are in ``core/google_oidc.py``.
"""

import logging
import secrets
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .. import audit, google_oidc, ratelimit
from ..google_oidc import GoogleSignInError
from ..models import AuditEvent, GoogleIdentity
from .account import _safe_next, invite_token_generator

logger = logging.getLogger(__name__)

SESSION_KEY = '_google_oauth'
BACKEND = 'core.auth_backends.GoogleBackend'
MODE_LOGIN = 'login'
MODE_CONNECT = 'connect'
MODE_INVITE = 'invite'
MODES = (MODE_LOGIN, MODE_CONNECT, MODE_INVITE)
# Where PasswordResetConfirmView keeps the invitation token after it takes
# it out of the address (django.contrib.auth.views).
INVITE_SESSION_TOKEN_KEY = '_password_reset_token'
# The placeholder token in the address once the real one is in the session.
INVITE_URL_TOKEN = 'set-password'
# How long the person has to finish at Google before the attempt is void.
FLOW_MAX_AGE = 10 * 60

# One wording for "no user is linked" and "the user is deactivated", so
# the page does not say which Google accounts belong to a user here.
REFUSED = (
    "No Cgate account uses that Google account. Sign in with your email "
    "and password, or ask your administrator."
)
FAILED = "Google sign-in did not finish. Please try again."
INVITE_EXPIRED = (
    "This invitation has expired or was already used. Ask the person who "
    "invited you to send a new one."
)


def _require_enabled():
    if not google_oidc.enabled():
        raise Http404


def _redirect_uri(request):
    return request.build_absolute_uri(reverse('google_callback'))


def _exit_url(flow):
    """Where to send the person when this attempt stops."""
    mode = flow.get('mode')
    if mode == MODE_CONNECT:
        return reverse('account_security')
    if mode == MODE_INVITE and flow.get('invite_uidb64'):
        # Back to the invitation page. The token is still in the session,
        # so the page opens again, or says that the link has expired.
        return reverse('invite_confirm', kwargs={
            'uidb64': flow['invite_uidb64'], 'token': INVITE_URL_TOKEN,
        })
    return reverse('login')


def _invited_user(request, uidb64):
    """The user whose invitation this session opened, or None.

    The invitation page has already checked the link and kept the token in
    the session. It is checked again here, because the page may have been
    open for a long time.
    """
    from django.contrib.auth import get_user_model

    token = request.session.get(INVITE_SESSION_TOKEN_KEY)
    if not uidb64 or not token:
        return None, None
    try:
        user = get_user_model()._default_manager.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (TypeError, ValueError, OverflowError, get_user_model().DoesNotExist):
        return None, None
    if not invite_token_generator.check_token(user, token):
        return None, None
    return user, token


@never_cache
@require_POST
def google_start(request):
    """Begin a sign-in (or a link) and send the browser to Google.

    POST only, with the CSRF token: a plain link would let another site
    start a sign-in on this browser.
    """
    _require_enabled()
    mode = request.POST.get('mode', MODE_LOGIN)
    if mode not in MODES:
        mode = MODE_LOGIN

    if mode == MODE_CONNECT and not request.user.is_authenticated:
        return redirect('login')
    if mode == MODE_LOGIN and request.user.is_authenticated:
        return redirect(_safe_next(request))

    flow_extra = {}
    login_hint = request.user.email if mode == MODE_CONNECT else ''
    if mode == MODE_INVITE:
        uidb64 = request.POST.get('uidb64', '')
        invited, token = _invited_user(request, uidb64)
        if invited is None:
            messages.error(request, INVITE_EXPIRED)
            return redirect('login')
        flow_extra = {
            'invite_user_id': invited.pk,
            'invite_token': token,
            'invite_uidb64': uidb64,
        }
        # Selects the right Google account for a person who has more than one.
        login_hint = invited.email

    ip = request.META.get('REMOTE_ADDR', '')
    if not ratelimit.allow(
        'google-start-ip', ip, settings.GOOGLE_SIGN_IN_LIMIT_PER_IP,
        settings.GOOGLE_SIGN_IN_LIMIT_WINDOW,
    ):
        messages.error(request, "Too many Google sign-in attempts. Wait a few minutes, then try again.")
        return redirect(_exit_url({'mode': mode, **flow_extra}))

    flow = google_oidc.new_flow()
    flow.update({
        'mode': mode,
        'next': _safe_next(request),
        'started': int(time.time()),
        'user_id': request.user.pk if mode == MODE_CONNECT else None,
        **flow_extra,
    })
    request.session[SESSION_KEY] = flow

    url = google_oidc.authorization_url(
        redirect_uri=_redirect_uri(request),
        state=flow['state'],
        nonce=flow['nonce'],
        code_verifier=flow['code_verifier'],
        login_hint=login_hint,
    )
    return HttpResponseRedirect(url)


@never_cache
@require_GET
def google_callback(request):
    """Google sends the browser back here with a code, or with an error."""
    _require_enabled()

    # Taken out of the session first, so one attempt can be answered once
    # whatever happens below.
    flow = request.session.pop(SESSION_KEY, None)
    if not isinstance(flow, dict):
        messages.error(request, FAILED)
        return redirect('login')
    mode = flow.get('mode', MODE_LOGIN)

    try:
        profile = _profile_from_callback(request, flow)
    except GoogleSignInError as exc:
        if exc.reason == 'cancelled':
            messages.info(request, "Google sign-in was cancelled.")
        else:
            logger.warning('Google sign-in failed: %s', exc.reason)
            messages.error(request, FAILED)
        return redirect(_exit_url(flow))

    if mode == MODE_CONNECT:
        return _finish_connect(request, flow, profile)
    if mode == MODE_INVITE:
        return _finish_invite(request, flow, profile)
    return _finish_login(request, flow, profile)


def _profile_from_callback(request, flow):
    """Run the protocol checks. Returns the person, or raises."""
    if int(time.time()) - int(flow.get('started') or 0) > FLOW_MAX_AGE:
        raise GoogleSignInError('flow_expired')

    state = request.GET.get('state', '')
    expected = flow.get('state') or ''
    if not state or not expected or not secrets.compare_digest(
        state.encode('utf-8'), expected.encode('utf-8'),
    ):
        raise GoogleSignInError('wrong_state')

    error = request.GET.get('error')
    if error:
        # access_denied is the person pressing Cancel at Google.
        raise GoogleSignInError('cancelled' if error == 'access_denied' else 'google_error')

    code = request.GET.get('code')
    if not code:
        raise GoogleSignInError('no_code')

    id_token = google_oidc.exchange_code(
        code=code,
        redirect_uri=_redirect_uri(request),
        code_verifier=flow.get('code_verifier') or '',
    )
    claims = google_oidc.decode_id_token(id_token)
    return google_oidc.check_claims(claims, nonce=flow.get('nonce') or '')


def _finish_login(request, flow, profile):
    identity = (
        GoogleIdentity.objects.select_related('user').filter(sub=profile.sub).first()
    )
    if identity is None or not identity.user.is_active:
        audit.record(
            AuditEvent.Action.GOOGLE_SIGN_IN_REFUSED, request=request,
            target=identity.user if identity else None,
            detail='User is deactivated' if identity else f'No user is linked to {profile.email}',
        )
        messages.error(request, REFUSED)
        return redirect('login')

    # update(), not save(): this is bookkeeping on every sign-in.
    GoogleIdentity.objects.filter(pk=identity.pk).update(last_used_at=timezone.now())
    # login() directly, not authenticate(): Google has done the check, and
    # django-axes must not count this as a password attempt. The session
    # key is renewed, and user_logged_in starts the session clock, writes
    # the audit event and sets up two-step sign-in (core/signals.py).
    login(request, identity.user, backend=BACKEND)
    return HttpResponseRedirect(flow.get('next') or reverse('dashboard'))


def _finish_invite(request, flow, profile):
    """Accept an invitation with Google: link the account, then sign in.

    Checks 7 to 10 of docs/GOOGLE_SIGN_IN_PLAN.md, section 5.5.
    """
    from django.contrib.auth import get_user_model

    user = get_user_model()._default_manager.filter(pk=flow.get('invite_user_id')).first()
    # 7 and 8. The invitation is still good (it stops working once the user
    # signs in or gets a password), and the account is active.
    if (
        user is None
        or not user.is_active
        or not invite_token_generator.check_token(user, flow.get('invite_token') or '')
    ):
        messages.error(request, INVITE_EXPIRED)
        return redirect('login')

    # 9. The Google account is for the invited address. The invitation
    # link proves the mailbox; this proves the Google account is the same.
    if not user.email or profile.email.lower() != user.email.strip().lower():
        audit.record(
            AuditEvent.Action.GOOGLE_SIGN_IN_REFUSED, request=request, target=user,
            detail=f'Invitation: Google account {profile.email} is not {user.email}',
        )
        messages.error(
            request,
            f"That Google account is {profile.email}. Use the Google account "
            f"for {user.email}, or choose a password below.",
        )
        return redirect(_exit_url(flow))

    if GoogleIdentity.objects.filter(user=user).exists():
        messages.error(request, INVITE_EXPIRED)
        return redirect('login')

    # 10. No other user has this Google account. The sub is unique, so
    # the database is the final check.
    try:
        with transaction.atomic():
            identity = GoogleIdentity.objects.create(
                user=user, sub=profile.sub, email=profile.email,
                hosted_domain=profile.hosted_domain, last_used_at=timezone.now(),
            )
    except IntegrityError:
        audit.record(
            AuditEvent.Action.GOOGLE_SIGN_IN_REFUSED, request=request, target=user,
            detail=f'Invitation: Google account {profile.email} is linked to another user',
        )
        messages.error(
            request,
            "That Google account is connected to another Cgate user. "
            "Use a different Google account, or choose a password below.",
        )
        return redirect(_exit_url(flow))

    # The name from Google fills in only what the Administrator left blank.
    changed = []
    if not user.first_name and profile.first_name:
        user.first_name = profile.first_name[:150]
        changed.append('first_name')
    if not user.last_name and profile.last_name:
        user.last_name = profile.last_name[:150]
        changed.append('last_name')
    if changed:
        user.save(update_fields=changed)

    # The token is spent: login() sets last_login, which is part of it.
    request.session.pop(INVITE_SESSION_TOKEN_KEY, None)
    login(request, user, backend=BACKEND)
    audit.record(
        AuditEvent.Action.GOOGLE_LINKED, request=request, actor=user, target=user,
        detail=f'{identity.email} (invitation)',
    )
    audit.record(
        AuditEvent.Action.INVITE_ACCEPTED, request=request, actor=user, target=user,
        detail='Google',
    )
    return HttpResponseRedirect(flow.get('next') or reverse('dashboard'))


def _finish_connect(request, flow, profile):
    user = request.user
    if not user.is_authenticated or user.pk != flow.get('user_id'):
        # Signed out, or signed in as someone else, while at Google.
        messages.error(request, FAILED)
        return redirect('login')

    if not user.email or profile.email.lower() != user.email.strip().lower():
        messages.error(
            request,
            f"That Google account is {profile.email}. Use the Google account "
            f"for {user.email or 'your email address'}.",
        )
        return redirect('account_security')

    if GoogleIdentity.objects.filter(user=user).exists():
        messages.info(request, "A Google account is connected already. Disconnect it first to use another.")
        return redirect('account_security')

    try:
        with transaction.atomic():
            GoogleIdentity.objects.create(
                user=user, sub=profile.sub, email=profile.email,
                hosted_domain=profile.hosted_domain,
            )
    except IntegrityError:
        # The sub is unique: another user has this Google account.
        messages.error(request, "That Google account is connected to another Cgate user.")
        return redirect('account_security')

    audit.record(
        AuditEvent.Action.GOOGLE_LINKED, request=request, target=user,
        detail=profile.email,
    )
    messages.success(request, f"Google account {profile.email} connected. You can now sign in with Google.")
    return redirect('account_security')


@login_required
@require_POST
def google_disconnect(request):
    """Remove the signed-in user's own Google link."""
    identity = GoogleIdentity.objects.filter(user=request.user).first()
    if identity is None:
        return redirect('account_security')
    if not request.user.has_usable_password():
        # Without a password the Google account is the only way in.
        messages.error(
            request,
            "You have no password, so Google is your only way to sign in. "
            "Ask an administrator to set a password for you first.",
        )
        return redirect('account_security')
    email = identity.email
    identity.delete()
    audit.record(
        AuditEvent.Action.GOOGLE_UNLINKED, request=request, target=request.user,
        detail=email,
    )
    messages.success(request, "Google account disconnected. Sign in with your email and password.")
    return redirect('account_security')
