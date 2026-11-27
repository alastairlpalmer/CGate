"""The public pages (docs: "C-gate marketing site: plan and build brief").

The Home page has no URL here: it is ``/``, and ``marketing.views.root``
picks Home or the dashboard (core/urls.py).
"""

from django.urls import path

from . import views

app_name = 'marketing'

urlpatterns = [
    path('features/', views.features, name='features'),
    path('pricing/', views.pricing, name='pricing'),
    path('about/', views.about, name='about'),
    path('contact/', views.contact, name='contact'),
]
