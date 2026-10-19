"""
Management command to import horse data from CSV files into the database.

Reads two CSV files:
  1. horses-by-name-simple.csv  - horse details, ownership, and rate/keep status
  2. horses-by-location.csv     - current location for each horse

Creates: BusinessSettings, Owner, Location, Horse, RateType, Placement records.
Idempotent: skips import if Placement records already exist.
"""

import csv
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    BusinessSettings,
    Horse,
    Location,
    Owner,
    Placement,
    RateType,
)

# ---------------------------------------------------------------------------
# Paths (relative to the Colgate project root)
# ---------------------------------------------------------------------------
BASE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', '..')
)
CSV1_PATH = os.path.join(BASE_DIR, '2026-02-03-horses-by-name-simple.csv')
CSV2_PATH = os.path.join(BASE_DIR, '2026-02-03-horses-by-location.csv')

# ---------------------------------------------------------------------------
# Color mapping  (CSV value -> model choice value)
# ---------------------------------------------------------------------------
COLOR_MAP = {
    'bay': 'bay',
    'chestnut': 'chestnut',
    'chesnut': 'chestnut',       # common typo in data
    'grey': 'grey',
    'black': 'black',
    'brown': 'brown',
    'palomino': 'palomino',
    'skewbald': 'skewbald',
    'piebald': 'piebald',
    'roan': 'roan',
    'dun': 'dun',
    'cream': 'cream',
    'bay/brown': 'bay',          # closest match
    'unknown': '',               # leave blank
}

# ---------------------------------------------------------------------------
# Sex mapping  (CSV value -> model choice value)
# ---------------------------------------------------------------------------
SEX_MAP = {
    'gelding': 'gelding',
    'mare': 'mare',
    'stallion': 'stallion',
    'colt': 'colt',
    'filly': 'filly',
    'horse': 'gelding',          # best guess for unspecified
    'yearling colt': 'colt',
}

# ---------------------------------------------------------------------------
# Known site prefixes for location parsing from CSV 2.
# Order matters: longer / more-specific prefixes first so they match before
# a shorter prefix would.
# ---------------------------------------------------------------------------
KNOWN_SITES = [
    'California farm',
    'Bourton-on-the-Water',
    'Waverton stud',      # lowercase so case-insensitive compare works
    'Waverton Stud',
    'Somerford',
    'Colgate',
    'Little Tew',
]


# ===================================================================
# Parsing helpers
# ===================================================================

def _split_respecting_parens(text: str) -> list[str]:
    """
    Split *text* on commas, but ignore commas that are inside parentheses.

    >>> _split_respecting_parens("GG (grey, with navy rug), 126yo grey mare, ")
    ['GG (grey, with navy rug)', '126yo grey mare', '']
    """
    parts = []
    current = []
    depth = 0
    for ch in text:
        if ch == '(':
            depth += 1
            current.append(ch)
        elif ch == ')':
            depth = max(depth - 1, 0)
            current.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    parts.append(''.join(current).strip())
    return parts


def parse_horse_name_field(raw: str):
    """
    Parse the HorseName column from CSV 1.

    Format: ``" Name, AgeYo Color Sex, Breeding"``

    Returns dict with keys: name, age, color, sex, breeding, has_passport, notes
    """
    # Strip outer whitespace and surrounding quotes
    raw = raw.strip().strip('"').strip()

    # Split on commas, but NOT commas inside parentheses.
    # e.g. "GG (grey, with navy rug), 126yo grey mare, " -> 3 parts
    parts = _split_respecting_parens(raw)

    name = parts[0].strip() if parts else raw.strip()
    age = None
    color = ''
    sex = ''
    breeding = ''
    has_passport = True
    notes = ''

    # --- Extract "no passport" flag from name ---
    if 'no passport' in name.lower():
        has_passport = False
        # Clean the flag out of the name, e.g. "True - 506 (no passport) "
        name = re.sub(r'\s*[-–]\s*no passport', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(no passport\)\s*', ' ', name, flags=re.IGNORECASE)
        name = re.sub(r'\bno passport\b', '', name, flags=re.IGNORECASE)
        name = name.strip(' -')
        notes = 'No passport'

    # --- Extract "no passport" from other parts too (e.g. "Flossie - no passport") ---
    # Already handled above since it's in the name portion.

    # --- Parse age / color / sex from second part ---
    if len(parts) >= 2:
        desc = parts[1].strip()
        # Expected pattern: "13yo grey gelding"
        m = re.match(
            r'(\d+)\s*yo\s+([\w/]+)\s+(.*)',
            desc,
            re.IGNORECASE,
        )
        if m:
            raw_age = int(m.group(1))
            age = None if raw_age == 126 else raw_age
            color = COLOR_MAP.get(m.group(2).lower().strip(), '')
            sex_raw = m.group(3).strip().lower()
            sex = SEX_MAP.get(sex_raw, '')
        else:
            # Try without color: e.g. "3yo gelding"
            m2 = re.match(r'(\d+)\s*yo\s+(.*)', desc, re.IGNORECASE)
            if m2:
                raw_age = int(m2.group(1))
                age = None if raw_age == 126 else raw_age
                sex_raw = m2.group(2).strip().lower()
                sex = SEX_MAP.get(sex_raw, '')

    # --- Breeding from third+ parts ---
    if len(parts) >= 3:
        breed_parts = ', '.join(parts[2:]).strip()
        # Breeding usually starts with "By ..."
        if breed_parts:
            breeding = breed_parts

    return {
        'name': name.strip(),
        'age': age,
        'color': color,
        'sex': sex,
        'breeding': breeding,
        'has_passport': has_passport,
        'notes': notes,
    }


def parse_owner_field(raw: str):
    """
    Parse the CurrentOwnership column from CSV 1.

    Formats seen:
      - ``Mr Andrew Hine since 09/09/2025``
      - ``"Fox, Mrs Tamara since 05/10/2022"``
      - ``"Clarkin, Nina and JP since 17/01/2023"``
      - ``Mr Mikey Howe 3.50 since 24/09/2024``
      - ``, since 28/03/2023``  (empty owner)

    Returns (owner_name: str, since_date: datetime.date | None)
    """
    raw = raw.strip().strip('"').strip()

    # Handle values that start with "since " (empty owner name)
    if raw.lower().startswith('since '):
        date_str = raw[6:].strip()
        try:
            since_date = datetime.strptime(date_str, '%d/%m/%Y').date()
        except ValueError:
            since_date = None
        return 'Unknown Owner', since_date

    # Split on " since "
    since_date = None
    if ' since ' in raw:
        before_since, date_str = raw.rsplit(' since ', 1)
        date_str = date_str.strip()
        try:
            since_date = datetime.strptime(date_str, '%d/%m/%Y').date()
        except ValueError:
            before_since = raw  # couldn't parse date, keep raw
    else:
        before_since = raw

    owner_raw = before_since.strip()

    # Handle "Surname, FirstPart" format  (e.g. "Fox, Mrs Tamara")
    if ',' in owner_raw:
        surname_part, first_part = owner_raw.split(',', 1)
        surname_part = surname_part.strip()
        first_part = first_part.strip()
        if surname_part and first_part:
            owner_raw = f"{first_part} {surname_part}"
        elif surname_part:
            owner_raw = surname_part

    # Strip stray trailing numbers (e.g. "Mr Mikey Howe 3.50")
    owner_raw = re.sub(r'\s+\d+\.\d+\s*$', '', owner_raw)

    # Normalise whitespace and strip stray punctuation
    owner_raw = ' '.join(owner_raw.split())
    owner_raw = owner_raw.strip(' ,')

    # If completely empty, use a placeholder
    if not owner_raw:
        owner_raw = 'Unknown Owner'

    return owner_raw, since_date


def parse_rate_field(raw: str):
    """
    Parse the CurrentKeepStatus column from CSV 1.

    Examples:
      ``Grass Livery incl hay \xa35 per day since 09/09/2025``
      ``Horse grazing@ \xa36/day incl hay since 11/09/2025``
      ``Mare and foal at grass \xa310 since 05/10/2025``
      ``Horse in stable \xa324 per day since 01/02/2026``
      ``Mare and Foal at grass @ \xa37.35/day since 30/01/2026``

    The \xa3 is the pound sign that may appear as the replacement char \ufffd.

    Returns (rate_name: str, daily_rate: Decimal, since_date: datetime.date | None)
    """
    raw = raw.strip()

    # Split off " since DD/MM/YYYY"
    since_date = None
    m_since = re.search(r'\s+since\s+(\d{2}/\d{2}/\d{4})\s*$', raw)
    if m_since:
        try:
            since_date = datetime.strptime(m_since.group(1), '%d/%m/%Y').date()
        except ValueError:
            pass
        raw = raw[:m_since.start()].strip()

    # Extract the monetary value.
    # The pound sign may be  \xa3  or  \ufffd  or  £  or absent before the number.
    # Patterns seen:
    #   ...hay £5 per day
    #   ...@ £6/day incl hay
    #   ...grass £10
    #   ...stable £24 per day
    #   ...@ £7.35/day
    #   ...hay £4.725 per day
    rate_match = re.search(r'[\xa3\ufffd£@]?\s*(\d+(?:\.\d+)?)\s*(?:/day|per day)?', raw)
    daily_rate = Decimal('0.00')
    rate_name = raw

    if rate_match:
        try:
            daily_rate = Decimal(rate_match.group(1))
        except InvalidOperation:
            pass

        # The rate name is the descriptive part, cleaned up.
        # Remove the rate number and surrounding punctuation from the string.
        # Strategy: take everything before the currency/@ symbol + number as the
        # base name, then append any meaningful text after the number.
        full = raw

        # Remove the matched monetary portion and trailing "per day" / "/day"
        # We'll rebuild the name from the text parts.
        # Remove "@ " before the amount
        full = re.sub(r'@\s*', '', full)
        # Remove the currency symbol(s)
        full = re.sub(r'[\xa3\ufffd£]', '', full)
        # Remove the numeric rate and per day/day suffix
        full = re.sub(r'\d+(?:\.\d+)?\s*(?:/day|per day)?', '', full)
        # Collapse whitespace
        full = ' '.join(full.split())
        rate_name = full.strip()

    # Normalise specific rate names for deduplication
    rate_name = normalise_rate_name(rate_name)

    return rate_name, daily_rate, since_date


def normalise_rate_name(name: str) -> str:
    """
    Normalise rate type names so equivalent descriptions collapse to the same key.
    """
    # Lowercase for comparison, then title-case the result
    n = name.strip()
    # "Mare and Foal at grass" / "Mare and foal at grass" -> same
    n_lower = n.lower()

    if 'mare and foal' in n_lower and 'grass' in n_lower:
        return 'Mare and foal at grass'
    if 'horse grazing' in n_lower or 'horse grazing@' in n_lower:
        return 'Horse grazing incl hay'
    if 'grass livery' in n_lower:
        return 'Grass Livery incl hay'
    if 'horse in stable' in n_lower:
        return 'Horse in stable'

    # Default: title-case
    return n


def parse_location_field(raw: str):
    """
    Parse the Location column from CSV 2.

    Returns (site: str, field_name: str)

    Special cases:
      - "Somerford - Flat Whitakers"   -> ("Somerford", "Flat Whitakers")
      - "Waverton Stud "               -> ("Waverton Stud", "Waverton Stud")
      - "Waverton stud - mini"         -> ("Waverton Stud", "Waverton Stud - mini")
      - "Colgate Front field"          -> ("Colgate", "Front field")
      - "California farm Rough grounds"-> ("California farm", "Rough grounds")
      - "Little Tew"                   -> ("Little Tew", "Little Tew")
      - "Bourton-on-the-Water"         -> ("Bourton-on-the-Water", "Bourton-on-the-Water")
    """
    raw = raw.strip()

    # Handle " - " separator (e.g. "Somerford - Red Hatches", "Waverton stud - mini")
    if ' - ' in raw:
        site_part, name_part = raw.split(' - ', 1)
        site_part = site_part.strip()
        name_part = name_part.strip()

        # Special handling for "Waverton stud - mini" -> keep full name, normalise site
        if site_part.lower() == 'waverton stud':
            return 'Waverton Stud', f'Waverton Stud - {name_part}'

        return site_part, name_part

    # Try known site prefixes (longest-first matching)
    raw_lower = raw.lower()
    for site in KNOWN_SITES:
        if raw_lower.startswith(site.lower()):
            remainder = raw[len(site):].strip()
            site_actual = raw[:len(site)].strip()

            # Normalise "Waverton Stud " casing
            if site_actual.lower() == 'waverton stud':
                site_actual = 'Waverton Stud'

            if remainder:
                return site_actual, remainder
            else:
                # Single-word location: site and name are the same
                return site_actual, site_actual

    # Fallback: entire string is both site and name
    return raw, raw


def parse_date_csv2(raw: str):
    """
    Parse dates in D-Mon-YY format from CSV 2 (e.g. ``1-Oct-25``).

    Returns datetime.date or None.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%d-%b-%y').date()
    except ValueError:
        return None


def normalise_horse_name_for_matching(name: str) -> str:
    """
    Produce a canonical form of a horse name for matching between CSV 1 and CSV 2.
    Strips whitespace, lowercases, removes trailing punctuation.
    """
    n = name.strip().lower()
    n = re.sub(r'\s+', ' ', n)
    return n


# ===================================================================
# Main command
# ===================================================================

class Command(BaseCommand):
    help = 'Import horse data from the two CSV files into the database.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv1',
            default=CSV1_PATH,
            help='Path to horses-by-name-simple CSV file',
        )
        parser.add_argument(
            '--csv2',
            default=CSV2_PATH,
            help='Path to horses-by-location CSV file',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force import even if data already exists (will skip existing records)',
        )

    def handle(self, *args, **options):
        csv1_path = options['csv1']
        csv2_path = options['csv2']
        force = options['force']

        # Idempotency check
        if Placement.objects.exists() and not force:
            self.stdout.write(
                self.style.WARNING(
                    'Data already exists (Placements found). '
                    'Use --force to import anyway. Aborting.'
                )
            )
            return

        # Validate files exist
        for path, label in [(csv1_path, 'CSV 1 (by-name)'), (csv2_path, 'CSV 2 (by-location)')]:
            if not os.path.isfile(path):
                self.stderr.write(self.style.ERROR(f'{label} not found at: {path}'))
                return

        self.stdout.write(f'CSV 1: {csv1_path}')
        self.stdout.write(f'CSV 2: {csv2_path}')

        # Read CSV files
        csv1_rows = self._read_csv(csv1_path)
        csv2_rows = self._read_csv(csv2_path)

        self.stdout.write(f'  CSV 1 rows: {len(csv1_rows)}')
        self.stdout.write(f'  CSV 2 rows: {len(csv2_rows)}')

        # Build location lookup from CSV 2  (horse_name_normalised -> row)
        # We store multiple keys per horse to handle name variations between CSVs.
        # CSV 2 Horse column may contain "no passport" or other suffixes that
        # get stripped when CSV 1 parses the horse name.
        location_lookup = {}
        for row in csv2_rows:
            horse_col = row.get('Horse', '').strip()
            if horse_col:
                key = normalise_horse_name_for_matching(horse_col)
                location_lookup[key] = row

                # Also store a cleaned version with "no passport" stripped,
                # so horses like "Flossie - no passport" match parsed name "Flossie"
                cleaned = horse_col
                if 'no passport' in cleaned.lower():
                    cleaned = re.sub(r'\s*[-–]\s*no passport', '', cleaned, flags=re.IGNORECASE)
                    cleaned = re.sub(r'\s*\(no passport\)\s*', ' ', cleaned, flags=re.IGNORECASE)
                    cleaned = cleaned.strip(' -')
                cleaned_key = normalise_horse_name_for_matching(cleaned)
                if cleaned_key != key:
                    location_lookup[cleaned_key] = row

        self.stdout.write(f'  Location lookup entries: {len(location_lookup)}')

        # Import everything inside a transaction
        try:
            with transaction.atomic():
                self._do_import(csv1_rows, location_lookup)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'Import failed: {exc}'))
            raise

    # ---------------------------------------------------------------
    # CSV reader helper
    # ---------------------------------------------------------------
    def _read_csv(self, path: str):
        """Read a CSV file, trying utf-8 first then latin-1."""
        for encoding in ('utf-8', 'latin-1', 'cp1252'):
            try:
                with open(path, 'r', encoding=encoding, newline='') as fh:
                    reader = csv.DictReader(fh)
                    rows = list(reader)
                return rows
            except UnicodeDecodeError:
                continue
        # Last resort
        with open(path, 'r', encoding='utf-8', errors='replace', newline='') as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
        return rows

    # ---------------------------------------------------------------
    # Core import logic
    # ---------------------------------------------------------------
    def _do_import(self, csv1_rows, location_lookup):
        # --- 1. Business Settings ---
        settings, created = BusinessSettings.objects.get_or_create(pk=1)
        if created or settings.business_name != 'Colgate Livery':
            settings.business_name = 'Colgate Livery'
            settings.pk = 1
            settings.save()
            self.stdout.write(self.style.SUCCESS('Created BusinessSettings (Colgate Livery)'))
        else:
            self.stdout.write('BusinessSettings already exists.')

        # --- 2. Collect unique owners, rate types, locations ---
        owners_cache = {}       # normalised name -> Owner instance
        rate_types_cache = {}   # (normalised_name, rate) -> RateType instance
        locations_cache = {}    # (site, field_name) -> Location instance

        # Pre-create the "Unknown" location for horses not in CSV 2
        unknown_loc, _ = Location.objects.get_or_create(
            name='Unknown',
            site='Unknown',
            defaults={'description': 'Placeholder for horses with no known location'},
        )
        locations_cache[('Unknown', 'Unknown')] = unknown_loc

        # Pre-parse all CSV 2 locations so they are available
        for row in location_lookup.values():
            loc_raw = row.get('Location', '').strip()
            if loc_raw:
                site, field_name = parse_location_field(loc_raw)
                key = (site, field_name)
                if key not in locations_cache:
                    loc_obj, _ = Location.objects.get_or_create(
                        name=field_name,
                        site=site,
                        defaults={'description': ''},
                    )
                    locations_cache[key] = loc_obj

        self.stdout.write(
            self.style.SUCCESS(f'Created/found {len(locations_cache)} locations')
        )

        # --- 3. Process each horse row from CSV 1 ---
        horses_created = 0
        owners_created = 0
        rates_created = 0
        placements_created = 0

        for row in csv1_rows:
            raw_name = row.get('HorseName', '')
            raw_owner = row.get('CurrentOwnership', '')
            raw_rate = row.get('CurrentKeepStatus', '')

            if not raw_name.strip():
                continue

            # --- Parse horse ---
            horse_info = parse_horse_name_field(raw_name)
            horse_name = horse_info['name']

            if not horse_name:
                self.stdout.write(self.style.WARNING(f'  Skipping empty horse name: {raw_name!r}'))
                continue

            # --- Get or create Horse ---
            horse_obj, h_created = Horse.objects.get_or_create(
                name=horse_name,
                defaults={
                    'age': horse_info['age'],
                    'color': horse_info['color'],
                    'sex': horse_info['sex'],
                    'breeding': horse_info['breeding'],
                    'has_passport': horse_info['has_passport'],
                    'notes': horse_info['notes'],
                    'is_active': True,
                },
            )
            if h_created:
                horses_created += 1
                self.stdout.write(f'  Horse: {horse_name}')

            # --- Parse and get/create Owner ---
            owner_name, owner_since = parse_owner_field(raw_owner)
            owner_key = owner_name.strip().lower()

            if owner_key not in owners_cache:
                owner_obj, o_created = Owner.objects.get_or_create(
                    name=owner_name,
                    defaults={},
                )
                owners_cache[owner_key] = owner_obj
                if o_created:
                    owners_created += 1
                    self.stdout.write(f'  Owner: {owner_name}')
            owner_obj = owners_cache[owner_key]

            # --- Parse and get/create RateType ---
            rate_name, daily_rate, rate_since = parse_rate_field(raw_rate)
            rate_key = (rate_name.lower(), daily_rate)

            if rate_key not in rate_types_cache:
                rate_obj, r_created = RateType.objects.get_or_create(
                    name=rate_name,
                    daily_rate=daily_rate,
                    defaults={'is_active': True},
                )
                rate_types_cache[rate_key] = rate_obj
                if r_created:
                    rates_created += 1
                    self.stdout.write(
                        f'  RateType: {rate_name} @ {daily_rate}/day'
                    )
            rate_obj = rate_types_cache[rate_key]

            # --- Determine location from CSV 2 ---
            match_key = normalise_horse_name_for_matching(horse_name)
            loc2_row = location_lookup.get(match_key)

            if loc2_row:
                loc_raw = loc2_row.get('Location', '').strip()
                site, field_name = parse_location_field(loc_raw)
                loc_key = (site, field_name)
                location_obj = locations_cache.get(loc_key, unknown_loc)
            else:
                location_obj = unknown_loc

            # --- Determine start date ---
            # Use the rate/keep-status "since" date (placement start)
            start_date = rate_since
            if start_date is None:
                # Fallback to owner "since" date
                start_date = owner_since
            if start_date is None:
                # Last resort: today
                from django.utils import timezone
                start_date = timezone.localdate()

            # --- Create Placement (skip full_clean to avoid overlap validation
            #     during bulk import; all placements are new/non-overlapping) ---
            # Check for existing placement to support idempotency with --force
            placement_exists = Placement.objects.filter(
                horse=horse_obj,
                owner=owner_obj,
                end_date__isnull=True,
            ).exists()

            if not placement_exists:
                Placement.objects.create(
                    horse=horse_obj,
                    owner=owner_obj,
                    location=location_obj,
                    rate_type=rate_obj,
                    start_date=start_date,
                    end_date=None,
                    notes='',
                )
                placements_created += 1

        # --- Summary ---
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=== Import Summary ==='))
        self.stdout.write(f'  Horses created:     {horses_created}')
        self.stdout.write(f'  Owners created:     {owners_created}')
        self.stdout.write(f'  Rate types created: {rates_created}')
        self.stdout.write(f'  Locations created:  {len(locations_cache)}')
        self.stdout.write(f'  Placements created: {placements_created}')
        self.stdout.write(self.style.SUCCESS('Import complete.'))
