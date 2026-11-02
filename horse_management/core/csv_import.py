"""Read a CSV file whatever program saved it."""

from pathlib import Path

# Tried in this order. UTF-8 (with or without Excel's byte-order mark)
# fails loudly on anything else, so it goes first. cp1252 is what Excel on
# Windows saves as "CSV": it has £, € and curly quotes where latin-1 has
# control codes. latin-1 decodes any byte at all, so it must be last.
CSV_ENCODINGS = ('utf-8-sig', 'cp1252', 'latin-1')


def read_csv_text(filepath):
    """The text of a CSV file and the encoding that read it.

    The whole file is decoded before any row is used. Opening with one
    encoding and falling back on UnicodeDecodeError does not work: open()
    never raises it, the first bad byte does, halfway through the import.
    """
    raw = Path(filepath).read_bytes()
    for encoding in CSV_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise AssertionError('latin-1 decodes every byte')  # pragma: no cover
