"""Sign in with Google: the OpenID Connect protocol steps.

The authorization code flow for a server-side web app, written with the
``requests`` library, the same way as the Xero client
(``xero_integration/client.py``). Design: docs/GOOGLE_SIGN_IN_PLAN.md.

This module has no views and reads no session. The views in
``core/views/google.py`` keep ``state``, ``nonce`` and the PKCE verifier in
the session and call these functions:

* ``new_flow`` makes the three secrets for one sign-in attempt;
* ``authorization_url`` builds the address to send the browser to;
* ``exchange_code`` swaps the code Google sent back for an ID token;
* ``check_claims`` checks that token and returns who signed in.

Nothing from Google is kept. The ID token is read once and dropped, and no
access or refresh token is stored.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

AUTHORIZE_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
# Google documents both forms of the issuer.
ISSUERS = ('https://accounts.google.com', 'accounts.google.com')
SCOPE = 'openid email profile'
TOKEN_TIMEOUT = 10
# Allowance for a small difference between this server's clock and Google's.
CLOCK_LEEWAY = 60
# models.GoogleIdentity.sub
SUB_MAX_LENGTH = 255


class GoogleSignInError(Exception):
    """A step of the flow failed. ``reason`` is a short code for the log
    and the audit trail; it is never shown to the person signing in."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class GoogleProfile:
    """Who signed in, from a checked ID token."""

    sub: str
    email: str
    hosted_domain: str = ''
    first_name: str = ''
    last_name: str = ''


def enabled() -> bool:
    return bool(getattr(settings, 'GOOGLE_SIGN_IN_ENABLED', False))


def new_flow() -> dict:
    """The secrets for one sign-in attempt. Kept in the session, used once."""
    return {
        'state': secrets.token_urlsafe(32),
        'nonce': secrets.token_urlsafe(32),
        'code_verifier': secrets.token_urlsafe(64),
    }


def code_challenge(code_verifier: str) -> str:
    """The PKCE S256 challenge for ``code_verifier`` (RFC 7636)."""
    digest = hashlib.sha256(code_verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


def authorization_url(*, redirect_uri: str, state: str, nonce: str,
                      code_verifier: str, login_hint: str = '') -> str:
    """Where to send the browser so the person can pick a Google account."""
    params = {
        'client_id': settings.GOOGLE_OAUTH_CLIENT_ID,
        'response_type': 'code',
        'scope': SCOPE,
        'redirect_uri': redirect_uri,
        'state': state,
        'nonce': nonce,
        'code_challenge': code_challenge(code_verifier),
        'code_challenge_method': 'S256',
        # Always show the account list. Without it Google silently uses
        # the one signed-in account, which on a shared yard computer may
        # be somebody else's.
        'prompt': 'select_account',
    }
    if login_hint:
        params['login_hint'] = login_hint
    return f'{AUTHORIZE_URL}?{urlencode(params)}'


def exchange_code(*, code: str, redirect_uri: str, code_verifier: str) -> str:
    """Swap the authorization code for an ID token. Returns the raw token."""
    try:
        response = requests.post(
            TOKEN_URL,
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': redirect_uri,
                'client_id': settings.GOOGLE_OAUTH_CLIENT_ID,
                'client_secret': settings.GOOGLE_OAUTH_CLIENT_SECRET,
                'code_verifier': code_verifier,
            },
            timeout=TOKEN_TIMEOUT,
        )
    except requests.RequestException as exc:
        # The exception text can hold the request, client secret included.
        logger.warning('Google token request failed: %s', type(exc).__name__)
        raise GoogleSignInError('token_request_failed') from None
    if response.status_code != 200:
        logger.warning(
            'Google token request refused: HTTP %s %s',
            response.status_code, _error_code(response),
        )
        raise GoogleSignInError('token_request_refused')
    try:
        id_token = response.json().get('id_token')
    except (ValueError, AttributeError):
        id_token = None
    if not id_token or not isinstance(id_token, str):
        raise GoogleSignInError('no_id_token')
    return id_token


def _error_code(response) -> str:
    """Google's short OAuth error code (``invalid_grant``...), nothing else."""
    try:
        code = response.json().get('error')
    except (ValueError, AttributeError):
        return ''
    return code[:60] if isinstance(code, str) else ''


def decode_id_token(id_token: str) -> dict:
    """The claims of an ID token.

    The signature is not checked. OpenID Connect Core (section 3.1.3.7)
    allows that when the token comes straight from the token endpoint
    over TLS, which is the only place this module gets one: ``requests``
    has already verified Google's certificate. Never call this on a token
    that came from the browser.
    """
    parts = id_token.split('.')
    if len(parts) != 3:
        raise GoogleSignInError('malformed_id_token')
    payload = parts[1]
    try:
        raw = base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))
        claims = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise GoogleSignInError('malformed_id_token') from None
    if not isinstance(claims, dict):
        raise GoogleSignInError('malformed_id_token')
    return claims


def check_claims(claims: dict, *, nonce: str, now: float | None = None) -> GoogleProfile:
    """Check an ID token's claims and return the person they describe.

    Every check must pass: the token is from Google, it was made for this
    app, it has not expired, it answers the request this session started,
    and Google has verified the email address.
    """
    client_id = settings.GOOGLE_OAUTH_CLIENT_ID
    now = time.time() if now is None else now

    if claims.get('iss') not in ISSUERS:
        raise GoogleSignInError('wrong_issuer')

    audience = claims.get('aud')
    if isinstance(audience, list):
        # More than one audience is only acceptable when the token names
        # this app as the party it was issued to.
        if client_id not in audience or claims.get('azp') != client_id:
            raise GoogleSignInError('wrong_audience')
    elif audience != client_id:
        raise GoogleSignInError('wrong_audience')
    if claims.get('azp') not in (None, client_id):
        raise GoogleSignInError('wrong_audience')

    expires = claims.get('exp')
    if isinstance(expires, bool) or not isinstance(expires, (int, float)):
        raise GoogleSignInError('no_expiry')
    if expires + CLOCK_LEEWAY < now:
        raise GoogleSignInError('expired')

    token_nonce = claims.get('nonce')
    if not isinstance(token_nonce, str) or not nonce or not secrets.compare_digest(
        token_nonce.encode('utf-8'), nonce.encode('utf-8'),
    ):
        raise GoogleSignInError('wrong_nonce')

    sub = claims.get('sub')
    if not isinstance(sub, str) or not sub or len(sub) > SUB_MAX_LENGTH:
        raise GoogleSignInError('no_subject')

    email = claims.get('email')
    if not isinstance(email, str) or '@' not in email:
        raise GoogleSignInError('no_email')
    # Google sends a JSON true. Some of its older endpoints sent the
    # string; accept that and nothing else.
    if claims.get('email_verified') not in (True, 'true'):
        raise GoogleSignInError('email_not_verified')

    return GoogleProfile(
        sub=sub,
        email=email.strip(),
        hosted_domain=_text(claims.get('hd')),
        first_name=_text(claims.get('given_name')),
        last_name=_text(claims.get('family_name')),
    )


def _text(value, limit: int = 255) -> str:
    return value[:limit] if isinstance(value, str) else ''
