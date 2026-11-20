"""Back-fill from what the yard already has (plan §7.7, phase 1d).

Pure text rules. Each returns what it found, or None; nothing here
saves. pedigree.imports turns the results into proposed changes for a
person to review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .names import is_unknown

# "By X out of Y", "X x Y", "X ex Y", "X / Y", "X (IRE) x Y (GB)".
_BY_OUT_OF = re.compile(r'^\s*by\s+(?P<sire>.+?)\s+(?:out\s+of|o/o|ex)\s+(?P<dam>.+?)\s*$', re.I)
_SEPARATED = re.compile(r'^\s*(?P<sire>.+?)\s+(?:x|ex|/)\s+(?P<dam>.+?)\s*$', re.I)
_SLASH = re.compile(r'^\s*(?P<sire>[^/]+?)\s*/\s*(?P<dam>[^/]+?)\s*$')

# A name that is too long to be a horse name is a sentence, not a name.
MAX_WORDS = 6


@dataclass(frozen=True)
class Parents:
    sire: str
    dam: str


def _clean(name: str) -> str:
    name = name.strip().strip('.,;:').strip()
    name = re.sub(r'\s+', ' ', name)
    return name


def _plausible(name: str) -> bool:
    if not name or is_unknown(name):
        return bool(name)
    words = name.split()
    if len(words) > MAX_WORDS:
        return False
    return any(ch.isalpha() for ch in name)


def parse_breeding(text: str | None) -> Parents | None:
    """Sire and dam from free breeding text, or None.

    Only the first line counts. A line that is a sentence ("Bought at
    the sales from a breeder in Kildare") gives None, not a guess.
    """
    if not text:
        return None
    line = text.strip().splitlines()[0] if text.strip() else ''
    for pattern in (_BY_OUT_OF, _SEPARATED, _SLASH):
        match = pattern.match(line)
        if match:
            sire, dam = _clean(match['sire']), _clean(match['dam'])
            if re.match(r'^by\s', sire, re.I):
                sire = _clean(sire[3:])
            if _plausible(sire) and _plausible(dam):
                return Parents(sire, dam)
    return None


# "Avicii (Karma Avicii)": a yard name with the registered name after it.
# A country suffix such as "(GB)" is not a registered name.
_REGISTERED_IN_BRACKETS = re.compile(r'^(?P<yard>[^()]+?)\s*\((?P<registered>[^()]{4,})\)\s*$')


def registered_from_name(name: str | None) -> tuple[str, str] | None:
    """(yard name, registered name) from "Avicii (Karma Avicii)", or None."""
    if not name:
        return None
    match = _REGISTERED_IN_BRACKETS.match(name.strip())
    if not match:
        return None
    registered = match['registered'].strip()
    if registered.isupper() and len(registered) <= 3:
        return None
    if not any(ch.isalpha() for ch in registered):
        return None
    return match['yard'].strip(), registered


MARKING_WORDS = ('sock', 'socks', 'star', 'blaze', 'snip', 'stripe', 'stocking', 'stockings',
                 'white', 'bald', 'ermine', 'coronet', 'heel', 'heels')


def markings_from_name(name: str | None) -> tuple[str, str] | None:
    """(yard name, markings) from "Beech - white sock", or None."""
    if not name or ' - ' not in name:
        return None
    yard, _sep, rest = name.partition(' - ')
    words = set(re.findall(r'[a-z]+', rest.lower()))
    if not words & set(MARKING_WORDS):
        return None
    return yard.strip(), rest.strip()
