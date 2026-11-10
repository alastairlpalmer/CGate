"""URLs for the farrier page, mounted at /farrier/ (see the root urls.py).

No URL carries a provider id: the provider always comes from the
signed-in user (health.farrier.provider_page).
"""

from django.urls import path

from . import farrier_views as views

app_name = 'farrier'

urlpatterns = [
    path('', views.today, name='today'),
    path('visits/', views.visit_list, name='visit_list'),
    path('visits/<int:pk>/', views.visit_detail, name='visit_detail'),
    path('prices/', views.price_list, name='price_list'),
]
