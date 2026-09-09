"""Restore a backup into a scratch database, to prove the backups work.

    python manage.py restore_test --list        # what is in the bucket
    python manage.py restore_test --database    # database only
    python manage.py restore_test --media       # uploaded files only
    python manage.py restore_test               # both

This exists so the restore test is a thing you run from a dashboard
rather than a morning's work with a terminal. A test that is hard to run
gets run once and then never again, and a backup nobody has restored
since March is not meaningfully different from one nobody has restored at
all.

It will not touch production. See core/backup/restore.py for the guards
and why each one is there.
"""

from django.core.management.base import BaseCommand, CommandError

from core.backup import restore, runner


class Command(BaseCommand):
    help = 'Restore the newest backup into the scratch database and media bucket.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--list', action='store_true',
            help='Show the backups available and change nothing.',
        )
        parser.add_argument(
            '--database', action='store_true',
            help='Restore the database only.',
        )
        parser.add_argument(
            '--media', action='store_true',
            help='Restore the uploaded files only.',
        )
        parser.add_argument(
            '--key', default=None,
            help='Restore this exact object key instead of the newest. '
                 'Only valid with one of --database or --media.',
        )
        parser.add_argument(
            '--reset', action='store_true',
            help='Empty the scratch database first, by dropping and '
                 'recreating its public schema. Makes the test repeatable. '
                 'Destructive, so it runs only after the guards have proved '
                 'the target is not production.',
        )
        parser.add_argument(
            '--allow-nonempty', action='store_true',
            help='Restore even though the scratch database already holds '
                 'tables. Off by default: that guard is what stops a '
                 'mistyped URL from landing on something real.',
        )

    def handle(self, *args, **options):
        if options['list']:
            return self._list()

        want_database = options['database'] or not options['media']
        want_media = options['media'] or not options['database']

        if options['key'] and want_database and want_media:
            raise CommandError('--key needs --database or --media, so it is clear which one it names.')

        try:
            if want_database:
                self._restore_database(options)
            if want_media:
                self._restore_media(options)
        except (restore.RestoreNotPermitted, restore.RestoreError) as exc:
            raise CommandError(str(exc))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            'Restored. Now point a throwaway copy of the app at the scratch '
            'database and work through the five checks in '
            'docs/BACKUP_RESTORE.md — the restore is only proved by those.'
        ))

    def _list(self):
        for label, prefix in (('Database', runner.DB_PREFIX), ('Media', runner.MEDIA_PREFIX)):
            self.stdout.write(f'{label}:')
            try:
                keys = sorted(k for k in restore.storage.list_keys(prefix) if not k.endswith('/'))
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                raise CommandError(f'Could not list {prefix}: {exc}')
            if not keys:
                self.stdout.write('  (none)')
            for key in keys[-10:]:
                marker = '  <- newest' if key == keys[-1] else ''
                self.stdout.write(f'  {key}{marker}')
            self.stdout.write('')

    def _restore_database(self, options):
        self.stdout.write('Restoring the database...')
        if options['reset']:
            self.stdout.write(self.style.WARNING(
                '  emptying the scratch database first (--reset)'
            ))
        result = restore.restore_database(
            key=options['key'] if options['database'] else None,
            allow_nonempty=options['allow_nonempty'],
            reset=options['reset'],
        )
        self.stdout.write(f"  from {result['key']} ({result['bytes']} bytes)")
        if result['warnings']:
            self.stdout.write(self.style.WARNING('  pg_restore said:'))
            for line in result['warnings'].splitlines():
                self.stdout.write(f'    {line}')
        if result['ignored_errors']:
            self.stdout.write(self.style.WARNING(
                '  pg_restore ignored the errors above and carried on. The '
                'app tables are present and populated, so the restore stands. '
                'Supabase dumps always report the supabase_vault extension '
                'as missing on plain PostgreSQL; that is not your data.'
            ))

    def _restore_media(self, options):
        self.stdout.write('Restoring the uploaded files...')
        result = restore.restore_media(key=options['key'] if options['media'] else None)
        self.stdout.write(
            f"  from {result['key']}: {result['files']} files "
            f"({result['bytes']} bytes) into {result['bucket']}"
        )
