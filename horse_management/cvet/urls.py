"""URLs for C-vet, mounted at /c-vet/ (see the root urls.py).

The prefix is also the service worker's scope and the installed app's
scope (static/cvet/manifest.json), so the three must change together.

No URL carries a practice id: the practice always comes from the
signed-in login (cvet.access).
"""

from django.urls import path

from . import views

app_name = 'cvet'

urlpatterns = [
    path('', views.app, name='app'),
    path('sw.js', views.service_worker, name='service_worker'),
    path('api/session/', views.api_session, name='api_session'),
    path('api/yard/', views.api_yard, name='api_yard'),
    path('api/notes/', views.api_notes, name='api_notes'),
    path('api/files/<uuid:client_id>/', views.api_file, name='api_file'),
    path('api/prices/', views.api_prices, name='api_prices'),
    path('api/visits/<uuid:client_id>/finish/', views.api_visit_finish, name='api_visit_finish'),
]
