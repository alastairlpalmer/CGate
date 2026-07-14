"""
URL configuration for core app.
"""

from django.contrib.auth.decorators import login_required
from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    # Dashboard
    path('', views.dashboard, name='dashboard'),
    path('_partials/health-alerts/', views.dashboard_health_alerts, name='dashboard_health_alerts'),
    path('_partials/quick-find/', views.quick_find, name='quick_find'),

    # Finances overview
    path('finances/', views.finances, name='finances'),

    # Horses
    path('horses/', views.HorseListView.as_view(), name='horse_list'),
    path('horses/add/', views.HorseCreateView.as_view(), name='horse_create'),
    path('horses/new-arrival/', views.new_arrival, name='horse_new_arrival'),
    path('horses/<int:pk>/', views.HorseDetailView.as_view(), name='horse_detail'),
    path('horses/<int:pk>/edit/', views.HorseUpdateView.as_view(), name='horse_update'),
    path('horses/<int:pk>/move/', views.horse_move, name='horse_move'),
    path('horses/<int:pk>/arrive/', views.horse_arrive, name='horse_arrive'),
    path('horses/<int:pk>/depart/', views.horse_depart, name='horse_depart'),
    path('horses/<int:pk>/reactivate/', views.horse_reactivate, name='horse_reactivate'),
    path('horses/<int:pk>/confirm-departure/', views.confirm_departure, name='confirm_departure'),
    path('horses/<int:pk>/cancel-departure/', views.cancel_departure, name='cancel_departure'),
    path('horses/confirm-departures/', views.confirm_departures_bulk, name='confirm_departures_bulk'),
    path('horses/<int:pk>/ownership/', views.manage_ownership_shares, name='horse_ownership'),
    path('horses/<int:pk>/photos/add/', views.horse_photo_add, name='horse_photo_add'),
    path('horses/photos/<int:pk>/delete/', views.horse_photo_delete, name='horse_photo_delete'),

    # Owners
    path('owners/', views.OwnerListView.as_view(), name='owner_list'),
    path('owners/add/', views.OwnerCreateView.as_view(), name='owner_create'),
    path('owners/<int:pk>/', views.OwnerDetailView.as_view(), name='owner_detail'),
    path('owners/<int:pk>/edit/', views.OwnerUpdateView.as_view(), name='owner_update'),

    # Locations
    path('locations/', views.LocationListView.as_view(), name='location_list'),
    path('locations/add/', views.LocationCreateView.as_view(), name='location_create'),
    path('locations/<int:pk>/', views.LocationDetailView.as_view(), name='location_detail'),
    path('locations/<int:pk>/edit/', views.LocationUpdateView.as_view(), name='location_update'),
    path('locations/<int:pk>/arrive/', views.log_arrival, name='location_arrive'),
    path('locations/<int:pk>/depart/', views.log_departure, name='location_depart'),
    path('locations/<int:pk>/set-usage/', views.set_location_usage, name='location_set_usage'),

    # Placements (create/edit still needed, list redirects to locations)
    path('placements/', login_required(RedirectView.as_view(url='/locations/?tab=history', permanent=False)), name='placement_list'),
    path('placements/add/', views.PlacementCreateView.as_view(), name='placement_create'),
    path('placements/<int:pk>/edit/', views.PlacementUpdateView.as_view(), name='placement_update'),
    path('placements/<int:pk>/delete/', views.placement_delete, name='placement_delete'),

    # Documents (passport scans, insurance certs, …)
    path('documents/add/', views.document_create, name='document_create'),
    path('documents/<int:pk>/delete/', views.document_delete, name='document_delete'),
]
