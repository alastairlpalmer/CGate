# Security

How to report a problem with Yardway, what the app protects, and what it
does not protect yet.

## Reporting a vulnerability

Email the maintainer. Do not open a public GitHub issue for a security
problem — an open issue tells everyone about the hole before it is closed.

Include what you did, what happened, and what you expected. A short
reproduction is worth more than a scanner report.

## What this application holds

Yardway runs one livery business per deployment. It holds:

| Data | Sensitivity |
|---|---|
| Horse owner names, email addresses, postal addresses, phone numbers | Personal data (UK GDPR) |
| Invoices, rates, payments, bank details on invoice templates | Commercial and financial |
| Horse passports and insurance documents (uploaded files) | Personal and identifying |
| Vaccination and veterinary records | Operational |
| Staff sign-in credentials | Account security |

There is no multi-tenancy. One deployment serves one business, so there is
no cross-customer isolation boundary to get wrong.

## Controls in place

**Authentication and authorisation**
- Sign-in accepts an email address or a username, case-insensitively
  (`core/auth_backends.py`).
- Django's password validators are on: length, common passwords, numeric
  only, and similarity to the user's own details.
- Sign-in locks after 5 failed attempts for 30 minutes (django-axes,
  keyed on the username). Clear a lock early with
  `python manage.py axes_reset_username <email>`.
- Every feature area is gated by a role (`core/permissions.py`). Templates
  hide what a role cannot use, and every view re-checks — hiding is not
  enforcement.
- Sessions expire after 7 days without a request (`SESSION_COOKIE_AGE`).
  Expiry rolls forward, so weekly users are not signed out.
- The Django admin path is set by `ADMIN_URL`, so it can be moved off the
  default that untargeted scanners try first.
- Uploaded files served from a local disk are gated by the same roles, not
  only by being signed in (`horse_management/urls.py`). Passports and
  receipts are not reachable by guessing a path.
- With uploads in a bucket (`MEDIA_S3_BUCKET`), the bucket is private and
  every file is served through a signed link that expires after
  `MEDIA_S3_SIGNED_URL_TTL` seconds.

**Transport and browser**
- HTTPS redirect, HSTS for one year with subdomains and preload.
- Session and CSRF cookies: Secure, HttpOnly, SameSite=Lax.
- `X-Frame-Options: DENY` and a CSP `frame-ancestors 'none'`.
- Content-Security-Policy in **report-only** mode (see Known gaps).
- `Referrer-Policy: strict-origin-when-cross-origin`, so record IDs in URLs
  do not leak to third parties.

**Secrets**
- `SECRET_KEY` has no default. The app refuses to start without one.
- Xero OAuth access and refresh tokens are encrypted at rest with Fernet
  (`core/encryption.py`). They are shown masked in the Django admin.
- No secret is committed to the repository. CI runs gitleaks over the full
  history on every push.

**Data integrity and recovery**
- `DATABASE_URL` missing with `DEBUG=False` stops the boot. There is no
  silent SQLite fallback that would accept data and lose it on redeploy.
- Upload limits: 10 MB per file, 12 MB per request.
- Nightly off-site backup of the database **and** the uploaded media, with
  grandfather-father-son retention (`core/backup/`). Off until
  `BACKUP_ENABLED` and the storage credentials are set. Every attempt is
  recorded in `BackupRun`, so a job that stops running is visible rather
  than assumed. See `docs/BACKUP_RESTORE.md`.

**Monitoring**
- Sentry reports unhandled errors from both the web process and the Celery
  worker. Off until `SENTRY_DSN` is set.
- `send_default_pii` is False, and `core/monitoring.py` scrubs this app's
  own secrets and strips query strings before an event is sent. The app
  holds personal data; the default Sentry configuration would send more of
  it than is acceptable.

**Supply chain and CI**
- `.github/workflows/ci.yml` — lint, missing migrations, Django checks,
  1199 Python tests, JavaScript unit tests.
- `.github/workflows/security.yml` — `manage.py check --deploy` at
  production settings, `pip-audit`, `npm audit`, bandit, gitleaks. Also on
  a weekly timer, because a clean dependency today gets a CVE next month.
- `.gitleaksignore` lists the findings the secret scan may pass over. Each
  entry is a single fingerprint (commit, file, rule, line) with a written
  reason. A new secret in the same file still fails the scan. Review this
  file — a suppression is a decision, not a fix.
- Dependabot for pip, npm and GitHub Actions.

## Known gaps

Listed on purpose. A reviewer should not have to discover these.

| Gap | Risk | Plan |
|---|---|---|
| **No multi-factor authentication.** A stolen password is enough. | High | Add `django-otp` for administrator accounts. |
| **CSP allows `unsafe-inline` and `unsafe-eval` for scripts.** Inline `<script>` blocks and Alpine.js need them today. | Medium | Move to per-tag nonces and Alpine's CSP build, then set `CSP_ENFORCE=True`. |
| **Sessions have no absolute lifetime.** Seven days of inactivity ends one, but an attacker actively using a stolen cookie keeps it alive indefinitely. | Low | Add an absolute cap alongside the rolling one. |
| **`ADMIN_URL` still defaults to `admin/`.** The setting exists; a deployment that does not set it gains nothing. | Low | Set `ADMIN_URL` in production, or restrict by IP at the proxy. |
| **No per-request rate limiting** outside sign-in. Report generation and PDF export are unthrottled. | Medium | Rate-limit at Cloudflare, or add `django-ratelimit` on the expensive views. |
| **Uploads on a local volume are a single point of failure** where `MEDIA_S3_BUCKET` is not set. Worse, a volume attaches to one service, so the backup job on the worker cannot read files written by the web service and archives nothing while still reporting success. | Medium (availability) | Set the `MEDIA_S3_*` settings so uploads go to a private bucket. |
| **Python dependencies are version ranges (`~=`), not a lockfile.** Two builds can differ. (JavaScript has `package-lock.json`.) | Low | Add `pip-compile` or `uv lock`. |
| **The restore has never been tested.** Backups now run, but a backup nobody has restored is not proven. | High (availability) | Work through the five-point restore test in `docs/BACKUP_RESTORE.md` and record the date. |

## Running the security checks locally

```bash
cd horse_management
pip install -r requirements.txt bandit pip-audit ruff

# Production-shaped settings, so the deploy checks actually apply
SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(50))") \
  DEBUG=False DATABASE_URL=postgres://u:p@localhost:5432/x ALLOWED_HOSTS=example.com \
  python manage.py check --deploy --fail-level WARNING

pip-audit --requirement requirements.txt --strict
bandit --configfile bandit.yaml --recursive . --severity-level medium
ruff check .
```
