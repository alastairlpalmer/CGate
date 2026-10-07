#!/bin/bash
pip install -r requirements.txt
npm install
npm run build:css
python manage.py migrate --noinput
python manage.py collectstatic --noinput
