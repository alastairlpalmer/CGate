"""Model fields that store their value encrypted.

See core/encryption.py for the key handling and the threat model.
"""

from django.db import models

from .encryption import decrypt, encrypt


class EncryptedTextField(models.TextField):
    """A TextField whose value is encrypted in the database.

    Reads and writes look exactly like a normal TextField, so call sites
    need no changes and comparisons between two instances still work on
    plain text.

    Two limits worth knowing:

    * You cannot filter or order by this column. Fernet output is
      randomised, so ``filter(token='abc')`` will never match. Nothing in
      this app queries a token, and nothing should start.
    * The column is text, and ciphertext is roughly a third longer than
      the input plus overhead. TextField has no length limit, so this
      only matters if the field is ever narrowed to a CharField.
    """

    def from_db_value(self, value, expression, connection):
        return decrypt(value)

    def get_prep_value(self, value):
        return encrypt(super().get_prep_value(value))

    def to_python(self, value):
        # Used by deserialisation (loaddata) and full_clean, which can hand
        # back a value straight from the database.
        return decrypt(super().to_python(value))
