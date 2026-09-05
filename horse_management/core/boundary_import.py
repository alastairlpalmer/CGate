"""Reading field boundaries out of a Land App (or RPA) GeoJSON export.

Everything here is Python, using shapely, and runs on the server. The
browser is never trusted to validate an uploaded file.

Validation, in order, failing fast with a specific message:

1. valid JSON;
2. a GeoJSON ``FeatureCollection`` or ``Feature``;
3. coordinates in WGS84 range — a file whose ``crs`` names EPSG:27700, or
   whose numbers sit in the National Grid's extent, is converted with
   ``core.bng`` (Land App's RPA parcel export is in that grid; the plan's
   first draft rejected it, but the real files are BNG so they convert);
   any other projected system is rejected;
4. only ``Polygon`` and ``MultiPolygon`` kept, other types counted and dropped;
5. winding order normalised (right-hand rule);
6. self-intersection reported with shapely's explanation, the shape
   flagged so the matching screen only lets it be skipped;
7. polygons over ``MAX_VERTICES`` vertices simplified at about a metre;
8. an empty result explained.

Area: shapely works in the units it is given, so ``.area`` on degrees is
meaningless. ``area_m2`` uses the equal-area approximation — longitude
scaled by cos(mean latitude), both axes by metres per degree — which is
within 1 % at UK latitudes on a single field. No pyproj.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from shapely.geometry import MultiPolygon, Point, Polygon, mapping, shape
from shapely.geometry.polygon import orient
from shapely.validation import explain_validity

from .bng import bng_position_to_lnglat, looks_like_bng

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_VERTICES = 1000
SIMPLIFY_TOLERANCE_DEG = 0.00001   # about one metre
METRES_PER_DEGREE = 111_320.0
M2_PER_HECTARE = 10_000.0
M2_PER_ACRE = 4046.8564224

# Where a shape's name may live, in order. Land App templates differ.
NAME_KEYS = ('name', 'Name', 'NAME', 'title', 'label', 'field_name', 'Field Name', 'FIELD_NAME')
# What to show under the name: land cover, notes.
SUBTITLE_KEYS = ('description', 'Description', 'land_cover', 'landcover', 'notes')
# The RPA parcel export names a parcel by its sheet and parcel number.
RPA_SHEET_KEY, RPA_PARCEL_KEY = 'sheetId', 'parcelId'

# Name matching: words that carry no identity.
STOP_WORDS = {'the', 'field', 'fields', 'paddock', 'pen', 'barn', 'yard', 'and', 'of', 'a'}
NAME_MATCH_THRESHOLD = 0.6


class BoundaryImportError(Exception):
    """A file that cannot be imported, with a message for the operator."""


@dataclass
class ImportedShape:
    index: int
    name: str
    subtitle: str
    geometry: dict            # GeoJSON geometry, WGS84, oriented
    area_m2: float
    anchor: tuple[float, float]   # (lat, lng), inside the shape
    vertex_count: int
    simplified: bool = False
    invalid: str | None = None    # shapely's explanation when self-intersecting

    @property
    def hectares(self) -> float:
        return self.area_m2 / M2_PER_HECTARE

    @property
    def acres(self) -> float:
        return self.area_m2 / M2_PER_ACRE

    @property
    def importable(self) -> bool:
        return self.invalid is None

    def as_dict(self) -> dict:
        """JSON-safe, for the session between upload and commit."""
        return {
            'index': self.index, 'name': self.name, 'subtitle': self.subtitle,
            'geometry': self.geometry, 'area_m2': self.area_m2,
            'anchor': list(self.anchor), 'vertex_count': self.vertex_count,
            'simplified': self.simplified, 'invalid': self.invalid,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ImportedShape':
        return cls(
            index=data['index'], name=data['name'], subtitle=data.get('subtitle', ''),
            geometry=data['geometry'], area_m2=data['area_m2'],
            anchor=tuple(data['anchor']), vertex_count=data['vertex_count'],
            simplified=data.get('simplified', False), invalid=data.get('invalid'),
        )


@dataclass
class ImportReport:
    shapes: list[ImportedShape] = field(default_factory=list)
    discarded: int = 0                # points, lines, other geometry types
    converted_from_bng: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def importable(self) -> list[ImportedShape]:
        return [s for s in self.shapes if s.importable]

    @property
    def invalid(self) -> list[ImportedShape]:
        return [s for s in self.shapes if not s.importable]


# ── Parsing ────────────────────────────────────────────────────────────────

def parse_geojson(data) -> ImportReport:
    """Validate and normalise an export. ``data`` is bytes or str.

    Raises ``BoundaryImportError`` with a message the operator can act on.
    """
    if isinstance(data, bytes):
        try:
            data = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise BoundaryImportError(
                "That file isn't text. Export the plan as GeoJSON from Land App "
                "(Export → GeoJSON) and upload the .geojson file."
            )
    try:
        doc = json.loads(data)
    except (ValueError, TypeError):
        raise BoundaryImportError(
            "That file isn't valid JSON. Export the plan again as GeoJSON from "
            "Land App and upload the .geojson file it gives you."
        )
    if not isinstance(doc, dict):
        raise BoundaryImportError('That file is JSON, but not GeoJSON: expected a FeatureCollection.')

    kind = doc.get('type')
    if kind == 'FeatureCollection':
        features = doc.get('features')
        if not isinstance(features, list):
            raise BoundaryImportError('The FeatureCollection has no "features" list.')
    elif kind == 'Feature':
        features = [doc]
    else:
        raise BoundaryImportError(
            f'Expected a GeoJSON FeatureCollection or Feature, not "{kind or "nothing"}". '
            'Land App\'s GeoJSON export produces a FeatureCollection.'
        )

    report = ImportReport()
    geometries = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get('geometry')
        if not isinstance(geometry, dict) or geometry.get('type') not in ('Polygon', 'MultiPolygon'):
            report.discarded += 1
            continue
        coords = geometry.get('coordinates')
        if not isinstance(coords, list):
            report.discarded += 1
            continue
        geometries.append((feature, geometry))

    if not geometries and not features:
        raise BoundaryImportError(
            'The file has no features at all. In Land App, check the plan has '
            'drawn field boundaries, then export it again.'
        )

    # Coordinate system. GeoJSON must be WGS84; the RPA/Land App parcel
    # export declares EPSG:27700 (British National Grid) and is converted.
    declared_bng = _declares_bng(doc)
    positions = [p for _, g in geometries for p in _positions(g['coordinates'])]
    if positions:
        in_wgs84 = all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in positions)
        if declared_bng or not in_wgs84:
            if all(looks_like_bng(x, y) for x, y in positions):
                for _, g in geometries:
                    g['coordinates'] = _convert(g['coordinates'])
                report.converted_from_bng = True
                report.notes.append(
                    'The file uses British National Grid coordinates (EPSG:27700); '
                    'they were converted to latitude and longitude.'
                )
            else:
                raise BoundaryImportError(
                    'The file appears to use a projected coordinate system (its numbers '
                    'are not latitudes and longitudes, and not British National Grid). '
                    'Export from Land App as GeoJSON in WGS84.'
                )

    for feature, geometry in geometries:
        shp = _build_shape(len(report.shapes) + 1, feature, geometry, report)
        if shp is not None:
            report.shapes.append(shp)

    _disambiguate_names(report.shapes)

    if report.discarded:
        report.notes.append(
            f'{report.discarded} feature{"s" if report.discarded != 1 else ""} '
            f'{"were" if report.discarded != 1 else "was"} not a field boundary '
            '(points, lines or unknown shapes) and were left out.'
        )
    if not report.shapes:
        if report.discarded:
            raise BoundaryImportError(
                'The file has no field boundaries — only points, lines or other '
                'markers. In Land App, export the plan that holds the field polygons.'
            )
        raise BoundaryImportError(
            'The file has no field boundaries. In Land App, draw or import the '
            'fields first, then export the plan as GeoJSON.'
        )
    return report


def _disambiguate_names(shapes):
    """Two shapes with one name (an RPA parcel split by land cover, say)
    get their subtitle, or a number, appended so they can be told apart."""
    counts = {}
    for shp in shapes:
        counts[shp.name] = counts.get(shp.name, 0) + 1
    seen = {}
    for shp in shapes:
        if counts[shp.name] < 2:
            continue
        seen[shp.name] = seen.get(shp.name, 0) + 1
        suffix = shp.subtitle or str(seen[shp.name])
        shp.name = f'{shp.name} ({suffix})'


def _declares_bng(doc: dict) -> bool:
    crs = doc.get('crs')
    if not isinstance(crs, dict):
        return False
    name = str((crs.get('properties') or {}).get('name', ''))
    return '27700' in name


def _positions(coords):
    """Every ``[x, y]`` pair inside a nested coordinate list."""
    if not isinstance(coords, list) or not coords:
        return
    if isinstance(coords[0], (int, float)):
        if len(coords) >= 2 and all(isinstance(v, (int, float)) for v in coords[:2]):
            yield float(coords[0]), float(coords[1])
        return
    for item in coords:
        yield from _positions(item)


def _convert(coords):
    if isinstance(coords[0], (int, float)):
        return bng_position_to_lnglat(coords)
    return [_convert(item) for item in coords]


def _build_shape(index, feature, geometry, report) -> ImportedShape | None:
    try:
        geom = shape(geometry)
    except Exception:
        report.discarded += 1
        return None
    if geom.is_empty:
        report.discarded += 1
        return None

    invalid = None
    if not geom.is_valid:
        invalid = explain_validity(geom)

    # Right-hand rule: exterior rings anticlockwise, holes clockwise.
    if isinstance(geom, Polygon):
        geom = orient(geom, sign=1.0)
    elif isinstance(geom, MultiPolygon):
        geom = MultiPolygon([orient(p, sign=1.0) for p in geom.geoms])

    vertex_count = _vertex_count(geom)
    simplified = False
    if vertex_count > MAX_VERTICES:
        simpler = geom.simplify(SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
        if not simpler.is_empty:
            geom = simpler
            simplified = True
            vertex_count = _vertex_count(geom)

    properties = feature.get('properties') or {}
    if not isinstance(properties, dict):
        properties = {}
    name = shape_name(properties, index)
    subtitle = _first_text(properties, SUBTITLE_KEYS)

    if invalid is None:
        anchor_point = geom.representative_point()
    else:
        # representative_point() needs valid geometry; the centroid of the
        # bounding box is enough to draw a warning badge.
        minx, miny, maxx, maxy = geom.bounds
        anchor_point = Point((minx + maxx) / 2, (miny + maxy) / 2)

    return ImportedShape(
        index=index,
        name=name,
        subtitle=subtitle,
        geometry=_rounded(mapping(geom)),
        area_m2=approx_area_m2(geom),
        anchor=(round(anchor_point.y, 6), round(anchor_point.x, 6)),
        vertex_count=vertex_count,
        simplified=simplified,
        invalid=invalid,
    )


def _vertex_count(geom) -> int:
    polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    return sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors) for p in polys)


def _rounded(geometry: dict) -> dict:
    """GeoJSON with coordinates rounded to 7 decimals (about a centimetre)."""
    def rnd(coords):
        if isinstance(coords[0], (int, float)):
            return [round(float(coords[0]), 7), round(float(coords[1]), 7)]
        return [rnd(c) for c in coords]
    return {'type': geometry['type'], 'coordinates': rnd(list(geometry['coordinates']))}


def _first_text(properties: dict, keys) -> str:
    for key in keys:
        value = properties.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return ''


def shape_name(properties: dict, index: int) -> str:
    """A name for a shape from whatever the export wrote, else ``Shape n``."""
    name = _first_text(properties, NAME_KEYS)
    if name:
        return name
    sheet = _first_text(properties, (RPA_SHEET_KEY,))
    parcel = _first_text(properties, (RPA_PARCEL_KEY,))
    if sheet and parcel:
        return f'{sheet} {parcel}'
    return f'Shape {index}'


def approx_area_m2(geom) -> float:
    """Equal-area approximation on a WGS84 shape; see the module docstring."""
    polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    total = 0.0
    for poly in polys:
        if poly.is_empty:
            continue
        k = math.cos(math.radians(poly.centroid.y))
        projected = Polygon(
            _project_ring(poly.exterior, k),
            [_project_ring(r, k) for r in poly.interiors],
        )
        total += abs(projected.area)
    return round(total, 1)


def _project_ring(ring, k):
    return [(x * k * METRES_PER_DEGREE, y * METRES_PER_DEGREE) for x, y in ring.coords]


def anchor_for(geometry: dict) -> tuple[float, float] | None:
    """``(lat, lng)`` guaranteed inside a stored GeoJSON geometry."""
    try:
        geom = shape(geometry)
    except Exception:
        return None
    if geom.is_empty:
        return None
    point = geom.representative_point() if geom.is_valid else geom.centroid
    return round(point.y, 6), round(point.x, 6)


# ── Matching ───────────────────────────────────────────────────────────────

def normalise_name(name: str) -> list[str]:
    """Lowercase tokens with punctuation and filler words removed."""
    tokens = re.sub(r"[^a-z0-9\s]", ' ', (name or '').lower()).split()
    return [t for t in tokens if t not in STOP_WORDS]


# Without a shared word, a difflib ratio must be this high to count: two
# RPA parcel codes ("SO9820 8294" / "ST9482 4843") score about 0.6 on
# characters alone, and that is noise, not a match.
NAME_RATIO_FLOOR = 0.8


def name_similarity(a: str, b: str) -> float:
    """0–1: the share of shared words, else a strict difflib ratio."""
    ta, tb = normalise_name(a), normalise_name(b)
    if not ta or not tb:
        return 0.0
    overlap = len(set(ta) & set(tb)) / min(len(set(ta)), len(set(tb)))
    if overlap:
        return overlap
    ratio = SequenceMatcher(None, ' '.join(ta), ' '.join(tb)).ratio()
    return ratio if ratio >= NAME_RATIO_FLOOR else 0.0


def suggest_matches(shapes, locations) -> dict[int, dict]:
    """``{shape.index: {'location': Location, 'strength': 'strong'|'weak'}}``.

    Spatial first: a location whose stored point sits inside the polygon
    is near-certain. Then names, above ``NAME_MATCH_THRESHOLD``, marked
    weaker. Each location is suggested for at most one shape.
    """
    suggestions: dict[int, dict] = {}
    taken = set()
    polygons = {}
    for shp in shapes:
        if not shp.importable:
            continue
        try:
            polygons[shp.index] = shape(shp.geometry)
        except Exception:
            continue

    for shp in shapes:
        poly = polygons.get(shp.index)
        if poly is None:
            continue
        for loc in locations:
            if loc.pk in taken or not loc.has_coordinates:
                continue
            if poly.contains(Point(float(loc.longitude), float(loc.latitude))):
                suggestions[shp.index] = {'location': loc, 'strength': 'strong'}
                taken.add(loc.pk)
                break

    for shp in shapes:
        if shp.index in suggestions or shp.index not in polygons:
            continue
        best, best_score = None, 0.0
        for loc in locations:
            if loc.pk in taken:
                continue
            score = name_similarity(shp.name, loc.name)
            if score > best_score:
                best, best_score = loc, score
        if best is not None and best_score >= NAME_MATCH_THRESHOLD:
            suggestions[shp.index] = {'location': best, 'strength': 'weak'}
            taken.add(best.pk)
    return suggestions


# ── Committing ─────────────────────────────────────────────────────────────

def apply_boundary(location, shp: ImportedShape, *, source, now, user=None):
    """Write one shape onto one location. Returns the previous boundary
    (or None) so the caller can record it. Not transactional by itself."""
    from .models import LocationBoundaryHistory

    previous = location.boundary
    if previous is not None:
        LocationBoundaryHistory.objects.create(
            location=location, boundary=previous,
            source=location.boundary_source, replaced_by=user,
        )
    location.boundary = shp.geometry
    location.boundary_source = source
    location.boundary_updated_at = now
    fields = ['boundary', 'boundary_source', 'boundary_updated_at', 'updated_at']
    if not location.has_coordinates:
        # An import never leaves a location worse off: give it a point.
        from decimal import Decimal
        location.latitude = Decimal(str(shp.anchor[0]))
        location.longitude = Decimal(str(shp.anchor[1]))
        fields += ['latitude', 'longitude']
    location.full_clean()
    location.save(update_fields=fields)
    return previous


# ── The matching screen's preview ──────────────────────────────────────────

PREVIEW_COLOUR = '#3D5A63'


def preview_payload(site, shapes) -> dict:
    """The uploaded shapes shaped like ``map_locations`` output, so the
    phase 3 map partial can draw them before anything is saved. Badges show
    the shape number; tapping one jumps to its row."""
    locations = []
    for shp in shapes:
        locations.append({
            'pk': f'shape-{shp.index}',
            'name': shp.name,
            'site': site,
            'count': shp.index,
            'capacity': None,
            'availability': None,
            'usage': 'other',
            'usage_label': 'Imported shape',
            'holds_horses': True,       # so the badge shows the number
            'rest_days': None,
            'lat': shp.anchor[0],
            'lng': shp.anchor[1],
            'boundary': shp.geometry if shp.importable else None,
            'anchor': list(shp.anchor),
            'kind': 'polygon' if shp.importable else 'circle',
            'state': None,
            'colour': '#C0392B' if not shp.importable else PREVIEW_COLOUR,
            'radius_m': None if shp.importable else 25,
            'urls': {'horses': f'#shape-{shp.index}', 'detail': f'#shape-{shp.index}'},
        })
    return {
        'site': site,
        'horses': 0,
        'total': len(locations),
        'located': len(locations),
        'unlocated': 0,
        'locations': locations,
    }
