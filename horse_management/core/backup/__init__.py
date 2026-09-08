"""Off-site backup of the database and the uploaded media files.

Run by `manage.py backup`, and nightly by the Celery task in
core/tasks.py. See docs/BACKUP_RESTORE.md for the restore procedure — the
half of this that actually matters.

Two things are backed up, and they are separate on purpose:

* The **database** — a pg_dump in PostgreSQL's custom format, which
  pg_restore can read selectively and which compresses itself.
* The **media files** — horse photos, passports, insurance documents and
  receipts. These live on a Railway volume and are in no database backup.
  Losing them loses documents that cannot be recreated.

Destination is any S3-compatible object store. Cloudflare R2 and Backblaze
B2 both work and both have a free tier large enough for a single yard. Use
a *different* provider from the one hosting the database: one provider is
one account suspension away from losing both.
"""
