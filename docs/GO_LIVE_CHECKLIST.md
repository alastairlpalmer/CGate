# Go-live checklist

The work that is left is all in dashboards, not in code. This is the order
to do it in, and why that order.

Tick each box as you go. You can stop after any phase and come back — each
one leaves the app in a working state.

| Phase | What | Time |
|---|---|---|
| 1 | Quick wins | 20 min |
| 2 | Turn backups on | 30 min |
| 3 | **Prove the restore works** | 1 hour |
| 4 | Get down to one host | 1–2 hours |
| 5 | Real domain and email | 1 hour + DNS wait |

**Why this order.** Phase 3 comes before phase 4 on purpose. Phase 4 is the
riskiest thing on the list, and you want a backup you have actually
restored *before* you touch hosting, not after.

---

## Phase 1 — Quick wins (20 min)

### 1.1 Turn on GitHub secret scanning

- [ ] Go to the repository → **Settings** → **Code security**
- [ ] Turn on **Secret scanning**
- [ ] Turn on **Push protection** (this blocks a secret before it is committed)

Two toggles. Free on public repos and on GitHub Advanced Security plans.

### 1.2 Set the encryption key

Right now the key that encrypts your Xero tokens is derived from
`SECRET_KEY`. That works, but it means rotating `SECRET_KEY` in phase 4
would make the stored token unreadable. Set a dedicated key first.

- [ ] Generate a key:

  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

- [ ] Save it in your password manager, **next to your backups**. A restored
      database is unreadable without it.
- [ ] Set `FIELD_ENCRYPTION_KEYS` to that value on **every** service
      (`web`, `worker`, `beat`)
- [ ] Redeploy

> **Expect Xero to disconnect.** The stored token was encrypted with the old
> derived key, so after this change it cannot be read. The app handles that
> by showing "not connected" rather than erroring.
>
> - [ ] Go to **Settings → Integrations** and reconnect Xero (2 minutes)
>
> This happens once. Do it now, deliberately, rather than by accident during
> phase 4.

### 1.3 Turn on Sentry

- [ ] Create a free account at sentry.io
- [ ] Create a project, platform **Django**
- [ ] Copy the DSN (looks like `https://abc123@o12345.ingest.sentry.io/678`)
- [ ] Set `SENTRY_DSN` on **web and worker** (the worker is where the
      invoicing and reminders run — that is the half nobody watches)
- [ ] Leave `SENTRY_TRACES_SAMPLE_RATE` unset. The default `0.0` is right;
      tracing every request burns the free tier in days.
- [ ] Redeploy

**Check it works.** Visit a URL that does not exist on your site, then look
in Sentry. Nothing after a few minutes means the DSN did not take.

---

## Phase 2 — Turn backups on (30 min)

### 2.1 Create the bucket

Use **Cloudflare R2**. Free tier is 10 GB, which is far more than a yard
needs, and it is a different company from Supabase — that is the point.

- [ ] Sign in to Cloudflare → **R2** → **Create bucket**
- [ ] Name it `yardway-backups`
- [ ] Note your **Account ID** (shown on the R2 overview page)
- [ ] **Manage R2 API Tokens** → **Create API token**
- [ ] Permission: **Object Read & Write**, scoped to that one bucket
- [ ] Copy the **Access Key ID** and **Secret Access Key** — the secret is
      shown once only

### 2.2 Set the variables

On the **worker** service:

```
BACKUP_ENABLED=True
BACKUP_S3_ENDPOINT=https://<your-account-id>.r2.cloudflarestorage.com
BACKUP_S3_BUCKET=yardway-backups
BACKUP_S3_ACCESS_KEY=<access key id>
BACKUP_S3_SECRET_KEY=<secret access key>
```

- [ ] Set those five
- [ ] **If your `DATABASE_URL` contains `pooler.supabase.com`**, also set:

  ```
  BACKUP_DATABASE_URL=<the DIRECT connection string>
  ```

  Get it from Supabase → **Connect** → **Direct connection** (port 5432,
  not 6543). `pg_dump` cannot work through a transaction-mode pooler, and
  the error it gives is not obvious.

- [ ] Redeploy

### 2.3 Run one by hand

Do not wait until 02:00 to find out whether it works.

- [ ] Open a shell on the Railway `web` service
- [ ] Run:

  ```bash
  python manage.py backup
  ```

- [ ] It should print the sizes and the object keys it wrote
- [ ] Check the bucket in Cloudflare — two files, under
      `backups/database/` and `backups/media/`

**If it fails**, the error says which of the three causes it is: no
`pg_dump` in the image, wrong credentials, or the pooler problem above.

### 2.4 Set a reminder to check it

- [ ] Django admin → **Core → Backup runs**
- [ ] Confirm there is a row with status **Success**
- [ ] Put a monthly reminder in your calendar: *"Check Yardway backup runs"*

That table exists because a backup job that stopped three weeks ago looks
exactly like one that is working.

---

## Phase 3 — Prove the restore works (1 hour)

**This is the most valuable hour on this list.** A backup nobody has
restored is not a backup, and it is the first thing your security reviewer
will ask about.

Full procedure with commands: [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md).

The short version:

- [ ] Download the newest `.dump` from the R2 bucket
- [ ] Create a scratch database (**not** production):

  ```bash
  createdb yardway_restore_test
  ```

- [ ] Restore into it:

  ```bash
  pg_restore --no-owner --no-privileges \
    --dbname "postgres://user:pass@host:5432/yardway_restore_test" \
    yardway-db-YYYY-MM-DDTHHMM.dump
  ```

- [ ] Run the app locally against it, with your **production**
      `SECRET_KEY` and `FIELD_ENCRYPTION_KEYS`
- [ ] Check all five:
  - [ ] Sign in with a real account
  - [ ] Horse list count matches production
  - [ ] Open an invoice and export the PDF
  - [ ] A horse photo loads (needs the media archive restored too)
  - [ ] Settings → Integrations shows Xero as connected
- [ ] Write the date and how long it took in the log table at the bottom of
      `BACKUP_RESTORE.md`, and commit that
- [ ] Drop the scratch database

The Xero check is the one that catches a wrong encryption key. If it shows
disconnected, your keys did not match — find out now, not during a real
emergency.

---

## Phase 4 — Get down to one host (1–2 hours)

You currently run the same app on Vercel and Railway. Vercel has no worker
process, so reminders and invoice automation do not run there.

**First answer this:** is Railway already serving your real traffic, or is
Vercel still the live site?

### 4A — If Railway is not live yet

Follow [`CUTOVER.md`](CUTOVER.md). It is a complete runbook: services,
variables, volume, smoke tests, DNS swap and rollback. Do not improvise
around it.

Its advice still stands: **leave Vercel alone until DNS has moved**, and
decommission only after Railway has been stable for about a week including
one full weekday reminder cycle.

Come back here for 4C when that week is up.

### 4B — If Railway is already live

- [ ] Confirm Railway is genuinely serving: check that a reminder email
      actually went out this week
- [ ] Confirm `worker` and `beat` are both running

Then go to 4C.

### 4C — Decommission Vercel

- [ ] Check no preview URL points at your production database. If one does,
      that is a live exposure — do this step today.
- [ ] **Delete** the Vercel project. Not pause — a paused project keeps its
      environment variables.

### 4D — Rotate the secrets

Anything that lived in two dashboards has twice the exposure history. You
cannot prove the old value is gone, so treat it as burnt.

Do these **one at a time**, checking the app after each:

- [ ] **Database password** — rotate in Supabase, update `DATABASE_URL`
      (and `BACKUP_DATABASE_URL`)
- [ ] **Xero client secret** — regenerate in the Xero developer portal,
      update `XERO_CLIENT_SECRET`, then reconnect Xero
- [ ] **SMTP password** — revoke the Gmail app password. Better: move to
      Resend or Postmark (see phase 5) and use an API key instead of a
      password to your personal mailbox.
- [ ] **`SECRET_KEY`** — generate a new one. **Do this last, at a quiet
      hour**: it signs everyone out and invalidates pending password-reset
      links.

  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(50))"
  ```

> `FIELD_ENCRYPTION_KEYS` was set in phase 1.2, so rotating `SECRET_KEY`
> here does **not** affect the Xero token. That was the whole reason for
> doing 1.2 first.

### 4E — Delete the dead code

Once Vercel is gone, ask for this as a PR — roughly 40 lines of
host-detection branching in `settings.py`, the `migrate`-on-cold-start block
in `wsgi.py`, and `vercel.json` all become dead. Dead conditional logic
around security settings is exactly what a reviewer flags.

---

## Phase 5 — Real domain and email (1 hour + DNS wait)

### 5.1 The domain

- [ ] Buy the domain
- [ ] Add it to Railway: `web` → **Settings → Networking → Custom Domain**
- [ ] Point the DNS record at the CNAME target Railway gives you
- [ ] Set `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` to that domain only —
      no wildcards
- [ ] Update `XERO_REDIRECT_URI` on Railway **and** in the Xero developer
      portal
- [ ] Optional: put Cloudflare in front (free) for DDoS protection and a WAF

### 5.2 Email that arrives

You send invoices. Gmail SMTP with an app password will land them in spam,
and anyone can spoof your address.

- [ ] Sign up for **Resend** or **Postmark**
- [ ] Verify your domain there
- [ ] Add the three DNS records they give you:
  - [ ] **SPF** — says which servers may send as you
  - [ ] **DKIM** — signs your mail so it cannot be forged
  - [ ] **DMARC** — tells receivers what to do with mail that fails
- [ ] Update `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` and
      `DEFAULT_FROM_EMAIL` on **web and worker**
- [ ] Send yourself a test invoice and confirm it lands in the inbox, not
      spam

---

## When you are done

Give your two reviewers:

- Read-only access to the GitHub repository
- A staging login with made-up data — **not production**
- [`REVIEWER_PACK.md`](REVIEWER_PACK.md), which has the architecture, the
  data map, the threat list and the five questions worth their time
- [`SECURITY.md`](../SECURITY.md), which lists the gaps you already know
  about

A known gap earns respect. A surprise does not.
