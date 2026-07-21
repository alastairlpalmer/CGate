"""
Xero API HTTP client.

Handles OAuth token management and authenticated API requests
using the requests library directly.
"""

import logging
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

XERO_AUTHORIZE_URL = 'https://login.xero.com/identity/connect/authorize'
XERO_TOKEN_URL = 'https://identity.xero.com/connect/token'
XERO_CONNECTIONS_URL = 'https://api.xero.com/connections'
XERO_API_BASE = 'https://api.xero.com/api.xro/2.0'


class XeroAuthError(Exception):
    """Raised when Xero authentication fails."""


class XeroTokenExpiredError(XeroAuthError):
    """Raised when refresh token has expired (re-auth required)."""


class XeroAPIError(Exception):
    """Raised when a Xero API call fails."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class XeroClient:
    """HTTP client for Xero API with automatic token management."""

    def __init__(self):
        from .models import XeroConnection
        self.connection = XeroConnection.get_connection()

    # ── OAuth helpers ──

    @staticmethod
    def get_authorization_url(state):
        """Build the Xero OAuth2 authorization URL."""
        params = {
            'response_type': 'code',
            'client_id': settings.XERO_CLIENT_ID,
            'redirect_uri': settings.XERO_REDIRECT_URI,
            'scope': settings.XERO_SCOPES,
            'state': state,
        }
        return f'{XERO_AUTHORIZE_URL}?{urlencode(params)}'

    @staticmethod
    def exchange_code_for_tokens(code):
        """Exchange authorization code for access + refresh tokens.

        Returns dict with access_token, refresh_token, expires_in.
        """
        response = requests.post(
            XERO_TOKEN_URL,
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': settings.XERO_REDIRECT_URI,
                'client_id': settings.XERO_CLIENT_ID,
                'client_secret': settings.XERO_CLIENT_SECRET,
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=15,
        )
        if response.status_code != 200:
            raise XeroAuthError(
                f'Token exchange failed: {response.status_code} {response.text}'
            )
        return response.json()

    @staticmethod
    def get_tenant_connections(access_token):
        """Fetch tenant connections from Xero.

        Returns list of dicts with tenantId, tenantName, etc.
        """
        response = requests.get(
            XERO_CONNECTIONS_URL,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=15,
        )
        if response.status_code != 200:
            raise XeroAuthError(
                f'Failed to fetch connections: {response.status_code} {response.text}'
            )
        return response.json()

    # ── Token management ──

    def _refresh_access_token(self):
        """Refresh the access token using the stored refresh token.

        Refresh tokens rotate on every use, and gunicorn workers, the Celery
        worker and beat can all hold this singleton at once — so the refresh
        is serialised on the connection row and re-read under the lock: if
        another process already refreshed, adopt its token instead of
        burning the rotated refresh token again.

        Only a definitive ``invalid_grant`` deactivates the connection. Any
        other failure (Xero 429/5xx, network blip) is transient — treating
        it as "refresh token expired" used to permanently kill the
        integration on the first identity-endpoint hiccup.
        """
        from django.db import transaction

        from .models import XeroConnection

        with transaction.atomic():
            conn = XeroConnection.objects.select_for_update().get(
                pk=self.connection.pk
            )
            # Someone else refreshed while we waited for the lock.
            if (
                conn.access_token != self.connection.access_token
                and not conn.is_token_expired
            ):
                self.connection = conn
                return

            if not conn.refresh_token:
                raise XeroTokenExpiredError(
                    'No refresh token available. Please reconnect.'
                )

            try:
                response = requests.post(
                    XERO_TOKEN_URL,
                    data={
                        'grant_type': 'refresh_token',
                        'refresh_token': conn.refresh_token,
                        'client_id': settings.XERO_CLIENT_ID,
                        'client_secret': settings.XERO_CLIENT_SECRET,
                    },
                    headers={'Content-Type': 'application/x-www-form-urlencoded'},
                    timeout=15,
                )
            except requests.RequestException as exc:
                raise XeroAuthError(
                    f'Could not reach Xero to refresh the token: {exc}'
                ) from exc

            if response.status_code != 200:
                body = response.text or ''
                is_invalid_grant = (
                    response.status_code in (400, 401)
                    and 'invalid_grant' in body
                )
                if is_invalid_grant:
                    conn.is_active = False
                    conn.save(update_fields=['is_active'])
                    self.connection = conn
                    raise XeroTokenExpiredError(
                        'Refresh token expired. Please reconnect to Xero.'
                    )
                logger.warning(
                    'Transient Xero token refresh failure: %s %s',
                    response.status_code, body[:500],
                )
                raise XeroAuthError(
                    f'Xero token refresh failed ({response.status_code}); '
                    'connection kept — will retry.'
                )

            data = response.json()
            now = timezone.now()
            conn.access_token = data['access_token']
            conn.refresh_token = data['refresh_token']
            conn.token_expires_at = now + timezone.timedelta(
                seconds=data.get('expires_in', 1800)
            )
            conn.last_refreshed_at = now
            conn.save(update_fields=[
                'access_token', 'refresh_token', 'token_expires_at',
                'last_refreshed_at',
            ])
            self.connection = conn
        logger.info('Xero access token refreshed successfully')

    def _ensure_valid_token(self):
        """Ensure we have a valid access token, refreshing if needed."""
        if not self.connection.is_connected:
            raise XeroTokenExpiredError('Xero is not connected. Please connect first.')
        if self.connection.is_token_expired:
            self._refresh_access_token()

    # ── API requests ──

    def _api_request(self, method, endpoint, json_data=None, params=None,
                     idempotency_key=None):
        """Make an authenticated request to the Xero API.

        Network failures surface as XeroAPIError so every caller's
        record-the-error path handles them — a raw requests exception used
        to escape push_invoice_to_xero without writing a sync record at
        all, leaving the app claiming a push failed that Xero had actually
        accepted. ``idempotency_key`` (Xero's Idempotency-Key header) makes
        retried POSTs safe: a repeat within 24h returns the original result
        instead of creating a duplicate.
        """
        self._ensure_valid_token()

        url = f'{XERO_API_BASE}/{endpoint}'
        headers = {
            'Authorization': f'Bearer {self.connection.access_token}',
            'Xero-Tenant-Id': self.connection.xero_tenant_id,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key

        try:
            response = requests.request(
                method, url,
                headers=headers,
                json=json_data,
                params=params,
                timeout=20,
            )

            if response.status_code == 401:
                # Token may have expired between check and request — try once more
                self._refresh_access_token()
                headers['Authorization'] = f'Bearer {self.connection.access_token}'
                response = requests.request(
                    method, url,
                    headers=headers,
                    json=json_data,
                    params=params,
                    timeout=20,
                )
        except requests.RequestException as exc:
            raise XeroAPIError(
                f'Network error talking to Xero: {exc}',
            ) from exc

        if response.status_code >= 400:
            raise XeroAPIError(
                f'Xero API error: {response.status_code}',
                status_code=response.status_code,
                response_body=response.text,
            )

        return response.json()

    # ── Contacts ──

    def find_contact_by_name(self, name):
        """Search for a contact by exact name. Returns contact dict or None."""
        # Xero API filtering uses == for exact match
        safe_name = name.replace('"', '\\"')
        data = self._api_request(
            'GET', 'Contacts',
            params={'where': f'Name=="{safe_name}"'},
        )
        contacts = data.get('Contacts', [])
        return contacts[0] if contacts else None

    def create_contact(self, contact_data):
        """Create a new contact in Xero.

        contact_data should include: Name, EmailAddress, and Address fields.
        Returns the created contact dict.
        """
        data = self._api_request('POST', 'Contacts', json_data=contact_data)
        contacts = data.get('Contacts', [])
        if not contacts:
            raise XeroAPIError('No contact returned from Xero after creation')
        return contacts[0]

    # ── Invoices ──

    def create_invoice(self, invoice_data, idempotency_key=None):
        """Create an invoice in Xero.

        invoice_data should be a Xero-compatible invoice dict.
        Returns the created invoice dict.
        """
        data = self._api_request(
            'POST', 'Invoices', json_data=invoice_data,
            idempotency_key=idempotency_key,
        )
        invoices = data.get('Invoices', [])
        if not invoices:
            raise XeroAPIError('No invoice returned from Xero after creation')
        return invoices[0]

    def get_invoice(self, xero_invoice_id):
        """Fetch a single invoice from Xero by ID."""
        data = self._api_request('GET', f'Invoices/{xero_invoice_id}')
        invoices = data.get('Invoices', [])
        if not invoices:
            raise XeroAPIError(f'Invoice {xero_invoice_id} not found in Xero')
        return invoices[0]
