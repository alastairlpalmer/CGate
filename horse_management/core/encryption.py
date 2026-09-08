"""Encryption for secrets held in the database.

The Xero OAuth refresh token is the reason this exists. It is a long-lived
bearer credential: anyone who reads that one column can act on the yard's
Xero accounting data — read contacts, read invoices, create invoices —
without touching this app at all. A database backup, a support dump or a
stray SELECT would hand it over in plain text.

What this gives you, and what it does not:

* It removes the token from anything that only reads the database — a
  dumped backup, a snapshot handed to a contractor, a leaked query result.
* It does NOT protect against an attacker who already runs this app's code
  with its environment, because that attacker has the key too. Nothing
  short of an external key service (KMS/HSM) would, and that is far beyond
  what a single-yard deployment needs.

Key material
------------
Set ``FIELD_ENCRYPTION_KEYS`` to one or more Fernet keys, newest first,
separated by commas. The first key encrypts; every key is tried when
decrypting, which is what makes rotation possible:

    1. Generate a key:  python -c "from cryptography.fernet import Fernet; \\
                                   print(Fernet.generate_key().decode())"
    2. Put the new key FIRST, keep the old one after it, and deploy.
    3. Once every stored value has been re-saved, drop the old key.

With no ``FIELD_ENCRYPTION_KEYS`` set, a key is derived from ``SECRET_KEY``
so the app works out of the box. The cost of that default: changing
SECRET_KEY makes existing ciphertext unreadable, and the app then asks you
to reconnect Xero. That is a safe failure, but a dedicated key is better —
it lets SECRET_KEY be rotated on its own.
"""

import logging

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.utils.functional import SimpleLazyObject

logger = logging.getLogger(__name__)

# Marks a stored value as ciphertext. Values without it are treated as
# legacy plain text and passed through, so rows written before this module
# existed keep working until they are next saved.
PREFIX = 'enc$v1$'


def _derive_key_from_secret_key():
    """A Fernet key derived from SECRET_KEY.

    HKDF with a distinct info string, so this key cannot be confused with
    anything else Django derives from SECRET_KEY (session signing, password
    reset tokens). Deriving rather than reusing means a leak of one does
    not directly give the other.
    """
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b'yardway.field-encryption.v1',
        info=b'yardway field encryption key',
    ).derive(settings.SECRET_KEY.encode())
    import base64
    return base64.urlsafe_b64encode(material)


def _build_fernet():
    keys = [
        k.strip() for k in getattr(settings, 'FIELD_ENCRYPTION_KEYS', [])
        if k and k.strip()
    ]
    if not keys:
        return MultiFernet([Fernet(_derive_key_from_secret_key())])
    return MultiFernet([Fernet(k.encode() if isinstance(k, str) else k) for k in keys])


# Lazy: settings are not loaded when this module is first imported.
_fernet = SimpleLazyObject(_build_fernet)


def encrypt(value: str) -> str:
    """Ciphertext for ``value``, carrying the PREFIX marker.

    Empty values stay empty — an empty token means "not connected", and
    encrypting it would hide that from ``is_connected``.
    """
    if not value:
        return value
    if value.startswith(PREFIX):
        return value  # already encrypted; do not wrap it twice
    return PREFIX + _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Plain text for ``value``.

    A value without the PREFIX is legacy plain text and is returned as-is.

    A value that cannot be decrypted (the key changed, or the ciphertext is
    damaged) returns an empty string rather than raising. For the Xero
    token that turns into "not connected", which prompts a reconnect —
    far better than a 500 on every page that touches the connection.
    """
    if not value or not value.startswith(PREFIX):
        return value
    try:
        return _fernet.decrypt(value[len(PREFIX):].encode()).decode()
    except (InvalidToken, ValueError):
        logger.error(
            'Could not decrypt a stored secret. The encryption key has '
            'probably changed (FIELD_ENCRYPTION_KEYS or SECRET_KEY). '
            'Reconnect the affected integration.'
        )
        return ''
