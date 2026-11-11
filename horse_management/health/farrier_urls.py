"""URLs for the farrier page, mounted at /farrier/ (see the root urls.py).

No URL carries a provider id: the provider always comes from the
signed-in user (health.farrier.provider_page).
"""

from django.urls import path

from . import farrier_views as views

app_name = 'farrier'

urlpatterns = [
    path('', views.today, name='today'),
    path('who/', views.who, name='who'),
    path('horses/<int:pk>/', views.horse_panel, name='horse_panel'),
    path('horses/<int:pk>/add/', views.horse_add, name='horse_add'),
    path('horses/<int:pk>/remove/', views.horse_remove, name='horse_remove'),
    path('horses/<int:pk>/note/', views.horse_note, name='horse_note'),
    path('visit/finish/', views.visit_finish, name='visit_finish'),
    path('visits/', views.visit_list, name='visit_list'),
    path('visits/<int:pk>/', views.visit_detail, name='visit_detail'),
    path('visits/<int:visit_pk>/lines/<int:pk>/', views.line_edit, name='line_edit'),
    path('visits/<int:visit_pk>/lines/<int:pk>/remove/', views.line_remove, name='line_remove'),
    path('prices/', views.price_list, name='price_list'),
    path('prices/add/', views.price_add, name='price_add'),
    path('prices/<int:pk>/', views.price_edit, name='price_edit'),
    path('prices/<int:pk>/toggle/', views.price_toggle, name='price_toggle'),
]
