# Backup and restore

A backup you have never restored is not a backup. This document says what
to back up, how, and how to prove it works.

## What has to be backed up

| What | Where it lives | In a database backup? |
|---|---|---|
| Horses, owners, placements, invoices, health records, users, roles | PostgreSQL | Yes |
| Celery beat schedule, task results | PostgreSQL | Yes |
| **Horse photos, passports, insurance documents, receipts, business logo** | `MEDIA_ROOT` — a Railway volume at `/data/media` | **No** |
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
this — also set `BACKUP_DATABASE_URL` to the **direct** connection string.
`pg_dump` cannot work through a transaction-mode pooler.

**What it does**, nightly at 02:00 Europe/London:

1. `pg_dump --format=custom` of the database.
2. A `tar.gz` of `MEDIA_ROOT`.
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

`MEDIA_ROOT` is a Railway volume. Railway volumes are **not** replicated and
**not** included in a database backup. If the volume is lost, every horse
photo and every uploaded passport is gone.

The nightly backup above copies the volume off-box, which removes the
"one disk, one copy" problem.

It does **not** remove the volume as a single point of failure for
*serving* those files: if the volume dies, the app cannot show a photo
until you restore the archive. **Moving media to object storage** — add
`django-storages` and point the `default` entry of `STORAGES` at R2 or S3 —
fixes that too, and is still the better end state.

## 3. The restore test

Do this **before** the security review, and then every six months. Put the
date in the table at the end of this file.

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

4. Check all five of these. Any one failing means the backup is incomplete.

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
|  |  |  |  |  |
