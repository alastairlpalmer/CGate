"""URLs for C-vet, mounted at /c-vet/ (see the root urls.py).

The prefix is also the service worker's scope and the installed app's
scope (static/cvet/manifest.json), so the three must change together.

No URL carries a practice id: the practice always comes from the
signed-in login (cvet.access).

``setup/`` is the one page here for the yard's administrator, not for
the practice (cvet.setup).
"""

from django.urls import path

from . import office, setup, views

app_name = 'cvet'

urlpatterns = [
    path('', views.app, name='app'),
    path('setup/', setup.setup, name='setup'),
    path('sw.js', views.service_worker, name='service_worker'),
    path('api/session/', views.api_session, name='api_session'),
    path('api/yard/', views.api_yard, name='api_yard'),
    path('api/notes/', views.api_notes, name='api_notes'),
    path('api/files/<uuid:client_id>/', views.api_file, name='api_file'),
    path('api/prices/', views.api_prices, name='api_prices'),
    path('api/clients/', views.api_clients, name='api_clients'),
    path('api/horses/', views.api_horses, name='api_horses'),
    path('api/visits/', views.api_visits, name='api_visits'),
    path('api/visits/<uuid:client_id>/', views.api_visit, name='api_visit'),
    path('api/visits/<uuid:client_id>/finish/', views.api_visit_finish, name='api_visit_finish'),
    path('api/bills/', views.api_bills, name='api_bills'),
    path('api/bills/<int:pk>/paid/', views.api_bill_paid, name='api_bill_paid'),
    path('bills/', views.bill_list, name='bill_list'),
    path('bills/export.csv', views.bill_export, name='bill_export'),
    path('bills/settings/', views.bill_settings, name='bill_settings'),
    path('bills/now/', views.bill_now, name='bill_now'),
    path('bills/<int:pk>/', views.bill_detail, name='bill_detail'),
    # The practice office (cvet.office): pages, with a signal.
    path('bills/<int:pk>/paid/', office.bill_paid, name='bill_paid'),
    path('bills/<int:pk>/email/', office.bill_email, name='bill_email'),
    path('statements/<uuid:client_id>/', office.statement, name='statement'),
    path('reports/', office.reports, name='reports'),
    path('prices/file/', office.price_file, name='price_file'),
]
