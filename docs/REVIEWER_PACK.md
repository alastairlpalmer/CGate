# Reviewer pack

For the developer and the security reviewer. Read this first — it should
save you a day of orientation.

Related: [`SECURITY.md`](../SECURITY.md) (controls and known gaps),
[`BACKUP_RESTORE.md`](BACKUP_RESTORE.md), [`CUTOVER.md`](CUTOVER.md)
(the Vercel → Railway migration).

## What the application does

Yardway manages a horse livery yard. Horses, their owners, which field each
horse is in, what that costs, monthly invoicing, health records, and
automated email reminders. One deployment serves one business.

## Architecture

```
                        ┌─────────────────────────────┐
   Browser ── HTTPS ──▶ │ Railway: web                │
                        │ gunicorn + WhiteNoise       │
                        │ Django 5.2 / Python 3.11    │
                        └──┬────────┬─────────┬───────┘
                           │        │         │
              ┌────────────▼──┐  ┌──▼─────┐  ┌▼──────────────┐
              │ PostgreSQL    │  │ Redis  │  │ Volume /data  │
              │ (Supabase)    │  │(broker)│  │ uploaded media│
              └───────────────┘  └──┬─────┘  └───────────────┘
                                    │
                     ┌──────────────┴──────────────┐
                     │                             │
             ┌───────▼────────┐            ┌───────▼────────┐
             │ Railway: worker│            │ Railway: beat  │
             │ celery worker  │            │ celery beat    │
             └───────┬────────┘            └────────────────┘
                     │
        ┌────────────┼─────────────┐
        │            │             │
   ┌────▼────┐  ┌────▼─────┐  ┌────▼──────┐
   │ SMTP    │  │ Xero API │  │ PostHog   │
   │ invoices│  │ OAuth2   │  │ analytics │
   │reminders│  │ invoices │  │ (proxied) │
   └─────────┘  └──────────┘  └───────────┘
```

**Note on hosting.** The app currently runs on **both** Vercel and Railway.
That is a migration in progress, not a design. Vercel has no worker
process, so reminders and invoice automation do not run there. Reducing to
Railway alone is the top open item — see `CUTOVER.md`.

## Where to look first

The highest-value files for a security review, in order:

| File | Why |
|---|---|
| `horse_management/horse_management/settings.py` | Every security setting, and roughly 40 lines of host-detection branching worth a second opinion. |
| `core/permissions.py` + `core/features.py` | The single authorisation seam. Every view declares a feature and a level. |
| `horse_management/horse_management/urls.py` | The gated `/media/` serving. Uploaded passports live behind this. |
| `core/auth_backends.py` | Sign-in by email or username. Handles the case where one string matches two accounts. |
| `core/encryption.py` + `core/fields.py` | Fernet encryption of the Xero tokens at rest. |
| `core/views/analytics_proxy.py` | A reverse proxy to PostHog. The upstream host is fixed by settings, so it is not an open proxy — please confirm that reading. |
| `xero_integration/client.py` | OAuth2 token refresh, under `select_for_update`. |
| `invoicing/services.py` | Money. Rate splitting between co-owners, partial periods, VAT. |

## Data map

| Data | Where | Retention today |
|---|---|---|
| Owner name, email, address, phone | PostgreSQL `core_owner` | Indefinite |
| Invoices and payments | PostgreSQL `invoicing_*` | Indefinite (UK: keep 6 years) |
| Horse passports, insurance documents | Volume `/data/media/documents/` | Indefinite |
| Horse photos, receipts | Volume `/data/media/` | Indefinite |
| Staff accounts and password hashes | PostgreSQL `auth_user` | Until deleted |
| Failed sign-in attempts | PostgreSQL `axes_accessattempt` | Cleared after the cool-off |
| Product analytics events | PostHog EU cloud | PostHog default |

**Analytics and personal data.** PostHog runs for signed-in users only.
Session replay masks every input and all page text. Query strings are
stripped before events leave the browser, because the search box puts owner
and horse names into `?q=`. Usernames are not sent to person profiles by
default (`POSTHOG_SEND_USERNAMES=False`). This is worth checking — see
`templates/includes/posthog.html` and `core/analytics.py`.

**No retention policy exists yet.** Nothing is deleted on a schedule. That
is a UK GDPR gap, not just an operational one.

## Threat list

Who would attack this, and what they would want. Ordered by how likely we
think each one is.

1. **A former employee with a live account.** Most likely by far. They know
   the URL, they know owner names, and their account may still work.
   *Control*: roles, and deactivation in Settings → Users.
   *Gap*: no automatic review of dormant accounts.

2. **Credential stuffing against a staff account.** Owner email addresses
   are printed on invoices, so the username half is not secret.
   *Control*: lockout after 5 failures (django-axes).
   *Gap*: no MFA.

3. **An owner seeing another owner's data.** There is no owner-facing
   login today, so this is limited to staff with a restricted role.
   *Control*: `core/permissions.py`, re-checked on every view and on
   `/media/` paths.
   *Please review this specifically.* It is the control we most want a
   second pair of eyes on.

4. **Theft of the Xero refresh token.** It grants access to the yard's
   accounting data without touching this app.
   *Control*: encrypted at rest; masked in the admin.
   *Gap*: an attacker with the running app's environment has the key.

5. **Loss of the media volume.** Not an attack, but the highest-impact
   failure. Every passport and photo is on one unreplicated disk.
   *Gap*: see `BACKUP_RESTORE.md`.

6. **Invoice fraud through a bug, not a breach.** A rate-splitting or
   partial-period error silently bills the wrong amount. There is history
   here — see `docs/QA_REPORT.md`.
   *Control*: `invoicing/test_*.py` is the largest part of the suite.

## What we would most like from the review

1. Is `core/permissions.py` actually a complete seam? Find a view or a
   template that reaches data without going through it.
2. Is the `/media/` gating in `urls.py` correct for every prefix, including
   ones added later?
3. Is `core/views/analytics_proxy.py` genuinely not an open proxy?
4. Is the Vercel/Railway settings branching hiding a case where a
   production deployment runs with a development setting?
5. Is the money code right? Co-owner splits, partial periods, VAT.

## Running it

```bash
cd horse_management
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then set SECRET_KEY
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Tests (1199 of them, about 90 seconds in parallel):

```bash
DJANGO_SETTINGS_MODULE=horse_management.test_settings \
  python manage.py test --parallel auto
```

Security checks: see the last section of `SECURITY.md`.

## Access we will give you

- **Read-only** access to the GitHub repository.
- A sign-in on a **staging** deployment with made-up data.
- A **read-only** database user on staging.

Not production, and not production secrets. If something can only be
reproduced on production, tell us and we will run it with you.
