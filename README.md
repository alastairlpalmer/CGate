# Yardway

A web application for managing horse livery operations including tracking horses by location, owner management, and automated invoicing.

## Features

- **Horse Management**: Track horses with details (age, color, sex, breeding, notes)
- **Location Management**: Manage multiple sites and fields
- **Owner Management**: Track owner contact information and their horses
- **Placement Tracking**: Record where each horse is located and at what rate
- **Invoicing**: Generate monthly invoices with PDF export
- **Health Tracking**: Vaccination schedules and farrier visit records
- **Extra Charges**: Bill for vet visits, farrier, feed, and other services
- **Email Notifications**: Automated reminders for vaccinations, farrier, and overdue invoices

## Technology Stack

- **Backend**: Django 5.x with Python 3.11+
- **Database**: SQLite (development) / PostgreSQL (production)
- **Frontend**: Django templates with Tailwind CSS (via CDN)
- **PDF Generation**: WeasyPrint / ReportLab
- **Task Queue**: Celery + Redis (for automated reminders)

## Quick Start

### 1. Set up virtual environment

```bash
cd horse_management
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set up environment

```bash
copy .env.example .env
# Edit .env with your settings
```

### 4. Run migrations

```bash
python manage.py migrate
```

### 5. Create superuser

```bash
python manage.py createsuperuser
```

### 6. Import existing data (optional)

Place CSV files in the parent directory and run:

```bash
python manage.py import_data
```

### 7. Run development server

```bash
python manage.py runserver
```

Visit http://127.0.0.1:8000/ and log in.

## Tests and lint

CI (`.github/workflows/ci.yml`) runs these on every push and pull request.
Run them locally before pushing:

```bash
cd horse_management
pip install ruff
ruff check .                                   # errors only: undefined names, unused variables, bug patterns
python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=horse_management.test_settings python manage.py test --parallel auto
```

`test_settings` uses an in-memory SQLite database and skips migrations, so the
full suite runs in a couple of minutes.

## Running Celery (for automated notifications)

### Start Redis
```bash
redis-server
```

### Start Celery worker
```bash
celery -A horse_management worker -l info
```

### Start Celery beat (scheduler)
```bash
celery -A horse_management beat -l info
```

## Configuration

### Email Settings

Configure email in `.env`:

```
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
```

### Business Settings

Log into admin and configure:
- Business name, address, phone, email
- Logo (optional)
- Bank details for invoices
- Default payment terms

## Rate Types

The system supports different livery rates:

| Type | Daily Rate | Notes |
|------|-----------|-------|
| Grass Livery | £5.00 | Standard rate |
| Horse Grazing | £6.00 | Including hay |
| Grass Livery Premium | £7.00 | Premium service |
| Mare and Foal | £10.00 | Mare with foal |
| Stabled | £24.00 | Full stable livery |

## Project Structure

```
horse_management/
├── core/           # Main models (Horse, Owner, Location, Invoice)
├── health/         # Vaccinations and farrier visits
├── billing/        # Extra charges and service providers
├── invoicing/      # Invoice generation and PDF
├── notifications/  # Email and Celery tasks
├── templates/      # HTML templates
└── data/           # CSV import script
```

## Admin Access

The Django admin provides full control over all data:
- http://127.0.0.1:8000/admin/

## API

REST API endpoints are available for mobile integration (if needed):
- Configure in `api/` app (not fully implemented yet)

## Product Analytics (PostHog)

Off by default. Nothing is sent until `POSTHOG_API_KEY` is set, so local
development and the test suite stay silent.

### Turn it on

1. Create a free project at [posthog.com](https://posthog.com). Pick the **EU**
   region — it keeps personal data inside the EEA.
2. Copy the public project key (`phc_...`) from **Settings > Project**.
3. Set it in `.env`, or in the host's environment variables:

   ```
   POSTHOG_API_KEY=phc_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

4. Restart the app.

### What is tracked

| Signal | How |
|---|---|
| Clicks and taps | `posthog-js` autocapture — no code per element |
| Page views and time on page | Fired on HTMX settle; `$pageleave` gives the duration |
| Sign in / sign out / failed sign in | Django auth signals, server-side |
| Heatmaps | Autocapture click coordinates; view them in the PostHog toolbar |
| Session replay | On by default; set `POSTHOG_SESSION_RECORDING=False` to stop it |

### Is it working?

```bash
python manage.py check_analytics                     # config report
python manage.py check_analytics --send-test-event   # prove it can send
python manage.py check_analytics --reach             # probe the hosts
```

Run it inside the deployed container (Railway shell, or `vercel dev`). A
dashboard shows you that a variable exists; this shows you the app read it,
the browser snippet is registered, the sign-in signal is connected, and
PostHog accepts the key. Exit code is 0 when healthy, 1 when not. An unset
`POSTHOG_API_KEY` reports OFF and exits 0 — that is a valid state.

`--send-test-event` posts straight to the capture endpoint rather than through
the SDK, because the SDK queues events and returns before the network call —
it reports success even when the host is unreachable. The direct post gives a
real verdict: HTTP 200 means accepted, 401 means the key is wrong or belongs
to a project in the other region.

### Privacy

This app holds owner names, addresses and invoice figures, so the tracker is
masked by default:

- The snippet loads **only for signed-in users**. The sign-in page and every
  anonymous request load no tracker and set no analytics cookie.
- Session replay masks every input and every piece of page text.
- Autocapture masks element text and element attributes. A link labelled with
  an owner's name is recorded by position and CSS selector only.
- URL query strings are stripped, because the search box puts typed names
  into `?q=`.
- Person profiles carry a user id and a role. Usernames are sent only when
  `POSTHOG_SEND_USERNAMES=True`, because people can sign in with an email
  address.

To keep one region of a page out of session replay, add the class
`ph-no-capture` to its container.

### Ad blockers and the proxy

uBlock, Brave Shields and similar block `*.posthog.com` by default, so anyone
running one is invisible to analytics. To get round that the browser never
talks to PostHog directly. It sends everything to `/ingest/...` on this app,
and `core.views.posthog_proxy` forwards it:

| Path | Forwarded to |
|---|---|
| `/ingest/static/...` | `POSTHOG_ASSET_HOST` (the `array.js` bundle) |
| `/ingest/...` anything else | `POSTHOG_HOST` (events, replay, flags) |

The proxy never forwards cookies, accepts only GET, HEAD, POST and OPTIONS,
refuses `..` in paths, and returns 404 when analytics is off. Upstream
failures come back as 502 and never surface as an app error. Set
`POSTHOG_PROXY=False` to switch it off and have browsers hit posthog.com
directly.

### Why page views are fired by hand

`<body>` carries `hx-boost="true"`, so every link is an HTMX swap of
`#main-content`, not a browser page load. Automatic page-view capture would
record one view for a whole session. `templates/includes/posthog.html` sets
`capture_pageview: false` and fires `$pageview` on `htmx:afterSettle` instead,
guarded on the URL so partial swaps (bulk action forms, refreshes) do not
inflate the count.

### Files

| File | Role |
|---|---|
| `core/analytics.py` | Client, context processor, capture helper |
| `core/signals.py` | Sign in / sign out / failed sign in receivers |
| `templates/includes/posthog.html` | Browser snippet and HTMX page-view fix |
| `core/tests/test_analytics.py` | Tests for both halves |

## License

Private use only.
