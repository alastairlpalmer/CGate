"""Sign in with Google (docs/GOOGLE_SIGN_IN_PLAN.md, phase 2).

Google is never called. ``requests.post`` in ``core.google_oidc`` is
replaced with a mock that answers as Google's token endpoint does, so the
tests cover this app's half of the flow: the secrets it keeps in the
session, the checks on the reply, and who is let in.
"""

import base64
import json
import re
import time
from unittest import mock
from urllib.parse import parse_qs, urlparse

import requests
from axes.handlers.proxy import AxesProxyHandler
from django.contrib.auth import BACKEND_SESSION_KEY, get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core import google_oidc, mfa
from core.google_oidc import GoogleSignInError
from core.middleware import SESSION_STARTED_KEY
from core.models import AuditEvent, GoogleIdentity
from core.roles_testutils import make_admin, make_user_with_access
from core.views.google import SESSION_KEY

User = get_user_model()
PW = 'correct-horse-battery-staple'
CLIENT_ID = 'test-client.apps.googleusercontent.com'
SUB = '110169484474386276334'
ON = dict(
    GOOGLE_SIGN_IN_ENABLED=True,
    GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET='test-secret',
)


def _b64(data):
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b'=').decode()


def id_token(**claims):
    """An ID token as Google's token endpoint returns it. Not signed: the
    app does not check the signature of a token it fetched itself."""
    return f"{_b64({'alg': 'RS256', 'kid': 'k'})}.{_b64(claims)}.c2lnbmF0dXJl"


def claims_for(nonce, /, **changes):
    claims = {
        'iss': 'https://accounts.google.com',
        'aud': CLIENT_ID,
        'azp': CLIENT_ID,
        'sub': SUB,
        'email': 'jo@example.com',
        'email_verified': True,
        'nonce': nonce,
        'iat': int(time.time()),
        'exp': int(time.time()) + 3600,
        'given_name': 'Jo',
        'family_name': 'Groom',
    }
    claims.update(changes)
    return {k: v for k, v in claims.items() if v is not None}


def token_response(claims, status=200):
    response = mock.Mock(status_code=status)
    response.json.return_value = {'id_token': id_token(**claims), 'access_token': 'unused'}
    return response


# ── The protocol module ──────────────────────────────────────────────────────

@override_settings(**ON)
class ClaimCheckTests(SimpleTestCase):
    def _check(self, **changes):
        return google_oidc.check_claims(claims_for('n-1', **changes), nonce='n-1')

    def _refused(self, reason, **changes):
        with self.assertRaises(GoogleSignInError) as caught:
            self._check(**changes)
        self.assertEqual(caught.exception.reason, reason)

    def test_good_claims_give_the_profile(self):
        profile = self._check(hd='example.com')
        self.assertEqual(profile.sub, SUB)
        self.assertEqual(profile.email, 'jo@example.com')
        self.assertEqual(profile.hosted_domain, 'example.com')
        self.assertEqual(profile.first_name, 'Jo')

    def test_both_forms_of_the_issuer_are_accepted(self):
        self._check(iss='accounts.google.com')

    def test_another_issuer_is_refused(self):
        self._refused('wrong_issuer', iss='https://accounts.example.com')

    def test_a_token_for_another_app_is_refused(self):
        self._refused('wrong_audience', aud='other.apps.googleusercontent.com')

    def test_a_token_issued_to_another_app_is_refused(self):
        self._refused('wrong_audience', azp='other.apps.googleusercontent.com')

    def test_several_audiences_need_this_app_as_authorised_party(self):
        self._check(aud=[CLIENT_ID, 'other'])
        self._refused('wrong_audience', aud=[CLIENT_ID, 'other'], azp='other')
        self._refused('wrong_audience', aud=['other', 'another'])

    def test_an_expired_token_is_refused(self):
        self._refused('expired', exp=int(time.time()) - 600)

    def test_a_token_without_an_expiry_is_refused(self):
        self._refused('no_expiry', exp=None)
        self._refused('no_expiry', exp='tomorrow')

    def test_another_sessions_nonce_is_refused(self):
        self._refused('wrong_nonce', nonce='n-2')
        self._refused('wrong_nonce', nonce=None)

    def test_an_empty_expected_nonce_never_matches(self):
        with self.assertRaises(GoogleSignInError):
            google_oidc.check_claims(claims_for(''), nonce='')

    def test_an_unverified_email_is_refused(self):
        self._refused('email_not_verified', email_verified=False)
        self._refused('email_not_verified', email_verified=None)
        self._refused('email_not_verified', email_verified='yes')

    def test_missing_subject_or_email_is_refused(self):
        self._refused('no_subject', sub=None)
        self._refused('no_subject', sub='x' * 300)
        self._refused('no_email', email=None)

    def test_malformed_tokens_are_refused(self):
        for bad in ('', 'one.two', 'a.!!!.c', f"a.{_b64(['not', 'a', 'dict'])}.c"):
            with self.assertRaises(GoogleSignInError):
                google_oidc.decode_id_token(bad)


@override_settings(**ON)
class ProtocolRequestTests(SimpleTestCase):
    def test_authorization_url_carries_every_parameter(self):
        flow = google_oidc.new_flow()
        url = google_oidc.authorization_url(
            redirect_uri='https://app.example/accounts/google/callback/',
            state=flow['state'], nonce=flow['nonce'],
            code_verifier=flow['code_verifier'], login_hint='jo@example.com',
        )
        parsed = urlparse(url)
        self.assertEqual(f'{parsed.scheme}://{parsed.netloc}{parsed.path}', google_oidc.AUTHORIZE_URL)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.assertEqual(query['client_id'], CLIENT_ID)
        self.assertEqual(query['response_type'], 'code')
        self.assertEqual(query['scope'], 'openid email profile')
        self.assertEqual(query['redirect_uri'], 'https://app.example/accounts/google/callback/')
        self.assertEqual(query['state'], flow['state'])
        self.assertEqual(query['nonce'], flow['nonce'])
        self.assertEqual(query['code_challenge_method'], 'S256')
        self.assertEqual(query['code_challenge'], google_oidc.code_challenge(flow['code_verifier']))
        self.assertEqual(query['prompt'], 'select_account')
        self.assertEqual(query['login_hint'], 'jo@example.com')
        # The verifier and the client secret never go to the browser.
        self.assertNotIn(flow['code_verifier'], url)
        self.assertNotIn('test-secret', url)

    def test_code_challenge_matches_the_rfc_7636_example(self):
        self.assertEqual(
            google_oidc.code_challenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'),
            'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM',
        )

    def test_each_flow_has_fresh_secrets(self):
        self.assertNotEqual(google_oidc.new_flow(), google_oidc.new_flow())

    def test_exchange_posts_the_code_secret_and_verifier(self):
        with mock.patch('core.google_oidc.requests.post', return_value=token_response({'sub': '1'})) as post:
            google_oidc.exchange_code(code='c-1', redirect_uri='https://app.example/cb/', code_verifier='v-1')
        (url,), kwargs = post.call_args
        self.assertEqual(url, google_oidc.TOKEN_URL)
        self.assertEqual(kwargs['timeout'], google_oidc.TOKEN_TIMEOUT)
        self.assertEqual(kwargs['data'], {
            'grant_type': 'authorization_code', 'code': 'c-1',
            'redirect_uri': 'https://app.example/cb/', 'client_id': CLIENT_ID,
            'client_secret': 'test-secret', 'code_verifier': 'v-1',
        })

    def test_exchange_failures_raise_without_leaking_the_secret(self):
        with mock.patch('core.google_oidc.requests.post', side_effect=requests.ConnectionError('test-secret')):
            with self.assertLogs('core.google_oidc', level='WARNING') as logs:
                with self.assertRaises(GoogleSignInError) as caught:
                    google_oidc.exchange_code(code='c', redirect_uri='r', code_verifier='v')
        self.assertEqual(caught.exception.reason, 'token_request_failed')
        self.assertNotIn('test-secret', ' '.join(logs.output))

        refused = mock.Mock(status_code=400)
        refused.json.return_value = {'error': 'invalid_grant'}
        with mock.patch('core.google_oidc.requests.post', return_value=refused):
            with self.assertLogs('core.google_oidc', level='WARNING'):
                with self.assertRaises(GoogleSignInError) as caught:
                    google_oidc.exchange_code(code='c', redirect_uri='r', code_verifier='v')
        self.assertEqual(caught.exception.reason, 'token_request_refused')

        empty = mock.Mock(status_code=200)
        empty.json.return_value = {'access_token': 'only'}
        with mock.patch('core.google_oidc.requests.post', return_value=empty):
            with self.assertRaises(GoogleSignInError) as caught:
                google_oidc.exchange_code(code='c', redirect_uri='r', code_verifier='v')
        self.assertEqual(caught.exception.reason, 'no_id_token')


# ── The views ────────────────────────────────────────────────────────────────

class GoogleFlowMixin:
    """Drive the flow the way a browser does, with Google mocked."""

    def setUp(self):
        super().setUp()
        cache.clear()  # the start view is rate limited through the cache
        self.user = make_user_with_access('jo@example.com', password=PW, horses='view')
        self.user.email = 'jo@example.com'
        self.user.save(update_fields=['email'])

    def link(self, user=None, sub=SUB, email='jo@example.com'):
        return GoogleIdentity.objects.create(user=user or self.user, sub=sub, email=email)

    def start(self, client=None, mode='login', **extra):
        client = client or self.client
        response = client.post(reverse('google_start'), {'mode': mode, **extra})
        return response

    def flow(self, client=None):
        return (client or self.client).session[SESSION_KEY]

    def callback(self, client=None, claims=None, state=None, code='auth-code', **params):
        """Come back from Google. ``claims`` change the ID token."""
        client = client or self.client
        flow = client.session.get(SESSION_KEY) or {}
        query = {'state': flow.get('state', '') if state is None else state, **params}
        if code is not None:
            query['code'] = code
        token = token_response(claims_for(flow.get('nonce', ''), **(claims or {})))
        with mock.patch('core.google_oidc.requests.post', return_value=token) as post:
            response = client.get(reverse('google_callback'), query)
        self.token_request = post
        return response

    def sign_in(self, client=None, **kwargs):
        self.start(client)
        return self.callback(client, **kwargs)

    def signed_in_as(self, client=None):
        return (client or self.client).session.get('_auth_user_id')


class FeatureOffTests(GoogleFlowMixin, TestCase):
    """Without a client ID there is no button and no address answers."""

    def test_addresses_answer_404(self):
        self.assertEqual(self.client.post(reverse('google_start'), {'mode': 'login'}).status_code, 404)
        self.assertEqual(self.client.get(reverse('google_callback')).status_code, 404)

    def test_no_button_on_the_sign_in_page(self):
        self.assertNotContains(self.client.get(reverse('login')), 'Sign in with Google')

    def test_no_card_on_account_security(self):
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse('account_security')), 'Connect Google')

    def test_settings_default_to_off(self):
        from django.conf import settings
        self.assertFalse(settings.GOOGLE_SIGN_IN_ENABLED)


@override_settings(**ON)
class StartTests(GoogleFlowMixin, TestCase):
    def test_button_is_on_the_sign_in_page_and_keeps_next(self):
        page = self.client.get(reverse('login') + '?next=/horses/')
        self.assertContains(page, 'Sign in with Google')
        self.assertContains(page, reverse('google_start'))
        self.assertContains(page, 'name="next" value="/horses/"')

    def test_get_is_refused(self):
        self.assertEqual(self.client.get(reverse('google_start')).status_code, 405)

    def test_post_needs_the_csrf_token(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(reverse('google_start'), {'mode': 'login'}).status_code, 403)

    def test_start_keeps_the_secrets_and_redirects_to_google(self):
        response = self.start()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(google_oidc.AUTHORIZE_URL + '?'))
        flow = self.flow()
        query = {k: v[0] for k, v in parse_qs(urlparse(response.url).query).items()}
        self.assertEqual(query['state'], flow['state'])
        self.assertEqual(query['nonce'], flow['nonce'])
        self.assertEqual(query['redirect_uri'], 'http://testserver/accounts/google/callback/')
        self.assertEqual(flow['mode'], 'login')
        self.assertNotIn('login_hint', query)
        self.assertNotIn(flow['code_verifier'], response.url)

    def test_an_unknown_mode_is_a_sign_in(self):
        self.start(mode='bogus')
        self.assertEqual(self.flow()['mode'], 'login')

    def test_an_unsafe_next_is_dropped(self):
        self.start(next='https://evil.example/steal')
        self.assertEqual(self.flow()['next'], reverse('dashboard'))
        self.start(next='/horses/')
        self.assertEqual(self.flow()['next'], '/horses/')

    @override_settings(GOOGLE_SIGN_IN_LIMIT_PER_IP=2)
    def test_starts_are_rate_limited(self):
        self.assertTrue(self.start().url.startswith(google_oidc.AUTHORIZE_URL))
        self.assertTrue(self.start().url.startswith(google_oidc.AUTHORIZE_URL))
        limited = self.start()
        self.assertRedirects(limited, reverse('login'), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse('login')), 'Too many Google sign-in attempts')

    def test_a_signed_in_user_is_not_sent_to_google_to_sign_in(self):
        self.client.force_login(self.user)
        response = self.start()
        self.assertEqual(response.url, reverse('dashboard'))

    def test_connect_needs_a_signed_in_user(self):
        self.assertRedirects(self.start(mode='connect'), reverse('login'), fetch_redirect_response=False)
        self.assertNotIn(SESSION_KEY, self.client.session)


@override_settings(**ON)
class SignInTests(GoogleFlowMixin, TestCase):
    def test_a_linked_google_account_signs_in(self):
        self.link()
        response = self.sign_in()
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.assertEqual(int(self.signed_in_as()), self.user.pk)
        self.assertEqual(self.client.session[BACKEND_SESSION_KEY], 'core.auth_backends.GoogleBackend')
        self.assertIsNotNone(GoogleIdentity.objects.get(user=self.user).last_used_at)
        # The app's own page loads on the next request: the backend can
        # fetch the user again.
        self.assertEqual(self.client.get(reverse('account_security')).status_code, 200)

    def test_the_code_is_exchanged_with_this_sessions_verifier(self):
        self.link()
        self.start()
        verifier = self.flow()['code_verifier']
        self.callback()
        data = self.token_request.call_args.kwargs['data']
        self.assertEqual(data['code'], 'auth-code')
        self.assertEqual(data['code_verifier'], verifier)
        self.assertEqual(data['redirect_uri'], 'http://testserver/accounts/google/callback/')

    def test_sign_in_goes_to_next(self):
        self.link()
        self.start(next='/horses/')
        self.assertRedirects(self.callback(), '/horses/', fetch_redirect_response=False)

    def test_the_session_is_set_up_like_a_password_sign_in(self):
        self.link()
        self.sign_in()
        self.assertIn(SESSION_STARTED_KEY, self.client.session)
        self.assertFalse(self.client.session[mfa.SESSION_ENROLLED_KEY])
        event = AuditEvent.objects.get(action=AuditEvent.Action.SIGNED_IN, target_user=self.user)
        self.assertEqual(event.detail, 'Google')

    def test_a_password_sign_in_is_not_marked_google(self):
        self.client.post(reverse('login'), {'username': 'jo@example.com', 'password': PW})
        event = AuditEvent.objects.get(action=AuditEvent.Action.SIGNED_IN, target_user=self.user)
        self.assertEqual(event.detail, '')

    def test_the_session_key_changes_at_sign_in(self):
        self.link()
        self.start()
        before = self.client.session.session_key
        self.callback()
        self.assertNotEqual(self.client.session.session_key, before)

    def test_sign_in_is_by_sub_so_a_changed_google_email_still_works(self):
        self.link()
        self.sign_in(claims={'email': 'jo.renamed@example.com'})
        self.assertEqual(int(self.signed_in_as()), self.user.pk)

    def test_an_unlinked_google_account_is_refused_and_no_user_is_made(self):
        users = User.objects.count()
        response = self.sign_in()
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertIsNone(self.signed_in_as())
        self.assertEqual(User.objects.count(), users)
        self.assertEqual(GoogleIdentity.objects.count(), 0)
        self.assertContains(self.client.get(reverse('login')), 'No Cgate account uses that Google account')
        event = AuditEvent.objects.get(action=AuditEvent.Action.GOOGLE_SIGN_IN_REFUSED)
        self.assertIn('jo@example.com', event.detail)

    def test_a_matching_email_alone_does_not_sign_in(self):
        # jo@example.com is a user, and Google says the email is verified,
        # but the Google account was never linked.
        self.sign_in(claims={'sub': 'a-different-google-account'})
        self.assertIsNone(self.signed_in_as())

    def test_a_deactivated_user_is_refused_with_the_same_message(self):
        self.link()
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertRedirects(self.sign_in(), reverse('login'), fetch_redirect_response=False)
        self.assertIsNone(self.signed_in_as())
        self.assertContains(self.client.get(reverse('login')), 'No Cgate account uses that Google account')
        event = AuditEvent.objects.get(action=AuditEvent.Action.GOOGLE_SIGN_IN_REFUSED)
        self.assertEqual(event.target_user, self.user)

    def test_deactivating_a_user_ends_a_google_session(self):
        self.link()
        self.sign_in()
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        response = self.client.get(reverse('account_security'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)


@override_settings(**ON)
class CallbackRefusalTests(GoogleFlowMixin, TestCase):
    """Each protocol failure ends at the sign-in page, signed out."""

    def setUp(self):
        super().setUp()
        self.link()

    def assert_refused(self, response, text='Google sign-in did not finish'):
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertIsNone(self.signed_in_as())
        self.assertContains(self.client.get(reverse('login')), text)

    def test_callback_without_a_started_flow(self):
        with self.assertNoLogs('core.views.google', level='WARNING'):
            response = self.callback(state='anything')
        self.assert_refused(response)
        self.token_request.assert_not_called()

    def test_wrong_state(self):
        self.start()
        with self.assertLogs('core.views.google', level='WARNING'):
            response = self.callback(state='forged')
        self.assert_refused(response)
        self.token_request.assert_not_called()

    def test_missing_state(self):
        self.start()
        with self.assertLogs('core.views.google', level='WARNING'):
            self.assert_refused(self.callback(state=''))

    def test_a_flow_can_be_answered_once(self):
        self.start()
        state = self.flow()['state']
        self.callback()
        self.client.post(reverse('logout'))
        again = self.callback(state=state)
        self.assert_refused(again)

    def test_a_flow_older_than_ten_minutes(self):
        self.start()
        session = self.client.session
        flow = session[SESSION_KEY]
        flow['started'] -= 11 * 60
        session[SESSION_KEY] = flow
        session.save()
        with self.assertLogs('core.views.google', level='WARNING'):
            self.assert_refused(self.callback())

    def test_cancel_at_google_is_a_calm_message(self):
        self.start()
        with self.assertNoLogs('core.views.google', level='WARNING'):
            response = self.callback(code=None, error='access_denied')
        self.assert_refused(response, 'Google sign-in was cancelled')
        self.token_request.assert_not_called()

    def test_another_google_error(self):
        self.start()
        with self.assertLogs('core.views.google', level='WARNING'):
            self.assert_refused(self.callback(code=None, error='server_error'))

    def test_no_code(self):
        self.start()
        with self.assertLogs('core.views.google', level='WARNING'):
            self.assert_refused(self.callback(code=None))

    def test_token_request_fails(self):
        self.start()
        state = self.flow()['state']
        with mock.patch('core.google_oidc.requests.post', side_effect=requests.Timeout()):
            with self.assertLogs('core', level='WARNING'):
                response = self.client.get(reverse('google_callback'), {'state': state, 'code': 'c'})
        self.assert_refused(response)

    def test_bad_id_tokens(self):
        cases = {
            'wrong issuer': {'iss': 'https://evil.example'},
            'another app': {'aud': 'other.apps.googleusercontent.com'},
            'replayed nonce': {'nonce': 'from-another-session'},
            'expired': {'exp': int(time.time()) - 600},
            'unverified email': {'email_verified': False},
        }
        for name, claims in cases.items():
            with self.subTest(name):
                self.start()
                with self.assertLogs('core.views.google', level='WARNING'):
                    response = self.callback(claims=claims)
                self.assert_refused(response)

    def test_post_to_the_callback_is_refused(self):
        self.assertEqual(self.client.post(reverse('google_callback')).status_code, 405)


@override_settings(**ON)
class OtherControlsTests(GoogleFlowMixin, TestCase):
    """Lockout and two-step sign-in around a Google sign-in."""

    def test_a_user_with_two_step_sign_in_still_gives_a_code(self):
        from core.tests.test_account_security import enrol

        self.link()
        enrol(self.user)
        self.sign_in()
        self.assertEqual(int(self.signed_in_as()), self.user.pk)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('mfa_verify'), response.url)

    @override_settings(MFA_REQUIRED_FOR_ADMINS=True)
    def test_an_administrator_without_two_step_is_sent_to_set_up(self):
        admin = make_admin('boss', email='boss@example.com')
        self.link(user=admin, sub='admin-sub', email='boss@example.com')
        self.sign_in(claims={'sub': 'admin-sub', 'email': 'boss@example.com'})
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('mfa_setup'), response.url)

    @override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
    def test_a_password_lockout_does_not_block_google(self):
        AxesProxyHandler.reset_attempts()
        self.addCleanup(AxesProxyHandler.reset_attempts)
        self.addCleanup(AxesProxyHandler.reset_logs)
        self.link()
        guesser = Client()
        for _ in range(3):
            guesser.post(reverse('login'), {'username': 'jo@example.com', 'password': 'wrong'})
        locked = guesser.post(reverse('login'), {'username': 'jo@example.com', 'password': PW})
        self.assertEqual(locked.status_code, 429)

        self.sign_in()
        self.assertEqual(int(self.signed_in_as()), self.user.pk)

    def test_the_google_backend_never_accepts_a_password(self):
        from core.auth_backends import GoogleBackend

        self.assertIsNone(GoogleBackend().authenticate(None, username='jo@example.com', password=PW))
        self.assertIsNone(GoogleBackend().get_user(999999))


@override_settings(**ON)
class ConnectTests(GoogleFlowMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def connect(self, **kwargs):
        self.start(mode='connect')
        return self.callback(**kwargs)

    def test_account_security_offers_connect(self):
        page = self.client.get(reverse('account_security'))
        self.assertContains(page, 'Connect Google')
        self.assertContains(page, 'name="mode" value="connect"')
        # A redirect to Google cannot be followed by an HTMX request.
        self.assertContains(page, f'action="{reverse("google_start")}" hx-boost="false"')

    def test_connect_hints_the_users_own_address(self):
        response = self.start(mode='connect')
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query['login_hint'], ['jo@example.com'])

    def test_connect_links_the_google_account(self):
        response = self.connect(claims={'hd': 'example.com', 'email': 'Jo@Example.com'})
        self.assertRedirects(response, reverse('account_security'), fetch_redirect_response=False)
        identity = GoogleIdentity.objects.get(user=self.user)
        self.assertEqual(identity.sub, SUB)
        self.assertEqual(identity.hosted_domain, 'example.com')
        event = AuditEvent.objects.get(action=AuditEvent.Action.GOOGLE_LINKED)
        self.assertEqual(event.target_user, self.user)
        page = self.client.get(reverse('account_security'))
        self.assertContains(page, 'Connected.')

        # And then it signs in.
        self.client.post(reverse('logout'))
        self.sign_in()
        self.assertEqual(int(self.signed_in_as()), self.user.pk)

    def test_another_email_address_is_refused(self):
        response = self.connect(claims={'email': 'someone.else@example.com'})
        self.assertRedirects(response, reverse('account_security'), fetch_redirect_response=False)
        self.assertFalse(GoogleIdentity.objects.exists())
        self.assertContains(self.client.get(reverse('account_security')), 'someone.else@example.com')

    def test_a_google_account_in_use_by_another_user_is_refused(self):
        other = make_user_with_access('other@example.com', horses='view')
        self.link(user=other, email='other@example.com')
        self.connect()
        self.assertFalse(GoogleIdentity.objects.filter(user=self.user).exists())
        self.assertContains(
            self.client.get(reverse('account_security')), 'connected to another Cgate user',
        )

    def test_a_second_google_account_is_refused(self):
        self.link(sub='first-google-account')
        self.connect()
        self.assertEqual(GoogleIdentity.objects.get(user=self.user).sub, 'first-google-account')

    def test_connect_is_refused_if_the_user_changed_while_at_google(self):
        self.start(mode='connect')
        flow = self.flow()
        other = make_user_with_access('other@example.com', horses='view')
        self.client.force_login(other)
        session = self.client.session
        session[SESSION_KEY] = flow
        session.save()
        self.callback()
        self.assertFalse(GoogleIdentity.objects.exists())

    def test_disconnect_removes_the_link(self):
        self.link()
        response = self.client.post(reverse('google_disconnect'))
        self.assertRedirects(response, reverse('account_security'), fetch_redirect_response=False)
        self.assertFalse(GoogleIdentity.objects.exists())
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.GOOGLE_UNLINKED).exists())
        # The Google account no longer opens the user's account.
        self.client.post(reverse('logout'))
        self.sign_in()
        self.assertIsNone(self.signed_in_as())

    def test_disconnect_is_refused_without_a_password(self):
        self.link()
        self.user.set_unusable_password()
        self.user.save(update_fields=['password'])
        self.client.force_login(self.user)
        self.client.post(reverse('google_disconnect'))
        self.assertTrue(GoogleIdentity.objects.filter(user=self.user).exists())
        page = self.client.get(reverse('account_security'))
        self.assertNotContains(page, f'action="{reverse("google_disconnect")}"')

    def test_disconnect_needs_post_and_a_signed_in_user(self):
        self.link()
        self.assertEqual(self.client.get(reverse('google_disconnect')).status_code, 405)
        Client().post(reverse('google_disconnect'))
        self.assertTrue(GoogleIdentity.objects.exists())

    def test_deleting_the_user_deletes_the_link(self):
        self.link()
        self.user.delete()
        self.assertFalse(GoogleIdentity.objects.exists())


# ── Accepting an invitation with Google (phase 3, flow A) ────────────────────

@override_settings(**ON)
class InviteTests(GoogleFlowMixin, TestCase):
    """The invitation link proves the mailbox; Google proves the account."""

    def setUp(self):
        super().setUp()
        from core.models import Role

        self.admin = make_admin('inviter', email='inviter@example.com', first_name='Ada')
        role = Role.objects.create(name='Groom', access={'horses': 'view'})
        admin_client = Client()
        admin_client.force_login(self.admin)
        admin_client.post(reverse('user_create'), {
            'first_name': 'Nina', 'last_name': '', 'email': 'new@example.com',
            'role': role.pk, 'password1': '', 'password2': '',
        })
        self.invited = User.objects.get(email='new@example.com')
        self.admin_client = admin_client
        match = re.search(r'https?://[^/]+(/accounts/invite/\S+)', mail.outbox[-1].body)
        self.link_url = match.group(1)
        self.uidb64 = self.link_url.split('/')[3]

    def open_invitation(self, client=None):
        """Open the emailed link, as the browser does: the token moves into
        the session and the address changes to ``set-password``."""
        client = client or self.client
        response = client.get(self.link_url)
        self.assertEqual(response.status_code, 302)
        return client.get(response.url)

    def accept(self, client=None, **kwargs):
        client = client or self.client
        self.open_invitation(client)
        self.start(client, mode='invite', uidb64=self.uidb64)
        return self.callback(client, **kwargs)

    def google(self, **changes):
        return {'email': 'new@example.com', 'sub': 'new-person-sub', **changes}

    # The page and the email

    def test_invitation_page_offers_google_and_a_password(self):
        page = self.open_invitation()
        self.assertContains(page, 'Continue with Google')
        self.assertContains(page, 'name="mode" value="invite"')
        self.assertContains(page, f'name="uidb64" value="{self.uidb64}"')
        self.assertContains(page, 'name="new_password1"')

    @override_settings(GOOGLE_SIGN_IN_ENABLED=False)
    def test_no_google_button_when_the_feature_is_off(self):
        self.assertNotContains(self.open_invitation(), 'Continue with Google')

    def test_invitation_email_mentions_google(self):
        self.assertIn('sign in with Google or choose a password', mail.outbox[-1].body)

    @override_settings(GOOGLE_SIGN_IN_ENABLED=False)
    def test_invitation_email_without_google(self):
        mail.outbox.clear()
        self.admin_client.post(reverse('user_update', args=[self.invited.pk]), {'resend_invite': '1'})
        self.assertNotIn('Google', mail.outbox[-1].body)

    # Starting

    def test_start_hints_the_invited_address(self):
        self.open_invitation()
        response = self.start(mode='invite', uidb64=self.uidb64)
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query['login_hint'], ['new@example.com'])
        flow = self.flow()
        self.assertEqual(flow['mode'], 'invite')
        self.assertEqual(flow['invite_user_id'], self.invited.pk)

    def test_start_needs_the_invitation_opened_in_this_session(self):
        response = self.start(mode='invite', uidb64=self.uidb64)
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_start_refuses_another_users_id(self):
        self.open_invitation()
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        other = urlsafe_base64_encode(force_bytes(self.user.pk))
        response = self.start(mode='invite', uidb64=other)
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertNotIn(SESSION_KEY, self.client.session)

    # Finishing

    def test_accepting_links_the_account_and_signs_in(self):
        response = self.accept(claims=self.google(given_name='Nia', family_name='Rider'))
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.assertEqual(int(self.signed_in_as()), self.invited.pk)
        self.assertEqual(self.client.session[BACKEND_SESSION_KEY], 'core.auth_backends.GoogleBackend')
        identity = GoogleIdentity.objects.get(user=self.invited)
        self.assertEqual(identity.sub, 'new-person-sub')
        self.invited.refresh_from_db()
        self.assertFalse(self.invited.has_usable_password())
        # The name the Administrator typed stays; what they left blank
        # comes from Google.
        self.assertEqual((self.invited.first_name, self.invited.last_name), ('Nina', 'Rider'))
        actions = set(AuditEvent.objects.filter(target_user=self.invited).values_list('action', flat=True))
        self.assertIn(AuditEvent.Action.GOOGLE_LINKED, actions)
        self.assertIn(AuditEvent.Action.INVITE_ACCEPTED, actions)

        # And it signs in again next time.
        self.client.post(reverse('logout'))
        self.sign_in(claims=self.google())
        self.assertEqual(int(self.signed_in_as()), self.invited.pk)

    def test_the_link_works_once(self):
        self.accept(claims=self.google())
        self.client.post(reverse('logout'))
        again = Client().get(self.link_url)
        self.assertContains(again, 'expired or was already used')

    def test_another_email_address_is_refused(self):
        response = self.accept(claims=self.google(email='someone.else@gmail.com'))
        # Back to the invitation page, which still works.
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/accounts/invite/{self.uidb64}/set-password/', response.url)
        self.assertFalse(GoogleIdentity.objects.exists())
        self.assertIsNone(self.signed_in_as())
        page = self.client.get(response.url)
        self.assertContains(page, 'someone.else@gmail.com')
        self.assertContains(page, 'name="new_password1"')
        self.assertTrue(AuditEvent.objects.filter(
            action=AuditEvent.Action.GOOGLE_SIGN_IN_REFUSED, target_user=self.invited,
        ).exists())

    def test_email_case_is_ignored(self):
        self.accept(claims=self.google(email='New@Example.COM'))
        self.assertEqual(int(self.signed_in_as()), self.invited.pk)

    def test_a_google_account_linked_to_another_user_is_refused(self):
        self.link()  # jo@example.com has SUB
        response = self.accept(claims=self.google(sub=SUB))
        self.assertIn('/set-password/', response.url)
        self.assertFalse(GoogleIdentity.objects.filter(user=self.invited).exists())
        self.assertIsNone(self.signed_in_as())
        self.assertContains(self.client.get(response.url), 'connected to another Cgate user')

    def test_an_invitation_sent_again_while_at_google_is_refused(self):
        self.open_invitation()
        self.start(mode='invite', uidb64=self.uidb64)
        # The Administrator sends a new invitation; the old token is void.
        self.admin_client.post(reverse('user_update', args=[self.invited.pk]), {'resend_invite': '1'})
        response = self.callback(claims=self.google())
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertFalse(GoogleIdentity.objects.exists())
        self.assertIsNone(self.signed_in_as())

    def test_a_user_deactivated_while_at_google_is_refused(self):
        self.open_invitation()
        self.start(mode='invite', uidb64=self.uidb64)
        User.objects.filter(pk=self.invited.pk).update(is_active=False)
        self.callback(claims=self.google())
        self.assertFalse(GoogleIdentity.objects.exists())
        self.assertIsNone(self.signed_in_as())

    def test_cancel_at_google_goes_back_to_the_invitation(self):
        self.open_invitation()
        self.start(mode='invite', uidb64=self.uidb64)
        response = self.callback(error='access_denied', code=None)
        self.assertIn('/set-password/', response.url)
        self.assertContains(self.client.get(response.url), 'cancelled')

    def test_the_password_still_works_after_a_refusal(self):
        response = self.accept(claims=self.google(email='wrong@gmail.com'))
        new_pw = 'my-own-long-password'
        self.client.post(response.url, {'new_password1': new_pw, 'new_password2': new_pw})
        self.invited.refresh_from_db()
        self.assertTrue(self.invited.check_password(new_pw))

    @override_settings(MFA_REQUIRED_FOR_ADMINS=True)
    def test_an_administrator_must_still_set_up_two_step_sign_in(self):
        from core.models import UserRole
        from core.roles_testutils import administrator_role

        UserRole.objects.filter(user=self.invited).update(role=administrator_role())
        response = self.accept(claims=self.google())
        self.assertEqual(int(self.signed_in_as()), self.invited.pk)
        follow = self.client.get(response.url)
        self.assertEqual(follow.status_code, 302)
        self.assertIn(reverse('mfa_setup'), follow.url)


# ── Administrator controls (phase 3) ─────────────────────────────────────────

@override_settings(**ON)
class AdminControlTests(GoogleFlowMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = make_admin('boss', email='boss@example.com')
        self.client.force_login(self.admin)
        self.url = reverse('user_update', args=[self.user.pk])

    def test_sign_in_row_shows_the_password(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'Signs in with a password')
        self.assertNotContains(page, 'Disconnect Google')

    def test_sign_in_row_shows_google(self):
        self.link()
        page = self.client.get(self.url)
        self.assertContains(page, 'Signs in with Google or a password')
        self.assertContains(page, 'Google account jo@example.com')
        self.assertContains(page, 'Disconnect Google')

    def test_disconnect_removes_the_link_and_ends_sessions(self):
        self.link()
        jo = Client()
        jo.force_login(self.user)
        self.client.post(self.url, {'google_disconnect': '1'})
        self.assertFalse(GoogleIdentity.objects.exists())
        self.assertIsNone(jo.session.get('_auth_user_id'))
        event = AuditEvent.objects.get(action=AuditEvent.Action.GOOGLE_UNLINKED)
        self.assertEqual(event.target_user, self.user)
        self.assertEqual(event.actor, self.admin)

    def test_a_google_only_user_can_then_be_invited_again(self):
        self.link()
        self.user.set_unusable_password()
        self.user.save(update_fields=['password'])
        page = self.client.get(self.url)
        self.assertContains(page, 'Signs in with Google<')
        self.assertNotContains(page, 'Send Invitation Again')
        mail.outbox.clear()
        # An invitation now would be no use: the Google link still opens
        # the account. The view refuses it, as the page hides it.
        self.client.post(self.url, {'resend_invite': '1'})
        self.assertEqual(len(mail.outbox), 0)

        self.client.post(self.url, {'google_disconnect': '1'})
        page = self.client.get(self.url)
        self.assertContains(page, 'send them a new invitation')
        self.assertContains(page, 'Send Invitation Again')
        self.client.post(self.url, {'resend_invite': '1'})
        self.assertEqual(len(mail.outbox), 1)

    def test_own_google_account_is_not_disconnected_here(self):
        self.link(user=self.admin, email='boss@example.com')
        page = self.client.get(reverse('user_update', args=[self.admin.pk]))
        self.assertNotContains(page, 'Disconnect Google</button>')
        self.client.post(reverse('user_update', args=[self.admin.pk]), {'google_disconnect': '1'})
        self.assertTrue(GoogleIdentity.objects.filter(user=self.admin).exists())

    def test_a_manager_cannot_disconnect_an_administrator(self):
        other_admin = make_admin('admin2', email='admin2@example.com')
        self.link(user=other_admin, email='admin2@example.com')
        manager = make_user_with_access('manager@example.com', users='full')
        self.client.force_login(manager)
        self.client.post(reverse('user_update', args=[other_admin.pk]), {'google_disconnect': '1'})
        self.assertTrue(GoogleIdentity.objects.filter(user=other_admin).exists())


class ContentSecurityPolicyTests(SimpleTestCase):
    def test_form_action_allows_google_only_when_the_feature_is_on(self):
        # Chrome applies form-action to the redirect that follows a form
        # post, so the button needs Google's host there. The policy is
        # built when settings load; check the rule it follows.
        from django.conf import settings

        allowed = 'https://accounts.google.com' in settings.CSP_POLICY['DIRECTIVES']['form-action']
        self.assertEqual(allowed, settings.GOOGLE_SIGN_IN_ENABLED)
