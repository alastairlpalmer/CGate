"""
Celery configuration for horse_management project.
"""

import os

from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'horse_management.settings')

# Every task retries on connection errors, records a final failure and,
# when it is on the beat schedule, runs once a day (core/jobs.py).
app = Celery('horse_management', task_cls='core.jobs:ReliableTask')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
