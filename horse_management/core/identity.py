"""Rules for a horse's identity: numbers, height and age.

docs/HORSE_IDENTITY_AND_PEDIGREE_PLAN.md, phase 1a (§6.1, §6.6).

Plain functions with no database access, so the model, the forms, the
views and the migrations can all use the same rules.
"""

from __future__ import annotations

import re
from datetime import date

from django.core.exceptions import ValidationError

# ---------------------------------------------------------------------------
# Microchip
# ---------------------------------------------------------------------------

# Words a chip scanner can put before the number (the chip standard).
_SCANNER_PREFIX = re.compile(r'\b(?:FDX-?[AB]|HDX|ISO(?:\s*11784)?)\b', re.I)
# Spaces, dashes, dots, stars and colons are not part of a chip number.
_CHIP_NOISE = re.compile(r'[\s\-.*:]')

# The first three digits of a 15-digit chip are a country code (ISO 3166
# numeric) or a maker code (900 to 998). These are the countries a yard
# in Britain or Ireland meets most often.
CHIP_COUNTRIES = {
    '826': 'UK',
    '372': 'Irish',
    '250': 'French',
    '276': 'German',
    '528': 'Dutch',
    '056': 'Belgian',
    '724': 'Spanish',
    '380': 'Italian',
    '756': 'Swiss',
    '040': 'Austrian',
    '208': 'Danish',
    '752': 'Swedish',
    '578': 'Norwegian',
    '840': 'US',
    '124': 'Canadian',
    '032': 'Argentine',
    '076': 'Brazilian',
    '152': 'Chilean',
    '858': 'Uruguayan',
    '036': 'Australian',
    '554': 'New Zealand',
    '710': 'South African',
    '392': 'Japanese',
}

# Where a person checks a chip by hand. Links only: these sites are never
# read by the app.
CHIP_CHECKER_UK = 'https://www.equineregister.co.uk/'
CHIP_CHECKER_IRELAND = 'https://equinechipcheck.agriculture.gov.ie/'


def normalise_microchip(raw: str | None) -> str:
    """The chip number as stored: no spaces, dashes or scanner words.

    Letters are kept (in capitals): some old chips are not digits only.
    """
    if not raw:
        return ''
    text = _SCANNER_PREFIX.sub(' ', str(raw))
    return _CHIP_NOISE.sub('', text).upper()


def is_standard_microchip(chip: str) -> bool:
    return len(chip) == 15 and chip.isdigit()


def microchip_warning(chip: str) -> str:
    """A warning for a chip that is not the usual 15 digits, else ''."""
    if not chip or is_standard_microchip(chip):
        return ''
    return (f'The microchip {chip} is not the usual 15 digits. '
            'Check the number. Old chips can have 9 or 10 characters.')


def microchip_hint(chip: str) -> str:
    """'Irish chip', 'UK chip', 'Maker code chip' or ''."""
    if not is_standard_microchip(chip):
        return ''
    prefix = chip[:3]
    if '900' <= prefix <= '998':
        return 'Maker code chip'
    country = CHIP_COUNTRIES.get(prefix)
    return f'{country} chip' if country else ''


def chip_checker_url(chip: str) -> str:
    """The national checker that fits the chip: Irish for 372, else UK."""
    return CHIP_CHECKER_IRELAND if chip.startswith('372') else CHIP_CHECKER_UK


def group_digits(number: str, size: int = 3) -> str:
    """'985141000123456' -> '985 141 000 123 456', for reading aloud."""
    if not number:
        return ''
    return ' '.join(number[i:i + size] for i in range(0, len(number), size))


# ---------------------------------------------------------------------------
# UELN
# ---------------------------------------------------------------------------

UELN_LENGTH = 15


def normalise_ueln(raw: str | None) -> str:
    """Capitals, with no spaces or dashes."""
    if not raw:
        return ''
    return re.sub(r'[\s\-.]', '', str(raw)).upper()


def validate_ueln(ueln: str) -> None:
    """Refuse characters that cannot be in a UELN. The length only warns."""
    if ueln and not ueln.isalnum():
        raise ValidationError('A UELN has letters and digits only.')


def ueln_warning(ueln: str) -> str:
    if not ueln or len(ueln) == UELN_LENGTH:
        return ''
    return (f'The UELN {ueln} has {len(ueln)} characters, not the usual '
            f'{UELN_LENGTH}. Check it against the passport.')


def format_ueln(ueln: str) -> str:
    """'826002000123456' -> '826002 000123456': database code, then horse."""
    if len(ueln) == UELN_LENGTH:
        return f'{ueln[:6]} {ueln[6:]}'
    return ueln


# ---------------------------------------------------------------------------
# Country suffix
# ---------------------------------------------------------------------------

# Stud-book suffixes for countries in the southern hemisphere. A horse
# foaled there is a year older on 1 August, not 1 January.
SOUTHERN_HEMISPHERE = frozenset({
    'ARG', 'AUS', 'BRZ', 'CHI', 'NZ', 'PER', 'RSA', 'SAF', 'URU', 'ZIM',
})

# Suggestions for the form. Any 2 or 3 letters are accepted.
COUNTRY_SUGGESTIONS = (
    'GB', 'IRE', 'FR', 'GER', 'ITY', 'USA', 'CAN', 'JPN',
    'ARG', 'AUS', 'NZ', 'BRZ', 'CHI', 'SAF',
)

_SUFFIX_IN_NAME = re.compile(r'^(?P<name>.*\S)\s*\((?P<country>[A-Za-z]{2,3})\)\s*$')


def normalise_country(raw: str | None) -> str:
    """'(ire)' -> 'IRE'. Refuses anything but 2 or 3 letters."""
    if not raw:
        return ''
    code = str(raw).strip().strip('()').strip().upper()
    if not code:
        return ''
    if not (code.isalpha() and 2 <= len(code) <= 3):
        raise ValidationError('Use the 2 or 3 letter stud-book suffix, for example GB, IRE or FR.')
    return code


def split_suffix(name: str) -> tuple[str, str]:
    """'Karma Avicii (GB)' -> ('Karma Avicii', 'GB'). No suffix: (name, '')."""
    match = _SUFFIX_IN_NAME.match(name or '')
    if not match:
        return (name or '').strip(), ''
    return match.group('name').strip(), match.group('country').upper()


def is_southern(country: str | None) -> bool:
    return (country or '').upper() in SOUTHERN_HEMISPHERE


# ---------------------------------------------------------------------------
# Height (hands)
# ---------------------------------------------------------------------------

_HEIGHT = re.compile(r'^\s*(?P<hands>\d{1,2})(?:\.(?P<inches>\d))?\s*(?:hh|h|hands)?\s*$', re.I)


def parse_height(text: str | None) -> int | None:
    """'16.2', '16.2hh', '16 hh' -> inches (16.2 hh is 66 inches).

    The digit after the point is inches, 0 to 3: 16.2 means 16 hands and
    2 inches, not 16.2 hands.
    """
    if text is None or not str(text).strip():
        return None
    match = _HEIGHT.match(str(text))
    if not match:
        raise ValidationError('Type the height in hands, for example 16.2 or 15.3hh.')
    hands = int(match.group('hands'))
    inches = int(match.group('inches') or 0)
    if inches > 3:
        raise ValidationError(
            'The digit after the point is inches, 0 to 3. 16.2 means 16 hands and 2 inches.'
        )
    if not 6 <= hands <= 20:
        raise ValidationError('Type a height between 6 and 20 hands.')
    return hands * 4 + inches


def format_height(inches: int | None) -> str:
    """66 -> '16.2 hh'."""
    if inches is None:
        return ''
    return f'{inches // 4}.{inches % 4} hh'


# ---------------------------------------------------------------------------
# Age: one rule for all horses (plan decision 4)
# ---------------------------------------------------------------------------

def season_year(day: date, *, southern: bool) -> int:
    """The year a horse's age counts from.

    Northern hemisphere: the calendar year (everyone is a year older on
    1 January). Southern hemisphere: the year that started on the last
    1 August.
    """
    if southern and day.month < 8:
        return day.year - 1
    return day.year


def foaling_season(dob: date, *, exact: bool, southern: bool) -> int:
    """The season a horse was foaled in.

    A year-only or estimated date is stored as 1 January of that year and
    means "foaled in that season", so it is never moved back a year.
    """
    if not exact:
        return dob.year
    return season_year(dob, southern=southern)


def age_on(today: date, dob: date | None, *, exact: bool = True,
           southern: bool = False) -> int | None:
    """Age in whole years on ``today``, by the 1 January (1 August) rule.

    None without a date, or for a date in the future.
    """
    if dob is None or dob > today:
        return None
    return season_year(today, southern=southern) - foaling_season(
        dob, exact=exact, southern=southern)


def estimated_dob_from_age(age: int, made_on: date, *, southern: bool = False) -> date:
    """Turn an age typed on ``made_on`` into an estimated foaling date.

    The date is 1 January of the foaling season, so ``age_on(made_on)``
    gives the same age back, and the age goes up each year after.
    """
    return date(season_year(made_on, southern=southern) - age, 1, 1)
