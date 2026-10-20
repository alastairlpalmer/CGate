"""Encrypt backup files before they leave the server.

docs/LAUNCH_READINESS_PLAN.md, P1 work package 8.

The bucket provider encrypts what it stores, but holds the key itself. With
``BACKUP_ENCRYPTION_KEY`` set, each backup is also encrypted here, before
upload, with a key the provider never sees. A leaked bucket token then
gives an attacker files they cannot read.

Format (``.enc``): a header line, then the file in chunks. Each chunk is
AES-256-GCM with a fresh nonce. Its position and whether it is the last
chunk are bound into the tag, so a chunk cannot be moved, dropped, or the
file cut short without decryption failing. Chunks keep memory use flat for
a large media archive.

Keys are 32 random bytes in URL-safe base64. Make one with
``python manage.py backup --new-key``. ``BACKUP_DECRYPTION_KEYS`` holds
earlier keys (comma-separated) so backups taken before a key change can
still be restored. **Store the key somewhere other than the server**: a
backup is no use if the key is lost with the server.
"""

import base64
import hashlib
import os
import secrets
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

SUFFIX = '.enc'
MAGIC = b'YARDWAY-BACKUP-1\n'
CHUNK_SIZE = 4 * 1024 * 1024
_NONCE = 12
_KEY_ID = 8


class BackupKeyError(RuntimeError):
    """A key is missing, malformed, or does not match the file."""


def new_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def _decode(text):
    try:
        key = base64.urlsafe_b64decode(text.strip().encode())
    except ValueError as exc:
        raise BackupKeyError('A backup key is not valid base64.') from exc
    if len(key) != 32:
        raise BackupKeyError('A backup key must be 32 bytes (44 characters of base64).')
    return key


def _key_id(key):
    return hashlib.sha256(key).digest()[:_KEY_ID]


def encryption_key():
    """The key new backups are encrypted with, or None when not set."""
    text = getattr(settings, 'BACKUP_ENCRYPTION_KEY', '')
    return _decode(text) if text else None


def _decryption_keys():
    texts = [getattr(settings, 'BACKUP_ENCRYPTION_KEY', '')]
    texts += [t for t in getattr(settings, 'BACKUP_DECRYPTION_KEYS', '').split(',')]
    return {_key_id(k): k for k in (_decode(t) for t in texts if t.strip())}


def _aad(index, last):
    return struct.pack('>QB', index, 1 if last else 0)


def encrypt_file(source, key):
    """Write ``source`` + '.enc' and return its path. Deletes nothing."""
    source = Path(source)
    target = source.with_name(source.name + SUFFIX)
    cipher = AESGCM(key)
    with source.open('rb') as plain, target.open('wb') as out:
        out.write(MAGIC + _key_id(key))
        index = 0
        chunk = plain.read(CHUNK_SIZE)
        while True:
            following = plain.read(CHUNK_SIZE)
            last = not following
            nonce = os.urandom(_NONCE)
            sealed = cipher.encrypt(nonce, chunk, _aad(index, last))
            out.write(struct.pack('>I', len(sealed)) + nonce + sealed)
            if last:
                return target
            chunk = following
            index += 1


def decrypt_file(source):
    """Decrypt ``source`` (ending '.enc') next to itself; return the path."""
    from cryptography.exceptions import InvalidTag

    source = Path(source)
    target = source.with_name(source.name[:-len(SUFFIX)])
    with source.open('rb') as sealed_file:
        if sealed_file.read(len(MAGIC)) != MAGIC:
            raise BackupKeyError(f'{source.name} is not an encrypted Yardway backup.')
        key = _decryption_keys().get(sealed_file.read(_KEY_ID))
        if key is None:
            raise BackupKeyError(
                f'No key for {source.name}. Set BACKUP_ENCRYPTION_KEY, or add the key '
                'it was made with to BACKUP_DECRYPTION_KEYS.'
            )
        cipher = AESGCM(key)
        index = 0
        header = sealed_file.read(4)
        with target.open('wb') as out:
            while True:
                if len(header) < 4:
                    raise BackupKeyError(f'{source.name} is cut short.')
                (length,) = struct.unpack('>I', header)
                nonce = sealed_file.read(_NONCE)
                sealed = sealed_file.read(length)
                if len(nonce) < _NONCE or len(sealed) < length:
                    raise BackupKeyError(f'{source.name} is cut short.')
                # The last chunk was sealed as last. A file cut after an
                # earlier chunk, or with bytes added, fails the tag check.
                following = sealed_file.read(4)
                last = not following
                try:
                    out.write(cipher.decrypt(nonce, sealed, _aad(index, last)))
                except InvalidTag as exc:
                    raise BackupKeyError(f'{source.name} is damaged, cut short or was changed.') from exc
                if last:
                    return target
                header = following
                index += 1


def maybe_encrypt(path, key_name):
    """Encrypt when a key is set. Returns (path to upload, object key)."""
    key = encryption_key()
    if key is None:
        return Path(path), key_name
    return encrypt_file(path, key), key_name + SUFFIX


def maybe_decrypt(path):
    """Decrypt a downloaded '.enc' file; return the path to use."""
    path = Path(path)
    if not path.name.endswith(SUFFIX):
        return path
    return decrypt_file(path)
