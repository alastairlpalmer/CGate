"""Typo-tolerant matching for list searches.

The dataset is small (a few hundred horses), so fuzzy matching is done in
Python with difflib rather than a database extension like pg_trgm — it
behaves identically on the SQLite used by tests/dev and the Postgres used
in production, and needs no migrations.
"""

from difflib import SequenceMatcher

# Minimum similarity (0..1) before a candidate counts as a match. 0.75
# tolerates one or two typos in a typical horse/owner name without pulling
# in unrelated records.
FUZZY_THRESHOLD = 0.75

# Queries shorter than this are exact-substring only — one or two letters
# carry too little signal for similarity scoring.
MIN_FUZZY_QUERY_LENGTH = 3


def is_fuzzy_match(query, text, threshold=FUZZY_THRESHOLD):
    """True if ``query`` roughly matches ``text`` (case-insensitive).

    Matches exact substrings, whole-string similarity, per-word similarity
    (so "mitchel" finds owner "Sarah Mitchell"), and similarity against a
    same-length prefix (so a typo while part-way through typing a long
    name still matches).
    """
    query = (query or '').strip().lower()
    text = (text or '').lower()
    if not query or not text:
        return False
    if query in text:
        return True
    if len(query) < MIN_FUZZY_QUERY_LENGTH:
        return False

    candidates = [text] + text.split()
    for candidate in candidates:
        if SequenceMatcher(None, query, candidate).ratio() >= threshold:
            return True

    # Partial typing with a typo: "alihnter" while aiming for "alihunter…"
    if len(query) >= 4:
        for candidate in candidates:
            prefix = candidate[: len(query) + 1]
            if SequenceMatcher(None, query, prefix).ratio() >= threshold:
                return True

    return False


def fuzzy_horse_ids(query):
    """IDs of horses whose name, owner or location roughly matches ``query``.

    Mirrors the fields the horse list's exact search covers (except free-text
    notes, where fuzziness would produce noise).
    """
    from core.models import Horse, Placement

    if len((query or '').strip()) < MIN_FUZZY_QUERY_LENGTH:
        return set()

    ids = {
        pk for pk, name in Horse.objects.values_list('pk', 'name')
        if is_fuzzy_match(query, name)
    }
    placements = Placement.objects.values_list(
        'horse_id', 'owner__name', 'location__name'
    )
    for horse_id, owner_name, location_name in placements:
        if horse_id in ids:
            continue
        if is_fuzzy_match(query, owner_name) or is_fuzzy_match(query, location_name):
            ids.add(horse_id)
    return ids
