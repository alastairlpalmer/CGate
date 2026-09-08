"""The Xero OAuth tokens must not be readable in the database.

The refresh token is a long-lived bearer credential for the yard's Xero
accounting data. Anyone who can read that column — from a backup, a
snapshot, a stray query — could use it without going near this app.
"""

from django.db import connection
from django.test import TestCase, override_settings

from core.encryption import PREFIX, decrypt, encrypt

from .models import XeroConnection

ACCESS = 'access-token-value-abc123'
REFRESH = 'refresh-token-value-xyz789'


# A column name cannot be a bound parameter, so the statements are written
# out in full rather than built by string formatting.
RAW_SELECTS = {
    'access_token':
        'SELECT access_token FROM xero_integration_xeroconnection WHERE id = %s',
    'refresh_token':
        'SELECT refresh_token FROM xero_integration_xeroconnection WHERE id = %s',
}


def raw_column(pk, column):
    """The bytes actually stored, bypassing the field's decryption."""
    with connection.cursor() as cursor:
        cursor.execute(RAW_SELECTS[column], [pk])
        return cursor.fetchone()[0]


class TokenEncryptionTests(TestCase):
    def test_tokens_are_not_stored_in_plain_text(self):
        conn = XeroConnection.objects.create(
            access_token=ACCESS, refresh_token=REFRESH,
        )

        for column, value in (
            ('access_token', ACCESS),
            ('refresh_token', REFRESH),
        ):
            stored = raw_column(conn.pk, column)
            self.assertNotIn(value, stored)
            self.assertTrue(stored.startswith(PREFIX))

    def test_round_trip_through_the_orm_is_transparent(self):
        conn = XeroConnection.objects.create(
            access_token=ACCESS, refresh_token=REFRESH,
        )
        fresh = XeroConnection.objects.get(pk=conn.pk)
        self.assertEqual(fresh.access_token, ACCESS)
        self.assertEqual(fresh.refresh_token, REFRESH)

    def test_two_rows_with_the_same_token_look_different(self):
        """Fernet output is randomised. Identical ciphertext would tell an
        attacker which accounts share a value."""
        first = encrypt(ACCESS)
        second = encrypt(ACCESS)
        self.assertNotEqual(first, second)
        self.assertEqual(decrypt(first), decrypt(second))

    def test_empty_token_stays_empty(self):
        """An empty token means "not connected". Encrypting it would make
        is_connected read as True."""
        conn = XeroConnection.objects.create(access_token='', refresh_token='')
        self.assertEqual(raw_column(conn.pk, 'access_token'), '')
        self.assertFalse(
            XeroConnection.objects.get(pk=conn.pk).is_connected
        )

    def test_legacy_plain_text_is_still_readable(self):
        """Rows written before encryption existed must keep working until
        they are next saved — otherwise this change would silently
        disconnect a live Xero integration."""
        conn = XeroConnection.objects.create()
        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE xero_integration_xeroconnection '
                'SET refresh_token = %s WHERE id = %s',
                [REFRESH, conn.pk],
            )

        fresh = XeroConnection.objects.get(pk=conn.pk)
        self.assertEqual(fresh.refresh_token, REFRESH)

        # Saving it migrates the row to ciphertext.
        fresh.save(update_fields=['refresh_token'])
        self.assertTrue(raw_column(conn.pk, 'refresh_token').startswith(PREFIX))

    def test_double_encryption_is_not_possible(self):
        """save() twice must not wrap the value again — the second read
        would then return ciphertext instead of the token."""
        conn = XeroConnection.objects.create(refresh_token=REFRESH)
        conn.save()
        conn.save()
        self.assertEqual(
            XeroConnection.objects.get(pk=conn.pk).refresh_token, REFRESH,
        )

    def test_a_wrong_key_reads_as_disconnected_not_as_a_crash(self):
        """If the key changes, the app must ask for a reconnect rather than
        raise on every page that touches the connection."""
        conn = XeroConnection.objects.create(
            refresh_token=REFRESH, is_active=True,
        )
        # A different key, so nothing already stored can be decrypted.
        from cryptography.fernet import Fernet
        with override_settings(FIELD_ENCRYPTION_KEYS=[Fernet.generate_key().decode()]):
            import core.encryption as enc
            original = enc._fernet
            enc._fernet = enc._build_fernet()
            try:
                fresh = XeroConnection.objects.get(pk=conn.pk)
                self.assertEqual(fresh.refresh_token, '')
                self.assertFalse(fresh.is_connected)
            finally:
                enc._fernet = original


class KeyRotationTests(TestCase):
    def test_an_old_key_can_still_decrypt_after_rotation(self):
        """Rotation works by putting the new key first and keeping the old
        one. Without that, every stored token would be lost on rotation."""
        from cryptography.fernet import Fernet

        import core.encryption as enc

        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()
        original = enc._fernet

        try:
            with override_settings(FIELD_ENCRYPTION_KEYS=[old_key]):
                enc._fernet = enc._build_fernet()
                ciphertext = enc.encrypt(REFRESH)

            # New key first, old key retained.
            with override_settings(FIELD_ENCRYPTION_KEYS=[new_key, old_key]):
                enc._fernet = enc._build_fernet()
                self.assertEqual(enc.decrypt(ciphertext), REFRESH)
                # Anything written now uses the new key.
                fresh = enc.encrypt(REFRESH)

            # Old key dropped: the new ciphertext still reads.
            with override_settings(FIELD_ENCRYPTION_KEYS=[new_key]):
                enc._fernet = enc._build_fernet()
                self.assertEqual(enc.decrypt(fresh), REFRESH)
        finally:
            enc._fernet = original
