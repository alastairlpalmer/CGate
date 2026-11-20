"""Read a spreadsheet of horse records (plan §7.6, phase 1d).

Pure reading and checking: no database writes. The columns are the same
as Download as CSV, so a downloaded file can be changed in Excel and
read back. Each row gives its values, tidied, and its problems; a
problem in one row never stops the other rows.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from django.utils import timezone

from core.identity import (
    normalise_country,
    normalise_microchip,
    normalise_ueln,
    parse_height,
    split_suffix,
    validate_ueln,
)

COLUMNS = [
    'Yard name', 'Registered name', 'Country', 'Foaled', 'Sex', 'Colour', 'Breed',
    'Sire', 'Dam', "Dam's sire", 'Microchip', 'UELN', 'Passport number',
    'Passport issuer', 'Markings', 'Height',
]

# Column heading (tidied) → key. Other spellings people use, too.
_HEADINGS = {
    'yardname': 'name', 'name': 'name', 'horse': 'name', 'horsename': 'name', 'stablename': 'name',
    'registeredname': 'registered_name', 'passportname': 'registered_name',
    'country': 'country', 'suffix': 'country', 'countryofbirth': 'country',
    'foaled': 'foaled', 'dob': 'foaled', 'dateofbirth': 'foaled', 'foalingdate': 'foaled',
    'yearofbirth': 'foaled', 'year': 'foaled', 'born': 'foaled',
    'sex': 'sex', 'gender': 'sex',
    'colour': 'colour', 'color': 'colour',
    'breed': 'breed',
    'sire': 'S', 'dam': 'D', 'damssire': 'DS', 'damsire': 'DS', 'broodmaresire': 'DS',
    'microchip': 'microchip', 'chip': 'microchip', 'microchipnumber': 'microchip', 'chipnumber': 'microchip',
    'ueln': 'ueln',
    'passportnumber': 'passport_number', 'passport': 'passport_number', 'passportno': 'passport_number',
    'passportissuer': 'passport_issuer', 'issuer': 'passport_issuer', 'issuedby': 'passport_issuer',
    'markings': 'markings', 'marks': 'markings',
    'height': 'height', 'heighthh': 'height', 'heighthands': 'height',
}

MAX_ROWS = 2000
MAX_BYTES = 2 * 1024 * 1024

# One-letter forms as on a racecard: m mare, f filly, c colt, g gelding,
# h horse (a stallion).
SEXES = {
    'mare': 'mare', 'm': 'mare', 'f': 'filly',
    'gelding': 'gelding', 'g': 'gelding',
    'stallion': 'stallion', 'h': 'stallion', 'horse': 'stallion', 'entire': 'stallion', 's': 'stallion',
    'colt': 'colt', 'c': 'colt',
    'filly': 'filly',
}

COLOURS = {
    'bay': 'bay', 'b': 'bay', 'darkbay': 'bay', 'bayorbrown': 'brown', 'dkb': 'bay',
    'chestnut': 'chestnut', 'ch': 'chestnut', 'chesnut': 'chestnut',
    'grey': 'grey', 'gray': 'grey', 'gr': 'grey', 'g': 'grey',
    'black': 'black', 'bl': 'black', 'br': 'brown', 'brown': 'brown',
    'palomino': 'palomino', 'skewbald': 'skewbald', 'piebald': 'piebald', 'roan': 'roan',
    'dun': 'dun', 'cream': 'cream', 'other': 'other',
}


class SheetError(Exception):
    """The file as a whole cannot be read."""


@dataclass
class Row:
    number: int                       # the line in the file, as Excel shows it
    values: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)

    @property
    def label(self):
        return self.values.get('name') or self.values.get('registered_name') or f'Row {self.number}'


def _heading_key(text: str) -> str:
    return re.sub(r'[^a-z]', '', (text or '').lower())


def decode(raw: bytes) -> str:
    if len(raw) > MAX_BYTES:
        raise SheetError('The file is too big. Use a file of at most 2 MB.')
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SheetError('The file cannot be read. Save it as CSV (UTF-8) in Excel, then try again.')


def parse_foaled(text: str, today: date | None = None):
    """(date, precision) from '12/04/2023', '2023-04-12', '2011', 'c. 2011'.

    Precision is 'exact', 'year' or 'estimated'. Raises ValueError.
    """
    today = today or timezone.localdate()
    text = (text or '').strip()
    estimated = bool(re.match(r'^(c\.?|ca\.?|circa|about|approx\.?)\s*', text, re.I)) or text.endswith('?')
    bare = re.sub(r'^(c\.?|ca\.?|circa|about|approx\.?)\s*', '', text, flags=re.I).rstrip('?').strip()
    if re.fullmatch(r'\d{4}', bare):
        year = int(bare)
        if not 1950 <= year <= today.year:
            raise ValueError(f'{text} is not a foaling year.')
        return date(year, 1, 1), 'estimated' if estimated else 'year'
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%Y-%m-%d', '%d %b %Y', '%d %B %Y', '%d/%m/%y'):
        try:
            found = datetime.strptime(bare, fmt).date()
        except ValueError:
            continue
        if found > today:
            raise ValueError(f'{text} is in the future.')
        return found, 'exact'
    raise ValueError(f'"{text}" is not a date. Use day/month/year, or only the year.')


def _tidy_row(number: int, raw: dict) -> Row:
    row = Row(number)
    values = row.values
    for key, text in raw.items():
        text = (text or '').strip()
        if not text:
            continue
        try:
            if key == 'name':
                values['name'] = text[:100]
            elif key == 'registered_name':
                name, suffix = split_suffix(text)
                values['registered_name'] = name[:200]
                if suffix and 'country' not in raw:
                    values.setdefault('country', normalise_country(suffix))
                elif suffix:
                    values.setdefault('suffix_from_name', normalise_country(suffix))
            elif key == 'country':
                values['country'] = normalise_country(text)
            elif key == 'foaled':
                values['foaled'] = parse_foaled(text)
            elif key == 'sex':
                sex = SEXES.get(_heading_key(text))
                if sex is None:
                    raise ValueError(f'"{text}" is not a sex. Use mare, gelding, stallion, colt or filly.')
                values['sex'] = sex
            elif key == 'colour':
                colour = COLOURS.get(_heading_key(text))
                if colour is None:
                    raise ValueError(f'"{text}" is not a colour on the list.')
                values['colour'] = colour
            elif key == 'microchip':
                chip = normalise_microchip(text)
                if len(chip) > 20:
                    raise ValueError(f'{text} is too long to be a microchip number.')
                values['microchip'] = chip
            elif key == 'ueln':
                code = normalise_ueln(text)
                validate_ueln(code)
                if len(code) > 15:
                    raise ValueError(f'{text}: a UELN has at most 15 characters.')
                values['ueln'] = code
            elif key == 'height':
                values['height'] = parse_height(text)
            elif key in ('S', 'D', 'DS'):
                values[key] = text[:220]
            elif key in ('breed', 'passport_issuer'):
                values[key] = text[:100]
            elif key == 'passport_number':
                values[key] = text[:50]
            elif key == 'markings':
                values[key] = text[:500]
        except Exception as error:  # a bad value: skip it, keep the row
            message = getattr(error, 'messages', None)
            row.problems.append(message[0] if message else str(error))
    if 'country' in values and 'suffix_from_name' in values:
        values.pop('suffix_from_name')
    if not values.get('name') and not values.get('registered_name'):
        row.problems.append('No yard name or registered name, so this row is left out.')
    return row


def read(raw: bytes) -> tuple[list[Row], list[str]]:
    """(rows, unknown column headings). Raises SheetError."""
    text = decode(raw)
    if not text.strip():
        raise SheetError('The file is empty.')
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        headings = next(reader)
    except StopIteration:
        raise SheetError('The file is empty.') from None
    keys = [_HEADINGS.get(_heading_key(h)) for h in headings]
    if 'name' not in keys and 'registered_name' not in keys:
        raise SheetError('The first line must have the column headings, with "Yard name" or '
                         '"Registered name". Use the template.')
    unknown = [h for h, k in zip(headings, keys) if h.strip() and k is None]
    rows = []
    for number, cells in enumerate(reader, start=2):
        if not any(cell.strip() for cell in cells):
            continue
        if len(rows) >= MAX_ROWS:
            raise SheetError(f'The file has more than {MAX_ROWS} rows. Split it into smaller files.')
        raw_values = {}
        for key, cell in zip(keys, cells):
            if key and cell.strip() and key not in raw_values:
                raw_values[key] = cell
        rows.append(_tidy_row(number, raw_values))
    return rows, unknown


def template_header() -> list[str]:
    return list(COLUMNS)
