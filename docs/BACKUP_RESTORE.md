# Backup and restore

A backup you have never restored is not a backup. This document says what
to back up, how, and how to prove it works.

## What has to be backed up

| What | Where it lives | In a database backup? |
|---|---|---|
| Horses, owners, placements, invoices, health records, users, roles | PostgreSQL | Yes |
| Celery beat schedule, task results | PostgreSQL | Yes |
| **Horse photos, passports, insurance documents, receipts, business logo** | A private media bucket (`MEDIA_S3_BUCKET`), or `MEDIA_ROOT` on a volume where that is not set | **No** |
| Environment variables (`SECRET_KEY`, `FIELD_ENCRYPTION_KEYS`, database URL, Xero credentials, SMTP credentials) | The host's dashboard | **No** |

The second and third rows are the ones that get forgotten. A perfect
database restore with no media files gives you invoices that reference
passports nobody can open. A perfect database restore with the wrong
`FIELD_ENCRYPTION_KEYS` gives you a Xero connection that cannot be
decrypted.

> **Keep `SECRET_KEY` and `FIELD_ENCRYPTION_KEYS` with the backup, in a
> password manager.** Without them a restored database still works, but the
> Xero tokens in it are unreadable and the integration must be reconnected.

## 1. Database

### Automatic backups from the provider

Turn these on first. They cost nothing and they cover the common case.

- **Supabase**: Project → Database → Backups. Check the retention period.
  The free tier keeps very little.
- **Railway Postgres**: Service → Backups. Set a daily schedule.

Write down the retention period here once you have set it:

    Provider backups retained for: ______ days

### The built-in nightly backup

The app takes its own backup of **both** the database and the media files,
to an object store of your choosing. This is the part that covers the two
rows above that a provider backup does not.

It is **off by default.** Turn it on with these variables on the `worker`
service (see `.env.example` for the full list):

```
BACKUP_ENABLED=True
BACKUP_S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
BACKUP_S3_BUCKET=yardway-backups
BACKUP_S3_ACCESS_KEY=...
BACKUP_S3_SECRET_KEY=...
```

Use a provider **different from the one hosting the database**. One
provider is one suspended account away from losing both copies.

If the app reaches PostgreSQL through a pooler — Supabase's pgbouncer does
this — also set `BACKUP_DATABASE_URL`. `pg_dump` cannot work through a
transaction-mode pooler (port 6543).

Which string to use, on Supabase:

| Option | Port | Use for `BACKUP_DATABASE_URL`? |
|---|---|---|
| Direct connection (`db.<ref>.supabase.co`) | 5432 | **Only if the host has outbound IPv6.** Supabase resolves this name to an IPv6 address only, and Railway cannot route to it: `Network is unreachable`. |
| **Session pooler** (`<region>.pooler.supabase.com`) | **5432** | **Yes.** IPv4, and session mode is what `pg_dump` needs. |
| Transaction pooler | 6543 | No. `pg_dump` cannot use it. |

The reliable way to build it: copy `DATABASE_URL` and change the port from
`6543` to `5432`. The username and password come across correct, which
avoids the trap that the pooler's username is `postgres.<project-ref>` and
not plain `postgres` — and that the error for a wrong username reads
`password authentication failed`, which sends you hunting the wrong thing.

**What it does**, nightly at 02:00 Europe/London:

1. `pg_dump --format=custom` of the database.
2. A `tar.gz` of the uploaded media, read through Django's storage
   backend — the media bucket when one is configured, `MEDIA_ROOT`
   otherwise.
3. Uploads both to `backups/database/` and `backups/media/`.
4. Deletes backups the retention policy has expired.

**Retention** is grandfather-father-son: every backup from the last 7 days,
then one per week for 4 weeks, then one per month for 12 months. About 23
copies. That covers both "undo yesterday" and "we only noticed two months
later".

**Run one by hand** — do this before any risky change:

```bash
python manage.py backup              # database and media
python manage.py backup --no-media   # database only
python manage.py backup --prune-only # apply retention, back nothing up
```

The command exits non-zero if the backup fails, so a cron or CI step can
tell.

### Checking it is actually running

This is the part people skip. A backup job that stopped three weeks ago
looks exactly like one that is working.

Every attempt writes a row to **`BackupRun`**, visible in the Django admin
under *Core → Backup runs*. Look at the newest row with status *Success*.
If it is not from last night, the backup is not working.

A failure does three things: it records the row, it emails the business
address in Settings, and it re-raises so Celery marks the task failed and
Sentry reports it.

### If you prefer to run it outside the app

```bash
pg_dump --format=custom --no-owner --no-privileges \
  "$DATABASE_URL" \
  --file "yardway-$(date +%Y-%m-%d).dump"

# rclone configured with a remote called "backup"
rclone sync /data/media backup:yardway-media --backup-dir backup:yardway-media-old
```

`--backup-dir` matters: a plain `sync` propagates a deletion. With it, a
file deleted by mistake is still recoverable.

## 2. Media files

Uploads are **not** in a database backup, whichever way they are stored.

### On a media bucket (the supported end state)

Set `MEDIA_S3_BUCKET`, `MEDIA_S3_ENDPOINT`, `MEDIA_S3_ACCESS_KEY` and
`MEDIA_S3_SECRET_KEY` and the app reads and writes uploads there instead of
a local disk. The nightly job then archives from the bucket.

**The bucket must be private.** No public development URL, no custom
domain. It holds passports, insurance documents and vet records. The app
serves them through signed links that expire after
`MEDIA_S3_SIGNED_URL_TTL` seconds (default 900).

### Why a volume is not enough

A host volume attaches to **one service**. With uploads on the web
service's volume, the nightly backup — which runs on the worker — sees an
empty directory, archives nothing, and still records **Success**. A restore
from such a backup gives you every record with no documents attached.

The symptom is a `Success` row in **Core → Backup runs** with a database
size and a dash under Media. Treat that dash as a failure.

A volume is also a single point of failure for *serving*: if it dies, the
app cannot show a photo until the archive is restored. A bucket is
reachable from every service and removes both problems.

### Moving existing uploads to the bucket

Once the four `MEDIA_S3_*` settings are in place, copy what is already on
the volume. Run this **on the service the volume is attached to**:

```bash
python manage.py migrate_media_to_s3 --dry-run   # list what would move
python manage.py migrate_media_to_s3             # move it
```

Files are written under the same relative paths the database already
stores, so every existing record keeps working and no data migration is
needed. The command skips anything already in the bucket, so running it
twice is harmless.

Without a shell on that service, put it in front of the start command for
one deploy — `python manage.py migrate_media_to_s3 && gunicorn ...` — then
take it out again. It is safe to repeat on every restart in the meantime.

## 3. The restore test

Do this **before** the security review, and then every six months. Put the
date in the table at the end of this file.

Two ways to run it. The first needs no local tooling and is the one to
reach for on a hosted deployment; the second is quicker if you already
have PostgreSQL and Python on your machine.

### Route A — from the host, no local tooling

`python manage.py restore_test` downloads the newest backup and puts it
back, into somewhere that is explicitly marked as scratch.

**Set up the scratch targets first.** Neither has a default, and the
command refuses to run without them:

```
RESTORE_TEST_DATABASE_URL=<a scratch database, not production>
RESTORE_TEST_MEDIA_BUCKET=<a scratch bucket, not the live one>
RESTORE_TEST_S3_ACCESS_KEY=<a token scoped to that scratch bucket>
RESTORE_TEST_S3_SECRET_KEY=
```

The scratch bucket needs its own token. The credentials fall back to the
`MEDIA_S3_*` ones, but a token scoped to a single bucket cannot write a
different one — and widening the live media token to cover the scratch
bucket would leave production uploads writable by a testing credential
long after the test finished.

The guards in `core/backup/restore.py` refuse a target that names the
same host and database as `DATABASE_URL` or `BACKUP_DATABASE_URL` — the
port is ignored in that comparison, because a pooler on 6543 and one on
5432 are two doors into the same database — a media bucket that is the
live one or the backup one, and a scratch database that already holds
tables.

```bash
python manage.py restore_test --list        # what is in the bucket
python manage.py restore_test               # database and media
python manage.py restore_test --database    # one or the other
python manage.py restore_test --reset       # empty the scratch database first
```

**Give the scratch bucket its own token, and copy both halves from one
screen.** An access key and a secret from different tokens produce
`SignatureDoesNotMatch`, and R2 shows a third value — the Token value —
that is for Cloudflare's own API and is not the secret. Every key looks
alike once the creation screen is closed, so name the tokens for what
they are.

`--reset` drops and recreates the scratch database's public schema before
restoring, which is what makes a second run possible without emptying a
database by hand. It is the most destructive thing here, so it happens
only behind that flag and only after the guards above have proved the
target is not production.

**Expect errors from a Supabase dump.** It carries
`CREATE EXTENSION supabase_vault`, which plain PostgreSQL does not have,
so `pg_restore` reports three errors, ignores them, and exits 1. That is
not a failed restore, and none of it is the yard's data. The command
does not take the exit code at face value either way: on exit 1 it checks
that the app's own tables arrived and are populated, and fails if they
did not.

**Restore first, then start the checking service.** `--reset` drops the
scratch database's schema, so anything already connected to it starts
returning errors and holds stale connections afterwards. Standing the
checking service up second avoids that; if it is already running,
redeploy it after the restore rather than just reloading the page.

Then point a throwaway copy of the app at the scratch database and media
bucket, and work through the five checks below. On a host where services
are created from a dashboard, that is a second web service using the same
repository with `DATABASE_URL` and `MEDIA_S3_BUCKET` set to the scratch
values — and the **production** `SECRET_KEY` and `FIELD_ENCRYPTION_KEYS`,
which is what makes the Xero check mean anything.

Delete the scratch service, database and bucket afterwards. The dump
holds every owner's details in plain text.

### Route B — on your own machine

1. Create a scratch database. Do not touch production.

   ```bash
   createdb yardway_restore_test
   ```

2. Restore the newest dump into it.

   ```bash
   pg_restore --no-owner --no-privileges \
     --dbname "postgres://user:pass@host:5432/yardway_restore_test" \
     yardway-YYYY-MM-DD.dump
   ```

3. Point a local copy of the app at it and start it.

   ```bash
   cd horse_management
   DATABASE_URL="postgres://user:pass@host:5432/yardway_restore_test" \
   SECRET_KEY="<the same key as production>" \
   FIELD_ENCRYPTION_KEYS="<the same keys as production>" \
   DEBUG=True python manage.py runserver
   ```

### The five checks — either route

Any one failing means the backup is incomplete. Finding that out here is
the entire point of the exercise.

   - [ ] Sign in with a real account.
   - [ ] Open the horse list. The count matches production.
   - [ ] Open an invoice and export the PDF.
   - [ ] Open a horse with a photo — does the image load? (It will not,
         unless media was restored too. That is the point of the check.)
   - [ ] Open Settings → Integrations. Does Xero show as connected? If it
         shows disconnected, the encryption keys did not match.

5. Record how long the whole thing took. That number is your recovery time.

6. Drop the scratch database.

   ```bash
   dropdb yardway_restore_test
   ```

## 4. Restoring for real

Same steps, but restore into a **new** database and repoint `DATABASE_URL`,
rather than restoring over the live one. If the backup turns out to be bad,
you still have the damaged original to work from.

Order:

1. Stop the `worker` and `beat` services first. Otherwise a scheduled task
   sends invoices or reminders from half-restored data.
2. Restore the database.
3. Restore the media files.
4. Repoint `DATABASE_URL` and redeploy `web`.
5. Check the five items above.
6. Start `worker` and `beat` again.

## Test log

| Date | Backup age | Restore time | Result | Notes |
|---|---|---|---|---|
| 2026-09-09 | 5 hours | ~2 hours, most of it first-time setup | Pass, with one known exception | Sign-in, horse count, invoice PDF and horse photos all restored and worked. Xero showed disconnected — correctly: the backup predated that day's reconnection, so the stored token genuinely could not be decrypted at the time it was taken. |
