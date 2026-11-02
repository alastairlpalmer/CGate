"""
Management command to import CSV data.
"""

from django.core.management.base import BaseCommand

from data.import_csv import run_import


class Command(BaseCommand):
    help = 'Import horse data from CSV files'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Read the files and report what would be created, then save nothing.',
        )

    def handle(self, *args, **options):
        self.stdout.write('Starting data import...')
        run_import(dry_run=options['dry_run'])
        self.stdout.write(self.style.SUCCESS('Data import completed!'))
