"""
Import horse data from CSV files into the Django database.
Run via: python manage.py shell < import_data.py
"""
import csv
import re
import os
import sys
from datetime import datetime, date
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'horse_management.settings')

import django
django.setup()

from core.models import Owner, Location, Horse, RateType, Placement, BusinessSettings

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)

NAME_CSV = os.path.join(PARENT_DIR, '2026-02-03-horses-by-name-simple.csv')
LOCATION_CSV = os.path.join(PARENT_DIR, '2026-02-03-horses-by-location.csv')


def parse_date(date_str):
    """Parse various date formats."""
    date_str = date_str.strip()
    for fmt in ('%d/%m/%Y', '%d-%b-%y', '%d-%B-%y', '%d/%m/%y'):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    print(f"  WARNING: Could not parse date: '{date_str}'")
    return None


def clean_text(text):
    """Clean text of encoding artifacts."""
    if not text:
        return ''
    # Replace common encoding artifacts for £
    text = text.replace('\ufffd', '£').replace('�', '£')
    return text.strip().strip('"').strip()


def parse_horse_info(horse_field):
    """Parse horse name, age, color, sex from the HorseName field."""
    horse_field = clean_text(horse_field)

    # Split by comma
    parts = [p.strip() for p in horse_field.split(',')]

    name = parts[0].strip() if parts else horse_field
    age = None
    color = ''
    sex = ''
    breeding = ''

    if len(parts) >= 2:
        # Second part: "13yo grey gelding"
        desc = parts[1].strip()

        # Extract age
        age_match = re.search(r'(\d+)yo', desc)
        if age_match:
            age = int(age_match.group(1))
            if age == 126:  # Seems like a placeholder for unknown age
                age = None
            desc = desc[age_match.end():].strip()

        # Map colors
        color_map = {
            'bay/brown': 'brown',
            'bay': 'bay',
            'chesnut': 'chestnut',
            'chestnut': 'chestnut',
            'grey': 'grey',
            'black': 'black',
            'brown': 'brown',
            'palomino': 'palomino',
            'roan': 'roan',
            'dun': 'dun',
            'cream': 'cream',
            'skewbald': 'skewbald',
            'piebald': 'piebald',
        }

        sex_map = {
            'mare': 'mare',
            'gelding': 'gelding',
            'stallion': 'stallion',
            'colt': 'colt',
            'filly': 'filly',
            'horse': '',  # generic
            'yearling colt': 'colt',
        }

        desc_lower = desc.lower()

        # Try to match color first (longer matches first)
        for key in sorted(color_map.keys(), key=len, reverse=True):
            if key in desc_lower:
                color = color_map[key]
                desc_lower = desc_lower.replace(key, '').strip()
                break

        # Try to match sex
        for key in sorted(sex_map.keys(), key=len, reverse=True):
            if key in desc_lower:
                sex = sex_map[key]
                break

    if len(parts) >= 3:
        breeding_part = parts[2].strip()
        if breeding_part:
            breeding = breeding_part

    return name, age, color, sex, breeding


def parse_owner(owner_field):
    """Parse owner name and date from ownership field."""
    owner_field = clean_text(owner_field)
    if not owner_field:
        return None, None

    # Handle "Fox, Mrs Tamara since ..." format
    if ',' in owner_field and 'since' in owner_field:
        comma_idx = owner_field.index(',')
        since_idx = owner_field.index('since')
        if comma_idx < since_idx:
            # Might be "LastName, Title FirstName since date"
            parts_before_since = owner_field[:since_idx].strip()
            # Check if it looks like "Fox, Mrs Tamara"
            name_parts = parts_before_since.split(',')
            if len(name_parts) == 2:
                last = name_parts[0].strip()
                first = name_parts[1].strip()
                owner_name = f"{first} {last}"
                date_str = owner_field[since_idx + 5:].strip()
                return owner_name, parse_date(date_str)

    # Handle "Name since DD/MM/YYYY" format
    since_match = re.search(r'(.+?)\s+since\s+(\d{2}/\d{2}/\d{4})', owner_field)
    if since_match:
        owner_name = since_match.group(1).strip()
        # Remove trailing rate info like "3.50"
        owner_name = re.sub(r'\s+\d+\.\d+$', '', owner_name)
        since_date = parse_date(since_match.group(2))
        return owner_name, since_date

    # Handle "Clarkin, Nina and JP since ..."
    if ',' in owner_field:
        parts = owner_field.split(',', 1)
        rest = parts[1].strip()
        since_match2 = re.search(r'(.+?)\s+since\s+(\d{2}/\d{2}/\d{4})', rest)
        if since_match2:
            name_part = since_match2.group(1).strip()
            owner_name = f"{name_part} {parts[0].strip()}"
            return owner_name, parse_date(since_match2.group(2))

    return owner_field, None


def parse_rate(rate_field):
    """Parse rate type name, daily rate, and start date."""
    rate_field = clean_text(rate_field)
    if not rate_field:
        return None, None, None

    # Extract "since DD/MM/YYYY" from end
    since_match = re.search(r'since\s+(\d{2}/\d{2}/\d{4})', rate_field)
    since_date = None
    if since_match:
        since_date = parse_date(since_match.group(1))
        rate_desc = rate_field[:since_match.start()].strip()
    else:
        rate_desc = rate_field

    # Extract rate amount - look for £ followed by number
    rate_amount = None
    rate_match = re.search(r'£(\d+(?:\.\d+)?)', rate_desc)
    if rate_match:
        rate_amount = Decimal(rate_match.group(1))

    # Determine rate type name
    rate_name = rate_desc
    # Clean up the rate name - remove the amount
    if rate_match:
        rate_name = rate_desc[:rate_match.start()].strip() + ' ' + rate_desc[rate_match.end():].strip()
    rate_name = re.sub(r'\s+', ' ', rate_name).strip()
    # Remove trailing "per day" variations for cleaner name, then re-add
    rate_name = rate_name.strip()

    # Standardize rate names
    if 'mare and foal' in rate_name.lower():
        rate_name = 'Mare and foal at grass'
    elif 'horse grazing' in rate_name.lower():
        rate_name = 'Horse grazing incl hay'
    elif 'grass livery' in rate_name.lower():
        rate_name = 'Grass Livery incl hay'
    elif 'stable' in rate_name.lower():
        rate_name = 'Horse in stable'

    return rate_name, rate_amount, since_date


def parse_location(loc_str):
    """Parse location string into site and field name."""
    loc_str = loc_str.strip()

    site_mappings = [
        ('Colgate', 'Colgate'),
        ('Somerford', 'Somerford'),
        ('California farm', 'California Farm'),
        ('California Farm', 'California Farm'),
        ('Little Tew', 'Little Tew'),
        ('Bourton-on-the-Water', 'Bourton-on-the-Water'),
        ('Waverton stud', 'Waverton Stud'),
        ('Waverton Stud', 'Waverton Stud'),
    ]

    for prefix, site in site_mappings:
        if loc_str.lower().startswith(prefix.lower()):
            field_name = loc_str[len(prefix):].strip()
            # Clean up separators
            field_name = field_name.lstrip('-').lstrip().strip()
            if not field_name:
                field_name = site  # Use site name if no field specified
            return site, field_name

    return loc_str, loc_str


def import_data():
    print("=" * 60)
    print("IMPORTING HORSE DATA")
    print("=" * 60)

    # Create default business settings
    BusinessSettings.get_settings()
    print("Created business settings")

    # --- STEP 1: Parse name CSV for horses, owners, rates ---
    print("\n--- Parsing horses-by-name CSV ---")

    horses_data = []
    with open(NAME_CSV, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            horse_field = row.get('HorseName', '')
            owner_field = row.get('CurrentOwnership', '')
            rate_field = row.get('CurrentKeepStatus', '')

            name, age, color, sex, breeding = parse_horse_info(horse_field)
            owner_name, owner_since = parse_owner(owner_field)
            rate_name, rate_amount, rate_since = parse_rate(rate_field)

            horses_data.append({
                'name': name,
                'age': age,
                'color': color,
                'sex': sex,
                'breeding': breeding,
                'owner_name': owner_name,
                'owner_since': owner_since,
                'rate_name': rate_name,
                'rate_amount': rate_amount,
                'rate_since': rate_since,
                'raw_horse': horse_field,
            })

    print(f"  Parsed {len(horses_data)} horses from name CSV")

    # --- STEP 2: Parse location CSV ---
    print("\n--- Parsing horses-by-location CSV ---")

    location_data = []
    with open(LOCATION_CSV, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            horse_name = clean_text(row.get('Horse', ''))
            location_str = clean_text(row.get('Location', ''))
            since_str = row.get('SinceDate', '').strip()

            site, field_name = parse_location(location_str)
            since_date = parse_date(since_str) if since_str else None

            location_data.append({
                'horse_name': horse_name,
                'location_full': location_str,
                'site': site,
                'field_name': field_name,
                'since': since_date,
            })

    print(f"  Parsed {len(location_data)} location entries")

    # --- STEP 3: Create Owners ---
    print("\n--- Creating Owners ---")

    owner_names = set()
    for h in horses_data:
        if h['owner_name']:
            # Normalize owner names
            name = h['owner_name'].strip()
            # Remove trailing whitespace artifacts
            name = re.sub(r'\s+', ' ', name).strip()
            h['owner_name'] = name
            owner_names.add(name)

    owners = {}
    for name in sorted(owner_names):
        owner, created = Owner.objects.get_or_create(name=name)
        owners[name] = owner
        status = "CREATED" if created else "exists"
        print(f"  {status}: {name}")

    # --- STEP 4: Create Locations ---
    print("\n--- Creating Locations ---")

    location_keys = set()
    for loc in location_data:
        location_keys.add((loc['site'], loc['field_name']))

    locations = {}
    for site, field_name in sorted(location_keys):
        loc, created = Location.objects.get_or_create(
            name=field_name,
            site=site,
        )
        locations[(site, field_name)] = loc
        status = "CREATED" if created else "exists"
        print(f"  {status}: {field_name} ({site})")

    # --- STEP 5: Create Rate Types ---
    print("\n--- Creating Rate Types ---")

    rate_keys = set()
    for h in horses_data:
        if h['rate_name'] and h['rate_amount']:
            rate_keys.add((h['rate_name'], h['rate_amount']))

    rate_types = {}
    for rate_name, rate_amount in sorted(rate_keys):
        rt, created = RateType.objects.get_or_create(
            name=rate_name,
            daily_rate=rate_amount,
        )
        rate_types[(rate_name, rate_amount)] = rt
        status = "CREATED" if created else "exists"
        print(f"  {status}: {rate_name} @ £{rate_amount}/day")

    # --- STEP 6: Create Horses ---
    print("\n--- Creating Horses ---")

    # Build location lookup by horse name
    horse_location_map = {}
    for loc in location_data:
        horse_location_map[loc['horse_name']] = loc

    horse_objects = {}
    for h in horses_data:
        name = h['name']

        # Check for special notes in name
        notes_parts = []

        # Extract notes like "first winter", "no passport", "lame", "needs rug", etc.
        note_patterns = [
            (r'\(first winter\)', 'First winter'),
            (r'\bfirst winter\b', 'First winter'),
            (r'\bno passport\b', 'No passport'),
            (r'\blame\b', 'Lame'),
            (r'\bneeds rug\b', 'Needs rug'),
            (r'\bpin fired\b', 'Pin fired'),
            (r'\bblisterd and lame\b', 'Blistered and lame'),
            (r'\bbad feet\b', 'Bad feet'),
        ]

        raw = h.get('raw_horse', '')
        for pattern, note in note_patterns:
            if re.search(pattern, raw, re.IGNORECASE):
                notes_parts.append(note)

        has_passport = True
        if 'no passport' in raw.lower():
            has_passport = False

        # Additional notes from name
        if h.get('breeding'):
            breeding = h['breeding']
            # Check if breeding is in "By X out of Y" format
            if breeding.startswith('By') or breeding.startswith('by'):
                pass  # Keep as is
        else:
            breeding = ''

        notes_str = '; '.join(notes_parts) if notes_parts else ''

        horse, created = Horse.objects.get_or_create(
            name=name,
            defaults={
                'age': h['age'],
                'color': h['color'],
                'sex': h['sex'],
                'breeding': breeding,
                'notes': notes_str,
                'has_passport': has_passport,
                'is_active': True,
            }
        )
        horse_objects[name] = horse
        status = "CREATED" if created else "exists"
        if created:
            details = []
            if h['age']:
                details.append(f"{h['age']}yo")
            if h['color']:
                details.append(h['color'])
            if h['sex']:
                details.append(h['sex'])
            print(f"  {status}: {name} ({', '.join(details) if details else 'no details'})")

    # --- STEP 7: Create Placements ---
    print("\n--- Creating Placements ---")

    placements_created = 0
    placements_skipped = 0

    for h in horses_data:
        name = h['name']
        horse = horse_objects.get(name)
        if not horse:
            print(f"  SKIP: Horse '{name}' not found")
            placements_skipped += 1
            continue

        owner = owners.get(h['owner_name']) if h['owner_name'] else None
        if not owner:
            print(f"  SKIP: No owner for horse '{name}' (owner field: '{h['owner_name']}')")
            placements_skipped += 1
            continue

        rate_key = (h['rate_name'], h['rate_amount']) if h['rate_name'] and h['rate_amount'] else None
        rate_type = rate_types.get(rate_key) if rate_key else None
        if not rate_type:
            print(f"  SKIP: No rate type for horse '{name}'")
            placements_skipped += 1
            continue

        # Find location from location CSV
        loc_entry = horse_location_map.get(name)
        location = None
        if loc_entry:
            loc_key = (loc_entry['site'], loc_entry['field_name'])
            location = locations.get(loc_key)

        if not location:
            # Try to find by partial name match
            for loc_data in location_data:
                if loc_data['horse_name'] and name.lower().startswith(loc_data['horse_name'].lower()[:10]):
                    loc_key = (loc_data['site'], loc_data['field_name'])
                    location = locations.get(loc_key)
                    if location:
                        break

        if not location:
            # Create a generic "Unknown" location
            location, _ = Location.objects.get_or_create(
                name='Unknown',
                site='Unknown',
            )

        # Use the rate start date or owner start date as placement start
        start_date = h['rate_since'] or h['owner_since'] or date(2025, 1, 1)

        # Check if placement already exists
        existing = Placement.objects.filter(
            horse=horse,
            owner=owner,
            end_date__isnull=True,
        ).exists()

        if not existing:
            Placement.objects.create(
                horse=horse,
                owner=owner,
                location=location,
                rate_type=rate_type,
                start_date=start_date,
            )
            placements_created += 1
        else:
            placements_skipped += 1

    print(f"\n  Created {placements_created} placements, skipped {placements_skipped}")

    # --- SUMMARY ---
    print("\n" + "=" * 60)
    print("IMPORT COMPLETE")
    print("=" * 60)
    print(f"  Owners:     {Owner.objects.count()}")
    print(f"  Locations:  {Location.objects.count()}")
    print(f"  Rate Types: {RateType.objects.count()}")
    print(f"  Horses:     {Horse.objects.count()}")
    print(f"  Placements: {Placement.objects.count()}")
    print("=" * 60)


if __name__ == '__main__':
    import_data()
