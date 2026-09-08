"""Encrypt the Xero tokens that are already in the database.

Migration 0003 changed the column type. This one rewrites the rows: the
historical model uses EncryptedTextField, so reading a legacy plain-text
value passes it through unchanged and saving it writes ciphertext.

Safe to run more than once. An already-encrypted value decrypts on read and
re-encrypts on save, which is a no-op in effect.
"""

from django.db import migrations

FIELDS = ('access_token', 'refresh_token')


def encrypt_tokens(apps, schema_editor):
    XeroConnection = apps.get_model('xero_integration', 'XeroConnection')
    for conn in XeroConnection.objects.all():
        if any(getattr(conn, name) for name in FIELDS):
            conn.save(update_fields=list(FIELDS))


def decrypt_tokens(apps, schema_editor):
    """Reverse: write the tokens back as plain text.

    Only for rolling this migration back. It uses the same key, so it can
    only run while that key is still available.
    """
    from core.encryption import decrypt

    XeroConnection = apps.get_model('xero_integration', 'XeroConnection')
    for conn in XeroConnection.objects.all():
        updates = {}
        for name in FIELDS:
            # Reading through the field has already decrypted the value,
            # so this second call is a no-op unless the row was stored
            # double-wrapped by an interrupted run.
            updates[name] = decrypt(getattr(conn, name))
        XeroConnection.objects.filter(pk=conn.pk).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ('xero_integration', '0003_alter_xeroconnection_access_token_and_more'),
    ]

    operations = [
        migrations.RunPython(encrypt_tokens, decrypt_tokens),
    ]
