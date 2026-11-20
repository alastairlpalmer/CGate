"""How a horse in a pedigree is written and matched.

"Copper Ridge (GB) 2004" is the name, the stud-book suffix and the year.
The suffix and the year are optional. ``name_key`` is the name reduced to
letters and digits in lower case, for matching only: "Sea The Stars",
"SEA THE STARS" and "Sea-the-Stars" share one key.

docs/HORSE_IDENTITY_AND_PEDIGREE_PLAN.md §6.3, §6.4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_LABEL = re.compile(
    r'^\s*(?P<name>.*?\S)\s*'
    r'(?:\((?P<country>[A-Za-z]{2,3})\))?\s*'
    r'(?P<year>(?:1[89]|20)\d\d)?\s*$'
)
_KEY_DROP = re.compile(r'[^0-9a-z]')

UNKNOWN_WORDS = {'unknown', 'not known', 'unk', '?'}


@dataclass(frozen=True)
class ParsedName:
    name: str
    country: str = ''
    year: int | None = None

    @property
    def key(self) -> str:
        return name_key(self.name)


def name_key(name: str) -> str:
    """Lower case, letters and digits only."""
    return _KEY_DROP.sub('', (name or '').lower())


def is_unknown(text: str | None) -> bool:
    return (text or '').strip().lower() in UNKNOWN_WORDS


def parse(text: str | None) -> ParsedName | None:
    """'Copper Ridge (GB) 2004' -> ParsedName('Copper Ridge', 'GB', 2004).

    None for an empty box. The year is read only when it is the last word
    and looks like a year, so "Mill Reef 1971" works and "Number 9" stays
    a name.
    """
    text = (text or '').strip()
    if not text:
        return None
    match = _LABEL.match(text)
    if not match:
        return ParsedName(text)
    name = match.group('name').strip()
    country = (match.group('country') or '').upper()
    year = int(match.group('year')) if match.group('year') else None
    if not name_key(name):
        # Only a suffix or a year was typed: keep the text as the name.
        return ParsedName(text)
    return ParsedName(name, country, year)


def label(name: str, country: str = '', year: int | None = None) -> str:
    """The text a record is shown and suggested as: 'Copper Ridge (GB) 2004'."""
    out = name
    if country:
        out += f' ({country})'
    if year:
        out += f' {year}'
    return out
