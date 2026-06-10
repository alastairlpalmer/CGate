#!/bin/bash
set -euo pipefail  # fail the deploy if any step fails, instead of shipping a broken build
pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput
