# Railway Cutover Runbook

Migrating CGate from Vercel serverless to Railway. Vercel stays live and
untouched until the final DNS step — everything here can be done and verified
in parallel with production.

## Target architecture

One Railway project, four services, all built from this repo:

| Service  | What it runs | Config file (set in dashboard) |
|----------|--------------|--------------------------------|
| `web`    | gunicorn + WhiteNoise, migrate on deploy | `/horse_management/railway/web.json` |
| `worker` | `celery -A horse_management worker` | `/horse_management/railway/worker.json` |
| `beat`   | `celery -A horse_management beat` (django-celery-beat DB scheduler) | `/horse_management/railway/beat.json` |
| `Redis`  | Railway Redis plugin — Celery broker | n/a |

Plus a **volume** mounted on `web` at `/data` for uploaded media (horse
photos, business logo, receipts). The Celery tasks never read media files, so
only `web` needs the volume (Railway volumes attach to a single service).

The build is defined in `horse_management/railpack.json`: Python 3.11 +
Node 22, `pip install -r requirements.txt`, `npm run build:css` (Tailwind),
`collectstatic`. Migrations run in the web service's **pre-deploy command**
(`horse_management/railway/web.json`), *not* at import time — the `call_command('migrate')`
hack in the project-level `wsgi.py` is now gated behind the `VERCEL` env var
and never fires on Railway.

## 1. Create the project and services

1. New Railway project → **Deploy from GitHub repo** → `alastairlpalmer/CGate`.
2. Add a **Redis** database to the project (right-click canvas → Database → Redis).
3. Create three services from the same repo (`web`, `worker`, `beat`).
   For **each** of the three, in *Service → Settings*:
   - **Root Directory**: `horse_management`
   - **Config-as-code file path**: `/horse_management/railway/web.json` /
     `/horse_management/railway/worker.json` /
     `/horse_management/railway/beat.json` respectively.
     The path is written from the **repo root**, but the file itself must
     live **inside the root directory** — when a Root Directory is set,
     Railway only snapshots that directory, and a config file outside it
     fails initialization with `service config at '...' not found`.
   - `worker` and `beat` only: add the service variable
     `RAILPACK_CONFIG_FILE=railpack.worker.json`. This switches their build
     to a minimal Python-only plan (no Node/Tailwind, no `collectstatic`),
     so their builds need no other variables and finish faster. Without it
     they use the default `railpack.json`, whose `collectstatic` step
     **fails the image build if `SECRET_KEY` isn't set yet**.
4. `web` only: *Settings → Networking* → **Generate Domain** (you get
   `something.up.railway.app` for testing before the real domain moves).
5. `web` only: *Settings (or right-click service) → Attach Volume* →
   mount path **`/data`**.

If you'd rather paste start commands into the dashboard instead of using the
config files, they are:

```
# web (pre-deploy command)
python manage.py migrate --noinput
# web (start command)
gunicorn horse_management.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60 --access-logfile -
# worker
celery -A horse_management worker --loglevel=info --concurrency=2
# beat
celery -A horse_management beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## 2. Environment variables

Set these on **all three** services — not just `web`. The worker and beat
processes boot the same Django settings, so they need `SECRET_KEY`,
`DATABASE_URL` and `CELERY_BROKER_URL` at runtime even though they serve no
HTTP. Use a Railway *shared variable* / environment-level variable to avoid
triplication where possible:

| Variable | Value | Where it comes from |
|----------|-------|---------------------|
| `SECRET_KEY` | same value as Vercel | Vercel dashboard → Project → Settings → Environment Variables |
| `DEBUG` | `False` | — |
| `DATABASE_URL` | `postgresql://postgres.<ref>:<password>@...pooler.supabase.com:6543/postgres` | Supabase dashboard → Connect → **Transaction pooler** (port 6543). Same value Vercel uses. |
| `CELERY_BROKER_URL` | `${{Redis.REDIS_URL}}` | Railway reference variable pointing at the Redis service (name must match the Redis service's name) |
| `ALLOWED_HOSTS` | `your-domain.example` | your real domain; the Railway-generated domain and `healthcheck.railway.app` are added automatically via `RAILWAY_PUBLIC_DOMAIN` / `RAILWAY_ENVIRONMENT` |
| `CSRF_TRUSTED_ORIGINS` | `https://your-domain.example` | same |
| `XERO_CLIENT_ID` / `XERO_CLIENT_SECRET` / `XERO_REDIRECT_URI` | same as Vercel (redirect URI changes at cutover, see §5) | Vercel env / Xero developer portal |

`web` service additionally:

| Variable | Value | Notes |
|----------|-------|-------|
| `MEDIA_ROOT` | `/data/media` | inside the volume mount |
| `SERVE_MEDIA` | `True` | makes Django serve `/media/` with `DEBUG=False` (WhiteNoise only covers static) |

Optional knobs (sensible defaults baked in):

| Variable | Default | Meaning |
|----------|---------|---------|
| `CONN_MAX_AGE` | `600` | persistent DB connections (forced to `0` on Vercel automatically) |
| `CELERY_RESULT_BACKEND` | `django-db` | task results stored via django-celery-results |
| `REMINDER_HOUR` / `REMINDER_MINUTE` | `7` / `0` | reminder emails, Europe/London (tasks staggered +0/+5/+10/+15 min) |
| `REMINDER_DAYS_OF_WEEK` | `mon-fri` | crontab day spec; use `*` for every day |
| `INVOICE_STATUS_HOUR` | `6` | daily SENT→OVERDUE promotion (every day) |

**Do NOT set `VERCEL`** on Railway — that variable is what activates the
serverless-only behaviour (CONN_MAX_AGE=0, migrate-on-cold-start, celery apps
removed from INSTALLED_APPS).

### Email — important

Until you set the variables below, `EMAIL_BACKEND` defaults to the **console
backend**: the beat schedule will run, tasks will "succeed", and every
reminder email is printed to the worker's logs instead of being sent.
Nothing goes to a real inbox. When ready:

| Variable | Example |
|----------|---------|
| `EMAIL_BACKEND` | `django.core.mail.backends.smtp.EmailBackend` |
| `EMAIL_HOST` | `smtp.gmail.com` (default) |
| `EMAIL_PORT` | `587` (default) |
| `EMAIL_USE_TLS` | `True` (default) |
| `EMAIL_HOST_USER` | SMTP username |
| `EMAIL_HOST_PASSWORD` | SMTP / app password |
| `DEFAULT_FROM_EMAIL` | `yard@your-domain.example` |

Set these on the **worker** (it sends the reminder emails) and the **web**
service (invoice "send" button).

## 3. First deploy

1. Set all variables **before** the first build — the `web` build runs
   `collectstatic`, which needs `SECRET_KEY` (a missing key fails the image
   build with `Failed to build an image`). `worker`/`beat` builds are
   variable-free thanks to `RAILPACK_CONFIG_FILE=railpack.worker.json`, but
   still need their runtime variables before they can start.
2. Deploy `web` first. The pre-deploy command runs `migrate` against the
   production DB. Because `django_celery_beat` / `django_celery_results`
   were never installed on Vercel, this first run **creates their tables** —
   that's expected. (Known quirk: `migrate` only works against a DB with
   existing history, which production has. Don't point this at an empty DB.)
3. Deploy `worker` and `beat`.
4. Check logs:
   - `web`: gunicorn booted, healthcheck on `/_health/` passed (the deploy
     won't go live unless it does — it's wired in
     `horse_management/railway/web.json`).
   - `worker`: `celery@... ready.` and the 5 `notifications.tasks.*` tasks
     listed in the registered tasks banner.
   - `beat`: `DatabaseScheduler` startup, schedule entries written.
     Verify in Django admin → *Periodic tasks* that the 5 entries exist.
     Note: those 5 entries are re-synced from `settings.CELERY_BEAT_SCHEDULE`
     on every beat restart — change times via the env vars above, not by
     editing those rows in the admin.

## 4. Smoke tests (Railway domain, before DNS)

Run against `https://<generated>.up.railway.app`:

1. `curl https://<generated>.up.railway.app/_health/` → `{"status": "ok"}`
   (this performs a real `SELECT 1` against Supabase, so it proves DB
   connectivity through the pooler).
2. Log in, load the dashboard.
3. **Media write/read**: upload a horse photo (or business logo in
   Settings), confirm it renders. Then *redeploy the web service* and
   confirm the image still renders — proves it landed on the volume, not
   the ephemeral filesystem.
4. **Celery round-trip**: trigger a task and watch it execute in the worker
   logs, e.g. from a one-off shell on the worker service
   (`railway run` or the service's shell):
   `python manage.py shell -c "from notifications.tasks import check_invoice_status; print(check_invoice_status.delay().id)"`
   Then confirm the result row in admin → *Task results*.
5. Wait for (or temporarily move `REMINDER_HOUR`/`REMINDER_MINUTE` to a few
   minutes ahead) one beat tick and confirm the reminder tasks fire in the
   worker logs. With email unset they print to the log — see §2.
6. Watch Supabase dashboard → Database → connections: should be a small,
   stable number (pooler + CONN_MAX_AGE working).

## 5. Cutover (DNS / domain)

1. Railway `web` service → *Settings → Networking → Custom Domain* → add
   your domain. Railway shows the CNAME target.
2. **Lower the DNS TTL** on the domain ~an hour ahead (e.g. to 300s) so the
   swap — and any rollback — propagates fast.
3. Update the DNS record from Vercel's target to Railway's CNAME.
4. Update `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` on Railway if you haven't
   already (the custom domain must be present in both).
5. Xero: change `XERO_REDIRECT_URI` (env var on Railway **and** the redirect
   URI registered in the Xero developer portal) if it pointed at a
   `*.vercel.app` URL rather than the custom domain.
6. Re-run the §4 smoke tests against the real domain (especially a
   form POST — that exercises CSRF_TRUSTED_ORIGINS).

**Leave Vercel completely alone during all of this.** It keeps serving until
DNS moves, and keeps working after (on its `*.vercel.app` URL) as a fallback.

## 6. Rollback plan

DNS is the only thing that moved, so rollback is one step:

1. Point the DNS record back at Vercel (fast, thanks to the lowered TTL).
2. Nothing else to undo: the DB is shared and untouched (the only new
   migrations are the additive `django_celery_beat`/`django_celery_results`
   tables, which Vercel's settings simply ignore), and Vercel's config
   (`vercel.json`, env vars, migrate-on-cold-start) was not modified by this
   migration.
3. Optionally pause/scale-down the `beat` + `worker` services if you don't
   want reminder emails going out while you're back on Vercel.

> **Warning — uploads while on Vercel are lost.** Vercel's filesystem is
> ephemeral and nothing serves `/media/` there, so any horse photo,
> passport, or receipt uploaded while Vercel is serving will error or
> vanish on the next cold start. Treat a rollback window as read-only for
> uploads, and re-add any files uploaded during it once back on Railway
> (persistent `/data` volume).

Decommission Vercel only after Railway has been stable for a comfortable
period (suggest ≥1 week, including one full weekday-morning reminder cycle).

## Known repo quirks (unchanged, for reference)

- Run tests with
  `DJANGO_SETTINGS_MODULE=horse_management.test_settings python manage.py test core.tests`
  (a stray project-level `horse_management/__init__.py` breaks bare discovery).
- `migrate` against a completely **empty** DB fails (old migration-graph
  issue); production has history so incremental migrate is fine.
