"""Coordinate parsing and distance helpers.

Shared by the location edit form, the site settings form and the
``backfill_location_coords`` command, so a Google Maps link is read the
same way everywhere. Pure functions apart from ``resolve_short_link``,
which follows redirects over the network.
"""

from __future__ import annotations

import logging
import math
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)

EARTH_RADIUS_M = 6_371_000

# Anything that looks like a web address, in free text.
URL_RE = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)

# Hosts whose links are short redirects to the full Google Maps URL.
SHORT_LINK_HOSTS = ('maps.app.goo.gl', 'goo.gl', 'g.co')

# "51.5, -2.1" — a signed decimal pair separated by a comma and/or spaces.
_NUM = r'[-+]?\d+(?:\.\d+)?'
COORD_PAIR_RE = re.compile(rf'^\s*({_NUM})\s*(?:,\s*|\s+)({_NUM})\s*$')

# The three Google Maps URL forms, tried in order.
AT_RE = re.compile(rf'/@({_NUM}),({_NUM})')
DATA_RE = re.compile(rf'!3d({_NUM})!4d({_NUM})')
QUERY_KEYS = ('q', 'query', 'll', 'destination', 'center')

# One row of the coordinate picker's warning: the site centre check.
SITE_DISTANCE_WARN_M = 10_000


def _to_decimal(value) -> Decimal | None:
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not d.is_finite():
        return None
    return d.quantize(Decimal('0.000001'))


def parse_coords_text(text: str) -> tuple[Decimal, Decimal] | None:
    """``"52.1234, -1.2345"`` → ``(Decimal, Decimal)``; anything else → None.

    Accepts a comma, a space, or both as the separator. Does not range
    check — ``validate_coordinate_pair`` does that.
    """
    if not text:
        return None
    m = COORD_PAIR_RE.match(text)
    if not m:
        return None
    lat, lng = _to_decimal(m.group(1)), _to_decimal(m.group(2))
    if lat is None or lng is None:
        return None
    return lat, lng


def extract_url(text: str) -> str | None:
    """The first web address in a free-text field, or None."""
    if not text:
        return None
    m = URL_RE.search(text)
    if not m:
        return None
    # Trailing punctuation from the sentence around the link is not part of it.
    return m.group(0).rstrip('.,;:)]')


def is_short_link(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or '').lower()
    except ValueError:
        return False
    return host in SHORT_LINK_HOSTS


def resolve_short_link(url: str, timeout: float = 10) -> str:
    """Follow redirects (at most five) to the full URL a short link points at.

    Returns the input unchanged when the link is not a short link. Raises
    ``requests.RequestException`` when the network fails, so callers decide
    whether that is fatal (the form) or just "unresolved" (the backfill).
    """
    if not is_short_link(url):
        return url
    import requests

    session = requests.Session()
    session.max_redirects = 5
    response = session.get(url, allow_redirects=True, timeout=timeout, stream=True)
    try:
        return response.url
    finally:
        response.close()


def parse_maps_url(url: str) -> tuple[Decimal, Decimal] | None:
    """Read a latitude/longitude pair out of a full Google Maps URL.

    Handles, in order: ``/@<lat>,<lng>,<zoom>z`` in the path, ``?q=`` /
    ``&query=`` (and the other place-query keys) holding ``<lat>,<lng>``,
    and ``!3d<lat>!4d<lng>`` in the data segment. Returns None for a URL
    with a place name but no coordinates, and for malformed input.
    """
    if not url:
        return None
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    decoded = unquote(url)

    m = AT_RE.search(decoded)
    if m:
        return _pair(m.group(1), m.group(2))

    query = parse_qs(parts.query)
    for key in QUERY_KEYS:
        for value in query.get(key, []):
            pair = parse_coords_text(value.replace('+', ' '))
            if pair:
                return pair

    m = DATA_RE.search(decoded)
    if m:
        return _pair(m.group(1), m.group(2))
    return None


def _pair(lat, lng):
    lat, lng = _to_decimal(lat), _to_decimal(lng)
    if lat is None or lng is None:
        return None
    return lat, lng


def coords_from_link(text: str, *, resolve: bool = True, timeout: float = 10):
    """Find a maps link in ``text`` and read coordinates from it.

    Returns ``(coords, resolved_url)``. ``coords`` is None when no link is
    found or the link carries no coordinates. With ``resolve`` off, short
    links are not followed (offline tests).
    """
    url = extract_url(text)
    if not url:
        return None, None
    if resolve:
        url = resolve_short_link(url, timeout=timeout)
    return parse_maps_url(url), url


def haversine_m(lat1, lng1, lat2, lng2) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = phi2 - phi1
    dlambda = math.radians(float(lng2) - float(lng1))
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def site_distance_warning(location, site_settings) -> str | None:
    """A message when a location sits far from its site centre.

    A swapped latitude and longitude is the most common entry error and
    always fails this check. Warn, never block: some sites are large.
    """
    if location is None or site_settings is None:
        return None
    if not (location.has_coordinates and site_settings.has_coordinates):
        return None
    metres = haversine_m(
        location.latitude, location.longitude,
        site_settings.latitude, site_settings.longitude,
    )
    if metres <= SITE_DISTANCE_WARN_M:
        return None
    km = metres / 1000
    return (
        f"{location.name} is {km:.1f} km from the {site_settings.site} site "
        "centre. Check the latitude and longitude are the right way round."
    )


def format_coords(lat, lng) -> str:
    return f"{Decimal(lat).normalize():f}, {Decimal(lng).normalize():f}"
