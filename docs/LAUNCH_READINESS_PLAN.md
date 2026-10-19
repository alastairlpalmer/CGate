# Launch readiness: check and plan

This document compares Yardway with a launch requirements list of about 150 items (169 checks after splitting some items).
It gives each item a status, the evidence in the code, and the next action.
Then it puts the actions in order as an implementation plan.

Date of check: 19 October 2026. Branch checked: `main` at `4b179b5`.

## How to read this document

| Status | Meaning |
|---|---|
| ✅ Done | The code does this. Evidence is given. |
| 🟡 Partial | Some of it is done. The gap is given. |
| ❌ Missing | The code does not do this. |
| ⚙️ Dashboard | No code is needed. A person must set it up in a provider dashboard. Most of these are in [`GO_LIVE_CHECKLIST.md`](GO_LIVE_CHECKLIST.md). |
| ➖ N/A | The item does not apply to Yardway now. The reason is given. |

The **Phase** column tells you when to do the work. See [Implementation plan](#implementation-plan).

| Phase | When |
|---|---|
| **P0** | This week. Small fixes and one bug. |
| **P1** | Before the first paying yard. |
| **P2** | Before Yardway is sold to more than one yard (SaaS). |
| **P3** | Only when an enterprise buyer asks for it. |

All code paths are relative to `horse_management/`.

---

## The one decision that changes this plan

Today Yardway is **single-tenant**: one deployment serves one yard
(`SECURITY.md`, "There is no multi-tenancy"). The business plan
(`docs/market/uk-market-sizing.md`) is to sell it as **SaaS to many yards**.

Many items on the list only matter for SaaS: tenant isolation, impersonation,
subscriptions, a status page, data residency per customer. This plan puts those
items in **P2**. If you sell to a second yard before P2 is done, each yard needs
its own deployment, database and bucket.

**Decision needed from you:** launch to one yard on the current design first
(recommended), or rebuild for multi-tenancy before launch?

---

## Summary

| Area | Items | ✅ | 🟡 | ❌ | ⚙️ | ➖ |
|---|---|---|---|---|---|---|
| Sign-in and access | 15 | 9 | 0 | 4 | 0 | 2 |
| Data and database | 23 | 11 | 8 | 3 | 0 | 1 |
| Security | 21 | 5 | 11 | 2 | 2 | 1 |
| Compliance | 12 | 0 | 2 | 6 | 0 | 4 |
| Infrastructure and deploys | 14 | 0 | 6 | 4 | 3 | 1 |
| Jobs, email and cache | 11 | 2 | 5 | 3 | 1 | 0 |
| Monitoring and incidents | 11 | 0 | 3 | 8 | 0 | 0 |
| Testing | 9 | 1 | 5 | 3 | 0 | 0 |
| Billing and payments | 11 | 2 | 4 | 3 | 0 | 2 |
| API and integrations | 8 | 2 | 0 | 1 | 0 | 5 |
| AI features | 8 | 0 | 0 | 0 | 0 | 8 |
| User experience | 12 | 1 | 8 | 2 | 0 | 1 |
| Code, docs and team | 14 | 0 | 7 | 5 | 2 | 0 |
| **Total** | **169** | **33** | **59** | **44** | **8** | **25** |

**What is already strong:** money is stored as `Decimal` everywhere, dates are
timezone-aware, invoice creation and sends are safe against duplicates, the
role system is a per-feature matrix, the Xero client handles token refresh,
rate limits and idempotency, every data migration can be reversed, and CI runs
about 1,490 tests plus CVE, secret and static-analysis scans.

**What is weakest:** no audit trail, no GDPR erasure, no staging
environment, no uptime monitoring or on-call, the restore has never been
tested, and the legal pages are placeholders.

---

## Fix first (P0)

These are small. Do them before anything else.

**Status:** items 1–5 and 7 are fixed in code. Item 6 is still to do in the Railway dashboard.

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | **The worker runs a restore test every time it starts.** A commit marked "TEMPORARY" was left in place. | `railway/worker.json` start command: `python manage.py restore_test --reset; celery ...` | Remove `restore_test --reset;` from the start command. Run the restore test by hand, as `BACKUP_RESTORE.md` says. |
| 2 | **The revenue chart leaves out overdue invoices.** An overdue invoice is still revenue, so the chart will not match Xero. | `core/views/finances.py:56` filters `status__in=['paid', 'sent']` | Add `'overdue'`. Add a test. Decide if the chart shows totals with or without VAT, and label it. |
| 3 | `SECURITY.md` says uploads are limited to 10 MB. The code limits them to 5 MB. | `core/models.py:28` | Correct `SECURITY.md`. |
| 4 | HTMX requests have no global CSRF header. Each form must remember its own token. | `templates/base.html:109` | Add `hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'` to `<body>`. |
| 5 | Password reset links stay valid for 3 days (Django default). | `PASSWORD_RESET_TIMEOUT` not set | Set `PASSWORD_RESET_TIMEOUT = 60 * 60` (1 hour). |
| 6 | `ADMIN_URL` defaults to `admin/`. | `horse_management/settings.py:643` | Set `ADMIN_URL` in production. ⚙️ |
| 7 | Some dates use the UTC date, not the London date. Between 00:00 and 01:00 in summer they give yesterday. | `invoicing/views.py:516`, `core/management/commands/backfill_location_usage.py:120`, `load_csv_data.py:669` | Use `timezone.localdate()`. |

---

## Full check

### 1. Sign-in and access

| Requirement | Status | Evidence | Action | Phase |
|---|---|---|---|---|
| Login works with a `+` in the email | ✅ | `core/auth_backends.py:28` matches `email__iexact`; nothing strips `+` | Add one test with a `+` address. | P0 |
| Reset links expire and work once | ✅ | Django's stock token: one use, 1-hour expiry (`PASSWORD_RESET_TIMEOUT`) | None. | — |
| MFA, and recovery for a lost phone | ✅ | TOTP app plus 10 single-use recovery codes (`core/mfa.py`). Required for Administrators. An Administrator can turn it off for a user who lost both; `mfa_reset` command for the last Administrator. | Encourage it for other roles. | — |
| Sessions time out | ✅ | 7-day rolling idle timeout plus a 30-day absolute cap (`SESSION_ABSOLUTE_AGE`, `core/middleware.py`) | None. | — |
| Kill every session for one user | ✅ | "Sign Out Everywhere" on Settings → Users, and "Sign Out Other Devices" on Account Security (`core/account_security.py`) | None. | — |
| SSO | ❌ | None | Only if an enterprise buyer requires it. | P3 |
| SAML | ❌ | None | Same as SSO. | P3 |
| SCIM | ❌ | None | Same as SSO. | P3 |
| Roles deeper than admin / everyone | ✅ | Custom roles × 14 features × hidden/view/full (`core/features.py`, `core/permissions.py`) | None. | — |
| User A cannot read user B's data by changing an ID | ➖ | Staff-only app. Access is per feature, not per record, by design. No owner login exists. | Becomes critical when the owner portal (payments plan phase 4) or multi-tenancy is built. Add per-object checks and tests then. | P2 |
| Tenant isolation | ➖ | One yard per deployment | Required for SaaS. Every query scoped by organisation, with tests that try to cross tenants. | P2 |
| Admin "log in as user", with audit trail | ❌ | None | Build with the audit log. Each use is logged with who, whom, when and why. | P2 |
| Rate limits on login | ✅ | django-axes on sign-in; password reset limited per address and per IP (`core/ratelimit.py`) | Set `CACHE_URL` to Redis so the reset limit is shared by all web processes. | ⚙️ P1 |
| Lockouts that do not lock out the CFO | ✅ | Lock stays keyed on username (behind a proxy the IP is not reliable; see `settings.py`). The person unlocks it themselves with a password reset, which needs their mailbox. Administrators have an **Unlock** button. MFA means a guessed password alone no longer signs anyone in. | None. | — |
| Invite users by email | ✅ | Leave the password blank on Add User to send a one-time, 3-day invitation link | None. | — |

### 2. Data and database

| Requirement | Status | Evidence | Action | Phase |
|---|---|---|---|---|
| A designed schema | 🟡 | Mostly sound. Smells: `Location.site` is free text used as a key (`core/models.py:225`); two ownership models side by side (`HorseOwnership`, `OwnershipShare`); `Horse.age` stored next to `date_of_birth`; old breeding date columns kept beside the new tables | Write a short schema decision record. Merge the ownership models and add a `Site` model before multi-tenancy. | P2 |
| Data model that survives the second customer | 🟡 | Single-yard assumptions: `BusinessSettings` singleton, GBP fixed, Xero `tenants[0]` | Part of the multi-tenancy rebuild. | P2 |
| Migrations run forward and roll back | ✅ | All 12 `RunPython` steps have `reverse_code` | None. | — |
| Migrations that do not lock the main table | ✅ | Backfills are batched; tables are small | For SaaS scale, add a review rule: no column default rewrite or index build without `CONCURRENTLY`. | P2 |
| Indexes | ✅ | 13 `Meta.indexes` blocks plus `db_index` on flags | Add an index on `Invoice.due_date` for the overdue sweep. | P2 |
| Connection pooling | ✅ | `CONN_MAX_AGE=600`, health checks, Supabase pooler (`settings.py:176`) | None. | — |
| Backups every night | 🟡 | Built (`core/backup/`), off until set up | Turn on (Go-live phase 2). | ⚙️ P1 |
| Restore from backup, tested | ❌ | Test log in `BACKUP_RESTORE.md:306` is empty | Run the five-point restore test and record the date. Repeat each quarter. | ⚙️ P1 |
| Point-in-time recovery | ❌ | Not set up; nightly dump only (up to 24 hours lost) | Turn on Supabase PITR (paid add-on). | ⚙️ P1 |
| Soft deletes | 🟡 | Owners, locations and horses archive. Payments, charges, costs, health records and draft invoices hard-delete. | Soft delete (or void with reason) for payments first, then charges and health records. Add an "Archived" view to restore them. | P1 |
| Validation on the server | ✅ | 35 form `clean` methods, 8 model `clean()`, some DB constraints | Add DB `CheckConstraint`s: amounts ≥ 0, `period_end ≥ period_start`. | P1 |
| Timezones stored right | ✅ | `USE_TZ=True`, `Europe/London`, Celery uses the same zone, `timezone.localdate()` everywhere except P0 #7 | Fix P0 #7. | P0 |
| Apostrophes in names | ✅ | ORM only, no raw SQL; PDF text is escaped (`invoicing/pdf.py:26`) | None. | — |
| Emoji, accents and right-to-left text | 🟡 | PDFs use Helvetica only, so Arabic, Chinese and emoji print as boxes. Statement file name built straight from the owner name (`invoicing/views.py:516`). CSV has no UTF-8 BOM, so Excel shows accents wrong. | Register a Unicode font (DejaVu or Noto) in `pdf.py`. Sanitise file names and add `filename*`. Write the BOM on CSV export. | P1 |
| Money stored as exact numbers | ✅ | `DecimalField` everywhere; no float in money paths | Decimal is correct (integers in pence would also be correct). Optionally set `ROUND_HALF_UP` explicitly. | — |
| Currency, rounding and tax per country | ➖ | UK only, GBP only, one VAT rate | Only if you sell outside the UK. | P3 |
| Idempotency keys on payment | ✅ | Payment save locks the invoice; Xero calls send an Idempotency-Key | Card payments (Stripe) will need their own keys. | P2 |
| Deduplication for four clicks on submit | ✅ | Submit buttons disable on click (`templates/base.html:756`); invoice creation locks the owner and refuses overlaps | None. | — |
| Pagination on page 400 | ✅ | Django offset paging on all big lists | Fine at single-yard size. Use keyset paging if a list grows past about 100,000 rows. | — |
| Exports to a spreadsheet | 🟡 | Invoice CSV and PDF only | Add CSV export for horses, owners, charges and health records. This is also the GDPR access export. | P1 |
| Imports that survive an old CSV | 🟡 | `data/import_csv.py` handles `utf-8-sig` then `cp1252`. `load_csv_data.py` tries latin-1 before cp1252, so cp1252 never runs. No dry run. | Fix encoding order. Add `--dry-run`. Delete the duplicate root `import_data.py`. | P1 |
| Retention rules, and code that deletes | ❌ | Only backup retention exists | Write a retention schedule (for example: invoices 6 years for HMRC; archived owners' contact details deleted after that). Build a scheduled job that applies it. | P1 |
| Caching, and clearing it when data changes | 🟡 | Only the business name is cached, in per-process memory; the clear reaches one process | Harmless now. Use Redis as `CACHES` backend before caching more. | P2 |

### 3. Security

| Requirement | Status | Evidence | Action | Phase |
|---|---|---|---|---|
| Secrets out of the code and repo | ✅ | `SECRET_KEY` has no default; gitleaks on full history in CI | Turn on GitHub secret scanning and push protection (Go-live 1.1). | ⚙️ P1 |
| Secrets out of AI chat history | ⚙️ | Cannot check from code | Treat any secret ever pasted into a chat as burnt. Rotate it. | P1 |
| Rotate every key that was exposed | ⚙️ | Planned in Go-live phase 4D | Do 4D. | P1 |
| Every package exists and is the right one | 🟡 | `requirements.txt` uses `~=` ranges, no lockfile, no hashes | Add `uv lock` or `pip-compile --generate-hashes`. Review each top-level package name once. | P1 |
| Dependency scanning | ✅ | pip-audit, npm audit, Dependabot, weekly run (`.github/workflows/security.yml`) | Pin GitHub Actions by commit SHA. | P1 |
| Input sanitisation | ✅ | Django forms everywhere; CSV formula-injection guard (`invoicing/utils.py:161`) | None. | — |
| SQL injection | ✅ | No `.raw()` or `.extra()`; all `cursor.execute` calls static or parameterised | None. | — |
| XSS | 🟡 | Autoescape on; one `\|safe` with a fixed string only. CSP is report-only with `unsafe-inline` and `unsafe-eval`. | Move to CSP nonces and Alpine's CSP build, then set `CSP_ENFORCE=True`. | P2 |
| CSRF | 🟡 | One exempt view (the analytics proxy, no app state). No global HTMX header. | P0 #4. | P0 |
| Rest of the OWASP list | 🟡 | No rate limit outside login (`SECURITY.md` gap) | Rate-limit password reset, PDF export and reports. Or put Cloudflare WAF in front. | P1 |
| File uploads scanned, limited, stored safely | 🟡 | 5 MB limit; extension allow-list; images re-encoded by Pillow; private bucket with signed links. Documents (PDF, Word) are checked by extension only. No virus scan. | Check file type by content (magic bytes). Serve documents with `Content-Disposition: attachment`. Add a virus scan (ClamAV in the worker, or the storage provider's scanner) before SaaS. | P1 / P2 |
| CORS not "allow everything" | ➖ | No CORS headers at all; same-origin app | None. | — |
| Security headers on every response | 🟡 | HSTS, nosniff, X-Frame-Options, Referrer-Policy present. No `Permissions-Policy`. CSP report-only. | Add `Permissions-Policy` (camera, microphone, geolocation limited to self, as maps use location). Enforce CSP in P2. | P1 |
| Encryption in transit | 🟡 | HTTPS and HSTS. Database connection does not require TLS. | Add `sslmode=require` to the database options. | P1 |
| Encryption at rest | 🟡 | Xero tokens are Fernet-encrypted. Backups and media rely on provider defaults. | Confirm R2 and Supabase encrypt at rest (they do by default) and write it down. Encrypt backup files before upload. | P1 |
| Least privilege, including AI agents | 🟡 | R2 tokens scoped to one bucket (Go-live 2.1). Supabase uses the `postgres` superuser. | Create a separate database user for the app without superuser rights. Never give an AI agent production credentials; give it staging only. | P1 |
| Logs without passwords, tokens or card numbers | ✅ | Sentry scrubbing (`core/monitoring.py`); no card data held | Stop logging Xero error bodies in full (`xero_integration/client.py:182`) and stop showing them to users (`views.py:104`). | P1 |
| Penetration test | ❌ | None | Two reviewers are planned (`REVIEWER_PACK.md`). Book a paid pen test before the first enterprise or multi-yard sale. | P2 |
| Security questionnaire answers with evidence | 🟡 | `SECURITY.md` and `REVIEWER_PACK.md` cover most of it | Keep a standard answers file that links to the evidence. | P2 |
| Disclosure process | 🟡 | `SECURITY.md` says "email the maintainer" with no address | Add a real address (`security@<domain>`), a response time, and `/.well-known/security.txt`. | P1 |
| Plan for a stolen laptop | ❌ | None | Write a one-page runbook: which keys to rotate, in what order, and how to revoke GitHub, Railway, Supabase, Xero and R2 sessions. | P1 |

### 4. Compliance

| Requirement | Status | Evidence | Action | Phase |
|---|---|---|---|---|
| Audit trail of who changed a record | 🟡 | Only `uploaded_by` on documents and photos; Django `LogEntry` covers admin edits only | Add `django-simple-history` (or `django-auditlog`) on invoices, payments, owners, users and roles. Show history on each record page. | P1 |
| Audit trail of who viewed a record | ❌ | None | Log views of passports, insurance documents and owner contact details. | P2 |
| GDPR deletion reaches every table, backup and log | ❌ | No erasure code. Owner delete is blocked once history exists. | Build "Anonymise owner": keep invoices (legal duty), blank name, email, phone and address, delete documents. Write down that backups expire within 12 months under the GFS retention. | P1 |
| GDPR access request (export one person's data) | ❌ | None | Export everything about one owner as a ZIP (CSV plus documents). | P1 |
| Terms and privacy policy that match the code | ❌ | `website/src/content/pages/privacy.md`, `terms.md`, `cookies.md` are placeholders. PostHog, Sentry, Xero, Supabase, Cloudflare R2 and Railway are not named. | Give a solicitor the list of sub-processors and what data each one gets. Publish the result. | P1 |
| Data residency (UK / EU only) | 🟡 | Depends on Supabase, Railway, R2, Sentry and PostHog regions | Check each region and pick EU or UK where possible. Write it in the privacy policy. | P1 |
| Quarterly access reviews | ❌ | None | Calendar task: list users and roles in the app, GitHub, Railway, Supabase, R2; record sign-off. | P1 |
| SOC 2 | ❌ | None | Not needed for UK livery yards. Revisit only for an enterprise buyer. | P3 |
| HIPAA | ➖ | Horse health records only; no human patient data | None. | — |
| BAA with every vendor | ➖ | HIPAA only | None. | — |
| PCI scope, card numbers kept out of the database | ➖ | No card handling today. A static "pay by card" link only. | When Stripe is added, use Stripe Checkout so card data never touches Yardway (SAQ A). | P2 |
| AI provider BAA | ➖ | No AI in the app | None. | — |

### 5. Infrastructure and deploys

| Requirement | Status | Evidence | Action | Phase |
|---|---|---|---|---|
| Dev, staging and production that match | ❌ | No staging. Tests run on SQLite; production is Postgres behind pgbouncer. | Create a Railway staging environment with its own Supabase project and made-up data. Add a Postgres service to CI. | P1 |
| Infrastructure as code | 🟡 | Service config in `railway/*.json` and `railpack*.json`. Project, Redis, variables and buckets made by hand (`docs/CUTOVER.md`). | Write down every variable in one table (names only). Terraform only if the setup grows. | P1 |
| Deploy pipeline that runs tests first | 🟡 | CI runs tests, but Railway deploys straight from GitHub | Turn on branch protection on `main` (required checks) and Railway "Wait for CI". | ⚙️ P1 |
| One-click rollback | 🟡 | Railway can redeploy an earlier build. No runbook. | Write a rollback runbook, including what to do when a migration has run. | P1 |
| Zero-downtime deploys | 🟡 | Railway swaps after `/_health/` passes. Migrations run before the swap with no compatibility rule. | Rule: each migration must work with the old and the new code (expand, then contract). Add it to the PR template. | P1 |
| Feature flags | 🟡 | Environment booleans only (`LOCATION_MAPS_ENABLED`, `CSP_ENFORCE`). `core/features.py` is role permissions, not flags. | Enough for one yard. Add per-yard flags with multi-tenancy. | P2 |
| Autoscaling | ⚙️ | Railway setting | Not needed for one yard. | P2 |
| CDN | 🟡 | WhiteNoise with compression and long cache | Put Cloudflare in front (Go-live 5.1). | ⚙️ P1 |
| DNS, SSL and renewals | ⚙️ | Not done yet (Go-live phase 5) | Railway renews certificates. Set the domain to auto-renew and add a calendar reminder 60 days before expiry. | P1 |
| Get down to one host | ⚙️ | App runs on both Vercel and Railway | Go-live phase 4, then delete Vercel code (`vercel.json`, `build_files.sh`, migrate-on-start in `wsgi.py`, host branches in `settings.py`). | P1 |
| Storage rules for old files | ❌ | No lifecycle rules on buckets | Add an R2 lifecycle rule for temporary files. Backups already prune with GFS. | P2 |
| Cost alerts | ❌ | None | Set spend alerts in Railway, Supabase, Cloudflare, Sentry and PostHog. | ⚙️ P1 |
| Disaster recovery plan, tested yearly | ❌ | Backup runbook exists; no full DR plan | Extend `BACKUP_RESTORE.md` with "Railway is gone" and "Supabase is gone" scenarios, with target recovery times. Test once a year. | P1 |
| Second region | ➖ | No uptime promise in any contract | Only if a contract promises more uptime than one region gives. | P3 |

### 6. Background jobs, email and cache

| Requirement | Status | Evidence | Action | Phase |
|---|---|---|---|---|
| Background job queue | ✅ | Celery + Redis + beat | None. | — |
| Retries with backoff | 🟡 | No `autoretry_for`, `retry_backoff` or `acks_late` on any task | Add retries with backoff to email and Xero tasks. Set `task_acks_late=True` and `task_reject_on_worker_lost=True`. | P1 |
| Dead-letter queue | ❌ | None | Record tasks that fail every retry in a table, and show them on an admin page. | P1 |
| Cron jobs that do not run twice | 🟡 | Most tasks claim rows with a conditional update. Document expiry reminders do not check the update count (`notifications/tasks.py:410`). Nothing stops two beat processes. | Fix the expiry claim. Add a Redis lock to beat (or to each scheduled task). | P1 |
| SPF, DKIM and DMARC | ⚙️ | Planned (Go-live 5.2) | Do 5.2. | P1 |
| Bounce and complaint handling | ❌ | None | Use the Postmark or Resend webhook to mark an owner email as bouncing and show it on the owner page. | P1 |
| Emails that render in every client | 🟡 | 8 HTML templates; no plain-text part (`notifications/emails.py:41`) | Send `EmailMultiAlternatives` with a plain-text version. Test in Gmail, Outlook and Apple Mail. | P1 |
| Notifications people can turn off | ❌ | No preferences, no unsubscribe | Per-owner switches for reminder emails (invoices stay on). Add `List-Unsubscribe` header. Per-staff switch for digests. | P1 |
| Caching | 🟡 | See section 2 | — | P2 |
| Health check covers the whole system | 🟡 | `/_health/` checks the database only | Add Redis. Add a beat heartbeat (see monitoring). | P1 |
| Reminder at the right time | ✅ | 07:00 London time, Monday to Friday, follows summer time (`settings.py:528`) | None. | — |

### 7. Monitoring and incidents

| Requirement | Status | Evidence | Action | Phase |
|---|---|---|---|---|
| Structured logs you can search | 🟡 | Plain text console logs | Add a JSON formatter. Railway log search then works on fields. | P1 |
| Error tracking that groups bugs | 🟡 | Sentry is built in, off until `SENTRY_DSN` is set | Go-live 1.3. | ⚙️ P1 |
| Uptime checks from outside | ❌ | None | Free external monitor (for example Better Stack or UptimeRobot) on `/_health/`. | ⚙️ P1 |
| Heartbeat for scheduled jobs | ❌ | Backup failures are emailed; nothing tells you if beat stops | Each scheduled task pings a heartbeat URL (Sentry Crons or the uptime tool). | P1 |
| Alerts that page a human | ❌ | None | Route uptime and Sentry alerts to a phone. Tune after two weeks. | P1 |
| Dashboards for latency, errors, throughput | ❌ | None | Sentry Performance at a low sample rate, or Railway metrics. | P2 |
| Tracing across services | ❌ | None | Sentry tracing links web and worker. Low priority with two services. | P2 |
| On-call rotation | ❌ | One person | Write who is on call and who is backup. See "People" below. | P1 |
| Runbooks | 🟡 | Backup, cutover and go-live runbooks exist | Add: site down, emails not sending, Xero disconnected, worker stopped, stolen laptop, rollback. | P1 |
| Postmortems | ❌ | None | One-page template in `docs/`. Each one ends with a tracked action. | P1 |
| Status page | ❌ | None | Needed when you have more than one customer. | P2 |

### 8. Testing

| Requirement | Status | Evidence | Action | Phase |
|---|---|---|---|---|
| Unit tests from someone who knows the logic | ✅ | About 1,490 Django tests, 69 JavaScript tests, in CI | Ask the yard owner to review the invoicing test cases against real invoices. | P1 |
| Integration tests for third-party APIs | 🟡 | Xero mocked at client level; email via test outbox | Add tests for the OAuth and refresh flow. Run a contract test against the Xero demo company before each release. | P1 |
| End-to-end tests for money flows | 🟡 | Playwright scripts in `tools/qa/` are one-off, not in CI | Turn three of them into CI tests: sign in, create and send an invoice, record a payment. | P1 |
| Load testing | ❌ | None | One Locust run against staging with SaaS-sized data before the multi-yard launch. | P2 |
| Test data like production, but not production | 🟡 | `tools/qa/seed_qa.py` is a shell script with hard-coded passwords | Make a `seed_demo` management command with made-up data. Use it for staging and reviewers. | P1 |
| Regression tests | 🟡 | Many tests cite bug numbers in `docs/BUGFIXES.md` | Rule in the PR template: every bug fix adds a test. | P1 |
| Postgres in tests | ❌ | SQLite only; `select_for_update` and pgbouncer settings never run in CI | Add a Postgres job to CI. | P1 |
| Testing on an old laptop / slow device | ❌ | Mobile QA done once (`MOBILE_QA_REPORT.md`) | Lighthouse run with CPU and network throttling, recorded each release. | P2 |
| Accessibility compliance | 🟡 | Many labels and ARIA attributes. No skip link, no audit, no automated checks. | Add a skip link. Add axe checks to the E2E tests. Do one keyboard-only and one screen-reader pass. | P1 |

### 9. Billing and payments

| Requirement | Status | Evidence | Action | Phase |
|---|---|---|---|---|
| PO number on invoices | ❌ | No field; PDF and Xero carry only the owner's account code | Add `customer_reference` to `Invoice` (and a default on `Owner`). Print it on the PDF; send it as Xero `Reference`. | P1 |
| Sales tax and VAT | ✅ | VAT rate copied onto each invoice; VAT number on PDF | Limits: one rate per invoice; Xero only 0% or 20%. Enough for UK livery. Hide "VAT Reg: N/A" when not registered. | P1 |
| Refunds, credits and partial refunds | 🟡 | Partial payments work. No credit notes or refunds. Overpayment is refused. | Add credit notes linked to an invoice, synced to Xero. | P1 |
| Failed-payment retries and dunning | 🟡 | One overdue reminder, every 7 days, with no end | Stepped reminders (7, 14, 30 days, then final notice) and a per-owner pause. | P1 |
| Invoice numbers safe and gap-free | 🟡 | Unique and race-safe. A deleted draft leaves a gap. | Give a number only when a draft is sent, or keep cancelled numbers instead of deleting. HMRC needs a unique sequence; gaps must be explainable. | P1 |
| Revenue reports finance can reconcile | 🟡 | Chart bug (P0 #2). No reconciliation with Xero. | Fix P0 #2. Add a monthly report: invoiced, paid, outstanding, with VAT split, that matches Xero. | P1 |
| Subscriptions, upgrades, downgrades, proration | ❌ | No SaaS billing exists | Needed to charge yards. Use Stripe Billing; do not build proration by hand. | P2 |
| Usage metering that matches the bill | ❌ | Plan prices by horse count | Count active horses per yard each month and store the count with the invoice. | P2 |
| Annual contracts with custom terms | ➖ | No SaaS contracts yet | Handle in Stripe with custom prices. | P2 |
| Idempotent card payments | ➖ | No card payments yet | See payments plan. | P2 |
| Money as exact numbers | ✅ | See section 2 | — | — |

### 10. API and integrations

| Requirement | Status | Evidence | Action | Phase |
|---|---|---|---|---|
| Webhooks that verify signatures | ➖ | No inbound webhooks today | Stripe and email-provider webhooks must verify signatures when built. | P1 / P2 |
| Webhooks that retry, with a log | ➖ | No outbound webhooks | None. | — |
| Versioned API | ➖ | No public API | None. | — |
| API keys customers create and revoke | ➖ | No public API | None. | — |
| API rate limits | ➖ | No public API | None. | — |
| Plan for a third-party API that changes | ✅ | Xero client uses safe defaults on missing fields, keeps the connection on errors | Add a Sentry alert when a Xero response is missing expected fields. | P2 |
| OAuth token refresh | ✅ | Locked refresh, 401 retry, encrypted tokens (`xero_integration/client.py:117`) | Add tests (section 8). | P1 |
| Sandbox for integrations | ❌ | — | Staging connected to the Xero demo company serves this. | P1 |

### 11. AI features

Yardway has no AI or LLM features. A search for `anthropic`, `openai`,
`langchain` and `gemini` found nothing. These items are ➖ N/A:
prompt injection, data kept out of prompts, model spend cap, evals,
fallback provider, model deprecation plan, prompt logs, and human approval
for AI actions.

**One rule still applies:** an AI coding agent must not hold production
credentials (see "Least privilege" in section 3).

### 12. User experience

| Requirement | Status | Evidence | Action | Phase |
|---|---|---|---|---|
| Pages that load on hotel wifi | 🟡 | Scripts local and deferred; `styles.css` is 204 KB; about 15 script files on every page | Check why the CSS is large (unused Tailwind classes). Bundle the scripts. | P2 |
| Empty, loading and error states | 🟡 | Empty states widely used; progress bar and skeletons. A failed page load shows nothing. | Add a global HTMX error handler that shows a message with a "Try again" button. | P1 |
| Error messages a customer can act on | ✅ | Form errors say what to do; custom 403, 404, 500 and lockout pages | None. | — |
| Forms keep what people typed | 🟡 | Server forms re-render with input. Bulk delete form is built in JavaScript. | Check the JS-built forms. | P2 |
| Emails render in every client | 🟡 | See section 6 | — | P1 |
| Notifications people can turn off | ❌ | See section 6 | — | P1 |
| Undo | 🟡 | Archive and restore for owners and locations. Other deletes are permanent. | Comes with soft deletes (section 2). Add an "Undo" link in the success message. | P1 |
| Keyboard and screen reader support | 🟡 | See section 8 | — | P1 |
| Translations and locale formats | ➖ | UK English only; dates fixed to `dd/mm/yyyy`; £ fixed | Only if you sell outside the UK. | P3 |
| Back button does what people expect | 🟡 | HTMX navigation; not tested | Check that HTMX swaps push URLs. Add one E2E test for back. | P2 |
| Admin panel for ops | 🟡 | 26 models in Django admin. `Payment`, `Role` and documents are missing. | Register the missing models (read-only where possible). | P1 |
| Support tooling | ❌ | No impersonation, no audit view | Comes with the audit log (P1) and impersonation (P2). | P2 |

### 13. Code, docs and team

| Requirement | Status | Evidence | Action | Phase |
|---|---|---|---|---|
| Code a human can read | 🟡 | Clean style, ruff in CI | See the large files below. | P2 |
| Code review on every change | ⚙️ | Not enforced in the repo | Branch protection: one approving review required on `main`. | P1 |
| Branching strategy | ❌ | Not written down | Add `CONTRIBUTING.md`: short-lived branches, PR to `main`, squash merge. | P1 |
| PR template and CODEOWNERS | ❌ | None in `.github/` | Add both. The template asks: tests added? migration safe both ways? `SECURITY.md` still true? | P1 |
| Monthly dependency upgrades | 🟡 | Dependabot on; nothing makes sure its PRs merge | Calendar task: merge Dependabot PRs each month. | P1 |
| Framework upgrades before support ends | 🟡 | Django 5.2 is LTS (security fixes to April 2028) | Plan the move to Django 6.2 LTS for 2027. | P3 |
| Delete dead code | 🟡 | Root `import_data.py` (513 lines, duplicates `data/import_csv.py`); `load_csv_data.py` (one-off); Vercel code; keep-Supabase-awake ping | Delete after Go-live phase 4. | P1 |
| Split the very large files | 🟡 | `health/views.py` 1,830 lines; `core/views/horses.py` 1,802; `core/models.py` 1,580; `core/views/locations.py` 1,241; `core/forms.py` 1,050; `health/models.py` 1,009 | Split by sub-area, one file per PR, no behaviour change. | P2 |
| Architecture decisions written down | ❌ | No ADRs | Start `docs/adr/`. First records: single-tenant, Decimal money, Celery claim pattern, Xero as push target. | P1 |
| README that gets a new engineer running in an hour | 🟡 | Steps exist; Windows first; no demo data | Add Linux/Mac first, Docker Compose for Postgres and Redis, and `seed_demo`. Time a new person on it. | P1 |
| Documentation for whoever takes over | 🟡 | Good ops docs; no single handover guide | One `docs/HANDOVER.md` that links everything and lists every account. | P1 |
| Changelog customers can read | ❌ | None | `CHANGELOG.md`, plain-language entries per release. | P1 |
| Every account, domain and repo in the company's name | ⚙️ | Repo is under a personal GitHub account | Move the repo to a GitHub organisation owned by the company. Same for Railway, Supabase, Cloudflare, Xero app, Sentry, PostHog, domain registrar. | P1 |
| Bus factor higher than one | ❌ | One maintainer | See "People" below. | P1 |

---

## Implementation plan

Each phase leaves the app working. Code tasks are sized for one PR each
unless stated. "Dashboard" tasks need no code.

### P0 — This week (about 1 day)

Code (one PR):

1. Remove `restore_test --reset` from `railway/worker.json`.
2. Fix the revenue chart to include overdue invoices; add a test.
3. Global HTMX CSRF header in `base.html`.
4. `PASSWORD_RESET_TIMEOUT = 3600`.
5. `timezone.localdate()` in the three places in P0 #7.
6. Correct the upload limit in `SECURITY.md`.
7. Test for sign-in with a `+` address.

Dashboard:

8. Set `ADMIN_URL`.

### P1 — Before the first paying yard (about 6–8 weeks for one developer)

**Dashboard first (the existing go-live checklist, about 1 day plus DNS wait):**

1. Work through [`GO_LIVE_CHECKLIST.md`](GO_LIVE_CHECKLIST.md) phases 1–5: secret scanning, encryption key, Sentry, backups, **restore test**, one host, secret rotation, domain, SPF/DKIM/DMARC.
2. Turn on Supabase point-in-time recovery.
3. Branch protection on `main` with required checks and one review; Railway "Wait for CI".
4. External uptime monitor and phone alerts.
5. Cost alerts on every provider.
6. Staging environment on Railway with its own database.
7. Move the repo and every account into the company's name.
8. Check data regions for every provider.

**Code, in this order (most risk removed first):**

| # | Work package | Covers | Size |
|---|---|---|---|
| 1 | ✅ **Account security** (done): MFA with recovery codes (required for administrators), self-unlock by password reset and an admin unlock button, rate limit on password reset, absolute session lifetime, "sign out everywhere", invite by email | MFA, lockouts, sessions, rate limits | 1.5 weeks |
| 2 | **Audit log**: `django-simple-history` on invoices, payments, owners, users, roles; history panel on record pages | Audit trail (changes), support | 1 week |
| 3 | **Soft deletes**: void payments with a reason; archive charges and health records; "Undo" in success messages | Soft deletes, undo | 1 week |
| 4 | **GDPR**: anonymise owner, export one owner's data, retention job, CSV exports for main lists | GDPR, retention, exports | 1 week |
| 5 | **Billing fixes**: PO / customer reference, credit notes, stepped dunning, gap-free numbering, reconciliation report | Billing | 1.5 weeks |
| 6 | **Email**: plain-text parts, `List-Unsubscribe`, owner and staff preferences, bounce webhook with signature check | Email, notifications | 1 week |
| 7 | **Jobs**: retries with backoff, `acks_late`, failed-task table, beat lock, fix expiry claim, heartbeats, health check covers Redis | Jobs, cron, monitoring | 3 days |
| 8 | **Hardening**: upload content check and `attachment` header, `Permissions-Policy`, DB `sslmode=require`, separate DB user, encrypted backups, Python lockfile, Actions pinned by SHA, trim Xero error logs, `security.txt` | Security | 3 days |
| 9 | **Unicode and data quality**: PDF font, safe file names, CSV BOM, import encoding fix and `--dry-run`, DB check constraints | Unicode, imports, validation | 3 days |
| 10 | **Tests and CI**: Postgres job in CI, three E2E tests in CI with axe checks, Xero OAuth tests, `seed_demo` command | Testing, accessibility | 1 week |
| 11 | **Cleanup and docs**: delete Vercel and import dead code, skip link, missing admin models, JSON logs, global HTMX error message, `CONTRIBUTING.md`, PR template, CODEOWNERS, `CHANGELOG.md`, first ADRs, README update, `HANDOVER.md`, runbooks (site down, email, Xero, worker, rollback, stolen laptop, DR), postmortem template | Docs, team, ops | 1 week |

**Non-code, in parallel:**

- Solicitor writes terms, privacy and cookie pages from the sub-processor list.
- Yard owner reviews invoicing test cases against real invoices.
- Two external reviewers use `REVIEWER_PACK.md`.
- Start the quarterly access review and monthly Dependabot calendar tasks.

### P2 — Before selling to more than one yard (about 4–6 months)

This is the multi-tenancy rebuild that the market document estimates.

1. Organisation model; every query scoped by organisation; tests that try to read across tenants.
2. Per-object access checks, ready for the owner portal.
3. Support impersonation with an audit record on every use.
4. View logging for sensitive records.
5. Stripe Billing for yard subscriptions (horse-count tiers, metering).
6. Stripe Checkout for owner payments (payments plan phases 1–3), with signed webhooks and idempotency keys.
7. CSP enforced with nonces; virus scan on uploads.
8. Status page, dashboards, tracing.
9. Load test on staging with SaaS-sized data.
10. Paid penetration test and a standard security questionnaire answers file.
11. Schema cleanup: `Site` model, one ownership model.
12. Split the largest files.

### P3 — Only when a buyer requires it

SSO, SAML, SCIM, SOC 2, a second region, multi-currency and foreign tax,
translations, public API with keys and versioning.

---

## People

The list ends with the roles a launched app needs. For Yardway today:

| Need | Now | Minimum for launch |
|---|---|---|
| Someone who reads every change before it ships | AI-assisted, one maintainer | One named reviewer (paid, part-time) with merge rights. Branch protection makes the review required. |
| On call when emails or invoicing stop | One person | Named primary and backup. Runbooks so the backup can act. |
| Security and compliance | None | The two planned external reviewers now; a paid pen test before P2. |
| Someone who remembers why | One person | ADRs and `HANDOVER.md` (P1 work package 11). |

---

## Decisions needed from you

1. **Single yard first, or multi-tenant first?** This plan assumes single yard first.
2. **Who is the backup on-call person and the code reviewer?**
3. **Supabase plan with point-in-time recovery?** It is a paid add-on.
4. **Credit notes and gap-free numbering:** confirm with the accountant how HMRC wants cancelled invoices handled.
5. **Retention periods:** confirm with the solicitor how long to keep owner data after an owner leaves.
