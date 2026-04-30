"""
CSV import script to load existing horse data.

Usage:
    python manage.py shell < data/import_csv.py

Or run as a Django management command.
"""

import csv
import os
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

# Add project root to path for Django setup
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'horse_management.settings')

import django
django.setup()

from django.db import transaction

from core.models import Horse, Location, Owner, Placement, RateType


REQUIRED_LOCATION_HEADERS = {'Horse', 'Location', 'Owners'}


def open_csv(filepath):
    """Open a CSV with BOM-aware UTF-8 first, falling back to cp1252.

    Excel often exports as cp1252; without this fallback an exported file
    crashes the importer mid-loop with UnicodeDecodeError.
    """
    try:
        return open(filepath, 'r', encoding='utf-8-sig')
    except UnicodeDecodeError:
        return open(filepath, 'r', encoding='cp1252')


def parse_date(date_str):
    """Parse date from various formats."""
    if not date_str:
        return None

    date_str = date_str.strip()

    # Try various formats
    formats = [
        '%d/%m/%Y',
        '%d-%b-%y',
        '%d-%m-%Y',
        '%Y-%m-%d',
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    print(f"Could not parse date: {date_str}")
    return None


def parse_age(description):
    """Extract age from description like '13yo grey gelding'."""
    if not description:
        return None

    match = re.search(r'(\d+)yo', description)
    if match:
        age = int(match.group(1))
        # Filter out obviously wrong ages (126 seems to be placeholder)
        if age > 50:
            return None
        return age
    return None


def parse_sex(description):
    """Extract sex from description."""
    if not description:
        return ''

    description = description.lower()

    if 'gelding' in description:
        return 'gelding'
    elif 'mare' in description:
        return 'mare'
    elif 'stallion' in description:
        return 'stallion'
    elif 'colt' in description:
        return 'colt'
    elif 'filly' in description:
        return 'filly'

    return ''


def parse_color(description):
    """Extract color from description."""
    if not description:
        return ''

    description = description.lower()

    colors = [
        ('chestnut', 'chestnut'),
        ('chesnut', 'chestnut'),  # Handle typo
        ('bay/brown', 'brown'),
        ('bay', 'bay'),
        ('grey', 'grey'),
        ('black', 'black'),
        ('brown', 'brown'),
        ('palomino', 'palomino'),
        ('skewbald', 'skewbald'),
        ('piebald', 'piebald'),
        ('roan', 'roan'),
        ('dun', 'dun'),
    ]

    for pattern, color in colors:
        if pattern in description:
            return color

    return ''


def parse_rate_info(rate_str):
    """Parse rate type and daily rate from string like 'Grass Livery incl hay £5 per day since 09/09/2025'."""
    if not rate_str:
        return None, None, None

    # Extract the rate amount
    rate_match = re.search(r'[£€](\d+(?:\.\d+)?)', rate_str)
    rate = Decimal(rate_match.group(1)) if rate_match else None

    # Extract date
    date_match = re.search(r'since\s+(\d{2}/\d{2}/\d{4})', rate_str)
    date = parse_date(date_match.group(1)) if date_match else None

    # Determine rate type
    rate_str_lower = rate_str.lower()
    if 'mare and foal' in rate_str_lower:
        rate_type = 'Mare and Foal'
    elif 'stable' in rate_str_lower:
        rate_type = 'Stabled'
    elif 'horse grazing' in rate_str_lower:
        rate_type = 'Horse Grazing'
    else:
        rate_type = 'Grass Livery'

    return rate_type, rate, date


def parse_owner_name(owner_str):
    """Clean up owner name."""
    if not owner_str:
        return None

    owner_str = owner_str.strip()

    # Remove trailing notes like 'since XX/XX/XXXX'
    owner_str = re.sub(r'\s+since\s+\d+/\d+/\d+.*$', '', owner_str)

    # Remove trailing rate info
    owner_str = re.sub(r'\s+\d+\.\d+\s*$', '', owner_str)

    # Handle format "Fox, Mrs Tamara" -> "Mrs Tamara Fox"
    comma_match = re.match(r'^(\w+),\s*(.+)$', owner_str)
    if comma_match:
        owner_str = f"{comma_match.group(2).strip()} {comma_match.group(1).strip()}"

    return owner_str.strip() if owner_str else None


def get_site_from_location(location_name):
    """Extract site name from location."""
    if not location_name:
        return 'Unknown'

    location_lower = location_name.lower()

    if 'colgate' in location_lower:
        return 'Colgate'
    elif 'somerford' in location_lower:
        return 'Somerford'
    elif 'california' in location_lower:
        return 'California Farm'
    elif 'little tew' in location_lower:
        return 'Little Tew'
    elif 'bourton' in location_lower:
        return 'Bourton-on-the-Water'
    elif 'waverton' in location_lower:
        return 'Waverton Stud'

    return location_name.split()[0] if location_name else 'Unknown'


@transaction.atomic
def import_location_csv(filepath):
    """Import from horses-by-location CSV format.

    Wrapped in a transaction so any mid-import failure rolls back, leaving
    the database in its previous consistent state instead of a half-finished
    mix of horses/owners/placements.
    """
    print(f"\nImporting from: {filepath}")

    owners_created = 0
    locations_created = 0
    horses_created = 0
    placements_created = 0
    rate_types_created = 0

    # Cache for lookups
    owner_cache = {}
    location_cache = {}
    rate_cache = {}

    with open_csv(filepath) as f:
        reader = csv.DictReader(f)

        # Validate headers up front; DictReader silently returns None for
        # missing columns, which would otherwise drop every row in silence.
        fieldnames = set(reader.fieldnames or [])
        missing = REQUIRED_LOCATION_HEADERS - fieldnames
        if missing:
            raise ValueError(
                f"CSV {filepath} is missing required columns: "
                f"{sorted(missing)}. Found: {sorted(fieldnames)}"
            )

        for row in reader:
            # Skip empty rows
            if not row.get('Horse') or not row.get('Horse').strip():
                continue

            horse_name = row['Horse'].strip()
            location_name = row.get('Location', '').strip()
            owner_name_raw = row.get('Owners', '').strip()
            description = row.get('Description', '').strip()
            breeding = row.get('Breeding', '').strip()
            since_date = parse_date(row.get('SinceDate', ''))

            # Parse owner name
            owner_name = parse_owner_name(owner_name_raw)

            # Create/get owner
            if owner_name and owner_name not in owner_cache:
                owner, created = Owner.objects.get_or_create(
                    name=owner_name
                )
                owner_cache[owner_name] = owner
                if created:
                    owners_created += 1
                    print(f"  Created owner: {owner_name}")

            owner = owner_cache.get(owner_name)

            # Create/get location
            if location_name and location_name not in location_cache:
                site = get_site_from_location(location_name)
                location, created = Location.objects.get_or_create(
                    name=location_name,
                    defaults={'site': site}
                )
                location_cache[location_name] = location
                if created:
                    locations_created += 1
                    print(f"  Created location: {location_name} ({site})")

            location = location_cache.get(location_name)

            # Parse horse details
            age = parse_age(description)
            sex = parse_sex(description)
            color = parse_color(description)

            # Check for special notes in name
            notes = ''
            name_lower = horse_name.lower()
            if 'first winter' in name_lower:
                notes += 'First winter. '
            if 'lame' in name_lower:
                notes += 'Lame. '
            if 'no passport' in name_lower:
                notes += 'No passport. '
            if 'needs rug' in name_lower:
                notes += 'Needs rug. '

            # Check if horse already exists
            horse = Horse.objects.filter(name=horse_name).first()
            if not horse:
                horse = Horse.objects.create(
                    name=horse_name,
                    age=age,
                    sex=sex,
                    color=color,
                    breeding=breeding,
                    notes=notes.strip(),
                    has_passport='no passport' not in name_lower
                )
                horses_created += 1
                print(f"  Created horse: {horse_name}")

            # Get or create rate type (default to Grass Livery £5)
            rate_type_name = 'Grass Livery'
            rate_amount = Decimal('5.00')

            rate_key = f"{rate_type_name}_{rate_amount}"
            if rate_key not in rate_cache:
                rate_type, created = RateType.objects.get_or_create(
                    name=rate_type_name,
                    daily_rate=rate_amount,
                    defaults={'description': f'{rate_type_name} at £{rate_amount}/day'}
                )
                rate_cache[rate_key] = rate_type
                if created:
                    rate_types_created += 1
                    print(f"  Created rate type: {rate_type_name} @ £{rate_amount}")

            rate_type = rate_cache[rate_key]

            # Create placement if we have all required data
            if owner and location and since_date:
                # Check if similar placement exists
                existing = Placement.objects.filter(
                    horse=horse,
                    owner=owner,
                    location=location,
                    end_date__isnull=True
                ).first()

                if not existing:
                    Placement.objects.create(
                        horse=horse,
                        owner=owner,
                        location=location,
                        rate_type=rate_type,
                        start_date=since_date
                    )
                    placements_created += 1

    print(f"\nImport complete:")
    print(f"  Owners created: {owners_created}")
    print(f"  Locations created: {locations_created}")
    print(f"  Horses created: {horses_created}")
    print(f"  Rate types created: {rate_types_created}")
    print(f"  Placements created: {placements_created}")


def import_name_csv(filepath):
    """Import from horses-by-name CSV format to fill in rate details."""
    print(f"\nImporting rate details from: {filepath}")

    rate_types_created = 0

    # Pre-create common rate types
    rate_types_data = [
        ('Grass Livery', Decimal('5.00'), 'Standard grass livery including hay'),
        ('Grass Livery Premium', Decimal('7.00'), 'Premium grass livery including hay'),
        ('Horse Grazing', Decimal('6.00'), 'Horse grazing including hay'),
        ('Mare and Foal', Decimal('10.00'), 'Mare and foal at grass'),
        ('Stabled', Decimal('24.00'), 'Horse in stable'),
        ('Grass Livery Discounted', Decimal('4.725'), 'Discounted grass livery'),
        ('Mare and Foal Premium', Decimal('7.35'), 'Premium mare and foal rate'),
    ]

    for name, rate, description in rate_types_data:
        obj, created = RateType.objects.get_or_create(
            name=name,
            daily_rate=rate,
            defaults={'description': description}
        )
        if created:
            rate_types_created += 1
            print(f"  Created rate type: {name} @ £{rate}")

    print(f"Rate types created: {rate_types_created}")


def create_default_vaccination_types():
    """Create default vaccination types."""
    from health.models import VaccinationType

    types_data = [
        ('Flu', 12, 30, 'Annual flu vaccination'),
        ('Tetanus', 24, 60, 'Tetanus vaccination every 2 years'),
        ('Flu/Tet Combined', 12, 30, 'Combined flu and tetanus vaccination'),
        ('Strangles', 12, 30, 'Strangles vaccination'),
        ('Herpes (EHV)', 6, 30, 'Equine herpes virus vaccination'),
    ]

    created = 0
    for name, interval, reminder, desc in types_data:
        obj, was_created = VaccinationType.objects.get_or_create(
            name=name,
            defaults={
                'interval_months': interval,
                'reminder_days_before': reminder,
                'description': desc
            }
        )
        if was_created:
            created += 1
            print(f"  Created vaccination type: {name}")

    print(f"Vaccination types created: {created}")


def create_default_settings():
    """Create default business settings."""
    from core.models import BusinessSettings

    settings = BusinessSettings.get_settings()
    if settings.business_name == 'Horse Livery':
        settings.business_name = 'Colgate Livery'
        settings.save()
        print("  Updated business settings")


def run_import():
    """Run the full import process."""
    # Find CSV files
    data_dir = Path(__file__).resolve().parent.parent.parent

    location_csv = data_dir / '2026-02-03-horses-by-location.csv'
    name_csv = data_dir / '2026-02-03-horses-by-name-simple.csv'

    print("=" * 60)
    print("Horse Management System - Data Import")
    print("=" * 60)

    # Import rate types first
    if name_csv.exists():
        import_name_csv(name_csv)

    # Import location data
    if location_csv.exists():
        import_location_csv(location_csv)
    else:
        print(f"Location CSV not found: {location_csv}")

    # Create vaccination types
    print("\nCreating default vaccination types...")
    create_default_vaccination_types()

    # Create default settings
    print("\nCreating default settings...")
    create_default_settings()

    print("\n" + "=" * 60)
    print("Import completed!")
    print("=" * 60)


if __name__ == '__main__':
    run_import()
