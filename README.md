# Horse Management System

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
- **PDF Generation**: ReportLab
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

## License

Private use only.
