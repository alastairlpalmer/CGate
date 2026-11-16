"""URLs for C-vet, mounted at /c-vet/ (see the root urls.py).

The prefix is also the service worker's scope and the installed app's
scope (static/cvet/manifest.json), so the three must change together.
"""

from django.urls import path

from . import views

app_name = 'cvet'

urlpatterns = [
    path('', views.app, name='app'),
    path('sw.js', views.service_worker, name='service_worker'),
    path('api/session/', views.api_session, name='api_session'),
    path('api/bootstrap/', views.api_bootstrap, name='api_bootstrap'),
    path('api/entries/', views.api_entries, name='api_entries'),
]
