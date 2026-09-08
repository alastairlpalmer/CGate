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
- Uploaded files under `/media/` are gated by the same roles, not only by
  being signed in (`horse_management/urls.py`). Passports and receipts are
  not reachable by guessing a path.

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

**Data integrity**
- `DATABASE_URL` missing with `DEBUG=False` stops the boot. There is no
  silent SQLite fallback that would accept data and lose it on redeploy.
- Upload limits: 10 MB per file, 12 MB per request.

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
| **Encryption key derives from `SECRET_KEY` by default.** Rotating `SECRET_KEY` forces a Xero reconnect. | Low | Set `FIELD_ENCRYPTION_KEYS` in production. |
| **Sessions last 30 days and roll forward.** A stolen session cookie stays valid a long time. | Medium | Shorten to 7 days once staff habits are known. |
| **Django admin is at the default `/admin/`.** | Low | Move the path, or restrict by IP at the proxy. |
| **No per-request rate limiting** outside sign-in. Report generation and PDF export are unthrottled. | Medium | Rate-limit at Cloudflare, or add `django-ratelimit` on the expensive views. |
| **Media files are on a single volume** with no replication. | High (availability) | Move to object storage. See `docs/BACKUP_RESTORE.md`. |
| **Python dependencies are version ranges (`~=`), not a lockfile.** Two builds can differ. (JavaScript has `package-lock.json`.) | Low | Add `pip-compile` or `uv lock`. |
| **No automated restore test.** | High (availability) | See `docs/BACKUP_RESTORE.md`. |

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
