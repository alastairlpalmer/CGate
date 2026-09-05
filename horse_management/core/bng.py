"""British National Grid (EPSG:27700) to WGS84 (EPSG:4326).

Land App and the Rural Payments Agency export field parcels as GeoJSON
with ``crs: EPSG::27700`` — eastings and northings in metres, not the
longitude/latitude the GeoJSON standard requires. This converts them so a
file can be imported without a reprojection library.

Two steps, both from the Ordnance Survey's "A guide to coordinate systems
in Great Britain": the inverse Transverse Mercator projection onto the
Airy 1830 ellipsoid (OSGB36 latitude/longitude), then a seven-parameter
Helmert transformation to WGS84. The Helmert step is what every library
without the OSTN15 grid does, and it is accurate to a few metres — the
same order as a phone's GPS, and far below a field's size. Checked
against pyproj to 0.1 m.
"""

from __future__ import annotations

import math

# Airy 1830 ellipsoid and the National Grid projection.
_A, _B = 6377563.396, 6356256.909
_F0 = 0.9996012717
_LAT0, _LON0 = math.radians(49.0), math.radians(-2.0)
_N0, _E0 = -100000.0, 400000.0

# WGS84 ellipsoid.
_A2, _B2 = 6378137.0, 6356752.3142

# OSGB36 → WGS84 Helmert parameters (Ordnance Survey).
_TX, _TY, _TZ = 446.448, -125.157, 542.060
_RX, _RY, _RZ = (math.radians(s / 3600) for s in (0.1502, 0.2470, 0.8421))
_S = -20.4894e-6

# Eastings and northings of the National Grid proper (a 700 × 1300 km box).
BNG_EASTING_RANGE = (0.0, 700000.0)
BNG_NORTHING_RANGE = (0.0, 1300000.0)


def looks_like_bng(x: float, y: float) -> bool:
    """True when a coordinate pair sits inside the National Grid's extent."""
    return (
        BNG_EASTING_RANGE[0] <= x <= BNG_EASTING_RANGE[1]
        and BNG_NORTHING_RANGE[0] <= y <= BNG_NORTHING_RANGE[1]
    )


def _osgb36_lat_lon(easting: float, northing: float) -> tuple[float, float]:
    """Inverse Transverse Mercator: grid metres → OSGB36 radians."""
    a, b, f0 = _A, _B, _F0
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)

    lat = _LAT0
    m = 0.0
    while True:
        lat = (northing - _N0 - m) / (a * f0) + lat
        ma = (1 + n + 5 / 4 * n ** 2 + 5 / 4 * n ** 3) * (lat - _LAT0)
        mb = (3 * n + 3 * n ** 2 + 21 / 8 * n ** 3) * math.sin(lat - _LAT0) * math.cos(lat + _LAT0)
        mc = (15 / 8 * n ** 2 + 15 / 8 * n ** 3) * math.sin(2 * (lat - _LAT0)) * math.cos(2 * (lat + _LAT0))
        md = 35 / 24 * n ** 3 * math.sin(3 * (lat - _LAT0)) * math.cos(3 * (lat + _LAT0))
        m = b * f0 * (ma - mb + mc - md)
        if northing - _N0 - m < 0.00001:
            break

    sin_lat, cos_lat, tan_lat = math.sin(lat), math.cos(lat), math.tan(lat)
    nu = a * f0 / math.sqrt(1 - e2 * sin_lat * sin_lat)
    rho = a * f0 * (1 - e2) * (1 - e2 * sin_lat * sin_lat) ** -1.5
    eta2 = nu / rho - 1

    vii = tan_lat / (2 * rho * nu)
    viii = tan_lat / (24 * rho * nu ** 3) * (5 + 3 * tan_lat ** 2 + eta2 - 9 * tan_lat ** 2 * eta2)
    ix = tan_lat / (720 * rho * nu ** 5) * (61 + 90 * tan_lat ** 2 + 45 * tan_lat ** 4)
    x = 1 / (cos_lat * nu)
    xi = 1 / (cos_lat * 6 * nu ** 3) * (nu / rho + 2 * tan_lat ** 2)
    xii = 1 / (cos_lat * 120 * nu ** 5) * (5 + 28 * tan_lat ** 2 + 24 * tan_lat ** 4)
    xiia = 1 / (cos_lat * 5040 * nu ** 7) * (61 + 662 * tan_lat ** 2 + 1320 * tan_lat ** 4 + 720 * tan_lat ** 6)

    de = easting - _E0
    lat = lat - vii * de ** 2 + viii * de ** 4 - ix * de ** 6
    lon = _LON0 + x * de - xi * de ** 3 + xii * de ** 5 - xiia * de ** 7
    return lat, lon


def _helmert_to_wgs84(lat: float, lon: float) -> tuple[float, float]:
    """OSGB36 radians → WGS84 radians via geocentric coordinates."""
    e2 = 1 - (_B * _B) / (_A * _A)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    nu = _A / math.sqrt(1 - e2 * sin_lat * sin_lat)
    x = nu * cos_lat * math.cos(lon)
    y = nu * cos_lat * math.sin(lon)
    z = (1 - e2) * nu * sin_lat

    x2 = _TX + (1 + _S) * x - _RZ * y + _RY * z
    y2 = _TY + _RZ * x + (1 + _S) * y - _RX * z
    z2 = _TZ - _RY * x + _RX * y + (1 + _S) * z

    e2b = 1 - (_B2 * _B2) / (_A2 * _A2)
    p = math.hypot(x2, y2)
    phi = math.atan2(z2, p * (1 - e2b))
    for _ in range(10):
        nu2 = _A2 / math.sqrt(1 - e2b * math.sin(phi) ** 2)
        phi = math.atan2(z2 + e2b * nu2 * math.sin(phi), p)
    return phi, math.atan2(y2, x2)


def bng_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """Grid ``(easting, northing)`` in metres → ``(latitude, longitude)`` in degrees."""
    lat, lon = _osgb36_lat_lon(float(easting), float(northing))
    phi, lam = _helmert_to_wgs84(lat, lon)
    return math.degrees(phi), math.degrees(lam)


def bng_position_to_lnglat(position) -> list[float]:
    """A GeoJSON position ``[E, N]`` → ``[lng, lat]`` (GeoJSON axis order)."""
    lat, lng = bng_to_wgs84(position[0], position[1])
    return [round(lng, 7), round(lat, 7)]
